"""Consulta de placa (services/consulta_placa.py + POST /ocorrencias/consulta-placa).

⛔ NENHUMA chamada real ao fornecedor — cada consulta real custa dinheiro do
Alisson. O httpx.Client do serviço é trocado por um com httpx.MockTransport;
qualquer teste que tentasse sair para a rede falharia (a transport de teste
é a única que existe dentro do serviço durante o teste).

SQLite em memória com ATTACH do schema `coordenadoria` (mesmo padrão de
tests/ambiente_acesso.py). Placas no formato das placas de teste do
fornecedor (TST0001 encontrada, TST0002 não encontrada, TST0003 503).
"""
import json
import logging
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.cadastro import Funcionario
from app.models.consulta_placa import ConsultaPlaca
from app.routers import ocorrencias as router_mod
from app.services import consulta_placa as servico

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

CHAVE = "cdp_test_CHAVE-SECRETA-QUE-NAO-PODE-VAZAR"
_AUTOR = Funcionario(id=uuid4(), re="41999", nome="Coordenador Consulta Teste")


def _veiculo(**extra):
    base = {
        "plate": "TST0001", "brand": "FIAT", "model": "UNO",
        "version": "Uno Mille Economy 1.0 Flex 4p",
        "manufacture_year": 2012, "model_year": 2013, "color": "Prata",
        "fuel": "ALCOOL/GASOLINA", "type": "AUTOMOVEL", "city": "FORTALEZA",
        "state": "CE", "theft": False,
        "fipe": {"code": "001177-0", "amount": "18000.00", "versions": []},
    }
    base.update(extra)
    return base


def _corpo_found(**extra_veiculo):
    return {
        "object": "lookup", "id": "lkp_teste", "status": "completed", "plate": "TST0001",
        "result": "found", "price": {"amount": "0.07", "currency": "BRL"}, "free": False,
        "vehicle": _veiculo(**extra_veiculo),
    }


def _corpo_not_found():
    return {
        "object": "lookup", "id": "lkp_teste2", "status": "completed", "plate": "TST0002",
        "result": "not_found", "price": {"amount": "0.07", "currency": "BRL"}, "vehicle": None,
    }


def _problema(status_code, code):
    return httpx.Response(
        status_code, headers={"Content-Type": "application/problem+json"},
        json={"status": status_code, "code": code, "request_id": "req_teste", "detail": "x"},
    )


class _Fornecedor:
    """Fornecedor simulado: conta chamadas e guarda os cabeçalhos recebidos."""

    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        if isinstance(self.resposta, Exception):
            raise self.resposta
        if isinstance(self.resposta, httpx.Response):
            return self.resposta
        return httpx.Response(201, json=self.resposta)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(eng, tables=[Funcionario.__table__, ConsultaPlaca.__table__])
    with Session(eng) as db:
        db.add(Funcionario(id=_AUTOR.id, re=_AUTOR.re, nome=_AUTOR.nome))
        db.commit()
    return eng


@pytest.fixture
def config(monkeypatch):
    """Settings com chave de teste; `config(api_key=None)` etc. muda campos."""
    estado = {"api_key": CHAVE, "limite": 30, "cache": 30}

    def _settings():
        return get_settings().model_copy(update={
            "consulta_placa_api_key": estado["api_key"],
            "consulta_placa_api_url": "https://fornecedor.invalid/api/v1",
            "consulta_placa_limite_diario": estado["limite"],
            "consulta_placa_cache_dias": estado["cache"],
        })

    monkeypatch.setattr(servico, "get_settings", _settings)
    monkeypatch.setattr(router_mod, "get_settings", _settings)

    def ajustar(**kw):
        estado.update(kw)
    return ajustar


@pytest.fixture
def fornecedor(monkeypatch):
    """Instala o fornecedor simulado no httpx do serviço. Default: found."""
    atual = _Fornecedor(_corpo_found())
    cliente_real = httpx.Client

    def _cliente(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda req: atual(req))
        return cliente_real(*args, **kwargs)

    monkeypatch.setattr(servico.httpx, "Client", _cliente)

    class _Controle:
        def responder(self, resposta):
            nonlocal atual
            atual = _Fornecedor(resposta)
            return atual

        @property
        def chamadas(self):
            return atual.chamadas

    return _Controle()


def _linhas(engine):
    with Session(engine) as db:
        return db.execute(select(ConsultaPlaca)).scalars().all()


