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
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.ocorrencia import Ocorrencia, OcorrenciaVeiculoTerceiro, TipoOcorrencia
from app.models.pessoas import Motorista
from app.models.pre_cadastro import PessoaPreCadastro
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
    TipoOcorrencia.__table__, Ocorrencia.__table__, OcorrenciaVeiculoTerceiro.__table__,
    PreOcorrenciaAutorizacao.__table__, PreOcorrencia.__table__, PreOcorrenciaAnexo.__table__,
    # Bloco H — a conversão alimenta o pré-cadastro (services/pre_cadastro.py).
    Motorista.__table__, PessoaPreCadastro.__table__,
]

_COORD_A = Funcionario(id=uuid4(), re="50001", nome="Coordenador A")
_COORD_B = Funcionario(id=uuid4(), re="50002", nome="Coordenador B")
_CCO = Funcionario(id=uuid4(), re="50003", nome="CCO Teste")
_ADMIN = Funcionario(id=uuid4(), re="5598", nome="Admin Teste")
# Item 1.1 (19/08/2026): precisa de alguém com ENCARREGADO pra provar que
# ele NÃO vê mais a fila inteira (perdeu o que tinha em comum com
# _ve_todas_ocorrencias()).
_ENCARREGADO = Funcionario(id=uuid4(), re="50005", nome="Encarregado Teste")


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
        # Sem isso o SQLite ignora ON DELETE CASCADE por padrão — precisa
        # pra provar o cascade de apagar_autorizacao() (item 2, 15/08/2026).
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)

    tipo_outros_id = uuid4()
    with Session(engine) as setup:
        for f in (_COORD_A, _COORD_B, _CCO, _ADMIN, _ENCARREGADO):
            setup.add(Funcionario(id=f.id, re=f.re, nome=f.nome))
        # Funções — só o suficiente pra
        # _eh_cco()/_eh_admin()/_ve_todas_pre_ocorrencias()
        f_cco = Funcao(id=uuid4(), codigo="CCO", nome="CCO", categoria="OPERACAO", nivel=6)
        f_admin = Funcao(id=uuid4(), codigo="ADMIN", nome="Admin", categoria="SISTEMA", nivel=1)
        f_coord = Funcao(id=uuid4(), codigo="COORDENADOR_TRAFEGO", nome="Coordenador", categoria="GESTAO", nivel=4)
        f_encarregado = Funcao(id=uuid4(), codigo="ENCARREGADO", nome="Encarregado", categoria="GESTAO", nivel=3)
        setup.add_all([f_cco, f_admin, f_coord, f_encarregado])
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_CCO.id, funcao_id=f_cco.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_ADMIN.id, funcao_id=f_admin.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_COORD_A.id, funcao_id=f_coord.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_COORD_B.id, funcao_id=f_coord.id, ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_ENCARREGADO.id, funcao_id=f_encarregado.id, ativo=True))
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


def _criar_anexo_pre_ocorrencia(engine, pre_ocorrencia_id, **campos) -> UUID:
    anexo_id = uuid4()
    with Session(engine) as db:
        db.add(PreOcorrenciaAnexo(
            id=anexo_id, pre_ocorrencia_id=pre_ocorrencia_id,
            tipo=campos.pop("tipo", "CNH"),
            caminho=campos.pop("caminho", f"pre_ocorrencias/{pre_ocorrencia_id}/arquivo-teste.jpg"),
            nome_original=campos.pop("nome_original", "arquivo-teste.jpg"),
            mime_type=campos.pop("mime_type", "image/jpeg"),
            tamanho_bytes=campos.pop("tamanho_bytes", 123),
            **campos,
        ))
        db.commit()
    return anexo_id


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


# ─── Item A (PROMPT-correcoes-lote-2.md) — data da conversão no fuso certo ────


