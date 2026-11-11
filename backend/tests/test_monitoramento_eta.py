"""Bloco F — previsão de chegada (ETA).

Replay de dois dias sintéticos na 1726-10 (sentido 1, TP → TS): o dia 1
alimenta o histórico (velocidade_trecho), o dia 2 é previsto minuto a minuto
e comparado com a chegada real — a hora em que o SIM mostra o carro dentro
do raio do TS. O erro médio fica registrado no prompt da fase 2.
"""
import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.monitoramento import LeituraVeiculo, VelocidadeAgregacao, VelocidadeTrecho
from app.services import monitoramento_eta as eta
from app.services import monitoramento_regras as regras
from app.services.monitoramento_linhas import linha_do_json, obter_linha
from tests.ambiente_monitoramento import criar_engine

LINHA = obter_linha("1726-10")
S = 1
PTS = LINHA.tracados[S]
ACUM = regras.acumulado(LINHA, S)
TOTAL = ACUM[-1]
TS = LINHA.terminais["TS"]


def _ponto_em(prog: float):
    import bisect
    i = max(1, min(bisect.bisect_left(ACUM, prog), len(ACUM) - 1))
    a, b = PTS[i - 1], PTS[i]
    t = 0.0 if ACUM[i] == ACUM[i - 1] else (prog - ACUM[i - 1]) / (ACUM[i] - ACUM[i - 1])
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _vel_real(prog: float, instante: datetime) -> float:
    """Trânsito sintético: 22 km/h; trechos 8–14 lentos (9 km/h) das 17h às 19h de SP."""
    hora = instante.astimezone(FUSO_OPERACAO).hour
    trecho = int(prog // eta.TRECHO_M)
    if 17 <= hora < 19 and 8 <= trecho <= 14:
        return 9.0
    if 7 <= hora < 9:
        return 16.0
    return 22.0


def _viagem(prefixo: str, saida: datetime, rnd: random.Random, fator: float) -> list[dict]:
    """Leituras 1/min de uma viagem TP → TS até entrar no raio do TS."""
    leituras, prog, t = [], 0.0, saida
    prox = saida
    while prog < TOTAL:
        if t >= prox:
            la, lo = _ponto_em(prog)
            leituras.append({"prefixo": prefixo, "em": t, "s": S, "la": la, "lo": lo})
            prox = t + timedelta(minutes=1)
        v = _vel_real(prog, t) * fator * rnd.uniform(0.85, 1.15)
        prog += v / 3.6 * 10
        t += timedelta(seconds=10)
    la, lo = PTS[-1]
    leituras.append({"prefixo": prefixo, "em": t, "s": S, "la": la, "lo": lo})
    return leituras


def _dia(d: date, rnd: random.Random, fator=1.0) -> list[list[dict]]:
    ini = datetime(d.year, d.month, d.day, 6, 0, tzinfo=FUSO_OPERACAO)
    return [_viagem(f"2{2400 + k}", ini + timedelta(minutes=12 * k), rnd, fator) for k in range(80)]  # 06:00–21:48


def _chegada(leituras: list[dict]) -> datetime:
    for r in leituras:
        if min(regras.distancia((r["la"], r["lo"]), c) for c in TS) <= regras.RAIO_TERMINAL:
            return r["em"]
    return leituras[-1]["em"]


@pytest.fixture(scope="module")
def replay():
    rnd = random.Random(20261110)
    eng = criar_engine()
    dia1, dia2 = date(2026, 11, 9), date(2026, 11, 10)   # segunda e terça (dia útil)
    with Session(eng) as db:
        for viagem in _dia(dia1, rnd):
            for r in viagem:
                # UTC: o SQLite dos testes grava a hora "de parede", sem o fuso
                db.add(LeituraVeiculo(posicao_em=r["em"].astimezone(timezone.utc), prefixo=r["prefixo"],
                                      linha_codigo=LINHA.codigo,
                                      sim_codigo=1403, sentido=S, lat=r["la"], lon=r["lo"]))
        db.commit()
        leituras_dia1 = eta.agregar_dia(db, LINHA, dia1)
        hist = eta.historico_da_linha(db, LINHA.codigo, S, "UTIL")
    viagens2 = _dia(dia2, rnd, fator=1.05)
    todas2 = sorted((r for v in viagens2 for r in v), key=lambda r: r["em"])
    erros, erros_so_hist, dentro_faixa, sem = [], [], 0, 0
    for v in viagens2[::3]:
        chegada = _chegada(v)
        for r in v[5::7]:  # várias previsões ao longo da viagem
            if r["em"] >= chegada - timedelta(minutes=2):
                break
            agora = r["em"]
            prog = regras.classificar(dict(t=0, **{k: r[k] for k in ("la", "lo", "s")}), LINHA)["_prog"]
            por_carro: dict[str, list] = {}
            for x in todas2:
                if agora - eta.JANELA_AO_VIVO <= x["em"] <= agora:
                    por_carro.setdefault(x["prefixo"], []).append(x)
            vivo = eta.velocidades_ao_vivo(por_carro, LINHA, S, agora)
            propria = eta.velocidade_propria(por_carro.get(r["prefixo"], []), LINHA, S, agora)
            p = eta.prever(linha=LINHA, sentido=S, progresso_m=prog, agora=agora, ao_vivo=vivo,
                           historico=hist, propria_kmh=propria)
            if p is None:
                sem += 1
                continue
            real_min = (chegada - agora).total_seconds() / 60
            erro = abs(p["minutos"] - real_min)
            erros.append(erro)
            dentro_faixa += erro <= p["mais_menos_min"] + 0.5
            q = eta.prever(linha=LINHA, sentido=S, progresso_m=prog, agora=agora, ao_vivo={}, historico=hist,
                           propria_kmh=None)
            if q:
                erros_so_hist.append(abs(q["minutos"] - real_min))
    return {"engine": eng, "leituras_dia1": leituras_dia1, "hist": hist, "erros": erros,
            "erros_so_hist": erros_so_hist, "dentro_faixa": dentro_faixa, "sem": sem}


def test_replay_erro_medio_da_previsao(replay):
    erros = replay["erros"]
    assert len(erros) > 100
    medio = sum(erros) / len(erros)
    p90 = sorted(erros)[int(len(erros) * 0.9)]
    print(f"\nETA replay: {len(erros)} previsões, erro médio {medio:.2f} min, p90 {p90:.1f} min, "
          f"dentro da faixa {replay['dentro_faixa'] / len(erros):.0%}, só histórico "
          f"{sum(replay['erros_so_hist']) / len(replay['erros_so_hist']):.2f} min, sem previsão {replay['sem']}")
    assert medio < 3.0
    assert replay["dentro_faixa"] / len(erros) >= 0.7


def test_historico_agregado_e_nao_soma_o_mesmo_dia_duas_vezes(replay):
    eng = replay["engine"]
    assert replay["leituras_dia1"] > 1000 and replay["hist"]
    # o trecho lento das 17h–19h ficou mais lento que o resto no histórico
    lento = [m for (t, f), (m, n) in replay["hist"].items() if 8 <= t <= 14 and 34 <= f <= 37]
    normal = [m for (t, f), (m, n) in replay["hist"].items() if t >= 20 and 24 <= f <= 30]
    assert lento and normal and max(lento) < min(normal)
    with Session(eng) as db:
        n_antes = db.execute(select(func.sum(VelocidadeTrecho.n))).scalar_one()
        assert eta.agregar_dia(db, LINHA, date(2026, 11, 9)) == 0
        assert db.execute(select(func.sum(VelocidadeTrecho.n))).scalar_one() == n_antes
        assert db.get(VelocidadeAgregacao, (date(2026, 11, 9), LINHA.codigo)) is not None


def test_sem_previsao_sem_mapa_fora_da_rota_ou_sem_dado():
    agora = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    sem_mapa = linha_do_json("8012-10", None)
    assert eta.prever(linha=sem_mapa, sentido=1, progresso_m=100, agora=agora, ao_vivo={}, historico={},
                      propria_kmh=None) is None
    assert eta.prever(linha=LINHA, sentido=1, progresso_m=None, agora=agora, ao_vivo={}, historico={},
                      propria_kmh=20) is None
    # só a velocidade própria (1,5 km) para 20 km de caminho: cobertura baixa → sem previsão
    assert eta.prever(linha=LINHA, sentido=1, progresso_m=0, agora=agora, ao_vivo={}, historico={},
                      propria_kmh=20) is None
    assert eta.texto_previsao(None) == "sem previsão"


def test_texto_da_previsao_com_faixa():
    agora = datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)  # 18:00 SP
    vivo = {t: [20.0, 20.0, 20.0] for t in range(int(TOTAL // eta.TRECHO_M) + 1)}
    p = eta.prever(linha=LINHA, sentido=1, progresso_m=TOTAL - 5000, agora=agora, ao_vivo=vivo, historico={},
                   propria_kmh=None)
    assert p["minutos"] == 15 and p["mais_menos_min"] == 2  # 5 km a 20 km/h; ±15% de 15 = 2
    assert eta.texto_previsao(p) == f"previsão: chega ~{p['chegada']} (±2 min)" and p["chegada"] == "18:15"


def test_carro_no_sentido_contrario_da_mesma_rua_nao_conta_como_transito():
    ida = [{"em": datetime(2026, 11, 10, 15, k, tzinfo=timezone.utc), "s": 2, "la": PTS[200 - 10 * k][0],
            "lo": PTS[200 - 10 * k][1]} for k in range(6)]
    assert eta.amostras(ida, LINHA, 1) == []
    volta = list(reversed([dict(r, em=r["em"]) for r in ida]))
    for k, r in enumerate(volta):
        r["em"] = datetime(2026, 11, 10, 15, k, tzinfo=timezone.utc)
    assert len(eta.amostras(volta, LINHA, 1)) == 5


def test_ao_vivo_traz_a_previsao_no_cartao_do_carro():
    from app.models.monitoramento import Coleta
    from app.services import monitoramento_ao_vivo as servico
    eng = criar_engine()
    agora = datetime.now(timezone.utc)
    with Session(eng) as db:
        for k in range(12):
            i = 100 + k * 12
            for p, desloc in (("22412", 0), ("22413", 60)):
                j = min(i + desloc, len(PTS) - 1)
                db.add(LeituraVeiculo(posicao_em=agora - timedelta(minutes=12 - k), recebido_em=agora, prefixo=p,
                                      linha_codigo=LINHA.codigo, sim_codigo=1403, sentido=1,
                                      lat=PTS[j][0], lon=PTS[j][1], velocidade=25))
        db.add(Coleta(recebido_em=agora, status="ok", qtd_veiculos=2))
        # histórico do caminho todo (sem ele, 2 carros não cobrem metade do caminho → sem previsão)
        tipo = eta.tipo_dia(agora.astimezone(FUSO_OPERACAO).date())
        for trecho in range(int(TOTAL // eta.TRECHO_M) + 1):
            for faixa in range(48):
                db.add(VelocidadeTrecho(linha_codigo=LINHA.codigo, sentido=1, trecho=trecho, faixa=faixa,
                                        tipo_dia=tipo, mediana_kmh=18.0, n=10))
        db.commit()
        j = servico.montar_ao_vivo(db, LINHA, agora)
    c = {x["prefixo"]: x for x in j["carros"]}["22412"]
    assert c["zona"] == "rota" and c["eta"] is not None
    assert c["eta_texto"].startswith("previsão: chega ~") and math.isfinite(c["eta"]["minutos"])
