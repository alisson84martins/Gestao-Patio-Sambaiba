"""Permissão de importação de escala passa a usar o RBAC (item 3, prompt de
15/08/2026 — _handoff-claude/SISTEMA-EM-PRODUCAO.md).

Antes, os 4 endpoints de app/routers/importacao.py checavam
`usuario.perfil` (sistema legado) via `_pode_importar()` — por isso
liberar alguém pela tela de Permissões não tinha efeito nenhum sobre
importar escala: o endpoint nunca consultava `funcao_permissao`/`permissao`.

Três níveis de prova:
  1. O endpoint real (não só `exige()` isolado, que
     test_seguranca_autorizacao.py já cobre) nega 403 pra quem não tem
     "escala" — é este que prova a trava.
  2. Estrutural: `_pode_importar` não existe mais, nenhum resquício de
     perfil legado no módulo.
  3. Integração: quem TEM "escala" com escrita/leitura passa; e o campo
     legado `importado_por` (FK pra `usuario.id`, nunca migrada) recebe o
     id do ESPELHO em `usuario`, nunca o id do Funcionario — são UUIDs
     diferentes mesmo pra mesma pessoa (funcionario.id ≠ usuario.id;
     ver `_resolver_usuario_legado()` em importacao.py e
     `get_current_user()` em app/core/deps.py).
"""
import typing
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.cadastro import Funcionario
from app.models.enums import PerfilUsuarioEnum, StatusImportacaoEnum
from app.models.operacoes import ImportacaoEscala
from app.models.pessoas import Usuario
from app.routers import importacao as importacao_mod


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


# ─── 1 — o ENDPOINT real nega 403 pra quem não tem "escala" ───────────────────


class _DBSemAcessoAEscala:
    """Funcionario resolve normalmente (get_current_funcionario), mas
    vw_acesso_efetivo não devolve linha nenhuma pro recurso "escala" —
    exatamente o cenário de alguém sem essa permissão."""

    def __init__(self, funcionario):
        self._funcionario = funcionario

    def get(self, model, id_):
        if model is Funcionario and id_ == self._funcionario.id:
            return self._funcionario
        return None

    def execute(self, stmt, params=None, *a, **k):
        class _R:
            def scalar_one_or_none(self):
                return None  # sem UsuarioLogin — não bloqueia get_current_funcionario

            def fetchone(self):
                return None  # vw_acesso_efetivo: sem linha pro recurso "escala"
        return _R()


