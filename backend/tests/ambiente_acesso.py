"""Ambiente SQLite em memória compartilhado pelos testes do acesso de
motoristas (test_acesso_motorista*.py).

- ATTACH dos schemas `coordenadoria` e `portaria`.
- fn_ajustar_funcao_principal registrada como função SQLite que não faz
  nada (no Postgres ela só reorganiza qual vínculo é o principal).
- As quatro "segundas fontes" de nome (pré-ocorrência, recolhida anormal,
  avaria na saída, constatação de avaria) entram com DDL mínimo — o serviço
  só lê motorista_re/motorista_nome/motorista_cpf, e os modelos completos
  arrastam dezenas de NOT NULL sem relação com o que está sob teste.

⛔ Nenhum dado pessoal real — RE, nome e CPF fictícios (CPFs gerados só
para ter dígito verificador válido).
"""
import sqlite3
import uuid as _uuid_mod
from uuid import uuid4

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.models.pessoas import Motorista, Usuario
from app.models.portaria import EmpresaTerceira, VeiculoPortaria
from app.models.pre_cadastro import PessoaPreCadastro

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

# CPFs fictícios com dígito verificador válido.
CPF_A = "52998224725"
CPF_B = "11144477735"
CPF_C = "39053344705"
CPF_D = "86288366757"

TABELAS = [
    Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
    UsuarioLogin.__table__, Usuario.__table__, Motorista.__table__,
    PessoaPreCadastro.__table__, LogAcesso.__table__,
    EmpresaTerceira.__table__, VeiculoPortaria.__table__,
    TipoOcorrencia.__table__, OrgaoAutoridade.__table__,
    Ocorrencia.__table__, OcorrenciaAnalise.__table__, OcorrenciaVeiculoTerceiro.__table__,
    OcorrenciaAvaria.__table__, OcorrenciaVitima.__table__, OcorrenciaTestemunha.__table__,
    OcorrenciaAutoridade.__table__, OcorrenciaAnexo.__table__,
]

_DDL_FONTES = [
    "CREATE TABLE coordenadoria.pre_ocorrencia (id TEXT PRIMARY KEY, motorista_re TEXT, motorista_nome TEXT, motorista_cpf TEXT)",
    "CREATE TABLE portaria.recolhida_anormal (id TEXT PRIMARY KEY, motorista_re TEXT, motorista_nome TEXT)",
    "CREATE TABLE portaria.avaria_saida (id TEXT PRIMARY KEY, motorista_re TEXT, motorista_nome TEXT)",
    "CREATE TABLE portaria.avaria_constatacao (id TEXT PRIMARY KEY, motorista_re TEXT, motorista_nome TEXT)",
]


def criar_engine():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _preparar(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS portaria")
        dbapi_conn.create_function("fn_ajustar_funcao_principal", 1, lambda _fid: None)
        # Receita do SQLAlchemy para SAVEPOINT no pysqlite: sem BEGIN
        # explícito, o driver deixa o 1º SAVEPOINT abrir a transação e o
        # RELEASE dele COMMITA — a simulação do lote e o savepoint da
        # finalização seriam testados errado (no Postgres isso não acontece).
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=TABELAS)
    with engine.begin() as conn:
        for ddl in _DDL_FONTES:
            conn.execute(text(ddl))
    with Session(engine) as db:
        for codigo, nome in (("MOTORISTA", "Motorista"), ("COBRADOR", "Cobrador"), ("ADMIN", "Administrador")):
            db.add(Funcao(id=uuid4(), codigo=codigo, nome=nome, categoria="OPERACAO", nivel=8))
        db.commit()
    return engine


def inserir_fonte(engine, tabela: str, *, re: str, nome: str, cpf: str | None = None) -> None:
    colunas = "id, motorista_re, motorista_nome" + (", motorista_cpf" if tabela == "coordenadoria.pre_ocorrencia" else "")
    valores = ":id, :re, :nome" + (", :cpf" if tabela == "coordenadoria.pre_ocorrencia" else "")
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO {tabela} ({colunas}) VALUES ({valores})"),
                     {"id": uuid4().hex, "re": re, "nome": nome, "cpf": cpf})


_numero = iter(range(1, 10_000))


def inserir_ocorrencia(engine, *, re: str, nome: str = "Fulano Teste", cpf: str | None = None,
                       status: str = "RASCUNHO", registrado_por=None, excluida: bool = False):
    from datetime import date, datetime, time, timezone
    with Session(engine) as db:
        tipo = db.execute(select(TipoOcorrencia)).scalars().first()
        if tipo is None:
            tipo = TipoOcorrencia(
                id=uuid4(), codigo="INCIDENTE", nome="Incidente", exige_vitima=False,
                exige_terceiro=False, exige_analise=False, ordem=1, ativo=True,
            )
            db.add(tipo)
            db.flush()
        oc = Ocorrencia(
            id=uuid4(), numero=next(_numero), tipo_ocorrencia_id=tipo.id, prefixo="21234",
            data_ocorrencia=date(2026, 11, 1), hora_ocorrencia=time(10, 0),
            condutor_re=re, condutor_nome=nome, condutor_cpf=cpf, status=status,
            registrado_por=registrado_por,
            excluida_em=datetime.now(timezone.utc) if excluida else None,
        )
        db.add(oc)
        db.commit()
        return oc.id


def login_de(engine, re: str):
    with Session(engine) as db:
        return db.execute(
            select(UsuarioLogin).join(Funcionario, Funcionario.id == UsuarioLogin.funcionario_id)
            .where(Funcionario.re == re)
        ).scalar_one_or_none()


def pre_cadastro_de(engine, re: str):
    with Session(engine) as db:
        return db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == re)
            .order_by(PessoaPreCadastro.criado_em.desc())
        ).scalars().first()