def _inserir(engine, *, placa="TST0001", resultado="ENCONTRADA", quando=None, dados=None):
    with Session(engine) as db:
        db.add(ConsultaPlaca(
            placa=placa, resultado=resultado,
            dados=dados if dados is not None else (
                {k: _veiculo().get(k) for k in servico._CAMPOS_GUARDADOS} if resultado == "ENCONTRADA" else None
            ),
            consultado_em=quando or datetime.now(timezone.utc), consultado_por=_AUTOR.id,
        ))
        db.commit()


def _consultar(engine, placa="TST0001"):
    with Session(engine) as db:
        return servico.consultar(db, placa, _AUTOR.id)


# ─── 1. Antes de chamar a API ────────────────────────────────────────────────

@pytest.mark.parametrize("placa", ["", "ABC", "AB12345", "1234ABC", "ABC12345", None])
def test_placa_invalida_nao_chama_api(engine, config, fornecedor, placa):
    r = _consultar(engine, placa)
    assert r.situacao == "PLACA_INVALIDA"
    assert fornecedor.chamadas == []
    assert _linhas(engine) == []


def test_placa_com_hifen_e_minuscula_e_normalizada(engine, config, fornecedor):
    r = _consultar(engine, "tst-0001")
    assert r.situacao == "ENCONTRADA"
    assert json.loads(fornecedor.chamadas[0].content) == {"plate": "TST0001"}


def test_cache_dentro_de_30_dias_nao_chama_api(engine, config, fornecedor):
    _inserir(engine, quando=datetime.now(timezone.utc) - timedelta(days=29))
    r = _consultar(engine)
    assert r.situacao == "ENCONTRADA"
    assert r.origem == "cache"
    assert r.dados["marca"] == "FIAT"
    assert fornecedor.chamadas == []
    assert len(_linhas(engine)) == 1  # cache não grava


def test_cache_vencido_chama_api(engine, config, fornecedor):
    _inserir(engine, quando=datetime.now(timezone.utc) - timedelta(days=31))
    r = _consultar(engine)
    assert r.origem == "api"
    assert len(fornecedor.chamadas) == 1
    assert len(_linhas(engine)) == 2


def test_nao_encontrada_nao_vale_como_cache(engine, config, fornecedor):
    _inserir(engine, resultado="NAO_ENCONTRADA")
    r = _consultar(engine)
    assert r.origem == "api"
    assert len(fornecedor.chamadas) == 1


def test_sem_chave_nao_chama(engine, config, fornecedor):
    config(api_key=None)
    assert _consultar(engine).situacao == "INDISPONIVEL_SEM_CHAVE"
    config(api_key="   ")
    assert _consultar(engine).situacao == "INDISPONIVEL_SEM_CHAVE"
    assert fornecedor.chamadas == []


def test_limite_diario_bloqueia_a_31a(engine, config, fornecedor):
    fornecedor.responder(_corpo_not_found())
    for i in range(30):
        r = _consultar(engine, f"TST{i:04d}")
        assert r.situacao == "NAO_ENCONTRADA", i
    assert len(fornecedor.chamadas) == 30

    r = _consultar(engine, "TST0099")
    assert r.situacao == "LIMITE_DIARIO"
    assert r.mensagem == "Limite de consultas de hoje atingido. Preencha à mão."
    assert len(fornecedor.chamadas) == 30
    assert len(_linhas(engine)) == 30


def test_limite_diario_conta_pelo_dia_de_sao_paulo(engine, config, fornecedor):
    """Consultas de ontem (dia de SP) não contam para hoje."""
    agora_sp = datetime.now(FUSO_OPERACAO)
    inicio_hoje_sp = agora_sp.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem = (inicio_hoje_sp - timedelta(minutes=1)).astimezone(timezone.utc)
    for i in range(30):
        _inserir(engine, placa=f"TST{i:04d}", resultado="NAO_ENCONTRADA", quando=ontem)
    config(limite=30)
    assert _consultar(engine).origem == "api"


# ─── 2. Resposta do fornecedor ───────────────────────────────────────────────

