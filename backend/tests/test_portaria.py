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
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import FUSO_OPERACAO
from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.catalogos import Linha, TipoDefeito
from app.models.enums import OrigemEscalaEnum, SetorEnum, StatusFichaEnum, TipoEscalaEnum
from app.models.operacoes import Escala, FichaManutencao, ImportacaoEscala
from app.models.pessoas import Motorista, Usuario
from app.models.portaria import (
    Credencial, EmpresaTerceira, MovimentoPortaria, PortariaLocal,
    RecolhidaAnormal, VeiculoPortaria, VeiculoSituacaoHist,
)
from app.routers import portaria as portaria_router_mod
from app.routers import portaria_recolhidas as portaria_recolhidas_router_mod
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
_MECANICO = Funcionario(id=uuid4(), re="60004", nome="Mecânico Teste")
_DONO_A = Funcionario(id=uuid4(), re="60010", nome="Dono A")
_DONO_B = Funcionario(id=uuid4(), re="60011", nome="Dono B")

_TABELAS = [
    Funcionario.__table__, PortariaLocal.__table__, EmpresaTerceira.__table__,
    VeiculoPortaria.__table__, VeiculoSituacaoHist.__table__, MovimentoPortaria.__table__,
    Credencial.__table__, RecolhidaAnormal.__table__,
    Motorista.__table__, Linha.__table__, TipoDefeito.__table__,
    ImportacaoEscala.__table__, Escala.__table__, Usuario.__table__, FichaManutencao.__table__,
]

# Onibus tem coluna GERADA com sintaxe específica do Postgres (CASE ...
# ::setor_enum) que o SQLite não entende — mesmo ajuste de
# test_ocorrencias_autopreencher.py/test_renumerar_fila.py: DDL bruto, sem
# GENERATED, "setor" como valor comum. Não muda o que o SELECT via ORM lê.
_DDL_ONIBUS = """
CREATE TABLE onibus (
    id CHAR(36) PRIMARY KEY,
    numero_frota INTEGER NOT NULL UNIQUE,
    placa VARCHAR(10),
    setor VARCHAR(10),
    status VARCHAR(20) NOT NULL DEFAULT 'ATIVO',
    codigo_externo VARCHAR(50),
    criado_em DATETIME,
    criado_por CHAR(36),
    atualizado_em DATETIME,
    atualizado_por CHAR(36)
)
"""

# Pacote de permissões — migration 024 (§2.5, D6) + migration 026 (Bloco F,
# §2.4): CONTROLADOR_ACESSO escreve recolhida_anormal mas NUNCA tem
# recolhida_gerencial; MECANICO escreve recolhida_anormal e `manutencao`
# (recurso já existente — a avaliação usa esse, não um recurso novo).
_PERMISSOES = {
    "CONTROLADOR": {
        "leitura_acesso": True, "escrita_acesso": True,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": False,
        "leitura_recolhida": True, "escrita_recolhida": True,
        "leitura_gerencial": False, "escrita_manutencao": False,
    },
    "ENCARREGADO": {
        "leitura_acesso": True, "escrita_acesso": False,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": True,
        "leitura_recolhida": True, "escrita_recolhida": False,
        "leitura_gerencial": True, "escrita_manutencao": False,
    },
    "MECANICO": {
        "leitura_acesso": False, "escrita_acesso": False,
        "leitura_cadastro": False, "escrita_cadastro": False,
        "leitura_autorizacao": False, "escrita_autorizacao": False,
        "leitura_recolhida": True, "escrita_recolhida": True,
        "leitura_gerencial": False, "escrita_manutencao": True,
    },
    "ADMIN": {chave: True for chave in (
        "leitura_acesso", "escrita_acesso", "leitura_cadastro",
        "escrita_cadastro", "leitura_autorizacao", "escrita_autorizacao",
        "leitura_recolhida", "escrita_recolhida", "leitura_gerencial", "escrita_manutencao",
    )},
}
_USUARIOS = {
    "CONTROLADOR": _CONTROLADOR, "ENCARREGADO": _ENCARREGADO,
    "ADMIN": _ADMIN, "MECANICO": _MECANICO,
}


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
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL_ONIBUS)

    with Session(engine) as setup:
        for f in (_CONTROLADOR, _ENCARREGADO, _ADMIN, _MECANICO, _DONO_A, _DONO_B):
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
        "leitura_recolhida": _dependency_de(portaria_recolhidas_router_mod.LeituraRecolhida),
        "escrita_recolhida": _dependency_de(portaria_recolhidas_router_mod.EscritaRecolhida),
        "leitura_gerencial": _dependency_de(portaria_recolhidas_router_mod.LeituraGerencial),
        "escrita_manutencao": _dependency_de(portaria_recolhidas_router_mod.EscritaManutencao),
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


