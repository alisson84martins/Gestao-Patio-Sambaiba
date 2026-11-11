"""Cerca virtual (app/services/monitoramento_regras.py — porte do regras.js v0.3).

Sem Postgres: só geometria sobre o traçado real da 1726-10. Os testes provam
o comportamento ERRADO que a v0.3 corrigiu (alertas falsos medidos em
10/11/2026), não só o caminho feliz — cada caso "não alerta" tem um caso
irmão mostrando que a regra continua viva quando deveria alertar.
"""
import math

import pytest
from sqlalchemy.orm import Session

from app.services import monitoramento_regras as R
from app.services.monitoramento_linhas import GARAGEM_G3, obter_linha, prefixo_patio
from tests.ambiente_monitoramento import criar_engine

T0 = 1794300000000  # epoch ms
MIN = 60000
TP_REAL = (-23.438087, -46.566383)
TS_REAL = (-23.502205, -46.62377)
TP_OFICIAL_SIM = (-23.434086, -46.565939)


@pytest.fixture(scope="module")
def linha():
    return obter_linha("1726-10")


def _ponto_em(linha, sentido, m):
    """Ponto do traçado a `m` metros do início do sentido."""
    pts, acum = linha.tracados[sentido], R.acumulado(linha, sentido)
    m = max(0.0, min(m, acum[-1]))
    for i in range(1, len(pts)):
        if acum[i] >= m:
            f = (m - acum[i - 1]) / ((acum[i] - acum[i - 1]) or 1)
            return (pts[i - 1][0] + f * (pts[i][0] - pts[i - 1][0]),
                    pts[i - 1][1] + f * (pts[i][1] - pts[i - 1][1]))
    return pts[-1]


