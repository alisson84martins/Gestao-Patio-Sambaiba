"""Bloco J — quadro "Prontos para a frota" (GET /patio/liberados).

Testes de _handoff-claude/PROMPT-patio-liberados-bloco-J.md §J.6.

SQLite em memória com ATTACH DATABASE pro schema `portaria` (mesmo padrão
de test_portaria.py) — recolhida_anormal vive lá, ficha_manutencao vive no
public.

⚠️ Datas: o SQLite guarda DateTime(timezone=True) como "wall clock" e
descarta o offset. Por isso TODO datetime deste arquivo é aware em
America/Sao_Paulo — misturar UTC e SP aqui faria a comparação de corte
comparar horas de fusos diferentes e o teste mentiria. Em produção
(PostgreSQL/timestamptz) a comparação é real.

⛔ Nenhum dado pessoal real.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.catalogos import TipoDefeito
from app.models.enums import StatusFichaEnum, TipoFilaEnum
from app.models.frota import AlocacaoPatio, Fila
from app.models.operacoes import FichaManutencao
from app.models.portaria import RecolhidaAnormal
from app.routers import patio as patio_router_mod
from app.routers.patio import _inicio_ciclo_servico
from app.routers.alocacoes import get_data_servico

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_SP = ZoneInfo("America/Sao_Paulo")

_OPERADOR = Funcionario(id=uuid4(), re="70101", nome="Operador Patio Teste")

_TABELAS = [
    Funcionario.__table__,
    Fila.__table__,
    AlocacaoPatio.__table__,
    TipoDefeito.__table__,
    FichaManutencao.__table__,
    RecolhidaAnormal.__table__,
]

# onibus.setor é coluna gerada no PostgreSQL — o create_all do SQLite não
# reproduz. Mesma solução de test_portaria.py: DDL na mão.
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


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS portaria")
        # ⛔ Sem PRAGMA foreign_keys: alocacao_patio e ficha_manutencao
        # apontam pra `usuario`/`motorista`, tabelas que este teste não
        # precisa criar. FK não é o que está sob teste aqui.

    Base.metadata.create_all(engine, tables=_TABELAS)
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL_ONIBUS)

    with Session(engine) as setup:
        setup.add(Funcionario(id=_OPERADOR.id, re=_OPERADOR.re,
                              nome=_OPERADOR.nome, status="ATIVO"))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    dep_alocacao = _dependency_de(patio_router_mod.LeituraAlocacao)
    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[dep_alocacao] = lambda: _OPERADOR

    yield {"engine": engine, "http": TestClient(app), "dep_alocacao": dep_alocacao}

    app.dependency_overrides.pop(dep_alocacao, None)
    app.dependency_overrides.pop(get_db, None)


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


def _alocar(db, onibus_id, fila_id, posicao=1, data_ref: date | None = None):
    db.add(AlocacaoPatio(
        id=uuid4(), onibus_id=onibus_id, fila_id=fila_id, posicao=posicao,
        ativa=True, data_referencia=data_ref or get_data_servico(),
        alocado_em=datetime.now(_SP),
    ))


def _recolhida(db, onibus_id, frota, *, status_ra, encerrado_em=None,
               desfecho=None, avaliacao=None):
    db.add(RecolhidaAnormal(
        id=uuid4(), momento=datetime.now(_SP), data_referencia=get_data_servico(),
        prefixo=str(frota), onibus_id=onibus_id, motivo="DEFEITO",
        tipo_defeito_codigo="MEC", status=status_ra, avaliacao=avaliacao,
        desfecho=desfecho, encerrado_em=encerrado_em,
        registrado_por=_OPERADOR.id, criado_em=datetime.now(_SP),
    ))


def _ficha(db, onibus_id, *, status_ficha, concluida_em=None, defeito="Motor"):
    td = TipoDefeito(id=uuid4(), codigo=f"D{uuid4().hex[:6]}", nome=defeito, ativo=True)
    db.add(td)
    db.add(FichaManutencao(
        id=uuid4(), onibus_id=onibus_id, tipo_defeito_id=td.id,
        status=status_ficha, aberta_em=datetime.now(_SP), concluida_em=concluida_em,
    ))


def _liberados(ambiente):
    r = ambiente["http"].get("/patio/liberados")
    assert r.status_code == 200, r.text
    return r.json()


def _dentro_do_ciclo():
    """Um instante seguramente dentro do ciclo corrente."""
    return _inicio_ciclo_servico() + timedelta(minutes=30)


def _antes_do_ciclo():
    """Ciclo anterior — o carro liberado ontem não é notícia hoje."""
    return _inicio_ciclo_servico() - timedelta(hours=2)


# ─── J.6-1 — o teste que prova o bloco inteiro ──────────────────────────
# O endpoint tem UMA porta de RBAC, e ela é `alocacao`. Se alguém acrescentar
# um exige("manutencao") aqui, este teste cai — e é pra cair: o ponto do
# bloco é o operador saber sem ter acesso ao módulo da manutenção.

def _negar():
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sem permissão (fake, teste)")


def _recursos_exigidos(path: str):
    """Lê, do próprio grafo de dependências da rota, quais recurso/escrita o
    exige() capturou. Inspecionar a closure em vez do nome da função é o que
    faz este teste falhar de verdade se alguém trocar o recurso."""
    rota = next(r for r in app.routes if getattr(r, "path", None) == path)
    achados = []
    for dep in rota.dependant.dependencies:
        fn = dep.call
        if not getattr(fn, "__qualname__", "").startswith("exige"):
            continue
        celulas = dict(zip(fn.__code__.co_freevars,
                           (c.cell_contents for c in (fn.__closure__ or ()))))
        achados.append((celulas.get("recurso"), celulas.get("escrever")))
    return achados


def test_gate_de_rbac_e_apenas_alocacao_leitura(ambiente):
    # 🔴 UMA porta, e ela é `alocacao` leitura. Se alguém acrescentar
    # exige("manutencao") aqui, este teste cai — e é pra cair: o bloco inteiro
    # existe pro operador saber SEM acesso ao módulo da manutenção.
    assert _recursos_exigidos("/patio/liberados") == [("alocacao", False)]


def test_sem_alocacao_recebe_403(ambiente):
    ambiente["http"].app.dependency_overrides[ambiente["dep_alocacao"]] = _negar
    r = ambiente["http"].get("/patio/liberados")
    assert r.status_code == 403


# ─── J.6-2 — RA encerrada aparece; alocada numa linha, some ─────────────

def test_recolhida_encerrada_na_fila_manutencao_aparece(ambiente):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2101)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _recolhida(db, onibus, 2101, status_ra="ENCERRADA",
                   encerrado_em=_dentro_do_ciclo(), desfecho="SERVICO_EXECUTADO")
        db.commit()

    dados = _liberados(ambiente)
    assert len(dados) == 1
    assert dados[0]["prefixo"] == 2101
    assert dados[0]["origem"] == "RECOLHIDA"
    assert dados[0]["detalhe"] == "Serviço executado"


def test_carro_alocado_em_fila_de_linha_some_do_quadro(ambiente):
    """🟢 O coração do desenho: o ato de alocar é a baixa. Este é o teste que
    mais tende a quebrar numa refatoração futura — por isso ele existe."""
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2102)
        fila_linha = _fila(db, TipoFilaEnum.NUMERICA, "Fila 07", numero=7)
        _alocar(db, onibus, fila_linha)
        _recolhida(db, onibus, 2102, status_ra="ENCERRADA",
                   encerrado_em=_dentro_do_ciclo(), desfecho="SEM_DEFEITO")
        db.commit()

    assert _liberados(ambiente) == []


# ─── J.6-3 — avaliação LIBERADO é prognóstico, não fato ─────────────────

def test_recolhida_apenas_avaliada_nao_aparece(ambiente):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2103)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _recolhida(db, onibus, 2103, status_ra="AVALIADA", avaliacao="LIBERADO")
        db.commit()

    assert _liberados(ambiente) == []


# ─── J.6-4 — ficha CONCLUIDA aparece; ABERTA/EM_ANDAMENTO não ───────────

def test_ficha_concluida_aparece(ambiente):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2104)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _ficha(db, onibus, status_ficha=StatusFichaEnum.CONCLUIDA,
               concluida_em=_dentro_do_ciclo(), defeito="Freio")
        db.commit()

    dados = _liberados(ambiente)
    assert len(dados) == 1
    assert dados[0]["prefixo"] == 2104
    assert dados[0]["origem"] == "FICHA"
    assert dados[0]["detalhe"] == "Freio"


@pytest.mark.parametrize("status_ficha", [StatusFichaEnum.ABERTA, StatusFichaEnum.EM_ANDAMENTO])
def test_ficha_nao_concluida_nao_aparece(ambiente, status_ficha):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2105)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _ficha(db, onibus, status_ficha=status_ficha)
        db.commit()

    assert _liberados(ambiente) == []


# ─── J.6-5 — corte pelo ciclo operacional (⛔ nunca date.today) ──────────

def test_liberacao_do_ciclo_anterior_nao_aparece(ambiente):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2106)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _recolhida(db, onibus, 2106, status_ra="ENCERRADA",
                   encerrado_em=_antes_do_ciclo(), desfecho="SEM_DEFEITO")
        db.commit()

    assert _liberados(ambiente) == []


# ─── Dedupe — RA com motivo DEFEITO abre ficha; encerrar fecha a ficha ──

def test_mesmo_carro_com_recolhida_e_ficha_vira_um_chip_so(ambiente):
    with Session(ambiente["engine"]) as db:
        onibus = _onibus(db, 2107)
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        _alocar(db, onibus, fila_man)
        _ficha(db, onibus, status_ficha=StatusFichaEnum.CONCLUIDA,
               concluida_em=_dentro_do_ciclo(), defeito="Motor")
        _recolhida(db, onibus, 2107, status_ra="ENCERRADA",
                   encerrado_em=_dentro_do_ciclo() + timedelta(minutes=1),
                   desfecho="SERVICO_EXECUTADO")
        db.commit()

    dados = _liberados(ambiente)
    assert len(dados) == 1
    # A mais recente vence — aqui, a recolhida.
    assert dados[0]["origem"] == "RECOLHIDA"


# ─── Ordenação — mais recente no topo ───────────────────────────────────

def test_ordena_do_mais_recente_para_o_mais_antigo(ambiente):
    with Session(ambiente["engine"]) as db:
        fila_man = _fila(db, TipoFilaEnum.MANUTENCAO, "Manutenção")
        for i, minutos in enumerate([10, 90, 45]):
            frota = 2110 + i
            onibus = _onibus(db, frota)
            _alocar(db, onibus, fila_man, posicao=i + 1)
            _recolhida(db, onibus, frota, status_ra="ENCERRADA",
                       encerrado_em=_inicio_ciclo_servico() + timedelta(minutes=minutos),
                       desfecho="SEM_DEFEITO")
        db.commit()

    prefixos = [d["prefixo"] for d in _liberados(ambiente)]
    assert prefixos == [2111, 2112, 2110]