def test_conversao_data_ocorrencia_ausente_usa_data_de_sao_paulo_nao_utc(ambiente):
    """data_ocorrencia é data de parede que a pessoa lê/digita, não um
    instante — precisa nascer no fuso de operação (America/Sao_Paulo) quando
    o motorista não preencheu, nunca em UTC.

    ⚠️ Este teste só falha DE VERDADE entre 21h e meia-noite (horário de
    Brasília) — é a única janela em que a data em São Paulo e a data em UTC
    divergem. Fora dela, passa com ou sem a correção. O projeto não tem
    freezegun nem injeção de relógio ainda, e o item pede para não introduzir
    dependência nova só para isto — a limitação fica documentada aqui em vez
    de mascarada."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        data_ocorrencia=None,
        motorista_re="60002", prefixo="1721", relato="Relato de teste sem data preenchida.",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text

    esperado_sp = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    with Session(engine) as db:
        oc = db.get(Ocorrencia, UUID(resp.json()["ocorrencia_id"]))
        assert oc.data_ocorrencia == esperado_sp


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


# ─── Bloco H — a conversão alimenta o pré-cadastro (§5.2 do prompt) ────

def test_conversao_alimenta_pre_cadastro_de_motorista_e_cobrador(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        motorista_re="60010", motorista_nome="Motorista Fictício",
        motorista_cpf="11122233344", motorista_cnh="98765432100",
        cobrador_re="60011", cobrador_nome="Cobrador Fictício",
        prefixo="1721", relato="Colisão leve, sem feridos.",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text

    with Session(engine) as db:
        motorista_pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "60010")
        ).scalar_one()
        cobrador_pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "60011")
        ).scalar_one()
    assert motorista_pc.papel_sugerido == "MOTORISTA"
    assert motorista_pc.nome == "Motorista Fictício"
    assert motorista_pc.cpf == "11122233344"
    assert motorista_pc.ultima_origem == "PRE_OCORRENCIA"
    assert cobrador_pc.papel_sugerido == "COBRADOR"
    assert cobrador_pc.nome == "Cobrador Fictício"


# ─── 24 — 🔴 terceiro da pré-ocorrência jamais vira pré-cadastro ────────

def test_conversao_com_terceiro_nao_cria_pre_cadastro_de_terceiro(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        prefixo="1722", relato="Colisão com terceiro.",
        terceiro_nome="Terceiro Fictício", terceiro_placa="ZZZ9Z99",
        terceiro_telefone="11988887777", terceiro_seguradora="Seguradora Fictícia",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text

    with Session(engine) as db:
        total = db.execute(select(PessoaPreCadastro)).scalars().all()
    assert total == []


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


# ─── Item 5 (PROMPT-correcoes-pre-ocorrencia.md) — rate limit por IP só conta
# tentativa que NÃO resolveu, nunca autosave legítimo ──────────────────────
#
# Cada teste usa um IP próprio (TestClient(..., client=(ip, porta))) pra não
# herdar nem deixar sujeira nos contadores globais de outros testes deste
# arquivo, que sempre usam o IP padrão "testclient" do TestClient. Limpa os
# dicts do módulo antes de cada um por segurança extra.


def _limpar_rate_limit():
    from app.services import pre_ocorrencia_token as token_mod
    token_mod._TENTATIVAS_POR_IP.clear()
    token_mod._TENTATIVAS_POR_TOKEN.clear()


def test_rate_limit_por_ip_nao_conta_autosave_com_token_valido(ambiente):
    """O caso do motorista escrevendo em rajadas curtas: mais chamadas que
    _IP_MAX, todas com o MESMO token válido — nenhuma pode voltar 429. Sem
    a correção (contador de IP incrementado em toda tentativa, mesmo as que
    resolvem), este teste estoura no request de número _IP_MAX + 1."""
    from app.services.pre_ocorrencia_token import _IP_MAX

    _limpar_rate_limit()
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id)

    http = TestClient(app, client=("203.0.113.10", 51000))
    for i in range(_IP_MAX + 10):
        resp = http.patch(
            "/pre-ocorrencias/publico",
            json={"relato": f"Relato sendo digitado aos poucos, parte {i}."},
            headers={"X-Pre-Ocorrencia-Token": token},
        )
        assert resp.status_code == 200, f"request {i}: {resp.text}"


# ─── PROMPT 15/08/2026, item 1 — cancelar autorização ─────────────────────────


def test_autor_cancela_a_propria_autorizacao(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_id}/cancelar")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELADA"

    with Session(engine) as db:
        assert db.get(PreOcorrenciaAutorizacao, auth_id).status == "CANCELADA"


def test_outro_coordenador_nao_cancela_autorizacao_alheia(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)

    _autenticar(ambiente, _COORD_B)
    resp = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_id}/cancelar")
    assert resp.status_code == 403, resp.text


def test_admin_cancela_a_autorizacao_de_outro(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)

    _autenticar(ambiente, _ADMIN)
    resp = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_id}/cancelar")
    assert resp.status_code == 200, resp.text


def test_cancelar_autorizacao_ja_convertida_e_bloqueado(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(
        engine, auth_id, status="CONVERTIDA",
        relato="Já virou ocorrência definitiva.",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_id}/cancelar")
    assert resp.status_code == 409, resp.text


def test_apos_cancelar_o_token_para_de_funcionar_nas_rotas_publicas(ambiente):
    """🔴 É este teste que prova a regra — token cancelado precisa devolver
    a MESMA resposta genérica que inválido/expirado/usado, nas 4 rotas
    públicas (aqui, GET representa as 4 — todas passam por
    _carregar_autorizacao())."""
    engine = ambiente["engine"]
    auth_id, token = _criar_autorizacao(engine, coordenador=_COORD_A)

    resp_antes = ambiente["http"].get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp_antes.status_code == 200, resp_antes.text

    _autenticar(ambiente, _COORD_A)
    resp_cancelar = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_id}/cancelar")
    assert resp_cancelar.status_code == 200, resp_cancelar.text

    resp_depois = ambiente["http"].get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": token})
    assert resp_depois.status_code == 404
    # Backend padroniza erro em {"erro": "...", "status_code": N}, não no
    # "detail" padrão do FastAPI (ver app/core/exception_handlers.py).
    assert resp_depois.json()["erro"] == "Link inválido ou expirado."


# ─── PROMPT 15/08/2026, item 2 — exclusão definitiva (só ADMIN) ───────────────


def test_admin_apaga_autorizacao_definitivamente(ambiente, tmp_path, monkeypatch):
    from app.routers import pre_ocorrencias as router_mod

    monkeypatch.setattr(router_mod, "UPLOADS_BASE", tmp_path)

    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(engine, auth_id, relato="Relato de teste — vai ser apagado.")

    caminho_relativo = f"pre_ocorrencias/{pre_oc_id}/arquivo-teste.jpg"
    caminho_absoluto = tmp_path / caminho_relativo
    caminho_absoluto.parent.mkdir(parents=True, exist_ok=True)
    caminho_absoluto.write_bytes(b"conteudo ficticio de teste, nunca documento real")
    anexo_id = uuid4()
    with Session(engine) as db:
        db.add(PreOcorrenciaAnexo(
            id=anexo_id, pre_ocorrencia_id=pre_oc_id, tipo="CNH",
            caminho=caminho_relativo, nome_original="arquivo-teste.jpg",
            mime_type="image/jpeg", tamanho_bytes=len(caminho_absoluto.read_bytes()),
        ))
        db.commit()
    assert caminho_absoluto.exists()

    _autenticar(ambiente, _ADMIN)
    resp = ambiente["http"].delete(f"/pre-ocorrencias/autorizacoes/{auth_id}")
    assert resp.status_code == 204, resp.text

    assert not caminho_absoluto.exists()
    with Session(engine) as db:
        assert db.get(PreOcorrenciaAutorizacao, auth_id) is None
        assert db.get(PreOcorrencia, pre_oc_id) is None
        assert db.get(PreOcorrenciaAnexo, anexo_id) is None


def test_coordenador_destinatario_apaga_a_propria_autorizacao(ambiente):
    """PROMPT 19/08/2026, item 1.3: na fila, o coordenador apaga só o que
    é dele — o destinatário passa a poder apagar definitivamente, não só
    o ADMIN. Substitui o antigo teste (que barrava exatamente este caso,
    de propósito, sob a regra anterior)."""
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].delete(f"/pre-ocorrencias/autorizacoes/{auth_id}")
    assert resp.status_code == 204, resp.text

    with Session(engine) as db:
        assert db.get(PreOcorrenciaAutorizacao, auth_id) is None


def test_coordenador_de_outra_autorizacao_nao_apaga(ambiente):
    """Quem não é o destinatário nem ADMIN continua barrado — inclusive
    outro coordenador comum, não só o CCO."""
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_B, aberta_por=_COORD_B)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].delete(f"/pre-ocorrencias/autorizacoes/{auth_id}")
    assert resp.status_code == 403, resp.text

    with Session(engine) as db:
        assert db.get(PreOcorrenciaAutorizacao, auth_id) is not None


def test_cco_que_abriu_nao_apaga_so_o_destinatario_ou_admin(ambiente):
    """Quem só abriu (CCO) não apaga — só cancela, que já é a ação dele
    (cancelar_autorizacao acima)."""
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_CCO)

    _autenticar(ambiente, _CCO)
    resp = ambiente["http"].delete(f"/pre-ocorrencias/autorizacoes/{auth_id}")
    assert resp.status_code == 403, resp.text

    with Session(engine) as db:
        assert db.get(PreOcorrenciaAutorizacao, auth_id) is not None


def test_apagar_autorizacao_ja_convertida_e_bloqueado(ambiente):
    engine = ambiente["engine"]
    auth_id, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_id, status="CONVERTIDA", relato="Já virou ocorrência.")

    _autenticar(ambiente, _ADMIN)
    resp = ambiente["http"].delete(f"/pre-ocorrencias/autorizacoes/{auth_id}")
    assert resp.status_code == 409, resp.text


def _funcionario_cadastrado(engine, **campos) -> Funcionario:
    """Cria e persiste um Funcionario extra além dos 4 fixos do módulo
    (_COORD_A/B, _CCO, _ADMIN) — usado pelos testes de pré-preenchimento
    (item 5) que precisam de RG/CNH/telefone/CPF no cadastro central."""
    func = Funcionario(id=uuid4(), **campos)
    with Session(engine) as db:
        db.add(func)
        db.commit()
    return func


# ─── PROMPT 15/08/2026, item 5 — pré-preenchimento na abertura ────────────────


def test_autorizacao_com_re_conhecido_pre_preenche_pre_ocorrencia(ambiente):
    engine = ambiente["engine"]
    _funcionario_cadastrado(
        engine, re="60010", nome="Motorista Cadastrado", telefone="11988880000",
        cpf="11122233344", rg="123456789", cnh="98765432100",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11999990000", "motorista_re": "60010"},
    )
    assert resp.status_code == 201, resp.text
    auth_id = UUID(resp.json()["id"])

    with Session(engine) as db:
        pre_oc = db.execute(
            select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == auth_id)
        ).scalar_one()
        assert pre_oc.motorista_nome == "Motorista Cadastrado"
        assert pre_oc.motorista_telefone == "11988880000"
        assert pre_oc.motorista_cpf == "11122233344"
        assert pre_oc.motorista_rg == "123456789"
        assert pre_oc.motorista_cnh == "98765432100"
        # NOT NULL satisfeitos, mesmos defaults do fluxo público.
        assert pre_oc.relato == ""
        assert pre_oc.status == "AGUARDANDO"


def test_autorizacao_com_re_inexistente_abre_normalmente_sem_erro(ambiente):
    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11999990000", "motorista_re": "60099-nao-existe"},
    )
    assert resp.status_code == 201, resp.text

    with Session(ambiente["engine"]) as db:
        pre_oc = db.execute(
            select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == UUID(resp.json()["id"]))
        ).scalar_one()
        assert pre_oc.motorista_nome is None
        assert pre_oc.motorista_telefone is None


def test_autorizacao_sem_re_abre_normalmente(ambiente):
    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11999990000"},
    )
    assert resp.status_code == 201, resp.text


def test_valor_informado_na_autorizacao_nao_e_sobrescrito_pelo_cadastro(ambiente):
    engine = ambiente["engine"]
    _funcionario_cadastrado(engine, re="60011", nome="Nome do Cadastro Central")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={
            "telefone_destino": "11999990000",
            "motorista_re": "60011",
            "motorista_nome": "Nome Digitado Por Quem Abriu",
        },
    )
    assert resp.status_code == 201, resp.text

    with Session(engine) as db:
        pre_oc = db.execute(
            select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == UUID(resp.json()["id"]))
        ).scalar_one()
        assert pre_oc.motorista_nome == "Nome Digitado Por Quem Abriu"


def test_cobrador_continua_sem_pre_preenchimento(ambiente):
    """Decisão 10, reafirmada — nem autorização nem cadastro têm de onde
    vir dado de cobrador; a linha nasce sempre vazia nesses campos."""
    engine = ambiente["engine"]
    _funcionario_cadastrado(engine, re="60012", nome="Alguém Qualquer", telefone="11977776666")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(
        "/pre-ocorrencias/autorizacoes",
        json={"telefone_destino": "11999990000", "motorista_re": "60012"},
    )
    assert resp.status_code == 201, resp.text

    with Session(engine) as db:
        pre_oc = db.execute(
            select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == UUID(resp.json()["id"]))
        ).scalar_one()
        assert pre_oc.cobrador_re is None
        assert pre_oc.cobrador_nome is None
        assert pre_oc.cobrador_telefone is None


def test_autopreencher_pessoa_nao_devolve_cpf_rg_nem_cnh(ambiente):
    """🔴 Teste explícito sobre a RESPOSTA, não sobre a intenção — é o que
    garante que o CCO (que também chama este endpoint) nunca passe a ler
    documento."""
    engine = ambiente["engine"]
    _funcionario_cadastrado(
        engine, re="60013", nome="Motorista Sigiloso", telefone="11955554444",
        cpf="99988877766", rg="555444333", cnh="11122233344",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias/autopreencher/pessoa?re=60013")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo == {"nome": "Motorista Sigiloso", "telefone": "11955554444"}
    assert "cpf" not in corpo and "rg" not in corpo and "cnh" not in corpo


def test_autopreencher_pessoa_re_inexistente_retorna_200_vazio(ambiente):
    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias/autopreencher/pessoa?re=nao-existe-999")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"nome": None, "telefone": None}


def test_rate_limit_por_ip_ainda_barra_varredura_de_token_invalido(ambiente):
    """A defesa contra varredura de tokens continua de pé: tentativas que
    NÃO resolvem (token nunca existiu) seguem contando pro limite por IP.
    Um token diferente por request, como uma varredura de verdade faria —
    o limite por TOKEN (que conta por hash) não teria como barrar isso
    sozinho, cada hash aqui só aparece uma vez."""
    from app.services.pre_ocorrencia_token import _IP_MAX

    _limpar_rate_limit()
    http = TestClient(app, client=("203.0.113.11", 51001))

    respostas = [
        http.get("/pre-ocorrencias/publico", headers={"X-Pre-Ocorrencia-Token": f"token-que-nunca-existiu-{i}"})
        for i in range(_IP_MAX + 5)
    ]

    assert all(r.status_code == 404 for r in respostas[:_IP_MAX])
    assert respostas[_IP_MAX].status_code == 429


# ─── PROMPT 19/08/2026, item 1.1 — só ADMIN vê a fila inteira ─────────────────


def test_encarregado_nao_ve_mais_a_fila_de_outro_coordenador(ambiente):
    """Decisão de 19/08/2026: o paralelo com _ve_todas_ocorrencias() (que
    inclui ENCARREGADO/GERENTE_GERAL/GERENTE_OPERACIONAL) deixa de
    existir de propósito — só ADMIN enxerga a fila inteira agora.
    ENCARREGADO cai no mesmo filtro por coordenador_id que qualquer
    coordenador comum, e não é o destinatário de nada aqui."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a, relato="Relato direcionado ao coordenador A.")

    _autenticar(ambiente, _ENCARREGADO)
    resp = ambiente["http"].get("/pre-ocorrencias")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_encarregado_nao_acessa_detalhe_de_pre_ocorrencia_alheia(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_a = _criar_pre_ocorrencia(engine, auth_a)

    _autenticar(ambiente, _ENCARREGADO)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_a}")
    assert resp.status_code == 403, resp.text


