"""v4.0 fase 3, bloco B — saídas confirmadas pelo Rastro do SIM.

O comportamento ERRADO que barramos (11/11/2026): o robô lê a posição a
cada 60 s e perde a saída de quem entra e sai do terminal entre duas
leituras — a viagem `done` do NIMER ficava "o SIM não viu" para sempre.
Agora o carro em dúvida vai para o robô, o Rastro chega, a saída casa e o
carro sai da lista (sem pedir a mesma janela de novo).
⛔ Sem dado pessoal real.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models.monitoramento import LeituraVeiculo, NimerViagem
from app.routers import monitoramento as router_mod
from app.services import fiscalizacao_tempo_real as tr
from app.services import monitoramento_rastro as rastro
from app.services.monitoramento_linhas import obter_linha
from tests.ambiente_monitoramento import criar_engine
from tests.cenario_tempo_real import DIA, em, viagem, viagem_sim

TOKEN = "token-de-teste-do-robo"
LI = {1: 1403, 2: 34171}


@pytest.fixture
def eng():
    rastro.limpar_cache()
    return criar_engine()


@pytest.fixture
def cliente(eng, monkeypatch):
    monkeypatch.setattr(router_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"monitor_ingest_token": TOKEN}))

    def _db():
        with Session(eng) as db:
            yield db
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        rastro.limpar_cache()


def _nimer(db, tabela, sentido, prog_min, status, carro, real_min=None):
    from app.models.monitoramento import Coleta
    if db.execute(select(Coleta)).first() is None:  # o robô do SIM está no ar
        db.add(Coleta(recebido_em=em(0), origem="teste", status="ok", qtd_veiculos=0))
    v = viagem(tabela, sentido, prog_min, status, carro=carro, real_min=real_min)
    db.add(NimerViagem(
        data_servico=DIA.date(), linha_codigo="1726-10", tabela=tabela, nimer_departure_id=f"d{tabela}{prog_min}",
        sentido=sentido, status=status, prog_inicio=v["prog_inicio"], prog_fim=v["prog_fim"],
        real_inicio=v["real_inicio"], carro=carro))
    db.commit()


def _pontos(leituras, passo_s=15):
    """Leituras de 1/min do cenário → pontos do Rastro (mais densos não importam aqui)."""
    return [{"d": int(r["em"].timestamp() * 1000), "la": r["la"], "lo": r["lo"], "s": r["s"], "li": LI[r["s"]],
             "l": "1726-10", "vel": 20} for r in leituras]


def _calcular(db, agora):
    linha = obter_linha("1726-10", db)
    return tr.montar_linha_tempo_real(db, linha, DIA.date(), agora)


def test_candidatos_programada_recente_e_done_sem_saida():
    viagens = [dict(viagem("1", 1, 30, "done", carro="2735", real_min=31), status_efetivo="done", saida_sim=None),
               dict(viagem("2", 1, 100, "lost", carro="2611"), status_efetivo="lost", saida_sim=None),
               dict(viagem("3", 1, 10, "lost", carro="2000"), status_efetivo="lost", saida_sim=None)]
    agora = em(120)
    por_carro = {c["carro"]: c for c in rastro.candidatos_da_linha(viagens, agora)}
    assert set(por_carro) == {"2735", "2611"}  # a 3 é perdida e velha (fora dos 90 min)
    assert por_carro["2735"]["desde"] == em(16) and por_carro["2735"]["ate"] == em(36)
    assert por_carro["2611"]["desde"] == em(100) and por_carro["2611"]["ate"] == agora


def test_rastro_confirma_a_saida_e_o_carro_sai_da_lista(cliente, eng):
    with Session(eng) as db:
        _nimer(db, "032101", 1, 30, "done", "2616", real_min=31)
        agora = em(120)
        (c,) = rastro.carros_em_duvida(db, agora)
        assert c["prefixo_5dig"] == "22616" and c["carro"] == "2616" and c["motivo"] == "nimer_sem_saida"
        assert _calcular(db, agora)["viagens"][0]["saida_sim"] is None

    r = cliente.post("/monitoramento/rastro", headers={"X-Monitor-Token": TOKEN}, json={
        "prefixo": "22616", "desde": c["desde_ms"], "ate": c["ate_ms"],
        "pontos": _pontos(viagem_sim("2616", 1, 31))})
    assert r.status_code == 200, r.text
    assert r.json()["gravados"] > 0

    with Session(eng) as db:
        fontes = set(db.execute(select(LeituraVeiculo.fonte)).scalars())
        assert fontes == {"rastro"}
        v = _calcular(db, agora)["viagens"][0]
        assert v["saida_sim"] is not None and v["carro_real"] == "2616"
        assert rastro.carros_em_duvida(db, agora) == []


def test_rastro_repetido_nao_duplica_e_ponto_de_outra_linha_grava_com_a_linha_dele(cliente, eng):
    h = {"X-Monitor-Token": TOKEN}
    base = int(em(0).timestamp() * 1000)
    pontos = [{"d": base, "la": -23.45, "lo": -46.57, "s": 1, "li": 1403, "l": "1726-10"},
              {"d": base + 15000, "la": -23.45, "lo": -46.58, "s": 1, "li": 777, "l": "2011-10"}]
    r = cliente.post("/monitoramento/rastro", headers=h, json={"prefixo": "22299", "pontos": pontos})
    assert r.status_code == 200
    r = cliente.post("/monitoramento/rastro", headers=h, json={"prefixo": "22299", "pontos": pontos})
    assert r.json()["gravados"] == 0
    with Session(eng) as db:
        linhas = sorted(db.execute(select(LeituraVeiculo.linha_codigo)).scalars())
    assert linhas == ["1726-10", "2011-10"]


def test_rastro_sem_token_401(cliente):
    assert cliente.post("/monitoramento/rastro", json={"prefixo": "22616", "pontos": []}).status_code == 401


def test_config_do_robo_tem_rastro_e_carros_em_duvida(cliente):
    r = cliente.get("/monitoramento/robo/config", headers={"X-Monitor-Token": TOKEN})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["intervalos"]["rastro_seg"] == 300
    assert corpo["carros_em_duvida"] == []


def test_janela_ja_puxada_nao_e_pedida_de_novo_e_a_recente_volta_em_5_min(eng):
    from app.models.monitoramento import RastroColeta
    with Session(eng) as db:
        _nimer(db, "032102", 1, 100, "lost", "2611")
        agora = em(110)
        (c,) = rastro.carros_em_duvida(db, agora)
        db.add(RastroColeta(prefixo="22611", desde=em(90), ate=agora, status="ok", pontos=0, gravados=0))
        db.commit()
        assert rastro.carros_em_duvida(db, agora + timedelta(minutes=2)) == []
        assert len(rastro.carros_em_duvida(db, agora + timedelta(minutes=6))) == 1
