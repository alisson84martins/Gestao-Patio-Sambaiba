"""v4.0 fase 3, bloco C — número real (decisão T3, 12/11/2026).

O comportamento ERRADO que barramos (casos de 11/11/2026, conferidos à mão
com o Rastro do SIM):
- 2735 com NIMER `done` às 15:44 e o GPS indo para a G3 contava como
  realizada (o NIMER contou viagem que não existiu);
- 2611 às 23:05 saiu (GPS) e não contava porque o NIMER não registrou;
- carro sem nenhuma leitura no SIM virava "o SIM não viu" — puniria a
  operação por falha do robô;
- a correção do fiscal tem que valer acima das duas fontes.
⛔ Fixture sem nome e sem RE reais (valores fictícios).
"""
from app.services import fiscalizacao_tempo_real as tr
from tests.cenario_tempo_real import LINHA, calcular, em, na_garagem, parado, viagem, viagem_sim


def _indo_para_a_g3(prefixo, de_min, sentido=2):
    """Carro no meio do itinerário (longe do terminal) e depois na G3 — nunca sai do terminal."""
    pts = LINHA.tracados[sentido]
    meio = pts[len(pts) // 2]
    return parado(prefixo, sentido, meio, de_min, de_min + 8) + na_garagem(prefixo, de_min + 12, de_min + 40, sentido)


def _fundo():
    """Outro carro rodando normal na linha: o SIM está ativo."""
    return viagem_sim("2001", 1, 200)


def _por_tabela(res):
    return {v["tabela"]: v for v in res["viagens"]}


def test_2735_nimer_done_e_gps_indo_para_a_g3_e_nimer_sem_gps_e_nao_conta():
    v = viagem("t2735", 2, 404, "done", carro="2735", real_min=404)
    res = calcular([v], _indo_para_a_g3("2735", 396) + _fundo(), agora_min=600)
    (x,) = res["viagens"]
    assert x["situacao"] == "nimer_sem_gps"
    assert x["conta_realizada"] is False
    assert x["a_conferir"] == "NIMER contou e o GPS não viu"
    real = res["real"]["total"]
    assert real["realizadas"] == 0 and real["perdidas"] == 1 and real["icv"] == 0.0
    # o ICV do NIMER (conferência) continua dizendo 100%: é exatamente o erro que o número real corrige
    assert res["icv"]["nimer_ate_agora"] == 100.0


def test_2611_sem_nimer_e_com_saida_no_gps_e_confirmada_gps_e_conta():
    v = viagem("t2611", 1, 545, "lost", carro="2611")
    res = calcular([v], viagem_sim("2611", 1, 546), agora_min=600)
    (x,) = res["viagens"]
    assert x["situacao"] == "confirmada_gps"
    assert x["conta_realizada"] is True and x["carro_real"] == "2611"
    assert x["a_conferir"] == "rodou e o NIMER não contou"
    assert res["real"]["total"]["realizadas"] == 1 and res["real"]["total"]["a_conferir"] == 1


def test_carro_sem_nenhuma_leitura_e_sem_cobertura_e_conta_como_o_nimer():
    v = viagem("t2999", 1, 300, "done", carro="2999", real_min=301)
    res = calcular([v], _fundo(), agora_min=600)
    (x,) = res["viagens"]
    assert x["situacao"] == "sem_cobertura"
    assert x["conta_realizada"] is True and x["sem_gps_para_conferir"] is True
    assert x["a_conferir"] is None


def test_sem_sim_no_ar_tudo_que_o_nimer_contou_e_sem_cobertura():
    v = viagem("t1", 1, 300, "done", carro="2412", real_min=300)
    res = calcular([v], [], agora_min=600, tem_sim=False)
    assert res["viagens"][0]["situacao"] == "sem_cobertura"


def test_correcao_do_fiscal_prevalece_sobre_as_duas_fontes():
    a = viagem("t2735", 2, 404, "done", carro="2735", real_min=404)
    b = viagem("t2611", 1, 545, "lost", carro="2611")
    correcoes = {a["id"]: {"status": {"valor": "done"}}, b["id"]: {"status": {"valor": "lost"}}}
    leituras = _indo_para_a_g3("2735", 396) + viagem_sim("2611", 1, 546)
    res = calcular([a, b], leituras, agora_min=600, correcoes=correcoes)
    x = _por_tabela(res)
    assert x["t2735"]["situacao"] == "corrigida" and x["t2735"]["conta_realizada"] is True
    assert x["t2611"]["situacao"] == "corrigida" and x["t2611"]["conta_realizada"] is False
    assert res["real"]["total"]["realizadas"] == 1 and res["real"]["total"]["perdidas"] == 1


def test_confirmada_e_ipp_usa_a_hora_de_saida_do_sim():
    # NIMER diz que saiu na hora (09:30); o GPS mostra a saída às 09:36 → atrasada
    v = viagem("t1", 1, 30, "done", carro="2412", real_min=30)
    res = calcular([v], viagem_sim("2412", 1, 36), agora_min=600)
    (x,) = res["viagens"]
    assert x["situacao"] == "confirmada"
    assert x["pontualidade_real"]["classe"] == "atrasada"
    assert res["real"]["total"]["ipp"] == 0.0
    assert x["pontualidade"]["classe"] == "pontual"  # pelo NIMER (conferência) seria pontual


def test_troca_de_carro_carro_real_e_o_do_sim():
    v = viagem("032101", 1, 60, "done", carro="2722", real_min=60)
    res = calcular([v], viagem_sim("2797", 1, 61), agora_min=600)
    (x,) = res["viagens"]
    assert x["situacao"] == "confirmada" and x["carro_real"] == "2797" and x["carro"] == "2722"


def test_recente_ainda_dentro_dos_15_min_fica_aguardando_sem_contar_nem_perder():
    v = viagem("t1", 1, 590, "lost", carro="2412")
    res = calcular([v], _fundo(), agora_min=600)
    assert res["viagens"][0]["situacao"] == "aguardando"
    assert res["real"]["total"]["perdidas"] == 0 and res["real"]["total"]["realizadas"] == 0


def test_numero_real_por_sentido():
    viagens = [viagem("a", 1, 30, "done", carro="2412", real_min=30),
               viagem("b", 2, 100, "lost", carro="2500")]
    res = calcular(viagens, viagem_sim("2412", 1, 30) + _fundo(), agora_min=600)
    s1, s2 = res["real"]["por_sentido"]["1"], res["real"]["por_sentido"]["2"]
    assert (s1["realizadas"], s1["programadas_ate_agora"], s1["icv"]) == (1, 1, 100.0)
    assert (s2["realizadas"], s2["perdidas"], s2["icv"]) == (0, 1, 0.0)


def test_faltam_para_meta():
    bloco = {"programadas_dia": 200, "realizadas": 150}
    assert tr.faltam_para_meta(bloco, 98) == 46
    assert tr.faltam_para_meta(bloco, None) is None


def test_agregado_real_e_ponderado():
    r1 = calcular([viagem("a", 1, 30, "done", carro="2999", real_min=30)], [], agora_min=600)
    r2 = calcular([viagem("b", 1, 30, "lost", carro="2998"), viagem("c", 1, 40, "lost", carro="2997")], [],
                  agora_min=600)
    ag = tr.agregar([r1, r2])
    assert ag["programadas_reais"] == 3 and ag["icv_real_ate_agora"] == 33.33