def _criar_onibus(engine, numero_frota: int) -> UUID:
    """Mesmo padrão de test_ocorrencias_autopreencher.py::_criar_onibus —
    INSERT bruto, nunca a classe ORM (Computed() faria o SQLAlchemy excluir
    `setor` do INSERT e a tabela de teste não tem a cláusula GENERATED)."""
    onibus_id = uuid4()
    with engine.begin() as conn:
        # .hex (sem hífen), não str() — mesmo formato que o adapter sqlite3
        # registrado no topo do arquivo grava pra UUID vindo do lado ORM;
        # com formatos diferentes o FK de escala.onibus_id não bate (string
        # crua, sem normalização de UUID pelo SQLite).
        conn.execute(
            text("INSERT INTO onibus (id, numero_frota, status) VALUES (:id, :numero_frota, 'ATIVO')"),
            {"id": onibus_id.hex, "numero_frota": numero_frota},
        )
    return onibus_id


def _criar_linha(db, codigo="101") -> Linha:
    linha = Linha(id=uuid4(), codigo=codigo, nome=f"Linha {codigo}", setor=SetorEnum.E2, ativa=True)
    db.add(linha)
    db.flush()
    return linha


def _criar_tipo_defeito(db, codigo="MEC_MOTOR") -> TipoDefeito:
    tipo = TipoDefeito(id=uuid4(), codigo=codigo, nome="Motor", categoria="mecanica", ativo=True)
    db.add(tipo)
    db.flush()
    return tipo


def _criar_motorista(db, re="50001", nome="Motorista Teste") -> Motorista:
    motorista = Motorista(id=uuid4(), re=re, nome=nome, status="ATIVO")
    db.add(motorista)
    db.flush()
    return motorista


def _criar_escala(db, *, onibus_id, motorista_id, linha_id, data, horario_saida) -> Escala:
    escala = Escala(
        id=uuid4(), data=data, onibus_id=onibus_id, motorista_id=motorista_id,
        linha_id=linha_id, horario_saida=horario_saida,
        tipo=TipoEscalaEnum.MANOBRA, origem=OrigemEscalaEnum.MANUAL,
    )
    db.add(escala)
    db.flush()
    return escala


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


# ============================================================================
# BLOCO E — QR do veículo (§1.7 do prompt)
# ============================================================================

# ─── 1 — emitir credencial: código gerado, ativa, sem placa/RE dentro ──

def test_emitir_credencial_gera_codigo_sem_placa_nem_re(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)

    resp = ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["ativa"] is True
    assert corpo["veiculo_id"] == str(veiculo_id)
    codigo = corpo["codigo"]
    assert codigo
    # 🔴 o QR é IDENTIFICADOR, não credencial de segurança (§1.0) — o token é
    # opaco, nunca carrega a placa nem o RE do dono.
    assert "ABC1D23" not in codigo
    assert _DONO_A.re not in codigo


# ─── 2 — reemitir revoga a anterior e gera código diferente ────────────

