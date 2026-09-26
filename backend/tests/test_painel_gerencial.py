"""Painel Gerencial (migration 046) — só leitura sobre vw_painel_evento.

Sem PostgreSQL, como o resto da suíte: o banco é um falso que só responde
ao que exige() e get_current_funcionario() perguntam (Funcionario +
vw_acesso_efetivo), e as consultas do painel (_consultar_*) são trocadas por
monkeypatch — o SQL delas foi conferido contra o banco, não aqui.

O que se prova:
  1. A trava: 403 para quem não tem `painel_gerencial` — inclusive o
     COORDENADOR_TRAFEGO, que tem `relatorios` (por isso o recurso é novo);
     200 para quem tem, nas três rotas.
  2. Dia do relógio em São Paulo: 25/09 = [03:00Z do dia 25, 03:00Z do 26);
     23h30 de SP (02:30Z do dia seguinte) ENTRA no dia 25.
  3. Período: de > ate → 422; mais de 400 dias → 422; ate no futuro trava
     em hoje; "inicio" usa o primeiro registro.
  4. CSV: BOM + ';' + data/hora em SP; retirada sem autor sai como
     "Limpeza geral do pátio".
  5. Chips (26/09): grupo vira tipo IN (...) com bind; categoria só com
     ENTRADAS/SAIDAS; grupo de outro módulo → 422.
  6. Regra de ouro: número do chip = linhas do filtro — o SQL de verdade
     rodando num SQLite com uma tabela no lugar da view.
  7. Dentro agora: len() da lista = contador, com a regra D18 de verdade.

⛔ REs e nomes fictícios (repositório público).
"""
import sqlite3
import uuid as _uuid_mod
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.cadastro import Funcionario
from app.routers import painel_gerencial as pg

# Mesmo ajuste de test_portaria.py: o sqlite3 puro não serializa uuid.UUID
# (seções 6 e 7 rodam o SQL de verdade em SQLite).
sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_ROTAS = (
    "/painel-gerencial/eventos", "/painel-gerencial/resumo", "/painel-gerencial/exportar.csv",
    "/painel-gerencial/dentro-agora", "/painel-gerencial/dentro-agora.csv",
)


class _DBFalso:
    """Responde só ao que a autenticação e o RBAC perguntam. `recursos` é o
    acesso efetivo da pessoa (o que vw_acesso_efetivo devolveria)."""

    def __init__(self, funcionario, recursos):
        self._funcionario = funcionario
        self._recursos = set(recursos)

    def get(self, model, id_):
        if model is Funcionario and id_ == self._funcionario.id:
            return self._funcionario
        return None

    def add(self, *_a, **_k):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, stmt, params=None, *a, **k):
        recursos = self._recursos

        class _Linha:
            pode_ler = True
            pode_escrever = False

        class _R:
            def scalar_one_or_none(self):
                return None  # sem UsuarioLogin — não bloqueia get_current_funcionario

            def fetchone(self):
                if "vw_acesso_efetivo" in str(stmt) and params and params.get("rec") in recursos:
                    return _Linha()
                return None
        return _R()


_EVENTO = {
    "id": uuid4(),
    "momento": datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc),  # 23h30 de 25/09 em SP
    "modulo": "PATIO", "tipo": "RETIRADA", "categoria": "Fila 3", "identificacao": "2140",
    "detalhe": "Saiu de Fila 3 · pos 1", "pessoa_re": None, "pessoa_nome": None,
    "autor_re": None, "autor_nome": None,
}

_DENTRO = {
    "id": uuid4(),
    "entrou_em": datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc),  # 08h00 de SP
    "identificacao": "TST1A23", "categoria": "TERCEIRO",
    "pessoa_re": None, "pessoa_nome": "Prestador Teste", "detalhe": "Empresa Ficticia",
}


