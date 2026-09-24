"""Escala de Fiscais — Fases 3 e 4 (regras e montagem do dia).

Mesmo padrão de test_escala_fiscais.py: SQLite em memória com ATTACH para o
schema coordenadoria e as permissões sobrescritas. O "é ADMIN?" (RN10) é
trocado por um interruptor do teste.

Cobre: RN01 (modelo pelo mês do próprio dia, virada de mês), RN02 (folga
base, inversão no mês par, folga não confirmada), RN03 (dobra calculada,
nunca gravada no rascunho, congelada ao publicar), RN04 (pergunta e registro
de quem confirmou), RN05, RN06/RN07 (trocas 1x1 e 2x2, dia trabalhado por
troca não é dobra), RN08 (acúmulo com e sem ponto final, pontos diferentes),
RN10, RN11, RN12, RN13, RN14 (versão e alteração), G3, término antes do
início, e a data com o relógio em UTC às 21h30 de sábado em São Paulo.

⛔ REs, nomes, linhas e postos FICTÍCIOS (repositório público).
"""
import inspect
import typing
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.escala_fiscais import (
    EscalaFiscalAlocacao,
    EscalaFiscalAlteracao,
    EscalaFiscalAusencia,
    EscalaFiscalCoordenadorHorario,
    EscalaFiscalCoordenadorPeriodo,
    EscalaFiscalDia,
    EscalaFiscalModelo,
    EscalaFiscalModeloPosto,
    EscalaFiscalPlantao,
    EscalaFiscalPontoFinal,
    EscalaFiscalPosto,
    EscalaFiscalPostoLinha,
    EscalaFiscalQuadro,
    EscalaFiscalTroca,
)
from app.routers import escala_fiscais as router_mod
from app.services import escala_fiscais_montagem as montagem
from app.services import escala_fiscais_regras as regras

_TABELAS = [
    EscalaFiscalQuadro, EscalaFiscalCoordenadorPeriodo, EscalaFiscalPontoFinal, EscalaFiscalPosto,
    EscalaFiscalPostoLinha, EscalaFiscalModelo, EscalaFiscalModeloPosto, EscalaFiscalDia,
    EscalaFiscalAlocacao, EscalaFiscalAusencia, EscalaFiscalTroca, EscalaFiscalCoordenadorHorario,
    EscalaFiscalPlantao, EscalaFiscalAlteracao,
]

# Datas de calendário do teste. Setembro = mês ímpar; outubro = par.
SAB = date(2026, 9, 26)
DOM = date(2026, 9, 27)
SAB_ANTERIOR = SAB - timedelta(days=7)
SEG = date(2026, 9, 28)

_COORD1 = Funcionario(id=uuid4(), re="80001", nome="Coordenador Um")
_COORD2 = Funcionario(id=uuid4(), re="80002", nome="Coordenador Dois")
_ADMIN = Funcionario(id=uuid4(), re="80009", nome="Admin Teste")
_FISCAL = Funcionario(id=uuid4(), re="90001", nome="Fiscal Teste A")
_PESSOAS = (_COORD1, _COORD2, _ADMIN, _FISCAL)


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


def _hm(txt):
    return time.fromisoformat(txt) if txt else None