def test_reemitir_credencial_revoga_anterior(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)

    primeira = ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    assert primeira.status_code == 201, primeira.text
    codigo_antigo = primeira.json()["codigo"]

    # Sem motivo -> 422, já existe credencial ativa.
    sem_motivo = ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    assert sem_motivo.status_code == 422, sem_motivo.text

    segunda = ambiente["http"].post(
        f"/portaria/veiculos/{veiculo_id}/credencial", json={"motivo": "Adesivo descolou"}
    )
    assert segunda.status_code == 201, segunda.text
    corpo = segunda.json()
    assert corpo["codigo"] != codigo_antigo
    assert corpo["ativa"] is True

    with Session(ambiente["engine"]) as db:
        credenciais = db.execute(select(Credencial).where(Credencial.veiculo_id == veiculo_id)).scalars().all()
    assert len(credenciais) == 2
    antiga = next(c for c in credenciais if c.codigo == codigo_antigo)
    assert antiga.ativa is False
    assert antiga.revogada_em is not None
    assert antiga.motivo_revogacao == "Adesivo descolou"


# ─── 3 — buscar-credencial devolve o mesmo payload da busca por placa ──

def test_buscar_credencial_devolve_mesmo_payload_da_busca(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
    emissao = ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    codigo = emissao.json()["codigo"]

    por_qr = ambiente["http"].get("/portaria/buscar-credencial", params={"codigo": codigo})
    por_placa = ambiente["http"].get("/portaria/buscar", params={"q": "ABC1D23"})
    assert por_qr.status_code == 200, por_qr.text
    assert por_qr.json() == por_placa.json()


# ─── 4 — código inexistente: 200 com lista vazia, nunca 404/403 ────────

def test_buscar_credencial_codigo_inexistente_devolve_200_vazio(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/buscar-credencial", params={"codigo": "codigo-que-nao-existe"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["candidatos"] == []
    assert corpo["exato"] is False


# ─── 5 — credencial revogada: 200 vazio, veículo continua registrável ──

def test_buscar_credencial_revogada_devolve_200_vazio_e_veiculo_segue_registravel(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
    emissao = ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    codigo = emissao.json()["codigo"]

    # TestClient.delete() desta versão de httpx não aceita `json=` (DELETE
    # com corpo é incomum, mas o endpoint exige `motivo` — usa .request()).
    revoga = ambiente["http"].request(
        "DELETE", f"/portaria/veiculos/{veiculo_id}/credencial",
        json={"motivo": "Carro vendido, adesivo removido"},
    )
    assert revoga.status_code == 200, revoga.text

    por_qr = ambiente["http"].get("/portaria/buscar-credencial", params={"codigo": codigo})
    assert por_qr.status_code == 200, por_qr.text
    assert por_qr.json()["candidatos"] == []

    # ⚠️ credencial.ativa=FALSE não é proibição — o veículo continua
    # registrável pela placa igual sempre foi (§1.3).
    registro = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ABC1D23"})
    assert registro.status_code == 201, registro.text


# ─── 6 — movimento identificado por QR grava origem = 'QR' ─────────────

def test_movimento_via_qr_grava_origem_qr(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].post(
        "/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ABC1D23", "origem": "QR"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["origem"] == "QR"


# ─── 7 — GET credencial ativa: null antes de emitir, objeto depois ─────
# (endpoint além dos 4 do prompt — sustenta o botão "Gerar QR" × "Reemitir"
# na ficha, ver §1.6)

def test_obter_credencial_ativa_null_antes_e_objeto_depois(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)

    antes = ambiente["http"].get(f"/portaria/veiculos/{veiculo_id}/credencial")
    assert antes.status_code == 200, antes.text
    assert antes.json() is None

    ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})
    depois = ambiente["http"].get(f"/portaria/veiculos/{veiculo_id}/credencial")
    assert depois.status_code == 200, depois.text
    assert depois.json()["ativa"] is True


# ─── 8 — SVG do QR e página de etiquetas respondem image/svg+xml e HTML ─

def test_credencial_svg_e_etiquetas_respondem_conteudo_esperado(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)
    ambiente["http"].post(f"/portaria/veiculos/{veiculo_id}/credencial", json={})

    svg = ambiente["http"].get(f"/portaria/veiculos/{veiculo_id}/credencial.svg")
    assert svg.status_code == 200, svg.text
    assert "image/svg+xml" in svg.headers["content-type"]
    assert "<svg" in svg.text

    etiquetas = ambiente["http"].get(f"/portaria/credenciais/etiquetas?ids={veiculo_id}")
    assert etiquetas.status_code == 200, etiquetas.text
    assert "text/html" in etiquetas.headers["content-type"]
    assert "ABC1D23" in etiquetas.text
    # ⛔ Nada de RE, nome ou CPF impresso na etiqueta (§1.4).
    assert _DONO_A.re not in etiquetas.text
    assert _DONO_A.nome not in etiquetas.text


# ============================================================================
# BLOCO F — Recolhida anormal (§2.8 do prompt)
# ============================================================================

# ─── 7 — controlador registra recolhida -> 201, AGUARDANDO ─────────────

def test_controlador_registra_recolhida_201_aguardando(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9999", "tipo_defeito_codigo": "MEC_MOTOR", "relato": "Fumaça no motor",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "AGUARDANDO"


# ─── 8 — 🔴 o teste mais importante: sem motorista/cobrador na resposta ─
# (§2.9-0: o controlador DIGITA o RE — aqui ele digita exatamente o que a
# escala teria sugerido, o que classifica origem_identificacao='ESCALA'.
# Relógio fixo pro teste não ficar refém do teto de sanidade de 20h §2.9-A.)

def test_resposta_da_recolhida_nao_contem_motorista_nem_cobrador(ambiente, monkeypatch):
    momento_sp = datetime(2026, 8, 20, 10, 0, tzinfo=FUSO_OPERACAO)
    momento_utc = momento_sp.astimezone(timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return momento_utc.replace(tzinfo=None)
            return momento_utc.astimezone(tz)

    monkeypatch.setattr(portaria_recolhidas_router_mod, "datetime", _DatetimeFixo)

    with Session(ambiente["engine"]) as db:
        onibus_id = _criar_onibus(ambiente["engine"], numero_frota=1721)
        linha = _criar_linha(db)
        motorista = _criar_motorista(db, re="50001", nome="Fulano da Silva")
        _criar_tipo_defeito(db, codigo="MEC_MOTOR")
        _criar_escala(
            db, onibus_id=onibus_id, motorista_id=motorista.id, linha_id=linha.id,
            data=momento_sp.date(), horario_saida=time(0, 0),
        )
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1721", "tipo_defeito_codigo": "MEC_MOTOR", "motorista_re": "50001",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert "motorista_re" not in corpo
    assert "motorista_nome" not in corpo
    assert "cobrador_re" not in corpo
    assert "cobrador_nome" not in corpo
    # Confere na base que a escala RESOLVEU e o nome foi preenchido (a
    # prova de que o teste não está passando "por acidente", sem motorista
    # nenhum pra esconder).
    with Session(ambiente["engine"]) as db:
        salva = db.get(RecolhidaAnormal, UUID(corpo["id"]))
    assert salva.motorista_re == "50001"
    assert salva.motorista_nome == "Fulano da Silva"
    assert salva.origem_identificacao == "ESCALA"
    assert salva.cobrador_re is None  # ⚠️ ninguém digitou cobrador neste teste

    listagem = ambiente["http"].get("/portaria/recolhidas")
    assert listagem.status_code == 200, listagem.text
    for item in listagem.json():
        assert "motorista_re" not in item
        assert "motorista_nome" not in item


# ─── 8b — 🔴 controlador digita RE que NÃO bate com a escala -> PORTARIA ─

def test_motorista_digitado_diferente_da_escala_grava_origem_portaria(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1725)
    with Session(ambiente["engine"]) as db:
        _criar_tipo_defeito(db, codigo="MEC_MOTOR")
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1725", "tipo_defeito_codigo": "MEC_MOTOR",
        "motorista_re": "70009", "cobrador_re": "70010",
    })
    assert resp.status_code == 201, resp.text
    with Session(ambiente["engine"]) as db:
        salva = db.get(RecolhidaAnormal, UUID(resp.json()["id"]))
    assert salva.motorista_re == "70009"
    assert salva.cobrador_re == "70010"
    assert salva.origem_identificacao == "PORTARIA"


# ─── 9 — 🔴 quem só tem recolhida_anormal não acessa /gerencial ────────

def test_gerencial_nega_403_para_quem_so_tem_recolhida_anormal(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/recolhidas/gerencial")
    assert resp.status_code == 403, resp.text


# ─── 10 — prefixo inexistente registra 201 mesmo assim, onibus_id nulo ─

def test_prefixo_inexistente_registra_201_onibus_id_nulo(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "8888", "tipo_defeito_codigo": "MEC_MOTOR",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["onibus_id"] is None


# ─── 11 — sem RE digitado e sem sugestão de escala -> NAO_INFORMADO ─────

def test_escala_nao_resolve_grava_nao_informado(ambiente):
    onibus_id = _criar_onibus(ambiente["engine"], numero_frota=1722)
    with Session(ambiente["engine"]) as db:
        _criar_tipo_defeito(db, codigo="MEC_MOTOR")
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1722", "tipo_defeito_codigo": "MEC_MOTOR",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["onibus_id"] == str(onibus_id)

    with Session(ambiente["engine"]) as db:
        salva = db.get(RecolhidaAnormal, UUID(resp.json()["id"]))
    assert salva.origem_identificacao == "NAO_INFORMADO"
    assert salva.motorista_re is None


# ─── 12 — 🔴 ficha não pôde nascer: registra assim mesmo, motivo preenchido ─

def test_ficha_nao_nasce_por_tipo_defeito_inexistente_registra_assim_mesmo(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1723)
    # Propositalmente SEM cadastrar TipoDefeito nenhum — abrir_ficha_de_recolhida
    # não acha 'COD_INEXISTENTE' no catálogo.
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1723", "tipo_defeito_codigo": "COD_INEXISTENTE",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["ficha_id"] is None
    assert corpo["ficha_falhou_motivo"]


# ─── 13 — recolhida com prefixo válido cria ficha ABERTA ────────────────

def test_recolhida_com_prefixo_valido_cria_ficha_aberta(ambiente):
    onibus_id = _criar_onibus(ambiente["engine"], numero_frota=1724)
    with Session(ambiente["engine"]) as db:
        _criar_tipo_defeito(db, codigo="MEC_FREIO")
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1724", "tipo_defeito_codigo": "MEC_FREIO", "relato": "Freio duro",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["ficha_id"] is not None
    assert corpo["ficha_falhou_motivo"] is None

    with Session(ambiente["engine"]) as db:
        ficha = db.get(FichaManutencao, UUID(corpo["ficha_id"]))
    assert ficha is not None
    assert ficha.onibus_id == onibus_id
    assert ficha.status == StatusFichaEnum.ABERTA
    assert "[Recolhida anormal]" in ficha.descricao


# ─── 14 — controlador tentando avaliar -> 403 (sem manutencao escrever) ─

def test_controlador_avaliar_recolhida_nega_403(ambiente):
    _como(ambiente, "CONTROLADOR")
    recolhida_id = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9998", "tipo_defeito_codigo": "MEC_MOTOR",
    }).json()["id"]

    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao", json={"avaliacao": "RETIDO"}
    )
    assert resp.status_code == 403, resp.text


# ─── 15 — mecânico: LIBERADO sem prazo rejeitado; com prazo -> AVALIADA ─

def test_mecanico_avalia_liberado_exige_prazo(ambiente):
    _como(ambiente, "CONTROLADOR")
    recolhida_id = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9997", "tipo_defeito_codigo": "MEC_MOTOR",
    }).json()["id"]

    _como(ambiente, "MECANICO")
    sem_prazo = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao", json={"avaliacao": "LIBERADO"}
    )
    assert sem_prazo.status_code == 422, sem_prazo.text

    com_prazo = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao",
        json={"avaliacao": "LIBERADO", "prazo_minutos": 30},
    )
    assert com_prazo.status_code == 200, com_prazo.text
    corpo = com_prazo.json()
    assert corpo["status"] == "AVALIADA"
    assert corpo["prazo_minutos"] == 30

    retido = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao", json={"avaliacao": "RETIDO"}
    )
    assert retido.status_code == 200, retido.text
    assert retido.json()["status"] == "AVALIADA"


