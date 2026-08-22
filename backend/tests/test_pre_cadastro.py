"""Pré-cadastro de pessoas (Bloco H). Testes 21-28 de
_handoff-claude/PROMPT-portaria-blocos-E-F.md §5.2.

SQLite em memória, sem ATTACH (pessoa_pre_cadastro é `public`, sem schema
próprio) — mesmo padrão simplificado de test_portaria.py, mas sem a parte
de ATTACH DATABASE `portaria` (não se aplica aqui).

⛔ Nenhum dado pessoal real — RE, nome, CPF e CNH fictícios.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.pessoas import Motorista
from app.models.pre_cadastro import PessoaPreCadastro
from app.routers import pre_cadastro as pre_cadastro_router_mod
from app.services import pre_cadastro as pre_cadastro_service_mod
from app.services.pre_cadastro import registrar_pessoa_vista

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_ADMIN = Funcionario(id=uuid4(), re="70001", nome="Admin Teste")
_GERENTE_OP = Funcionario(id=uuid4(), re="70002", nome="Gerente Operacional Teste")
_CONTROLADOR = Funcionario(id=uuid4(), re="70003", nome="Controlador Teste")

_TABELAS = [Funcionario.__table__, Motorista.__table__, PessoaPreCadastro.__table__]


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)
    with Session(engine) as setup:
        for f in (_ADMIN, _GERENTE_OP, _CONTROLADOR):
            setup.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    deps = {
        "leitura": _dependency_de(pre_cadastro_router_mod.LeituraPreCadastro),
        "escrita": _dependency_de(pre_cadastro_router_mod.EscritaPreCadastro),
        "usuarios": _dependency_de(pre_cadastro_router_mod.EscritaUsuarios),
    }
    app.dependency_overrides[get_db] = _get_db_teste

    yield {"engine": engine, "http": TestClient(app), **deps}

    for dep in deps.values():
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides.pop(get_db, None)


def _negar():
    def _dep():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sem permissão (fake, teste)")
    return _dep


def _permitir(usuario):
    def _dep():
        return usuario
    return _dep


def _como(ambiente, papel):
    """Espelha as permissões reais da migration 028: ADMIN tem
    pre_cadastro ler+escrever e usuarios escrever; GERENTE_OPERACIONAL só
    pre_cadastro ler; CONTROLADOR_ACESSO não tem nada disto."""
    permissoes = {
        "ADMIN": {"leitura": True, "escrita": True, "usuarios": True},
        "GERENTE_OPERACIONAL": {"leitura": True, "escrita": False, "usuarios": False},
        "CONTROLADOR": {"leitura": False, "escrita": False, "usuarios": False},
    }[papel]
    usuario = {"ADMIN": _ADMIN, "GERENTE_OPERACIONAL": _GERENTE_OP, "CONTROLADOR": _CONTROLADOR}[papel]
    for chave, permitido in permissoes.items():
        dep = ambiente[chave]
        ambiente["http"].app.dependency_overrides[dep] = _permitir(usuario) if permitido else _negar()
    return usuario


def _buscar_por_re(engine, re: str) -> PessoaPreCadastro | None:
    with Session(engine) as db:
        return db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == re)
        ).scalar_one_or_none()


# ─── 21 — RE já existente em funcionario -> nenhum pré-cadastro criado ──

def test_re_ja_cadastrado_em_funcionario_nao_cria_pre_cadastro(ambiente):
    with Session(ambiente["engine"]) as db:
        db.add(Funcionario(id=uuid4(), re="80001", nome="Já Cadastrado", status="ATIVO"))
        db.commit()
        registrar_pessoa_vista(db, re="80001", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()
    assert _buscar_por_re(ambiente["engine"], "80001") is None


# ─── 21b — 🔴 idem para motorista (a outra tabela de pessoa) ────────────

def test_re_ja_cadastrado_em_motorista_nao_cria_pre_cadastro(ambiente):
    with Session(ambiente["engine"]) as db:
        db.add(Motorista(id=uuid4(), re="80002", nome="Motorista Legado", status="ATIVO"))
        db.commit()
        registrar_pessoa_vista(db, re="80002", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()
    assert _buscar_por_re(ambiente["engine"], "80002") is None


# ─── 22 — RE novo pela portaria -> cria com papel_sugerido, só o RE ─────

def test_re_novo_pela_portaria_cria_pre_cadastro_so_com_re(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80003", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()
    criado = _buscar_por_re(ambiente["engine"], "80003")
    assert criado is not None
    assert criado.papel_sugerido == "MOTORISTA"
    assert criado.nome is None
    assert criado.cpf is None
    assert criado.status == "PENDENTE"
    assert criado.vezes_visto == 1
    assert criado.ultima_origem == "PORTARIA_RECOLHIDA"
    assert criado.retencao_expira_em is not None


# ─── 23 — mesmo RE depois pela pré-ocorrência -> enriquece, sem sobrescrever ─

def test_mesmo_re_por_pre_ocorrencia_enriquece_sem_sobrescrever(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80004", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()

    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(
            db, re="80004", papel="MOTORISTA", origem="PRE_OCORRENCIA",
            nome="Fulano da Silva", cpf="11122233344", cnh="12345678900",
        )
        db.commit()

    atualizado = _buscar_por_re(ambiente["engine"], "80004")
    assert atualizado.nome == "Fulano da Silva"
    assert atualizado.cpf == "11122233344"
    assert atualizado.cnh == "12345678900"
    assert atualizado.vezes_visto == 2
    assert atualizado.ultima_origem == "PRE_OCORRENCIA"

    # ⛔ Uma terceira chamada com nome diferente NÃO sobrescreve o que já
    # estava preenchido — só incrementa vezes_visto/atualiza a origem.
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(
            db, re="80004", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA", nome="Nome Errado",
        )
        db.commit()

    final = _buscar_por_re(ambiente["engine"], "80004")
    assert final.nome == "Fulano da Silva"
    assert final.vezes_visto == 3
    assert final.ultima_origem == "PORTARIA_RECOLHIDA"


# ─── 25 — 🔴 falha forçada no serviço nunca propaga ─────────────────────

def test_falha_forcada_no_servico_nunca_propaga(ambiente, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(pre_cadastro_service_mod, "_registrar", _explode)

    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80005", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        # Não levantou — a chamada acima já prova a regra número um.
        db.commit()
    assert _buscar_por_re(ambiente["engine"], "80005") is None


# ─── 26 — 🔴 CONTROLADOR_ACESSO chamando GET /pre-cadastros -> 403 ──────

def test_controlador_nao_acessa_lista_de_pre_cadastros(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/pre-cadastros")
    assert resp.status_code == 403, resp.text


def test_admin_le_lista_de_pre_cadastros(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80006", papel="COBRADOR", origem="PORTARIA_RECOLHIDA")
        db.commit()
    _como(ambiente, "ADMIN")
    resp = ambiente["http"].get("/pre-cadastros?status=PENDENTE")
    assert resp.status_code == 200, resp.text
    assert any(item["re"] == "80006" for item in resp.json())


# ─── 27 — /promover com pre_cadastro mas sem usuarios escrever -> 403 ───

def test_promover_sem_usuarios_escrever_nega_403(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80007", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()
        pre_id = _buscar_por_re(ambiente["engine"], "80007").id

    # GERENTE_OPERACIONAL tem pre_cadastro ler, mas não usuarios escrever.
    _como(ambiente, "GERENTE_OPERACIONAL")
    resp = ambiente["http"].post(f"/pre-cadastros/{pre_id}/promover")
    assert resp.status_code == 403, resp.text


# ─── 28 — promoção cria funcionario e ⛔ nunca usuario_login ────────────

def test_promocao_cria_funcionario_sem_criar_login(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(
            db, re="80008", papel="MOTORISTA", origem="PRE_OCORRENCIA", nome="Beltrano Souza",
        )
        db.commit()
        pre_id = _buscar_por_re(ambiente["engine"], "80008").id

    _como(ambiente, "ADMIN")
    resp = ambiente["http"].post(f"/pre-cadastros/{pre_id}/promover")
    assert resp.status_code == 200, resp.text
    funcionario_id = resp.json()["funcionario_id"]

    with Session(ambiente["engine"]) as db:
        funcionario = db.get(Funcionario, UUID(funcionario_id))
        atualizado = db.get(PessoaPreCadastro, pre_id)
    assert funcionario is not None
    assert funcionario.re == "80008"
    assert funcionario.nome == "Beltrano Souza"
    assert atualizado.status == "PROMOVIDO"
    assert atualizado.funcionario_id == funcionario.id
    assert atualizado.promovido_por == _ADMIN.id
    assert atualizado.promovido_em is not None
    # ⚠️ usuario_login nem existe no schema deste teste (não está em
    # _TABELAS) — se o router alguma vez tentasse criar um login aqui, a
    # chamada acima teria explodido com "no such table", não passado 200.


def test_promover_sem_nome_e_rejeitado(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80009", papel="MOTORISTA", origem="PORTARIA_RECOLHIDA")
        db.commit()
        pre_id = _buscar_por_re(ambiente["engine"], "80009").id

    _como(ambiente, "ADMIN")
    resp = ambiente["http"].post(f"/pre-cadastros/{pre_id}/promover")
    assert resp.status_code == 422, resp.text


def test_descartar_exige_motivo_e_muda_status(ambiente):
    with Session(ambiente["engine"]) as db:
        registrar_pessoa_vista(db, re="80010", papel="COBRADOR", origem="PORTARIA_RECOLHIDA")
        db.commit()
        pre_id = _buscar_por_re(ambiente["engine"], "80010").id

    _como(ambiente, "ADMIN")
    sem_motivo = ambiente["http"].post(f"/pre-cadastros/{pre_id}/descartar", json={"motivo": ""})
    assert sem_motivo.status_code == 422, sem_motivo.text

    resp = ambiente["http"].post(f"/pre-cadastros/{pre_id}/descartar", json={"motivo": "RE digitado errado"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["status"] == "DESCARTADO"
    assert corpo["descarte_motivo"] == "RE digitado errado"
