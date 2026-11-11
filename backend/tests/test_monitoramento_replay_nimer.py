"""Bloco J — replay da resposta FICTÍCIA do NIMER (scripts/enviar_nimer_ficticio.py)
pelo caminho inteiro: POST /monitoramento/nimer → banco → cruzamento → alertas.

É o mesmo JSON do teste manual local; aqui roda sem rede e com "agora" fixo.
⛔ Sem dado pessoal real.
"""
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import get_db
from app.main import app
from app.routers import monitoramento as router_mod
from app.services import fiscalizacao_alertas as alertas_op
from app.services import fiscalizacao_tempo_real as tr
from app.services.monitoramento_linhas import obter_linha
from scripts.enviar_nimer_ficticio import payload
from tests.ambiente_monitoramento import criar_engine

TOKEN = "token-de-teste-do-robo"


def test_replay_do_nimer_ficticio_pelo_caminho_inteiro(monkeypatch):
    eng = criar_engine()

    def _db():
        with Session(eng) as db:
            yield db
    monkeypatch.setattr(router_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"monitor_ingest_token": TOKEN}))
    app.dependency_overrides[get_db] = _db
    agora = datetime(2026, 11, 10, 22, 30, tzinfo=FUSO_OPERACAO)
    corpo = payload(agora)
    try:
        r = TestClient(app).post("/monitoramento/nimer", json=corpo, headers={"X-Monitor-Token": TOKEN})
        r2 = TestClient(app).post("/monitoramento/nimer", json=corpo, headers={"X-Monitor-Token": TOKEN})  # reenvio
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200 and r2.status_code == 200
    total = sum(len(t["trips"]) for t in corpo["timeline"]["tables"])
    assert r.json()["viagens"] == total == 128

    with Session(eng) as db:
        linha = obter_linha("1726-10", db)
        res = tr.montar_linha_tempo_real(db, linha, agora.date(), agora)
        alertas = alertas_op.calcular_alertas(linha, res, leituras_por_carro={}, leituras_outras_linhas=[],
                                              alertas_ao_vivo=[], agora=agora)
    v = res["viagens"]
    assert len(v) == 128                                         # reenvio não duplicou
    assert res["totais"]["duplicadas_nimer"] == 1                # 2293 registrado 2× na 032207
    sem_mot = [x for x in v if x["tabela"] == "032116"]
    assert sem_mot and all(x["sem_motorista"] for x in sem_mot)
    assert all(x["status_efetivo"] == "lost" for x in sem_mot if x["prog_inicio"] <= agora)
    recolhe = next(x for x in v if x.get("motivo_nimer") == "4- Recolhe Anormal / SOS")
    assert recolhe["carro"] == "2293" and recolhe["recolhe"] == "SOS"   # voltou à tarde na 032207
    transito = next(x for x in v if x.get("motivo_nimer") == "3 - Trânsito")
    assert transito["contador"] is None and transito["motivo_canonico"] == "TRANSITO"
    assert res["icv"]["nimer_dia"] < 100
    tipos = {a["tipo"] for a in alertas}
    assert "sem_motorista" in tipos