@pytest.fixture
def amb(monkeypatch):
    """Banco fictício: 5 postos, 2 pontos finais, 5 modelos, 1 coordenador
    por período e o horário padrão do plantão."""
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=[Funcionario.__table__] + [t.__table__ for t in _TABELAS])
    ids = {}
    with Session(engine) as db:
        for f in _PESSOAS:
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome))
        pa = EscalaFiscalPontoFinal(id=uuid4(), nome="Ponto Ficticio A")
        pb = EscalaFiscalPontoFinal(id=uuid4(), nome="Ponto Ficticio B")
        db.add_all([pa, pb])
        db.flush()
        for nome, lado, linha, ponto in [
            ("P1", "TP", "9001/10", pa.id), ("P2", "TP", "9002/10", pa.id), ("P3", "TP", "9003/10", pb.id),
            ("P4", "TS", "9004/10", None), ("P5", "TS", "9005/10", None),
        ]:
            p = EscalaFiscalPosto(id=uuid4(), lado=lado, cod_jb=f"9{nome[1]}", lote="E2", ponto_final_id=ponto)
            p.linhas = [EscalaFiscalPostoLinha(linha=linha, ordem=1)]
            db.add(p)
            ids[nome] = p.id
        db.flush()
        for tipo, par, nome in [("sabado", "impar", "Sábado ímpar"), ("domingo", "impar", "Domingo ímpar"),
                                ("sabado", "par", "Sábado par"), ("domingo", "par", "Domingo par"),
                                ("util", None, "Útil")]:
            m = EscalaFiscalModelo(id=uuid4(), tipo_dia=tipo, paridade=par, nome=nome)
            db.add(m)
            ids[nome] = m.id
        db.flush()
        # O mesmo gabarito em todos os modelos (o teste muda o que precisa).
        gabarito = [
            # (posto, ordem, período, início, término, situação, RE, garagem, marcador)
            ("P1", 1, 1, "05:00", "14:00", "escalado", "90001", None, None),
            ("P1", 1, 2, "14:00", "23:00", "escalado", "90002", None, None),
            ("P2", 2, 1, "05:00", "14:00", "escalado", "90003", None, None),
            ("P2", 2, 2, "14:00", "23:00", "descoberto", None, None, ""),
            ("P3", 3, 1, None, None, "outra_garagem", None, "G1", "G1"),
            ("P3", 3, 2, None, None, "direto", None, None, "DIRETO"),
            ("P4", 4, 1, None, None, "descoberto", None, None, "****"),
            ("P4", 4, 2, None, None, "descoberto", None, None, "xxx"),
            ("P5", 5, 1, "05:00", "14:00", "descoberto", None, None, "-"),
            ("P5", 5, 2, "12:00", "20:20", "escalado", "90004", None, None),
        ]
        for modelo in ("Sábado ímpar", "Domingo ímpar", "Sábado par", "Domingo par", "Útil"):
            for posto, ordem, per, ini, fim, sit, re_, gar, marc in gabarito:
                db.add(EscalaFiscalModeloPosto(
                    modelo_id=ids[modelo], posto_id=ids[posto], ordem=ordem, periodo=per,
                    hora_inicio=_hm(ini), hora_termino=_hm(fim), situacao_padrao=sit, re_padrao=re_,
                    outra_garagem=gar, marcador=marc,
                ))
        db.add(EscalaFiscalCoordenadorPeriodo(funcionario_id=_COORD1.id, periodo=1))
        db.add(EscalaFiscalCoordenadorPeriodo(funcionario_id=_COORD2.id, periodo=2))
        db.add(EscalaFiscalCoordenadorHorario(funcionario_id=_COORD1.id, turno="manha",
                                              hora_inicio=time(4), hora_fim=time(12)))
        db.add(EscalaFiscalCoordenadorHorario(funcionario_id=_COORD2.id, turno="tarde",
                                              hora_inicio=time(15), hora_fim=time(2, 30)))
        db.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    estado = {"usuario": _ADMIN, "admin": True}
    leitura_dep = _dependency_de(router_mod.LeituraEscala)
    escrita_dep = _dependency_de(router_mod.EscritaEscala)
    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[leitura_dep] = lambda: estado["usuario"]
    app.dependency_overrides[escrita_dep] = lambda: estado["usuario"]
    monkeypatch.setattr(router_mod, "eh_admin", lambda db, fid: estado["admin"] and fid == _ADMIN.id)

    def como(pessoa):
        estado["usuario"] = pessoa

    def add(*objs):
        with Session(engine) as db:
            db.add_all(objs)
            db.commit()

    yield {"http": TestClient(app), "engine": engine, "ids": ids, "como": como, "add": add}

    for dep in (get_db, leitura_dep, escrita_dep):
        app.dependency_overrides.pop(dep, None)


def _quadro(re_, periodo, folga):
    return EscalaFiscalQuadro(re=re_, periodo=periodo, folga_base=folga, ativo=True)


def _payload(resp: dict) -> dict:
    alocs = []
    for linha in resp["linhas"]:
        for p in (linha["p1"], linha["p2"]):
            if p is None:
                continue
            alocs.append({
                "posto_id": p["posto_id"], "periodo": p["periodo"], "re": p["re"],
                "marcador": None if p["re"] else p["marcador"],
                "hora_inicio": p["hora_inicio"], "hora_termino": p["hora_termino"],
            })
    return {"modelo_id": resp["modelo"]["id"], "alocacoes": alocs, "confirmacoes": []}


def _cel(payload: dict, posto_id, periodo) -> dict:
    return next(a for a in payload["alocacoes"] if a["posto_id"] == str(posto_id) and a["periodo"] == periodo)


def _linha(resp: dict, posto_id, periodo) -> dict:
    for linha in resp["linhas"]:
        p = linha[f"p{periodo}"]
        if p and p["posto_id"] == str(posto_id):
            return p
    raise AssertionError("linha não encontrada")


