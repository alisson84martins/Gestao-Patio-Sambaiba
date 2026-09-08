"""GET /patio — escala e alocação têm que cruzar pelo MESMO dia.

Bug de 08/09/2026 (_handoff-claude/PROMPT-FIX-DATA-ESCALA-PATIO.md): três
regras de data decidiam a mesma coisa e se desencontravam:

  • alocação:        AlocacaoPatio.data_referencia == get_data_servico()
                      (ciclo do pátio, vira às 20h em São Paulo)
  • escala (antes):  data_escala default = datetime.now(timezone.utc).date()
                      (vira às 21h em São Paulo — UTC-3)

Entre 20h e 21h de SP as duas regras apontavam pra dias diferentes: o
outerjoin de Escala em patio_completo não encontrava a linha daquele dia,
e o chip aparecia no pátio sem linha_codigo/horario_saida. A correção faz
o default de data_escala usar get_data_servico() também — UMA regra só.

Mesmo padrão de monkeypatch de test_portaria.py (`_DatetimeFixo` no lugar
de `datetime` do módulo) — aqui aplicado em app.routers.alocacoes, que é
onde get_data_servico() chama datetime.now(_SP).

⛔ Nenhum dado pessoal real.
"""
import sqlite3
import uuid as _uuid_mod
from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.catalogos import Linha
from app.models.enums import (
    OrigemEscalaEnum,
    PerfilUsuarioEnum,
    SetorEnum,
    TipoEscalaEnum,
    TipoFilaEnum,
)
from app.models.frota import AlocacaoPatio, Fila
from app.models.operacoes import Alerta, Escala, FichaManutencao
from app.models.pessoas import Usuario
from app.routers import alocacoes as alocacoes_router_mod

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_SP = ZoneInfo("America/Sao_Paulo")

_USUARIO = Usuario(
    id=uuid4(), re="70200", nome="Operador Pátio Teste",
    senha_hash="hash-fake-de-teste", perfil=PerfilUsuarioEnum.OPERADOR_PATIO,
    ativo=True,
)

_TABELAS = [
    Fila.__table__,
    AlocacaoPatio.__table__,
    Linha.__table__,
    Escala.__table__,
    Alerta.__table__,
    FichaManutencao.__table__,
]

# onibus.setor é coluna gerada no PostgreSQL — create_all do SQLite não
# reproduz. Mesma solução de test_patio_liberados.py: DDL na mão.
_DDL_ONIBUS = """
CREATE TABLE onibus (
    id CHAR(36) PRIMARY KEY,
    numero_frota INTEGER NOT NULL UNIQUE,
    placa VARCHAR(10),
    setor VARCHAR(10),
    status VARCHAR(20) NOT NULL DEFAULT 'ATIVO',
    codigo_externo VARCHAR(50),
    criado_em DATETIME,
    criado_por CHAR(36),
    atualizado_em DATETIME,
    atualizado_por CHAR(36)
)
"""


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine, tables=_TABELAS)
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL_ONIBUS)

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[get_current_user] = lambda: _USUARIO

    yield {"engine": engine, "http": TestClient(app)}

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _congelar_relogio(monkeypatch, momento_sp: datetime):
    """Fixa datetime.now() do módulo alocacoes — é lá que get_data_servico()
    decide o dia, e patio.py/escalas.py só importam a função pronta."""

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return momento_sp.replace(tzinfo=None)
            return momento_sp.astimezone(tz)

    monkeypatch.setattr(alocacoes_router_mod, "datetime", _DatetimeFixo)


# ─── Montagem do cenário ────────────────────────────────────────────────

def _onibus(db, frota: int):
    oid = uuid4()
    db.execute(
        Base.metadata.tables["onibus"].insert().values(
            id=oid, numero_frota=frota, status="ATIVO"
        )
    )
    return oid


def _fila(db, tipo: TipoFilaEnum, nome: str, numero=None):
    f = Fila(id=uuid4(), tipo=tipo, nome=nome, numero=numero, ativa=True)
    db.add(f)
    return f.id


def _linha(db, codigo: str):
    l = Linha(id=uuid4(), codigo=codigo, nome=f"Linha {codigo}", setor=SetorEnum.E2, ativa=True)
    db.add(l)
    return l.id


