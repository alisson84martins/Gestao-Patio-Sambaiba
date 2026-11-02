"""Migration 050 — CHECK de usuario_login.politica_senha.

🔴 O bug que motivou: a 011 criou CHECK ('CPF','FORTE') e trocar_senha()
grava 'PROPRIA' — ninguém conseguia trocar a própria senha (500 por
violação de CHECK). Nenhum teste pegava porque o SQLite dos testes não
recebe o CHECK das migrations. Aqui o conjunto aceito é lido do SQL da 050
e confrontado com (a) o que trocar_senha() grava de verdade e (b) todo
literal de politica_senha escrito no código do backend.

⛔ Nenhum dado pessoal real — RE, nome e CPF fictícios.
"""
import re
import sqlite3
import uuid as _uuid_mod
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_funcionario
from app.core.security import hash_password
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_RAIZ = Path(__file__).resolve().parents[2]
_MIGRATION_050 = _RAIZ / "database" / "migrations" / "050-acesso-motoristas.sql"
_APP = Path(__file__).resolve().parents[1] / "app"


def _politicas_aceitas() -> set[str]:
    sql = _MIGRATION_050.read_text(encoding="utf-8")
    m = re.search(
        r"ADD CONSTRAINT chk_usuario_login_politica_senha\s+CHECK \(politica_senha IN \(([^)]*)\)\)",
        sql,
    )
    assert m, "CHECK de politica_senha não encontrado na 050"
    return set(re.findall(r"'([A-Z]+)'", m.group(1)))


def _eventos_aceitos() -> set[str]:
    sql = _MIGRATION_050.read_text(encoding="utf-8")
    m = re.search(r"ADD CONSTRAINT chk_log_acesso_evento CHECK \(\s*evento IN \(([^)]*)\)", sql)
    assert m, "CHECK de log_acesso.evento não encontrado na 050"
    return set(re.findall(r"'([A-Z_0-9]+)'", m.group(1)))


def test_050_aceita_as_quatro_politicas():
    assert _politicas_aceitas() == {"CPF", "FORTE", "PROPRIA", "PROVISORIA"}


def test_050_mantem_eventos_antigos_e_inclui_os_novos():
    assert _eventos_aceitos() == {
        "LOGIN_SUCESSO", "LOGIN_FALHA", "NEGADO_403", "LEITURA_OCORRENCIA",
        "ACESSO_CRIADO", "IDENTIDADE_CONFIRMADA", "IDENTIDADE_NEGADA",
    }


def test_todo_literal_de_politica_senha_no_codigo_cabe_no_check():
    """Pega a próxima regressão do mesmo tipo: alguém grava um valor novo
    sem trocar o CHECK."""
    aceitas = _politicas_aceitas()
    achados: dict[str, set[str]] = {}
    for arq in _APP.rglob("*.py"):
        texto = arq.read_text(encoding="utf-8")
        for valor in re.findall(r"politica_senha\s*(?:=|==)\s*[\"']([A-Z]+)[\"']", texto):
            achados.setdefault(valor, set()).add(arq.name)
    assert achados, "nenhum literal de politica_senha encontrado — regex quebrou?"
    fora = {v: arqs for v, arqs in achados.items() if v not in aceitas}
    assert not fora, f"valores fora do CHECK da 050: {fora}"


def test_todo_evento_de_log_no_codigo_cabe_no_check():
    aceitos = _eventos_aceitos()
    achados: set[str] = set()
    for arq in _APP.rglob("*.py"):
        texto = arq.read_text(encoding="utf-8")
        achados.update(re.findall(r"registrar_log_acesso\(\s*\w+,\s*[\"']([A-Z_0-9]+)[\"']", texto))
    assert achados
    assert achados <= aceitos, f"eventos fora do CHECK da 050: {achados - aceitos}"


# ─── trocar_senha() de verdade ───────────────────────────────────────────────

_DONO = Funcionario(id=uuid4(), re="88001", nome="Dono Da Senha Teste", cpf="52998224725")


@pytest.fixture
def cliente():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
        UsuarioLogin.__table__,
    ])
    with Session(engine) as db:
        db.add(Funcionario(id=_DONO.id, re=_DONO.re, nome=_DONO.nome, cpf=_DONO.cpf, status="ATIVO"))
        db.add(UsuarioLogin(funcionario_id=_DONO.id, senha_hash=hash_password("4725"), politica_senha="CPF"))
        db.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[get_current_funcionario] = lambda: _DONO
    yield TestClient(app), engine
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_funcionario, None)
    engine.dispose()


def test_trocar_senha_grava_propria_e_propria_esta_no_check(cliente):
    http, engine = cliente
    r = http.patch(
        f"/funcionarios/{_DONO.id}/login/senha",
        json={"senha_atual": "4725", "senha_nova": "nova-senha-123"},
    )
    assert r.status_code == 200, r.text
    with Session(engine) as db:
        login = db.execute(
            select(UsuarioLogin).where(UsuarioLogin.funcionario_id == _DONO.id)
        ).scalar_one()
    assert login.politica_senha == "PROPRIA"
    assert login.politica_senha in _politicas_aceitas()
