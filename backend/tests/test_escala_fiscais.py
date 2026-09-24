"""Escala de Fiscais — Fase 2 (cadastros e importação dos modelos).

Mesmo padrão do projeto: SQLite em memória com ATTACH DATABASE para o schema
coordenadoria, permissão sobrescrita (vw_acesso_efetivo é view Postgres) e,
para provar o 403, o ENDPOINT real com um banco falso que não devolve
permissão nenhuma (mesmo padrão de test_importacao_permissao.py).

Decisões do Alisson de 24/09 cobertas aqui: D-A (fiscal pelo RE em texto,
sem cadastro obrigatório; nome quando há cadastro), D-B (marcador com o texto
original), D-C (G1/G2/G4 guardados, G3 bloqueado), D-D (A2/Ar2 → AR2), D-E
(horário inválido vira o padrão, com aviso) e D-F ("TS" sobrando sai do campo).

⛔ Todos os REs, nomes, linhas e postos daqui são FICTÍCIOS (repositório
público). Nenhum dado da planilha real entra em teste.
"""
import copy
import inspect
import json
import re as _re
import typing
from datetime import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
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
from app.services import escala_fiscais_importacao as importacao

_TABELAS_ESCALA = [
    EscalaFiscalQuadro, EscalaFiscalCoordenadorPeriodo, EscalaFiscalPontoFinal, EscalaFiscalPosto,
    EscalaFiscalPostoLinha, EscalaFiscalModelo, EscalaFiscalModeloPosto, EscalaFiscalDia,
    EscalaFiscalAlocacao, EscalaFiscalAusencia, EscalaFiscalTroca, EscalaFiscalCoordenadorHorario,
    EscalaFiscalPlantao, EscalaFiscalAlteracao,
]

# Cadastrados em Pessoas (funcionario). 90002, 90003, 90004 e 99999 NÃO são —
# D-A: fiscal sem cadastro continua entrando na escala pelo RE.
_COORD = Funcionario(id=uuid4(), re="80001", nome="Coordenador Teste")
_FISCAL_A = Funcionario(id=uuid4(), re="90001", nome="Fiscal Teste A")
# RE com zero à esquerda e RE com letra: provam que RE é texto, nunca número.
_FISCAL_ZERO = Funcionario(id=uuid4(), re="0905", nome="Fiscal Teste Zero")
_FISCAL_LETRA = Funcionario(id=uuid4(), re="9A06", nome="Fiscal Teste Letra")
_PESSOAS = (_COORD, _FISCAL_A, _FISCAL_ZERO, _FISCAL_LETRA)


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=[Funcionario.__table__] + [t.__table__ for t in _TABELAS_ESCALA])
    with Session(engine) as setup:
        for f in _PESSOAS:
            setup.add(Funcionario(id=f.id, re=f.re, nome=f.nome))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    leitura_dep = _dependency_de(router_mod.LeituraEscala)
    escrita_dep = _dependency_de(router_mod.EscritaEscala)
    app.dependency_overrides[get_db] = _get_db_teste
    app.dependency_overrides[leitura_dep] = lambda: _COORD
    app.dependency_overrides[escrita_dep] = lambda: _COORD

    yield {"engine": engine, "http": TestClient(app)}

    for dep in (get_db, leitura_dep, escrita_dep):
        app.dependency_overrides.pop(dep, None)


def _contar_tudo(engine) -> dict[str, int]:
    with Session(engine) as db:
        return {t.__tablename__: db.execute(select(func.count()).select_from(t)).scalar_one() for t in _TABELAS_ESCALA}


def _enviar(http, rota, dados):
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    return http.post(rota, files={"arquivo": ("escala.json", corpo, "application/json")})


# ─── JSON fictício no formato do extrator da planilha ─────────────────────────

def _posto(bloco, linha_planilha, cod_jb, lote, linhas, re1, ini1, fim1, re2, ini2, fim2, **extra):
    return {
        "bloco": bloco, "L": bloco, "linha_planilha": linha_planilha, "cod_jb": cod_jb, "lote": lote,
        "linhas_1p": linhas, "re_1p": re1, "dobra_amarelo_1p": False, "inicio_1p": ini1, "termino_1p": fim1,
        "linhas_2p": linhas, "re_2p": re2, "dobra_amarelo_2p": False, "inicio_2p": ini2, "termino_2p": fim2,
        **extra,
    }