def test_found_grava_uma_linha_com_dados_normalizados(engine, config, fornecedor):
    r = _consultar(engine)
    assert r.situacao == "ENCONTRADA"
    assert r.origem == "api"
    assert r.dados == {
        "marca": "FIAT",
        "modelo": "Uno Mille Economy 1.0 Flex 4p",
        "ano": "2013",
        "ano_fabricacao": 2012,
        "ano_modelo": 2013,
        "cor": "PRATA",
        "cor_original": "Prata",
        "cidade_placa": "FORTALEZA",
        "estado_placa": "CE",
    }
    linhas = _linhas(engine)
    assert len(linhas) == 1
    linha = linhas[0]
    assert linha.resultado == "ENCONTRADA"
    assert linha.placa == "TST0001"
    assert str(linha.preco) == "0.07"
    assert linha.consultado_por == _AUTOR.id
    # D9: roubo/furto e FIPE nunca gravados.
    assert "theft" not in linha.dados
    assert "fipe" not in linha.dados


def test_headers_da_chamada(engine, config, fornecedor):
    _consultar(engine)
    req = fornecedor.chamadas[0]
    assert req.method == "POST"
    assert str(req.url) == "https://fornecedor.invalid/api/v1/lookups"
    assert req.headers["Authorization"] == f"Bearer {CHAVE}"
    assert req.headers["Content-Type"] == "application/json"
    assert _uuid_mod.UUID(req.headers["Idempotency-Key"]).version == 4


def test_idempotency_key_nova_por_consulta(engine, config, fornecedor):
    fornecedor.responder(_corpo_not_found())
    _consultar(engine, "TST0002")
    _consultar(engine, "TST0002")
    chaves = {r.headers["Idempotency-Key"] for r in fornecedor.chamadas}
    assert len(chaves) == 2


def test_cor_grena_sem_acento(engine, config, fornecedor):
    fornecedor.responder(_corpo_found(color="Grena"))
    assert _consultar(engine).dados["cor"] == "GRENÁ"


def test_cor_desconhecida_vai_crua_em_cor_original(engine, config, fornecedor):
    fornecedor.responder(_corpo_found(color="AZUL CELESTE PEROLIZADO"))
    d = _consultar(engine).dados
    assert d["cor"] is None
    assert d["cor_original"] == "AZUL CELESTE PEROLIZADO"


def test_modelo_sem_versao_quando_nao_cabe_em_60(engine, config, fornecedor):
    fornecedor.responder(_corpo_found(model="ONIX", version="X" * 70))
    assert _consultar(engine).dados["modelo"] == "ONIX"


def test_modelo_junta_versao_que_nao_repete_o_modelo(engine, config, fornecedor):
    fornecedor.responder(_corpo_found(model="ONIX", version="LT 1.0 TURBO"))
    assert _consultar(engine).dados["modelo"] == "ONIX LT 1.0 TURBO"


def test_ano_igual_vai_um_so_e_uf_estranha_vira_none(engine, config, fornecedor):
    fornecedor.responder(_corpo_found(manufacture_year=2020, model_year=2020, state="XX"))
    d = _consultar(engine).dados
    assert d["ano"] == "2020"
    assert d["estado_placa"] is None


def test_not_found_grava(engine, config, fornecedor):
    fornecedor.responder(_corpo_not_found())
    r = _consultar(engine, "TST0002")
    assert r.situacao == "NAO_ENCONTRADA"
    assert r.dados is None
    assert r.mensagem == "Placa não encontrada na base. Preencha à mão."
    linhas = _linhas(engine)
    assert len(linhas) == 1 and linhas[0].resultado == "NAO_ENCONTRADA"


@pytest.mark.parametrize("resposta, situacao", [
    (_problema(402, "insufficient_balance"), "SEM_SALDO"),
    (_problema(402, "daily_limit_exceeded"), "LIMITE_FORNECEDOR"),
    (_problema(503, "service_unavailable"), "INDISPONIVEL"),
    (_problema(429, "rate_limited"), "LIMITE_FORNECEDOR"),
    (_problema(401, "invalid_api_key"), "ERRO_CHAVE"),
    (_problema(403, "test_plate_required"), "ERRO_CHAVE"),
    (_problema(422, "validation_failed"), "PLACA_INVALIDA"),
    (_problema(500, "internal_error"), "INDISPONIVEL"),
    (httpx.Response(201, text="nao e json"), "INDISPONIVEL"),
    (httpx.ReadTimeout("lento"), "INDISPONIVEL"),
    (httpx.ConnectError("sem rede"), "INDISPONIVEL"),
])
def test_erros_nao_gravam(engine, config, fornecedor, resposta, situacao):
    fornecedor.responder(resposta)
    r = _consultar(engine, "TST0003")
    assert r.situacao == situacao
    assert r.dados is None
    assert r.mensagem
    assert _linhas(engine) == []


