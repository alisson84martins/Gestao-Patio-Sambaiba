"""Item 6 — 1º acesso do login PROVISORIA: "Você é fulano?".

Fluxo de verdade, sem override das dependências de autenticação: login em
/auth/login, token no header, rotas reais (uma legada do Pátio via
CurrentUser, uma nova via get_current_funcionario).

⛔ Nenhum dado pessoal real — ver tests/ambiente_acesso.py.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcionario, UsuarioLogin
from app.models.pessoas import Usuario
from app.routers import auth as auth_router_mod
from app.services.acesso_motorista import criar_acesso
from tests.ambiente_acesso import CPF_A, CPF_B, criar_engine, login_de

RE_NOVO = "90001"      # login PROVISORIA criado pelo serviço
RE_ANTIGO = "90002"    # login CPF de antes (fiscal/controlador) — D7


@pytest.fixture(autouse=True)
def _limpa_rate_limit():
    auth_router_mod._LOGIN_TENTATIVAS.clear()
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()
    yield
    auth_router_mod._LOGIN_TENTATIVAS.clear()
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()


@pytest.fixture
def ambiente():
    engine = criar_engine()
    with Session(engine) as db:
        r = criar_acesso(db, re=RE_NOVO, nome="Joaquim Pereira", cpf=CPF_A,
                         funcao_codigo="MOTORISTA", origem="OCORRENCIA", criado_por=None)
        assert r.situacao == "CRIADO", r.motivos
        fid = uuid4()
        db.add(Funcionario(id=fid, re=RE_ANTIGO, nome="Fiscal Antigo", cpf=CPF_B, status="ATIVO"))
        senha_hash = hash_password(CPF_B[-4:])
        db.add(UsuarioLogin(funcionario_id=fid, senha_hash=senha_hash, politica_senha="CPF"))
        db.add(Usuario(re=RE_ANTIGO, nome="Fiscal Antigo", senha_hash=senha_hash,
                       perfil="MOTORISTA", ativo=True))
        db.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db_teste
    yield {"http": TestClient(app), "engine": engine}
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _login(amb, re, senha):
    return amb["http"].post("/auth/login", json={"re": re, "senha": senha})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _token(amb, re, senha):
    r = _login(amb, re, senha)
    assert r.status_code == 200, r.text
    return r.json()


def _erro(r):
    corpo = r.json()
    return corpo.get("erro") or corpo.get("detail")


def _funcionario_id(engine, re):
    with Session(engine) as db:
        return db.execute(select(Funcionario.id).where(Funcionario.re == re)).scalar_one()


# ─── Login ────────────────────────────────────────────────────────────────────

def test_login_provisorio_pede_confirmacao_com_nome(ambiente):
    corpo = _token(ambiente, RE_NOVO, CPF_A[-4:])
    assert corpo["confirmar_identidade"] is True
    assert corpo["nome"] == "Joaquim Pereira"
    assert corpo["re"] == RE_NOVO


def test_login_cpf_antigo_nao_pede_confirmacao(ambiente):
    corpo = _token(ambiente, RE_ANTIGO, CPF_B[-4:])
    assert corpo["confirmar_identidade"] is False
    assert corpo["nome"] is None


# ─── Trava no backend ─────────────────────────────────────────────────────────

def test_provisorio_bloqueado_em_rota_legada_do_patio(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    r = ambiente["http"].get("/funcoes", headers=_auth(token))
    assert r.status_code == 403
    assert _erro(r) == "IDENTIDADE_PENDENTE"


def test_provisorio_bloqueado_em_rota_nova(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    fid = _funcionario_id(ambiente["engine"], RE_NOVO)
    # Nem a troca de senha "normal" serve de atalho para pular a confirmação.
    r = ambiente["http"].patch(f"/funcionarios/{fid}/login/senha", headers=_auth(token),
                               json={"senha_atual": CPF_A[-4:], "senha_nova": "atalho123"})
    assert r.status_code == 403
    assert _erro(r) == "IDENTIDADE_PENDENTE"


def test_provisorio_bloqueado_em_auth_me(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    r = ambiente["http"].get("/auth/me", headers=_auth(token))
    assert r.status_code == 403
    assert _erro(r) == "IDENTIDADE_PENDENTE"


def test_login_cpf_antigo_nao_e_bloqueado(ambiente):
    token = _token(ambiente, RE_ANTIGO, CPF_B[-4:])["access_token"]
    r = ambiente["http"].get("/funcoes", headers=_auth(token))
    assert r.status_code == 200, r.text
    fid = _funcionario_id(ambiente["engine"], RE_ANTIGO)
    r = ambiente["http"].patch(f"/funcionarios/{fid}/login/senha", headers=_auth(token),
                               json={"senha_atual": CPF_B[-4:], "senha_nova": "minha-senha"})
    assert r.status_code == 200, r.text


# ─── Sou eu ───────────────────────────────────────────────────────────────────

def test_sou_eu_troca_senha_libera_e_sincroniza_espelho(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": True, "senha_nova": "senha-nova-1"})
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "CONFIRMADA"

    login = login_de(ambiente["engine"], RE_NOVO)
    assert login.politica_senha == "PROPRIA"
    assert login.identidade_confirmada_em is not None
    assert verify_password("senha-nova-1", login.senha_hash)
    with Session(ambiente["engine"]) as db:
        espelho = db.execute(select(Usuario).where(Usuario.re == RE_NOVO)).scalar_one()
        assert espelho.senha_hash == login.senha_hash
        eventos = db.execute(select(LogAcesso.evento)).scalars().all()
    assert "IDENTIDADE_CONFIRMADA" in eventos

    # Mesmo token agora passa; senha antiga (4 do CPF) não entra mais.
    assert ambiente["http"].get("/funcoes", headers=_auth(token)).status_code == 200
    assert _login(ambiente, RE_NOVO, CPF_A[-4:]).status_code == 401
    novo = _token(ambiente, RE_NOVO, "senha-nova-1")
    assert novo["confirmar_identidade"] is False


@pytest.mark.parametrize("senha, trecho", [
    ("12345", "6 caracteres"),
    (None, "6 caracteres"),
])
def test_sou_eu_senha_fraca_recusada(ambiente, senha, trecho):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": True, "senha_nova": senha})
    assert r.status_code == 422
    assert trecho in _erro(r)
    assert login_de(ambiente["engine"], RE_NOVO).politica_senha == "PROVISORIA"


def test_confirmar_de_novo_da_409(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                          json={"sou_eu": True, "senha_nova": "senha-nova-1"})
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": False})
    assert r.status_code == 409
    assert login_de(ambiente["engine"], RE_NOVO).ativo


def test_login_antigo_nao_usa_confirmar_identidade(ambiente):
    token = _token(ambiente, RE_ANTIGO, CPF_B[-4:])["access_token"]
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": False})
    assert r.status_code == 409
    assert login_de(ambiente["engine"], RE_ANTIGO).ativo


# ─── Não sou eu ───────────────────────────────────────────────────────────────

def test_nao_sou_eu_desativa_login_e_espelho_e_bloqueia_proximo_login(ambiente):
    token = _token(ambiente, RE_NOVO, CPF_A[-4:])["access_token"]
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": False})
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "NEGADA"

    assert login_de(ambiente["engine"], RE_NOVO).ativo is False
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(Usuario.ativo).where(Usuario.re == RE_NOVO)).scalar_one() is False
        assert "IDENTIDADE_NEGADA" in db.execute(select(LogAcesso.evento)).scalars().all()

    assert _login(ambiente, RE_NOVO, CPF_A[-4:]).status_code == 403
    # O token que já estava na mão também morre.
    assert ambiente["http"].get("/funcoes", headers=_auth(token)).status_code in (401, 403)
    r = ambiente["http"].post("/auth/confirmar-identidade", headers=_auth(token),
                              json={"sou_eu": True, "senha_nova": "tentei-voltar"})
    assert r.status_code == 403
