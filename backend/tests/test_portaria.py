"""Módulo Portaria — controle de acesso veicular (Bloco D).

Os cenários da seção "Testes" do prompt de execução (revisão de 20/08) —
provam o comportamento CERTO e o ERRADO. D6 é o mais importante: só o
recurso certo muda cada coisa (cadastrar != autorizar), e a regra número
um (§1.1) — o sistema nunca impede um registro — tem que sobreviver a
veículo suspenso, placa desconhecida e RE repetido.

Mesmo padrão de test_pre_ocorrencia.py: SQLite em memória com ATTACH
DATABASE pro schema `portaria` (isolado do `main`, igual à separação real
do Postgres), sem conftest.py. O gate RBAC (`exige()`) usa
`vw_acesso_efetivo`, uma view Postgres que não existe em SQLite — por
isso as dependências de permissão dos routers são sobrescritas
diretamente (mesmo padrão de test_ocorrencias_smoke.py e
test_ocorrencias_autoria.py), simulando cada papel (CONTROLADOR_ACESSO,
ENCARREGADO, ADMIN) escrevendo/lendo exatamente os três recursos que a
migration 024 criou.

⛔ Nenhum dado pessoal real — RE, nome e placa fictícios.
"""
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import FUSO_OPERACAO
from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.portaria import (
    EmpresaTerceira, MovimentoPortaria, PortariaLocal, VeiculoPortaria,
    VeiculoSituacaoHist,
)
from app.routers import portaria as portaria_router_mod
from app.routers import portaria_veiculos as portaria_veiculos_router_mod

# Mesmo ajuste de test_pre_ocorrencia.py/test_ocorrencias_visibilidade.py:
# o driver sqlite3 puro não serializa uuid.UUID sozinho.
sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


_CONTROLADOR = Funcionario(id=uuid4(), re="60001", nome="Controlador Teste")
_ENCARREGADO = Funcionario(id=uuid4(), re="60002", nome="Encarregado Teste")
_ADMIN = Funcionario(id=uuid4(), re="60003", nome="Admin Teste")
_DONO_A = Funcionario(id=uuid4(), re="60010", nome="Dono A")
_DONO_B = Funcionario(id=uuid4(), re="60011", nome="Dono B")

_TABELAS = [
    Funcionario.__table__, PortariaLocal.__table__, EmpresaTerceira.__table__,
    VeiculoPortaria.__table__, VeiculoSituacaoHist.__table__, MovimentoPortaria.__table__,
]

