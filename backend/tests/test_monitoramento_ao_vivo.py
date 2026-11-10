"""GET /monitoramento/... — leitura para as telas (ao-vivo.html).

SQLite em memória (ATTACH monitoramento). A permissão (exige_qualquer
fiscalizacao/fiscalizacao_painel, que lê vw_acesso_efetivo no Postgres) é
trocada por um funcionário fictício nos testes de conteúdo; o teste sem
override prova que sem token não entra.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.monitoramento import Coleta, LeituraVeiculo
from app.routers import monitoramento as router_mod
from app.services import monitoramento_ao_vivo as servico
from app.services import monitoramento_regras as regras
from app.services.monitoramento_linhas import obter_linha

TOKEN = "token-de-teste-do-robo"
TP_REAL = (-23.438087, -46.566383)
_CHECKER = router_mod.Leitura.__metadata__[0].dependency


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS monitoramento")

    Base.metadata.create_all(eng, tables=[LeituraVeiculo.__table__, Coleta.__table__])
    return eng


@pytest.fixture
def client(engine, monkeypatch):
    def _db():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(router_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"monitor_ingest_token": TOKEN}))
    servico.limpar_cache()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[_CHECKER] = lambda: Funcionario(id=uuid4(), re="16867", nome="Fiscal Teste")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(_CHECKER, None)
        servico.limpar_cache()


@pytest.fixture(scope="module")
def linha():
    return obter_linha("1726-10")


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _veic(p, li, s, ponto, quando, loc=None):
    return {"p": p, "li": li, "l": "1726-10", "s": s, "la": ponto[0], "lo": ponto[1], "vel": 20,
            "loc": loc or "Rua Teste", "d": _ms(quando)}


def _enviar(client, veiculos, status="ok"):
    r = client.post("/monitoramento/leituras", headers={"X-Monitor-Token": TOKEN},
                    json={"origem": "teste", "status": status, "mensagem": "x", "veiculos": veiculos})
    assert r.status_code == 200, r.text
    return r


def _pto(linha, s, i):
    return linha.tracados[s][i]


def test_sem_token_401(engine):
    servico.limpar_cache()
    assert TestClient(app).get("/monitoramento/linhas/1726-10/ao-vivo").status_code == 401


def test_linhas_monitoradas(client):
    r = client.get("/monitoramento/linhas")
    assert r.status_code == 200
    (lm,) = r.json()
    assert lm["codigo"] == "1726-10"
    assert lm["terminais"]["TS"] == "TS Terminal Santana"


def test_linha_nao_monitorada_404_em_portugues(client):
    r = client.get("/monitoramento/linhas/8000-10/ao-vivo")
    assert r.status_code == 404
    assert r.json()["erro"] == "A linha 8000-10 não é monitorada ao vivo"


def test_tracado_estatico_com_cache_control(client):
    r = client.get("/monitoramento/linhas/1726/tracado")  # aceita o código em outro formato
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    j = r.json()
    assert [s["sentido"] for s in j["sentidos"]] == [1, 2]
    assert len(j["sentidos"][0]["pontos"]) > 500
    assert {t["codigo"] for t in j["terminais"]} == {"TP", "TS"}
    assert j["garagem"]["centro"] == [-23.4902, -46.561]


def test_ao_vivo_3_carros_da_1726_e_1_de_outra_linha_devolve_so_os_3(client, linha):
    agora = datetime.now(timezone.utc)
    _enviar(client, [
        _veic(22412, 1403, 1, _pto(linha, 1, 100), agora - timedelta(seconds=30)),
        _veic(22413, 34171, 2, _pto(linha, 2, 200), agora - timedelta(seconds=40)),
        _veic(22414, 1403, 1, TP_REAL, agora - timedelta(seconds=20)),
        _veic(29999, 555, 1, _pto(linha, 1, 50), agora - timedelta(seconds=10)),
    ])
    r = client.get("/monitoramento/linhas/1726-10/ao-vivo")
    assert r.status_code == 200
    j = r.json()
    assert sorted(c["prefixo"] for c in j["carros"]) == ["22412", "22413", "22414"]
    por = {c["prefixo"]: c for c in j["carros"]}
    assert por["22412"]["prefixo_patio"] == "2412"
    assert por["22414"]["zona"] == "TP" and por["22414"]["classe"] == "terminal"
    assert por["22412"]["classe"] == "ok" and por["22412"]["destino"] == "TS"
    assert j["coleta"]["status"] == "ok" and j["coleta"]["ultima_ok_em"]
    assert j["contadores"]["terminal"] == 1 and j["contadores"]["rodando"] == 2
    assert j["alertas_agora"] == []


def test_ao_vivo_mostra_coleta_com_sessao_caida(client):
    _enviar(client, [], status="sessao")
    j = client.get("/monitoramento/linhas/1726-10/ao-vivo").json()
    assert j["coleta"]["status"] == "sessao"
    assert j["coleta"]["ultima_ok_em"] is None
    assert j["carros"] == []


# ─── Montagem direta (sem HTTP), com "agora" controlado ─────────────────────

def _gravar(engine, prefixo, s, pontos, inicio, passo_min=1, recebido=None):
    with Session(engine) as db:
        for k, p in enumerate(pontos):
            quando = inicio + timedelta(minutes=k * passo_min)
            db.add(LeituraVeiculo(posicao_em=quando, prefixo=prefixo, linha_codigo="1726-10",
                                  sim_codigo=1403 if s == 1 else 34171, sentido=s, lat=p[0], lon=p[1],
                                  velocidade=0, local_sim="Rua Teste", recebido_em=recebido or quando))
        db.commit()


def _coleta_ok(engine, quando):
    with Session(engine) as db:
        db.add(Coleta(recebido_em=quando, origem="teste", status="ok", qtd_veiculos=1))
        db.commit()


def test_desvio_vira_alerta_agora_e_ocorrencia_do_dia(engine, linha):
    agora = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 12:00 em SP
    na_rota = [_pto(linha, 1, i) for i in range(150, 160)]
    base = na_rota[-1]
    fora = [(base[0], base[1] + 0.005 + k * 0.0003) for k in range(4)]
    # o desvio só vale se os pontos estão fora dos dois traçados
    assert all(regras.classificar({"t": 0, "la": p[0], "lo": p[1], "s": 1}, linha)["_z"] == "fora" for p in fora)
    pontos = na_rota + fora
    _gravar(engine, "22412", 1, pontos, agora - timedelta(minutes=len(pontos) - 1))
    _coleta_ok(engine, agora)
    with Session(engine) as db:
        j = servico.montar_ao_vivo(db, linha, agora)
    (c,) = j["carros"]
    assert c["classe"] == "alerta" and c["zona"] == "fora"
    (a,) = j["alertas_agora"]
    assert a["tipo"] == "desvio" and a["nivel"] == "alerta" and a["ativo"] is True
    assert a["prefixo_patio"] == "2412"
    assert "Fora do itinerário" in a["texto"]
    assert [o["tipo"] for o in j["ocorrencias_dia"]] == ["desvio"]
    assert j["contadores"]["alerta"] == 1


def test_sumiu_quando_ficou_fora_das_ultimas_coletas(engine, linha):
    agora = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    _gravar(engine, "22412", 1, [_pto(linha, 1, 100)], agora - timedelta(minutes=10))
    _gravar(engine, "22413", 1, [_pto(linha, 1, 300)], agora - timedelta(seconds=30))
    _gravar(engine, "22414", 1, [_pto(linha, 1, 200)], agora - timedelta(minutes=90))  # sumiu há 1 h 30
    _coleta_ok(engine, agora)
    with Session(engine) as db:
        j = servico.montar_ao_vivo(db, linha, agora)
    por = {c["prefixo"]: c for c in j["carros"]}
    assert "22414" not in por  # some da tela depois de 1 h
    assert por["22412"]["sumiu"] is True
    assert por["22413"]["sumiu"] is False and por["22413"]["classe"] == "ok"
    assert j["contadores"]["sumiram"] == 1
    # semtx não entra em "ocorrências do dia" (igual à POC)
    assert all(o["tipo"] != "semtx" for o in j["ocorrencias_dia"])


def test_robo_parado_nao_marca_ninguem_como_sumido(engine, linha):
    agora = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    _gravar(engine, "22412", 1, [_pto(linha, 1, 100)], agora - timedelta(minutes=2))
    with Session(engine) as db:  # nenhuma coleta OK registrada
        j = servico.montar_ao_vivo(db, linha, agora)
    assert j["carros"][0]["sumiu"] is False


def test_leitura_fora_da_janela_de_4h_nao_entra(engine, linha):
    agora = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    _gravar(engine, "22412", 1, [_pto(linha, 1, 100)], agora - timedelta(hours=5))
    with Session(engine) as db:
        assert servico.montar_ao_vivo(db, linha, agora)["carros"] == []


def test_rastro_dos_ultimos_90_min(client, engine, linha):
    agora = datetime.now(timezone.utc)
    pontos = [_pto(linha, 1, i) for i in range(100, 220, 1)]  # 120 leituras, 1/min
    _gravar(engine, "22412", 1, pontos, agora - timedelta(minutes=len(pontos) - 1))
    r = client.get("/monitoramento/linhas/1726-10/carros/22412/rastro")
    assert r.status_code == 200
    j = r.json()
    assert j["minutos"] == 90 and j["prefixo_patio"] == "2412"
    assert 89 <= len(j["pontos"]) <= 91
    assert {p["zona"] for p in j["pontos"]} <= {"rota", "TP", "TS", "fora", "garagem"}
    assert client.get("/monitoramento/linhas/1726-10/carros/abc/rastro").status_code == 422
    assert client.get("/monitoramento/linhas/1726-10/carros/22412/rastro?minutos=999").status_code == 422