def _json_ficticio() -> dict:
    return {
        "_aviso": "Massa FICTÍCIA de teste.",
        "SABADO IMPAR": {
            "titulo_tp": "ESCALA SABADO TP",
            "postos": [
                # 0 — horário inválido "23;00" no 2º período (vira padrão, com aviso)
                _posto("TP", 3, "901", "AR2", "9001/10 / 9002/10", 90001, "05:00", "14:00", 90002, "14:00", "23;00"),
                # 1 — outra garagem sem horário (vira padrão) e DIRETO com "-"
                _posto("TP", 5, "902", "AR2", "9003/10", "G1", None, None, "DIRETO", "-", "-"),
                # 2 — G3 no lugar do RE (bloqueia); lote "A2"; **** vira descoberto
                _posto("TS", 3, "903", "A2", "9004/10", "G3", None, None, "****", None, None),
                # 3 — RE sem cadastro (99999) no 1º; em branco no 2º; posto das 12:00 às 20:20
                _posto("TS", 5, "904", "AR2", "9005/10", 99999, "12:00", "20:20", None, "12:00", "20:20"),
                # 4 — texto do rodapé que caiu dentro da linha de posto
                _posto("TS", 7, "FISCAIS FÉRIAS 1° / 90009", "90010", "90011", None, None, None, None, None, None),
                # 5 — "TS" sobrando no campo de linhas (D-F); RE com zero à esquerda; xxx
                _posto("TP", 7, "905", "E2", "9006/10 TS", "0905", "05:00", "14:00", "xxx", None, None),
                # 6 — lote que não é lote; horário diferente do padrão entra como está
                _posto("TP", 9, "906", "E2 / 118", "9008/10", "0905", "08:30", "20:00", "G4", None, None),
            ],
            "rodape_bruto": [
                {"celula": "A55", "valor": "FISCAIS DE FOLGA", "amarelo": False},
                {"celula": "N55", "valor": "ATESTADO MÉDICO", "amarelo": False},
                {"celula": "C56", "valor": "1°", "amarelo": False},
                {"celula": "D56", "valor": 90003, "amarelo": False},
                {"celula": "E56", "valor": 90001, "amarelo": False},
                # Lista de ATESTADO (coluna N) — não é folga.
                {"celula": "N57", "valor": 90099, "amarelo": False},
                {"celula": "C59", "valor": "2°", "amarelo": False},
                {"celula": "D59", "valor": 90004, "amarelo": False},
                {"celula": "C62", "valor": "Folga 1x1 90004 90002", "amarelo": False},
            ],
        },
        "DOMINGO IMPAR": {
            "titulo_tp": "ESCALA DOMINGO TP",
            "postos": [
                # 0 — término antes do início (14:30 às 14:00) → padrão; lote "Ar2"
                _posto("TP", 3, "901", "Ar2", "9001/10 / 9002/10", 90003, "14:30", "14:00", 90004, "14:00", "23:00"),
                # 1 — data do Excel no lugar do horário → padrão
                _posto("TP", 5, "902", "AR2", "9003/10", 90001, "05:00", "07/01/1900", "G2", None, None),
            ],
            "rodape_bruto": [
                {"celula": "B49", "valor": "1°", "amarelo": False},
                {"celula": "C49", "valor": 90003, "amarelo": False},
                # Rótulo "TS" solto no meio da lista não pode cortá-la.
                {"celula": "M50", "valor": "TS", "amarelo": False},
                {"celula": "C50", "valor": 90005, "amarelo": False},
                {"celula": "B52", "valor": "2°", "amarelo": False},
                {"celula": "C52", "valor": 90002, "amarelo": False},
                {"celula": "C55", "valor": "Folga 1x1 90004 90002", "amarelo": False},
            ],
        },
        "quadro_Plan2": {"1_periodo": [90001], "2_periodo": [90002]},
    }


def _json_corrigido() -> dict:
    """Só o que BLOQUEIA precisa de correção: o G3 e a linha de rodapé."""
    d = _json_ficticio()
    si = d["SABADO IMPAR"]["postos"]
    si[2]["re_1p"] = "G1"
    si[4]["_descartar"] = True
    return d


