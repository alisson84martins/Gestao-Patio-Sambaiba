"""Pré-ocorrência do motorista (Bloco C do fechamento, 11/08/2026).

Os 10 cenários obrigatórios do PROMPT-pre-ocorrencia-motorista.md, seção
"Testes". Mesmo padrão do resto do projeto: testa o comportamento ERRADO,
não só o certo. SQLite em memória com ATTACH DATABASE (mesmo padrão de
test_ocorrencias_colecoes_filhas.py) — só funciona se as constraints
estiverem mesmo lá.

⛔ Nenhum CPF, RG, CNH ou telefone real — tudo fictício.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.ocorrencia import Ocorrencia, TipoOcorrencia
from app.models.pre_ocorrencia import PreOcorrencia, PreOcorrenciaAnexo, PreOcorrenciaAutorizacao
from app.routers import pre_ocorrencias as router_mod
from app.services.pre_ocorrencia_token import gerar_token

# _ve_todas_pre_ocorrencias()/_eh_admin()/_eh_cco() usam text() cru com um
# UUID Python como parâmetro — funciona em produção (psycopg3 adapta
# sozinho), mas o driver sqlite3 puro não sabe serializar UUID. Mesmo
# ajuste de test_ocorrencias_visibilidade.py: SQLAlchemy, ao gravar via
# ORM numa coluna PG_UUID(as_uuid=True) rodando sobre SQLite, serializa
# como .hex — o adapter abaixo faz o driver cru bindar do mesmo jeito.
sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

# Ocorrencia.numero é BigInteger + Identity(always=True) — Postgres gera
# sozinho; SQLite não tem GENERATED ALWAYS AS IDENTITY pra coluna que não
# é a PK. Simula com um contador simples, só pra este módulo de teste (o
# cenário 8 é o primeiro teste do projeto a fazer INSERT de Ocorrencia de
# verdade contra SQLite, via converter() — os outros testes de ocorrência
# sempre pré-semeiam com numero explícito).
_contador_numero_ocorrencia = {"valor": 0}


@event.listens_for(Ocorrencia, "before_insert")
def _simular_identity_numero(mapper, connection, target):
    if target.numero is None:
        _contador_numero_ocorrencia["valor"] += 1
        target.numero = _contador_numero_ocorrencia["valor"]


_TABELAS = [
    Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
    TipoOcorrencia.__table__, Ocorrencia.__table__,
    PreOcorrenciaAutorizacao.__table__, PreOcorrencia.__table__, PreOcorrenciaAnexo.__table__,
]

_COORD_A = Funcionario(id=uuid4(), re="50001", nome="Coordenador A")
_COORD_B = Funcionario(id=uuid4(), re="50002", nome="Coordenador B")
_CCO = Funcionario(id=uuid4(), re="50003", nome="CCO Teste")
_ADMIN = Funcionario(id=uuid4(), re="5598", nome="Admin Teste")


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
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=_TABELAS)

    tipo_outros_id = uuid4()
    with Session(engine) as setup:
        for f in (_COORD_A, _COORD_B, _CCO, _ADMIN):
            setup.add(Funcionario(id=f.id, re=f.re, nome=f.nome))
        # Funções — só o suficiente pra _eh_cco()/_eh_admin()/_ve_todas_pre_ocorrencias()
        f_cco = Funcao(id=uuid4(), codigo="CCO", nome="CCO", categoria="OPERACAO", nivel=6)
        f_admin = Funcao(id=uuid4(), codigo="ADMIN", nome="Admin", categoria="SISTEMA", nivel=1)
        f_coord = Funcao(id=uuid4(), codigo="COORDENADOR_TRAFEGO", nome="Coordenador", categoria="GESTAO", nivel=4)
        setup.add_all([f_cco, f_admin, f_coord])
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_CCO.id, funcao_id=f_cco.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_ADMIN.id, funcao_id=f_admin.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_COORD_A.id, funcao_id=f_coord.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_COORD_B.id, funcao_id=f_coord.id, ativo=True))
        setup.add(TipoOcorrencia(
            id=tipo_outros_id, codigo="OUTROS", nome="Outros",
            exige_vitima=False, exige_terceiro=False, exige_analise=False, ordem=99, ativo=True,
        ))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    leitura_dep = _dependency_de(router_mod.LeituraPreOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaPreOcorrencia)
    app.dependency_overrides[get_db] = _get_db_teste

    yield {"engine": engine, "http": TestClient(app), "leitura_dep": leitura_dep, "escrita_dep": escrita_dep, "tipo_outros_id": tipo_outros_id}

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)


def _autenticar(ambiente, usuario):
    ambiente["http"].app.dependency_overrides[ambiente["leitura_dep"]] = lambda: usuario
    ambiente["http"].app.dependency_overrides[ambiente["escrita_dep"]] = lambda: usuario


def _criar_autorizacao(engine, *, coordenador, aberta_por=None, expira_em=None, usada_em=None) -> tuple[UUID, str]:
    token_claro, token_hash = gerar_token()
    autorizacao_id = uuid4()
    with Session(engine) as db:
        db.add(PreOcorrenciaAutorizacao(
            id=autorizacao_id,
            aberta_por=(aberta_por or coordenador).id,
            coordenador_id=coordenador.id,
            telefone_destino="11999990000",
            token_hash=token_hash,
            expira_em=expira_em or (datetime.now(timezone.utc) + timedelta(hours=2)),
            usada_em=usada_em,
            status="AGUARDANDO",
        ))
        db.commit()
    return autorizacao_id, token_claro


def _criar_pre_ocorrencia(engine, autorizacao_id, **campos) -> UUID:
    pre_oc_id = uuid4()
    with Session(engine) as db:
        db.add(PreOcorrencia(
            id=pre_oc_id, autorizacao_id=autorizacao_id,
            hora_ocorrencia=campos.pop("hora_ocorrencia", time(14, 30)),
            relato=campos.pop("relato", "Relato de teste — colisão leve, sem feridos."),
            status=campos.pop("status", "AGUARDANDO"),
            **campos,
        ))
        db.commit()
    return pre_oc_id


# ─── 1 — token inválido / expirado / usado: MESMA resposta ────────────────────


def test_token_invalido_expirado_e_usado_dao_a_mesma_resposta(ambiente):
    http = ambiente["http"]
    engine = ambiente["engine"]

    _, token_expirado = _criar_autorizacao(
        engine, coordenador=_COORD_A,
        expira_em=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _, token_usado = _criar_autorizacao(
        engine, coordenador=_COORD_A,
        usada_em=datetime.now(timezone.utc),
    )

    resp_invalido = http.patch("/pre-ocorrencias/publico", json={}, headers={"X-Pre-Ocorrencia-Token": "token-que-nunca-existiu"})
    resp_expirado = http.patch("/pre-ocorrencias/publico", json={}, headers={"X-Pre-Ocorrencia-Token": token_expirado})
    resp_usado = http.patch("/pre-ocorrencias/publico", json={}, headers={"X-Pre-Ocorrencia-Token": token_usado})

    assert resp_invalido.status_code == resp_expirado.status_code == resp_usado.status_code == 404
    assert resp_invalido.json() == resp_expirado.json() == resp_usado.json()


# ─── 2 — token de uma autorização não dá acesso a outra ───────────────────────


def test_token_de_uma_autorizacao_nao_acessa_outra(ambiente):
    http = ambiente["http"]
    engine = ambiente["engine"]

    auth_1, token_1 = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_1, relato="Relato da pré-ocorrência 1 — confidencial.")

    auth_2, token_2 = _criar_autorizacao(engine, coordenador=_COORD_B)
    pre_oc_2 = _criar_pre_ocorrencia(engine, auth_2, relato="Relato da pré-ocorrência 2 — outra pessoa.")

    resp = http.get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": token_2})
    assert resp.status_code == 200
    assert resp.json()["relato"] == "Relato da pré-ocorrência 2 — outra pessoa."
    assert "confidencial" not in resp.text


# ─── 3 — endpoint público não alcança Ocorrencia definitiva ───────────────────


def test_endpoint_publico_nao_alcanca_ocorrencia_definitiva():
    """Trava estrutural, não de comportamento: o router público nunca
    importa o modelo Ocorrencia nem qualquer coisa que leve até ele —
    conferido no próprio módulo, não só por request."""
    import app.routers.pre_ocorrencias_publico as publico_mod

    assert "Ocorrencia" not in dir(publico_mod) or publico_mod.__dict__.get("Ocorrencia") is None
    codigo_fonte = open(publico_mod.__file__, encoding="utf-8").read()
    assert "from app.models.ocorrencia import" not in codigo_fonte
    assert "import Ocorrencia" not in codigo_fonte


# ─── Item 2 (PROMPT-correcoes-pre-ocorrencia.md) — hora inicial no fuso certo ──


def _diff_minutos_circular(t1: time, t2: time) -> float:
    def _minutos(t: time) -> float:
        return t.hour * 60 + t.minute + t.second / 60

    diff = abs(_minutos(t1) - _minutos(t2))
    return min(diff, 1440 - diff)


def test_hora_ocorrencia_inicial_no_fuso_de_sao_paulo_nao_em_utc(ambiente):
    """hora_ocorrencia é hora de parede que o motorista lê e digita, não um
    instante — precisa nascer no fuso de operação (America/Sao_Paulo), nunca
    em UTC. Sem isso, um motorista que abre o formulário às 14h vê 17:00
    já preenchido e não confere um campo que já veio preenchido."""
    http = ambiente["http"]
    engine = ambiente["engine"]

    _, token = _criar_autorizacao(engine, coordenador=_COORD_A)

    resp = http.get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp.status_code == 200

    hora_recebida = time.fromisoformat(resp.json()["hora_ocorrencia"])
    esperado_sp = datetime.now(ZoneInfo("America/Sao_Paulo")).time()
    esperado_utc = datetime.now(timezone.utc).time()

    assert _diff_minutos_circular(hora_recebida, esperado_sp) <= 5
    # Prova que o comportamento ERRADO volta a falhar o teste: UTC está ~3h
    # à frente de São Paulo, então se a correção regredir para
    # datetime.now(timezone.utc), esta asserção estoura.
    assert _diff_minutos_circular(hora_recebida, esperado_utc) >= 60


# ─── 4 — CCO chamando GET /pre-ocorrencias/{id} → 403 ─────────────────────────


def test_cco_nao_le_detalhe_de_pre_ocorrencia(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_CCO)
    pre_oc_id = _criar_pre_ocorrencia(engine, auth_id)

    _autenticar(ambiente, _CCO)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_id}")
    assert resp.status_code == 403, resp.text


# ─── 5 — coordenador A acessando pré-ocorrência de B → 403 ────────────────────


def test_coordenador_nao_ve_pre_ocorrencia_de_outro_coordenador(ambiente):
    engine = ambiente["engine"]
    auth_b, _ = _criar_autorizacao(engine, coordenador=_COORD_B)
    pre_oc_b = _criar_pre_ocorrencia(engine, auth_b)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_b}")
    assert resp.status_code == 403, resp.text


def test_coordenador_ve_a_propria_pre_ocorrencia(ambiente):
    """Controle positivo do cenário 5."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_a = _criar_pre_ocorrencia(engine, auth_a, relato="Relato do coordenador A.")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_a}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["relato"] == "Relato do coordenador A."


