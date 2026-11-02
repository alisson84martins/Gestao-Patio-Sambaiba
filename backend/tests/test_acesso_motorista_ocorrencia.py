"""Item 3 — acesso automático do condutor ao FINALIZAR a ocorrência
(chave AUTO_ACESSO_MOTORISTA_OCORRENCIA).

⛔ Nenhum dado pessoal real — ver tests/ambiente_acesso.py.
"""
import typing
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao, UsuarioLogin
from app.models.ocorrencia import Ocorrencia
from app.models.pessoas import Usuario
from app.routers import ocorrencias as router_mod
from tests.ambiente_acesso import CPF_A, criar_engine, inserir_ocorrencia, login_de

_AUTOR = Funcionario(id=uuid4(), re="41000", nome="Coordenador Teste")


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ambiente(monkeypatch):
    engine = criar_engine()
    with Session(engine) as db:
        db.add(Funcionario(id=_AUTOR.id, re=_AUTOR.re, nome=_AUTOR.nome, status="ATIVO"))
        db.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    escrita = _dependency_de(router_mod.EscritaOcorrencia)
    app.dependency_overrides[escrita] = lambda: _AUTOR
    app.dependency_overrides[get_db] = _get_db_teste

    chave = {"ligada": False}
    monkeypatch.setattr(
        router_mod, "get_settings",
        lambda: SimpleNamespace(auto_acesso_motorista_ocorrencia=chave["ligada"]),
    )
    yield {"http": TestClient(app), "engine": engine, "chave": chave}

    app.dependency_overrides.pop(escrita, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _finalizar(amb, oc_id):
    r = amb["http"].post(f"/ocorrencias/{oc_id}/finalizar")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FINALIZADA"
    return r


def _status(engine, oc_id):
    with Session(engine) as db:
        return db.get(Ocorrencia, oc_id).status


def test_chave_desligada_finalizar_nao_cria_nada(ambiente):
    oc = inserir_ocorrencia(ambiente["engine"], re="50001", nome="Joaquim Pereira",
                            cpf=CPF_A, registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)
    assert login_de(ambiente["engine"], "50001") is None
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(Funcionario).where(Funcionario.re == "50001")).first() is None


def test_chave_ligada_cria_login_provisorio_funcao_motorista_e_espelho(ambiente):
    ambiente["chave"]["ligada"] = True
    oc = inserir_ocorrencia(ambiente["engine"], re="50002", nome="Joaquim Pereira",
                            cpf="529.982.247-25", registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)

    login = login_de(ambiente["engine"], "50002")
    assert login is not None and login.politica_senha == "PROVISORIA"
    assert login.criado_por == _AUTOR.id
    with Session(ambiente["engine"]) as db:
        codigos = db.execute(
            select(Funcao.codigo).join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(FuncionarioFuncao.funcionario_id == login.funcionario_id)
        ).scalars().all()
        assert codigos == ["MOTORISTA"]
        assert db.execute(select(Usuario).where(Usuario.re == "50002")).scalar_one().ativo


def test_chave_ligada_cpf_invalido_finaliza_sem_criar(ambiente):
    ambiente["chave"]["ligada"] = True
    oc = inserir_ocorrencia(ambiente["engine"], re="50003", cpf="52998224724",
                            registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)
    assert _status(ambiente["engine"], oc) == "FINALIZADA"
    assert login_de(ambiente["engine"], "50003") is None


def test_excecao_no_servico_nao_impede_finalizacao(ambiente, monkeypatch):
    ambiente["chave"]["ligada"] = True

    def _explode(*a, **k):
        raise RuntimeError("falha simulada no serviço")

    monkeypatch.setattr(router_mod, "criar_acesso", _explode)
    oc = inserir_ocorrencia(ambiente["engine"], re="50004", cpf=CPF_A, registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)
    assert _status(ambiente["engine"], oc) == "FINALIZADA"
    assert login_de(ambiente["engine"], "50004") is None


def test_falha_de_banco_no_meio_do_servico_desfaz_so_o_acesso(ambiente, monkeypatch):
    """Exceção DEPOIS de já ter gravado funcionário/login na sessão: o
    savepoint desfaz o acesso pela metade, a finalização fica."""
    ambiente["chave"]["ligada"] = True
    from app.services import acesso_motorista as svc

    def _explode_no_espelho(*a, **k):
        raise RuntimeError("falha simulada no espelho")

    monkeypatch.setattr(svc, "criar_ou_atualizar_espelho_usuario", _explode_no_espelho)
    oc = inserir_ocorrencia(ambiente["engine"], re="50005", cpf=CPF_A, registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)
    assert _status(ambiente["engine"], oc) == "FINALIZADA"
    with Session(ambiente["engine"]) as db:
        assert db.scalar(select(func.count()).select_from(UsuarioLogin)) == 0
        assert db.execute(select(Funcionario).where(Funcionario.re == "50005")).first() is None


def test_ocorrencia_sem_condutor_re_finaliza_normalmente(ambiente):
    ambiente["chave"]["ligada"] = True
    oc = inserir_ocorrencia(ambiente["engine"], re=None, registrado_por=_AUTOR.id)
    _finalizar(ambiente, oc)
    with Session(ambiente["engine"]) as db:
        assert db.scalar(select(func.count()).select_from(UsuarioLogin)) == 0
