"""Bloco C — cadastro das linhas no banco, linha sem traçado, gerador de
traçado GTFS e calibração dos terminais.

Duas linhas ao mesmo tempo (decisão 3): a 1726-10 (com traçado) e uma
segunda linha de teste SEM traçado — incluir linha = inserir em
linha_monitorada, sem código novo.
"""
import csv
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.monitoramento import Coleta, LeituraVeiculo, LinhaMonitoradaCadastro
from app.routers import monitoramento as router_mod
from app.services import monitoramento_ao_vivo as servico
from app.services import monitoramento_regras as regras
from app.services.monitoramento_linhas import calibrar_centros, linha_do_json, obter_linha
from scripts import gerar_tracados_gtfs as gerador
from tests.ambiente_monitoramento import criar_engine, liberar_tudo

_CHECKER = router_mod.Leitura.__metadata__[0].dependency
SEGUNDA = "8012-10"  # linha de teste sem traçado


@pytest.fixture
def engine():
    eng = criar_engine()
    with Session(eng) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo=SEGUNDA, nimer_line_id=99, ativo=True))
        db.add(LinhaMonitoradaCadastro(linha_codigo="8013-10", nimer_line_id=None, ativo=False))
        db.commit()
    return eng


@pytest.fixture
def client(engine, monkeypatch):
    liberar_tudo(monkeypatch)
    def _db():
        with Session(engine) as db:
            yield db
    servico.limpar_cache()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[_CHECKER] = lambda: Funcionario(id=uuid4(), re="70001", nome="Fiscal Teste")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(_CHECKER, None)
        servico.limpar_cache()


def test_linhas_vem_do_banco_so_as_ativas(client):
    j = client.get("/monitoramento/linhas").json()
    assert [(x["codigo"], x["tem_mapa"], x["tem_nimer"]) for x in j] == [("1726-10", True, True), (SEGUNDA, False, True)]


def test_linha_desativada_404(client):
    r = client.get("/monitoramento/linhas/8013-10/ao-vivo")
    assert r.status_code == 404 and r.json()["erro"] == "A linha 8013-10 não é monitorada ao vivo"


def test_linha_sem_tracado_funciona_sem_mapa_e_nao_acusa_desvio(client, engine):
    agora = datetime.now(timezone.utc)
    with Session(engine) as db:
        # 6 leituras longe de tudo: numa linha com mapa seria desvio
        for k in range(6):
            quando = agora - timedelta(minutes=6 - k)
            db.add(LeituraVeiculo(posicao_em=quando, recebido_em=agora, prefixo="28001", linha_codigo=SEGUNDA,
                                  sim_codigo=4242, sentido=1, lat=-23.6 + k * 0.003, lon=-46.7, velocidade=20))
        db.add(Coleta(recebido_em=agora, status="ok", qtd_veiculos=1))
        db.commit()
    j = client.get(f"/monitoramento/linhas/{SEGUNDA}/ao-vivo").json()
    assert j["linha"]["tem_mapa"] is False
    (c,) = j["carros"]
    assert c["zona"] == "sem_mapa" and c["classe"] == "ok"
    assert j["alertas_agora"] == [] and j["ocorrencias_dia"] == []
    t = client.get(f"/monitoramento/linhas/{SEGUNDA}/tracado").json()
    assert t["tem_mapa"] is False and t["sentidos"] == [] and t["terminais"] == []


def test_linha_sem_tracado_ainda_avisa_sem_transmissao():
    linha = linha_do_json(SEGUNDA, None)
    agora = 10 * 60_000
    res = regras.analisar_carro("28001", [{"t": 0, "la": -23.6, "lo": -46.7, "s": 1}], linha, agora)
    assert [e["tipo"] for e in res["eventos"]] == ["semtx"]


def test_tracado_1726_continua_igual_ao_da_fase_1():
    linha = obter_linha("1726-10")
    assert linha.tem_mapa and linha.shapes == {1: "86298", 2: "86299"}
    assert linha.terminais["TP"][0] == (-23.438087, -46.566383)   # centro real, não o ponto do SIM
    assert linha.terminais["TS"][0] == (-23.502205, -46.62377)
    assert len(linha.terminais["TP"]) == 2 and len(linha.terminais["TS"]) == 3
    assert linha.tracados[1][0] == linha.terminais["TP"][1]


def test_centro_do_terminal_pode_ser_sobrescrito_no_json():
    base = {"sentidos": {"1": {"destino": "TS", "pontos": [[0, 0], [0, 0.1]]},
                         "2": {"destino": "TP", "pontos": [[0, 0.1], [0, 0.001]]}},
            "terminais": {"TP": {"nome": "TP X", "centros": [[0, 0]], "centros_calibrados": [[0.001, 0]]},
                          "TS": {"nome": "TS Y", "centros": [[0, 0.1]]}}}
    linha = linha_do_json("9001-10", base)
    assert linha.terminais["TP"] == [(0.001, 0.0), (0.0, 0.0)]
    assert linha.nomes_terminais == {"TP": "TP X", "TS": "TS Y"}