def test_admin_continua_vendo_a_fila_inteira(ambiente):
    """Controle positivo do item 1.1 — a exceção é só ADMIN, ninguém mais."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a, relato="Relato do coordenador A.")

    _autenticar(ambiente, _ADMIN)
    resp = ambiente["http"].get("/pre-ocorrencias")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ─── PROMPT 19/08/2026, item 2 — convertida/descartada saem da tela ───────────


def test_convertida_nao_aparece_na_fila(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a, status="CONVERTIDA")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_descartada_nao_aparece_na_fila(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a, status="DESCARTADA")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_convertida_nao_aparece_em_minhas_autorizacoes(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a, status="CONVERTIDA")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias/autorizacoes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_cancelar_ja_tira_a_autorizacao_de_minhas_autorizacoes(ambiente):
    """Efeito colateral desejado do item 2: cancelar_autorizacao() marca a
    pré-ocorrência ligada como DESCARTADA, e isso já basta pra sumir da
    lista — sem filtro extra nenhum além do de status."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)
    _criar_pre_ocorrencia(engine, auth_a)

    _autenticar(ambiente, _COORD_A)
    resp_cancelar = ambiente["http"].patch(f"/pre-ocorrencias/autorizacoes/{auth_a}/cancelar")
    assert resp_cancelar.status_code == 200, resp_cancelar.text

    resp = ambiente["http"].get("/pre-ocorrencias/autorizacoes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_autorizacao_sem_pre_ocorrencia_continua_em_minhas_autorizacoes(ambiente):
    """Não conte com abrir_autorizacao() sempre criar uma PreOcorrencia —
    uma autorização "nua" (sem pré-ocorrência ligada) precisa continuar
    aparecendo; o outer join não pode virar filtro que a esconde."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A, aberta_por=_COORD_A)
    # Sem _criar_pre_ocorrencia() de propósito.

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get("/pre-ocorrencias/autorizacoes")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == str(auth_a)


# ─── PROMPT 19/08/2026, item 3.2 — baixar anexo da pré-ocorrência ─────────────


def test_baixar_anexo_de_pre_ocorrencia_que_nao_e_sua_e_403(ambiente):
    engine = ambiente["engine"]
    auth_b, _ = _criar_autorizacao(engine, coordenador=_COORD_B)
    pre_oc_b = _criar_pre_ocorrencia(engine, auth_b)
    anexo_id = _criar_anexo_pre_ocorrencia(engine, pre_oc_b)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_b}/anexos/{anexo_id}/arquivo")
    assert resp.status_code == 403, resp.text


def test_baixar_anexo_de_outra_pre_ocorrencia_com_id_valido_e_404(ambiente):
    """O id do anexo existe de verdade — só que é de OUTRA pré-ocorrência.
    A busca filtra por id E por pre_ocorrencia_id juntos, então um anexo
    "certo" preso a um caminho de pré-ocorrência "errado" nunca aparece
    como se fosse dela."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_a = _criar_pre_ocorrencia(engine, auth_a)

    auth_b, _ = _criar_autorizacao(engine, coordenador=_COORD_B)
    pre_oc_b = _criar_pre_ocorrencia(engine, auth_b)
    anexo_de_b = _criar_anexo_pre_ocorrencia(engine, pre_oc_b)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_a}/anexos/{anexo_de_b}/arquivo")
    assert resp.status_code == 404, resp.text


