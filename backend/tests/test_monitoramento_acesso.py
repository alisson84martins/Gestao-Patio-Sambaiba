"""Bloco H — quem vê qual linha ao vivo (regra no BACKEND).

Três pontos ao mesmo tempo (decisão 5): fiscais em pontos diferentes, cada
um com as linhas do turno dele; coordenador com as linhas que coordena;
ADMIN com todas. O comportamento ERRADO que barramos: fiscal vendo linha
fora do turno (403) e fiscal sem turno aberto vendo qualquer linha (403).
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.fiscalizacao import LinhaCoordenador, Turno, TurnoLinha
from app.models.monitoramento import LinhaMonitoradaCadastro
from app.routers import monitoramento as router_mod
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_ao_vivo as servico
from tests.ambiente_monitoramento import criar_engine

DIA = date(2026, 11, 10)
FISCAL_TP = Funcionario(id=uuid4(), re="70011", nome="Fiscal ponto 1")
FISCAL_TS = Funcionario(id=uuid4(), re="70012", nome="Fiscal ponto 2")
FISCAL_3 = Funcionario(id=uuid4(), re="70013", nome="Fiscal ponto 3")   # duas linhas no turno
SEM_TURNO = Funcionario(id=uuid4(), re="70014", nome="Fiscal sem turno")
COORD = Funcionario(id=uuid4(), re="70015", nome="Coordenador")
ADMIN = Funcionario(id=uuid4(), re="70016", nome="Admin")
OUTRA, TERCEIRA = "8012-10", "8013-10"
ROTAS = ["/monitoramento/linhas/{l}/ao-vivo", "/monitoramento/linhas/{l}/tracado",
         "/monitoramento/linhas/{l}/tempo-real", "/monitoramento/linhas/{l}/carros/22412/rastro"]


def _turno(db, f, linhas, status="ABERTO"):
    t = Turno(id=uuid4(), funcionario_id=f.id, fiscal_re=f.re, terminal="TP", periodo="1", data_referencia=DIA,
              tipo_dia="UTIL", status=status, aberto_em=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc))
    db.add(t)
    for linha in linhas:
        db.add(TurnoLinha(id=uuid4(), turno_id=t.id, linha_codigo=linha))


@pytest.fixture
def cli(monkeypatch):
    eng = criar_engine()
    with Session(eng) as db:
        for f in (FISCAL_TP, FISCAL_TS, FISCAL_3, SEM_TURNO, COORD, ADMIN):
            db.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        db.add(LinhaMonitoradaCadastro(linha_codigo=OUTRA, ativo=True))
        db.add(LinhaMonitoradaCadastro(linha_codigo=TERCEIRA, ativo=True))
        _turno(db, FISCAL_TP, ["1726-10"])
        _turno(db, FISCAL_TS, [OUTRA])
        _turno(db, FISCAL_3, ["1726-10", TERCEIRA])
        _turno(db, SEM_TURNO, ["1726-10"], status="FECHADO")
        db.add(LinhaCoordenador(id=uuid4(), linha_codigo="1726-10", funcionario_id=COORD.id, periodo="1", ativo=True))
        funcao = Funcao(id=uuid4(), codigo="ADMIN", nome="Administrador", categoria="SISTEMA", nivel=1)
        db.add(funcao)
        db.flush()
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=ADMIN.id, funcao_id=funcao.id, principal=True,
                                 data_inicio=DIA, ativo=True))
        db.commit()

    def _db():
        with Session(eng) as db:
            yield db
    estado = {"u": FISCAL_TP}
    monkeypatch.setattr(acesso, "tem_acesso", lambda db, fid, recurso, escrever=False:
                        recurso == "fiscalizacao_painel" and fid == COORD.id)
    servico.limpar_cache()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[router_mod.Leitura.__metadata__[0].dependency] = lambda: estado["u"]
    cliente = TestClient(app)
    cliente.como = lambda f: estado.update(u=f)
    try:
        yield cliente
    finally:
        app.dependency_overrides.clear()
        servico.limpar_cache()


def _codigos(cli):
    return [x["codigo"] for x in cli.get("/monitoramento/linhas").json()]


def test_fiscal_ve_so_a_linha_do_turno_dele(cli):
    assert _codigos(cli) == ["1726-10"]
    for rota in ROTAS:
        assert cli.get(rota.format(l="1726-10")).status_code == 200, rota
        r = cli.get(rota.format(l=OUTRA))
        assert r.status_code == 403 and r.json()["erro"] == "Esta linha não está no seu turno aberto", rota


def test_tres_pontos_ao_mesmo_tempo_cada_um_com_as_suas(cli):
    cli.como(FISCAL_TS)
    assert _codigos(cli) == [OUTRA]
    assert cli.get(f"/monitoramento/linhas/{OUTRA}/ao-vivo").status_code == 200
    assert cli.get("/monitoramento/linhas/1726-10/ao-vivo").status_code == 403
    cli.como(FISCAL_3)
    assert _codigos(cli) == ["1726-10", TERCEIRA]
    assert cli.get(f"/monitoramento/linhas/{OUTRA}/tempo-real").status_code == 403


def test_fiscal_sem_turno_aberto_403_com_a_mensagem(cli):
    cli.como(SEM_TURNO)
    assert _codigos(cli) == []
    for rota in ROTAS:
        r = cli.get(rota.format(l="1726-10"))
        assert r.status_code == 403 and r.json()["erro"] == "Abra o turno para ver a linha ao vivo", rota


def test_coordenador_ve_as_linhas_que_coordena(cli):
    cli.como(COORD)
    assert _codigos(cli) == ["1726-10"]
    r = cli.get(f"/monitoramento/linhas/{OUTRA}/ao-vivo")
    assert r.status_code == 403 and "coordena" in r.json()["erro"]


def test_admin_ve_todas(cli):
    cli.como(ADMIN)
    assert _codigos(cli) == ["1726-10", OUTRA, TERCEIRA]
    assert cli.get(f"/monitoramento/linhas/{TERCEIRA}/tempo-real").status_code == 200


def test_linha_nao_monitorada_continua_404_para_quem_tem_turno(cli):
    assert cli.get("/monitoramento/linhas/9999-10/ao-vivo").status_code == 404
