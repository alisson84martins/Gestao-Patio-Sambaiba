"""Ambiente SQLite em memória do monitoramento (053 + 054), compartilhado
pelos testes test_monitoramento_*.py.

ATTACH dos schemas `monitoramento` e `fiscalizacao` (isolados do `main`,
igual ao Postgres) e seed igual ao da migration 054: 1726-10 monitorada
(NIMER 33534) e os códigos do SIM 1403/34171. ⛔ Sem dado pessoal real.
"""
import sqlite3
import uuid as _uuid_mod

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.fiscalizacao import (
    AcaoCoordenacao,
    Baita,
    EventoTurno,
    LinhaCoordenador,
    ObservacaoTurno,
    Parametro,
    PartidaProgramada,
    RegistroPartida,
    Turno,
    TurnoLinha,
    TurnoPosto,
)
from app.models.monitoramento import (
    AlertaTratativa,
    Coleta,
    LeituraVeiculo,
    LinhaMonitoradaCadastro,
    NimerColeta,
    NimerFiscalEscala,
    NimerViagem,
    PontoNimer,
    SimCodigoLinha,
    VelocidadeAgregacao,
    VelocidadeTrecho,
    ViagemCorrecao,
)

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

TABELAS = [
    Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
    LeituraVeiculo.__table__, Coleta.__table__, LinhaMonitoradaCadastro.__table__, SimCodigoLinha.__table__,
    NimerViagem.__table__, NimerColeta.__table__, ViagemCorrecao.__table__, AlertaTratativa.__table__,
    VelocidadeTrecho.__table__, VelocidadeAgregacao.__table__,
    NimerFiscalEscala.__table__, PontoNimer.__table__,
    Turno.__table__, TurnoLinha.__table__, TurnoPosto.__table__, PartidaProgramada.__table__,
    RegistroPartida.__table__, EventoTurno.__table__, ObservacaoTurno.__table__, Baita.__table__,
    LinhaCoordenador.__table__, Parametro.__table__, AcaoCoordenacao.__table__,
]


def criar_engine(seed: bool = True):
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS monitoramento")
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS fiscalizacao")

    Base.metadata.create_all(eng, tables=TABELAS)
    if seed:
        with Session(eng) as db:
            db.add(LinhaMonitoradaCadastro(linha_codigo="1726-10", nimer_line_id=33534, ativo=True))
            db.add(SimCodigoLinha(sim_codigo=1403, linha_codigo="1726-10", sentido=1))
            db.add(SimCodigoLinha(sim_codigo=34171, linha_codigo="1726-10", sentido=2))
            # Semente da 056 (ponto_nimer)
            for linha in ("1726-10", "178T-10", "1764-10", "2025-10", "1783-10"):
                db.add(PontoNimer(local_texto="metro santana", linha_codigo=linha, terminal="TS", ativo=True))
            db.add(PontoNimer(local_texto="cem. parque dos pinheiros", linha_codigo="1726-10", terminal="TP",
                              ativo=True))
            db.commit()
    return eng


def liberar_tudo(monkeypatch) -> None:
    """Escopo ADMIN (todas as linhas) — para os testes que não são sobre o
    acesso (bloco H). O SQLite não tem a view vw_acesso_efetivo."""
    from app.services import monitoramento_acesso as acesso
    monkeypatch.setattr(acesso, "escopo", lambda db, fid: {"tipo": "todas", "linhas": None, "turno": None})