def _tipos(relatorio, tipo):
    return [p for p in relatorio["problemas"] if p["tipo"] == tipo]


# ─── Permissão ────────────────────────────────────────────────────────────────

class _DBSemEscalaFiscal:
    """Funcionario resolve, mas vw_acesso_efetivo não tem linha para
    escala_fiscal — quem não tem o recurso."""

    def __init__(self, funcionario):
        self._f = funcionario

    def get(self, model, id_):
        return self._f if model is Funcionario and id_ == self._f.id else None

    def execute(self, *a, **k):
        class _R:
            def scalar_one_or_none(self):
                return None

            def fetchone(self):
                return None
        return _R()


@pytest.mark.parametrize("metodo,rota", [
    ("get", "/escala-fiscais/quadro"),
    ("get", "/escala-fiscais/modelos"),
    ("post", "/escala-fiscais/quadro"),
    ("post", "/escala-fiscais/importacao/simular"),
    ("post", "/escala-fiscais/importacao/confirmar"),
])
def test_quem_nao_tem_escala_fiscal_leva_403(metodo, rota):
    func_ = Funcionario(id=uuid4(), re="70001", nome="Sem Escala Fiscal")
    token = create_access_token(subject=func_.id)
    app.dependency_overrides[get_db] = lambda: _DBSemEscalaFiscal(func_)
    try:
        cliente = TestClient(app)
        headers = {"Authorization": f"Bearer {token}"}
        if metodo == "get":
            resp = cliente.get(rota, headers=headers)
        elif "importacao" in rota:
            resp = cliente.post(rota, headers=headers, files={"arquivo": ("a.json", b"{}", "application/json")})
        else:
            resp = cliente.post(rota, headers=headers, json={"re": "90001", "periodo": 1})
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_router_usa_o_recurso_escala_fiscal_e_nao_a_escala_do_patio():
    fonte = inspect.getsource(router_mod)
    assert 'exige("escala_fiscal")' in fonte
    assert 'exige("escala_fiscal", escrever=True)' in fonte
    assert 'exige("escala")' not in fonte


def test_rotas_literais_vem_antes_das_rotas_com_parametro():
    caminhos = [r.path for r in router_mod.router.routes]
    primeira_com_parametro = next(i for i, c in enumerate(caminhos) if "{" in c)
    assert all("{" in c for c in caminhos[primeira_com_parametro:])


def test_backend_nao_usa_date_today_nem_data_utc():
    for modulo in (router_mod, importacao):
        fonte = inspect.getsource(modulo)
        codigo = "\n".join(ln for ln in fonte.splitlines() if not ln.strip().startswith(("#", "*", "·", "⛔")))
        assert "date.today()" not in codigo.replace("nunca date.today()", "")
        assert "timezone.utc).date()" not in codigo.replace("datetime.now(timezone.utc).date().", "")
    assert "FUSO_OPERACAO" in inspect.getsource(router_mod)


# ─── D-A: fiscal pelo RE em texto, sem cadastro obrigatório ──────────────────

def test_quadro_aceita_re_sem_cadastro_e_mostra_so_o_re(ambiente):
    http = ambiente["http"]
    resp = http.post("/escala-fiscais/quadro", json={"re": " 90002 ", "periodo": 2})
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"re": "90002", "nome": None, "cadastrado": False,
                           "periodo": 2, "folga_base": None, "ativo": True}


def test_nome_aparece_quando_o_re_tem_cadastro(ambiente):
    http = ambiente["http"]
    assert http.post("/escala-fiscais/quadro", json={"re": "90001", "periodo": 1}).status_code == 201
    [item] = http.get("/escala-fiscais/quadro").json()
    assert item["nome"] == "Fiscal Teste A" and item["cadastrado"] is True


def test_quem_cadastra_depois_ganha_o_nome_sem_mexer_na_escala(ambiente):
    http, engine = ambiente["http"], ambiente["engine"]
    http.post("/escala-fiscais/quadro", json={"re": "90004", "periodo": 1})
    assert http.get("/escala-fiscais/quadro").json()[0]["nome"] is None
    with Session(engine) as db:  # cadastrado em Pessoas depois
        db.add(Funcionario(id=uuid4(), re="90004", nome="Fiscal Teste D"))
        db.commit()
    assert http.get("/escala-fiscais/quadro").json()[0]["nome"] == "Fiscal Teste D"