# ─── 16 — 🔴 data_referencia às 21h em São Paulo é HOJE, não amanhã ────

def test_recolhida_data_referencia_21h_sao_paulo_nao_vira_amanha(ambiente, monkeypatch):
    momento_sp = datetime(2026, 8, 20, 21, 30, tzinfo=FUSO_OPERACAO)
    momento_utc = momento_sp.astimezone(timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return momento_utc.replace(tzinfo=None)
            return momento_utc.astimezone(tz)

    monkeypatch.setattr(portaria_recolhidas_router_mod, "datetime", _DatetimeFixo)

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9996", "tipo_defeito_codigo": "MEC_MOTOR",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["data_referencia"] == "2026-08-20"


# ─── 17 — fila/contagem/análise respondem (endpoints além dos 4 do prompt) ─

def test_pendentes_contagem_e_analise_respondem(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9995", "tipo_defeito_codigo": "MEC_MOTOR",
    })

    _como(ambiente, "MECANICO")
    pendentes = ambiente["http"].get("/portaria/recolhidas/pendentes")
    assert pendentes.status_code == 200, pendentes.text
    assert len(pendentes.json()) == 1

    contagem = ambiente["http"].get("/portaria/recolhidas/contagem-pendentes")
    assert contagem.status_code == 200, contagem.text
    assert contagem.json()["total"] == 1

    _como(ambiente, "ENCARREGADO")
    analise = ambiente["http"].get("/portaria/recolhidas/analise")
    assert analise.status_code == 200, analise.text
    corpo = analise.json()
    assert any(item["chave"] == "9995" for item in corpo["por_prefixo"])


