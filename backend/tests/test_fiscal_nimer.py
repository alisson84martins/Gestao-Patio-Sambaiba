"""v4.0 fase 3, bloco A — fiscal identificado pelo NIMER.

O comportamento ERRADO que barramos:
- hora do NIMER lida como UTC (o servidor roda em UTC): a pegada das 22:00
  de São Paulo virava 22:00 UTC (19:00 SP) e a batida das 00:10 caía no
  mesmo dia da pegada das 23:00;
- fiscal com a batida de início com foto e a tela dizendo "Abra o turno";
- fiscal escalado e ainda sem foto vendo a linha como se tivesse turno (pode
  corrigir viagem) — tem que ser só `previsto`;
- turno que o fiscal fechou sendo reaberto sozinho.
⛔ Sem dado pessoal real: REs fictícios (9xxxx).
"""
import os
import time as relogio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import NimerFiscalEscala
from app.routers import monitoramento as router_mod
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_fiscais as fiscais
from tests.ambiente_monitoramento import criar_engine

TOKEN = "token-de-teste-do-robo"
DIA = date(2026, 11, 11)
LOCAL = "metro santana 178t/10 - 1726/10 - 1764/10 - 2025/10 - 1783/10"
FISCAL = Funcionario(id=uuid4(), re="96867", nome="Fiscal de teste")


@pytest.fixture(autouse=True)
def processo_em_utc(monkeypatch):
    """O servidor de produção roda em UTC: nada aqui pode depender do relógio local."""
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(relogio, "tzset"):
        relogio.tzset()
    yield
    if hasattr(relogio, "tzset"):
        os.environ.pop("TZ", None)
        relogio.tzset()


def sp(hh, mm, dia=DIA):
    """Hora de parede de São Paulo (UTC−3) → instante UTC."""
    return datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=timezone.utc) + timedelta(hours=3)


# ─── Fuso ────────────────────────────────────────────────────────────────────

def test_pegada_22h_e_inicio_22h05_de_sao_paulo_com_processo_em_utc():
    cartao = fiscais.cartao_da_escala(
        {"re": "96867", "tabela": "176259", "pegada": "11/11/2026 22:00", "largada": "12/11/2026 06:00",
         "local": LOCAL, "inicio": "22:05", "fim": ""}, DIA)
    assert cartao["pegada_em"] == datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
    assert cartao["inicio_em"] == datetime(2026, 11, 12, 1, 5, tzinfo=timezone.utc)
    assert cartao["largada_em"] == datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
    assert cartao["fim_em"] is None


def test_inicio_00h10_com_pegada_23h_e_do_dia_seguinte():
    cartao = fiscais.cartao_da_escala(
        {"re": "96867", "tabela": "1", "pegada": "11/11/2026 23:00", "inicio": "00:10"}, DIA)
    assert cartao["inicio_em"] == sp(0, 10, date(2026, 11, 12))


def test_fim_depois_da_meia_noite_com_pegada_a_tarde_e_do_dia_seguinte():
    cartao = fiscais.cartao_da_escala(
        {"re": "96867", "tabela": "1", "pegada": "11/11/2026 14:00", "inicio": "14:13", "fim": "00:30"}, DIA)
    assert cartao["inicio_em"] == sp(14, 13)
    assert cartao["fim_em"] == sp(0, 30, date(2026, 11, 12))


def test_pegada_sem_ano_usa_o_ano_da_data_de_servico():
    assert fiscais.parede_para_instante("11/11 14:00", 2026) == sp(14, 0)


def test_local_casa_pelo_comeco_normalizado():
    eng = criar_engine()
    with Session(eng) as db:
        pontos = fiscais.pontos_do_local(db, "  Metrô   SANTANA 178t/10 - 1726/10")
        assert ("1726-10", "TS") in pontos and ("178T-10", "TS") in pontos and len(pontos) == 5
        assert fiscais.pontos_do_local(db, "Cem. Parque dos Pinheiros - 1726/10") == [("1726-10", "TP")]
        assert fiscais.pontos_do_local(db, "Lugar que não existe") == []
    assert fiscais.nome_do_local(LOCAL) == "metro santana"


# ─── Turno automático ────────────────────────────────────────────────────────

@pytest.fixture
def eng(monkeypatch):
    eng = criar_engine()
    with Session(eng) as db:
        db.add(Funcionario(id=FISCAL.id, re=FISCAL.re, nome=FISCAL.nome, status="ATIVO"))
        db.commit()
    # SQLite não tem vw_acesso_efetivo: o fiscal tem só `fiscalizacao`.
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False: False)
    return eng


def _escala(db, *, inicio=None, fim=None, pegada=(14, 0), local=LOCAL, re="96867"):
    db.add(NimerFiscalEscala(
        data_servico=DIA, re=re, tabela="176259", pegada_em=sp(*pegada), largada_em=sp(23, 0), local_texto=local,
        inicio_em=sp(*inicio) if inicio else None, fim_em=sp(*fim) if fim else None))
    db.commit()