def test_re_normalizado_igual_ao_cadastro_de_pessoas(ambiente):
    """Resposta de 24/09: mesma função de app/core/registro.py — " 9a06 " vira
    "9A06" na escala exatamente como vira no cadastro, e o nome casa."""
    from app.core.registro import normalizar_re

    http = ambiente["http"]
    resp = http.post("/escala-fiscais/quadro", json={"re": " 9a06 ", "periodo": 1})
    assert resp.status_code == 201, resp.text
    assert resp.json()["re"] == normalizar_re(" 9a06 ") == "9A06"
    assert resp.json()["nome"] == "Fiscal Teste Letra"
    assert http.patch("/escala-fiscais/quadro/9a06", json={"ativo": False}).status_code == 200
    assert importacao.normalizar_re(90001) == "90001"


def test_re_e_texto_zero_a_esquerda_nao_some_nem_aparece(ambiente):
    http = ambiente["http"]
    assert http.post("/escala-fiscais/quadro", json={"re": "0905", "periodo": 1}).status_code == 201
    # "905" é OUTRO RE (sem cadastro): entra, mas sem o nome do 0905.
    assert http.post("/escala-fiscais/quadro", json={"re": "905", "periodo": 1}).status_code == 201
    por_re = {i["re"]: i["nome"] for i in http.get("/escala-fiscais/quadro").json()}
    assert por_re == {"0905": "Fiscal Teste Zero", "905": None}


def test_quadro_nao_aceita_o_mesmo_re_duas_vezes_e_edita_pelo_re(ambiente):
    http = ambiente["http"]
    assert http.post("/escala-fiscais/quadro", json={"re": "90001", "periodo": 1, "folga_base": "domingo"}).status_code == 201
    assert http.post("/escala-fiscais/quadro", json={"re": "90001", "periodo": 2}).status_code == 409
    alterado = http.patch("/escala-fiscais/quadro/90001", json={"folga_base": "sabado"}).json()
    assert alterado["folga_base"] == "sabado" and alterado["nome"] == "Fiscal Teste A"
    assert http.delete("/escala-fiscais/quadro/90001").status_code == 204


def test_ausencia_e_troca_aceitam_re_sem_cadastro(ambiente):
    http = ambiente["http"]
    a = http.post("/escala-fiscais/ausencias", json={"re": "90003", "tipo": "ferias", "data_inicio": "2026-09-14"})
    assert a.status_code == 201 and a.json()["nome"] is None
    t = http.post("/escala-fiscais/trocas", json={"tipo": "1x1", "re_a": "90001", "re_b": "90002", "data_sabado": "2026-09-26"})
    assert t.status_code == 201, t.text
    assert t.json()["a"]["nome"] == "Fiscal Teste A" and t.json()["b"] == {"re": "90002", "nome": None, "cadastrado": False}


# O COORDENADOR continua exigindo cadastro (FK em funcionario).
@pytest.mark.parametrize("rota,corpo", [
    ("/escala-fiscais/coordenadores/periodos", {"re": "12345", "periodo": 2}),
    ("/escala-fiscais/coordenadores/horarios", {"re": "12345", "turno": "tarde", "hora_inicio": "15:00", "hora_fim": "02:30"}),
])
def test_coordenador_sem_cadastro_devolve_a_mensagem_combinada(ambiente, rota, corpo):
    resp = ambiente["http"].post(rota, json=corpo)
    assert resp.status_code == 422, resp.text
    assert resp.json()["erro"] == "RE 12345 não está cadastrado. Cadastre em Pessoas antes"


def test_autocomplete_mostra_re_e_nome(ambiente):
    itens = ambiente["http"].get("/escala-fiscais/pessoas", params={"q": "9000"}).json()
    assert {"re": "90001", "nome": "Fiscal Teste A"} in itens
    por_nome = ambiente["http"].get("/escala-fiscais/pessoas", params={"q": "teste zero"}).json()
    assert por_nome == [{"re": "0905", "nome": "Fiscal Teste Zero"}]


# ─── Ausência: data fim INCLUSIVA ─────────────────────────────────────────────

