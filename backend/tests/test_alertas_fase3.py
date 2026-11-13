"""v4.0 fase 3, bloco D — alertas novos, T5 (indo para a garagem) e baixa (T4).

O comportamento ERRADO que barramos:
- carro que terminou a volta (TP → TS) e foi para a G3 aparecendo como
  "fora do itinerário" na caixinha do fiscal;
- carro largando a tabela no meio e indo para a G3 sem aviso de possível R.A.,
  e o aviso continuando depois que ele já entrou na G3;
- saída 4 min antes do horário passando em branco (só o HH:00 era olhado);
- carro trocado sem baixa no NIMER sem alerta;
- baixa que some para sempre mesmo com o problema continuando, e fiscal
  dando baixa em alerta de linha que não é dele.
⛔ Sem dado pessoal real.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import AlertaTratativa, LinhaMonitoradaCadastro
from app.routers import monitoramento as router_mod
from app.services import fiscalizacao_alertas as al
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services import monitoramento_regras as R
from app.services.monitoramento_cadastro import data_servico_de
from app.services.monitoramento_linhas import GARAGEM_G3, obter_linha
from tests.ambiente_monitoramento import criar_engine
from tests.cenario_tempo_real import LINHA, calcular, em, viagem, viagem_sim
from tests.test_monitoramento_regras import TP_REAL, TS_REAL, _Carro, _ponto_em


def _alertas(viagens, leituras, agora_min):
    res = calcular(viagens, leituras, agora_min=agora_min)
    return al.calcular_alertas(LINHA, res, leituras_por_carro=al.leituras_por_carro(leituras),
                               leituras_outras_linhas=[], alertas_ao_vivo=[], agora=em(agora_min))


def _do_tipo(alertas, tipo):
    return [a for a in alertas if a["tipo"] == tipo]


# ─── T6 · saída adiantada ───────────────────────────────────────────────────

def test_saida_4_min_antes_em_horario_normal_alerta():
    v = viagem("032104", 1, 70, "done", carro="2412", real_min=66)          # 10:10 → saiu 10:06
    (a,) = _do_tipo(_alertas([v], [], agora_min=75), "saida_adiantada")
    assert a["nivel"] == "alerta" and "−4 min" in a["texto"] and "faixa" not in a["texto"]


def test_saida_1_min_antes_no_horario_cheio_alerta():
    v = viagem("032104", 1, 60, "done", carro="2412", real_min=59)          # 10:00 → 09:59
    (a,) = _do_tipo(_alertas([v], [], agora_min=65), "saida_adiantada")
    assert "faixa 10h" in a["texto"]


def test_saida_1_min_antes_em_horario_normal_nao_alerta():
    v = viagem("032104", 1, 70, "done", carro="2412", real_min=69)
    assert _do_tipo(_alertas([v], [], agora_min=75), "saida_adiantada") == []


def test_saida_adiantada_usa_a_hora_do_sim_quando_houver():
    # NIMER diz 10:10 em ponto; o GPS viu sair 10:05
    v = viagem("032104", 1, 70, "done", carro="2412", real_min=70)
    (a,) = _do_tipo(_alertas([v], viagem_sim("2412", 1, 65), agora_min=80), "saida_adiantada")
    assert "(SIM)" in a["texto"]


# ─── T7 · troca sem baixa ───────────────────────────────────────────────────

def test_troca_de_carro_sem_baixa_traz_os_dois_prefixos():
    v = viagem("032101", 1, 60, "done", carro="2722", real_min=60)
    (a,) = _do_tipo(_alertas([v], viagem_sim("2797", 1, 61), agora_min=80), "troca_sem_baixa")
    assert a["texto"] == "Troca sem baixa · tab 032101 · NIMER 2722 → rodando 2797"
    assert a["carro"] == "2797" and a["sentido"] == 1


def test_mesmo_carro_nao_e_troca():
    v = viagem("032101", 1, 60, "done", carro="2722", real_min=60)
    assert _do_tipo(_alertas([v], viagem_sim("2722", 1, 61), agora_min=80), "troca_sem_baixa") == []


# ─── T5 · indo para a garagem ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def linha():
    return obter_linha("1726-10")


def _rumo_g3(c, de, passos=12, ate=1.0, s=1):
    for k in range(1, passos + 1):
        f = ate * k / passos
        c.em((de[0] + f * (GARAGEM_G3[0] - de[0]), de[1] + f * (GARAGEM_G3[1] - de[1])), s)
    return c


def _analisar(linha, c):
    return R.analisar_carro("22412", [dict(r) for r in c.leituras], linha, c.leituras[-1]["t"])


def test_carro_tp_ts_rumo_g3_nao_gera_desvio(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, R.comprimento(linha, 1)).em(TS_REAL, 1)
    _rumo_g3(c, TS_REAL, ate=0.7)  # ainda a caminho (não chegou na G3)
    fora = [r for r in c.leituras if R.classificar(dict(r), linha)["_z"] == "fora"]
    assert len(fora) >= R.LEITURAS_DESVIO  # o teste só vale se o caminho sai mesmo da rota
    res = _analisar(linha, c)
    assert [e for e in res["eventos"] if e["tipo"] == "desvio"] == []


def _cerca(linha, c):
    res = _analisar(linha, c)
    agora_ms = c.leituras[-1]["t"]
    alertas, _, fim_de_volta = ao_vivo.separar_eventos("22412", res["eventos"], linha, agora_ms)
    return alertas + fim_de_volta  # o mesmo que montar_alertas junta (alertas_agora + fim_de_volta_agora)


def test_saindo_da_rota_no_meio_da_viagem_vira_indo_garagem_e_some_na_g3(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 7000)
    meio = _ponto_em(linha, 1, 7000)
    _rumo_g3(c, meio, passos=10, ate=0.6)
    agora = datetime.fromtimestamp(c.leituras[-1]["t"] / 1000, tz=timezone.utc)
    prox = dict(viagem("032110", 2, 0, "future", carro="2412"), status_efetivo="future",
                prog_inicio=agora + timedelta(minutes=40))
    (a,) = [x for x in al.alertas_da_cerca(linha, _cerca(linha, c), [prox], agora) if x["ativo"]]
    assert a["tipo"] == "indo_garagem" and a["texto"].startswith("Indo para a G3 — possível R.A.")
    assert a["nivel"] == "alerta" and a["tabela"] == "032110"

    _rumo_g3(c, (c.leituras[-1]["la"], c.leituras[-1]["lo"]), passos=6)   # entrou na G3
    agora = datetime.fromtimestamp(c.leituras[-1]["t"] / 1000, tz=timezone.utc)
    assert [x for x in al.alertas_da_cerca(linha, _cerca(linha, c), [prox], agora) if x["ativo"]] == []


def test_desvio_rumo_g3_sem_viagem_pela_frente_continua_desvio(linha):
    c = _Carro().em(TP_REAL, 1).percorrer(linha, 1, 0, 7000)
    _rumo_g3(c, _ponto_em(linha, 1, 7000), passos=10, ate=0.6)
    agora = datetime.fromtimestamp(c.leituras[-1]["t"] / 1000, tz=timezone.utc)
    (a,) = [x for x in al.alertas_da_cerca(linha, _cerca(linha, c), [], agora) if x["ativo"]]
    assert a["tipo"] == "desvio"


# ─── T4 · baixa ─────────────────────────────────────────────────────────────

AGORA = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


def _alerta_ativo(chave="desvio|1726-10||2412|202611121500"):
    return {"chave": chave, "ativo": True}


def _tratativa(motivo, minutos_atras):
    em_ = AGORA - timedelta(minutes=minutos_atras)
    reabre = None if motivo in (None, "alarme_falso") else em_ + al.REABRE_EM
    return SimpleNamespace(motivo=motivo, perfil="fiscal", criado_em=em_, reabre_em=reabre)


def test_baixa_tira_o_alerta_da_caixinha():
    (a,) = al.aplicar_baixas([_alerta_ativo()], {_alerta_ativo()["chave"]: _tratativa("atitude_tomada", 5)}, AGORA)
    assert a["aberto"] is False and a["baixa"]["motivo_texto"] == "Atitude tomada"


def test_baixa_volta_a_piscar_depois_de_15_min_se_continuar_ativo():
    chave = _alerta_ativo()["chave"]
    (a,) = al.aplicar_baixas([_alerta_ativo()], {chave: _tratativa("contatou_cco", 16)}, AGORA)
    assert a["aberto"] is True and a["reaberto"] is True
    inativo = dict(_alerta_ativo(), ativo=False)
    (b,) = al.aplicar_baixas([inativo], {chave: _tratativa("contatou_cco", 16)}, AGORA)
    assert b["aberto"] is False


def test_alarme_falso_nao_reabre():
    chave = _alerta_ativo()["chave"]
    (a,) = al.aplicar_baixas([_alerta_ativo()], {chave: _tratativa("alarme_falso", 120)}, AGORA)
    assert a["aberto"] is False and a["reaberto"] is False


FISCAL = Funcionario(id=uuid4(), re="97001", nome="Fiscal da 1726")
FISCAL_OUTRA = Funcionario(id=uuid4(), re="97002", nome="Fiscal de outra linha")
COORD = Funcionario(id=uuid4(), re="97003", nome="Coordenação")


@pytest.fixture
def cli(monkeypatch):
    eng = criar_engine()
    hoje = data_servico_de()
    with Session(eng) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo="8012-10", ativo=True))
        for f in (FISCAL, FISCAL_OUTRA, COORD):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        for f, linha_ in ((FISCAL, "1726-10"), (FISCAL_OUTRA, "8012-10")):
            t = Turno(id=uuid4(), funcionario_id=f.id, fiscal_re=f.re, terminal="TS", periodo="2",
                      data_referencia=hoje, status="ABERTO", aberto_em=datetime.now(timezone.utc), origem="NIMER")
            db.add(t)
            db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo=linha_))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    estado = {"u": FISCAL}
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False:
                        (recurso == "fiscalizacao_painel" and fid == COORD.id)
                        or (recurso == "fiscalizacao" and fid != COORD.id))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[router_mod.Leitura.__metadata__[0].dependency] = lambda: estado["u"]
    cliente = TestClient(app)
    cliente.como = lambda f: estado.update(u=f)
    cliente.eng = eng
    try:
        yield cliente
    finally:
        app.dependency_overrides.clear()


def _baixa(cli, motivo="atitude_tomada", linha="1726-10", chave="desvio|1726-10||2412|202611121500"):
    return cli.post("/monitoramento/alertas/baixa", json={"linha_codigo": linha, "chave": chave, "motivo": motivo})


def test_fiscal_da_linha_da_baixa_e_reabre_em_15_min(cli):
    r = _baixa(cli)
    assert r.status_code == 200, r.text
    assert r.json()["perfil"] == "fiscal"
    with Session(cli.eng) as db:
        (t,) = db.execute(select(AlertaTratativa)).scalars().all()
        reabre = t.reabre_em.replace(tzinfo=timezone.utc) if t.reabre_em.tzinfo is None else t.reabre_em
        criado = t.criado_em.replace(tzinfo=timezone.utc) if t.criado_em.tzinfo is None else t.criado_em
        assert t.motivo == "atitude_tomada" and reabre - criado == timedelta(minutes=15)


def test_alarme_falso_grava_sem_reabertura(cli):
    assert _baixa(cli, motivo="alarme_falso").json()["reabre_em"] is None


def test_fiscal_fora_do_escopo_403(cli):
    cli.como(FISCAL_OUTRA)
    assert _baixa(cli).status_code == 403


def test_coordenacao_da_baixa_em_qualquer_linha(cli):
    cli.como(COORD)
    r = _baixa(cli, motivo="contatou_cco")
    assert r.status_code == 200 and r.json()["perfil"] == "coordenacao"


def test_motivo_invalido_422(cli):
    assert _baixa(cli, motivo="sei_la").status_code == 422


def test_alarmes_falsos_por_tipo():
    from app.services.fiscalizacao_central import alarmes_falsos
    tratadas = [SimpleNamespace(chave="desvio|a", motivo="alarme_falso"),
                SimpleNamespace(chave="desvio|b", motivo="alarme_falso"),
                SimpleNamespace(chave="parado|c", motivo="alarme_falso"),
                SimpleNamespace(chave="parado|d", motivo="atitude_tomada")]
    assert alarmes_falsos(tratadas) == [{"tipo": "desvio", "quantidade": 2}, {"tipo": "parado", "quantidade": 1}]


def test_data_de_servico_e_de_sao_paulo():  # sanidade do carimbo usado na baixa
    assert data_servico_de(datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc)) == date(2026, 11, 11)


# ─── T5 revisto (12/11 22:46): fez a volta e vai para a G3 NO MEIO da tabela ─

def _volta_e_rumo_g3(linha, ate=0.7):
    """G3 → TP → TS → fora da rota se aproximando da G3 (caso 2735 em 11/11)."""
    c = _Carro().em(GARAGEM_G3, 1)
    c.em(TP_REAL, 1).percorrer(linha, 1, 0, R.comprimento(linha, 1)).em(TS_REAL, 1)
    return _rumo_g3(c, TS_REAL, ate=ate)


def _agora(c):
    return datetime.fromtimestamp(c.leituras[-1]["t"] / 1000, tz=timezone.utc)


def _proxima(agora, minutos=40):
    return dict(viagem("032120", 2, 0, "future", carro="2412"), status_efetivo="future",
                prog_inicio=agora + timedelta(minutes=minutos))


def test_fez_a_volta_e_vai_para_a_g3_no_meio_da_tabela_avisa_indo_garagem(linha):
    c = _volta_e_rumo_g3(linha)
    agora = _agora(c)
    (a,) = [x for x in al.alertas_da_cerca(linha, _cerca(linha, c), [_proxima(agora)], agora) if x["ativo"]]
    assert a["tipo"] == "indo_garagem" and a["nivel"] == "alerta"
    assert a["texto"].startswith("Indo para a G3 — possível R.A.") and a["tabela"] == "032120"


def test_fez_a_volta_e_vai_para_a_g3_na_ultima_viagem_nao_avisa_nada(linha):
    c = _volta_e_rumo_g3(linha)
    agora = _agora(c)
    cerca = _cerca(linha, c)
    # a cerca AVISA o fim de volta (não apaga mais); quem descarta é a tabela
    assert [e["tipo"] for e in cerca] == ["fim_de_volta"]
    sem_viagem = al.alertas_da_cerca(linha, cerca, [], agora)
    assert sem_viagem == []
    # viagem só daqui a 3 h também não conta como "no meio da tabela" (INDO_GARAGEM_PROXIMA = 2 h)
    assert al.alertas_da_cerca(linha, _cerca(linha, c), [_proxima(agora, 180)], agora) == []


def test_fez_a_volta_no_meio_da_tabela_e_entrou_na_g3_o_alerta_fica_inativo(linha):
    c = _volta_e_rumo_g3(linha)
    prox = _proxima(_agora(c))
    _rumo_g3(c, (c.leituras[-1]["la"], c.leituras[-1]["lo"]), passos=6)   # entrou na G3
    agora = _agora(c)
    alertas = al.alertas_da_cerca(linha, _cerca(linha, c), [prox], agora)
    assert [x for x in alertas if x["ativo"]] == []
    assert [x["tipo"] for x in alertas] == ["indo_garagem"]   # ainda na janela de 20 min, mas inativo


def test_fim_de_volta_nao_aparece_nas_telas_da_fase_1_e_2(linha):
    c = _volta_e_rumo_g3(linha)
    res = _analisar(linha, c)
    agora_ms = c.leituras[-1]["t"]
    alertas, ocorrencias, fim = ao_vivo.separar_eventos("22412", res["eventos"], linha, agora_ms)
    assert alertas == [] and ocorrencias == [] and len(fim) == 1
    assert ao_vivo.classe_carro(res, False, agora_ms) != "alerta"
