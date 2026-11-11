"""IPP = pontualidade de partida (regra do Alisson, 11/11/2026 00:58).

Pontual = saída em ±3 min do programado; no horário cheio (HH:00) só de 0
a +3 min. IPP = pontuais ÷ programadas.
"""
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.fiscalizacao import Parametro
from app.models.monitoramento import NimerViagem
from app.services import fiscalizacao_tempo_real as tr
from app.services.monitoramento_linhas import obter_linha
from tests.ambiente_monitoramento import criar_engine
from tests.cenario_tempo_real import calcular, viagem, viagem_sim


def sp(hhmm: str, seg: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, 10, h, m, seg, tzinfo=FUSO_OPERACAO).astimezone(timezone.utc)


def test_classificacao_basica_e_desvio_em_minutos():
    assert tr.classificar_pontualidade(sp("07:10"), sp("07:12")) == {"classe": "pontual", "desvio_min": 2}
    assert tr.classificar_pontualidade(sp("07:10"), sp("07:05")) == {"classe": "adiantada", "desvio_min": -5}
    assert tr.classificar_pontualidade(sp("07:10"), sp("07:20")) == {"classe": "atrasada", "desvio_min": 10}
    assert tr.classificar_pontualidade(sp("07:10"), None)["classe"] == "sem_dado"
    assert tr.horario_cheio(sp("08:00")) and not tr.horario_cheio(sp("08:01"))


def test_tolerancia_vem_do_parametro():
    assert tr.classificar_pontualidade(sp("07:10"), sp("07:14"), tolerancia_min=5)["classe"] == "pontual"
    eng = criar_engine()
    with Session(eng) as db:
        db.add(Parametro(chave="ipp_tolerancia_min", valor=5, descricao="teste"))
        db.add(NimerViagem(id=1, data_servico=date(2026, 11, 10), linha_codigo="1726-10", nimer_departure_id="1",
                           tabela="032104", sentido=1, status="done", prog_inicio=sp("07:10"),
                           real_inicio=sp("07:14"), carro="2412"))
        db.commit()
        res = tr.montar_linha_tempo_real(db, obter_linha("1726-10", db), date(2026, 11, 10), sp("09:00"))
    assert res["ipp"]["tolerancia_min"] == 5.0 and res["ipp"]["ate_agora"] == 100.0


def test_ipp_pelo_sim_ao_lado_rotulado():
    # NIMER diz 09:31 (pontual); o SIM viu a saída em ~09:31 também
    res = calcular([viagem("032104", 1, 30, "done", real_min=31)], viagem_sim("2412", 1, 31), agora_min=200)
    assert res["ipp"]["ate_agora"] == 100.0 and res["ipp"]["sim_ate_agora"] == 100.0
    assert "não o número oficial" in res["ipp"]["sim_rotulo"]
    assert res["viagens"][0]["pontualidade_sim"]["classe"] == "pontual"


def test_agregado_do_ipp_e_ponderado():
    a = calcular([viagem("A", 1, 30, "done", carro="3001", real_min=31), viagem("B", 1, 40, "lost", carro="3002")], [],
                 agora_min=200)
    b = calcular([viagem(f"T{i}", 1, 30 + i * 10, "done", carro=f"31{i}", real_min=31 + i * 10) for i in range(8)], [],
                 agora_min=200)
    assert tr.agregar([a, b])["ipp_ate_agora"] == 90.0   # 9 pontuais de 10, não a média de 50 e 100
