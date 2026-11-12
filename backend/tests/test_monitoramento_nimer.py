"""Bloco B — POST /monitoramento/nimer, GET /monitoramento/robo/config e o
recebimento do SIM para todas as linhas de linha_monitorada.

⛔ Sem dado pessoal real: REs fictícios.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models.monitoramento import (
    LeituraVeiculo,
    LinhaMonitoradaCadastro,
    NimerColeta,
    NimerViagem,
    SimCodigoLinha,
)
from app.routers import monitoramento as router_mod
from app.services.monitoramento_cadastro import data_servico_de
from tests.ambiente_monitoramento import criar_engine
from tests.fixtures_nimer import tabela, timeline, viagem

TOKEN = "token-de-teste-do-robo"
DIA = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # 09:00 em SP


@pytest.fixture
def engine():
    return criar_engine()


@pytest.fixture
def token(monkeypatch):
    estado = {"token": TOKEN}
    monkeypatch.setattr(router_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"monitor_ingest_token": estado["token"]}))
    return lambda v: estado.update(token=v)


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
    return client.post("/monitoramento/nimer", json=corpo, headers=headers)


def _corpo(tl, linha="1726-10", status="ok", data="2026-11-10"):
    return {"linha_codigo": linha, "data_servico": data, "coletado_em": int(DIA.timestamp() * 1000),
            "origem": "teste", "status": status, "timeline": tl}


def _viagens(engine):
    with Session(engine) as db:
        return db.execute(select(NimerViagem).order_by(NimerViagem.prog_inicio)).scalars().all()


def _base():
    return [
        tabela("032104", [
            viagem(1, 1, "done", DIA, real=DIA + timedelta(minutes=2), departure="5001"),
            viagem(2, 2, "future", DIA + timedelta(hours=1), departure="5002"),
        ], carro="2412", motorista="90011", cobrador="90012"),
        tabela("032116", [
            viagem(1, 1, "lost", DIA + timedelta(minutes=10), departure="5003", motivo="1 - Falta de Motorista"),
        ], carro="22293", motorista="Sem Motorista", cobrador=None),
    ]


# ─── Autenticação ───────────────────────────────────────────────────────────

def test_nimer_token_errado_401_sem_header_401_e_desligado_503(client, engine, token):
    assert _enviar(client, _corpo(timeline(_base())), token="x").status_code == 401
    assert _enviar(client, _corpo(timeline(_base())), token=None).status_code == 401
    token("")
    assert _enviar(client, _corpo(timeline(_base())), token="").status_code == 503
    assert _viagens(engine) == []


def test_config_do_robo_exige_token(client, token):
    assert client.get("/monitoramento/robo/config").status_code == 401
    token("")
    assert client.get("/monitoramento/robo/config", headers={"X-Monitor-Token": ""}).status_code == 503


def test_nimer_corpo_grande_demais_413(client):
    r = client.post("/monitoramento/nimer", content=b"x" * (router_mod.LIMITE_CORPO_NIMER_BYTES + 1),
                    headers={"X-Monitor-Token": TOKEN, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_nimer_json_invalido_422(client):
    r = client.post("/monitoramento/nimer", content=b"{x", headers={"X-Monitor-Token": TOKEN,
                                                                    "Content-Type": "application/json"})
    assert r.status_code == 422


# ─── Upsert das viagens ─────────────────────────────────────────────────────

def test_grava_viagens_com_sentido_carro_e_sem_motorista(client, engine):
    r = _enviar(client, _corpo(timeline(_base())))
    assert r.status_code == 200, r.text
    assert r.json()["viagens"] == 3
    v1, v_perdida, v2 = _viagens(engine)
    assert v1.tabela == "032104" and v1.sentido == 1 and v1.status == "done"
    assert v1.carro == "2412" and v1.motorista_re == "90011" and v1.cobrador_re == "90012"
    assert v1.atraso_inicio_min == 2 and v1.fonte_inicio == "GPS"
    assert v2.sentido == 2 and v2.status == "future"
    # 5 dígitos do SIM viram os 4 do pátio; "Sem Motorista" não vira RE
    assert v_perdida.carro == "2293" and v_perdida.sem_motorista is True and v_perdida.motorista_re is None
    assert v_perdida.motivo_perda_texto == "1 - Falta de Motorista"
    with Session(engine) as db:
        (c,) = db.execute(select(NimerColeta)).scalars().all()
    assert c.status == "ok" and c.planejadas == 3 and c.realizadas == 1 and float(c.pontualidade_pct) == 87.5


def test_reenvio_atualiza_a_mesma_viagem_sem_duplicar(client, engine):
    _enviar(client, _corpo(timeline(_base())))
    tabelas = _base()
    tabelas[0]["trips"][1].update(status="done", realStartMs=int((DIA + timedelta(hours=1, minutes=5)).timestamp() * 1000))
    _enviar(client, _corpo(timeline(tabelas)))
    viagens = _viagens(engine)
    assert len(viagens) == 3
    assert [v.status for v in viagens if v.nimer_departure_id == "5002"] == ["done"]


def test_viagem_que_some_do_nimer_fica_fora_sem_ser_apagada(client, engine):
    _enviar(client, _corpo(timeline(_base())))
    _enviar(client, _corpo(timeline(_base()[:1])))  # tabela 032116 retirada
    por_dep = {v.nimer_departure_id: v for v in _viagens(engine)}
    assert por_dep["5003"].removida_em is not None
    assert por_dep["5001"].removida_em is None


def test_viagem_sem_departure_id_ganha_chave_da_tabela(client, engine):
    tl = timeline([tabela("032105", [viagem(3, 2, "future", DIA, departure=None)], table_id="777")])
    _enviar(client, _corpo(tl))
    (v,) = _viagens(engine)
    assert v.nimer_departure_id == "t777-3"


def test_linha_nao_monitorada_descartada(client, engine):
    r = _enviar(client, _corpo(timeline(_base()), linha="8000-10"))
    assert r.status_code == 200 and r.json()["status"] == "descartada"
    assert _viagens(engine) == []
    with Session(engine) as db:
        assert db.execute(select(NimerColeta)).scalars().all() == []


def test_linha_desativada_descartada(client, engine):
    with Session(engine) as db:
        db.get(LinhaMonitoradaCadastro, "1726-10").ativo = False
        db.commit()
    assert _enviar(client, _corpo(timeline(_base()))).json()["status"] == "descartada"


def test_sessao_do_nimer_caida_so_grava_o_batimento(client, engine):
    r = _enviar(client, {"linha_codigo": "1726", "status": "sessao", "mensagem": "login"})
    assert r.status_code == 200 and r.json()["status"] == "sessao"
    assert _viagens(engine) == []
    with Session(engine) as db:
        (c,) = db.execute(select(NimerColeta)).scalars().all()
    assert c.status == "sessao" and c.linha_codigo == "1726-10"


def test_data_servico_aceita_formato_do_nimer(client, engine):
    _enviar(client, _corpo(timeline(_base()), data="10/11/2026"))
    assert {v.data_servico.isoformat() for v in _viagens(engine)} == {"2026-11-10"}


def test_data_de_servico_antes_das_4h_e_o_dia_anterior():
    assert data_servico_de(datetime(2026, 11, 11, 5, 30, tzinfo=timezone.utc)).isoformat() == "2026-11-10"  # 02:30 SP
    assert data_servico_de(datetime(2026, 11, 11, 7, 30, tzinfo=timezone.utc)).isoformat() == "2026-11-11"  # 04:30 SP


# ─── Config do robô ─────────────────────────────────────────────────────────

def test_config_devolve_linhas_ativas_e_carros_escalados(client, engine):
    with Session(engine) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo="2011-10", nimer_line_id=None, ativo=True))
        db.add(LinhaMonitoradaCadastro(linha_codigo="9000-10", nimer_line_id=1, ativo=False))
        db.commit()
    hoje = data_servico_de().isoformat()
    _enviar(client, _corpo(timeline(_base()), data=hoje))
    r = client.get("/monitoramento/robo/config", headers={"X-Monitor-Token": TOKEN})
    assert r.status_code == 200
    j = r.json()
    assert j["linhas"] == [{"linha_codigo": "1726-10", "nimer_line_id": 33534},
                           {"linha_codigo": "2011-10", "nimer_line_id": None}]
    assert j["intervalos"] == {"sim_seg": 60, "nimer_seg": 180, "rastro_seg": 300}  # 300: bloco B da fase 3
    assert j["prefixos_escalados"] == ["2293", "2412"]


# ─── SIM: todas as linhas monitoradas ───────────────────────────────────────

def _sim(client, veiculos):
    r = client.post("/monitoramento/leituras", headers={"X-Monitor-Token": TOKEN},
                    json={"origem": "t", "status": "ok", "veiculos": veiculos})
    assert r.status_code == 200, r.text
    return r.json()


def _carro_sim(p, li, linha, s, minutos_atras=1):
    d = int((datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)).timestamp() * 1000)
    return {"p": p, "li": li, "l": linha, "s": s, "la": -23.45, "lo": -46.57, "vel": 10, "loc": "Rua", "d": d}


def test_sim_aceita_segunda_linha_monitorada_e_aprende_o_codigo(client, engine):
    with Session(engine) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo="2011-10", ativo=True))
        db.commit()
    j = _sim(client, [_carro_sim(22742, 5555, "2011-10", 1), _carro_sim(22412, 1403, "1726-10", 1),
                      _carro_sim(23333, 6666, "8000-10", 1)])
    assert j["aceitos"] == 2
    with Session(engine) as db:
        aprendido = db.get(SimCodigoLinha, 5555)
        assert aprendido.linha_codigo == "2011-10" and aprendido.sentido == 1
        assert db.get(SimCodigoLinha, 6666) is None
        linhas = {lv.prefixo: lv.linha_codigo for lv in db.execute(select(LeituraVeiculo)).scalars()}
    assert linhas == {"22742": "2011-10", "22412": "1726-10"}


def test_sim_aceita_carro_escalado_rodando_em_linha_nao_monitorada(client, engine):
    hoje = data_servico_de().isoformat()
    _enviar(client, _corpo(timeline(_base()), data=hoje))  # escala 2412 e 2293
    j = _sim(client, [_carro_sim(22293, 7777, "2011-10", 2), _carro_sim(24444, 7777, "2011-10", 2)])
    assert j["aceitos"] == 1
    with Session(engine) as db:
        (lv,) = db.execute(select(LeituraVeiculo)).scalars().all()
        assert lv.prefixo == "22293" and lv.linha_codigo == "2011-10"
        # linha não monitorada: o `li` dela não é aprendido
        assert db.get(SimCodigoLinha, 7777) is None
