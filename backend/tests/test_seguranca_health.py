"""SEV-09 / SEV-10 (fechamento, 11/08/2026).

/docs, /redoc e /openapi.json ficavam abertos em qualquer ambiente — mapa
completo da API pra quem quisesse. /health, sem autenticação nenhuma,
devolvia `environment` e a contagem de todas as 12 tabelas — reconhecimento
de alvo de graça.

Padrão do projeto: testar o comportamento ERRADO, não só o certo.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import get_db
from app.core.deps import get_current_funcionario
from app.main import app


# ─── /health público — magro, nunca vaza detalhe ──────────────────────────


class _DBSaudavel:
    def execute(self, *args, **kwargs):
        class _R:
            def scalar(self_r):
                return 3
        return _R()


class _DBComFalha:
    """Simula erro de conexão real — a mensagem inclui algo que NUNCA
    pode aparecer na resposta HTTP (só no log do servidor)."""

    def execute(self, *args, **kwargs):
        raise RuntimeError("connection to server at host db-prod-01.internal, senha=trocada123 failed")


def test_health_publico_retorna_so_status_quando_banco_ok():
    app.dependency_overrides[get_db] = lambda: _DBSaudavel()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_publico_degradado_nao_vaza_detalhe_do_erro():
    """Antes desta correção, o corpo da resposta incluía str(exc) —
    nome de host, porta, e potencialmente credencial do traceback do
    driver de banco. Confirma que a mensagem crua nunca chega ao cliente,
    só um status genérico."""
    app.dependency_overrides[get_db] = lambda: _DBComFalha()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"status": "degraded"}
    corpo = resp.text
    assert "db-prod-01" not in corpo
    assert "trocada123" not in corpo


def test_health_publico_nao_expoe_environment_nem_contagens():
    """SEV-09/SEV-10: o achado original era exatamente isso — `environment`
    e `registros_por_tabela` saindo sem token nenhum."""
    app.dependency_overrides[get_db] = lambda: _DBSaudavel()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    corpo = resp.json()
    assert "environment" not in corpo
    assert "registros_por_tabela" not in corpo
    assert "total_tabelas" not in corpo
    assert "version" not in corpo


# ─── /health/detalhado — só autenticado ────────────────────────────────


class _FuncionarioFake:
    def __init__(self):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.re = "10001"


def test_health_detalhado_exige_autenticacao():
    """Sem token nenhum, oauth2_scheme (auto_error=True) barra antes de
    qualquer lógica do endpoint rodar — 401, não 200 com dado igual antes."""
    resp = TestClient(app).get("/health/detalhado")
    assert resp.status_code == 401


def test_health_detalhado_com_autenticacao_traz_contagens_e_ambiente():
    app.dependency_overrides[get_db] = lambda: _DBSaudavel()
    app.dependency_overrides[get_current_funcionario] = lambda: _FuncionarioFake()
    try:
        resp = TestClient(app).get("/health/detalhado")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_funcionario, None)

    assert resp.status_code == 200
    corpo = resp.json()
    assert "environment" in corpo
    assert corpo["total_tabelas"] == 12
    assert corpo["registros_por_tabela"]["onibus"] == 3


# ─── /docs, /redoc, /openapi.json — condicionados a is_production ─────────


def test_settings_is_production_reflete_environment():
    base = dict(
        database_url="postgresql+psycopg://u:p@localhost/db",
        secret_key="x" * 32,
    )
    assert Settings(**base, environment="production").is_production is True
    assert Settings(**base, environment="development").is_production is False
    assert Settings(**base, environment="staging").is_production is False


def test_docs_url_none_desliga_a_rota_de_verdade():
    """Prova o mecanismo que main.py usa (docs_url=None if is_production
    else "/docs") sem precisar recarregar o módulo app.main inteiro sob
    outra variável de ambiente: um app FastAPI minúsculo com a mesma
    expressão condicional, nos dois valores de is_production, confirma
    que `None` não é só "link escondido" — o FastAPI remove a rota, então
    /docs devolve 404 de verdade quando em produção."""
    for is_producao, status_esperado in [(True, 404), (False, 200)]:
        mini_app = FastAPI(
            docs_url=None if is_producao else "/docs",
            redoc_url=None if is_producao else "/redoc",
            openapi_url=None if is_producao else "/openapi.json",
        )
        resp = TestClient(mini_app).get("/docs")
        assert resp.status_code == status_esperado, (is_producao, resp.status_code)
