"""v4.0 fase 3, bloco F — Central "Ao vivo · minhas linhas" (T11).

O comportamento ERRADO que barramos:
- coordenador vendo ao vivo linha que ele não marcou em "Minhas linhas"
  (fiscalizacao.linha_coordenador) — ou um cadastro novo paralelo;
- cartão sem dizer quem está no ponto (e se a foto do NIMER chegou);
- cartão com alerta aberto perdido no meio dos outros (mais alertas primeiro).
⛔ Sem dado pessoal real.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.fiscalizacao import LinhaCoordenador, Turno, TurnoLinha
from app.models.monitoramento import LinhaMonitoradaCadastro, NimerFiscalEscala
from app.routers import monitoramento as router_mod
from app.services import fiscalizacao_central as central
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.monitoramento_cadastro import data_servico_de
from tests.ambiente_monitoramento import criar_engine

COORD = Funcionario(id=uuid4(), re="99001", nome="Coordenação 1726")
SEM_LINHA = Funcionario(id=uuid4(), re="99002", nome="Coordenação sem linha")
ADMIN = Funcionario(id=uuid4(), re="99003", nome="Admin")
FISCAL = Funcionario(id=uuid4(), re="99004", nome="Fiscal")


@pytest.fixture
def cli():
    eng = criar_engine()
    agora = datetime.now(timezone.utc)
    hoje: date = data_servico_de(agora)
    with Session(eng) as db:
        db.add(LinhaMonitoradaCadastro(linha_codigo="8012-10", ativo=True))
        for f in (COORD, SEM_LINHA, ADMIN, FISCAL):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        db.add(LinhaCoordenador(id=uuid4(), linha_codigo="1726-10", funcionario_id=COORD.id, periodo="2", ativo=True))
        t = Turno(id=uuid4(), funcionario_id=FISCAL.id, fiscal_re=FISCAL.re, terminal="TS", periodo="2",
                  data_referencia=hoje, status="ABERTO", aberto_em=agora, origem="NIMER")
        db.add(t)
        db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo="1726-10"))
        db.add(NimerFiscalEscala(data_servico=hoje, re=FISCAL.re, tabela="1", pegada_em=agora - timedelta(hours=1),
                                 inicio_em=agora - timedelta(minutes=47), local_texto="metro santana"))
        funcao = Funcao(id=uuid4(), codigo="ADMIN", nome="Administrador", categoria="SISTEMA", nivel=1)
        db.add(funcao)
        db.flush()
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=ADMIN.id, funcao_id=funcao.id, principal=True,
                                 data_inicio=hoje, ativo=True))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    estado = {"u": COORD}
    central.limpar_cache()
    ao_vivo.limpar_cache()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[router_mod.LeituraPainel.__metadata__[0].dependency] = lambda: estado["u"]
    cliente = TestClient(app)
    cliente.como = lambda f: (estado.update(u=f), central.limpar_cache())
    try:
        yield cliente
    finally:
        app.dependency_overrides.clear()
        central.limpar_cache()
        ao_vivo.limpar_cache()


URL = "/monitoramento/central/minhas-linhas"


def test_coordenador_ve_so_as_linhas_que_marcou(cli):
    r = cli.get(URL)
    assert r.status_code == 200, r.text
    j = r.json()
    assert [c["linha_codigo"] for c in j["cartoes"]] == ["1726-10"]
    (c,) = j["cartoes"]
    assert set(c) >= {"icv", "farol", "por_sentido", "a_conferir", "alertas_abertos", "tem_alerta", "fiscais"}
    (f,) = c["fiscais"]
    assert f["re"] == "99004" and f["terminal"] == "TS" and f["selo"].startswith("NIMER ✓ ")
    assert set(j["robo"]) == {"saude", "alarmes_falsos"}


def test_coordenador_sem_linha_marcada_nao_ve_nenhuma(cli):
    cli.como(SEM_LINHA)
    assert cli.get(URL).json()["cartoes"] == []


def test_admin_ve_todas_as_monitoradas(cli):
    cli.como(ADMIN)
    linhas = [c["linha_codigo"] for c in cli.get(URL).json()["cartoes"]]
    assert sorted(linhas) == ["1726-10", "8012-10"]


def test_cartao_com_mais_alertas_vem_primeiro(monkeypatch):
    linhas = [SimpleNamespace(codigo=c, nome=c, nimer_line_id=None) for c in ("A", "B", "C")]
    abertos = {"A": 0, "B": 2, "C": 1}
    vazio = {"total": {"icv": None, "a_conferir": 0}, "por_sentido": {}}
    monkeypatch.setattr(central.tempo_real, "montar_linha_tempo_real", lambda db, linha, d, agora: {
        "real": vazio, "saude": {"sim_ultima_ok_em": None, "nimer_status": None, "nimer_ultima_ok_em": None},
        "linha": linha.codigo})
    monkeypatch.setattr(central.alertas_op, "montar_alertas", lambda db, linha, r, agora: [
        {"chave": f"{r['linha']}{i}", "ativo": True, "nivel": "alerta", "inicio_em": f"2026-11-12T1{i}:00"}
        for i in range(abertos[r["linha"]])])
    monkeypatch.setattr(central, "tratativas", lambda db, d: {})
    monkeypatch.setattr(central, "fiscais_no_ponto", lambda db, ls: {})
    j = central.montar_minhas_linhas(None, linhas, date(2026, 11, 12), {})
    assert [c["linha_codigo"] for c in j["cartoes"]] == ["B", "C", "A"]
    inicios = [a["inicio_em"] for a in j["fila"]]
    assert len(inicios) == 3 and inicios == sorted(inicios, reverse=True)  # mais recentes primeiro