class _Carro:
    """Monta o histórico de um carro, uma leitura por minuto por padrão."""

    def __init__(self):
        self.t = T0
        self.leituras = []

    def em(self, ponto, s, passo=MIN, loc=None):
        self.leituras.append({"t": self.t, "la": ponto[0], "lo": ponto[1], "s": s, "loc": loc})
        self.t += passo
        return self

    def percorrer(self, linha, s, de_m, ate_m, cada_m=400):
        n = max(1, int(abs(ate_m - de_m) // cada_m))
        for k in range(n + 1):
            self.em(_ponto_em(linha, s, de_m + (ate_m - de_m) * k / n), s)
        return self

    def esperar(self, minutos):
        self.t += minutos * MIN
        return self


def _analisar(linha, carro, prefixo="22412"):
    # agora = última leitura: não queremos "sem transmissão" nos testes de trajeto
    return R.analisar_carro(prefixo, [dict(r) for r in carro.leituras], linha, carro.leituras[-1]["t"])


def _tipos(res, tipo):
    return [e for e in res["eventos"] if e["tipo"] == tipo]


def _deslocar(ponto, norte_m=0.0, leste_m=0.0):
    return (ponto[0] + norte_m / 110540,
            ponto[1] + leste_m / (111320 * math.cos(ponto[0] * math.pi / 180)))


# ─── Cadastro ────────────────────────────────────────────────────────────────

def test_cadastro_1726_bate_com_o_catalogo_e_com_o_sim(linha):
    assert linha.codigo == "1726-10"
    assert obter_linha("1726/10") is linha and obter_linha("1726") is linha
    with Session(criar_engine()) as db:  # códigos do SIM vêm do banco (054)
        do_banco = obter_linha("1726", db)
    assert do_banco.sentido_do_sim(1403) == 1 and do_banco.sentido_do_sim(34171) == 2
    assert do_banco.sentido_do_sim(999) is None and do_banco.nimer_line_id == 33534
    assert do_banco.tracados == linha.tracados
    assert linha.destino == {1: "TS", 2: "TP"}
    assert prefixo_patio("22412") == "2412" and prefixo_patio(22412) == "2412"


# ─── 1. Parado no terminal de ORIGEM com o SIM "piscando" o sentido ──────────

def test_1_parado_na_origem_com_sentido_trocando_duas_vezes_nao_vira(linha):
    c = _Carro()
    for s in (1, 1, 1, 2, 2, 1, 1, 1, 1):
        c.em(_deslocar(TP_REAL, norte_m=10), s)
    res = _analisar(linha, c)
    assert res["zona"] == "TP"
    assert _tipos(res, "virada") == []
    assert _tipos(res, "desvio") == []


# ─── 2. Saindo da G3 a ~5 km do itinerário ───────────────────────────────────

def _saida_da_g3(linha, c, n=15):
    c.em(GARAGEM_G3, 1)
    for k in range(1, n + 1):
        f = k / n
        c.em((GARAGEM_G3[0] + f * (TP_REAL[0] - GARAGEM_G3[0]),
              GARAGEM_G3[1] + f * (TP_REAL[1] - GARAGEM_G3[1])), 1)
    return c


def test_2_saida_da_g3_nao_e_desvio_ate_tocar_o_terminal(linha):
    c = _saida_da_g3(linha, _Carro())
    res = _analisar(linha, c)
    # o teste só vale se o caminho G3 → TP passa mesmo longe da rota
    assert sum(1 for r in c.leituras if R.classificar(dict(r), linha)["_z"] == "fora") >= R.LEITURAS_DESVIO
    assert res["zona"] == "TP"
    assert _tipos(res, "desvio") == [] and _tipos(res, "recolhida") == []


def test_2b_depois_de_tocar_o_terminal_sair_da_rota_volta_a_ser_desvio(linha):
    c = _saida_da_g3(linha, _Carro())
    c.percorrer(linha, 1, 0, 3000)
    base = _ponto_em(linha, 1, 3000)
    for _ in range(4):
        c.em(_deslocar(base, leste_m=600), 1)
    assert len(_tipos(_analisar(linha, c), "desvio")) == 1


# ─── 3. Buraco de leitura na passagem pelo TS ────────────────────────────────

def _ida_e_volta_sem_tocar_ts(linha, c, minutos_buraco):
    c.percorrer(linha, 1, 0, R.comprimento(linha, 1) - 600)
    c.esperar(minutos_buraco)
    c.percorrer(linha, 2, 600, 3000)
    return c


def test_3_buraco_de_20_min_na_passagem_pelo_ts_nao_vira(linha):
    """O buraco cai justo na troca de sentido — é o caso que o regras.js
    v0.3 ainda deixava passar (ver o comentário em analisar_carro)."""
    c = _ida_e_volta_sem_tocar_ts(linha, _Carro(), 20)
    assert not any(R.classificar(dict(r), linha)["_z"] == "TS" for r in c.leituras)
    assert _tipos(_analisar(linha, c), "virada") == []


def test_3c_buraco_no_meio_da_ida_tambem_trava(linha):
    c = _Carro().percorrer(linha, 1, 0, 6000).esperar(20).percorrer(linha, 1, 6000, R.comprimento(linha, 1) - 600)
    c.percorrer(linha, 2, 600, 3000)
    assert _tipos(_analisar(linha, c), "virada") == []


def test_3b_mesma_viagem_sem_buraco_vira(linha):
    # Controle do caso 3: sem o buraco, não ver o TS é virada de verdade.
    c = _ida_e_volta_sem_tocar_ts(linha, _Carro(), 0)
    assert len(_tipos(_analisar(linha, c), "virada")) == 1


# ─── 4. Virada de verdade ────────────────────────────────────────────────────

def test_4_anda_6_km_e_troca_o_sentido_a_4_km_do_destino_vira_uma_vez(linha):
    comp = R.comprimento(linha, 1)
    ponto_virada = comp - 4000
    assert ponto_virada >= 6000
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, ponto_virada)
    # o SIM troca o sentido ali mesmo e o carro volta pelo sentido 2
    volta_ini = R.comprimento(linha, 2) - ponto_virada
    c.percorrer(linha, 2, volta_ini, volta_ini + 3000)
    viradas = _tipos(_analisar(linha, c), "virada")
    assert len(viradas) == 1
    assert viradas[0]["terminal"] == "TS"
    assert "TS Terminal Santana" in R.texto_evento(viradas[0], linha)


def test_4b_viagem_que_toca_o_ts_nao_vira(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, R.comprimento(linha, 1))
    c.em(TS_REAL, 1).em(TS_REAL, 2)
    c.percorrer(linha, 2, 0, 3000)
    assert _tipos(_analisar(linha, c), "virada") == []


# ─── 5. Desvio e recolhida ───────────────────────────────────────────────────

def _desvio_de_500m(linha, c, n=3):
    # No km 7 do sentido 1, 500 m a leste fica longe dos DOIS traçados.
    c.em(TP_REAL, 1).percorrer(linha, 1, 0, 7000)
    base = _ponto_em(linha, 1, 7000)
    for k in range(n):
        p = _deslocar(_ponto_em(linha, 1, 7000 + 150 * k), leste_m=500)
        assert R.classificar({"t": 0, "la": p[0], "lo": p[1], "s": 1}, linha)["_z"] == "fora"
        c.em(p, 1, loc="Rua do Desvio")
    return c, base


def test_5_desvio_de_500m_por_3_leituras_e_desvio(linha):
    c, _ = _desvio_de_500m(linha, _Carro())
    c.percorrer(linha, 1, 7600, 9000)
    (d,) = _tipos(_analisar(linha, c), "desvio")
    assert d["ativo"] is False  # voltou para a rota
    assert d["maxD"] > 300
    assert "Fora do itinerário" in R.texto_evento(d, linha)


def test_5b_duas_leituras_fora_nao_e_desvio(linha):
    c, _ = _desvio_de_500m(linha, _Carro(), n=2)
    c.percorrer(linha, 1, 7600, 9000)
    assert _tipos(_analisar(linha, c), "desvio") == []


def test_5c_desvio_que_termina_na_g3_vira_recolhida(linha):
    c, _ = _desvio_de_500m(linha, _Carro())
    ultimo = (c.leituras[-1]["la"], c.leituras[-1]["lo"])
    for k in range(1, 9):
        f = k / 8
        c.em((ultimo[0] + f * (GARAGEM_G3[0] - ultimo[0]), ultimo[1] + f * (GARAGEM_G3[1] - ultimo[1])), 1)
    res = _analisar(linha, c)
    assert _tipos(res, "desvio") == []
    (rec,) = _tipos(res, "recolhida")
    assert rec["ativo"] is False
    assert "recolhida" in R.texto_evento(rec, linha)
    assert res["zona"] == "garagem"


# ─── 6. Centro real do TP x ponto oficial do SIM ─────────────────────────────

def test_6_ponto_perto_do_centro_real_e_longe_do_ponto_do_sim_e_tp(linha):
    """O prompt pedia "300 m do ponto oficial e 50 m do centro real", mas os
    dois ficam a 445 m um do outro — a 50 m do centro real o ponto está a
    pelo menos 395 m do oficial. O que importa (e é testado): fora do raio
    de 250 m do ponto do SIM, dentro do raio do centro real → TP."""
    p = _deslocar(TP_REAL, norte_m=-50)  # 50 m para o sul, longe do ponto do SIM
    assert R.distancia(p, TP_REAL) == pytest.approx(50, abs=1)
    assert R.distancia(p, TP_OFICIAL_SIM) > R.RAIO_TERMINAL
    assert R.classificar({"t": 0, "la": p[0], "lo": p[1], "s": 1}, linha)["_z"] == "TP"


def test_6b_o_ponto_oficial_do_sim_nao_e_centro_de_terminal(linha):
    assert TP_OFICIAL_SIM not in linha.terminais["TP"]
    assert R.distancia(TP_OFICIAL_SIM, TP_REAL) == pytest.approx(445, abs=5)


# ─── Demais regras ───────────────────────────────────────────────────────────

def test_parado_fora_de_terminal_8_min(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 4000)
    p = _ponto_em(linha, 1, 4000)
    for _ in range(9):
        c.em(p, 1)
    (e,) = _tipos(_analisar(linha, c), "parado")
    assert e["ativo"] is True and e["min"] >= R.PARADO_MIN


def test_parado_no_terminal_nao_alerta(linha):
    c = _Carro()
    for _ in range(90):  # 1 h 30 no TP: desligado de propósito (v0.3)
        c.em(TP_REAL, 1)
    res = _analisar(linha, c)
    assert res["eventos"] == []


def test_sem_transmissao_5_min_fora_da_garagem(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 2000)
    ult = c.leituras[-1]["t"]
    res = R.analisar_carro("22412", c.leituras, linha, ult + 6 * MIN)
    (e,) = _tipos(res, "semtx")
    assert e["min"] == 6
    na_g3 = _Carro().em(GARAGEM_G3, 1)
    assert _tipos(R.analisar_carro("22413", na_g3.leituras, linha, T0 + 60 * MIN), "semtx") == []


def test_retorno_no_itinerario(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 6000)
    c.percorrer(linha, 1, 5000, 4000)
    (e,) = _tipos(_analisar(linha, c), "retorno")
    assert e["recuo"] > R.RETORNO_M and e["terminal"] == "TS"


def test_sem_leituras_devolve_none(linha):
    assert R.analisar_carro("22412", [], linha, T0) is None


def test_classificacao_cacheada_por_prefixo_e_hora(linha, monkeypatch):
    R.limpar_cache()
    chamadas = []
    original = R._calcular_classe

    def _contar(*a, **k):
        chamadas.append(1)
        return original(*a, **k)

    monkeypatch.setattr(R, "_calcular_classe", _contar)
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 2000)
    R.analisar_carro("22412", [dict(r) for r in c.leituras], linha, T0)
    n = len(chamadas)
    assert n == len(c.leituras)
    R.analisar_carro("22412", [dict(r) for r in c.leituras], linha, T0)
    assert len(chamadas) == n  # 2ª análise (próximo polling) não recalcula