# ─── Calibração ─────────────────────────────────────────────────────────────

def _viagem(prefixo, t0, s, inicio, fim, n):
    return [{"prefixo": prefixo, "t": t0 + k * 60_000, "s": s,
             "la": inicio[0] + (fim[0] - inicio[0]) * k / max(n - 1, 1), "lo": inicio[1] + (fim[1] - inicio[1]) * k / max(n - 1, 1)}
            for k in range(n)]


def test_calibra_pela_mediana_das_trocas_e_ignora_o_pisca_pisca():
    tp, ts = (-23.40, -46.50), (-23.50, -46.60)
    leituras = []
    for i, carro in enumerate(("21", "22", "23")):
        desvio = (i - 1) * 0.0002
        t0 = i * 10_000_000
        ida = _viagem(carro, t0, 1, (tp[0], tp[1]), (ts[0] + desvio, ts[1]), 20)
        volta = _viagem(carro, ida[-1]["t"] + 60_000, 2, (ts[0] + desvio, ts[1]), (tp[0] + desvio, tp[1]), 20)
        leituras += ida + volta
    # pisca-pisca parado no TP: 1 leitura de sentido 2 no meio do sentido 1 — não conta
    leituras += _viagem("29", 0, 1, tp, tp, 12) + _viagem("29", 12 * 60_000, 2, tp, tp, 1) \
        + _viagem("29", 13 * 60_000, 1, tp, ts, 12)
    r = calibrar_centros(leituras, {1: "TS", 2: "TP"})
    assert r["TS"]["trocas"] == 3 and "TP" not in r
    # mediana das três trocas = a do carro do meio (sem desvio), no meio do
    # último ponto da ida e do primeiro da volta (os dois no TS)
    assert r["TS"]["centro"] == [round(ts[0], 6), round(ts[1], 6)]


# ─── Gerador do GTFS ────────────────────────────────────────────────────────

def _gtfs(pasta):
    def escrever(nome, cab, linhas):
        with open(pasta / nome, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(cab)
            w.writerows(linhas)
    escrever("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
             [["9001-10", "1", "9001-10", "Term. A - Term. B", "3"]])
    escrever("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"],
             [["9001-10", "USD", "9001-10-0", "Term. B", "0", "500"], ["9001-10", "USD", "9001-10-1", "Term. A", "1", "501"]])
    escrever("shapes.txt", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"],
             [["500", "-23.0", "-46.0", "2", "0"], ["500", "-23.1", "-46.0", "1", "0"], ["500", "-23.2", "-46.0", "3", "0"],
              ["501", "-23.2", "-46.01", "1", "0"], ["501", "-23.0", "-46.01", "2", "0"], ["999", "0", "0", "1", "0"]])


def test_gerador_cria_json_e_preserva_o_que_foi_ajustado(tmp_path):
    _gtfs(tmp_path)
    saida = tmp_path / "tracados"
    assert gerador.main(["9001", "--gtfs", str(tmp_path), "--saida", str(saida)]) == 0
    dados = json.loads((saida / "9001-10.json").read_text(encoding="utf-8"))
    assert dados["sentidos"]["1"]["pontos"] == [[-23.1, -46.0], [-23.0, -46.0], [-23.2, -46.0]]  # pela sequência
    assert dados["sentidos"]["1"]["destino"] == "TS" and dados["sentidos"]["2"]["destino"] == "TP"
    assert dados["terminais"]["TP"]["centros"] == [[-23.1, -46.0]]
    assert dados["terminais"]["TS"]["centros"] == [[-23.2, -46.0], [-23.2, -46.01]]  # sem a ponta final do sentido 2
    # ajuste à mão sobrevive a uma nova geração
    dados["terminais"]["TP"]["centros_calibrados"] = [[-23.15, -46.0]]
    dados["nome"] = "9001-10 nome da coordenação"
    (saida / "9001-10.json").write_text(json.dumps(dados), encoding="utf-8")
    gerador.main(["9001-10", "--gtfs", str(tmp_path), "--saida", str(saida)])
    dados2 = json.loads((saida / "9001-10.json").read_text(encoding="utf-8"))
    assert dados2["terminais"]["TP"]["centros_calibrados"] == [[-23.15, -46.0]]
    assert dados2["nome"] == "9001-10 nome da coordenação"
    linha = linha_do_json("9001-10", dados2)
    assert linha.tem_mapa and linha.terminais["TP"][0] == (-23.15, -46.0)


def test_gerador_avisa_linha_fora_do_gtfs(tmp_path, capsys):
    _gtfs(tmp_path)
    assert gerador.main(["9002", "--gtfs", str(tmp_path), "--saida", str(tmp_path / "t")]) == 0
    assert "9002-10" in capsys.readouterr().out