@pytest.fixture
def painel_sem_banco(monkeypatch):
    """Troca as consultas SQL do painel por respostas fixas."""
    monkeypatch.setattr(pg, "_consultar_eventos", lambda *a, **k: [dict(_EVENTO)])
    monkeypatch.setattr(pg, "_consultar_contagens", lambda *a, **k: [
        {"tipo": "ENTRADA", "categoria": "FUNCIONARIO", "n": 9},
        {"tipo": "ENTRADA", "categoria": "TERCEIRO", "n": 3},
        {"tipo": "SAIDA", "categoria": "FUNCIONARIO", "n": 10},
        {"tipo": "RECOLHIDA", "categoria": None, "n": 3},
        {"tipo": "RECOLHIDA_AVALIADA", "categoria": None, "n": 2},
        {"tipo": "AVARIA_ENCERRADA", "categoria": None, "n": 1},
        {"tipo": "MOVIMENTACAO", "categoria": None, "n": 40},
    ])
    monkeypatch.setattr(pg, "_primeiro_registro", lambda db: {
        "portaria": datetime(2026, 8, 22, 20, 49, tzinfo=timezone.utc),
        "patio": datetime(2026, 6, 13, 15, 35, tzinfo=timezone.utc),
    })
    monkeypatch.setattr(pg, "_contar_dentro_agora", lambda db: 7)
    monkeypatch.setattr(pg, "_listar_dentro_agora", lambda db: [dict(_DENTRO)])


def _chamar(rota, recursos, params=None):
    func = Funcionario(id=uuid4(), re="70001", nome="Pessoa Teste")
    token = create_access_token(subject=func.id)
    app.dependency_overrides[get_db] = lambda: _DBFalso(func, recursos)
    try:
        return TestClient(app).get(rota, params=params, headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─── 1 · A trava ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rota", _ROTAS)
def test_coordenador_de_trafego_nao_ve_mesmo_tendo_relatorios(rota, painel_sem_banco):
    """🔴 O motivo do recurso ser novo: `relatorios` não abre o painel."""
    resp = _chamar(rota, {"relatorios", "ocorrencia", "coordenacao"})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("rota", _ROTAS)
def test_operador_de_patio_nao_ve(rota, painel_sem_banco):
    resp = _chamar(rota, {"alocacao", "escala", "alerta"})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("rota", _ROTAS)
def test_quem_tem_painel_gerencial_ve(rota, painel_sem_banco):
    resp = _chamar(rota, {"painel_gerencial"})
    assert resp.status_code == 200, resp.text


def test_sem_token_da_401():
    assert TestClient(app).get("/painel-gerencial/resumo").status_code == 401


# ─── 2 · Dia do relógio em São Paulo ─────────────────────────────────────────

def test_intervalo_do_dia_25_em_utc():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 25), date(2026, 9, 25))
    assert inicio == datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    assert fim == datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)


def test_evento_as_23h30_de_sp_entra_no_dia_25():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 25), date(2026, 9, 25))
    momento = datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)  # 23h30 de 25/09 em SP
    assert inicio <= momento < fim


def test_intervalo_de_varios_dias_fecha_na_meia_noite_do_dia_seguinte():
    inicio, fim = pg.intervalo_utc(date(2026, 9, 1), date(2026, 9, 30))
    assert inicio == datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    assert fim == datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


def test_hoje_depois_das_21h_ainda_e_o_mesmo_dia_em_sp():
    """toISOString()/UTC já viraram o dia às 21h de SP; o relógio da garagem não."""
    agora = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)  # 22h de 25/09 em SP
    assert pg.hoje_sp(agora) == date(2026, 9, 25)


# ─── 3 · Período ─────────────────────────────────────────────────────────────

_HOJE = date(2026, 9, 25)


def _sem_primeiro():
    return None


def test_padrao_e_hoje():
    assert pg.resolver_periodo(None, None, _HOJE, _sem_primeiro) == (_HOJE, _HOJE)