def _abrir_e_salvar(http, d: date, mudar: dict | None = None) -> dict:
    resp = http.get(f"/escala-fiscais/dias/{d}").json()
    corpo = _payload(resp)
    for (posto, periodo), campos in (mudar or {}).items():
        _cel(corpo, posto, periodo).update(campos)
    r = http.put(f"/escala-fiscais/dias/{d}", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


# ─── RN01 — modelo pelo mês do próprio dia; virada de mês ────────────────────

def test_rn01_modelo_sugerido_pelo_mes_do_proprio_dia():
    assert regras.modelo_sugerido(SAB) == ("sabado", "impar")
    assert regras.modelo_sugerido(DOM) == ("domingo", "impar")
    assert regras.modelo_sugerido(date(2026, 10, 3)) == ("sabado", "par")
    assert regras.modelo_sugerido(SEG) == ("util", None)


def test_rn01_previa_de_dia_novo_usa_o_modelo_do_mes_e_nao_grava(amb):
    http = amb["http"]
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    assert resp["status"] == "novo" and resp["modelo"]["nome"] == "Sábado ímpar"
    assert http.get("/escala-fiscais/dias/2026-10-03").json()["modelo"]["nome"] == "Sábado par"
    assert http.get(f"/escala-fiscais/dias/{SEG}").json()["modelo"]["nome"] == "Útil"
    with Session(amb["engine"]) as db:
        assert db.execute(select(EscalaFiscalDia)).first() is None


def test_rn01_coordenador_troca_o_modelo_antes_de_salvar(amb):
    http, ids = amb["http"], amb["ids"]
    resp = http.get(f"/escala-fiscais/dias/{SAB}?modelo_id={ids['Sábado par']}").json()
    assert resp["modelo"]["nome"] == "Sábado par" and resp["modelo_sugerido"]["nome"] == "Sábado ímpar"
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=_payload(resp))
    assert r.status_code == 200 and r.json()["modelo"]["nome"] == "Sábado par"


def test_rn01_virada_de_mes_avisa_no_sabado_e_no_domingo(amb):
    http = amb["http"]
    for d in ("2026-10-31", "2026-11-01"):
        resp = http.get(f"/escala-fiscais/dias/{d}").json()
        assert resp["virada_mes"] is not None
        assert "Virada de mês" in resp["virada_mes"]["mensagem"]
        assert any(a["codigo"] == "virada_mes" for a in resp["validacao"]["avisos"])
    # 31/10 é outubro (par) e 01/11 é novembro (ímpar): cada dia com o seu modelo.
    assert http.get("/escala-fiscais/dias/2026-10-31").json()["modelo"]["nome"] == "Sábado par"
    assert http.get("/escala-fiscais/dias/2026-11-01").json()["modelo"]["nome"] == "Domingo ímpar"
    assert http.get(f"/escala-fiscais/dias/{SAB}").json()["virada_mes"] is None


# ─── RN02 — folga base, inversão no mês par, folga não confirmada ────────────

def test_rn02_folga_base_vale_no_mes_impar_e_inverte_no_par():
    quadro = {"90001": regras.FiscalQuadro("90001", 1, "sabado")}
    assert regras.folga_no_dia("90001", SAB, quadro, []) == (True, "base")
    assert regras.folga_no_dia("90001", DOM, quadro, []) == (False, "base")
    assert regras.folga_no_dia("90001", date(2026, 10, 3), quadro, []) == (False, "base")
    assert regras.folga_no_dia("90001", date(2026, 10, 4), quadro, []) == (True, "base")
    assert regras.folga_no_dia("90001", SEG, quadro, []) == (False, "util")


def test_rn02_sem_folga_base_nao_calcula_dobra_e_avisa(amb):
    amb["add"](_quadro("90001", 1, None))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    linha = _linha(resp, amb["ids"]["P1"], 1)
    assert linha["dobra"] is False and linha["folga"] is None
    assert linha["motivo_folga"] == "folga_nao_confirmada"
    assert "90001" in resp["resumo"]["folga_nao_confirmada"]
    assert any(a["codigo"] == "folga_nao_confirmada" for a in resp["validacao"]["avisos"])


# ─── RN03 — dobra calculada, nunca gravada no rascunho, congelada ao publicar ─

def test_rn03_escalado_na_propria_folga_e_dobra_amarela(amb):
    amb["add"](_quadro("90001", 1, "sabado"), _quadro("90003", 1, "domingo"))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    assert _linha(resp, amb["ids"]["P1"], 1)["dobra"] is True
    assert _linha(resp, amb["ids"]["P2"], 1)["dobra"] is False
    assert [d["re"] for d in resp["resumo"]["dobras"]] == ["90001"]
    # No domingo quem dobra é o outro.
    resp_dom = amb["http"].get(f"/escala-fiscais/dias/{DOM}").json()
    assert [d["re"] for d in resp_dom["resumo"]["dobras"]] == ["90003"]


