"""Testes do RE alfanumérico da alta gestão
(_handoff-claude/PROMPT-RE-ALFANUMERICO-2026-09-05.md).

RE com letra existe SÓ de gerente geral pra cima (diretoria e secretaria da
presidência) — todas as demais funções têm RE só numérico. Dois defeitos
cobertos aqui: (1) normalizar_re não valia na GRAVAÇÃO, só na busca —
gravar "a4011" e procurar "A4011" não casava; (2) RE não era corrigível
pela tela, e a correção precisa manter em sincronia o espelho `usuario`
(core/deps.py:87 resolve por RE, não por id).

SQLite em memória, sem ATTACH, mesmo padrão de test_pre_cadastro.py.

⛔ Nenhum dado pessoal real — RE, nome e CPF fictícios.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.registro import normalizar_re
from app.core.security import hash_password
from app.main import app
from app.models.cadastro import Funcionario
from app.models.pessoas import Motorista, Usuario
from app.routers import funcionarios as funcionarios_router_mod
from app.schemas.auth import LoginRequest
from app.schemas.cadastro import FuncionarioCreate
from app.schemas.pessoas import MotoristaCreate, UsuarioCreate
from app.services.identidade import resolver_por_re

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)


# ============================================================================
# normalizar_re — pura, sem banco
# ============================================================================

@pytest.mark.parametrize("bruto, esperado", [
    ("a4011", "A4011"),
    ("  A4011  ", "A4011"),
    ("19042", "19042"),
    ("01904", "01904"),  # 🔴 zero à esquerda preservado
    ("", None),
    ("   ", None),
    (None, None),
])
def test_normalizar_re(bruto, esperado):
    assert normalizar_re(bruto) == esperado


# ============================================================================
# Schemas de escrita aceitam letra minúscula e devolvem maiúsculo
# ============================================================================

def test_funcionario_create_normaliza_re_minusculo():
    dados = FuncionarioCreate(re="a4011", nome="Diretor Teste")
    assert dados.re == "A4011"


def test_usuario_create_normaliza_re_minusculo():
    dados = UsuarioCreate(re="a4011", nome="Diretor Teste", perfil="ADMIN", cpf="11122233344")
    assert dados.re == "A4011"


def test_motorista_create_normaliza_re_minusculo():
    dados = MotoristaCreate(re="a4011", nome="Motorista Teste")
    assert dados.re == "A4011"


def test_login_request_normaliza_re_minusculo():
    dados = LoginRequest(re="a4011", senha="123456")
    assert dados.re == "A4011"


# ─── RE em branco e RE de 21 caracteres continuam recusados ───────────────

@pytest.mark.parametrize("schema, kwargs", [
    (FuncionarioCreate, {"nome": "Fulano"}),
    (MotoristaCreate, {"nome": "Fulano"}),
    (LoginRequest, {"senha": "123456"}),
    (UsuarioCreate, {"nome": "Fulano", "perfil": "ADMIN", "cpf": "11122233344"}),
])
def test_re_em_branco_continua_recusado(schema, kwargs):
    with pytest.raises(ValidationError):
        schema(re="", **kwargs)
    with pytest.raises(ValidationError):
        schema(re="   ", **kwargs)


@pytest.mark.parametrize("schema, kwargs", [
    (FuncionarioCreate, {"nome": "Fulano"}),
    (MotoristaCreate, {"nome": "Fulano"}),
    (LoginRequest, {"senha": "123456"}),
    (UsuarioCreate, {"nome": "Fulano", "perfil": "ADMIN", "cpf": "11122233344"}),
])
def test_re_com_21_caracteres_continua_recusado(schema, kwargs):
    with pytest.raises(ValidationError):
        schema(re="A" * 21, **kwargs)


# ============================================================================
# Endpoint /funcionarios — SQLite em memória (mesmo padrão de
# test_pre_cadastro.py), com o dependency de GerenciaUsuarios sobrescrito
# por um ADMIN fixo (RBAC não é o que está sob teste aqui).
# ============================================================================

_TABELAS = [Funcionario.__table__, Usuario.__table__, Motorista.__table__]


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
    def _pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)

    admin = Funcionario(id=uuid4(), re="70001", nome="Admin Teste")

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    dep_gerencia = _dependency_de(funcionarios_router_mod.GerenciaUsuarios)

    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[dep_gerencia] = lambda: admin

    yield {"engine": engine, "http": TestClient(app)}

    app.dependency_overrides.pop(dep_gerencia, None)
    app.dependency_overrides.pop(get_db, None)


# ─── 🔴 gravar "a4011" e buscar "A4011" tem que casar ─────────────────────

def test_gravar_minuscula_e_buscar_maiuscula_casam(ambiente):
    resp = ambiente["http"].post("/funcionarios", json={"re": "a4011", "nome": "Diretor Teste"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["re"] == "A4011"

    with Session(ambiente["engine"]) as db:
        resolvido = resolver_por_re(db, "A4011")
    assert resolvido is not None
    assert resolvido.nome == "Diretor Teste"


# ─── 🔴 trocar o RE de um funcionário com espelho em usuario atualiza o
# espelho junto ─────────────────────────────────────────────────────────

def test_trocar_re_atualiza_espelho_em_usuario(ambiente):
    func_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=func_id, re="19042", nome="Fulano de Tal", status="ATIVO"))
        db.add(Usuario(
            id=uuid4(), re="19042", nome="Fulano de Tal",
            senha_hash=hash_password("1234"), perfil="MOTORISTA", ativo=True,
        ))
        db.commit()

    resp = ambiente["http"].patch(f"/funcionarios/{func_id}", json={"re": "a4011"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["re"] == "A4011"

    with Session(ambiente["engine"]) as db:
        espelho_novo = db.execute(select(Usuario).where(Usuario.re == "A4011")).scalar_one_or_none()
        espelho_antigo = db.execute(select(Usuario).where(Usuario.re == "19042")).scalar_one_or_none()
    assert espelho_novo is not None, "espelho não acompanhou a troca de RE"
    assert espelho_antigo is None, "espelho antigo ficou para trás — 401 garantido nos routers legados"


def test_trocar_re_sem_espelho_funciona_normalmente(ambiente):
    """Nem todo funcionário tem login — o espelho é opcional (só quem tem
    UsuarioLogin ganha o shim, ver _criar_ou_atualizar_espelho_usuario)."""
    func_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=func_id, re="19042", nome="Sem Login", status="ATIVO"))
        db.commit()

    resp = ambiente["http"].patch(f"/funcionarios/{func_id}", json={"re": "A4011"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["re"] == "A4011"


# ─── 🔴 trocar o RE para um já usado por outro funcionário -> 409, sem
# alterar o funcionário pela metade ─────────────────────────────────────

def test_trocar_re_para_ja_usado_por_funcionario_devolve_409_sem_alterar_nada(ambiente):
    func_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=func_id, re="19042", nome="Fulano", status="ATIVO"))
        db.add(Funcionario(id=uuid4(), re="19099", nome="Outro Funcionário", status="ATIVO"))
        db.commit()

    resp = ambiente["http"].patch(
        f"/funcionarios/{func_id}", json={"re": "19099", "nome": "Fulano Renomeado"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["conflito"]["campo"] == "re"

    with Session(ambiente["engine"]) as db:
        func = db.get(Funcionario, func_id)
    assert func.re == "19042"
    assert func.nome == "Fulano"  # nada mudou — nem o nome enviado no mesmo PATCH


def test_trocar_re_para_ja_usado_por_usuario_devolve_409(ambiente):
    """RE já usado por uma linha `usuario` órfã (sem funcionario
    correspondente) — sem esta checagem, a UNIQUE de usuario.re estouraria
    como 500 no meio da transação ao sincronizar o espelho."""
    func_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=func_id, re="19042", nome="Fulano", status="ATIVO"))
        db.add(Usuario(
            id=uuid4(), re="19099", nome="Usuário Órfão",
            senha_hash=hash_password("1234"), perfil="MOTORISTA", ativo=True,
        ))
        db.commit()

    resp = ambiente["http"].patch(f"/funcionarios/{func_id}", json={"re": "19099"})
    assert resp.status_code == 409, resp.text

    with Session(ambiente["engine"]) as db:
        func = db.get(Funcionario, func_id)
    assert func.re == "19042"


def test_re_nao_enviado_ou_igual_nao_mexe_em_nada(ambiente):
    func_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=func_id, re="19042", nome="Fulano", status="ATIVO"))
        db.commit()

    resp = ambiente["http"].patch(f"/funcionarios/{func_id}", json={"re": "19042", "nome": "Fulano Segundo"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["re"] == "19042"
    assert resp.json()["nome"] == "Fulano Segundo"
