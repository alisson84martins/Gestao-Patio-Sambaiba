"""Bloco D — cruzamento NIMER × SIM, ICV/IPP ao vivo e R.A × S.O.S.

Os testes que valem provam o comportamento ERRADO que evitamos: duplicado
do NIMER contando 2×, saída "vista" com o carro só piscando o sentido no
terminal, ICV travado em 100%, R.A virando S.O.S sem o carro voltar.
"""
from datetime import timedelta

from app.services import fiscalizacao_tempo_real as tr
from tests.cenario_tempo_real import (
    LINHA,
    calcular,
    em,
    na_garagem,
    parado,
    viagem,
    viagem_sim,
)


def _por_tabela(res):
    return {(v["tabela"], v["prog_inicio"]): v for v in res["viagens"]}


# ─── Saídas vistas pelo SIM ─────────────────────────────────────────────────

def test_saida_do_terminal_tem_a_hora_em_que_o_carro_saiu_do_raio():
    leituras = viagem_sim("2412", 1, saida_min=30)
    (s,) = tr.saidas_do_sim(leituras, LINHA.terminais, LINHA.destino)
    assert s.prefixo == "2412" and s.sentido == 1
    assert em(30) <= s.em <= em(32)  # saiu do raio no 1º minuto rodando


def test_sentido_piscando_parado_no_terminal_nao_e_saida():
    tp = LINHA.terminais["TP"][0]
    leituras = parado("2412", 1, tp, 0, 5) + parado("2412", 2, tp, 6, 7) + parado("2412", 1, tp, 8, 20)
    assert tr.saidas_do_sim(leituras, LINHA.terminais, LINHA.destino) == []


def test_linha_sem_terminal_nao_inventa_saida():
    assert tr.saidas_do_sim(viagem_sim("2412", 1, 30), {}, LINHA.destino) == []


# ─── Casamento ──────────────────────────────────────────────────────────────

def test_casa_mesma_vaga_e_depois_qualquer_carro_ate_15_min():
    viagens = [viagem("032104", 1, 30, "done", carro="2412", real_min=31),
               viagem("032105", 1, 60, "done", carro="2500", real_min=62)]
    # 2412 sai às 31; a 2500 não rodou: quem saiu às 66 foi o 2600 (carro trocado)
    leituras = viagem_sim("2412", 1, 31) + viagem_sim("2600", 1, 66)
    res = calcular(viagens, leituras, agora_min=200)
    v1, v2 = res["viagens"]
    assert v1["sim_viu"] == "sim" and v1["carro_real"] == "2412" and not v1["carro_trocado_sim"]
    assert v2["sim_viu"] == "sim" and v2["carro_real"] == "2600" and v2["carro_trocado_sim"] is True


def test_saida_a_mais_de_15_min_de_outro_carro_nao_casa():
    viagens = [viagem("032105", 1, 60, "done", carro="2500", real_min=60)]
    res = calcular(viagens, viagem_sim("2600", 1, 80), agora_min=200)
    assert res["viagens"][0]["sim_viu"] == "nao"


def test_sem_sim_fica_sem_dado_e_nao_vira_nao_viu():
    res = calcular([viagem("032104", 1, 30, "done", real_min=31)], [], agora_min=200, tem_sim=False)
    assert res["viagens"][0]["sim_viu"] == "sem_dado"
    assert res["icv"]["sim_ate_agora"] is None


# ─── ICV / IPP ──────────────────────────────────────────────────────────────

def test_duplicado_do_nimer_nao_conta_duas_vezes():
    viagens = [viagem("032104", 1, 30, "done", carro="2412", real_min=31),
               viagem("032104", 1, 35, "done", carro="2412", real_min=36),   # mesma saída, 5 min depois
               viagem("032105", 1, 60, "lost", carro="2500")]
    res = calcular(viagens, viagem_sim("2412", 1, 31), agora_min=200)
    assert res["totais"]["duplicadas_nimer"] == 1
    assert res["totais"]["realizadas_nimer_ate_agora"] == 1
    assert res["icv"]["nimer_ate_agora"] == round(1 / 3 * 100, 2)
    assert sum(v["sim_viu"] == "sim" for v in res["viagens"]) == 1


