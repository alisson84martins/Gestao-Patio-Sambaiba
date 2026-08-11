"""Bloco B (11/08/2026) — visibilidade de ocorrência por autoria.

Decisão do Alisson: Coordenador de Tráfego só vê e edita as PRÓPRIAS
ocorrências; ADMIN, Encarregado e gerência veem todas. Até esta mudança
não existia controle de autoria NENHUM na leitura — só em PATCH/DELETE
(ver test_ocorrencias_autoria.py).

Duas estratégias neste arquivo, por necessidade:

1. **detalhar(), mensagem-sinistro, baixar_anexo, criar()** — SQLite real
   (ATTACH DATABASE, mesmo padrão de test_ocorrencias_colecoes_filhas.py).
   Passam pelo ORM, então rodam de verdade contra tabela real.

2. **listar()** — NÃO dá pra rodar contra SQLite: a query usa
   `vw_ocorrencia_resumo` com `ILIKE` (`_FILTROS_SQL`), sintaxe exclusiva
   de Postgres que o SQLite nem reconhece (`OperationalError: near "ILIKE"`
   — confirmado tentando; não é suposição). Testado com FakeSession,
   inspecionando os PARÂMETROS que listar() manda pro SQL (abordagem
   caixa-branca) — confirma que o parâmetro coordenador_re é forçado
   corretamente, não que a query em si roda certo contra dado real. A
   query em si só é validável contra Postgres — registrado em
   EXECUCAO-2026-08-11.md como limite desta rodada, mesma natureza da
   limitação já documentada no relatório de segurança de 10/08 pra
   qualquer SQL sobre `vw_*`.

Dados fictícios — nunca RE, CPF ou nome real.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date, datetime, time, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.routers import ocorrencias as router_mod

# _ve_todas_ocorrencias()/_eh_admin() usam text() cru com um UUID Python
# como parâmetro — funciona em produção (psycopg3 sabe adaptar), mas o
# driver sqlite3 puro não sabe. SQLAlchemy, ao inserir via ORM numa coluna
# PG_UUID(as_uuid=True) rodando sobre SQLite, serializa como .hex (32
# chars sem hífen) — o adapter abaixo faz o driver cru bindar do mesmo
# jeito, senão a comparação nunca bate mesmo com o valor certo.
sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

# _carregar_completa() (usada por detalhar()/mensagem-sinistro) faz
# joinedload/selectinload em todas as coleções filhas — precisam existir
# no schema de teste mesmo vazias, senão a query real quebra por tabela
# ausente. Mesmo conjunto de test_ocorrencias_colecoes_filhas.py.
_TABELAS = [
    Funcionario.__table__, Funcao.__table__, FuncionarioFuncao.__table__,
    TipoOcorrencia.__table__, OrgaoAutoridade.__table__, Ocorrencia.__table__,
    OcorrenciaAnalise.__table__, OcorrenciaVeiculoTerceiro.__table__, OcorrenciaAvaria.__table__,
    OcorrenciaVitima.__table__, OcorrenciaTestemunha.__table__, OcorrenciaAutoridade.__table__,
    OcorrenciaAnexo.__table__,
]


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def mundo():
    """Monta: 2 coordenadores (A e B), 1 encarregado, 1 gerente, 1 admin,
    3 ocorrências (uma do A, uma do B, uma sem autor)."""
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=_TABELAS)

    tipo = TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )
    coord_a = Funcionario(id=uuid4(), re="50001", nome="Coordenador A")
    coord_b = Funcionario(id=uuid4(), re="50002", nome="Coordenador B")
    encarregado = Funcionario(id=uuid4(), re="50003", nome="Encarregado Teste")
    gerente = Funcionario(id=uuid4(), re="50004", nome="Gerente Teste")
    admin = Funcionario(id=uuid4(), re="5598", nome="Admin Teste")

    funcao_encarregado = Funcao(id=uuid4(), codigo="ENCARREGADO", nome="Encarregado", categoria="OPERACIONAL", nivel=3, ativo=True)
    funcao_gerente = Funcao(id=uuid4(), codigo="GERENTE_GERAL", nome="Gerente Geral", categoria="ADMINISTRATIVO", nivel=1, ativo=True)
    funcao_admin = Funcao(id=uuid4(), codigo="ADMIN", nome="Administrador", categoria="ADMINISTRATIVO", nivel=1, ativo=True)

    oc_a = Ocorrencia(
        id=uuid4(), numero=1, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia=date(2026, 8, 1), hora_ocorrencia=time(10, 0), prefixo="1111",
        cidade="São Paulo", via_urbana=False, via_rodoviaria=False, area_interna=False,
        corredor=False, tem_fotos=False, monitoramento=False, ocorrencia_policial=False,
        houve_policia_tecnica=False, registrado_por=coord_a.id,
        criado_em=datetime.now(timezone.utc),
    )
    oc_b = Ocorrencia(
        id=uuid4(), numero=2, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia=date(2026, 8, 2), hora_ocorrencia=time(11, 0), prefixo="2222",
        cidade="São Paulo", via_urbana=False, via_rodoviaria=False, area_interna=False,
        corredor=False, tem_fotos=False, monitoramento=False, ocorrencia_policial=False,
        houve_policia_tecnica=False, registrado_por=coord_b.id,
        criado_em=datetime.now(timezone.utc),
    )
    oc_sem_autor = Ocorrencia(
        id=uuid4(), numero=3, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia=date(2026, 7, 1), hora_ocorrencia=time(9, 0), prefixo="3333",
        cidade="São Paulo", via_urbana=False, via_rodoviaria=False, area_interna=False,
        corredor=False, tem_fotos=False, monitoramento=False, ocorrencia_policial=False,
        houve_policia_tecnica=False, registrado_por=None,
        criado_em=datetime.now(timezone.utc),
    )

    # expire_on_commit=False: os objetos (coord_a, oc_a, tipo...) são
    # devolvidos pela fixture e lidos de novo nos testes DEPOIS do commit
    # e do fechamento desta sessão — sem isso, o SQLAlchemy expira os
    # atributos no commit e o acesso pós-sessão explode com
    # DetachedInstanceError.
    with Session(engine, expire_on_commit=False) as setup:
        for f in (coord_a, coord_b, encarregado, gerente, admin):
            setup.add(f)
        for fn in (funcao_encarregado, funcao_gerente, funcao_admin):
            setup.add(fn)
        setup.add(tipo)
        for oc in (oc_a, oc_b, oc_sem_autor):
            setup.add(oc)
        setup.flush()
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=encarregado.id, funcao_id=funcao_encarregado.id, principal=True, data_inicio=date(2026, 1, 1), ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=gerente.id, funcao_id=funcao_gerente.id, principal=True, data_inicio=date(2026, 1, 1), ativo=True))
        setup.add(FuncionarioFuncao(id=uuid4(), funcionario_id=admin.id, funcao_id=funcao_admin.id, principal=True, data_inicio=date(2026, 1, 1), ativo=True))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    app.dependency_overrides[get_db] = _get_db_teste

    yield {
        "http": TestClient(app),
        "leitura_dep": leitura_dep,
        "escrita_dep": escrita_dep,
        "coord_a": coord_a, "coord_b": coord_b,
        "encarregado": encarregado, "gerente": gerente, "admin": admin,
        "oc_a": oc_a, "oc_b": oc_b, "oc_sem_autor": oc_sem_autor,
        "tipo": tipo,
    }

    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _como(m, usuario):
    m["http"].app.dependency_overrides[m["leitura_dep"]] = lambda: usuario
    m["http"].app.dependency_overrides[m["escrita_dep"]] = lambda: usuario


# ─── 8.6.1 — Coordenador lista → não vê a de outro coordenador ────────────────
# Caixa-branca com FakeSession (ver docstring do arquivo — listar() usa SQL
# só de Postgres, ILIKE, não roda em SQLite). Confirma o parâmetro que
# listar() manda pro SQL, não o resultado da query em si.


class _FakeResultListar:
    def __init__(self, valor):
        self._valor = valor

    def scalar_one(self):
        return self._valor

    def mappings(self):
        return self

    def all(self):
        return []


class _DBListar:
    """execute() serve tanto o COUNT quanto o SELECT de listar() — nenhum
    dos dois importa pro que este teste verifica (os PARÂMETROS ligados),
    então devolve sempre a mesma coisa vazia. Grava os params de cada
    chamada em self.chamadas pra inspeção."""

    def __init__(self, funcionario_veem_todas: set):
        self._veem_todas = funcionario_veem_todas
        self.chamadas = []

    def execute(self, stmt, params=None, *args, **kwargs):
        if hasattr(stmt, "text") and "funcionario_funcao" in stmt.text:
            fid = (params or {}).get("fid")
            return _FakeResultAdmin(fid in self._veem_todas)
        self.chamadas.append(dict(params or {}))
        return _FakeResultListar(0)


class _FakeResultAdmin:
    def __init__(self, e_admin: bool):
        self._linha = (1,) if e_admin else None

    def first(self):
        return self._linha


# listar() usa Query(...) como default de vários parâmetros — chamado
# direto (fora do despacho do FastAPI), o default literal é o próprio
# objeto Query, não o valor que ele resolveria numa request sem
# querystring. Por isso os três testes abaixo passam explicitamente tudo
# que tem Query(...) como default (data_inicio, data_fim, tipo, skip,
# limit), simulando "nenhum filtro na URL".


def test_listar_forca_coordenador_re_pro_proprio_re_de_quem_nao_ve_todas():
    coord = Funcionario(id=uuid4(), re="50001", nome="Coordenador A")
    db = _DBListar(funcionario_veem_todas=set())

    router_mod.listar(
        usuario=coord, db=db, data_inicio=None, data_fim=None, tipo=None,
        coordenador_re="50002",  # tenta ver o de outro
        skip=0, limit=50,
    )

    assert db.chamadas, "listar() não chamou db.execute com os filtros"
    assert db.chamadas[0]["coordenador_re"] == "50001", (
        "coordenador_re deveria ser forçado pro próprio RE, ignorando o "
        f"que o cliente mandou — veio {db.chamadas[0]['coordenador_re']!r}"
    )


def test_listar_nao_filtra_por_coordenador_re_por_padrao_pra_quem_ve_todas():
    admin = Funcionario(id=uuid4(), re="5598", nome="Admin Teste")
    db = _DBListar(funcionario_veem_todas={admin.id})

    router_mod.listar(
        usuario=admin, db=db, data_inicio=None, data_fim=None, tipo=None,
        coordenador_re=None, skip=0, limit=50,
    )

    assert db.chamadas[0]["coordenador_re"] is None


def test_listar_respeita_coordenador_re_explicito_de_quem_ve_todas():
    gerente = Funcionario(id=uuid4(), re="50004", nome="Gerente Teste")
    db = _DBListar(funcionario_veem_todas={gerente.id})

    router_mod.listar(
        usuario=gerente, db=db, data_inicio=None, data_fim=None, tipo=None,
        coordenador_re="50001", skip=0, limit=50,
    )

    assert db.chamadas[0]["coordenador_re"] == "50001"


# ─── 8.6.2 — Coordenador GET /ocorrencias/{id_do_outro} → 403 ─────────────────


def test_coordenador_nao_ve_detalhe_de_ocorrencia_alheia(mundo):
    _como(mundo, mundo["coord_a"])
    resp = mundo["http"].get(f"/ocorrencias/{mundo['oc_b'].id}")
    assert resp.status_code == 403, resp.text


def test_coordenador_ve_o_proprio_detalhe(mundo):
    _como(mundo, mundo["coord_a"])
    resp = mundo["http"].get(f"/ocorrencias/{mundo['oc_a'].id}")
    assert resp.status_code == 200, resp.text


# ─── 8.6.3 — Encarregado lista → vê as duas ────────────────────────────────────
# Não duplicado aqui como integração de listar(): "encarregado vê tudo" é
# exatamente _ve_todas_ocorrencias() devolvendo True, já provado contra
# SQLite real pelos testes de detalhe/sinistro abaixo (mesma função,
# usada pelos dois endpoints) — e o efeito dela em listar() especificamente
# é o que os três test_listar_* acima cobrem (o parâmetro coordenador_re).


# ─── 8.6.4 — Encarregado POST /ocorrencias → 403 (item 1/7: migration 020) ────


def test_encarregado_nao_escreve_ocorrencia():
    """Confirma a garantia de código do item 7: o gate real de escrita é
    exige("ocorrencia", escrever=True) (EscritaOcorrencia). Testado direto
    na dependency — o dado (ENCARREGADO perder pode_escrever em
    "ocorrencia") é da migration 020/seed 08, já confirmado por leitura no
    Item 1; aqui provamos que o CÓDIGO barra corretamente esse estado,
    sem precisar de Postgres com a view vw_acesso_efetivo rodando."""
    from app.core.deps import exige
    from app.core.security import create_access_token

    encarregado = Funcionario(id=uuid4(), re="50003", nome="Encarregado Teste")
    token = create_access_token(subject=encarregado.id)

    class _DB:
        def get(self, model, id_):
            if model is Funcionario and id_ == encarregado.id:
                return encarregado
            return None

        def execute(self, stmt, params=None, *args, **kwargs):
            class _R:
                def scalar_one_or_none(self):
                    return None
                def fetchone(self):
                    return None  # sem linha pode_escrever=True em "ocorrencia" — pós migration 020
            return _R()

    checker = exige("ocorrencia", escrever=True)
    with pytest.raises(HTTPException) as exc:
        checker(token, _DB())
    assert exc.value.status_code == 403


# ─── 8.6.5 — Gerente lista com ?coordenador_re= → filtra ──────────────────────
# Ver test_listar_respeita_coordenador_re_explicito_de_quem_ve_todas acima.

# ─── 8.6.6 — ADMIN vê tudo ─────────────────────────────────────────────────────
# ADMIN está em _FUNCOES_VEEM_TODAS_OCORRENCIAS — mesma cobertura da nota
# do 8.6.3 acima (a função é compartilhada por listar() e detalhar()).


# ─── 8.6.7 — registrado_por nulo: coordenador não vê; encarregado vê ──────────


def test_ocorrencia_sem_autor_nao_aparece_pro_coordenador(mundo):
    """A metade "não aparece na lista" deste cenário é semântica de SQL
    (coordenador_re = 'x' contra uma linha com coordenador_re NULL avalia
    NULL, não TRUE — por isso a linha nunca entra no filtro do
    coordenador) — não executável aqui (ver docstring do arquivo sobre
    ILIKE/SQLite). A metade que RODA de verdade é o detalhe, via
    _exige_pode_ver()/_ve_todas_ocorrencias(), mesmo mecanismo."""
    _como(mundo, mundo["coord_a"])
    detalhe = mundo["http"].get(f"/ocorrencias/{mundo['oc_sem_autor'].id}")
    assert detalhe.status_code == 403, detalhe.text


def test_ocorrencia_sem_autor_aparece_pro_encarregado(mundo):
    _como(mundo, mundo["encarregado"])
    detalhe = mundo["http"].get(f"/ocorrencias/{mundo['oc_sem_autor'].id}")
    assert detalhe.status_code == 200, detalhe.text


# ─── 8.6.8 — coordenador gera sinistro de ocorrência alheia → 403 ─────────────
# ("imprime" é renderização client-side sobre o detalhe — já coberto pelo
# 403 do detalhe, ver 8.6.2; mensagem-sinistro é rota própria e não herda
# a trava de detalhar() automaticamente, por isso tem teste dedicado.)


def test_coordenador_nao_gera_sinistro_de_ocorrencia_alheia(mundo):
    _como(mundo, mundo["coord_a"])
    resp = mundo["http"].get(f"/ocorrencias/{mundo['oc_b'].id}/mensagem-sinistro")
    assert resp.status_code == 403, resp.text


def test_coordenador_gera_sinistro_da_propria(mundo):
    _como(mundo, mundo["coord_a"])
    resp = mundo["http"].get(f"/ocorrencias/{mundo['oc_a'].id}/mensagem-sinistro")
    assert resp.status_code == 200, resp.text


def test_coordenador_nao_baixa_anexo_de_ocorrencia_alheia(mundo):
    """Achado ao aplicar o item 8.3 (fora da lista original de 8 cenários):
    baixar_anexo() tinha a mesma lacuna do detalhe. anexo_id aleatório é
    suficiente aqui — o 403 tem que sair ANTES de sequer procurar o anexo."""
    _como(mundo, mundo["coord_a"])
    resp = mundo["http"].get(f"/ocorrencias/{mundo['oc_b'].id}/anexos/{uuid4()}/arquivo")
    assert resp.status_code == 403, resp.text


# ─── Item 7 — COORDENADOR_TRAFEGO criando a própria → 201 ─────────────────────
# criar() não tem regra de autoria (não há ocorrência anterior pra checar) —
# este teste é regressão simples: confirma que nada em Bloco B quebrou o
# caminho de criação. numero usa Identity() (sequência Postgres), que o
# SQLite não popula sozinho — por isso FakeSession aqui, não o SQLite real
# de `mundo` (mesmo padrão de test_ocorrencias_smoke.py).


class _FakeSessionCriar:
    def __init__(self, tipo):
        self._tipo = tipo

    def get(self, model, id_):
        if model is TipoOcorrencia and id_ == self._tipo.id:
            return self._tipo
        return None

    def add(self, obj):
        self._ocorrencia = obj

    def commit(self):
        pass

    def refresh(self, obj):
        if obj.id is None:
            obj.id = uuid4()
        if obj.numero is None:
            obj.numero = 1
        if obj.criado_em is None:
            obj.criado_em = datetime.now(timezone.utc)


def test_coordenador_cria_a_propria_ocorrencia():
    coord = Funcionario(id=uuid4(), re="50001", nome="Coordenador A")
    tipo = TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )

    resp = router_mod.criar(
        payload=router_mod.OcorrenciaCreate(
            tipo_ocorrencia_id=tipo.id, data_ocorrencia="2026-08-05",
            hora_ocorrencia="15:00", prefixo="4444",
        ),
        usuario=coord,
        db=_FakeSessionCriar(tipo),
    )

    assert resp.status == "RASCUNHO"
    assert resp.registrado_por == coord.id