# ─── 6 — upload de 50 MB → 413, sem carregar tudo em memória ──────────────────


def test_upload_publico_50mb_retorna_413(ambiente):
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id)

    conteudo_grande = b"x" * (50 * 1024 * 1024)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/publico/anexos",
        headers={"X-Pre-Ocorrencia-Token": token},
        files={"arquivo": ("cnh.jpg", conteudo_grande, "image/jpeg")},
        data={"tipo": "CNH"},
    )
    assert resp.status_code == 413, resp.text


# ─── 7 — .exe renomeado para .pdf → recusado pela assinatura ──────────────────


def test_upload_publico_exe_disfarcado_de_pdf_e_recusado(ambiente):
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id)

    conteudo_exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
    resp = ambiente["http"].post(
        "/pre-ocorrencias/publico/anexos",
        headers={"X-Pre-Ocorrencia-Token": token},
        files={"arquivo": ("doc_veiculo.pdf", conteudo_exe, "application/pdf")},
        data={"tipo": "DOC_VEICULO"},
    )
    assert resp.status_code == 415, resp.text


# ─── 8 — conversão: registrado_por = coordenador, nunca o motorista ───────────


def test_conversao_registrado_por_e_o_coordenador_nunca_o_motorista(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        motorista_re="60001", motorista_nome="Motorista Fictício",
        prefixo="1721", relato="Colisão na Marginal, sem feridos.",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text

    ocorrencia_id = resp.json()["ocorrencia_id"]
    with Session(engine) as db:
        oc = db.get(Ocorrencia, UUID(ocorrencia_id))
        assert oc is not None
        assert oc.registrado_por == _COORD_A.id
        assert oc.registrado_por != UUID("00000000-0000-0000-0000-000000000000")
        # O motorista nunca tem funcionario_id nenhum associado aqui —
        # não existe conta pra ele (decisão 1). A prova de "nunca o
        # motorista" é registrado_por apontar pro coordenador autenticado,
        # não pra um id derivado de motorista_re.
        assert oc.condutor_nome == "Motorista Fictício"  # dado copiado, mas não é o autor


def test_cco_nao_converte_mesmo_tendo_escrita_no_recurso(ambiente):
    """🔴 A restrição fica no endpoint, não só na permissão — CCO tem
    pode_escrever=TRUE em pre_ocorrencia, mas não é o coordenador
    destinatário, então cai em 403 mesmo assim."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_CCO)
    pre_oc_id = _criar_pre_ocorrencia(engine, auth_a)

    _autenticar(ambiente, _CCO)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 403, resp.text


def test_cco_abre_autorizacao_informando_re_do_coordenador(ambiente):
    """CCO não tem leitura em 'usuarios' (decisão 4) — não dá pra resolver
    RE→id via /funcionarios/verificar como o resto do frontend faz. O
    endpoint resolve coordenador_re internamente."""
    _autenticar(ambiente, _CCO)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11988887777", "coordenador_re": _COORD_A.re},
    )
    assert resp.status_code == 201, resp.text

    with Session(ambiente["engine"]) as db:
        salva = db.get(PreOcorrenciaAutorizacao, UUID(resp.json()["id"]))
        assert salva.coordenador_id == _COORD_A.id


def test_cco_sem_coordenador_nenhum_informado_e_rejeitado(ambiente):
    _autenticar(ambiente, _CCO)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11988887777"},
    )
    assert resp.status_code == 422, resp.text


# ─── 9 — falha do webhook n8n não derruba a requisição ────────────────────────


def test_falha_do_webhook_n8n_nao_derruba_a_requisicao(ambiente, monkeypatch):
    """⚠️ Não dá pra monkeypatchar httpx.Client.post globalmente — o
    próprio TestClient é construído sobre httpx e quebraria junto.
    Repatcha só o NOME `httpx` dentro do módulo app.services.n8n (onde
    `import httpx` bindou uma referência própria), deixando o httpx real
    intacto pra tudo mais, inclusive o TestClient desta requisição."""
    from app.services import n8n as n8n_mod

    class _SettingsComN8N:
        n8n_webhook_url = "http://n8n-fake.invalid/webhook"
        n8n_webhook_token = None

    monkeypatch.setattr(n8n_mod, "get_settings", lambda: _SettingsComN8N())

    class _ClienteHttpxQueFalha:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise RuntimeError("n8n fora do ar (simulado no teste)")

    class _HttpxFalso:
        Client = _ClienteHttpxQueFalha

    monkeypatch.setattr(n8n_mod, "httpx", _HttpxFalso())

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11988887777", "motorista_nome": "Motorista Teste"},
    )

    assert resp.status_code == 201, resp.text
    assert "token" in resp.json()

    # E a autorização realmente foi gravada, apesar do webhook ter falhado.
    with Session(ambiente["engine"]) as db:
        salva = db.get(PreOcorrenciaAutorizacao, UUID(resp.json()["id"]))
        assert salva is not None


# ─── 10 — envio sem nenhum anexo é aceito, com pendência sinalizada ───────────


def test_envio_sem_anexo_e_aceito_com_pendencia_para_o_coordenador(ambiente):
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id, relato="Relato completo, sem nenhum documento anexado.")

    resp_envio = ambiente["http"].post("/pre-ocorrencias/publico/enviar", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp_envio.status_code == 200, resp_envio.text
    assert resp_envio.json()["protocolo"].startswith("PO-")

    _autenticar(ambiente, _COORD_A)
    resp_fila = ambiente["http"].get("/pre-ocorrencias")
    assert resp_fila.status_code == 200, resp_fila.text
    item = resp_fila.json()[0]
    assert item["status"] == "ENVIADA"
    assert set(item["pendencias"]) == {"CNH", "DOC_VEICULO"}


# ─── Extra — depois de enviar, PATCH para de aceitar escrita (regra 7) ────────


def test_apos_enviar_patch_publico_passa_a_rejeitar(ambiente):
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id)

    resp_envio = ambiente["http"].post("/pre-ocorrencias/publico/enviar", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp_envio.status_code == 200

    resp_patch_depois = ambiente["http"].patch(
        "/pre-ocorrencias/publico", json={"relato": "tentando editar depois de enviado"},
        headers={"X-Pre-Ocorrencia-Token": token},
    )
    assert resp_patch_depois.status_code == 404, resp_patch_depois.text

    # Mas o GET continua funcionando — protocolo consultável (regra 7).
    resp_get_depois = ambiente["http"].get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp_get_depois.status_code == 200, resp_get_depois.text