def test_get_importacoes_nega_403_para_quem_nao_tem_escala():
    """🔴 É este que prova a trava — antes da correção, este endpoint usava
    OperadorOuAdmin (perfil legado, `usuario.perfil`) e nunca consultava o
    RBAC; um Funcionario sem "escala" não seria barrado por aqui."""
    func = Funcionario(id=uuid4(), re="70001", nome="Sem Acesso a Escala")
    token = create_access_token(subject=func.id)

    app.dependency_overrides[get_db] = lambda: _DBSemAcessoAEscala(func)
    try:
        resp = TestClient(app).get("/importacoes", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_post_importacoes_escala_nega_403_para_quem_nao_tem_escala_escrever():
    func = Funcionario(id=uuid4(), re="70004", nome="Sem Acesso a Escala")
    token = create_access_token(subject=func.id)

    app.dependency_overrides[get_db] = lambda: _DBSemAcessoAEscala(func)
    try:
        resp = TestClient(app).post(
            "/importacoes/escala",
            headers={"Authorization": f"Bearer {token}"},
            files={"arquivo": ("escala.xlsx", b"conteudo", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"data_escala": "2026-08-15"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─── 2 — estrutural: nada de perfil legado sobrou no módulo ───────────────────


def test_importacao_nao_usa_mais_perfil_legado():
    assert not hasattr(importacao_mod, "_pode_importar")
    codigo_fonte = open(importacao_mod.__file__, encoding="utf-8").read()
    assert "PerfilUsuarioEnum" not in codigo_fonte
    assert "OperadorOuAdmin" not in codigo_fonte
    assert "CurrentUser" not in codigo_fonte
    assert "usuario.perfil" not in codigo_fonte
    assert 'exige("escala"' in codigo_fonte


# ─── 3 — quem TEM escala importa/lê; importado_por resolve o espelho legado ───

_TABELAS = [Funcionario.__table__, Usuario.__table__, ImportacaoEscala.__table__]


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine, tables=_TABELAS)

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    escrita_dep = _dependency_de(importacao_mod.EscritaEscala)
    leitura_dep = _dependency_de(importacao_mod.LeituraEscala)
    app.dependency_overrides[get_db] = _get_db_teste

    yield {
        "engine": engine, "http": TestClient(app),
        "escrita_dep": escrita_dep, "leitura_dep": leitura_dep,
    }

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(leitura_dep, None)


def test_quem_tem_escala_escrever_importa_e_resolve_o_espelho_legado(ambiente, monkeypatch):
    """Daniel Brauna (RE liberado individualmente em "escala" pela tela de
    Permissões, cenário real que motivou este item) consegue importar."""
    engine = ambiente["engine"]
    funcionario_id = uuid4()
    usuario_espelho_id = uuid4()
    with Session(engine) as db:
        db.add(Funcionario(id=funcionario_id, re="70002", nome="Daniel Brauna"))
        db.add(Usuario(
            id=usuario_espelho_id, re="70002", nome="Daniel Brauna",
            senha_hash="hash-fake-de-teste", perfil=PerfilUsuarioEnum.OPERADOR_PATIO,
        ))
        db.commit()

    # Objeto novo, nunca vinculado a sessão nenhuma — o que a dependency
    # override devolve não pode ser a instância que acabou de ser
    # commitada (expire_on_commit expira os atributos e o acesso
    # posterior, com a sessão já fechada, estoura DetachedInstanceError).
    funcionario = Funcionario(id=funcionario_id, re="70002", nome="Daniel Brauna")

    capturado = {}

    def _fake_importar_escala(**kwargs):
        capturado.update(kwargs)
        return (
            ImportacaoEscala(
                id=uuid4(), arquivo_nome=kwargs["arquivo_nome"], data_escala=kwargs["data_escala"],
                total_registros=1, registros_sucesso=1, registros_erro=0,
                status=StatusImportacaoEnum.SUCESSO, importado_por=kwargs["importado_por_id"],
                importado_em=datetime.now(timezone.utc),
            ),
            [], 0, 0,
        )

    monkeypatch.setattr(importacao_mod, "importar_escala", _fake_importar_escala)
    ambiente["http"].app.dependency_overrides[ambiente["escrita_dep"]] = lambda: funcionario

    resp = ambiente["http"].post(
        "/importacoes/escala",
        files={"arquivo": ("escala.xlsx", b"conteudo fake de teste", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"data_escala": "2026-08-15"},
    )
    assert resp.status_code == 201, resp.text
    # 🔴 O que decide se a FK quebra em produção (Postgres): importado_por
    # precisa ser o id do ESPELHO em `usuario`, nunca o id do Funcionario.
    assert capturado["importado_por_id"] == usuario_espelho_id
    assert capturado["importado_por_id"] != funcionario_id


def test_quem_tem_escala_escrever_mas_sem_espelho_em_usuario_importa_sem_quebrar(ambiente, monkeypatch):
    """RE novo, cadastrado só pelo fluxo RBAC, sem nunca ter passado pelo
    shim de espelho — importado_por precisa cair em None, nunca estourar."""
    funcionario_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=funcionario_id, re="70005", nome="Sem Espelho Legado"))
        db.commit()
    funcionario = Funcionario(id=funcionario_id, re="70005", nome="Sem Espelho Legado")

    capturado = {}

    def _fake_importar_escala(**kwargs):
        capturado.update(kwargs)
        return (
            ImportacaoEscala(
                id=uuid4(), arquivo_nome=kwargs["arquivo_nome"], data_escala=kwargs["data_escala"],
                total_registros=0, registros_sucesso=0, registros_erro=0,
                status=StatusImportacaoEnum.SUCESSO, importado_por=kwargs["importado_por_id"],
                importado_em=datetime.now(timezone.utc),
            ),
            [], 0, 0,
        )

    monkeypatch.setattr(importacao_mod, "importar_escala", _fake_importar_escala)
    ambiente["http"].app.dependency_overrides[ambiente["escrita_dep"]] = lambda: funcionario

    resp = ambiente["http"].post(
        "/importacoes/escala",
        files={"arquivo": ("escala.xlsx", b"conteudo", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"data_escala": "2026-08-15"},
    )
    assert resp.status_code == 201, resp.text
    assert capturado["importado_por_id"] is None


def test_quem_tem_escala_ler_lista_importacoes(ambiente):
    """Leitura segue a mesma regra da escrita — GET /importacoes atrás de
    exige("escala")."""
    funcionario = Funcionario(id=uuid4(), re="70003", nome="Leitor de Escala")
    ambiente["http"].app.dependency_overrides[ambiente["leitura_dep"]] = lambda: funcionario

    resp = ambiente["http"].get("/importacoes")
    assert resp.status_code == 200, resp.text