def test_icv_com_viagem_extra_passa_de_100():
    viagens = [viagem("032104", 1, 30, "done", carro="2412", real_min=31),
               viagem("EXTRA", 1, None, "done", carro="2600", real_min=70)]   # extra: sem horário programado
    leituras = viagem_sim("2412", 1, 31) + viagem_sim("2600", 1, 70)
    res = calcular(viagens, leituras, agora_min=200)
    assert res["icv"]["nimer_ate_agora"] == 200.0          # D30: sem teto
    assert res["icv"]["sim_ate_agora"] == 200.0
    assert res["ipp"]["ate_agora"] == 100.0                 # IPP = pontualidade: a extra não infla (11/11)
    assert res["totais"]["perda_absoluta"] == 0


def test_icv_ate_agora_e_do_dia_e_perda_absoluta():
    viagens = [viagem("A", 1, 10, "done", real_min=10), viagem("B", 1, 20, "lost"),
               viagem("C", 1, 30, "missing"), viagem("D", 1, 900, "future")]
    res = calcular(viagens, [], agora_min=100)
    assert res["totais"]["programadas_ate_agora"] == 3 and res["totais"]["programadas_dia"] == 4
    assert res["icv"]["nimer_ate_agora"] == 33.33 and res["icv"]["nimer_dia"] == 25.0
    assert res["totais"]["perda_absoluta"] == 2
    # se continuar assim (1 em 3), fecha o dia em (1 + 1 × 1/3) ÷ 4
    assert res["icv"]["projecao_fechamento"] == round((1 + 1 / 3) / 4 * 100, 2)


def test_correcao_do_fiscal_muda_o_status_efetivo_sem_mexer_no_nimer():
    v = viagem("032104", 1, 30, "lost", carro="2412")
    res = calcular([v], [], agora_min=200, correcoes={v["id"]: {"status": {"valor": "done"}}})
    (x,) = res["viagens"]
    assert x["status_nimer"] == "lost" and x["status_efetivo"] == "done" and x["corrigida"]
    assert res["icv"]["nimer_ate_agora"] == 100.0


def test_agregado_de_duas_linhas_e_ponderado():
    r1 = calcular([viagem("A", 1, 10, "done", real_min=10)] * 1 + [viagem("B", 1, 20, "lost")], [], agora_min=100)
    r2 = calcular([viagem(f"T{i}", 1, 10 + i, "done", carro=f"30{i}", real_min=10 + i) for i in range(8)], [], agora_min=100)
    ag = tr.agregar([r1, r2])
    # média simples daria (50 + 100) / 2 = 75; ponderado: 9 de 10
    assert ag["icv_nimer_ate_agora"] == 90.0


# ─── R.A × S.O.S (decisão 1) ────────────────────────────────────────────────

RECOLHE = "4- Recolhe Anormal / SOS"


def test_2293_perdeu_por_recolhe_e_voltou_a_rodar_na_tabela_032207_vira_sos():
    """Caso real de 10/11: 2293 perdida 13:28 por recolhe, voltou à tarde (032207)."""
    viagens = [viagem("032116", 1, 268, "lost", carro="2293", motivo=RECOLHE),     # 13:28 SP
               viagem("032207", 2, 420, "done", carro="2293", real_min=421)]
    res = calcular(viagens, [], agora_min=600)
    perdida = res["viagens"][0]
    assert perdida["recolhe"] == "SOS" and perdida["contador"] == "SOS"


def test_recolhe_volta_pelo_sim_sem_viagem_no_nimer_vira_sos():
    viagens = [viagem("032116", 1, 268, "lost", carro="2293", motivo=RECOLHE)]
    res = calcular(viagens, viagem_sim("2293", 2, 400), agora_min=600)
    assert res["viagens"][0]["recolhe"] == "SOS"


def test_recolhe_entrou_na_g3_e_nao_voltou_ate_o_fim_vira_ra():
    viagens = [viagem("032116", 1, 268, "lost", carro="2293", motivo=RECOLHE),
               viagem("032104", 1, 300, "done", carro="2412", real_min=300)]
    leituras = na_garagem("2293", 280, 320)
    antes_do_fim = calcular(viagens, leituras, agora_min=330)
    assert antes_do_fim["viagens"][0]["recolhe"] == "RA_PROVISORIO"
    assert antes_do_fim["viagens"][0]["contador"] == "RA"
    depois = calcular(viagens, leituras, agora_min=400)  # passou do fim da última viagem (300 + 50)
    assert depois["fim_servico"] is True
    assert depois["viagens"][0]["recolhe"] == "RA" and depois["viagens"][0]["contador"] == "RA"