def test_rn03_dobra_nunca_e_gravada_no_rascunho_e_congela_ao_publicar(amb):
    http, engine = amb["http"], amb["engine"]
    amb["add"](_quadro("90001", 1, "sabado"))
    _abrir_e_salvar(http, SAB)
    with Session(engine) as db:
        assert all(a.dobra_publicada is None for a in db.execute(select(EscalaFiscalAlocacao)).scalars())
    r = http.post(f"/escala-fiscais/dias/{SAB}/publicar", json={})
    assert r.status_code == 200, r.text
    with Session(engine) as db:
        por_re = {a.re: a.dobra_publicada for a in db.execute(select(EscalaFiscalAlocacao)).scalars() if a.re}
        assert por_re["90001"] is True and por_re["90002"] is False
        # Depois de publicada, a folga base muda: o amarelo publicado NÃO muda.
        db.get(EscalaFiscalQuadro, "90001").folga_base = "domingo"
        db.commit()
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    assert _linha(resp, amb["ids"]["P1"], 1)["dobra"] is True


# ─── RN06/RN07 — trocas ──────────────────────────────────────────────────────

def test_rn06_troca_1x1_inverte_a_folga_dos_dois_naquele_fim_de_semana():
    quadro = {"90001": regras.FiscalQuadro("90001", 1, "sabado"), "90003": regras.FiscalQuadro("90003", 1, "domingo")}
    trocas = [regras.Troca("1x1", "90001", "90003", SAB)]
    assert regras.folga_no_dia("90001", SAB, quadro, trocas) == (False, "troca_1x1")
    assert regras.folga_no_dia("90001", DOM, quadro, trocas) == (True, "troca_1x1")
    assert regras.folga_no_dia("90003", SAB, quadro, trocas) == (True, "troca_1x1")
    # No fim de semana seguinte a 1x1 não vale mais (03/10: outubro é par,
    # então a folga base de sábado vira domingo).
    assert regras.folga_no_dia("90001", SAB + timedelta(days=7), quadro, trocas) == (False, "base")
    assert regras.folga_no_dia("90001", DOM + timedelta(days=7), quadro, trocas) == (True, "base")


def test_rn07_troca_2x2_e_invertida_no_fim_de_semana_seguinte():
    quadro = {"90001": regras.FiscalQuadro("90001", 1, "sabado"), "90003": regras.FiscalQuadro("90003", 1, "sabado")}
    trocas = [regras.Troca("2x2", "90001", "90003", SAB)]
    # Fim de semana da troca: A trabalha os dois dias, B folga os dois.
    assert [regras.folga_no_dia("90001", d, quadro, trocas)[0] for d in (SAB, DOM)] == [False, False]
    assert [regras.folga_no_dia("90003", d, quadro, trocas)[0] for d in (SAB, DOM)] == [True, True]
    # Seguinte: inverte.
    prox = (SAB + timedelta(days=7), DOM + timedelta(days=7))
    assert [regras.folga_no_dia("90001", d, quadro, trocas)[0] for d in prox] == [True, True]
    assert [regras.folga_no_dia("90003", d, quadro, trocas)[0] for d in prox] == [False, False]


def test_rn07_dia_trabalhado_por_troca_nao_e_dobra(amb):
    # 90001 folga no sábado pela base, mas a 2x2 o põe para trabalhar.
    amb["add"](_quadro("90001", 1, "sabado"), _quadro("90005", 1, "domingo"),
               EscalaFiscalTroca(tipo="2x2", re_a="90001", re_b="90005", data_sabado=SAB))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    assert _linha(resp, amb["ids"]["P1"], 1)["dobra"] is False
    assert "90005" in [f["re"] for f in resp["resumo"]["folgas"]["1"]]
    assert {"tipo": "2x2", "re_a": "90001", "re_b": "90005", "invertida": False} in resp["resumo"]["trocas"]


def test_ao_abrir_o_dia_a_troca_poe_o_parceiro_no_posto(amb):
    # Modelo tem 90001 no P1; a 2x2 manda 90001 folgar (é o re_b) e 90005 trabalhar.
    amb["add"](_quadro("90001", 1, "domingo"), _quadro("90005", 1, "domingo"),
               EscalaFiscalTroca(tipo="2x2", re_a="90005", re_b="90001", data_sabado=SAB))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    linha = _linha(resp, amb["ids"]["P1"], 1)
    assert linha["re"] == "90005" and "Troca 2x2" in linha["nota"]


# ─── RN04 — dobra em dois fins de semana seguidos: pergunta e registra ───────

def _publicar_sabado_anterior_com_dobra(amb):
    amb["add"](_quadro("90001", 1, "sabado"))
    _abrir_e_salvar(amb["http"], SAB_ANTERIOR)
    assert amb["http"].post(f"/escala-fiscais/dias/{SAB_ANTERIOR}/publicar", json={}).status_code == 200