def test_ausencia_data_fim_e_inclusiva(ambiente):
    http = ambiente["http"]
    resp = http.post("/escala-fiscais/ausencias", json={
        "re": "90003", "tipo": "ferias", "data_inicio": "2026-09-14", "data_fim": "2026-09-26",
    })
    assert resp.status_code == 201, resp.text

    def fora(dia):
        return [a["re"] for a in http.get("/escala-fiscais/ausencias", params={"data": dia}).json()]

    assert fora("2026-09-13") == []
    assert fora("2026-09-14") == ["90003"]   # primeiro dia
    assert fora("2026-09-26") == ["90003"]   # último dia — INCLUSIVO
    assert fora("2026-09-27") == []           # dia seguinte já volta


def test_ausencia_sem_data_fim_vale_para_sempre_e_fim_antes_do_inicio_e_recusado(ambiente):
    http = ambiente["http"]
    assert http.post("/escala-fiscais/ausencias", json={
        "re": "90004", "tipo": "afastado", "data_inicio": "2026-09-01",
    }).status_code == 201
    assert [a["re"] for a in http.get("/escala-fiscais/ausencias", params={"data": "2027-01-01"}).json()] == ["90004"]
    resp = http.post("/escala-fiscais/ausencias", json={
        "re": "90004", "tipo": "atestado", "data_inicio": "2026-09-26", "data_fim": "2026-09-25",
    })
    assert resp.status_code == 422


# ─── Horários: coordenador passa da meia-noite, fiscal não ────────────────────

