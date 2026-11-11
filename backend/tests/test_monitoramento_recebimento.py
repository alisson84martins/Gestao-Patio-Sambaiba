"""POST /monitoramento/leituras — recebimento das posições enviadas pelo robô.

SQLite em memória com ATTACH do schema `monitoramento` (mesmo padrão de
tests/test_consulta_placa.py). Sem dado pessoal: só carro e posição.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models.monitoramento import Coleta, LeituraVeiculo
from app.routers import monitoramento as router_mod
from tests.ambiente_monitoramento import criar_engine

TOKEN = "token-de-teste-do-robo"
T0 = 1794318439000  # epoch ms


def _carro(p=22412, li=34171, s=2, d=T0, la=-23.4381, lo=-46.5664, **extra):
    v = {"p": p, "li": li, "l": "1726-10", "s": s, "la": la, "lo": lo, "vel": 0,
         "loc": "Av. Cabuçu x Estr. Tres Cruzes", "d": d}
    v.update(extra)
    return v


def _corpo(veiculos, status="ok"):
    return {"origem": "extensao-teste", "status": status, "mensagem": "teste", "veiculos": veiculos}


@pytest.fixture
def engine():
    return criar_engine()


@pytest.fixture
def token(monkeypatch):
    estado = {"token": TOKEN}

    def _settings():
        return get_settings().model_copy(update={"monitor_ingest_token": estado["token"]})

    monkeypatch.setattr(router_mod, "get_settings", _settings)

    def ajustar(valor):
        estado["token"] = valor
    return ajustar


@pytest.fixture
def client(engine, token):
    def _db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _enviar(client, corpo, token=TOKEN):
    headers = {} if token is None else {"X-Monitor-Token": token}
    return client.post("/monitoramento/leituras", json=corpo, headers=headers)


def _leituras(engine):
    with Session(engine) as db:
        return db.execute(select(LeituraVeiculo).order_by(LeituraVeiculo.posicao_em)).scalars().all()


def _coletas(engine):
    with Session(engine) as db:
        return db.execute(select(Coleta).order_by(Coleta.id)).scalars().all()


def test_token_errado_401_e_nao_grava(client, engine):
    r = _enviar(client, _corpo([_carro()]), token="outro")
    assert r.status_code == 401
    assert _leituras(engine) == [] and _coletas(engine) == []


def test_sem_header_401(client):
    assert _enviar(client, _corpo([_carro()]), token=None).status_code == 401


def test_token_vazio_na_config_desliga_com_503(client, engine, token):
    token("")
    r = _enviar(client, _corpo([_carro()]), token="")
    assert r.status_code == 503
    assert _leituras(engine) == []


def test_grava_leitura_da_1726_com_codigo_canonico(client, engine):
    r = _enviar(client, _corpo([_carro()]))
    assert r.status_code == 200, r.text
    assert r.json()["gravados"] == 1
    (lv,) = _leituras(engine)
    assert lv.prefixo == "22412"
    assert lv.linha_codigo == "1726-10"
    assert lv.sim_codigo == 34171 and lv.sentido == 2
    assert lv.posicao_em.replace(tzinfo=timezone.utc) == datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    assert lv.local_sim == "Av. Cabuçu x Estr. Tres Cruzes"


def test_li_de_outra_linha_descartado_em_silencio(client, engine):
    r = _enviar(client, _corpo([_carro(p=22412), _carro(p=11111, li=999, l="8000-10"),_carro(p=22413, li=1403, s=1)]))
    assert r.status_code == 200
    assert r.json()["recebidos"] == 3 and r.json()["aceitos"] == 2
    assert sorted(lv.prefixo for lv in _leituras(engine)) == ["22412", "22413"]
    assert _coletas(engine)[-1].qtd_veiculos == 2


def test_duplicado_ignorado_no_reenvio_e_no_mesmo_envio(client, engine):
    assert _enviar(client, _corpo([_carro(), _carro()])).json()["gravados"] == 1
    r = _enviar(client, _corpo([_carro(), _carro(d=T0 + 60000)]))
    assert r.status_code == 200
    assert r.json()["gravados"] == 1
    assert len(_leituras(engine)) == 2
    assert len(_coletas(engine)) == 2


def test_status_sessao_sem_veiculos_grava_coleta(client, engine):
    r = _enviar(client, _corpo([], status="sessao"))
    assert r.status_code == 200
    (c,) = _coletas(engine)
    assert c.status == "sessao" and c.qtd_veiculos == 0 and c.origem == "extensao-teste"


def test_status_desconhecido_vira_erro(client, engine):
    _enviar(client, _corpo([], status="???"))
    assert _coletas(engine)[-1].status == "erro"


def test_veiculo_quebrado_descartado_sem_derrubar_o_envio(client, engine):
    r = _enviar(client, _corpo([{"p": "abc", "li": 34171}, _carro(), {"li": 1403}]))
    assert r.status_code == 200
    assert r.json()["aceitos"] == 1


def test_veiculos_demais_413(client, engine):
    r = _enviar(client, _corpo([_carro(p=20000 + i, d=T0) for i in range(router_mod.LIMITE_VEICULOS + 1)]))
    assert r.status_code == 413
    assert _leituras(engine) == []


def test_corpo_grande_demais_413(client):
    r = client.post(
        "/monitoramento/leituras",
        content=b"x" * (router_mod.LIMITE_CORPO_BYTES + 1),
        headers={"X-Monitor-Token": TOKEN, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_json_invalido_422(client):
    r = client.post("/monitoramento/leituras", content=b"{nao e json",
                    headers={"X-Monitor-Token": TOKEN, "Content-Type": "application/json"})
    assert r.status_code == 422
    assert "erro" in r.json()


def test_retencao_apaga_leitura_velha_e_coleta_velha(client, engine, monkeypatch):
    agora = datetime.now(timezone.utc)
    velho = int((agora - timedelta(days=4)).timestamp() * 1000)
    novo = int((agora - timedelta(minutes=1)).timestamp() * 1000)
    with Session(engine) as db:
        db.add(Coleta(recebido_em=agora - timedelta(days=8), status="ok", qtd_veiculos=0))
        db.commit()
    _enviar(client, _corpo([_carro(p=22412, d=velho)]))
    monkeypatch.setattr(router_mod, "_contador_recebimentos", 0)  # força a limpeza
    _enviar(client, _corpo([_carro(p=22413, d=novo)]))
    assert [lv.prefixo for lv in _leituras(engine)] == ["22413"]
    with Session(engine) as db:
        assert db.execute(select(func.count()).select_from(Coleta)).scalar_one() == 2