def test_rn04_pergunta_e_grava_quem_confirmou_e_quando(amb):
    http, engine, ids = amb["http"], amb["engine"], amb["ids"]
    _publicar_sabado_anterior_com_dobra(amb)
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    [pergunta] = [p for p in resp["validacao"]["perguntas"] if p["codigo"] == "RN04"]
    assert pergunta["mensagem"] == (
        f"RE 90001 dobrou no sábado {SAB_ANTERIOR:%d/%m}. Deseja mesmo escalar de novo neste fim de semana?"
    )
    corpo = _payload(resp)
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 409, r.text  # [Cancelar] → nada gravado
    assert r.json()["erro"]["perguntas"][0]["chave"] == pergunta["chave"]
    with Session(engine) as db:
        assert db.execute(select(EscalaFiscalDia).where(EscalaFiscalDia.data == SAB)).first() is None

    corpo["confirmacoes"] = [pergunta["chave"]]  # [Sim, escalar]
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 200, r.text
    assert _linha(r.json(), ids["P1"], 1)["dobra_seguida_confirmada_por"] == "Admin Teste"
    with Session(engine) as db:
        a = db.execute(select(EscalaFiscalAlocacao).join(EscalaFiscalDia).where(
            EscalaFiscalDia.data == SAB, EscalaFiscalAlocacao.re == "90001")).scalar_one()
        assert a.dobra_seguida_confirmada_por == _ADMIN.id and a.dobra_seguida_confirmada_em is not None
    # Confirmação gravada: salvar de novo não pergunta outra vez.
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=_payload(r.json())).status_code == 200


def test_rn04_trocar_o_re_da_linha_apaga_a_confirmacao(amb):
    http, engine, ids = amb["http"], amb["engine"], amb["ids"]
    _publicar_sabado_anterior_com_dobra(amb)
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    corpo = _payload(resp)
    corpo["confirmacoes"] = [p["chave"] for p in resp["validacao"]["perguntas"]]
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P1"], 1).update(re="90007")
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200
    with Session(engine) as db:
        a = db.execute(select(EscalaFiscalAlocacao).join(EscalaFiscalDia).where(
            EscalaFiscalDia.data == SAB, EscalaFiscalAlocacao.periodo == 1,
            EscalaFiscalAlocacao.posto_id == ids["P1"])).scalar_one()
        assert a.re == "90007" and a.dobra_seguida_confirmada_por is None


def test_rn04_sem_dobra_no_fim_de_semana_anterior_nao_pergunta(amb):
    amb["add"](_quadro("90001", 1, "sabado"))
    _abrir_e_salvar(amb["http"], SAB)  # nenhum 409


# ─── RN05 — ausência bloqueia ────────────────────────────────────────────────

def test_rn05_ferias_bloqueia_com_a_mensagem_e_data_fim_inclusiva(amb):
    http, ids = amb["http"], amb["ids"]
    amb["add"](EscalaFiscalAusencia(re="90007", tipo="ferias", data_inicio=date(2026, 9, 14), data_fim=SAB))
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P2"], 2).update(re="90007", marcador=None)
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 422
    assert "RE 90007 está de férias de 14/09 a 26/09" in r.json()["erro"]["mensagem"]
    # Data fim inclusiva: no domingo ele já pode.
    corpo = _payload(http.get(f"/escala-fiscais/dias/{DOM}").json())
    _cel(corpo, ids["P2"], 2).update(re="90007", marcador=None)
    assert http.put(f"/escala-fiscais/dias/{DOM}", json=corpo).status_code == 200


def test_rn05_ausente_sai_da_previa_e_o_posto_fica_em_branco_com_a_nota(amb):
    amb["add"](EscalaFiscalAusencia(re="90003", tipo="atestado", data_inicio=SAB, data_fim=SAB))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    linha = _linha(resp, amb["ids"]["P2"], 1)
    assert linha["situacao"] == "descoberto" and linha["re"] is None and linha["marcador"] == ""
    assert linha["nota"] == "RE 90003 está de atestado em 26/09: posto em branco"
    assert [x["re"] for x in resp["resumo"]["ausentes"]["atestado"]["0"]] == ["90003"]
    # Depois de salvo a nota continua (é lida, não gravada).
    salvo = _abrir_e_salvar(amb["http"], SAB)
    assert _linha(salvo, amb["ids"]["P2"], 1)["nota"].startswith("RE 90003 está de atestado")


# ─── RN08 — acúmulo ──────────────────────────────────────────────────────────

def test_rn08_mesmo_ponto_final_permite_e_marca_acumulo(amb):
    ids = amb["ids"]
    salvo = _abrir_e_salvar(amb["http"], SAB, {(ids["P2"], 2): {"re": "90002", "marcador": None}})
    assert _linha(salvo, ids["P1"], 2)["acumulo"] and _linha(salvo, ids["P2"], 2)["acumulo"]
    assert salvo["resumo"]["acumulos"][0]["ponto_final"] == "Ponto Ficticio A"


