"""Bloco B item 7 (fechamento, 11/08/2026) — trilha de auditoria mínima.

Ver database/migrations/021-log-acesso.sql para o porquê, o volume
esperado e o escopo deliberadamente mínimo (não cobre 403 de autoria em
ocorrência nem os 12 routers legados).
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base, get_db
from app.main import app
from app.models.auditoria import LogAcesso
from app.models.cadastro import Funcionario
from app.services.auditoria import ip_do_request, registrar_log_acesso

from tests.test_seguranca_autenticacao import _FakeDBSemUsuario


# ─── registrar_log_acesso — mecânica básica ────────────────────────────────


def _sessao_sqlite() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[LogAcesso.__table__])
    return Session(engine)


def test_registrar_log_acesso_grava_e_e_consultavel():
    db = _sessao_sqlite()
    fid, oid = uuid4(), uuid4()

    registrar_log_acesso(
        db, "LEITURA_OCORRENCIA",
        funcionario_id=fid, ocorrencia_id=oid, ip="10.0.0.1",
    )

    linha = db.query(LogAcesso).one()
    assert linha.evento == "LEITURA_OCORRENCIA"
    assert linha.funcionario_id == fid
    assert linha.ocorrencia_id == oid
    assert linha.ip == "10.0.0.1"
    assert linha.criado_em is not None


def test_registrar_log_acesso_nunca_lanca_mesmo_com_sessao_quebrada():
    """Contrato central do módulo: auditoria não pode derrubar a ação que
    está tentando auditar. Simula banco fora do ar (add falha) E rollback
    falhando também (pior caso) — mesmo assim, nada escapa."""
    class _SessaoQuebrada:
        def add(self, obj):
            raise RuntimeError("banco fora do ar")

        def commit(self):
            pass

        def rollback(self):
            raise RuntimeError("rollback também falhou")

    registrar_log_acesso(_SessaoQuebrada(), "LOGIN_FALHA", re_tentativa="99999")


def test_ip_do_request_none_quando_sem_client_ou_sem_request():
    class _ReqSemClient:
        client = None

    assert ip_do_request(_ReqSemClient()) is None
    assert ip_do_request(None) is None


def test_log_acesso_nunca_tem_coluna_de_dado_pessoal_de_terceiro():
    """Trava estrutural: a regra do item é 'nenhum dado pessoal no log —
    só o id da ocorrência'. Se algum dia alguém adicionar uma coluna tipo
    nome_vitima/cpf na tabela, este teste avisa."""
    colunas = {c.name for c in LogAcesso.__table__.columns}
    proibidas = {"nome_vitima", "cpf_vitima", "cpf", "rg", "endereco", "nome", "senha", "senha_hash", "telefone"}
    assert colunas.isdisjoint(proibidas), colunas & proibidas


# ─── Integração — os três hooks disparam de verdade ────────────────────────


def test_login_falha_e_registrado_em_log_acesso(monkeypatch):
    from app.routers import auth as auth_mod

    chamadas = []
    monkeypatch.setattr(
        auth_mod, "registrar_log_acesso",
        lambda db, evento, **kw: chamadas.append((evento, kw)),
    )

    app.dependency_overrides[get_db] = lambda: _FakeDBSemUsuario()
    try:
        resp = TestClient(app).post("/auth/login", json={"re": "40001", "senha": "errada"})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert len(chamadas) == 1
    evento, kw = chamadas[0]
    assert evento == "LOGIN_FALHA"
    assert kw["re_tentativa"] == "40001"
    # A senha NUNCA é passada pra auditoria — nem por acidente num kwarg solto.
    assert "senha" not in kw and "40001" != "errada"


def test_negado_403_e_registrado_em_log_acesso(monkeypatch):
    """Mesmo cenário de test_exige_recusa_quando_view_nao_confirma_acesso
    (test_seguranca_autorizacao.py), mas confirmando que a negativa
    também vira uma chamada de auditoria — não só o 403 em si."""
    from app.core import deps as deps_mod
    from app.core.security import create_access_token

    chamadas = []
    monkeypatch.setattr(
        deps_mod, "registrar_log_acesso",
        lambda db, evento, **kw: chamadas.append((evento, kw)),
    )

    func = Funcionario(id=uuid4(), re="30001", nome="Motorista")
    token = create_access_token(subject=func.id)

    class _DB:
        def get(self, model, id_):
            if model is Funcionario and id_ == func.id:
                return func
            return None

        def execute(self, stmt, params=None, *args, **kwargs):
            class _R:
                def scalar_one_or_none(self):
                    return None

                def fetchone(self):
                    return None  # vw_acesso_efetivo: sem linha pro recurso
            return _R()

    checker = deps_mod.exige("usuarios", escrever=True)
    with pytest.raises(HTTPException) as exc:
        checker(None, token, _DB())
    assert exc.value.status_code == 403

    assert len(chamadas) == 1
    evento, kw = chamadas[0]
    assert evento == "NEGADO_403"
    assert kw["funcionario_id"] == func.id
    assert kw["recurso"] == "usuarios"


def test_leitura_ocorrencia_e_registrada_em_log_acesso(monkeypatch):
    from app.routers import ocorrencias as router_mod
    from app.models.ocorrencia import Ocorrencia, TipoOcorrencia
    from datetime import datetime, timezone
    import typing

    def _dependency_de(annotated_type):
        for arg in typing.get_args(annotated_type)[1:]:
            dependency = getattr(arg, "dependency", None)
            if dependency is not None:
                return dependency
        raise RuntimeError("Depends não encontrado")

    chamadas = []
    monkeypatch.setattr(
        router_mod, "registrar_log_acesso",
        lambda db, evento, **kw: chamadas.append((evento, kw)),
    )

    autor = Funcionario(id=uuid4(), re="40001", nome="Coordenador Autor")
    tipo = TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )
    oc = Ocorrencia(
        id=uuid4(), numero=1, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia="2026-08-11", hora_ocorrencia="10:00", prefixo="1234",
        cidade="São Paulo", via_urbana=False, via_rodoviaria=False,
        area_interna=False, corredor=False, tem_fotos=False, monitoramento=False,
        ocorrencia_policial=False, houve_policia_tecnica=False,
        registrado_por=autor.id, criado_em=datetime.now(timezone.utc),
    )
    oc.tipo_ocorrencia = tipo
    oc.veiculos_terceiro = []
    oc.avarias = []
    oc.testemunhas = []
    oc.vitimas = []
    oc.autoridades = []
    oc.anexos = []

    class _DB:
        def get(self, model, id_):
            if model is Ocorrencia and id_ == oc.id:
                return oc
            if model is Funcionario and id_ == autor.id:
                return autor
            return None

        def execute(self, stmt, params=None, *args, **kwargs):
            class _R:
                def unique(self_r):
                    return self_r

                def scalar_one_or_none(self_r):
                    return oc

                def first(self_r):
                    return (1,)  # _ve_todas_ocorrencias — irrelevante aqui, autor lê a própria
            return _R()

    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    app.dependency_overrides[leitura_dep] = lambda: autor
    app.dependency_overrides[get_db] = lambda: _DB()
    try:
        resp = TestClient(app).get(f"/ocorrencias/{oc.id}")
    finally:
        app.dependency_overrides.pop(leitura_dep, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200, resp.text
    assert len(chamadas) == 1
    evento, kw = chamadas[0]
    assert evento == "LEITURA_OCORRENCIA"
    assert kw["funcionario_id"] == autor.id
    assert kw["ocorrencia_id"] == oc.id
