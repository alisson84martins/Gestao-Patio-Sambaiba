"""Cadastro único de linhas (migration 048) — normalização e interconexão.

Número + código formam linhas DIFERENTES (`271A-10` ≠ `271A-51`,
`2023-41` ≠ `2023-42`). Uma função só (`app/core/linha.py`) decide o formato
para Pátio, Escala de Fiscais, Fiscalização e cadastro.
"""
import pytest

from app.core.linha import codigo_linha, formatar_como_escala, normalizar_linha


# ─── R2: normalizar_linha ────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("271A", ("271A", "10")),
    ("271A51", ("271A", "51")),
    ("2023.41", ("2023", "41")),
    ("202341", ("2023", "41")),
    ("1726/10", ("1726", "10")),
    ("1726-10", ("1726", "10")),
    ("177H-10", ("177H", "10")),
    (" 119c ", ("119C", "10")),
    ("falta", None),
    ("MAN-E2", None),
    ("1726-1A", None),
])
def test_normalizar_linha_exemplos_da_r2(entrada, esperado):
    assert normalizar_linha(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    (2023, ("2023", "10")),          # célula numérica da planilha
    (202341, ("2023", "41")),
    (202341.0, ("2023", "41")),      # openpyxl pode devolver float inteiro
    ("271A 51", ("271A", "51")),
    ("", None),
    (None, None),
    ("271", None),                   # número com 3 caracteres: não adivinha
    ("271A-", None),
    ("271A-5100", None),             # sufixo com mais de 3 dígitos
    ("lixo", None),                  # 4 letras sem dígito não é número de linha
])
def test_normalizar_linha_bordas(entrada, esperado):
    assert normalizar_linha(entrada) == esperado


def test_codigo_canonico_e_formato_da_escala():
    assert codigo_linha("271A51") == "271A-51"
    assert codigo_linha("271A") == "271A-10"
    assert codigo_linha("falta") is None
    assert formatar_como_escala("1156-10") == "1156/10"
    assert formatar_como_escala("MAN-E2") == "MAN-E2"


# ─── Ambiente SQLite (sem Postgres) ──────────────────────────────────────────

import io
import typing
from datetime import date, time
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import Alerta, Escala, ImportacaoEscala, Linha, Motorista
from app.models.enums import SetorEnum, TipoEscalaEnum
from app.services import importacao_excel


# onibus.setor é coluna gerada no PostgreSQL — o create_all do SQLite não
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
def engine():
    eng = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(eng, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS fiscalizacao")

    Base.metadata.create_all(
        eng, tables=[t.__table__ for t in (Linha, Motorista, Escala, ImportacaoEscala, Alerta)],
    )
    with eng.begin() as conn:
        conn.exec_driver_sql(_DDL_ONIBUS)
    return eng


def _planilha_e2(linhas: list) -> bytes:
    """Aba de plantão E2 no formato Sambaíba: 3 linhas de cabeçalho e o
    grupo (carro, hora, linha) nas colunas 0,1,2. Frotas fictícias 1xxx."""
    wb = Workbook()
    ws = wb.active
    ws.title = "PLANTÃO E2"
    for _ in range(3):
        ws.append(["CARRO", "HORA", "LINHA"])
    for i, valor in enumerate(linhas):
        ws.append([1100 + i, time(4, i), valor])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _importar(engine, valores: list):
    with Session(engine) as db:
        _, erros, _, _ = importacao_excel.importar_escala(
            db, "teste.xlsx", _planilha_e2(valores), date(2026, 9, 28),
            TipoEscalaEnum.PLANTAO_E2, None,
        )
    with Session(engine) as db:
        codigos = sorted(l.codigo for l in db.execute(select(Linha)).scalars())
        n_escalas = len(db.execute(select(Escala)).scalars().all())
    return erros, codigos, n_escalas


# ─── Pátio: importação da escala ─────────────────────────────────────────────

def test_patio_271A_e_271A51_sao_duas_linhas(engine):
    erros, codigos, n = _importar(engine, ["271A", "271A51"])
    assert erros == []
    assert codigos == ["271A-10", "271A-51"]
    assert n == 2


def test_patio_2023_ponto_41_e_202341_sao_a_mesma_linha(engine):
    erros, codigos, n = _importar(engine, ["2023.41", "202341", 202341])
    assert erros == []
    assert codigos == ["2023-41"]
    assert n == 3


def test_patio_falta_vira_erro_e_nao_cria_linha(engine):
    erros, codigos, n = _importar(engine, ["falta"])
    assert codigos == []
    assert n == 0
    assert len(erros) == 1 and erros[0]["valor_recebido"] == "falta"


def test_patio_manobra_continua_com_placeholder(engine):
    wb = Workbook()
    ws = wb.active
    ws.title = "MANOBRA"
    ws.append(["MANOBRA"])
    ws.append([2101, time(5, 0)])
    buf = io.BytesIO()
    wb.save(buf)
    with Session(engine) as db:
        _, erros, _, _ = importacao_excel.importar_escala(
            db, "m.xlsx", buf.getvalue(), date(2026, 9, 28), TipoEscalaEnum.MANOBRA, None,
        )
    with Session(engine) as db:
        linhas = db.execute(select(Linha)).scalars().all()
    assert erros == []
    assert [(l.codigo, l.numero, l.sufixo) for l in linhas] == [("MAN-AR2", None, None)]


def test_patio_acha_a_linha_ja_cadastrada_sem_criar_outra(engine):
    with Session(engine) as db:
        db.add(Linha(codigo="271A-51", numero="271A", sufixo="51", nome="CANGAÍBA",
                     setor=SetorEnum.E2, ativa=True))
        db.commit()
    erros, codigos, n = _importar(engine, ["271A51"])
    assert erros == [] and codigos == ["271A-51"] and n == 1