def test_horario_de_coordenador_pode_passar_da_meia_noite(ambiente):
    http = ambiente["http"]
    resp = http.post("/escala-fiscais/coordenadores/horarios", json={
        "re": "80001", "turno": "tarde", "hora_inicio": "15:00", "hora_fim": "02:30",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["passa_meia_noite"] is True
    assert corpo["hora_fim"] == "02:30:00"
    alterado = http.patch(f"/escala-fiscais/coordenadores/horarios/{corpo['id']}", json={"hora_fim": "04:00"})
    assert alterado.status_code == 200 and alterado.json()["passa_meia_noite"] is True


def test_horario_de_coordenador_com_inicio_igual_ao_fim_e_recusado(ambiente):
    resp = ambiente["http"].post("/escala-fiscais/coordenadores/horarios", json={
        "re": "80001", "turno": "manha", "hora_inicio": "04:00", "hora_fim": "04:00",
    })
    assert resp.status_code == 422


def _modelo_e_posto(http, lado="TS"):
    posto = http.post("/escala-fiscais/postos", json={"lado": lado, "linhas": ["9001/10"]}).json()
    modelo = http.post("/escala-fiscais/modelos", json={"tipo_dia": "sabado", "paridade": "impar", "nome": "Sábado ímpar"}).json()
    return posto, f"/escala-fiscais/modelos/{modelo['id']}/postos"


def test_horario_de_fiscal_no_modelo_nao_passa_da_meia_noite_e_re_sem_cadastro_entra(ambiente):
    http = ambiente["http"]
    posto, rota = _modelo_e_posto(http)
    base = {"posto_id": posto["id"], "ordem": 1, "periodo": 2, "situacao_padrao": "escalado", "re_padrao": "90002"}
    assert http.post(rota, json={**base, "hora_inicio": "15:00", "hora_termino": "02:30"}).status_code == 422
    ok = http.post(rota, json={**base, "hora_inicio": "12:00", "hora_termino": "20:20"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["re_padrao"] == "90002" and ok.json()["nome_padrao"] is None
    assert ok.json()["marcador"] is None


def test_modelo_posto_guarda_marcador_e_deriva_quando_a_tela_nao_manda(ambiente):
    http = ambiente["http"]
    posto, rota = _modelo_e_posto(http)
    base = {"posto_id": posto["id"], "periodo": 1}
    em_branco = http.post(rota, json={**base, "ordem": 1, "situacao_padrao": "descoberto"}).json()
    assert em_branco["marcador"] == ""
    estrelas = http.post(rota, json={**base, "ordem": 2, "situacao_padrao": "descoberto", "marcador": "****"}).json()
    assert estrelas["marcador"] == "****"
    garagem = http.post(rota, json={**base, "ordem": 3, "situacao_padrao": "outra_garagem", "outra_garagem": "g4"}).json()
    assert garagem["outra_garagem"] == "G4" and garagem["marcador"] == "G4"
    # Mudar só o horário não apaga o marcador escrito.
    alterado = http.patch(f"{rota}/{estrelas['id']}", json={"hora_inicio": "05:00", "hora_termino": "14:00"}).json()
    assert alterado["marcador"] == "****"


def test_escalado_precisa_de_re_e_outra_garagem_nunca_e_g3(ambiente):
    http = ambiente["http"]
    posto, rota = _modelo_e_posto(http, "TP")
    base = {"posto_id": posto["id"], "ordem": 1, "periodo": 1}
    assert http.post(rota, json={**base, "situacao_padrao": "escalado"}).status_code == 422
    assert http.post(rota, json={**base, "situacao_padrao": "outra_garagem", "outra_garagem": "G3"}).status_code == 422
    assert http.post(rota, json={**base, "situacao_padrao": "outra_garagem", "outra_garagem": "G1"}).status_code == 201


def test_troca_exige_sabado_e_dois_res_diferentes(ambiente):
    http = ambiente["http"]
    base = {"tipo": "2x2", "re_a": "90001", "re_b": "90002"}
    assert http.post("/escala-fiscais/trocas", json={**base, "data_sabado": "2026-09-27"}).status_code == 422
    assert http.post("/escala-fiscais/trocas", json={**base, "re_b": "90001", "data_sabado": "2026-09-26"}).status_code == 422


def test_posto_com_as_mesmas_linhas_na_mesma_ponta_nao_duplica(ambiente):
    http = ambiente["http"]
    pf = http.post("/escala-fiscais/pontos-finais", json={"nome": "Terminal Fictício"}).json()
    assert http.post("/escala-fiscais/postos", json={"lado": "TP", "linhas": ["9001/10", "9002/10"], "ponto_final_id": pf["id"]}).status_code == 201
    assert http.post("/escala-fiscais/postos", json={"lado": "TP", "linhas": ["9002/10", "9001/10"]}).status_code == 409
    assert http.post("/escala-fiscais/postos", json={"lado": "TS", "linhas": ["9001/10", "9002/10"]}).status_code == 201
    assert http.get("/escala-fiscais/pontos-finais").json()[0]["qtd_postos"] == 1


# ─── Importação: SIMULAR não grava nada ───────────────────────────────────────

def test_simulacao_nao_grava_nada(ambiente):
    antes = _contar_tudo(ambiente["engine"])
    resp = _enviar(ambiente["http"], "/escala-fiscais/importacao/simular", _json_corrigido())
    assert resp.status_code == 200, resp.text
    assert resp.json()["pode_confirmar"] is True
    assert _contar_tudo(ambiente["engine"]) == antes
    assert all(v == 0 for v in antes.values())


def test_simulacao_com_json_invalido_da_422(ambiente):
    resp = ambiente["http"].post(
        "/escala-fiscais/importacao/simular", files={"arquivo": ("a.json", b"{nao e json", "application/json")}
    )
    assert resp.status_code == 422


@pytest.fixture
def relatorio(ambiente):
    resp = _enviar(ambiente["http"], "/escala-fiscais/importacao/simular", _json_ficticio())
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_d_e_horario_invalido_ou_invertido_vira_padrao_com_aviso_sem_bloquear(relatorio):
    ajustados = _tipos(relatorio, "ajustado_padrao")
    valores = {p["valor"] for p in ajustados}
    assert {"23;00", "07/01/1900", "14:00"} <= valores
    assert not any(p["bloqueia"] for p in ajustados)
    por_valor = {p["valor"]: p for p in ajustados}
    assert por_valor["23;00"]["padrao"] == "14:00–23:00"
    assert por_valor["14:00"]["padrao"] == "05:00–14:00"
    assert "término não é depois do início" in por_valor["14:00"]["mensagem"]


def test_d_e_celula_sem_horario_fica_sem_horario_e_nao_vira_aviso(relatorio):
    """Resposta de 24/09: G1, DIRETO com "-", ****, xxx e em branco sem horário
    na planilha ficam SEM horário — o padrão é só para horário inválido."""
    assert not _tipos(relatorio, "horario_ausente")
    assert relatorio["resumo"]["ajustados_para_o_padrao"] == len(_tipos(relatorio, "ajustado_padrao")) == 3
    assert relatorio["resumo"]["sem_horario_na_planilha"] >= 5


def test_horario_padrao_tem_valor_no_codigo_sem_env(monkeypatch):
    """Se o .env de produção não tiver as variáveis, o sistema usa o padrão
    e não quebra."""
    from app.core.config import Settings

    monkeypatch.delenv("ESCALA_FISCAL_PADRAO_PERIODO_1", raising=False)
    monkeypatch.delenv("ESCALA_FISCAL_PADRAO_PERIODO_2", raising=False)
    s = Settings(_env_file=None, database_url="sqlite://", secret_key="x" * 32)
    assert s.escala_fiscal_padrao_periodo_1 == "05:00-14:00"
    assert s.escala_fiscal_padrao_periodo_2 == "14:00-23:00"
    assert importacao.padrao_de_texto(s.escala_fiscal_padrao_periodo_1) == (time(5, 0), time(14, 0))


def test_d_c_g3_continua_bloqueado(relatorio):
    [p] = _tipos(relatorio, "g3_no_re")
    assert p["campo"] == "re_1p" and p["valor"] == "G3" and p["bloqueia"]
    assert relatorio["pode_confirmar"] is False


def test_d_d_a2_e_ar2_viram_ar2_e_lote_estranho_vira_aviso(relatorio):
    normalizados = {p["valor"]: p for p in _tipos(relatorio, "lote_normalizado")}
    assert set(normalizados) == {"A2", "Ar2"}
    assert all('"AR2"' in p["mensagem"] for p in normalizados.values())
    [estranho] = _tipos(relatorio, "lote_invalido")
    assert estranho["valor"] == "E2 / 118" and not estranho["bloqueia"]


def test_d_f_ts_sobrando_sai_do_campo_e_a_linha_fica(relatorio):
    avisos = _tipos(relatorio, "ponta_anotada")
    # O posto fictício tem "9006/10 TS" nos dois períodos: um aviso por período.
    assert {(p["indice"], p["campo"]) for p in avisos} == {(5, "linhas_1p"), (5, "linhas_2p")}
    assert not any(p["bloqueia"] for p in avisos)
    assert not any(x["indice"] == 5 for x in _tipos(relatorio, "texto_rodape"))


def test_texto_do_rodape_dentro_do_posto_continua_bloqueando(relatorio):
    [p] = _tipos(relatorio, "texto_rodape")
    assert p["indice"] == 4 and p["bloqueia"] and "descartar" in p["acoes"]


def test_d_a_re_sem_cadastro_e_so_aviso(relatorio):
    res = [c["re"] for c in relatorio["re_sem_cadastro"]]
    assert "99999" in res and "90002" in res and "90001" not in res and "0905" not in res
    assert relatorio["resumo"]["escalado_sem_cadastro"] >= 1


def test_relatorio_sugere_folga_sem_gravar(ambiente, relatorio):
    sug = {s["re"]: s for s in relatorio["sugestao_folga"]}
    assert sug["90001"]["folga_sugerida"] == "sabado" and sug["90001"]["periodo_sugerido"] == 1
    assert sug["90001"]["nome"] == "Fiscal Teste A"
    assert sug["90002"]["folga_sugerida"] == "domingo" and sug["90002"]["periodo_sugerido"] == 2
    assert sug["90003"]["folga_sugerida"] is None and "decida" in sug["90003"]["observacao"]
    assert "troca" in sug["90004"]["observacao"]
    assert sug["90005"]["folga_sugerida"] == "domingo"  # depois do "TS" solto
    assert "90099" not in sug
    assert _contar_tudo(ambiente["engine"])["escala_fiscal_quadro"] == 0


# ─── Importação: CONFIRMAR ────────────────────────────────────────────────────

def test_confirmar_com_problema_bloqueante_nao_grava(ambiente):
    antes = _contar_tudo(ambiente["engine"])
    resp = _enviar(ambiente["http"], "/escala-fiscais/importacao/confirmar", _json_ficticio())
    assert resp.status_code == 422
    assert "Nada foi gravado" in resp.json()["erro"]
    assert _contar_tudo(ambiente["engine"]) == antes


def test_confirmar_grava_re_sem_cadastro_marcador_e_padrao(ambiente):
    http, engine = ambiente["http"], ambiente["engine"]
    resp = _enviar(http, "/escala-fiscais/importacao/confirmar", _json_corrigido())
    assert resp.status_code == 200, resp.text

    contagem = _contar_tudo(engine)
    assert contagem["escala_fiscal_modelo"] == 2
    assert contagem["escala_fiscal_ponto_final"] == 0   # ponto final: só pela tela
    assert contagem["escala_fiscal_quadro"] == 0        # folga base: só pela tela
    assert contagem["escala_fiscal_dia"] == 0           # montagem é a próxima fase

    with Session(engine) as db:
        itens = db.execute(select(EscalaFiscalModeloPosto)).scalars().all()
        res = {i.re_padrao for i in itens if i.situacao_padrao == "escalado"}
        # D-A: RE sem cadastro gravado como está; zero à esquerda preservado.
        assert {"90001", "90002", "99999", "0905"} <= res
        assert all(i.marcador is None for i in itens if i.situacao_padrao == "escalado")
        # D-B/D-C: o texto original da planilha.
        marcadores = {i.marcador for i in itens if i.situacao_padrao != "escalado"}
        assert {"", "****", "xxx", "G1", "G2", "G4", "DIRETO"} <= marcadores
        assert {i.outra_garagem for i in itens if i.situacao_padrao == "outra_garagem"} == {"G1", "G2", "G4"}
        # D-E: horário diferente do padrão entra como está; inválido vira padrão.
        horarios = {(i.hora_inicio, i.hora_termino) for i in itens}
        assert (time(8, 30), time(20, 0)) in horarios
        assert (time(12, 0), time(20, 20)) in horarios
        assert all(i.hora_termino > i.hora_inicio for i in itens if i.hora_inicio and i.hora_termino)
        # Fidelidade ao Excel: G1 sem horário na planilha fica sem horário.
        g1 = [i for i in itens if i.marcador == "G1"]
        assert g1 and all(i.hora_inicio is None and i.hora_termino is None for i in g1)
        # "xxx" fica como está escrito (não vira "XXX").
        assert "xxx" in {i.marcador for i in itens}
        # D-D: lote A2 gravado como AR2; D-F: a linha 9006/10 ficou no posto.
        lotes = {p.lote for p in db.execute(select(EscalaFiscalPosto)).scalars()}
        assert "A2" not in lotes and "Ar2" not in lotes and "AR2" in lotes
        linhas = {pl.linha for pl in db.execute(select(EscalaFiscalPostoLinha)).scalars()}
        assert "9006/10" in linhas
        assert contagem["escala_fiscal_posto"] == len({i.posto_id for i in itens})

    # Nome aparece na leitura do modelo quando o RE tem cadastro.
    modelo_id = http.get("/escala-fiscais/modelos").json()[0]["id"]
    detalhe = http.get(f"/escala-fiscais/modelos/{modelo_id}").json()
    nomes = {p["re_padrao"]: p["nome_padrao"] for p in detalhe["postos"] if p["re_padrao"]}
    assert nomes.get("90001") == "Fiscal Teste A"

    # Segunda confirmação do mesmo arquivo: não sobrescreve.
    de_novo = _enviar(http, "/escala-fiscais/importacao/simular", _json_corrigido()).json()
    assert {p["tipo"] for p in de_novo["problemas"] if p["bloqueia"]} == {"modelo_ja_importado"}


def test_importacao_nao_tem_dado_real_no_codigo():
    fonte = inspect.getsource(importacao)
    assert not _re.search(r"['\"]\d{3,6}['\"]", fonte)
    assert not _re.search(r"['\"][0-9A-Z]{4}/\d{2}['\"]", fonte)


def test_linha_descartada_aparece_no_relatorio(ambiente):
    d = copy.deepcopy(_json_ficticio())
    d["SABADO IMPAR"]["postos"][4]["_descartar"] = True
    rel = _enviar(ambiente["http"], "/escala-fiscais/importacao/simular", d).json()
    assert [x["indice"] for x in rel["linhas_descartadas"]] == [4]
    assert not _tipos(rel, "texto_rodape")
