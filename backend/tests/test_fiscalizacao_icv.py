"""Módulo Fiscalização — Bloco E: ICV, bacia e painel do coordenador.

Testes escritos JUNTO de cada bloco (não represados para o fim — ver
_handoff-claude/PROMPT-fiscalizacao-bloco-E-icv.md, instrução extra do
Alisson). Mesmo padrão de test_fiscalizacao.py: SQLite em memória, sem
conftest.py, sqlite3.register_adapter para UUID.

⛔ Nenhum dado pessoal real — RE, prefixo e nome fictícios. Os números de
ICV usados nos testes são FICTÍCIOS/didáticos — não temos o arquivo real de
planilha à disposição (§10 do prompt) — escolhidos só para provar a
matemática (ponderação D22, prioridade D23, suspeita D25), não para
reproduzir uma garagem real.

⚠️ Assim como o resto do módulo (ver RegistroPartida em models/
fiscalizacao.py), os CHECK constraints deste bloco (lote IN ('E2','AR2'),
programadas >= 0, faixa_hora BETWEEN 0 AND 23) existem SÓ na migration SQL
— nenhum model do projeto declara CheckConstraint no SQLAlchemy. Por isso
os testes aqui não tentam provar CHECK via INSERT direto em SQLite (não
seria enforced nessa camada); a validação de entrada da API é coberta nos
schemas Pydantic (ver test_acao_coordenacao_* nos blocos seguintes, que
testam via endpoint HTTP, não via Session direta).
"""
import sqlite3
import uuid as _uuid_mod
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import AcaoCoordenacao, Bacia, BaciaLinha, IcvApurado

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_TABELAS = [
    Funcionario.__table__, Bacia.__table__, BaciaLinha.__table__,
    IcvApurado.__table__, AcaoCoordenacao.__table__,
]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS fiscalizacao")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)
    with Session(engine) as session:
        yield session


# ============================================================================
# BLOCO 1 — migration 030: tabelas, defaults e constraints declaradas no
# SQLAlchemy (UniqueConstraint — os CHECK ficam só na migration SQL)
# ============================================================================

def test_bacia_meta_icv_default_98(db):
    """D29 — meta_icv tem DEFAULT 98.00 no schema, não hardcoded em cada
    INSERT do código de aplicação."""
    b = Bacia(codigo="BACIA_TESTE", nome="Bacia Teste")
    db.add(b)
    db.commit()
    db.refresh(b)
    assert float(b.meta_icv) == 98.00


def test_bacia_linha_unique_por_linha_vigencia_inicio(db):
    """A UNIQUE real (linha_codigo, vigencia_inicio) — não deixa duas
    vigências abrirem no mesmo dia para a mesma linha (ver "DESVIO" no
    cabeçalho da migration 030: não é UNIQUE(bacia_codigo, linha_codigo))."""
    db.add(Bacia(codigo="B1", nome="Bacia 1"))
    db.commit()
    db.add(BaciaLinha(bacia_codigo="B1", linha_codigo="0000-00", vigencia_inicio=date(2026, 8, 1)))
    db.commit()

    db.add(BaciaLinha(bacia_codigo="B1", linha_codigo="0000-00", vigencia_inicio=date(2026, 8, 1)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_bacia_linha_mesma_linha_troca_de_bacia_com_vigencia(db):
    """D21 — a mesma linha pode aparecer em DUAS bacias diferentes, em
    vigências que não se sobrepõem (o caso real: 1726-10 mudou de bacia
    entre 26/05 e 19-21/08/2026). O schema tem que aceitar isso sem erro."""
    db.add(Bacia(codigo="B1", nome="Bacia 1"))
    db.add(Bacia(codigo="B2", nome="Bacia 2"))
    db.commit()
    db.add(BaciaLinha(
        bacia_codigo="B1", linha_codigo="0000-00",
        vigencia_inicio=date(2026, 5, 26), vigencia_fim=date(2026, 8, 18),
    ))
    db.add(BaciaLinha(
        bacia_codigo="B2", linha_codigo="0000-00",
        vigencia_inicio=date(2026, 8, 19), vigencia_fim=None,
    ))
    db.commit()  # não pode levantar

    linhas = db.query(BaciaLinha).filter_by(linha_codigo="0000-00").all()
    assert len(linhas) == 2
    assert {l.bacia_codigo for l in linhas} == {"B1", "B2"}


def test_icv_apurado_unique_linha_data_referencia(db):
    """A UNIQUE que o endpoint de upload usa como alvo de upsert (D25/§5
    do prompt) — reimportar o mesmo dia tem que ATUALIZAR, não duplicar;
    aqui provamos que a constraint em si impede a duplicata."""
    db.add(IcvApurado(
        id=uuid4(), linha_codigo="1111-11", data_referencia=date(2026, 8, 19),
        programadas=100, realizadas_tp_ts=50, realizadas_ts_tp=45,
    ))
    db.commit()

    db.add(IcvApurado(
        id=uuid4(), linha_codigo="1111-11", data_referencia=date(2026, 8, 19),
        programadas=100, realizadas_tp_ts=48, realizadas_ts_tp=47,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_icv_apurado_sem_teto_realizadas_acima_de_programadas(db):
    """D30 — ICV pode passar de 100%: o banco não impede realizadas somadas
    maiores que programadas. Quem decide teto é a apresentação (services/
    icv.py), nunca o schema."""
    registro = IcvApurado(
        id=uuid4(), linha_codigo="2222-22", data_referencia=date(2026, 8, 19),
        programadas=20, realizadas_tp_ts=22, realizadas_ts_tp=0,
    )
    db.add(registro)
    db.commit()  # não deve levantar
    db.refresh(registro)
    assert registro.realizadas_tp_ts + registro.realizadas_ts_tp == 22 > registro.programadas


def test_icv_apurado_origem_default_planilha(db):
    registro = IcvApurado(
        id=uuid4(), linha_codigo="3333-33", data_referencia=date(2026, 8, 19), programadas=10,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    assert registro.origem == "PLANILHA"
    assert registro.suspeito is False


def test_acao_coordenacao_grava_com_funcionario_real(db):
    """D26 — ação da coordenação, ligada a um funcionário real (FK)."""
    func = Funcionario(id=uuid4(), re="70010", nome="Coordenador Teste", status="ATIVO")
    db.add(func)
    db.commit()

    acao = AcaoCoordenacao(
        id=uuid4(), linha_codigo="1726-10", data_referencia=date(2026, 8, 19),
        faixa_hora=18, descricao="Equipe reforçou o TP às 18h.",
        resultado_observado="ICV subiu de 94,62% para 99,46% em dois dias.",
        registrado_por=func.id,
    )
    db.add(acao)
    db.commit()
    db.refresh(acao)
    assert acao.registrado_por == func.id
    assert acao.faixa_hora == 18
