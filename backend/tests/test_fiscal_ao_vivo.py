"""v4.0 fase 3, bloco E — tela do fiscal ao vivo (endpoint agregado).

O comportamento ERRADO que barramos:
- trânsito pintado sem dado ao vivo (só pelo histórico) ou com limites
  espalhados pelo código;
- partida que sai do terminal do fiscal sem dizer onde está o carro (na G3,
  em outra linha, a caminho e se chega a tempo);
- fiscal em `previsto` (sem foto no NIMER) podendo corrigir viagem, e fiscal
  sem turno vendo a tela.
⛔ Sem dado pessoal real.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import LinhaMonitoradaCadastro, NimerFiscalEscala
from app.routers import monitoramento as router_mod
from app.services import fiscal_ao_vivo as fav
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.monitoramento_cadastro import data_servico_de
from tests.ambiente_monitoramento import criar_engine
from tests.cenario_tempo_real import calcular, em, viagem, viagem_sim

# ─── Trânsito (T9) ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kmh, esperado", [(16, "livre"), (15, "livre"), (10, "intenso"), (7, "intenso"),
                                           (5, "parado"), (None, None)])
def test_cor_do_transito_pelos_limites_da_tabela_unica(kmh, esperado):
    assert fav.classificar_transito(kmh) == esperado


def test_transito_usa_so_os_trechos_que_faltam_e_so_dado_ao_vivo():
    ao_vivo_ = {0: [5, 5, 5], 1: [5, 5], 4: [16, 17], 5: [16, 18, 20]}
    assert fav.transito_restante(2000, ao_vivo_) == "livre"     # do trecho 4 em diante: livre
    assert fav.transito_restante(0, {0: [10, 10], 1: [9, 11]}) == "intenso"
    assert fav.transito_restante(0, {}) is None                   # sem dado ao vivo → sem selo
    assert fav.transito_restante(0, {0: [30]}) is None            # 1 amostra só não vale
    assert fav.transito_restante(None, ao_vivo_) is None


# ─── Situação de cada partida que sai daqui ─────────────────────────────────

def _viagens(viagens, leituras, agora_min):
    return calcular(viagens, leituras, agora_min=agora_min)["viagens"]


def test_saiu_pontual_e_verde_e_adiantada_em_vermelho():
    (v,) = _viagens([viagem("1", 2, 60, "done", carro="2412", real_min=61)], [], 70)
    st = fav.situacao_saindo(v, None, None, "TS", em(70), 3)
    assert st["tipo"] == "saiu" and st["texto"].endswith("· +1") and st["cor"] == "verde"
    (v,) = _viagens([viagem("1", 2, 70, "done", carro="2412", real_min=64)], [], 75)
    st = fav.situacao_saindo(v, None, None, "TS", em(75), 3)
    assert "adiantada −6" in st["texto"] and st["cor"] == "vermelho"


def test_no_terminal_sai_em_n_min():
    (v,) = _viagens([viagem("1", 2, 80, "future", carro="2412")], [], 76)
    st = fav.situacao_saindo(v, {"zona": "TS"}, None, "TS", em(76), 3)
    assert st == {"tipo": "terminal", "texto": "no terminal · sai em 4 min", "cor": "verde"}


def test_na_g3_e_em_outra_linha_vai_perder():
    (v,) = _viagens([viagem("1", 2, 80, "future", carro="2412")], [], 76)
    assert fav.situacao_saindo(v, {"zona": "garagem"}, None, "TS", em(76), 3)["texto"] == "na G3 · vai perder"
    st = fav.situacao_saindo(v, None, "2011-10", "TS", em(76), 3)
    assert st["texto"] == "em outra linha (2011) · vai perder" and st["cor"] == "vermelho"


def test_a_caminho_com_chegada_depois_da_partida_fica_amarelo():
    (v,) = _viagens([viagem("1", 2, 100, "future", carro="2412")], [], 76)
    chegada = em(103)
    estado = {"zona": "rota", "destino": "TS", "local": "Av. Cruzeiro do Sul",
              "eta": {"chegada_em": chegada.isoformat(), "chegada": "10:43", "minutos": 27}}
    st = fav.situacao_saindo(v, estado, None, "TS", em(76), 3)
    assert st["texto"] == "a caminho · Av. Cruzeiro do Sul · chega 10:43 · +3" and st["cor"] == "amarelo"


def test_saindo_mostra_as_2_ultimas_e_as_4_proximas_do_sentido_que_parte_daqui():
    viagens = [viagem(str(i), 2, 10 * i, "done" if 10 * i < 50 else "future", carro=f"20{i:02d}",
                      real_min=10 * i if 10 * i < 50 else None) for i in range(1, 10)]
    viagens.append(viagem("x", 1, 55, "future", carro="2999"))  # outro sentido: fora
    vs = _viagens(viagens, [], 45)
    lista = fav.montar_saindo(vs, 2, {}, {}, "TS", em(45), 3)
    assert [p["programado"] for p in lista] == ["09:30", "09:40", "09:50", "10:00", "10:10", "10:20"]
    assert [p["passada"] for p in lista] == [True, True, False, False, False, False]


def test_carro_real_aparece_no_lugar_do_programado_quando_houve_troca():
    vs = _viagens([viagem("032101", 1, 60, "done", carro="2722", real_min=60)], viagem_sim("2797", 1, 61), 80)
    (p,) = fav.montar_saindo(vs, 1, {}, {}, "TP", em(80), 3)
    assert p["carro"] == "2797" and p["carro_programado"] == "2722" and p["trocado"] is True


def test_chegando_ordenado_pela_previsao_com_transito():
    carros = [
        {"prefixo_patio": "2001", "sentido": 1, "destino": "TS", "zona": "rota", "local": "Rua A", "progresso_m": 0,
         "eta": {"minutos": 12, "chegada": "10:12"}},
        {"prefixo_patio": "2002", "sentido": 1, "destino": "TS", "zona": "rota", "local": "Rua B", "progresso_m": 0,
         "eta": {"minutos": 6, "chegada": "10:06"}},
        {"prefixo_patio": "2003", "sentido": 2, "destino": "TP", "zona": "rota", "progresso_m": 0, "eta": None},
        {"prefixo_patio": "2004", "sentido": 1, "destino": "TS", "zona": "TS", "eta": None},
    ]
    lista = fav.montar_chegando(carros, "TS", {1: {0: [5, 6]}})
    assert [c["carro"] for c in lista] == ["2002", "2001"]
    assert lista[0]["transito"] == "parado" and lista[0]["rua"] == "Rua B"


# ─── Endpoint ───────────────────────────────────────────────────────────────

FISCAL = Funcionario(id=uuid4(), re="98001", nome="Fiscal com turno")
PREVISTO = Funcionario(id=uuid4(), re="98002", nome="Fiscal previsto")
SEM = Funcionario(id=uuid4(), re="98003", nome="Fiscal sem turno")
LOCAL = "metro santana 178t/10 - 1726/10 - 1764/10 - 2025/10 - 1783/10"


@pytest.fixture
def cli(monkeypatch):
    eng = criar_engine()
    agora = datetime.now(timezone.utc)
    hoje: date = data_servico_de(agora)
    with Session(eng) as db:
        for f in (FISCAL, PREVISTO, SEM):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        t = Turno(id=uuid4(), funcionario_id=FISCAL.id, fiscal_re=FISCAL.re, terminal="TS", periodo="2",
                  data_referencia=hoje, status="ABERTO", aberto_em=agora, origem="NIMER")
        db.add(t)
        db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo="1726-10"))
        # Santana: TS para a 1726-10, TP para a 178T-10 (ajustes de 12/11 noite)
        db.add(LinhaMonitoradaCadastro(linha_codigo="178T-10", ativo=True))
        db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo="178T-10"))
        db.add(NimerFiscalEscala(data_servico=hoje, re=FISCAL.re, tabela="1", pegada_em=agora - timedelta(hours=1),
                                 local_texto=LOCAL, inicio_em=agora - timedelta(minutes=50)))
        db.add(NimerFiscalEscala(data_servico=hoje, re=PREVISTO.re, tabela="2", pegada_em=agora + timedelta(minutes=20),
                                 largada_em=agora + timedelta(hours=9), local_texto=LOCAL))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    estado = {"u": FISCAL}
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False: recurso == "fiscalizacao")
    ao_vivo.limpar_cache()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[router_mod.Leitura.__metadata__[0].dependency] = lambda: estado["u"]
    cliente = TestClient(app)
    cliente.como = lambda f: estado.update(u=f)
    try:
        yield cliente
    finally:
        app.dependency_overrides.clear()
        ao_vivo.limpar_cache()


URL = "/monitoramento/linhas/1726-10/fiscal-ao-vivo"


def test_fiscal_com_turno_ve_a_tela_inteira(cli):
    r = cli.get(URL)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) >= {"cabecalho", "numeros", "a_conferir", "alertas", "saindo", "chegando", "robo"}
    cab = j["cabecalho"]
    assert cab["terminal"] == "TS" and cab["fiscal"] == {"re": "98001", "periodo": "2"}
    assert cab["selo"]["texto"].startswith("NIMER ✓ ") and cab["pode_corrigir"] is True
    assert j["saindo"]["sentido"] == 2 and j["chegando"]["sentido"] == 1
    assert set(j["numeros"]["por_sentido"]) == {"1", "2"}


def test_fiscal_previsto_ve_em_modo_previa_sem_corrigir(cli):
    cli.como(PREVISTO)
    j = cli.get(URL).json()
    assert j["cabecalho"]["modo"] == "previsto" and j["cabecalho"]["pode_corrigir"] is False
    assert j["cabecalho"]["pode_baixar"] is False
    assert j["faixa_previsto"].startswith("Previsto: metro santana às ")


def test_fiscal_sem_turno_403(cli):
    cli.como(SEM)
    assert cli.get(URL).status_code == 403


def test_terminal_pela_query(cli):
    j = cli.get(URL + "?terminal=TP").json()
    assert j["cabecalho"]["terminal"] == "TP" and j["saindo"]["sentido"] == 1


def test_terminal_e_do_par_local_linha_e_nao_do_turno(cli):
    """Turno NIMER no Metrô Santana (turno.terminal = TS, a maioria das linhas):
    a 1726-10 sai como TS, mas a 178T-10 é TP nesse ponto — "Saindo daqui"
    tem que mostrar o sentido certo de cada uma."""
    assert cli.get(URL).json()["cabecalho"]["terminal"] == "TS"
    j = cli.get("/monitoramento/linhas/178T-10/fiscal-ao-vivo").json()
    assert j["cabecalho"]["terminal"] == "TP"
    assert j["saindo"]["sentido"] == 1   # TP → TS parte do TP


def test_abas_do_cabecalho_levam_o_terminal_de_cada_linha(cli):
    cab = cli.get(URL).json()["cabecalho"]
    assert cab["terminais_do_ponto"] == {"1726-10": "TS", "178T-10": "TP"}