# Pacote de permissões da migration 024 (§2.5) — CONTROLADOR_ACESSO escreve
# cadastro mas só lê autorização (D6); ENCARREGADO é o espelho.
_PERMISSOES = {
    "CONTROLADOR": {
        "leitura_acesso": True, "escrita_acesso": True,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": False,
    },
    "ENCARREGADO": {
        "leitura_acesso": True, "escrita_acesso": False,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": True,
    },
    "ADMIN": {chave: True for chave in (
        "leitura_acesso", "escrita_acesso", "leitura_cadastro",
        "escrita_cadastro", "leitura_autorizacao", "escrita_autorizacao",
    )},
}
_USUARIOS = {"CONTROLADOR": _CONTROLADOR, "ENCARREGADO": _ENCARREGADO, "ADMIN": _ADMIN}


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS portaria")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)

    with Session(engine) as setup:
        for f in (_CONTROLADOR, _ENCARREGADO, _ADMIN, _DONO_A, _DONO_B):
            setup.add(Funcionario(id=f.id, re=f.re, nome=f.nome, status="ATIVO"))
        setup.add(PortariaLocal(codigo="LEVES", nome="Portaria de leves", ordem=1, ativo=True))
        setup.commit()

    def _get_db_teste():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    deps = {
        "leitura_acesso": _dependency_de(portaria_router_mod.LeituraAcesso),
        "escrita_acesso": _dependency_de(portaria_router_mod.EscritaAcesso),
        "leitura_cadastro": _dependency_de(portaria_veiculos_router_mod.LeituraCadastro),
        "escrita_cadastro": _dependency_de(portaria_veiculos_router_mod.EscritaCadastro),
        "leitura_autorizacao": _dependency_de(portaria_veiculos_router_mod.LeituraAutorizacao),
        "escrita_autorizacao": _dependency_de(portaria_veiculos_router_mod.EscritaAutorizacao),
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
    """Sobrescreve as 6 dependências de RBAC do módulo pro papel pedido —
    🔴 é isto que prova D6: CONTROLADOR tem escrita_cadastro mas NÃO tem
    escrita_autorizacao, então qualquer chamada que dependa desta última
    recebe 403 de verdade, do jeito que o Starlette despacharia."""
    usuario = _USUARIOS[papel]
    for chave, permitido in _PERMISSOES[papel].items():
        dep = ambiente[chave]
        ambiente["http"].app.dependency_overrides[dep] = _permitir(usuario) if permitido else _negar()
    return usuario


def _criar_veiculo(ambiente, **campos) -> UUID:
    veiculo_id = campos.pop("id", uuid4())
    with Session(ambiente["engine"]) as db:
        db.add(VeiculoPortaria(
            id=veiculo_id,
            propriedade=campos.pop("propriedade", "PARTICULAR"),
            funcionario_id=campos.pop("funcionario_id", None),
            empresa_terceira_id=campos.pop("empresa_terceira_id", None),
            placa=campos.pop("placa"),
            tipo=campos.pop("tipo", "CARRO"),
            situacao=campos.pop("situacao", "PENDENTE"),
            situacao_por=campos.pop("situacao_por", None),
            situacao_em=campos.pop("situacao_em", None),
            situacao_motivo=campos.pop("situacao_motivo", None),
            exige_hodometro=campos.pop("exige_hodometro", False),
            ativo=campos.pop("ativo", True),
            **campos,
        ))
        db.commit()
    return veiculo_id


# ─── 1 — cadastro nasce PENDENTE (D6) ───────────────────────────────────

def test_controlador_cadastra_veiculo_nasce_pendente(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "funcionario_id": str(_DONO_A.id), "placa": "ABC1D23",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["situacao"] == "PENDENTE"


# ─── 2 — 🔴 o teste mais importante: PATCH cadastral rejeita situacao ──

def test_patch_cadastral_rejeita_situacao(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)
    resp = ambiente["http"].patch(f"/portaria/veiculos/{veiculo_id}", json={"situacao": "AUTORIZADO"})
    assert resp.status_code == 422, resp.text


# ─── 3 — controlador não muda situação (recurso errado -> 403) ─────────

def test_controlador_nao_muda_situacao(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)
    resp = ambiente["http"].patch(f"/portaria/veiculos/{veiculo_id}/situacao", json={"situacao": "AUTORIZADO"})
    assert resp.status_code == 403, resp.text


# ─── 4 — suspender sem motivo é rejeitado ───────────────────────────────

def test_suspender_sem_motivo_rejeitado(ambiente):
    _como(ambiente, "ENCARREGADO")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
    resp = ambiente["http"].patch(f"/portaria/veiculos/{veiculo_id}/situacao", json={"situacao": "SUSPENSO"})
    assert resp.status_code == 422, resp.text


# ─── 5 — suspender com motivo grava situação + histórico correto ──────

def test_suspender_com_motivo_grava_situacao_e_historico(ambiente):
    usuario = _como(ambiente, "ENCARREGADO")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].patch(
        f"/portaria/veiculos/{veiculo_id}/situacao",
        json={"situacao": "SUSPENSO", "motivo": "Bateu no portão da garagem"},
    )
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["situacao"] == "SUSPENSO"
    assert corpo["situacao_motivo"] == "Bateu no portão da garagem"
    assert corpo["situacao_por"] == str(usuario.id)

    with Session(ambiente["engine"]) as db:
        hist = db.execute(
            select(VeiculoSituacaoHist).where(VeiculoSituacaoHist.veiculo_id == veiculo_id)
        ).scalars().all()
    assert len(hist) == 1
    assert hist[0].situacao_de == "AUTORIZADO"
    assert hist[0].situacao_para == "SUSPENSO"


# ─── 6/7 — 🔴 a regra número um: suspenso nunca bloqueia o registro ────

def test_entrada_suspenso_sem_observacao_rejeitada(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="SUSPENSO", situacao_motivo="teste")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ABC1D23"})
    assert resp.status_code == 422, resp.text


def test_entrada_suspenso_com_observacao_registra(ambiente):
    """Se este teste retornar 403/409, o módulo está errado — regra número um."""
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="SUSPENSO", situacao_motivo="teste")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "ABC1D23",
        "observacao": "Liberado pelo encarregado por telefone, autorização verbal",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["avisos"], "esperado aviso sobre o veículo suspenso, sem bloquear o registro"


