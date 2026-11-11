"""Bloco E — alertas operacionais calculados (nada gravado, D24).

Testes do comportamento ERRADO que evitamos: "partida atrasada" disparando
para carro parado no terminal DENTRO do horário; carro trocado de linha não
detectado com duas linhas monitoradas ao mesmo tempo.
"""
from app.services import fiscalizacao_alertas as al
from app.services.monitoramento_linhas import linha_do_json
from tests.cenario_tempo_real import LINHA, calcular, em, parado, viagem, viagem_sim

TP = LINHA.terminais["TP"][0]


def _alertas(viagens, leituras, agora_min, outras=(), cerca=(), linha=LINHA):
    res = calcular(viagens, leituras, agora_min=agora_min, linha=linha)
    return al.calcular_alertas(
        linha, res, leituras_por_carro=al.leituras_por_carro(leituras),
        leituras_outras_linhas=list(outras), alertas_ao_vivo=list(cerca), agora=em(agora_min),
    )


def _tipos(alertas):
    return [a["tipo"] for a in alertas]


# ─── 1. Tabela sem motorista ────────────────────────────────────────────────

def test_tabela_sem_motorista_alerta_ate_60_min_antes_da_1a_partida():
    viagens = [viagem("032116", 1, 100, "future", sem_motorista=True), viagem("032116", 2, 160, "future", sem_motorista=True)]
    assert _tipos(_alertas(viagens, [], agora_min=30)) == []          # faltam 70 min
    (a,) = _alertas(viagens, [], agora_min=50)                         # faltam 50 min
    assert a["tipo"] == "sem_motorista" and a["nivel"] == "atencao" and a["tabela"] == "032116"
    assert a["escalado_nimer"]["motorista_re"] is None and a["acao_sugerida"]
    (b,) = _alertas(viagens, [], agora_min=90)                         # faltam 10 min
    assert b["nivel"] == "alerta" and b["chave"] == a["chave"]         # mesma chave: a tratativa vale


def test_tabela_sem_motorista_que_ja_rodou_nao_alerta():
    viagens = [viagem("032116", 1, 100, "done", real_min=100, sem_motorista=True),
               viagem("032116", 2, 160, "future", sem_motorista=True)]
    assert _tipos(_alertas(viagens, [], agora_min=120)) == []


def test_tabela_sem_motorista_que_nao_rodou_continua_alertando_com_as_perdas():
    viagens = [viagem("032116", 1, 100, "lost", sem_motorista=True), viagem("032116", 2, 160, "missing", sem_motorista=True)]
    (a,) = _alertas(viagens, [], agora_min=170)
    assert "2 partida(s)" in a["texto"]


# ─── 2. Partida atrasada no terminal ────────────────────────────────────────

def test_partida_atrasada_com_o_carro_parado_no_terminal_de_origem():
    """Caso 2400 em 10/11: parado no TP, viagem das 13:09 perdida."""
    viagens = [viagem("032110", 1, 100, "missing", carro="2400")]
    (a,) = _alertas(viagens, parado("2400", 1, TP, 60, 112), agora_min=112)
    assert a["tipo"] == "partida_atrasada" and a["nivel"] == "alerta"
    assert "12 min" in a["texto"] and a["carro"] == "2400"


def test_carro_parado_no_terminal_dentro_do_horario_nao_dispara():
    viagens = [viagem("032110", 1, 100, "future", carro="2400")]
    # 40 min parado no TP (entrepico normal), mas a partida é só daqui a 10 min
    assert _tipos(_alertas(viagens, parado("2400", 1, TP, 50, 90), agora_min=90)) == []
    # passou 3 min do horário: ainda na tolerância de 5 min
    assert _tipos(_alertas(viagens, parado("2400", 1, TP, 50, 103), agora_min=103)) == []


def test_carro_que_saiu_do_terminal_nao_e_partida_atrasada():
    viagens = [viagem("032110", 1, 100, "missing", carro="2400")]
    assert "partida_atrasada" not in _tipos(_alertas(viagens, viagem_sim("2400", 1, 104), agora_min=112))


def test_sem_posicao_recente_nao_afirma_que_esta_parado():
    viagens = [viagem("032110", 1, 100, "missing", carro="2400")]
    assert _tipos(_alertas(viagens, parado("2400", 1, TP, 60, 104), agora_min=115)) == []


