"""SEV-15, SEV-16, SEV-11 (fechamento da auditoria, 11/08/2026).

Três achados de coerência/vazamento, sem relação funcional entre si — um
arquivo só porque o item do prompt de execução os agrupou (todos "baixo
risco, correção pequena").
"""
from pathlib import Path

from starlette.middleware.cors import CORSMiddleware

from app.core.config import Settings
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parent.parent


# ─── SEV-15 — expiração do JWT alinhada entre config, .env* e doc ─────────────


def test_jwt_expire_minutes_default_e_480():
    """Antes: 1440 (24h) aqui, mas 480 (8h) em .env.example, no .env real
    (dev) e no texto da doc OpenAPI — três fontes concordando, uma
    divergindo. Alinhado pro valor que já valia de fato."""
    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost/db",
        secret_key="x" * 32,
    )
    assert settings.jwt_expire_minutes == 480


def test_env_example_declara_480():
    conteudo = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "JWT_EXPIRE_MINUTES=480" in conteudo


def test_doc_openapi_do_main_py_diz_8_horas():
    """Guarda contra o texto da doc (main.py) divergir de novo do valor
    real — se alguém mudar jwt_expire_minutes sem atualizar a frase da
    doc, este teste é quem avisa."""
    conteudo = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "8 horas" in conteudo


# ─── SEV-16 — IntegrityError não vaza fragmento do banco ──────────────────────


class _FakeIntegrityError:
    """Duck-type de sqlalchemy.exc.IntegrityError — o handler só usa
    `.orig`, não faz isinstance, então não precisa da classe real."""

    def __init__(self, msg: str):
        self.orig = Exception(msg)


def _handler_integridade():
    from sqlalchemy.exc import IntegrityError
    handler = app.exception_handlers.get(IntegrityError)
    assert handler is not None, "integrity_error_handler não está registrado em app.exception_handlers"
    return handler


def test_integrity_error_duplicado_nao_vaza_nome_de_tabela_ou_coluna():
    handler = _handler_integridade()
    fragmento_sensivel = "tabela funcionario_funcao, coluna funcionario_id=8f2a viola unique"
    exc = _FakeIntegrityError(f'duplicate key value violates unique constraint "xyz"\nDETAIL: {fragmento_sensivel}')

    import asyncio
    resp = asyncio.run(handler(None, exc))

    corpo = resp.body.decode("utf-8")
    assert "funcionario_funcao" not in corpo
    assert "detalhes" not in corpo
    assert resp.status_code == 409


def test_integrity_error_foreign_key_nao_vaza():
    handler = _handler_integridade()
    exc = _FakeIntegrityError('insert or update on table "alocacao_patio" violates foreign key constraint "fk_segredo_interno"')

    import asyncio
    resp = asyncio.run(handler(None, exc))

    corpo = resp.body.decode("utf-8")
    assert "fk_segredo_interno" not in corpo
    assert "alocacao_patio" not in corpo


def test_integrity_error_check_constraint_nao_vaza():
    handler = _handler_integridade()
    exc = _FakeIntegrityError('new row for relation "onibus" violates check constraint "chk_onibus_numero_frota"')

    import asyncio
    resp = asyncio.run(handler(None, exc))

    corpo = resp.body.decode("utf-8")
    assert "chk_onibus_numero_frota" not in corpo
    assert resp.status_code == 422


def test_integrity_error_nao_mapeado_cai_no_generico_sem_vazar():
    handler = _handler_integridade()
    exc = _FakeIntegrityError("algum erro totalmente novo do driver, nunca visto, com detalhe interno xyz123")

    import asyncio
    resp = asyncio.run(handler(None, exc))

    corpo = resp.body.decode("utf-8")
    assert "xyz123" not in corpo
    assert resp.status_code == 409


def test_integrity_error_constraint_conhecida_continua_com_mensagem_especifica():
    """Controle positivo — as 3 constraints já mapeadas (mensagem em
    português, específica) não foram tocadas por este item."""
    handler = _handler_integridade()
    exc = _FakeIntegrityError('duplicate key value violates unique constraint "uq_alocacao_onibus_ativa"')

    import asyncio
    resp = asyncio.run(handler(None, exc))

    corpo = resp.body.decode("utf-8")
    assert "já está alocado" in corpo


# ─── SEV-11 — CORS allow_headers restrito ──────────────────────────────────────


def test_cors_allow_headers_restrito_a_tres_cabecalhos_conhecidos():
    """Antes: allow_headers=["*"] combinado com allow_credentials=True —
    qualquer cabeçalho passava numa requisição já autenticada. O
    frontend-v3 manda três cabeçalhos, não dois: Authorization e
    Content-Type (api.js, importacao.js, ocorrencia.form.js — o upload
    multipart tem Content-Type posto pelo navegador, não manual) e
    X-Pre-Ocorrencia-Token (pre-ocorrencia.page.js) — o formulário
    público do motorista não tem JWT pra mandar, então não passa por
    api.js e se autentica por um token de uso único em cabeçalho
    próprio. Esse terceiro ficou de fora da varredura original (SEV-11,
    11/08) porque nasceu no mesmo dia: a varredura cobriu os módulos que
    passam pelo cliente HTTP padrão, e o único que escapa dela é
    justamente o que foi escrito para não usar esse cliente."""
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    allow_headers = cors.kwargs["allow_headers"]

    assert set(allow_headers) == {"Authorization", "Content-Type", "X-Pre-Ocorrencia-Token"}
    assert "*" not in allow_headers


def test_cors_preflight_autoriza_x_pre_ocorrencia_token():
    """Guarda a funcionalidade, não só a lista: se X-Pre-Ocorrencia-Token
    sair de allow_headers no futuro, as 4 chamadas públicas do
    formulário do motorista (carregar, autosave, upload de anexo,
    enviar — pre-ocorrencia.page.js) voltam a ser bloqueadas no
    preflight, e o motorista volta a ver "Sem conexão com o servidor"
    mesmo com a API no ar. Prova pela porta que o navegador usa (OPTIONS
    de verdade via TestClient), não por introspecção do middleware —
    é o preflight que decide se o fetch real sai do navegador."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    # Usa uma origem de fato configurada em allow_origins deste ambiente
    # (cors_origins vem do .env, varia por máquina) — só o cabeçalho é o
    # que este teste quer provar, não a lista de origens.
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    origem = cors.kwargs["allow_origins"][0] if cors.kwargs["allow_origins"] != ["*"] else "https://exemplo.invalid"
    resp = client.options(
        "/pre-ocorrencias/publico",
        headers={
            "Origin": origem,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-pre-ocorrencia-token",
        },
    )
    assert resp.status_code == 200
    permitidos = {h.strip().lower() for h in resp.headers.get("access-control-allow-headers", "").split(",")}
    assert "x-pre-ocorrencia-token" in permitidos


def test_cors_allow_credentials_continua_true():
    """Não é o item a mexer nisso — só confirma que allow_headers restrito
    não foi acompanhado de uma mudança acidental em allow_credentials
    (que quebraria o envio do Bearer token)."""
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_credentials"] is True