# ─── 8 — placa inexistente registra avulso ──────────────────────────────

def test_placa_inexistente_registra_avulso(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ZZZ9999"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["cadastrado"] is False


# ─── 9 — placa normalizada acha o mesmo veículo (D10) ──────────────────

def test_placa_normalizada_acha_mesmo_veiculo(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].get("/portaria/buscar", params={"q": "abc-1d23"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["exato"] is True
    assert len(corpo["candidatos"]) == 1
    assert corpo["candidatos"][0]["veiculo"]["id"] == str(veiculo_id)


# ─── 10 — "dentro agora" deriva do último movimento por placa (D3) ─────
# ENTRADA e SAÍDA vêm com `momento` explícito (RETROATIVO), minutos
# apartados — o CURRENT_TIMESTAMP do SQLite só tem resolução de 1s, e as
# duas chamadas de teste rodam no mesmo segundo; sem isso os dois
# movimentos empatam no "último por placa" e o teste vira um teste do
# relógio da máquina, não da regra D3. Em produção o NOW() do Postgres é
# microssegundo, então esse empate não existe de verdade.

def test_dentro_deriva_ultimo_movimento_por_placa(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    agora = datetime.now(FUSO_OPERACAO)
    r1 = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "ABC1D23", "origem": "RETROATIVO",
        "momento": (agora - timedelta(minutes=10)).isoformat(),
        "observacao": "Lançamento de teste",
    })
    assert r1.status_code == 201, r1.text
    resp = ambiente["http"].get("/portaria/dentro")
    corpo = resp.json()
    assert [m["placa_registrada"] for m in corpo["dentro"]] == ["ABC1D23"]

    r2 = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "SAIDA", "placa": "ABC1D23", "origem": "RETROATIVO",
        "momento": agora.isoformat(),
        "observacao": "Lançamento de teste",
    })
    assert r2.status_code == 201, r2.text
    resp2 = ambiente["http"].get("/portaria/dentro")
    corpo2 = resp2.json()
    assert "ABC1D23" not in [m["placa_registrada"] for m in corpo2["dentro"]]


# ─── 11 — bloquear-por-re suspende TODOS os veículos ativos da pessoa ──