# ============================================================================
# §2.9-A — a escala não pode se perder na virada da madrugada
# ============================================================================

# ─── A — recolhida 00:30 de quinta, escala de quarta 23:00 -> resolve ────

def test_sugestao_de_escala_atravessa_meia_noite(ambiente, monkeypatch):
    quinta_00h30_sp = datetime(2026, 8, 20, 0, 30, tzinfo=FUSO_OPERACAO)  # quinta
    quarta = quinta_00h30_sp.date() - timedelta(days=1)
    momento_utc = quinta_00h30_sp.astimezone(timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return momento_utc.replace(tzinfo=None)
            return momento_utc.astimezone(tz)

    monkeypatch.setattr(portaria_recolhidas_router_mod, "datetime", _DatetimeFixo)

    with Session(ambiente["engine"]) as db:
        onibus_id = _criar_onibus(ambiente["engine"], numero_frota=1726)
        linha = _criar_linha(db)
        motorista = _criar_motorista(db, re="50002", nome="Ciclana Souza")
        _criar_escala(
            db, onibus_id=onibus_id, motorista_id=motorista.id, linha_id=linha.id,
            data=quarta, horario_saida=time(23, 0),
        )
        db.commit()

    _como(ambiente, "CONTROLADOR")
    sugestao = ambiente["http"].get("/portaria/recolhidas/resolver-prefixo?prefixo=1726")
    assert sugestao.status_code == 200, sugestao.text
    corpo = sugestao.json()
    assert corpo["motorista_re_sugerido"] == "50002"
    assert corpo["motorista_nome_sugerido"] == "Ciclana Souza"

    with Session(ambiente["engine"]) as db:
        _criar_tipo_defeito(db, codigo="MEC_MOTOR")
        db.commit()
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1726", "tipo_defeito_codigo": "MEC_MOTOR", "motorista_re": "50002",
    })
    assert resp.status_code == 201, resp.text
    with Session(ambiente["engine"]) as db:
        salva = db.get(RecolhidaAnormal, UUID(resp.json()["id"]))
    assert salva.origem_identificacao == "ESCALA"