def test_de_depois_de_ate_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("2026-09-20", "2026-09-10", _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_periodo_maior_que_400_dias_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("2025-08-01", "2026-09-25", _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_400_dias_exatos_passa():
    de, ate = pg.resolver_periodo("2025-08-22", "2026-09-25", _HOJE, _sem_primeiro)
    assert (ate - de).days + 1 == 400


def test_ate_no_futuro_trava_em_hoje():
    assert pg.resolver_periodo("2026-09-20", "2026-12-31", _HOJE, _sem_primeiro) == (date(2026, 9, 20), _HOJE)


def test_data_invalida_da_422():
    with pytest.raises(HTTPException) as exc:
        pg.resolver_periodo("25/09/2026", None, _HOJE, _sem_primeiro)
    assert exc.value.status_code == 422


def test_inicio_usa_o_primeiro_registro():
    assert pg.resolver_periodo("inicio", None, _HOJE, lambda: date(2026, 6, 13)) == (date(2026, 6, 13), _HOJE)


def test_de_maior_que_ate_pela_api_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"}, {"de": "2026-09-20", "ate": "2026-09-10"})
    assert resp.status_code == 422


def test_tipo_desconhecido_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"}, {"tipo": "DROP TABLE"})
    assert resp.status_code == 422


def test_antes_chave_com_id_invalido_da_422(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"},
                   {"antes": "2026-09-25T12:00:00Z", "antes_chave": "ENTRADA:nao-e-uuid"})
    assert resp.status_code == 422


def test_busca_escapa_curinga_do_ilike():
    _where, params = pg._where_eventos(None, None, "50%_", None, None, None)
    assert params["busca"] == "%50\\%\\_%"


# ─── Resumo ──────────────────────────────────────────────────────────────────

def test_resumo_so_devolve_contadores_simples(painel_sem_banco):
    """Visualização, não análise: número do período, sem "anterior", sem %,
    sem ranking nem agregação."""
    corpo = _chamar("/painel-gerencial/resumo", {"painel_gerencial"}).json()
    assert set(corpo) == {"periodo", "contadores", "categorias", "dentro_agora", "primeiro_registro"}
    # RA e Avarias somam os tipos do grupo; a chave antiga "recolhidas" saiu.
    assert corpo["contadores"] == {
        "entradas": 12, "saidas": 10, "ra": 5, "avarias": 1,
        "alocacoes": 0, "movimentacoes": 40, "retiradas": 0,
    }
    assert corpo["periodo"]["inclui_hoje"] is True
    assert corpo["dentro_agora"] == 7
    assert corpo["primeiro_registro"]["patio"].startswith("2026-06-13")


def test_resumo_de_periodo_passado_nao_calcula_dentro_agora(painel_sem_banco):
    corpo = _chamar("/painel-gerencial/resumo", {"painel_gerencial"},
                    {"de": "2026-09-01", "ate": "2026-09-02"}).json()
    assert corpo["periodo"]["inclui_hoje"] is False
    assert corpo["dentro_agora"] is None


# ─── 4 · CSV ─────────────────────────────────────────────────────────────────

def test_csv_tem_bom_ponto_e_virgula_e_hora_de_sp(painel_sem_banco):
    resp = _chamar("/painel-gerencial/exportar.csv", {"painel_gerencial"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xef\xbb\xbf")
    texto = resp.content.decode("utf-8-sig")
    cabecalho, linha = texto.splitlines()[:2]
    assert cabecalho.startswith("Data e hora;Módulo;Tipo;")
    assert linha.startswith("25/09/2026 23:30;PATIO;RETIRADA;")
    assert linha.endswith(";Limpeza geral do pátio")
    assert "attachment" in resp.headers["content-disposition"]


# ─── 5 · Chips: grupo e categoria (26/09) ────────────────────────────────────

def test_grupo_ra_traz_os_tres_tipos_e_nada_mais():
    where, params = pg._where_eventos("PORTARIA", None, None, None, None, None, "RA", None)
    tipos = {v for k, v in params.items() if k.startswith("grupo_tipo")}
    assert tipos == {"RECOLHIDA", "RECOLHIDA_AVALIADA", "RECOLHIDA_ENCERRADA"}
    # Valor nunca entra no texto do SQL — só o nome do parâmetro.
    assert "RECOLHIDA" not in where and "tipo IN (:grupo_tipo0" in where


def test_categoria_vira_parametro():
    where, params = pg._where_eventos("PORTARIA", None, None, None, None, None, "ENTRADAS", "TERCEIRO")
    assert "categoria = :categoria" in where and params["categoria"] == "TERCEIRO"


def test_todo_grupo_tem_modulo_e_todo_tipo_do_grupo_existe():
    assert set(pg.GRUPOS) == set(pg.MODULO_DO_GRUPO)
    tipos_validos = set(pg.Tipo.__args__)
    for tipos in pg.GRUPOS.values():
        assert set(tipos) <= tipos_validos
    # VEICULO_SITUACAO fica sem chip, de propósito.
    assert all("VEICULO_SITUACAO" not in t for t in pg.GRUPOS.values())


@pytest.mark.parametrize("rota", ("/painel-gerencial/eventos", "/painel-gerencial/exportar.csv"))
@pytest.mark.parametrize("params", [
    {"modulo": "PORTARIA", "grupo": "RA", "categoria": "TERCEIRO"},       # categoria com grupo errado
    {"modulo": "PORTARIA", "categoria": "TERCEIRO"},                      # categoria sem grupo
    {"modulo": "PORTARIA", "grupo": "ALOCACOES"},                         # grupo de outro módulo
    {"modulo": "PATIO", "grupo": "ENTRADAS"},                             # idem
    {"modulo": "PORTARIA", "grupo": "ENTRADAS", "tipo": "ENTRADA"},       # grupo e tipo juntos
    {"modulo": "PORTARIA", "grupo": "NADA"},                              # grupo desconhecido
    {"modulo": "PORTARIA", "grupo": "ENTRADAS", "categoria": "VISITA"},   # categoria desconhecida
])
def test_combinacao_invalida_da_422(rota, params, painel_sem_banco):
    resp = _chamar(rota, {"painel_gerencial"}, params)
    assert resp.status_code == 422, resp.text


def test_grupo_e_categoria_validos_passam(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"},
                   {"modulo": "PORTARIA", "grupo": "SAIDAS", "categoria": "FROTA_APOIO"})
    assert resp.status_code == 200, resp.text


def test_tipo_legado_continua_funcionando(painel_sem_banco):
    resp = _chamar("/painel-gerencial/eventos", {"painel_gerencial"}, {"modulo": "PATIO", "tipo": "RETIRADA"})
    assert resp.status_code == 200, resp.text


def test_categorias_sempre_com_as_4_chaves(painel_sem_banco):
    corpo = _chamar("/painel-gerencial/resumo", {"painel_gerencial"}).json()
    assert corpo["categorias"] == {
        "entradas": {"FUNCIONARIO": 9, "FROTA_APOIO": 0, "TERCEIRO": 3, "RESERVADO": 0},
        "saidas": {"FUNCIONARIO": 10, "FROTA_APOIO": 0, "TERCEIRO": 0, "RESERVADO": 0},
    }


def test_categorias_sem_movimento_vem_tudo_zero():
    assert pg.montar_categorias([]) == {
        "entradas": dict.fromkeys(pg.CATEGORIAS, 0), "saidas": dict.fromkeys(pg.CATEGORIAS, 0),
    }


# ─── 6 · Regra de ouro: número do chip = linhas do filtro ────────────────────
# O SQL de verdade (_consultar_contagens e _consultar_eventos) rodando contra
# uma TABELA com as colunas da view, num SQLite com o schema `public`
# anexado. Prova que contador e filtro leem o mesmo GRUPOS.

_INI = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
_FIM = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)

# (modulo, tipo, categoria, quantidade) — tem de tudo, inclusive
# VEICULO_SITUACAO (sem chip) e um evento FORA do período.
_SEMENTE = [
    ("PORTARIA", "ENTRADA", "FUNCIONARIO", 4), ("PORTARIA", "ENTRADA", "TERCEIRO", 3),
    ("PORTARIA", "ENTRADA", "FROTA_APOIO", 2), ("PORTARIA", "ENTRADA", "RESERVADO", 1),
    ("PORTARIA", "SAIDA", "FUNCIONARIO", 2), ("PORTARIA", "SAIDA", "TERCEIRO", 5),
    ("PORTARIA", "RECOLHIDA", "DEFEITO", 2), ("PORTARIA", "RECOLHIDA_AVALIADA", "DEFEITO", 1),
    ("PORTARIA", "RECOLHIDA_ENCERRADA", "DEFEITO", 1),
    ("PORTARIA", "AVARIA", "LEVE", 3), ("PORTARIA", "AVARIA_REVISTA", "LEVE", 2),
    ("PORTARIA", "AVARIA_CONTESTADA", "LEVE", 1), ("PORTARIA", "AVARIA_ENCERRADA", "LEVE", 1),
    ("PORTARIA", "VEICULO_SITUACAO", "AUTORIZADO", 2),
    ("PATIO", "ALOCACAO", "Fila 1", 6), ("PATIO", "MOVIMENTACAO", "Fila 2", 8), ("PATIO", "RETIRADA", "Fila 3", 3),
]


@pytest.fixture
def banco_view():
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS public")

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE public.vw_painel_evento (id TEXT, momento TIMESTAMP, modulo TEXT, tipo TEXT,"
            " categoria TEXT, identificacao TEXT, detalhe TEXT, pessoa_re TEXT, pessoa_nome TEXT,"
            " autor_re TEXT, autor_nome TEXT)"
        )
        ins = text("INSERT INTO public.vw_painel_evento (id, momento, modulo, tipo, categoria)"
                   " VALUES (:id, :momento, :modulo, :tipo, :categoria)")
        minuto = 0
        for modulo, tipo, categoria, n in _SEMENTE:
            for _ in range(n):
                minuto += 1
                conn.execute(ins, {"id": uuid4().hex, "momento": _INI + timedelta(minutes=minuto),
                                   "modulo": modulo, "tipo": tipo, "categoria": categoria})
        # Fora do período (dia 24): não pode contar nem aparecer.
        conn.execute(ins, {"id": uuid4().hex, "momento": _INI - timedelta(hours=1),
                           "modulo": "PORTARIA", "tipo": "ENTRADA", "categoria": "TERCEIRO"})
    with Session(engine) as db:
        yield db


def _linhas(db, modulo, grupo, categoria=None, limit=10_000):
    where, params = pg._where_eventos(modulo, None, None, None, None, None, grupo, categoria)
    return pg._consultar_eventos(db, _INI, _FIM, where, params, limit)


@pytest.mark.parametrize("chave,grupo", list(pg._CONTADORES.items()))
def test_regra_de_ouro_contador_igual_linhas_do_filtro(banco_view, chave, grupo):
    contagens = pg._consultar_contagens(banco_view, _INI, _FIM)
    contadores = pg.montar_contadores(contagens)
    linhas = _linhas(banco_view, pg.MODULO_DO_GRUPO[grupo], grupo)
    assert contadores[chave] == len(linhas) > 0
    assert {l["tipo"] for l in linhas} <= set(pg.GRUPOS[grupo])


def test_regra_de_ouro_ra_e_avarias_trazem_todas_as_situacoes(banco_view):
    assert {l["tipo"] for l in _linhas(banco_view, "PORTARIA", "RA")} == set(pg.GRUPOS["RA"])
    assert {l["tipo"] for l in _linhas(banco_view, "PORTARIA", "AVARIAS")} == set(pg.GRUPOS["AVARIAS"])


@pytest.mark.parametrize("chave,grupo", [("entradas", "ENTRADAS"), ("saidas", "SAIDAS")])
@pytest.mark.parametrize("categoria", pg.CATEGORIAS)
def test_regra_de_ouro_sub_chip_de_categoria(banco_view, chave, grupo, categoria):
    categorias = pg.montar_categorias(pg._consultar_contagens(banco_view, _INI, _FIM))
    linhas = _linhas(banco_view, "PORTARIA", grupo, categoria)
    assert categorias[chave][categoria] == len(linhas)
    assert all(l["categoria"] == categoria for l in linhas)


def test_segunda_pagina_com_grupo_nao_mistura_assunto(banco_view):
    """'Carregar mais' com o chip ativo: o cursor soma, não substitui, o grupo."""
    primeira = _linhas(banco_view, "PORTARIA", "SAIDAS", limit=3)
    ultimo = primeira[-1]
    where, params = pg._where_eventos("PORTARIA", None, None, ultimo["momento"],
                                      f"{ultimo['tipo']}:{ultimo['id']}", None, "SAIDAS", None)
    segunda = pg._consultar_eventos(banco_view, _INI, _FIM, where, params, 100)
    assert {l["tipo"] for l in primeira + segunda} == {"SAIDA"}
    assert len(primeira) + len(segunda) == 7
    assert not {l["id"] for l in primeira} & {l["id"] for l in segunda}


def test_csv_com_grupo_e_categoria_passa_o_filtro_para_a_consulta(monkeypatch, painel_sem_banco):
    visto = {}

    def _consulta(db, ini, fim, where, params, limit):
        visto.update(where=where, params=params)
        return [dict(_EVENTO, modulo="PORTARIA", tipo="ENTRADA", categoria="TERCEIRO")]

    monkeypatch.setattr(pg, "_consultar_eventos", _consulta)
    resp = _chamar("/painel-gerencial/exportar.csv", {"painel_gerencial"},
                   {"modulo": "PORTARIA", "grupo": "ENTRADAS", "categoria": "TERCEIRO"})
    assert resp.status_code == 200, resp.text
    assert visto["params"]["grupo_tipo0"] == "ENTRADA" and visto["params"]["categoria"] == "TERCEIRO"
    assert ";PORTARIA;ENTRADA;TERCEIRO;" in resp.content.decode("utf-8-sig")


# ─── 7 · Dentro agora (D2) ───────────────────────────────────────────────────

def test_dentro_agora_devolve_lista_e_total(painel_sem_banco):
    corpo = _chamar("/painel-gerencial/dentro-agora", {"painel_gerencial"}).json()
    assert corpo["total"] == len(corpo["dentro"]) == 1
    assert corpo["dentro"][0]["categoria"] == "TERCEIRO"


def test_dentro_agora_busca(painel_sem_banco):
    assert _chamar("/painel-gerencial/dentro-agora", {"painel_gerencial"}, {"busca": "tst1"}).json()["total"] == 1
    assert _chamar("/painel-gerencial/dentro-agora", {"painel_gerencial"}, {"busca": "zzz"}).json()["total"] == 0


def test_dentro_agora_csv(painel_sem_banco):
    resp = _chamar("/painel-gerencial/dentro-agora.csv", {"painel_gerencial"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xef\xbb\xbf")
    cabecalho, linha = resp.content.decode("utf-8-sig").splitlines()[:2]
    assert cabecalho == "Entrou em;Placa/Prefixo;Categoria;RE;Nome;Há quanto tempo;Detalhe"
    assert linha.startswith("26/09/2026 08:00;TST1A23;TERCEIRO;;Prestador Teste;")


@pytest.mark.parametrize("args,esperado", [
    (("2140", None, None, None), "RESERVADO"),
    ((None, "EMPRESA", "Fulano", None), "FROTA_APOIO"),
    ((None, "TERCEIRO", None, None), "TERCEIRO"),
    ((None, "PARTICULAR", None, "Empresa X"), "TERCEIRO"),
    ((None, None, "Fulano", None), "TERCEIRO"),
    ((None, "PARTICULAR", None, None), "FUNCIONARIO"),
    ((None, None, None, None), "FUNCIONARIO"),
])
def test_categoria_do_dentro_segue_o_case_da_view_046(args, esperado):
    assert pg.categoria_movimento(*args) == esperado


def test_permanencia():
    agora = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert pg.formatar_permanencia(datetime(2026, 9, 26, 11, 35, tzinfo=timezone.utc), agora) == "25 min"
    assert pg.formatar_permanencia(datetime(2026, 9, 26, 9, 5, tzinfo=timezone.utc), agora) == "2 h 55 min"


def test_tamanho_da_lista_de_dentro_igual_ao_contador():
    """🔴 Regra de ouro do D2, com a regra D18 de verdade (SQLite + ATTACH
    portaria, como test_portaria.py): o len() da lista = dentro_agora."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    from app.models.portaria import (
        EmpresaTerceira, MovimentoPortaria, PortariaLocal, PortariaSetor, VeiculoPortaria,
    )

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS portaria")

    Base.metadata.create_all(engine, tables=[
        Funcionario.__table__, PortariaLocal.__table__, PortariaSetor.__table__, EmpresaTerceira.__table__,
        VeiculoPortaria.__table__, MovimentoPortaria.__table__,
    ])
    autor = uuid4()
    agora = datetime.now(timezone.utc)

    def _mov(db, sentido, horas_atras, **campos):
        db.add(MovimentoPortaria(
            id=uuid4(), local_codigo="LEVES", sentido=sentido, momento=agora - timedelta(hours=horas_atras),
            data_referencia=date.today(), cadastrado=False, origem="MANUAL", registrado_por=autor, **campos,
        ))

    with Session(engine) as db:
        db.add(Funcionario(id=autor, re="70009", nome="Controlador Teste", status="ATIVO"))
        db.add(PortariaLocal(codigo="LEVES", nome="Portaria de leves", ordem=1, ativo=True))
        veiculo_terceiro = uuid4()
        veiculo_empresa = uuid4()
        db.add(VeiculoPortaria(id=veiculo_terceiro, propriedade="TERCEIRO", placa="TER1A11",
                               tipo="CARRO", situacao="AUTORIZADO"))
        db.add(VeiculoPortaria(id=veiculo_empresa, propriedade="EMPRESA", placa="EMP1A11",
                               tipo="CARRO", situacao="AUTORIZADO"))
        _mov(db, "ENTRADA", 1, placa_registrada="FUN1A11", re_registrado="70100", nome_registrado="Func Teste")
        _mov(db, "ENTRADA", 2, placa_registrada="TER1A11", veiculo_id=veiculo_terceiro)
        _mov(db, "ENTRADA", 3, placa_registrada="AVU1A11", terceiro_nome="Visita Teste")
        _mov(db, "ENTRADA", 5, placa_registrada="SAI1A11")                       # entrou e saiu
        _mov(db, "SAIDA", 4, placa_registrada="SAI1A11")
        _mov(db, "ENTRADA", 40, placa_registrada="VEL1A11")                      # > 36 h: sem saída
        _mov(db, "ENTRADA", 1, placa_registrada="EMP1A11", veiculo_id=veiculo_empresa)  # frota de apoio: fora (D18)
        _mov(db, "ENTRADA", 1, prefixo="2140")                                   # reservado: fora (D18)
        db.commit()

        lista = pg._listar_dentro_agora(db)
        assert len(lista) == pg._contar_dentro_agora(db) == 3
        assert {l["identificacao"]: l["categoria"] for l in lista} == {
            "FUN1A11": "FUNCIONARIO", "TER1A11": "TERCEIRO", "AVU1A11": "TERCEIRO",
        }