def test_bloquear_por_re_suspende_todos_os_veiculos(ambiente):
    _como(ambiente, "ENCARREGADO")
    _criar_veiculo(ambiente, placa="AAA1111", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", tipo="CARRO")
    _criar_veiculo(ambiente, placa="BBB2222", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", tipo="MOTO")

    resp = ambiente["http"].post(
        "/portaria/veiculos/bloquear-por-re", json={"re": _DONO_A.re, "motivo": "Desligamento"}
    )
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert len(corpo["veiculos_suspensos"]) == 2
    assert all(v["situacao"] == "SUSPENSO" for v in corpo["veiculos_suspensos"])
    assert corpo["ja_suspensos"] == []

    with Session(ambiente["engine"]) as db:
        hist = db.execute(select(VeiculoSituacaoHist)).scalars().all()
    assert len(hist) == 2


# ─── 12 — 🔴 data_referencia às 21h em São Paulo é HOJE, não amanhã ────
# (D9 + D16). O relógio do servidor abaixo é UTC de propósito — é o caso
# que quebra com CURRENT_DATE cru/date.today() do servidor.

def test_data_referencia_21h_sao_paulo_nao_vira_amanha(ambiente, monkeypatch):
    momento_sp = datetime(2026, 8, 20, 21, 30, tzinfo=FUSO_OPERACAO)
    momento_utc = momento_sp.astimezone(timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                # datetime.now() sem tz = "hora local ingênua do servidor"
                # — aqui simulamos servidor em UTC, o cenário que quebra.
                return momento_utc.replace(tzinfo=None)
            return momento_utc.astimezone(tz)

    monkeypatch.setattr(portaria_router_mod, "datetime", _DatetimeFixo)

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ABC1D23"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data_referencia"] == "2026-08-20"


# ─── 12b — GET /portaria/dentro separa "dentro" de "sem_saida" (D17) ──

def test_dentro_e_sem_saida_por_horas(ambiente):
    _como(ambiente, "CONTROLADOR")
    agora = datetime.now(timezone.utc)
    with Session(ambiente["engine"]) as db:
        db.add(MovimentoPortaria(
            id=uuid4(), local_codigo="LEVES", sentido="ENTRADA",
            momento=agora - timedelta(hours=2), data_referencia=date.today(),
            placa_registrada="AAA1111", cadastrado=False, origem="MANUAL",
            registrado_por=_CONTROLADOR.id,
        ))
        db.add(MovimentoPortaria(
            id=uuid4(), local_codigo="LEVES", sentido="ENTRADA",
            momento=agora - timedelta(hours=40), data_referencia=date.today(),
            placa_registrada="BBB2222", cadastrado=False, origem="MANUAL",
            registrado_por=_CONTROLADOR.id,
        ))
        db.commit()

    resp = ambiente["http"].get("/portaria/dentro")  # default horas=36
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    placas_dentro = [m["placa_registrada"] for m in corpo["dentro"]]
    placas_sem_saida = [m["placa_registrada"] for m in corpo["sem_saida"]]
    assert "AAA1111" in placas_dentro
    assert "BBB2222" not in placas_dentro
    assert "BBB2222" in placas_sem_saida


# ─── 13 — 🔴 busca por RE com dois veículos: 200 com 2 candidatos ──────
# (§3.6-B) — antes da revisão de 20/08 isto quebrava com 500
# (MultipleResultsFound). Este teste teria pego o bug.

def test_busca_por_re_com_dois_veiculos_retorna_dois_candidatos(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="AAA1111", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", tipo="CARRO")
    _criar_veiculo(ambiente, placa="BBB2222", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", tipo="MOTO")

    resp = ambiente["http"].get("/portaria/buscar", params={"q": _DONO_A.re})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["exato"] is False
    assert len(corpo["candidatos"]) == 2


# ─── 14 — 🔴 controlador não consegue desativar veículo (§3.6-A) ──────

def test_controlador_nao_desativa_veiculo(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
    resp = ambiente["http"].patch(f"/portaria/veiculos/{veiculo_id}", json={"ativo": False})
    assert resp.status_code == 422, resp.text


# ─── 15 — /situacao alcança veículo com ativo=false (§3.6-A.2) ────────

def test_situacao_alcanca_veiculo_inativo(ambiente):
    _como(ambiente, "ENCARREGADO")
    veiculo_id = _criar_veiculo(
        ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", ativo=False,
    )
    resp = ambiente["http"].patch(
        f"/portaria/veiculos/{veiculo_id}/situacao", json={"situacao": "BAIXADO", "motivo": "Veículo vendido"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["situacao"] == "BAIXADO"


# ─── 16 — 🔴 busca por prefixo com 2 placas: 2 candidatos, exato=false ─
# (§3.6-C) — antes da revisão isto escolhia 1 em silêncio (.limit(1)).

def test_busca_por_prefixo_com_duas_placas_retorna_candidatos(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1111", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
    _criar_veiculo(ambiente, placa="ABC2222", funcionario_id=_DONO_B.id, situacao="AUTORIZADO")

    resp = ambiente["http"].get("/portaria/buscar", params={"q": "ABC"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["exato"] is False
    assert len(corpo["candidatos"]) == 2