# ============================================================================
# BLOCO G — motivo da recolhida (§5.1 do prompt; numeração própria do §5.1
# — 17 a 20 — reaproveita os mesmos números do §2.8, prefixados "G" aqui
# pra não colidir com o teste de fila/contagem/análise acima).
# ============================================================================

# ─── G17 — motivo=FALTA_MOTORISTA não gera ficha ────────────────────────

def test_motivo_falta_motorista_nao_gera_ficha(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1727)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1727", "motivo": "FALTA_MOTORISTA",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["ficha_id"] is None
    assert corpo["ficha_falhou_motivo"]
    assert "FALTA_MOTORISTA" in corpo["ficha_falhou_motivo"]


# ─── G18 — motivo=DEFEITO com prefixo válido cria ficha ─────────────────

def test_motivo_defeito_com_prefixo_valido_cria_ficha(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1728)
    with Session(ambiente["engine"]) as db:
        _criar_tipo_defeito(db, codigo="MEC_MOTOR")
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1728", "motivo": "DEFEITO", "tipo_defeito_codigo": "MEC_MOTOR",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["ficha_id"] is not None


# ─── G19 — motivo=DEFEITO sem tipo_defeito_codigo -> 422 ────────────────

def test_motivo_defeito_sem_tipo_defeito_e_rejeitado(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1729", "motivo": "DEFEITO",
    })
    assert resp.status_code == 422, resp.text