def test_com_batida_de_inicio_abre_o_turno_nimer_com_as_linhas_do_ponto(eng):
    with Session(eng) as db:
        _escala(db, inicio=(14, 13))
        esc = acesso.escopo(db, FISCAL.id, agora=sp(14, 20))
        assert esc["tipo"] == "turno"
        assert esc["linhas"] == ["1726-10", "1764-10", "1783-10", "178T-10", "2025-10"]
        turno = esc["turno"]
        assert turno.origem == "NIMER" and turno.terminal == "TS" and turno.periodo == "2"
        assert turno.data_referencia == DIA and turno.fiscal_re == "96867"
    with Session(eng) as db:  # gravado de verdade; a 2ª consulta reaproveita o mesmo turno
        assert len(db.execute(select(Turno)).scalars().all()) == 1
        assert len(db.execute(select(TurnoLinha)).scalars().all()) == 5
        assert acesso.escopo(db, FISCAL.id, agora=sp(14, 30))["turno"].id == turno.id
        assert len(db.execute(select(Turno)).scalars().all()) == 1


def test_sem_inicio_e_pegada_daqui_a_20_min_e_previsto_so_leitura(eng):
    with Session(eng) as db:
        _escala(db)
        esc = acesso.escopo(db, FISCAL.id, agora=sp(13, 40))
        assert esc["tipo"] == "previsto" and esc["turno"] is None
        assert "1726-10" in esc["linhas"]
        assert esc["previsto"]["texto"] == "Previsto: metro santana às 14:00 · aguardando foto de início no NIMER"
        assert db.execute(select(Turno)).first() is None


def test_sem_inicio_e_pegada_daqui_a_1h_ainda_e_sem_turno(eng):
    with Session(eng) as db:
        _escala(db)
        assert acesso.escopo(db, FISCAL.id, agora=sp(13, 0))["tipo"] == "sem_turno"


def test_sem_nada_do_nimer_e_sem_turno_fluxo_manual(eng):
    with Session(eng) as db:
        _escala(db, inicio=(14, 13), re="90000")  # outro fiscal
        assert acesso.escopo(db, FISCAL.id, agora=sp(14, 20)) == {"tipo": "sem_turno", "linhas": [], "turno": None}


def test_com_batida_de_fim_nao_abre(eng):
    with Session(eng) as db:
        _escala(db, inicio=(14, 13), fim=(23, 18))
        assert acesso.escopo(db, FISCAL.id, agora=sp(23, 20))["tipo"] == "sem_turno"


def test_turno_fechado_pelo_fiscal_nao_reabre_no_mesmo_dia(eng):
    with Session(eng) as db:
        _escala(db, inicio=(14, 13))
        turno = acesso.escopo(db, FISCAL.id, agora=sp(14, 20))["turno"]
        turno.status = "FECHADO"
        db.commit()
        assert acesso.escopo(db, FISCAL.id, agora=sp(15, 0))["tipo"] == "sem_turno"


def test_local_sem_cadastro_nao_abre_turno(eng):
    with Session(eng) as db:
        _escala(db, inicio=(14, 13), local="ponto novo 9999/10")
        assert acesso.escopo(db, FISCAL.id, agora=sp(14, 20))["tipo"] == "sem_turno"


def test_re_com_letra_compara_como_texto(eng):
    with Session(eng) as db:
        outro = Funcionario(id=uuid4(), re="A1234", nome="Fiscal com letra", status="ATIVO")
        db.add(outro)
        db.commit()
        _escala(db, inicio=(6, 5), pegada=(6, 0), re="A1234")
        esc = acesso.escopo(db, outro.id, agora=sp(6, 10))
        assert esc["tipo"] == "turno" and esc["turno"].periodo == "1"


# ─── Recebimento ─────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(eng, monkeypatch):
    monkeypatch.setattr(router_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"monitor_ingest_token": TOKEN}))

    def _db():
        with Session(eng) as db:
            yield db
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _corpo(inicio="14:13"):
    return {"data_servico": "2026-11-11", "coletado_em": 1731340800000,
            "fiscais": [{"re": "96867", "tabela": "176259", "pegada": "11/11/2026 14:00",
                         "largada": "11/11/2026 23:00", "local": LOCAL, "inicio": inicio, "fim": ""}]}


def test_recebe_a_escala_e_faz_upsert(cliente, eng):
    h = {"X-Monitor-Token": TOKEN}
    r = cliente.post("/monitoramento/nimer/fiscais", json=_corpo(inicio=""), headers=h)
    assert r.status_code == 200 and r.json()["gravados"] == 1
    r = cliente.post("/monitoramento/nimer/fiscais", json=_corpo(), headers=h)
    assert r.status_code == 200
    with Session(eng) as db:
        (linha,) = db.execute(select(NimerFiscalEscala)).scalars().all()
        assert linha.inicio_em.replace(tzinfo=timezone.utc) == sp(14, 13)
        assert linha.data_servico == DIA and linha.local_texto == LOCAL


def test_recebimento_sem_token_401(cliente):
    assert cliente.post("/monitoramento/nimer/fiscais", json=_corpo()).status_code == 401
