"""Bloco I — Central da Fiscalização (coordenação).

Duas linhas monitoradas ao mesmo tempo. O que barramos: placar ordenado
por percentual em vez de perda absoluta (D23), farol com meta fixa no
código em vez de fiscalizacao.parametro (D29), alerta resolvido voltando
para o topo da fila.
⛔ Sem dado pessoal real: REs fictícios.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import AcaoCoordenacao, LinhaCoordenador, Parametro, Turno, TurnoLinha
from app.models.monitoramento import (
    AlertaTratativa,
    Coleta,
    LinhaMonitoradaCadastro,
    NimerColeta,
    NimerViagem,
    ViagemCorrecao,
)
from app.routers import monitoramento as router_mod
from app.services import fiscalizacao_central as servico
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.monitoramento_cadastro import data_servico_de
from tests.ambiente_monitoramento import criar_engine

COORD = Funcionario(id=uuid4(), re="70015", nome="Coordenador")
FISCAL = Funcionario(id=uuid4(), re="70011", nome="Fiscal ponto 1")
OUTRA = "8012-10"


def _dep(annotated):
    return annotated.__metadata__[0].dependency


@pytest.fixture
def cli(monkeypatch):
    agora = datetime.now(timezone.utc)
    hoje = data_servico_de(agora)
    eng = criar_engine()
    with Session(eng) as db:
        for f in (COORD, FISCAL):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        db.add(Parametro(chave="icv_meta", valor=98, descricao="meta"))
        db.add(Parametro(chave="icv_aceitavel", valor=95, descricao="aceitável"))
        db.add(LinhaMonitoradaCadastro(linha_codigo=OUTRA, nimer_line_id=99, ativo=True))
        db.add(LinhaCoordenador(id=uuid4(), linha_codigo="1726-10", funcionario_id=COORD.id, periodo="1", ativo=True))
        db.add(LinhaCoordenador(id=uuid4(), linha_codigo=OUTRA, funcionario_id=COORD.id, periodo="1", ativo=True))
        t = Turno(id=uuid4(), funcionario_id=FISCAL.id, fiscal_re=FISCAL.re, terminal="TP", periodo="1",
                  data_referencia=hoje, tipo_dia="UTIL", status="ABERTO", aberto_em=agora - timedelta(hours=3),
                  ponto_nome="Ponto TP")
        db.add(t)
        db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo="1726-10"))
        # 1726-10: 20 programadas, 2 perdidas (90%) — perde 2
        # 8012-10: 3 programadas, 1 perdida (66,7%) — perde 1, percentual pior
        k = 0
        for linha, total, perdidas in (("1726-10", 20, 2), (OUTRA, 3, 1)):
            for i in range(total):
                k += 1
                perdida = i < perdidas
                db.add(NimerViagem(
                    id=k, data_servico=hoje, linha_codigo=linha, nimer_departure_id=str(k), tabela=f"T{i:02d}",
                    sentido=1 + i % 2, status="lost" if perdida else "done",
                    prog_inicio=agora - timedelta(minutes=200 - i * 5),
                    real_inicio=None if perdida else agora - timedelta(minutes=199 - i * 5),
                    carro=f"{3000 + k}", motorista_re="90011",
                    motivo_perda_texto=("3 - Trânsito" if linha == OUTRA else "4- Recolhe Anormal / SOS") if perdida else None,
                    sem_motorista=False))
            db.add(NimerColeta(linha_codigo=linha, status="ok", recebido_em=agora))
        # tabela sem motorista com a 1ª partida daqui a 30 min (vira alerta na fila)
        db.add(NimerViagem(id=99, data_servico=hoje, linha_codigo=OUTRA, nimer_departure_id="99", tabela="T99",
                           sentido=1, status="future", prog_inicio=agora + timedelta(minutes=30), carro="3999",
                           sem_motorista=True))
        db.add(Coleta(status="ok", qtd_veiculos=0, recebido_em=agora))
        db.flush()
        db.add(ViagemCorrecao(nimer_viagem_id=2, campo="motivo", valor_nimer="4- Recolhe Anormal / SOS",
                              valor_corrigido="FALTA_OPERADORES", justificativa="motorista faltou",
                              autor_funcionario_id=FISCAL.id, criado_em=agora))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False: fid == COORD.id)
    servico.limpar_cache()
    ao_vivo.limpar_cache()
    app.dependency_overrides[get_db] = _db
    for dep in (_dep(router_mod.LeituraPainel), _dep(router_mod.EscritaPainel)):
        app.dependency_overrides[dep] = lambda: COORD
    c = TestClient(app)
    c.engine = eng
    try:
        yield c
    finally:
        app.dependency_overrides.clear()
        servico.limpar_cache()


def test_placar_por_perda_absoluta_e_farol_pelo_parametro(cli):
    j = cli.get("/monitoramento/central").json()
    assert j["escopo"] == "coordenador"
    assert [p["linha_codigo"] for p in j["placar"]] == ["1726-10", OUTRA]   # perde 2 > perde 1 (D23)
    p1726, poutra = j["placar"]
    assert p1726["icv_nimer"] == 90.0 and p1726["perda_absoluta"] == 2 and p1726["farol"] == "vermelho"
    assert poutra["icv_nimer"] == 66.67 and poutra["perda_absoluta"] == 1
    assert j["meta"] == {"icv_meta": 98.0, "icv_aceitavel": 95.0}
    # D22: ponderado — 20 de 23, não a média de 90 e 66,7
    assert j["agregado"]["icv_nimer_ate_agora"] == round(20 / 23 * 100, 2)
    assert "não o número oficial" in j["icv_sim_rotulo"]


def test_farol_amarelo_entre_aceitavel_e_meta(cli):
    with Session(cli.engine) as db:
        db.get(Parametro, "icv_aceitavel").valor = 89
        db.commit()
    j = cli.get("/monitoramento/central").json()
    assert {p["linha_codigo"]: p["farol"] for p in j["placar"]}["1726-10"] == "amarelo"


def test_pareto_do_dia_com_correcoes_e_transito(cli):
    j = cli.get("/monitoramento/central").json()
    pareto = {p["motivo"]: p["perdas"] for p in j["pareto"]}
    assert pareto == {"Falta de operadores": 1, "R.A": 1, "Trânsito (OBS)": 1}
    assert j["pareto"][-1]["acumulado_pct"] == 100.0


def test_cobertura_mostra_linha_sem_fiscal(cli):
    j = cli.get("/monitoramento/central").json()
    cob = {c["linha_codigo"]: c for c in j["cobertura"]}
    assert cob["1726-10"]["coberta"] and cob["1726-10"]["fiscais"][0]["re"] == "70011"
    assert cob[OUTRA]["coberta"] is False


def test_correcoes_e_saude_dos_robos(cli):
    j = cli.get("/monitoramento/central").json()
    (c,) = j["correcoes"]
    assert c["autor"]["re"] == "70011" and c["para"] == "FALTA_OPERADORES"
    assert {s["linha_codigo"] for s in j["saude"]} == {"1726-10", OUTRA}
    assert all(s["nimer_ultima_ok_em"] and s["sim_ultima_ok_em"] for s in j["saude"])


def test_fila_registrar_acao_e_resolver(cli):
    j = cli.get("/monitoramento/central").json()
    alvo = next(a for a in j["fila"] if a["tipo"] == "sem_motorista")
    assert alvo["linha"] == OUTRA and alvo["tabela"] == "T99"
    r = cli.post("/monitoramento/central/alertas", json={"chave": alvo["chave"], "linha_codigo": alvo["linha"],
                                                         "inicio_em": alvo["inicio_em"],
                                                         "descricao": "Liguei para o plantão", "resolver": True})
    assert r.status_code == 200 and r.json()["resolvido"] and r.json()["acao_coordenacao_id"]
    with Session(cli.engine) as db:
        (acao,) = db.execute(select(AcaoCoordenacao)).scalars().all()
        assert acao.linha_codigo == alvo["linha"] and "Liguei para o plantão" in acao.descricao
        assert acao.registrado_por == COORD.id
        assert len(db.execute(select(AlertaTratativa)).scalars().all()) == 1
    fila = cli.get("/monitoramento/central").json()["fila"]
    resolvido = next(a for a in fila if a["chave"] == alvo["chave"])
    assert resolvido["resolvido"] is True and fila[-1]["chave"] == alvo["chave"]
    # resolver de novo não duplica
    cli.post("/monitoramento/central/alertas", json={"chave": alvo["chave"], "linha_codigo": alvo["linha"], "resolver": True})
    with Session(cli.engine) as db:
        assert len(db.execute(select(AlertaTratativa)).scalars().all()) == 1


def test_acao_vazia_sem_resolver_422(cli):
    r = cli.post("/monitoramento/central/alertas", json={"chave": "x|1726-10||", "linha_codigo": "1726-10", "descricao": "  "})
    assert r.status_code == 422


def test_coordenador_sem_linha_atribuida_ve_todas(cli):
    with Session(cli.engine) as db:
        for lc in db.execute(select(LinhaCoordenador)).scalars():
            lc.ativo = False
        db.commit()
    j = cli.get("/monitoramento/central").json()
    assert j["escopo"].startswith("todas") and len(j["placar"]) == 2