# ─── G20 — motivo=COLISAO sem tipo_defeito_codigo -> aceito, sem ficha ──

def test_motivo_colisao_sem_tipo_defeito_e_aceito(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1730)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1730", "motivo": "COLISAO",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["ficha_id"] is None
    assert "COLISAO" in corpo["ficha_falhou_motivo"]


# ============================================================================
# §5.3 — Resolvedor de RE (funcionario/motorista)
# ============================================================================

# ─── 29 — RE que existe só em funcionario -> resolve FUNCIONARIO ────────

def test_resolver_re_encontra_em_funcionario(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get(f"/portaria/resolver-re?re={_ENCARREGADO.re}")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["encontrado"] is True
    assert corpo["origem"] == "FUNCIONARIO"
    assert corpo["nome"] == _ENCARREGADO.nome
    assert corpo["ativo"] is True


# ─── 30 — 🔴 RE que existe só em motorista -> resolve MOTORISTA ─────────

def test_resolver_re_encontra_em_motorista(ambiente):
    with Session(ambiente["engine"]) as db:
        _criar_motorista(db, re="50005", nome="Beltrano Lima")
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/resolver-re?re=50005")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["encontrado"] is True
    assert corpo["origem"] == "MOTORISTA"
    assert corpo["nome"] == "Beltrano Lima"


# ─── 31 — RE inexistente -> 200, encontrado=false, nunca 404 ────────────

def test_resolver_re_inexistente_devolve_200_vazio(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/resolver-re?re=999999")
    assert resp.status_code == 200, resp.text
    assert resp.json()["encontrado"] is False


# ─── 32 — RE de motorista desligado -> resolve com ativo=false ──────────

def test_resolver_re_pessoa_desligada_resolve_com_ativo_false(ambiente):
    with Session(ambiente["engine"]) as db:
        db.add(Motorista(id=uuid4(), re="50006", nome="Desligado Teste", status="DESLIGADO"))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/resolver-re?re=50006")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["encontrado"] is True
    assert corpo["ativo"] is False


# ─── 33 — 🔴 resposta nunca contém CPF/RG/CNH/telefone ──────────────────

def test_resolver_re_nao_devolve_dado_sensivel(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get(f"/portaria/resolver-re?re={_ENCARREGADO.re}")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    for campo in ("cpf", "rg", "cnh", "telefone"):
        assert campo not in corpo
