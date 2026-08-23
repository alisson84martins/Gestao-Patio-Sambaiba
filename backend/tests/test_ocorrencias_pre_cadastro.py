"""Bloco A3 (prompt de ajustes 23/08) — RE de condutor/cobrador digitado
direto na ocorrência alimenta a fila de pré-cadastro (Bloco H), mesma
regra já aplicada em pré-ocorrência (na conversão) e recolhida anormal
(ver test_pre_ocorrencia.py e tests/test_portaria.py).

Mesmo padrão de SQLite em memória + TestClient de
test_ocorrencias_colecoes_filhas.py — precisa das tabelas de todas as
coleções filhas porque _carregar_completa() (chamada no fim de
criar()/atualizar()) faz eager load de todas elas, mesmo vazias.

⛔ Nenhum dado pessoal real — RE e nomes fictícios.
"""
import itertools
import typing
from datetime import date, time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.models.pessoas import Motorista
from app.models.pre_cadastro import PessoaPreCadastro
from app.routers import ocorrencias as router_mod

_TABELAS = [
    Funcionario.__table__, TipoOcorrencia.__table__, OrgaoAutoridade.__table__,
    Ocorrencia.__table__, OcorrenciaAnalise.__table__, OcorrenciaVeiculoTerceiro.__table__,
    OcorrenciaAvaria.__table__, OcorrenciaVitima.__table__, OcorrenciaTestemunha.__table__,
    OcorrenciaAutoridade.__table__, OcorrenciaAnexo.__table__,
    Motorista.__table__, PessoaPreCadastro.__table__,
]

_AUTOR = Funcionario(id=uuid4(), re="41000", nome="Coordenador Pré-cadastro Teste")

_contador_numero = itertools.count(1)


class _SessaoNumeroAutomatico:
    """Ocorrencia.numero é Identity(always=True) — sequência do Postgres,
    que o SQLite não gera sozinho (mesmo problema contornado em
    test_ocorrencias_colecoes_filhas.py inserindo a ocorrência já pronta;
    aqui POST /ocorrencias é o que está sob teste, então precisa passar
    por criar() de verdade). Só intercepta add(), delega todo o resto
    (execute/flush/commit/refresh/begin_nested) pra uma Session real."""

    def __init__(self, session):
        self._session = session

    def add(self, obj):
        if isinstance(obj, Ocorrencia) and obj.numero is None:
            obj.numero = next(_contador_numero)
        self._session.add(obj)

    def __getattr__(self, nome):
        return getattr(self._session, nome)


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


@pytest.fixture
def cliente():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS coordenadoria")

    Base.metadata.create_all(engine, tables=_TABELAS)

    tipo_id = uuid4()
    with Session(engine) as setup:
        setup.add(Funcionario(id=_AUTOR.id, re=_AUTOR.re, nome=_AUTOR.nome))
        setup.add(TipoOcorrencia(
            id=tipo_id, codigo="INCIDENTE", nome="Incidente",
            exige_vitima=False, exige_terceiro=False, exige_analise=False,
            ordem=1, ativo=True,
        ))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield _SessaoNumeroAutomatico(db)
        finally:
            db.close()

    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    app.dependency_overrides[leitura_dep] = lambda: _AUTOR
    app.dependency_overrides[escrita_dep] = lambda: _AUTOR
    app.dependency_overrides[get_db] = _get_db_teste

    yield TestClient(app), engine, tipo_id

    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _payload_base(tipo_id):
    return {
        "tipo_ocorrencia_id": str(tipo_id),
        "data_ocorrencia": str(date(2026, 8, 23)),
        "hora_ocorrencia": str(time(10, 0)),
        "prefixo": "1234",
    }


def test_criar_com_condutor_re_desconhecido_alimenta_pre_cadastro(cliente):
    http, engine, tipo_id = cliente
    payload = _payload_base(tipo_id)
    payload.update(condutor_re="70001", condutor_nome="Fulano Fictício")

    resp = http.post("/ocorrencias", json=payload)
    assert resp.status_code == 201, resp.text

    with Session(engine) as db:
        pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "70001")
        ).scalar_one()
    assert pc.papel_sugerido == "MOTORISTA"
    assert pc.nome == "Fulano Fictício"
    assert pc.ultima_origem == "OCORRENCIA"


def test_criar_com_condutor_re_ja_cadastrado_nao_duplica_pre_cadastro(cliente):
    http, engine, tipo_id = cliente
    with Session(engine) as db:
        db.add(Motorista(id=uuid4(), re="70002", nome="Já Cadastrado", status="ATIVO"))
        db.commit()

    payload = _payload_base(tipo_id)
    payload.update(condutor_re="70002", condutor_nome="Já Cadastrado")
    resp = http.post("/ocorrencias", json=payload)
    assert resp.status_code == 201, resp.text

    with Session(engine) as db:
        total = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "70002")
        ).scalars().all()
    assert total == []


def test_atualizar_com_cobrador_re_digitado_depois_alimenta_pre_cadastro(cliente):
    http, engine, tipo_id = cliente
    resp = http.post("/ocorrencias", json=_payload_base(tipo_id))
    assert resp.status_code == 201, resp.text
    ocorrencia_id = resp.json()["id"]

    # 2º autosave — cobrador só é digitado depois, como no formulário real.
    resp2 = http.patch(f"/ocorrencias/{ocorrencia_id}", json={
        "cobrador_re": "70003", "cobrador_nome": "Cobrador Fictício",
    })
    assert resp2.status_code == 200, resp2.text

    with Session(engine) as db:
        pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "70003")
        ).scalar_one()
    assert pc.papel_sugerido == "COBRADOR"
    assert pc.ultima_origem == "OCORRENCIA"


def test_atualizar_sem_mexer_em_re_nao_toca_pre_cadastro(cliente):
    http, engine, tipo_id = cliente
    payload = _payload_base(tipo_id)
    payload.update(condutor_re="70004", condutor_nome="Condutor Fictício")
    resp = http.post("/ocorrencias", json=payload)
    assert resp.status_code == 201, resp.text
    ocorrencia_id = resp.json()["id"]

    with Session(engine) as db:
        antes = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "70004")
        ).scalar_one()
        vezes_visto_antes = antes.vezes_visto

    # PATCH que não toca condutor_re/cobrador_re — não deveria reprocessar.
    resp2 = http.patch(f"/ocorrencias/{ocorrencia_id}", json={"relato": "Detalhe adicional"})
    assert resp2.status_code == 200, resp2.text

    with Session(engine) as db:
        depois = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "70004")
        ).scalar_one()
    assert depois.vezes_visto == vezes_visto_antes