def _alocar(db, onibus_id, fila_id, data_ref: date, posicao=1):
    db.add(AlocacaoPatio(
        id=uuid4(), onibus_id=onibus_id, fila_id=fila_id, posicao=posicao,
        ativa=True, data_referencia=data_ref, alocado_em=datetime.now(_SP),
    ))


def _escala(db, onibus_id, linha_id, data_escala: date, horario="04:30:00"):
    db.add(Escala(
        id=uuid4(), data=data_escala, onibus_id=onibus_id, linha_id=linha_id,
        horario_saida=time.fromisoformat(horario), tipo=TipoEscalaEnum.PLANTAO_E2,
        origem=OrigemEscalaEnum.MANUAL,
    ))


def _chip_do_carro(resp_json, frota):
    for fila in resp_json:
        for onibus in fila["onibus"]:
            if onibus["numero_frota"] == frota:
                return onibus
    raise AssertionError(f"carro {frota} não apareceu em nenhuma fila do /patio")


# ─── Os três cenários do prompt ─────────────────────────────────────────

def test_as_22h_patio_cruza_escala_do_dia_seguinte(ambiente, monkeypatch):
    """🔴 O caso que está quebrado hoje: às 22h de D, o pátio já aloca pra
    D+1 (get_data_servico), e a escala de D+1 tem que ser encontrada — não
    a de D, que é o que o bug antigo (UTC, vira 21h) ainda enxergava."""
    momento_sp = datetime(2026, 9, 8, 22, 0, tzinfo=_SP)
    _congelar_relogio(monkeypatch, momento_sp)
    d_mais_1 = date(2026, 9, 9)

    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 3001)
        fila = _fila(db, TipoFilaEnum.NUMERICA, "Fila 01", numero=1)
        linha = _linha(db, "T01")
        _alocar(db, onibus, fila, data_ref=d_mais_1)
        _escala(db, onibus, linha, data_escala=d_mais_1, horario="05:15:00")
        db.commit()

    resp = ambiente["http"].get("/patio")
    assert resp.status_code == 200, resp.text
    chip = _chip_do_carro(resp.json(), 3001)
    assert chip["linha_codigo"] == "T01"
    assert chip["horario_saida"] == "05:15:00"


def test_as_18h_alocacao_e_escala_apontam_para_hoje(ambiente, monkeypatch):
    """Antes das 20h, ambas as regras concordam em D — cenário que já
    funcionava e não pode regredir."""
    momento_sp = datetime(2026, 9, 8, 18, 0, tzinfo=_SP)
    _congelar_relogio(monkeypatch, momento_sp)
    hoje = date(2026, 9, 8)

    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 3002)
        fila = _fila(db, TipoFilaEnum.NUMERICA, "Fila 02", numero=2)
        linha = _linha(db, "T02")
        _alocar(db, onibus, fila, data_ref=hoje)
        _escala(db, onibus, linha, data_escala=hoje, horario="06:00:00")
        db.commit()

    resp = ambiente["http"].get("/patio")
    assert resp.status_code == 200, resp.text
    chip = _chip_do_carro(resp.json(), 3002)
    assert chip["linha_codigo"] == "T02"
    assert chip["horario_saida"] == "06:00:00"


def test_janela_critica_20h_as_21h_nao_diverge_mais(ambiente, monkeypatch):
    """🔴 A janela que expunha o bug: antes da correção, entre 20h e 21h de
    SP, get_data_servico() (vira às 20h) já apontava pra D+1 enquanto o
    default antigo de data_escala (UTC, vira às 21h SP) ainda apontava pra
    D. Depois da correção as duas usam get_data_servico() e concordam."""
    momento_sp = datetime(2026, 9, 8, 20, 30, tzinfo=_SP)
    _congelar_relogio(monkeypatch, momento_sp)
    d_mais_1 = date(2026, 9, 9)

    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 3003)
        fila = _fila(db, TipoFilaEnum.NUMERICA, "Fila 03", numero=3)
        linha = _linha(db, "T03")
        _alocar(db, onibus, fila, data_ref=d_mais_1)
        _escala(db, onibus, linha, data_escala=d_mais_1, horario="23:45:00")
        db.commit()

    resp = ambiente["http"].get("/patio")
    assert resp.status_code == 200, resp.text
    chip = _chip_do_carro(resp.json(), 3003)
    assert chip["linha_codigo"] == "T03"
    assert chip["horario_saida"] == "23:45:00"