def test_carro_que_rodou_antes_da_perda_nao_vira_sos():
    viagens = [viagem("032116", 1, 100, "done", carro="2293", real_min=100),
               viagem("032116", 2, 268, "lost", carro="2293", motivo=RECOLHE)]
    res = calcular(viagens, viagem_sim("2293", 1, 100), agora_min=330)
    assert _por_tabela(res)[("032116", em(268))]["recolhe"] == "RA_PROVISORIO"


def test_fiscal_corrige_o_motivo_e_vale_a_correcao():
    v = viagem("032116", 1, 268, "lost", carro="2293", motivo=RECOLHE)
    res = calcular([v], [], agora_min=600, correcoes={v["id"]: {"motivo": {"valor": "FALTA_OPERADORES"}}})
    assert res["viagens"][0]["contador"] == "FALTA_OPERADORES"


def test_transito_fica_fora_dos_contadores():
    res = calcular([viagem("032116", 1, 268, "lost", motivo="3 - Trânsito")], [], agora_min=600)
    v = res["viagens"][0]
    assert v["contador"] is None and v["motivo_canonico"] == "TRANSITO"


def test_motivo_ainda_nao_decidido_vai_para_o_obs():
    res = calcular([viagem("032116", 1, 268, "lost", motivo="1 - Falta de Motorista")], [], agora_min=600)
    assert res["viagens"][0]["contador"] is None and res["viagens"][0]["motivo_canonico"] is None


def test_instante_do_horario_de_madrugada_e_do_dia_seguinte():
    from datetime import date, time
    a = tr.instante_do_horario(date(2026, 11, 10), time(0, 30))
    b = tr.instante_do_horario(date(2026, 11, 10), time(23, 30))
    assert a - b == timedelta(hours=1)


# ─── Endpoint (banco → núcleo → tela) ───────────────────────────────────────

def test_endpoint_tempo_real_le_nimer_correcao_e_sim_do_banco(monkeypatch):
    from datetime import date
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.database import get_db
    from app.main import app
    from app.models.cadastro import Funcionario
    from app.models.monitoramento import (
        Coleta,
        LeituraVeiculo,
        NimerColeta,
        NimerViagem,
        ViagemCorrecao,
    )
    from app.routers import monitoramento as router_mod
    from tests.ambiente_monitoramento import criar_engine, liberar_tudo

    liberar_tudo(monkeypatch)

    eng = criar_engine()
    fiscal = uuid4()
    with Session(eng) as db:
        db.add(Funcionario(id=fiscal, re="70001", nome="Fiscal", status="ATIVO"))
        base = dict(data_servico=date(2026, 11, 10), linha_codigo="1726-10", sentido=1, motorista_re="90011")
        db.add(NimerViagem(nimer_departure_id="1", tabela="032104", status="done", prog_inicio=em(30),
                           real_inicio=em(31), carro="2412", **base))
        db.add(NimerViagem(nimer_departure_id="2", tabela="032105", status="lost", prog_inicio=em(60),
                           carro="2500", motivo_perda_texto="4- Recolhe Anormal / SOS", **base))
        db.add(NimerColeta(linha_codigo="1726-10", status="ok", pontualidade_pct=81.5))
        db.add(Coleta(status="ok", qtd_veiculos=1))
        for r in viagem_sim("2412", 1, 31):
            db.add(LeituraVeiculo(posicao_em=r["em"], prefixo="2" + r["prefixo"], linha_codigo="1726-10",
                                  sim_codigo=1403, sentido=1, lat=r["la"], lon=r["lo"]))
        db.flush()
        db.add(ViagemCorrecao(nimer_viagem_id=2, campo="motivo", valor_nimer="4- Recolhe Anormal / SOS",
                              valor_corrigido="TROCA_OPERACIONAL", justificativa="carro trocado no TP",
                              autor_funcionario_id=fiscal))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    checker = router_mod.Leitura.__metadata__[0].dependency
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[checker] = lambda: Funcionario(id=fiscal, re="70001", nome="Fiscal")
    try:
        r = TestClient(app).get("/monitoramento/linhas/1726-10/tempo-real?data=2026-11-10")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(checker, None)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["icv"]["nimer_ate_agora"] == 50.0 and j["icv"]["sim_ate_agora"] == 50.0
    assert j["pontualidade_nimer"] == 81.5 and "não o número oficial" in j["icv_sim_rotulo"]
    p1, p2 = j["partidas"]
    assert p1["programado"] == "09:30" and p1["sim_viu"] == "sim" and p1["escalado_nimer"]["motorista_re"] == "90011"
    assert p2["contador"] == "TROCA_OPERACIONAL" and p2["campos_corrigidos"] == ["motivo"]