def test_sem_saldo_mensagem(engine, config, fornecedor):
    fornecedor.responder(_problema(402, "insufficient_balance"))
    assert _consultar(engine).mensagem == "Saldo da consulta acabou — avise o Alisson. Preencha à mão."


# ─── 3. Endpoint ─────────────────────────────────────────────────────────────

def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def cliente(engine):
    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    app.dependency_overrides[escrita_dep] = lambda: _AUTOR
    app.dependency_overrides[get_db] = _get_db_teste
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(escrita_dep, None)
        app.dependency_overrides.pop(get_db, None)


def test_endpoint_found(cliente, config, fornecedor):
    resp = cliente.post("/ocorrencias/consulta-placa", json={"placa": "TST-0001"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["situacao"] == "ENCONTRADA"
    assert corpo["origem"] == "api"
    assert corpo["dados"]["cor"] == "PRATA"
    assert corpo["mensagem"]


def test_endpoint_erro_do_fornecedor_e_200_com_mensagem(cliente, config, fornecedor):
    fornecedor.responder(_problema(503, "service_unavailable"))
    resp = cliente.post("/ocorrencias/consulta-placa", json={"placa": "TST0003"})
    assert resp.status_code == 200
    assert resp.json() == {
        "situacao": "INDISPONIVEL", "origem": None, "dados": None,
        "mensagem": "Consulta de placa indisponível agora. Preencha à mão.",
    }


def test_chave_nunca_aparece_na_resposta_nem_no_log(cliente, config, fornecedor, caplog):
    caplog.set_level(logging.DEBUG)
    respostas = []
    for resposta in (
        _corpo_found(), _corpo_not_found(), _problema(401, "invalid_api_key"),
        _problema(402, "insufficient_balance"), httpx.ReadTimeout("lento"),
    ):
        fornecedor.responder(resposta)
        respostas.append(cliente.post("/ocorrencias/consulta-placa", json={"placa": "TST0009"}).text)
    respostas.append(cliente.get("/openapi.json").text)
    for texto in respostas:
        assert CHAVE not in texto
    assert CHAVE not in caplog.text
    # Corpo cru da resposta (dado do veículo) também não vai para o log.
    assert "Uno Mille Economy" not in caplog.text


def test_catalogos_expoe_so_o_booleano(engine, config):
    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)

    class _SemCatalogo:
        def execute(self, *a, **k):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []
            return _R()

    app.dependency_overrides[leitura_dep] = lambda: _AUTOR
    app.dependency_overrides[get_db] = lambda: _SemCatalogo()
    try:
        c = TestClient(app)
        corpo = c.get("/ocorrencias/catalogos").json()
        assert corpo["consulta_placa_disponivel"] is True
        assert CHAVE not in json.dumps(corpo)
        config(api_key=None)
        assert c.get("/ocorrencias/catalogos").json()["consulta_placa_disponivel"] is False
    finally:
        app.dependency_overrides.pop(leitura_dep, None)
        app.dependency_overrides.pop(get_db, None)


class _DBSoLeitura:
    """Funcionario autenticado com pode_ler=True e pode_escrever=False em
    'ocorrencia' (mesmo padrão de tests/test_importacao_permissao.py)."""

    def __init__(self, funcionario):
        self._funcionario = funcionario

    def get(self, model, id_):
        if model is Funcionario and id_ == self._funcionario.id:
            return self._funcionario
        return None

    def execute(self, stmt, params=None, *a, **k):
        class _Linha:
            pode_ler = True
            pode_escrever = False

        class _R:
            def scalar_one_or_none(self):
                return None

            def fetchone(self):
                return _Linha()
        return _R()


def test_usuario_so_de_leitura_recebe_403(config, fornecedor):
    func = Funcionario(id=uuid4(), re="70777", nome="Só Leitura")
    token = create_access_token(subject=func.id)
    app.dependency_overrides[get_db] = lambda: _DBSoLeitura(func)
    try:
        resp = TestClient(app).post(
            "/ocorrencias/consulta-placa", json={"placa": "TST0001"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
        assert fornecedor.chamadas == []
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_sem_token_recebe_401(config, fornecedor):
    resp = TestClient(app).post("/ocorrencias/consulta-placa", json={"placa": "TST0001"})
    assert resp.status_code == 401
    assert fornecedor.chamadas == []