# ─── 3. Carro trocado de linha (duas linhas monitoradas) ────────────────────

def test_carro_da_tabela_rodando_em_outra_linha_monitorada():
    """Caso 2742 em 10/11: tabela da 1726, rodou o dia na 2011-10."""
    outra = "8012-10"
    viagens = [viagem("032120", 1, 100, "missing", carro="2742"), viagem("032120", 2, 160, "future", carro="2742")]
    na_outra = [dict(r, linha=outra) for r in viagem_sim("2742", 1, 95)]
    (a,) = [x for x in _alertas(viagens, [], agora_min=120, outras=na_outra) if x["tipo"] == "carro_trocado_linha"]
    assert outra in a["texto"] and a["tabela"] == "032120" and a["carro"] == "2742"
    # e na outra linha (sem esse carro escalado) não aparece nada
    linha_b = linha_do_json(outra, None)
    assert _alertas([], na_outra, agora_min=120, linha=linha_b) == []


def test_posicao_antiga_em_outra_linha_nao_alerta():
    viagens = [viagem("032120", 1, 100, "missing", carro="2742")]
    antigas = [dict(r, linha="8012-10") for r in parado("2742", 1, TP, 10, 20)]
    assert "carro_trocado_linha" not in _tipos(_alertas(viagens, [], agora_min=120, outras=antigas))


# ─── 4. Buraco de intervalo ─────────────────────────────────────────────────

def test_buraco_de_intervalo_maior_que_1_5_vez_o_programado():
    # programado a cada 10 min (sentido 1); saíram 10:00, 10:10, e a próxima só 10:40
    viagens = [viagem(f"T{i}", 1, 60 + 10 * i, "done" if i in (0, 1, 4) else "lost", carro=f"24{i}0",
                      real_min={0: 60, 1: 70, 4: 100}.get(i)) for i in range(6)]
    (a,) = [x for x in _alertas(viagens, [], agora_min=105) if x["tipo"] == "buraco_intervalo"]
    assert "30 min" in a["texto"] and a["nivel"] == "alerta" and not a["ativo"]


def test_buraco_em_aberto_e_ativo():
    viagens = [viagem(f"T{i}", 1, 60 + 10 * i, "done" if i == 0 else "missing", carro=f"24{i}0",
                      real_min=60 if i == 0 else None) for i in range(5)]
    (a,) = [x for x in _alertas(viagens, [], agora_min=80) if x["tipo"] == "buraco_intervalo"]
    assert a["ativo"] is True and a["nivel"] == "atencao"   # 20 min > 1,5 × 10 e ≤ 2 × 10


def test_intervalo_normal_nao_alerta():
    viagens = [viagem(f"T{i}", 1, 60 + 10 * i, "done", carro=f"24{i}0", real_min=61 + 10 * i) for i in range(5)]
    assert "buraco_intervalo" not in _tipos(_alertas(viagens, [], agora_min=105))


# ─── 5. Realizada e não contabilizada ───────────────────────────────────────

def test_realizada_no_nimer_que_o_sim_nao_viu():
    viagens = [viagem("032104", 1, 100, "done", carro="2412", real_min=100)]
    (a,) = _alertas(viagens, viagem_sim("2999", 2, 100), agora_min=200)   # o SIM via a linha, só não viu esta saída
    assert a["tipo"] == "nao_contabilizada" and "ICV oficial" in a["texto"]


# ─── 6. Fase 1 continua ─────────────────────────────────────────────────────

def test_alertas_da_cerca_virtual_entram_com_a_tabela_do_carro():
    viagens = [viagem("032104", 1, 100, "done", carro="2412", real_min=100)]
    cerca = [{"tipo": "desvio", "nivel": "alerta", "ativo": True, "prefixo_patio": "2412",
              "inicio_em": em(110).isoformat(), "texto": "Fora do itinerário desde 10:50"}]
    (a,) = [x for x in _alertas(viagens, viagem_sim("2412", 1, 100), agora_min=120, cerca=cerca) if x["tipo"] == "desvio"]
    assert a["tabela"] == "032104" and a["escalado_nimer"]["motorista_re"] == "90011" and a["acao_sugerida"]