def test_baixar_anexo_da_propria_pre_ocorrencia_funciona(ambiente, tmp_path, monkeypatch):
    """Controle positivo do item 3.2."""
    from app.routers import pre_ocorrencias as router_mod
    monkeypatch.setattr(router_mod, "UPLOADS_BASE", tmp_path)

    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_a = _criar_pre_ocorrencia(engine, auth_a)

    caminho_relativo = f"pre_ocorrencias/{pre_oc_a}/arquivo-teste.jpg"
    caminho_absoluto = tmp_path / caminho_relativo
    caminho_absoluto.parent.mkdir(parents=True, exist_ok=True)
    caminho_absoluto.write_bytes(b"conteudo ficticio de teste, nunca documento real")
    anexo_id = _criar_anexo_pre_ocorrencia(engine, pre_oc_a, caminho=caminho_relativo)

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].get(f"/pre-ocorrencias/{pre_oc_a}/anexos/{anexo_id}/arquivo")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"conteudo ficticio de teste, nunca documento real"


# ─── PROMPT 19/08/2026, item 7 — terceiro vai pra veiculo_terceiro, não
# pro relato ─────────────────────────────────────────────────────────────


def test_converter_com_terceiro_cria_uma_linha_em_veiculos_terceiro(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        relato="Colisão na Marginal, terceiro envolvido.",
        terceiro_placa="ABC1D23", terceiro_modelo_cor="Onix / Prata",
        terceiro_nome="Fulano de Tal", terceiro_telefone="11977776666",
        terceiro_seguradora="Seguradora Exemplo",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text
    ocorrencia_id = UUID(resp.json()["ocorrencia_id"])

    with Session(engine) as db:
        oc = db.get(Ocorrencia, ocorrencia_id)
        assert "Terceiro" not in oc.descricao_motorista

        terceiros = db.execute(
            select(OcorrenciaVeiculoTerceiro).where(OcorrenciaVeiculoTerceiro.ocorrencia_id == ocorrencia_id)
        ).scalars().all()
        assert len(terceiros) == 1
        terceiro = terceiros[0]
        assert terceiro.ordem == 1
        assert terceiro.placa == "ABC1D23"
        assert terceiro.modelo == "Onix"
        assert terceiro.cor == "Prata"
        assert terceiro.proprietario == "Fulano de Tal"
        assert terceiro.fones == "11977776666"
        assert terceiro.seguradora == "Seguradora Exemplo"


def test_converter_sem_terceiro_nao_cria_linha_nenhuma(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(engine, auth_a, relato="Sem nenhum terceiro envolvido.")

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text
    ocorrencia_id = UUID(resp.json()["ocorrencia_id"])

    with Session(engine) as db:
        terceiros = db.execute(
            select(OcorrenciaVeiculoTerceiro).where(OcorrenciaVeiculoTerceiro.ocorrencia_id == ocorrencia_id)
        ).scalars().all()
        assert terceiros == []


def test_converter_modelo_cor_sem_barra_vai_tudo_pra_modelo(ambiente):
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a, relato="Terceiro sem separador no modelo/cor.",
        terceiro_modelo_cor="Gol prata",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text
    ocorrencia_id = UUID(resp.json()["ocorrencia_id"])

    with Session(engine) as db:
        terceiro = db.execute(
            select(OcorrenciaVeiculoTerceiro).where(OcorrenciaVeiculoTerceiro.ocorrencia_id == ocorrencia_id)
        ).scalar_one()
        assert terceiro.modelo == "Gol prata"
        assert terceiro.cor is None


def test_converter_trunca_modelo_e_cor_que_excedem_o_limite_da_coluna(ambiente):
    """120 (terceiro_modelo_cor) não cabe em 60 (modelo) + 30 (cor) — o
    SQLite dos testes não aplica limite de VARCHAR, então isto prova só o
    truncamento em Python, mas é exatamente o que impede o INSERT de
    estourar contra o Postgres real de produção."""
    engine = ambiente["engine"]
    auth_a, _ = _criar_autorizacao(engine, coordenador=_COORD_A)
    modelo_longo = "M" * 80
    cor_longa = "C" * 50
    pre_oc_id = _criar_pre_ocorrencia(
        engine, auth_a,
        relato="Terceiro com modelo/cor longos demais.",
        terceiro_modelo_cor=f"{modelo_longo}/{cor_longa}",
    )

    _autenticar(ambiente, _COORD_A)
    resp = ambiente["http"].post(f"/pre-ocorrencias/{pre_oc_id}/converter", json={})
    assert resp.status_code == 200, resp.text
    ocorrencia_id = UUID(resp.json()["ocorrencia_id"])

    with Session(engine) as db:
        terceiro = db.execute(
            select(OcorrenciaVeiculoTerceiro).where(OcorrenciaVeiculoTerceiro.ocorrencia_id == ocorrencia_id)
        ).scalar_one()
        assert len(terceiro.modelo) == 60
        assert len(terceiro.cor) == 30