def test_rn08_pontos_finais_diferentes_no_mesmo_horario_bloqueia(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P3"], 1).update(re="90001", marcador=None, hora_inicio="06:00", hora_termino="12:00")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 422
    assert "RE 90001 já está no posto TP 91 · 9001/10, ponto final Ponto Ficticio A, nesse horário" in r.json()["erro"]["mensagem"]


def test_rn08_horario_sem_sobreposicao_nao_e_acumulo(amb):
    ids = amb["ids"]
    salvo = _abrir_e_salvar(amb["http"], SAB, {
        (ids["P3"], 1): {"re": "90001", "marcador": None, "hora_inicio": "14:30", "hora_termino": "20:00"},
        (ids["P1"], 1): {"hora_termino": "14:00"},
    })
    assert not _linha(salvo, ids["P3"], 1)["acumulo"]


def test_rn08_posto_sem_ponto_final_pergunta_antes_de_acumular(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    # 90004 já está no P5 (12:00–20:20, sem ponto final); P4 no mesmo horário.
    _cel(corpo, ids["P4"], 2).update(re="90004", marcador=None, hora_inicio="14:00", hora_termino="23:00")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 409
    [pergunta] = r.json()["erro"]["perguntas"]
    assert pergunta["codigo"] == "RN08" and "Confirmar acúmulo?" in pergunta["mensagem"]
    corpo["confirmacoes"] = [pergunta["chave"]]
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 200 and _linha(r.json(), ids["P4"], 2)["acumulo"]
    # Já gravado: não pergunta de novo.
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=_payload(r.json())).status_code == 200


# ─── RN10 — coordenador edita só o período dele ─────────────────────────────

def test_rn10_coordenador_do_1_periodo_nao_mexe_no_2(amb):
    http, ids = amb["http"], amb["ids"]
    amb["como"](_COORD1)
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    assert resp["periodos_editaveis"] == [1]
    corpo = _payload(resp)
    _cel(corpo, ids["P2"], 2).update(re="90008", marcador=None)
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 403
    assert "Você edita só o 1º período. Mudança no 2º período" in r.json()["erro"]
    # O período dele passa (o 2º igual ao modelo não conta como mudança).
    corpo = _payload(resp)
    _cel(corpo, ids["P2"], 1).update(re="90008")
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200
    # O coordenador do 2º período vê tudo e edita o 2º.
    amb["como"](_COORD2)
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P2"], 2).update(re="90009", marcador=None)
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200
    _cel(corpo, ids["P2"], 1).update(re="90010")
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 403


def test_rn10_admin_edita_os_dois_periodos(amb):
    ids = amb["ids"]
    salvo = _abrir_e_salvar(amb["http"], SAB, {(ids["P2"], 1): {"re": "90008"},
                                                 (ids["P2"], 2): {"re": "90009", "marcador": None}})
    assert salvo["periodos_editaveis"] == [1, 2]


# ─── RN11 — mesmo RE no 1º e no 2º período ──────────────────────────────────

def test_rn11_mesmo_re_nos_dois_periodos_pergunta(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P5"], 1).update(re="90002", marcador=None, hora_inicio="05:00", hora_termino="11:00")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 409
    [pergunta] = r.json()["erro"]["perguntas"]
    assert pergunta["codigo"] == "RN11"
    assert pergunta["mensagem"] == "RE 90002 está no 1º e no 2º período do mesmo dia. Confirmar?"
    corpo["confirmacoes"] = [pergunta["chave"]]
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200


# ─── RN12 — sumidos ──────────────────────────────────────────────────────────

def test_rn12_fiscal_do_quadro_sem_posto_folga_nem_ausencia_e_sumido(amb):
    amb["add"](_quadro("90011", 1, "domingo"), _quadro("90012", 1, "sabado"),
               _quadro("90013", 2, "domingo"),
               EscalaFiscalAusencia(re="90013", tipo="afastado", data_inicio=date(2026, 9, 1), data_fim=None))
    resp = amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()
    # 90011 trabalha no sábado e não está em posto: sumido. 90012 folga. 90013 afastado.
    assert [s["re"] for s in resp["resumo"]["sumidos"]] == ["90011"]
    assert any(a["codigo"] == "RN12" and "90011" in a["mensagem"] for a in resp["validacao"]["avisos"])
    assert [f["re"] for f in resp["resumo"]["folgas"]["1"]] == ["90012"]
    assert [x["re"] for x in resp["resumo"]["ausentes"]["afastado"]["2"]] == ["90013"]


# ─── RN13 — situações e marcador preservado ─────────────────────────────────

def test_rn13_marcadores_sao_preservados_como_escritos(amb):
    ids = amb["ids"]
    salvo = _abrir_e_salvar(amb["http"], SAB)
    esperado = {
        (ids["P3"], 1): ("outra_garagem", "G1"), (ids["P3"], 2): ("direto", "DIRETO"),
        (ids["P4"], 1): ("descoberto", "****"), (ids["P4"], 2): ("descoberto", "xxx"),
        (ids["P5"], 1): ("descoberto", "-"), (ids["P2"], 2): ("descoberto", ""),
    }
    for (posto, per), (sit, marc) in esperado.items():
        linha = _linha(salvo, posto, per)
        assert (linha["situacao"], linha["marcador"]) == (sit, marc)
    assert _linha(salvo, ids["P3"], 1)["hora_inicio"] is None  # sem horário, como no Excel
    descobertos = {(d["posto_id"], d["periodo"]) for d in salvo["resumo"]["descobertos"]}
    assert (str(ids["P4"]), 1) in descobertos and (str(ids["P2"]), 2) in descobertos


def test_marcador_desconhecido_e_recusado(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P4"], 1).update(marcador="FOLGOU")
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 422


# ─── Bloqueios que sobram: G3, RE vazio em escalado, término antes do início ─

def test_g3_bloqueia(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P3"], 1).update(marcador="G3")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 422 and "G3 é a própria garagem" in r.json()["erro"]["mensagem"]


def test_termino_antes_do_inicio_bloqueia(amb):
    http, ids = amb["http"], amb["ids"]
    corpo = _payload(http.get(f"/escala-fiscais/dias/{SAB}").json())
    _cel(corpo, ids["P1"], 2).update(hora_inicio="15:00", hora_termino="02:30")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 422 and "não passa da meia-noite" in r.json()["erro"]["mensagem"]


def test_re_vazio_em_escalado_bloqueia():
    posto = uuid4()
    ctx = regras.Contexto(data=SAB, postos={}, quadro={}, ausencias=[], trocas=[],
                          padroes={1: (time(5), time(14)), 2: (time(14), time(23))})
    av = regras.avaliar([regras.Aloc(posto_id=posto, periodo=1, situacao="escalado", re=None)], ctx)
    assert [b["codigo"] for b in av.bloqueios] == ["re_vazio"]


def test_so_bloqueiam_as_regras_combinadas():
    fonte = inspect.getsource(regras.avaliar)
    codigos = {c for c in ("re_vazio", "g3", "horario", "RN05", "RN08") if f'"codigo": "{c}"' in fonte}
    assert codigos == {"re_vazio", "g3", "horario", "RN05", "RN08"}
    for pergunta in ("RN04", "RN11"):
        assert f'"codigo": "{pergunta}", "chave"' in fonte


# ─── RN14 — rascunho, publicada, nova versão com registro ───────────────────

def test_rn14_alterar_publicada_gera_nova_versao_e_registro(amb):
    http, engine, ids = amb["http"], amb["engine"], amb["ids"]
    salvo = _abrir_e_salvar(http, SAB)
    assert salvo["status"] == "rascunho" and salvo["versao"] == 1
    publicada = http.post(f"/escala-fiscais/dias/{SAB}/publicar", json={}).json()
    assert publicada["status"] == "publicada" and publicada["versao"] == 1
    assert publicada["publicada_por"] == "Admin Teste"
    assert http.post(f"/escala-fiscais/dias/{SAB}/publicar", json={}).status_code == 409

    corpo = _payload(publicada)
    _cel(corpo, ids["P1"], 1).update(re="90021")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 200 and r.json()["versao"] == 2 and r.json()["status"] == "publicada"
    [alt] = http.get(f"/escala-fiscais/dias/{SAB}/alteracoes").json()
    assert alt["versao"] == 2 and alt["alterado_por"] == "Admin Teste"
    assert alt["antes"]["alocacoes"][0]["re"] == "90001"
    assert alt["depois"]["alocacoes"][0]["re"] == "90021"
    with Session(engine) as db:
        assert db.execute(select(EscalaFiscalAlteracao)).scalar_one().alterado_por == _ADMIN.id
        # A linha nova da publicada já nasce com a dobra congelada.
        nova = db.execute(select(EscalaFiscalAlocacao).where(EscalaFiscalAlocacao.re == "90021")).scalar_one()
        assert nova.dobra_publicada is False
    # Salvar sem mudar nada não cria versão.
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=_payload(r.json())).json()["versao"] == 2
    assert http.delete(f"/escala-fiscais/dias/{SAB}").status_code == 409


def test_rascunho_pode_ser_descartado_e_apaga_tudo(amb):
    http, engine = amb["http"], amb["engine"]
    _abrir_e_salvar(http, SAB)
    assert http.delete(f"/escala-fiscais/dias/{SAB}").status_code == 204
    with Session(engine) as db:
        assert db.execute(select(EscalaFiscalAlocacao)).first() is None
        assert db.execute(select(EscalaFiscalPlantao)).first() is None


# ─── Salvar de novo não colide na UNIQUE (dia, posto, período) ──────────────

def test_salvar_varias_vezes_trocando_e_removendo_linhas_nao_colide(amb):
    http, ids = amb["http"], amb["ids"]
    salvo = _abrir_e_salvar(http, SAB)
    corpo = _payload(salvo)
    corpo["alocacoes"] = [a for a in corpo["alocacoes"] if a["posto_id"] != str(ids["P4"])]
    _cel(corpo, ids["P1"], 1).update(re="90031")
    r = http.put(f"/escala-fiscais/dias/{SAB}", json=corpo)
    assert r.status_code == 200, r.text
    corpo = _payload(r.json())
    corpo["alocacoes"].append({"posto_id": str(ids["P4"]), "periodo": 1, "re": None, "marcador": "****",
                               "hora_inicio": None, "hora_termino": None})
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 200
    with Session(amb["engine"]) as db:
        assert len(db.execute(select(EscalaFiscalAlocacao)).all()) == 9


# ─── Plantão: copiado do cadastro, ajustável só no dia ──────────────────────

def test_plantao_nasce_do_cadastro_e_o_ajuste_e_so_do_dia(amb):
    http, engine = amb["http"], amb["engine"]
    salvo = _abrir_e_salvar(http, SAB)
    assert [(p["re"], p["hora_inicio"], p["hora_fim"], p["passa_meia_noite"]) for p in salvo["plantao"]] == [
        ("80001", "04:00", "12:00", False), ("80002", "15:00", "02:30", True),
    ]
    corpo = _payload(salvo)
    corpo["plantao"] = [{"turno": "manha", "re": "80001", "hora_inicio": "04:00", "hora_fim": "12:00"},
                        {"turno": "tarde", "re": "80002", "hora_inicio": "15:00", "hora_fim": "04:00"}]
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).json()["plantao"][1]["hora_fim"] == "04:00"
    with Session(engine) as db:
        cadastro = db.execute(select(EscalaFiscalCoordenadorHorario).where(
            EscalaFiscalCoordenadorHorario.turno == "tarde")).scalar_one()
        assert cadastro.hora_fim == time(2, 30)
    assert http.get(f"/escala-fiscais/dias/{DOM}").json()["plantao"][1]["hora_fim"] == "02:30"
    corpo["plantao"] = [{"turno": "tarde", "re": "99999", "hora_inicio": "15:00", "hora_fim": "23:00"}]
    assert http.put(f"/escala-fiscais/dias/{SAB}", json=corpo).status_code == 422  # coordenador sem cadastro


# ─── Títulos da impressão ───────────────────────────────────────────────────

def test_titulos_iguais_aos_da_planilha(amb):
    http = amb["http"]
    resp = http.get(f"/escala-fiscais/dias/{SAB}").json()
    assert resp["titulo_tp"] == "ESCALA SABADO 26 / 09 / 2026 - TP"
    assert resp["titulo_ts"] == "ESCALA SABADO 26 / 09 / 2026 - TS"
    assert http.get(f"/escala-fiscais/dias/{DOM}").json()["titulo_tp"] == "ESCALA DOMINGO 27 / 09 / 2026 - TP"
    assert http.get(f"/escala-fiscais/dias/{SEG}").json()["titulo_ts"] == (
        "ESCALA MENSAL DIAS UTIL A PARTIR DE 28/09/2026 - TS"
    )


# ─── Data de calendário com o relógio em UTC ────────────────────────────────

class _RelogioUTC(datetime):
    """00:30 de domingo em UTC = 21:30 de sábado em São Paulo."""

    @classmethod
    def now(cls, tz=None):
        instante = datetime(2026, 9, 27, 0, 30, tzinfo=UTC)
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


def test_hoje_e_sabado_as_21h30_em_sao_paulo_mesmo_com_utc_ja_no_domingo(amb, monkeypatch):
    monkeypatch.setattr(router_mod, "datetime", _RelogioUTC)
    resp = amb["http"].get("/escala-fiscais/dias").json()
    assert resp["hoje"] == "2026-09-26"
    assert amb["http"].get(f"/escala-fiscais/dias/{SAB}").json()["hoje"] == "2026-09-26"


def test_modulos_novos_nao_usam_date_today_nem_data_utc():
    for modulo in (montagem, regras):
        fonte = inspect.getsource(modulo)
        assert "date.today()" not in fonte
        assert "timezone.utc" not in fonte