def test_ordem_mais_urgente_primeiro():
    viagens = [viagem("032116", 1, 150, "future", sem_motorista=True),                  # atenção
               viagem("032110", 1, 100, "missing", carro="2400")]                       # alerta
    alertas = _alertas(viagens, parado("2400", 1, TP, 60, 112), agora_min=112)
    assert [a["nivel"] for a in alertas] == ["alerta", "atencao"]


def test_sem_leitura_da_linha_naquela_hora_nao_afirma_que_o_sim_nao_viu():
    viagens = [viagem("032104", 1, 100, "done", carro="2412", real_min=100)]
    assert _tipos(_alertas(viagens, viagem_sim("2999", 2, 300), agora_min=400)) == []


def test_endpoint_detecta_carro_trocado_entre_duas_linhas_monitoradas(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.database import get_db
    from app.main import app
    from app.models.cadastro import Funcionario
    from app.models.monitoramento import (
        Coleta,
        LeituraVeiculo,
        LinhaMonitoradaCadastro,
        NimerViagem,
    )
    from app.routers import monitoramento as router_mod
    from app.services.monitoramento_cadastro import data_servico_de
    from tests.ambiente_monitoramento import criar_engine, liberar_tudo

    liberar_tudo(monkeypatch)

    agora = datetime.now(timezone.utc)
    eng = criar_engine()
    with Session(eng) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo="8012-10", ativo=True))
        db.add(NimerViagem(data_servico=data_servico_de(agora), linha_codigo="1726-10", nimer_departure_id="1",
                           tabela="032120", sentido=1, status="missing", prog_inicio=agora - timedelta(minutes=20),
                           carro="2742"))
        db.add(Coleta(status="ok", qtd_veiculos=1, recebido_em=agora))
        for k in range(5):
            db.add(LeituraVeiculo(posicao_em=agora - timedelta(minutes=k), prefixo="22742", linha_codigo="8012-10",
                                  sim_codigo=5555, sentido=1, lat=-23.6, lon=-46.7 + k * 0.001))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    checker = router_mod.Leitura.__metadata__[0].dependency
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[checker] = lambda: Funcionario(id=uuid4(), re="70003", nome="Coord")
    try:
        cliente = TestClient(app)
        a = cliente.get("/monitoramento/linhas/1726-10/tempo-real").json()["alertas"]
        b = cliente.get("/monitoramento/linhas/8012-10/tempo-real").json()["alertas"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(checker, None)
    assert [x["tipo"] for x in a] == ["carro_trocado_linha"] and "8012-10" in a[0]["texto"]
    assert b == []


# ─── 7/8. Horário cheio: não liberar antes / saiu adiantado (11/11) ────────
# cenario_tempo_real: minuto 0 = 09:00 SP; minuto 60 = 10:00 (horário cheio)

def test_nao_liberar_antes_da_hora_cheia_com_o_carro_no_terminal():
    viagens = [viagem("032104", 1, 60, "future", carro="2412")]
    assert _tipos(_alertas(viagens, parado("2412", 1, TP, 40, 54), agora_min=54)) == []   # 6 min antes: cedo
    (a,) = _alertas(viagens, parado("2412", 1, TP, 40, 56), agora_min=56)                 # 4 min antes
    assert a["tipo"] == "nao_liberar_antes" and a["nivel"] == "atencao"
    assert a["texto"].startswith("Não liberar antes das 10:00 — carro 2412") and "faixa 10h" in a["texto"]


def test_fora_do_horario_cheio_nao_avisa_para_segurar():
    viagens = [viagem("032104", 1, 70, "future", carro="2412")]                           # 10:10
    assert _tipos(_alertas(viagens, parado("2412", 1, TP, 50, 67), agora_min=67)) == []


def test_saiu_adiantado_no_horario_cheio():
    viagens = [viagem("032104", 1, 60, "done", carro="2412", real_min=58)]                # 09:58 no lugar de 10:00
    (a,) = [x for x in _alertas(viagens, [], agora_min=70) if x["tipo"] == "saiu_adiantado"]
    assert a["nivel"] == "alerta" and "às 09:58" in a["texto"] and "faixa 10h" in a["texto"]
    assert "saiu_adiantado" not in _tipos(_alertas(viagens, [], agora_min=95))           # passou a janela de 30 min


def test_saida_na_hora_cheia_nao_e_adiantada():
    viagens = [viagem("032104", 1, 60, "done", carro="2412", real_min=61)]
    assert "saiu_adiantado" not in _tipos(_alertas(viagens, [], agora_min=70))
