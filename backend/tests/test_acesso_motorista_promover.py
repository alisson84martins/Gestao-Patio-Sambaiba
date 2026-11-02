"""Item 5 — POST /pre-cadastros/{id}/promover com `criar_acesso`.

⛔ Nenhum dado pessoal real — ver tests/ambiente_acesso.py.
"""
import typing
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.pre_cadastro import PessoaPreCadastro
from app.routers import pre_cadastro as router_mod
from tests.ambiente_acesso import CPF_A, CPF_B, criar_engine, inserir_ocorrencia, login_de

_ADMIN = Funcionario(id=uuid4(), re="70001", nome="Admin Teste")


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ambiente():
    engine = criar_engine()
    with Session(engine) as db:
        db.add(Funcionario(id=_ADMIN.id, re=_ADMIN.re, nome=_ADMIN.nome, status="ATIVO"))
        db.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    usuarios = _dependency_de(router_mod.EscritaUsuarios)
    app.dependency_overrides[usuarios] = lambda: _ADMIN
    app.dependency_overrides[get_db] = _get_db_teste
    yield {"http": TestClient(app), "engine": engine, "usuarios": usuarios}
    app.dependency_overrides.pop(usuarios, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _pre(engine, *, re="80001", nome="Cobrador Teste", cpf=CPF_A, papel="COBRADOR"):
    pid = uuid4()
    with Session(engine) as db:
        db.add(PessoaPreCadastro(id=pid, re=re, nome=nome, cpf=cpf, papel_sugerido=papel,
                                 status="PENDENTE"))
        db.commit()
    return pid


def _status_pre(engine, pid):
    with Session(engine) as db:
        return db.get(PessoaPreCadastro, pid).status


def _funcoes(engine, re):
    with Session(engine) as db:
        return db.execute(
            select(Funcao.codigo)
            .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .join(Funcionario, Funcionario.id == FuncionarioFuncao.funcionario_id)
            .where(Funcionario.re == re)
        ).scalars().all()


def test_sem_corpo_mantem_comportamento_antigo(ambiente):
    pid = _pre(ambiente["engine"])
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover")
    assert r.status_code == 200, r.text
    assert r.json()["acesso_criado"] is False
    assert _status_pre(ambiente["engine"], pid) == "PROMOVIDO"
    assert login_de(ambiente["engine"], "80001") is None
    assert _funcoes(ambiente["engine"], "80001") == []


def test_corpo_vazio_igual_a_sem_corpo(ambiente):
    """apiPost do front manda `{}` quando não há corpo."""
    pid = _pre(ambiente["engine"])
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={})
    assert r.status_code == 200, r.text
    assert login_de(ambiente["engine"], "80001") is None


def test_criar_acesso_cobrador_usa_papel_sugerido(ambiente):
    pid = _pre(ambiente["engine"], cpf="529.982.247-25")
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 200, r.text
    assert r.json()["acesso_criado"] is True
    login = login_de(ambiente["engine"], "80001")
    assert login is not None and login.politica_senha == "PROVISORIA"
    assert login.criado_por == _ADMIN.id
    assert _funcoes(ambiente["engine"], "80001") == ["COBRADOR"]
    assert _status_pre(ambiente["engine"], pid) == "PROMOVIDO"
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(Funcionario.cpf).where(Funcionario.re == "80001")).scalar_one() == CPF_A


def test_criar_acesso_sem_permissao_de_usuarios_da_403(ambiente):
    def _negar():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sem permissão (fake, teste)")

    ambiente["http"].app.dependency_overrides[ambiente["usuarios"]] = _negar
    pid = _pre(ambiente["engine"])
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 403
    assert _status_pre(ambiente["engine"], pid) == "PENDENTE"
    assert login_de(ambiente["engine"], "80001") is None


def test_indefinido_sem_funcao_da_422_e_nao_promove(ambiente):
    pid = _pre(ambiente["engine"], papel="INDEFINIDO")
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 422
    assert "função" in r.json()["erro"]
    assert _status_pre(ambiente["engine"], pid) == "PENDENTE"
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(Funcionario).where(Funcionario.re == "80001")).first() is None


def test_indefinido_com_funcao_escolhida_cria(ambiente):
    pid = _pre(ambiente["engine"], papel="INDEFINIDO")
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover",
                              json={"criar_acesso": True, "funcao_codigo": "MOTORISTA"})
    assert r.status_code == 200, r.text
    assert _funcoes(ambiente["engine"], "80001") == ["MOTORISTA"]


def test_conferencia_falhou_422_com_motivos_e_nada_muda(ambiente):
    """Tudo ou nada: CPF inválido → nem funcionário, nem login, e o
    pré-cadastro continua PENDENTE."""
    pid = _pre(ambiente["engine"], cpf="52998224724")
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 422
    corpo = r.json()
    assert "CPF com dígito verificador inválido" in corpo["detalhes"]
    assert "Acesso não criado" in corpo["erro"]
    assert _status_pre(ambiente["engine"], pid) == "PENDENTE"
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(Funcionario).where(Funcionario.re == "80001")).first() is None
        assert db.scalar(select(func.count()).select_from(UsuarioLogin)) == 0


def test_cpf_em_ocorrencia_de_outro_re_bloqueia_promover_com_acesso(ambiente):
    inserir_ocorrencia(ambiente["engine"], re="99001", nome="Outro", cpf=CPF_B)
    pid = _pre(ambiente["engine"], cpf=CPF_B)
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 422
    assert "CPF aparece em outra ocorrência com o RE 99001" in r.json()["detalhes"]
    assert _status_pre(ambiente["engine"], pid) == "PENDENTE"


def test_sem_cpf_com_acesso_da_422(ambiente):
    pid = _pre(ambiente["engine"], cpf=None)
    r = ambiente["http"].post(f"/pre-cadastros/{pid}/promover", json={"criar_acesso": True})
    assert r.status_code == 422
    assert "Sem CPF" in r.json()["detalhes"]


def test_leitura_expoe_pendencia(ambiente):
    pid = _pre(ambiente["engine"])
    with Session(ambiente["engine"]) as db:
        db.get(PessoaPreCadastro, pid).pendencia = "CPF com dígito verificador inválido"
        db.commit()
    leitura = _dependency_de(router_mod.LeituraPreCadastro)
    ambiente["http"].app.dependency_overrides[leitura] = lambda: _ADMIN
    try:
        r = ambiente["http"].get("/pre-cadastros?status=PENDENTE")
    finally:
        ambiente["http"].app.dependency_overrides.pop(leitura, None)
    assert r.status_code == 200, r.text
    assert r.json()[0]["pendencia"] == "CPF com dígito verificador inválido"
