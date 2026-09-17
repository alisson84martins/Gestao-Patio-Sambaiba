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
import re
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import FUSO_OPERACAO
from app.core.database import Base, get_db
from app.core.deps import get_current_funcionario
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.catalogos import Linha, TipoDefeito
from app.models.enums import OrigemEscalaEnum, SetorEnum, StatusFichaEnum, TipoEscalaEnum
from app.models.operacoes import Escala, FichaManutencao, ImportacaoEscala
from app.models.pessoas import Motorista, Usuario
from app.models.portaria import (
    AvariaConstatacao, AvariaContestacao, AvariaSaida, AvariaTipo, AvariaZona, Credencial, EmpresaTerceira,
    MovimentoPortaria, PortariaLocal, PortariaSetor, RecolhidaAnormal, VeiculoPortaria, VeiculoSituacaoHist,
)
from app.models.pre_cadastro import PessoaPreCadastro
from app.routers import portaria as portaria_router_mod
from app.routers import portaria_avarias as portaria_avarias_router_mod
from app.routers import portaria_recolhidas as portaria_recolhidas_router_mod
from app.routers import portaria_veiculos as portaria_veiculos_router_mod
from app.services import leitura_placa as leitura_placa_service_mod
from app.services import pre_cadastro as pre_cadastro_service_mod
from app.services.leitura_placa import LeituraPlacaResultado

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
    Funcionario.__table__, PortariaLocal.__table__, PortariaSetor.__table__, EmpresaTerceira.__table__,
    VeiculoPortaria.__table__, VeiculoSituacaoHist.__table__, MovimentoPortaria.__table__,
    Credencial.__table__, RecolhidaAnormal.__table__, AvariaSaida.__table__,
    # Migration 042 (Fase 1) — catálogos e as duas tabelas de ciclo de vida.
    AvariaZona.__table__, AvariaTipo.__table__, AvariaConstatacao.__table__, AvariaContestacao.__table__,
    Motorista.__table__, Linha.__table__, TipoDefeito.__table__,
    ImportacaoEscala.__table__, Escala.__table__, Usuario.__table__, FichaManutencao.__table__,
    # Bloco H — a recolhida alimenta o pré-cadastro (services/pre_cadastro.py).
    PessoaPreCadastro.__table__,
    # Bloco A2 — GET /identidade/re/{re} lê funções ativas do funcionário.
    Funcao.__table__, FuncionarioFuncao.__table__,
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
# §2.4) + migration 037 (Bloco I, 24/08): REGISTRAR (recolhida_anormal, só
# o controlador) e TRATAR (recolhida_tratativa, mecânico + gerência) viraram
# recursos separados. CONTROLADOR_ACESSO escreve recolhida_anormal mas
# NUNCA tem recolhida_gerencial nem recolhida_tratativa. MECANICO PERDEU
# recolhida_anormal (é essa remoção que corrige o card PORTARIA que não
# abria — ver cabeçalho da migration 037) e ganhou recolhida_tratativa
# ler+escrever. `leitura_recolhida_ou_tratativa` é o par de GET /recolhidas
# (histórico bruto), lido pelas duas pontas — ver LeituraRecolhidaOuTratativa
# no router.
_PERMISSOES = {
    "CONTROLADOR": {
        "leitura_acesso": True, "escrita_acesso": True,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": False,
        "leitura_recolhida": True, "escrita_recolhida": True,
        "leitura_gerencial": False,
        "leitura_tratativa": False, "escrita_tratativa": False,
        "leitura_recolhida_ou_tratativa": True,
        # Bloco G — mesmo recurso acesso_veicular de "leitura_acesso"/
        # "escrita_acesso", mas dependência SEPARADA (routers/portaria_avarias.py
        # chama exige() de novo — cada chamada gera um callable novo, então
        # dependency_overrides precisa mirar os dois objetos).
        "leitura_acesso_avarias": True, "escrita_acesso_avarias": True,
        # Migration 042/Fase 2 — /encerrar exige `manutencao` escrever, não
        # `acesso_veicular`: quem confere a saída não é quem dá baixa.
        "escrita_manutencao": False,
        "leitura_acesso_ou_manutencao_avarias": True,
    },
    "ENCARREGADO": {
        "leitura_acesso": True, "escrita_acesso": False,
        "leitura_cadastro": True, "escrita_cadastro": True,
        "leitura_autorizacao": True, "escrita_autorizacao": True,
        "leitura_recolhida": True, "escrita_recolhida": False,
        "leitura_gerencial": True,
        "leitura_tratativa": True, "escrita_tratativa": False,
        "leitura_recolhida_ou_tratativa": True,
        "leitura_acesso_avarias": True, "escrita_acesso_avarias": False,
        "escrita_manutencao": False,
        "leitura_acesso_ou_manutencao_avarias": True,
    },
    "MECANICO": {
        "leitura_acesso": False, "escrita_acesso": False,
        "leitura_cadastro": False, "escrita_cadastro": False,
        "leitura_autorizacao": False, "escrita_autorizacao": False,
        "leitura_recolhida": False, "escrita_recolhida": False,
        "leitura_gerencial": False,
        "leitura_tratativa": True, "escrita_tratativa": True,
        "leitura_recolhida_ou_tratativa": True,
        "leitura_acesso_avarias": False, "escrita_acesso_avarias": False,
        # MECANICO tem `manutencao` escrever na produção real (migrations
        # 011/037/038) — é quem dá baixa na avaria (Fase 2).
        "escrita_manutencao": True,
        # 17/09, item [4] — MECANICO não tem acesso_veicular, mas tem
        # `manutencao` (ler+escrever na produção real) — é o par que faz
        # exige_qualquer("acesso_veicular", "manutencao") passar pra ele,
        # sem lhe dar leitura_acesso_avarias de verdade.
        "leitura_acesso_ou_manutencao_avarias": True,
    },
    "ADMIN": {chave: True for chave in (
        "leitura_acesso", "escrita_acesso", "leitura_cadastro",
        "escrita_cadastro", "leitura_autorizacao", "escrita_autorizacao",
        "leitura_recolhida", "escrita_recolhida", "leitura_gerencial",
        "leitura_tratativa", "escrita_tratativa", "leitura_recolhida_ou_tratativa",
        "leitura_acesso_avarias", "escrita_acesso_avarias", "escrita_manutencao",
        "leitura_acesso_ou_manutencao_avarias",
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
        # R6/migration 041 — mesmas três categorias fechadas que o seed de
        # produção grava.
        setup.add(PortariaSetor(codigo="MANUTENCAO", nome="Manutenção", ordem=1, ativo=True))
        setup.add(PortariaSetor(codigo="OPERACAO", nome="Operação", ordem=2, ativo=True))
        setup.add(PortariaSetor(codigo="ADMINISTRACAO", nome="Administração", ordem=3, ativo=True))
        # Migration 042 — catálogo mínimo pros testes: os dois de backfill
        # (NAO_INFORMADA/NAO_INFORMADO, ativo=False) + duas zonas/tipos reais
        # pra exercitar o mapa/dedup sem precisar do seed de 58 linhas.
        setup.add(AvariaZona(
            codigo="NAO_INFORMADA", nome="Não informada", vista="INTERNO",
            regiao_ocorrencia="OUTRO", ordem=999, ativo=False,
        ))
        setup.add(AvariaZona(
            codigo="PARACHOQUE_DIANT", nome="Para-choque dianteiro", vista="FRENTE",
            regiao_ocorrencia="FRENTE", ordem=10, ativo=True,
        ))
        setup.add(AvariaZona(
            codigo="RETROVISOR_ESQ", nome="Retrovisor esquerdo", vista="FRENTE",
            regiao_ocorrencia="RETROVISOR", ordem=60, ativo=True,
        ))
        setup.add(AvariaTipo(codigo="NAO_INFORMADO", nome="Não informado", exige_conferencia=False, ordem=999, ativo=False))
        setup.add(AvariaTipo(codigo="AMASSADO", nome="Amassado", exige_conferencia=True, ordem=20, ativo=True))
        setup.add(AvariaTipo(codigo="TRINCADO", nome="Trincado", exige_conferencia=True, ordem=40, ativo=True))
        setup.add(AvariaTipo(codigo="RALADO", nome="Ralado", exige_conferencia=False, ordem=10, ativo=True))
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
        "leitura_tratativa": _dependency_de(portaria_recolhidas_router_mod.LeituraTratativa),
        "escrita_tratativa": _dependency_de(portaria_recolhidas_router_mod.EscritaTratativa),
        "leitura_recolhida_ou_tratativa": _dependency_de(
            portaria_recolhidas_router_mod.LeituraRecolhidaOuTratativa
        ),
        "leitura_acesso_avarias": _dependency_de(portaria_avarias_router_mod.LeituraAcesso),
        "escrita_acesso_avarias": _dependency_de(portaria_avarias_router_mod.EscritaAcesso),
        "escrita_manutencao": _dependency_de(portaria_avarias_router_mod.EscritaManutencao),
        # 17/09, item [4] — fila de avarias abertas no módulo Manutenção:
        # GET /avarias/catalogo e /avarias/historico passam a aceitar
        # acesso_veicular OU manutencao (exige_qualquer), dependência
        # SEPARADA de "leitura_acesso_avarias" acima.
        "leitura_acesso_ou_manutencao_avarias": _dependency_de(
            portaria_avarias_router_mod.LeituraAcessoOuManutencao
        ),
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


# ─── 1b — Bloco D (migration 035): dono com função de gestão nasce
#          AUTORIZADO direto, sem passar por PENDENTE, e grava histórico ──

def test_veiculo_de_gestor_nasce_autorizado_e_grava_historico(ambiente):
    with Session(ambiente["engine"]) as db:
        funcao = Funcao(
            id=uuid4(), codigo="ENCARREGADO", nome="Encarregado", categoria="OPERACAO",
            ativo=True, veiculo_auto_autorizado=True,
        )
        db.add(funcao)
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_DONO_A.id, funcao_id=funcao.id, ativo=True))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "funcionario_id": str(_DONO_A.id), "placa": "ABC1D23",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["situacao"] == "AUTORIZADO"
    assert corpo["situacao_motivo"] == "Autorização automática por função de gestão"
    assert corpo["situacao_por"] == str(_DONO_A.id)  # gestor responde pelo próprio carro

    with Session(ambiente["engine"]) as db:
        hist = db.execute(
            select(VeiculoSituacaoHist).where(VeiculoSituacaoHist.veiculo_id == UUID(corpo["id"]))
        ).scalars().all()
    assert len(hist) == 1
    assert hist[0].situacao_de is None
    assert hist[0].situacao_para == "AUTORIZADO"


def test_veiculo_de_funcao_sem_auto_autorizacao_continua_pendente(ambiente):
    """Confirma que a função ENCARREGADO da migration 035 só libera quando a
    coluna é TRUE — uma função qualquer, sem o flag, não muda nada (D6 continua)."""
    with Session(ambiente["engine"]) as db:
        funcao = Funcao(
            id=uuid4(), codigo="MOTORISTA", nome="Motorista", categoria="OPERACAO",
            ativo=True, veiculo_auto_autorizado=False,
        )
        db.add(funcao)
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_DONO_B.id, funcao_id=funcao.id, ativo=True))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "funcionario_id": str(_DONO_B.id), "placa": "BBB2222",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["situacao"] == "PENDENTE"


# ─── C1 (migration 039) — PARTICULAR sem funcionario_id não trava mais ──
# 🔴 Bug de produção: RE digitado que não resolve não podia mais recusar o
# cadastro (regra número um). re_dono_texto é o snapshot do que foi digitado.

def test_particular_com_re_dono_texto_sem_funcionario_id_cadastra(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "re_dono_texto": "12345", "placa": "ABC1D23",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["funcionario_id"] is None
    assert corpo["re_dono_texto"] == "12345"
    # Sem funcionario_id não há como saber se é gestor — nasce PENDENTE,
    # como qualquer outro (auto-autorização não se aplica aqui).
    assert corpo["situacao"] == "PENDENTE"


def test_particular_sem_funcionario_id_e_sem_re_dono_texto_e_422(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "placa": "ABC1D23",
    })
    assert resp.status_code == 422, resp.text


def test_particular_re_dono_texto_alimenta_pre_cadastro_origem_portaria_veiculo(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "re_dono_texto": "54321", "placa": "ABC1D23",
    })
    assert resp.status_code == 201, resp.text

    with Session(ambiente["engine"]) as db:
        pre = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "54321")
        ).scalar_one_or_none()
    assert pre is not None
    assert pre.ultima_origem == "PORTARIA_VEICULO"
    assert pre.papel_sugerido == "INDEFINIDO"


def test_divergencias_lista_autorizado_com_re_dono_texto_sem_funcionario(ambiente):
    """C1: a fila de Divergências (D13) passa a listar também o AUTORIZADO
    cujo dono nunca virou funcionário — antes só listava funcionário INATIVO."""
    _criar_veiculo(
        ambiente, placa="ABC1D23", propriedade="PARTICULAR", funcionario_id=None,
        re_dono_texto="65432", situacao="AUTORIZADO",
    )
    _como(ambiente, "ENCARREGADO")
    resp = ambiente["http"].get("/portaria/veiculos/divergencias")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert len(corpo) == 1
    assert corpo[0]["placa"] == "ABC1D23"
    assert corpo[0]["re_dono_texto"] == "65432"
    assert corpo[0]["funcionario_status"] == "NAO_CADASTRADO"


def test_busca_funcionario_portaria_expoe_auto_autorizado(ambiente):
    with Session(ambiente["engine"]) as db:
        funcao = Funcao(
            id=uuid4(), codigo="GERENTE_GERAL", nome="Gerente Geral", categoria="GESTAO",
            ativo=True, veiculo_auto_autorizado=True,
        )
        db.add(funcao)
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_DONO_A.id, funcao_id=funcao.id, ativo=True))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/funcionarios/busca", params={"q": _DONO_A.re})
    assert resp.status_code == 200, resp.text
    achado = next(f for f in resp.json() if f["id"] == str(_DONO_A.id))
    assert achado["auto_autorizado"] is True


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


# ─── C2 — RE de quem sai com veículo da frota: snapshot, nunca recusa ──

def test_movimento_empresa_com_re_registrado_de_nao_funcionario_grava_snapshot(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", propriedade="EMPRESA", situacao="AUTORIZADO")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "SAIDA", "placa": "ABC1D23",
        "re_registrado": "99999", "nome_registrado": "Motorista Desconhecido",
    })
    assert resp.status_code == 201, resp.text
    with Session(ambiente["engine"]) as db:
        salvo = db.execute(
            select(MovimentoPortaria).where(MovimentoPortaria.placa_registrada == "ABC1D23")
        ).scalar_one()
    assert salvo.funcionario_id is None
    assert salvo.re_registrado == "99999"
    assert salvo.nome_registrado == "Motorista Desconhecido"


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


# ─── P13 — leitura de placa por câmera grava origem/placa_lida_bruta ───
# Migration 040 + Bloco 3 do PROMPT-leitura-placa.md: a medição pós-
# produção depende só destes dois campos, sem tabela nova.

def test_movimento_via_camera_sem_correcao_grava_origem_e_bruta_iguais(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "ABC1D23",
        "origem": "CAMERA", "placa_lida_bruta": "ABC1D23",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["origem"] == "CAMERA"
    assert corpo["placa_lida_bruta"] == "ABC1D23"


def test_movimento_via_camera_com_correcao_grava_as_duas_diferentes(ambiente):
    """O controlador leu 'ABC1D24' na tela, corrigiu pra 'ABC1D23' antes de
    confirmar — P13: as duas ficam gravadas, é a diferença que interessa."""
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "ABC1D23",
        "origem": "CAMERA", "placa_lida_bruta": "ABC1D24",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["placa_registrada"] == "ABC1D23"
    assert corpo["placa_lida_bruta"] == "ABC1D24"
    assert corpo["placa_lida_bruta"] != corpo["placa_registrada"]


def test_movimento_manual_grava_placa_lida_bruta_nula(ambiente):
    """Digitado (origem default MANUAL) nunca tem placa_lida_bruta — é o
    que separa os dois grupos na consulta de medição do P13."""
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, situacao="AUTORIZADO")

    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "ABC1D23"})
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["origem"] == "MANUAL"
    assert corpo["placa_lida_bruta"] is None


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
    # Reversão de 2026-08-24 (§1.4 revisto): no veículo PARTICULAR a etiqueta
    # traz o RE do dono NO LUGAR da placa — só o RE, mais nada (a placa é
    # redundante: quem cola o adesivo é o próprio dono). Nome e CPF continuam
    # banidos do adesivo.
    assert _DONO_A.re in etiquetas.text
    assert "ABC1D23" not in etiquetas.text
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


# ─── 14 — controlador tentando avaliar -> 403 (sem recolhida_tratativa escrever) ─

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


# ============================================================================
# BLOCO H — a recolhida alimenta o pré-cadastro (§5.2 do prompt)
# ============================================================================

# ─── recolhida com RE digitado alimenta o pré-cadastro de motorista/cobrador ─

def test_recolhida_com_re_digitado_alimenta_pre_cadastro(ambiente):
    _criar_onibus(ambiente["engine"], numero_frota=1731)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "1731", "motivo": "FALTA_MOTORISTA",
        "motorista_re": "90001", "cobrador_re": "90002",
    })
    assert resp.status_code == 201, resp.text

    with Session(ambiente["engine"]) as db:
        motorista_pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "90001")
        ).scalar_one()
        cobrador_pc = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "90002")
        ).scalar_one()
    assert motorista_pc.papel_sugerido == "MOTORISTA"
    assert motorista_pc.ultima_origem == "PORTARIA_RECOLHIDA"
    assert cobrador_pc.papel_sugerido == "COBRADOR"


# ─── 25 — 🔴 falha forçada no pré-cadastro não impede a recolhida ───────
# ⚠️ Patch em pre_cadastro_service_mod._registrar, não em
# registrar_pessoa_vista — é o try/except de DENTRO de registrar_pessoa_vista
# que prova a regra número um; substituir a função inteira pularia a
# própria proteção que o teste quer verificar.

def test_falha_no_pre_cadastro_nao_impede_registro_da_recolhida(ambiente, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("falha simulada — não deveria propagar")

    monkeypatch.setattr(pre_cadastro_service_mod, "_registrar", _explode)

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9994", "motivo": "OUTRO", "motorista_re": "90003",
    })
    assert resp.status_code == 201, resp.text


# ============================================================================
# BLOCO I (prompt "Barra por módulo + RA como aba da Manutenção", 23/08) —
# encerramento da recolhida (migration 032, services/manutencao_recolhida.py
# ::encerrar_ficha_de_recolhida).
# ============================================================================

def _criar_recolhida_avaliada(ambiente, *, prefixo, com_ficha=True):
    """Registra e avalia (LIBERADO) uma recolhida, devolve (id, corpo da
    avaliação). com_ficha=True cadastra ônibus+tipo de defeito primeiro
    (motivo=DEFEITO default abre ficha automática); com_ficha=False usa
    motivo=OUTRO (nunca gera ficha — mesmo caminho de FALTA_MOTORISTA/
    COLISAO pro que importa aqui: ficha_id fica None)."""
    if com_ficha:
        _criar_onibus(ambiente["engine"], numero_frota=int(prefixo))
        with Session(ambiente["engine"]) as db:
            _criar_tipo_defeito(db, codigo="MEC_MOTOR")
            db.commit()
        payload = {"prefixo": prefixo, "tipo_defeito_codigo": "MEC_MOTOR"}
    else:
        payload = {"prefixo": prefixo, "motivo": "OUTRO"}

    _como(ambiente, "CONTROLADOR")
    criada = ambiente["http"].post("/portaria/recolhidas", json=payload)
    assert criada.status_code == 201, criada.text
    recolhida_id = criada.json()["id"]

    _como(ambiente, "MECANICO")
    avaliada = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao",
        json={"avaliacao": "LIBERADO", "prazo_minutos": 30},
    )
    assert avaliada.status_code == 200, avaliada.text
    return recolhida_id, avaliada.json()


# ─── I1 — encerrar SEM_DEFEITO: RA ENCERRADA, ficha CANCELADA ───────────

def test_encerrar_sem_defeito_cancela_ficha(ambiente):
    recolhida_id, avaliada = _criar_recolhida_avaliada(ambiente, prefixo="1740")
    ficha_id = avaliada["ficha_id"]
    assert ficha_id is not None

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SEM_DEFEITO", "encerramento_relato": "Testado, sem defeito encontrado"},
    )
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["status"] == "ENCERRADA"
    assert corpo["desfecho"] == "SEM_DEFEITO"
    assert corpo["encerramento_relato"] == "Testado, sem defeito encontrado"

    with Session(ambiente["engine"]) as db:
        ficha = db.get(FichaManutencao, UUID(ficha_id))
    assert ficha.status == StatusFichaEnum.CANCELADA


# ─── I2 — encerrar SERVICO_EXECUTADO: RA ENCERRADA, ficha CONCLUIDA ─────

def test_encerrar_servico_executado_conclui_ficha(ambiente):
    recolhida_id, avaliada = _criar_recolhida_avaliada(ambiente, prefixo="1741")
    ficha_id = avaliada["ficha_id"]

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SERVICO_EXECUTADO"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["desfecho"] == "SERVICO_EXECUTADO"

    with Session(ambiente["engine"]) as db:
        ficha = db.get(FichaManutencao, UUID(ficha_id))
    assert ficha.status == StatusFichaEnum.CONCLUIDA
    # services/manutencao_recolhida.py grava concluida_em explicitamente —
    # em produção o trigger fn_ficha_concluida_em faria o mesmo, mas o
    # SQLite dos testes não tem trigger nenhum.
    assert ficha.concluida_em is not None


# ─── I3 — encerrar RA ainda AGUARDANDO -> 409 (avalie antes de encerrar) ─

def test_encerrar_recolhida_aguardando_rejeitado_409(ambiente):
    _como(ambiente, "CONTROLADOR")
    criada = ambiente["http"].post("/portaria/recolhidas", json={"prefixo": "9988", "motivo": "OUTRO"})
    recolhida_id = criada.json()["id"]

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SEM_DEFEITO"},
    )
    assert resp.status_code == 409, resp.text


# ─── I4 — encerrar RA já ENCERRADA -> 409 (não reencerra) ───────────────

def test_encerrar_recolhida_ja_encerrada_rejeitado_409(ambiente):
    recolhida_id, _ = _criar_recolhida_avaliada(ambiente, prefixo="9987", com_ficha=False)

    _como(ambiente, "MECANICO")
    primeiro = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SEM_DEFEITO"},
    )
    assert primeiro.status_code == 200, primeiro.text

    segundo = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SEM_DEFEITO"},
    )
    assert segundo.status_code == 409, segundo.text


# ─── I5 — 🔴 encerrar RA sem ficha_id: 200, encerra, sem explosão ───────
# (regra número um — mesma que abrir_ficha_de_recolhida já respeita: nada
# do lado da ficha pode travar o registro/encerramento da recolhida)

def test_encerrar_recolhida_sem_ficha_encerra_sem_explosao(ambiente):
    recolhida_id, avaliada = _criar_recolhida_avaliada(ambiente, prefixo="9986", com_ficha=False)
    assert avaliada["ficha_id"] is None

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/encerramento",
        json={"desfecho": "SERVICO_EXECUTADO"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ENCERRADA"


# ─── I6 — §3.5: contagem/pendentes passam a somar AGUARDANDO + AVALIADA ─
# Decisão registrada no diff: as duas ainda exigem ação da manutenção
# (avaliar ou encerrar), e nenhum outro consumidor depende da semântica
# antiga (só AGUARDANDO) — ver comentário de _STATUS_PENDENTES no router.

def test_contagem_e_pendentes_somam_aguardando_e_avaliada(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/recolhidas", json={"prefixo": "9985", "motivo": "OUTRO"})
    id_avaliar = ambiente["http"].post(
        "/portaria/recolhidas", json={"prefixo": "9984", "motivo": "OUTRO"}
    ).json()["id"]

    _como(ambiente, "MECANICO")
    ambiente["http"].patch(f"/portaria/recolhidas/{id_avaliar}/avaliacao", json={"avaliacao": "RETIDO"})

    contagem = ambiente["http"].get("/portaria/recolhidas/contagem-pendentes")
    assert contagem.status_code == 200, contagem.text
    assert contagem.json()["total"] == 2

    pendentes = ambiente["http"].get("/portaria/recolhidas/pendentes")
    assert pendentes.status_code == 200, pendentes.text
    assert {item["status"] for item in pendentes.json()} == {"AGUARDANDO", "AVALIADA"}


# ============================================================================
# MIGRATION 037 (Bloco I, 24/08) — REGISTRAR (recolhida_anormal, controlador)
# separado de TRATAR (recolhida_tratativa, mecânico). Corrige o bug em que
# MECANICO via o card PORTARIA na tela de seleção (tinha recolhida_anormal,
# recurso do módulo PORTARIA) mas não conseguia abri-lo (sem
# acesso_veicular) — ver cabeçalho da migration.
# ============================================================================

# ─── I7 — 🔴 mecânico não registra mais recolhida (perdeu recolhida_anormal) ─

def test_mecanico_nao_registra_recolhida_pos_migration_037(ambiente):
    _como(ambiente, "MECANICO")
    resp = ambiente["http"].post("/portaria/recolhidas", json={"prefixo": "9979", "motivo": "OUTRO"})
    assert resp.status_code == 403, resp.text


# ─── I8 — mecânico continua avaliando/encerrando, agora via recolhida_tratativa ─
# (mesmo comportamento do teste 15/I1, só provando que a permissão nova —
# não mais `manutencao` — é o que autoriza)

def test_mecanico_avalia_via_recolhida_tratativa(ambiente):
    _como(ambiente, "CONTROLADOR")
    recolhida_id = ambiente["http"].post("/portaria/recolhidas", json={
        "prefixo": "9978", "motivo": "OUTRO",
    }).json()["id"]

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].patch(
        f"/portaria/recolhidas/{recolhida_id}/avaliacao",
        json={"avaliacao": "RETIDO"},
    )
    assert resp.status_code == 200, resp.text


# ─── I9 — 🔴 a lacuna que este bloco corrige: GET /recolhidas ("Encerradas
# hoje" da aba RA, manutencao.recolhidas.js) precisa continuar funcionando
# pro mecânico mesmo SEM recolhida_anormal — é por isso que o endpoint usa
# LeituraRecolhidaOuTratativa (exige_qualquer) em vez de só LeituraRecolhida.

def test_mecanico_le_recolhidas_por_tratativa_sem_recolhida_anormal(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/recolhidas", json={"prefixo": "9977", "motivo": "OUTRO"})

    _como(ambiente, "MECANICO")
    resp = ambiente["http"].get("/portaria/recolhidas")
    assert resp.status_code == 200, resp.text
    assert any(item["prefixo"] == "9977" for item in resp.json())


# ─── I10 — controlador continua lendo /recolhidas por recolhida_anormal ─
# (sem recolhida_tratativa nenhuma — prova que o OR não exige as duas)

def test_controlador_le_recolhidas_sem_recolhida_tratativa(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/recolhidas", json={"prefixo": "9976", "motivo": "OUTRO"})
    resp = ambiente["http"].get("/portaria/recolhidas")
    assert resp.status_code == 200, resp.text


# ============================================================================
# BLOCO A1 (prompt de ajustes 23/08) — base limpa de placa (D10 mantida)
# ============================================================================

# ─── placa fora do padrão nasce placa_atipica=true, mas cadastra normal ──

def test_cadastro_placa_fora_do_padrao_nasce_atipica_mas_nao_e_rejeitado(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "EMPRESA", "placa": "ABC12345",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["placa_atipica"] is True


def test_cadastro_placa_padrao_nasce_nao_atipica(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "EMPRESA", "placa": "abc-1d23",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["placa_atipica"] is False
    # D10 (core/placa.py::normalizar_placa) — sempre maiúscula, sem hífen.
    assert corpo["placa"] == "ABC1D23"


def test_atualizar_placa_recalcula_atipica(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)
    resp = ambiente["http"].patch(f"/portaria/veiculos/{veiculo_id}", json={"placa": "ZZZ999"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["placa_atipica"] is True


def test_filtro_placa_atipica_na_listagem(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id)
    # _criar_veiculo insere direto via ORM (sem passar por cadastrar_veiculo,
    # que é quem calcula placa_atipica) — precisa vir explícito aqui.
    _criar_veiculo(ambiente, placa="PROV1SORIA", funcionario_id=_DONO_B.id, placa_atipica=True)

    resp = ambiente["http"].get("/portaria/veiculos?placa_atipica=true")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert len(corpo) == 1
    assert corpo[0]["placa"] == "PROV1SORIA"


# ============================================================================
# BLOCO A2 (prompt de ajustes 23/08) — GET /identidade/re/{re}
# ============================================================================

def _como_identidade(ambiente, funcionario):
    """/identidade não usa exige() — é Depends(get_current_funcionario) puro
    (ver docstring de app/routers/identidade.py). Mesmo padrão de override
    de test_seguranca_health.py."""
    app.dependency_overrides[get_current_funcionario] = lambda: funcionario


def test_identidade_encontra_funcionario_com_funcoes_e_veiculo_particular(ambiente):
    with Session(ambiente["engine"]) as db:
        funcao = Funcao(id=uuid4(), codigo="COORDENADOR", nome="Coordenador", categoria="OPERACAO", ativo=True)
        db.add(funcao)
        db.add(FuncionarioFuncao(id=uuid4(), funcionario_id=_DONO_A.id, funcao_id=funcao.id, ativo=True))
        db.commit()
    _criar_veiculo(ambiente, placa="ABC1D23", funcionario_id=_DONO_A.id, propriedade="PARTICULAR")

    _como_identidade(ambiente, _CONTROLADOR)
    try:
        resp = ambiente["http"].get(f"/identidade/re/{_DONO_A.re}")
    finally:
        app.dependency_overrides.pop(get_current_funcionario, None)

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["encontrado"] is True
    assert corpo["origem"] == "FUNCIONARIO"
    assert corpo["funcoes"] == ["COORDENADOR"]
    assert len(corpo["veiculo_particular"]) == 1
    assert corpo["veiculo_particular"][0]["placa"] == "ABC1D23"


def test_identidade_inexistente_devolve_200_vazio(ambiente):
    _como_identidade(ambiente, _CONTROLADOR)
    try:
        resp = ambiente["http"].get("/identidade/re/999999")
    finally:
        app.dependency_overrides.pop(get_current_funcionario, None)
    assert resp.status_code == 200, resp.text
    assert resp.json()["encontrado"] is False


def test_identidade_nao_devolve_dado_sensivel(ambiente):
    _como_identidade(ambiente, _CONTROLADOR)
    try:
        resp = ambiente["http"].get(f"/identidade/re/{_ENCARREGADO.re}")
    finally:
        app.dependency_overrides.pop(get_current_funcionario, None)
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    for campo in ("cpf", "rg", "cnh", "telefone"):
        assert campo not in corpo


# ============================================================================
# BLOCO G — Avaria na saída da frota (migration 036)
# ============================================================================

def test_avaria_registrada_pelo_controlador_devolve_201(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1234", "descricao": "Retrovisor direito rachado.",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["prefixo"] == "1234"
    assert corpo["descricao"] == "Retrovisor direito rachado."
    assert corpo["data_servico"] is not None


def test_avaria_post_exige_escrita_em_acesso_veicular(ambiente):
    """RBAC do Bloco G — reaproveita acesso_veicular (migration 024), nenhum
    recurso novo. ENCARREGADO lê acesso_veicular mas não escreve -> 403."""
    _como(ambiente, "ENCARREGADO")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1234", "descricao": "Para-choque quebrado.",
    })
    assert resp.status_code == 403, resp.text


def test_avaria_get_esconde_registro_expirado(ambiente):
    vencida_id = uuid4()
    valida_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(AvariaSaida(
            id=vencida_id, prefixo="1234", data_servico=date(2026, 1, 1),
            descricao="Vencida — expira_em no passado.",
            zona_codigo="NAO_INFORMADA", tipo_codigo="NAO_INFORMADO", status="ABERTA",
            registrado_por=_CONTROLADOR.id,
            expira_em=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db.add(AvariaSaida(
            id=valida_id, prefixo="1234", data_servico=date(2026, 1, 1),
            descricao="Válida — expira_em no futuro.",
            zona_codigo="NAO_INFORMADA", tipo_codigo="NAO_INFORMADO", status="ABERTA",
            registrado_por=_CONTROLADOR.id,
            expira_em=datetime.now(timezone.utc) + timedelta(days=59),
        ))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].get("/portaria/avarias", params={"prefixo": "1234"})
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()]
    assert str(valida_id) in ids
    assert str(vencida_id) not in ids


def test_avaria_re_resolvido_usa_nome_do_cadastro_como_snapshot(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1234", "descricao": "Risco na lateral.",
        "motorista_re": _DONO_A.re, "motorista_nome": "nome digitado errado",
    })
    assert resp.status_code == 201, resp.text
    # RE resolveu em funcionario -> nome vem do cadastro, não do payload.
    assert resp.json()["motorista_nome"] == _DONO_A.nome


def test_avaria_re_nao_resolvido_alimenta_pre_cadastro(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1234", "descricao": "Farol trincado.",
        "motorista_re": "99999", "motorista_nome": "Motorista Desconhecido",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["motorista_nome"] == "Motorista Desconhecido"

    with Session(ambiente["engine"]) as db:
        pre = db.execute(
            select(PessoaPreCadastro).where(PessoaPreCadastro.re == "99999")
        ).scalar_one_or_none()
    assert pre is not None
    assert pre.ultima_origem == "PORTARIA_AVARIA"


# ============================================================================
# MIGRATION 042 (Fase 1) — mapa clicável, deduplicação e prova.
# Ver _handoff-claude/PROMPT-avaria-mapa-visual-2026-09-15.md, P4.
# Catálogo de teste (seedado em `ambiente`): zonas PARACHOQUE_DIANT e
# RETROVISOR_ESQ; tipos AMASSADO/TRINCADO (exige_conferencia) e RALADO.
# ============================================================================

def test_042_zona_tipo_inedito_cria_avaria_e_uma_constatacao(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1173", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
        "motorista_re": "14249",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["vezes_vista"] == 1
    assert corpo["ja_existia"] is False
    with Session(ambiente["engine"]) as db:
        constatacoes = db.execute(
            select(AvariaConstatacao).where(AvariaConstatacao.avaria_id == UUID(corpo["id"]))
        ).scalars().all()
    assert len(constatacoes) == 1


def test_042_mesmo_post_outro_re_acrescenta_constatacao_nao_cria_avaria(ambiente):
    """🔴 O teste que prova a frente inteira — o segundo motorista fica
    protegido sem criar avaria nova (P2, item 3)."""
    _como(ambiente, "CONTROLADOR")
    payload = {"prefixo": "1173", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO"}
    primeiro = ambiente["http"].post("/portaria/avarias", json={**payload, "motorista_re": "14249"})
    assert primeiro.status_code == 201, primeiro.text
    avaria_id = primeiro.json()["id"]

    segundo = ambiente["http"].post("/portaria/avarias", json={**payload, "motorista_re": "4338"})
    assert segundo.status_code == 200, segundo.text
    corpo = segundo.json()
    assert corpo["id"] == avaria_id
    assert corpo["ja_existia"] is True
    assert corpo["vezes_vista"] == 2

    with Session(ambiente["engine"]) as db:
        assert len(db.execute(select(AvariaSaida)).scalars().all()) == 1
        assert len(db.execute(select(AvariaConstatacao)).scalars().all()) == 2


def test_042_piorou_sem_observacao_e_422_com_observacao_sobe_severidade(ambiente):
    _como(ambiente, "CONTROLADOR")
    payload = {"prefixo": "1173", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO"}
    base = ambiente["http"].post("/portaria/avarias", json=payload)
    assert base.status_code == 201, base.text
    assert base.json()["severidade"] == "LEVE"

    sem_obs = ambiente["http"].post("/portaria/avarias", json={**payload, "piorou": True})
    assert sem_obs.status_code == 422, sem_obs.text

    com_obs = ambiente["http"].post(
        "/portaria/avarias",
        json={**payload, "piorou": True, "observacao": "Amassado ficou maior — outro motorista."},
    )
    assert com_obs.status_code == 200, com_obs.text
    assert com_obs.json()["severidade"] == "MEDIA"


def test_042_delete_avaria_com_constatacao_falha_por_integridade(ambiente):
    """A prova não se destrói — RESTRICT, ⛔ nunca CASCADE."""
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1173", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    avaria_id = UUID(resp.json()["id"])
    with Session(ambiente["engine"]) as db:
        avaria = db.get(AvariaSaida, avaria_id)
        db.delete(avaria)
        with pytest.raises(Exception):
            db.commit()


def test_042_rota_de_constatacao_nunca_tem_put_nem_delete(ambiente):
    """⚠️ Lê pelo OpenAPI, não por app.routes (armadilha_contagem_rotas_fastapi)."""
    caminhos = app.openapi()["paths"]
    rotas_constatacao = [p for p in caminhos if p.startswith("/portaria/constatacoes")]
    assert rotas_constatacao, "esperava pelo menos a rota de anular"
    for caminho in rotas_constatacao:
        metodos = set(caminhos[caminho].keys())
        assert "put" not in metodos
        assert "delete" not in metodos


def test_042_anular_constatacao_sem_nota_e_422_com_nota_mantem_linha(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1173", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    avaria_id = UUID(resp.json()["id"])
    with Session(ambiente["engine"]) as db:
        constatacao_id = db.execute(
            select(AvariaConstatacao).where(AvariaConstatacao.avaria_id == avaria_id)
        ).scalar_one().id

    sem_nota = ambiente["http"].post(f"/portaria/constatacoes/{constatacao_id}/anular", json={})
    assert sem_nota.status_code == 422, sem_nota.text

    com_nota = ambiente["http"].post(
        f"/portaria/constatacoes/{constatacao_id}/anular", json={"anulacao_nota": "Marcado por engano."}
    )
    assert com_nota.status_code == 200, com_nota.text
    assert com_nota.json()["anulada_em"] is not None

    with Session(ambiente["engine"]) as db:
        ainda_existe = db.get(AvariaConstatacao, constatacao_id)
    assert ainda_existe is not None
    assert ainda_existe.anulacao_nota == "Marcado por engano."


def test_042_avaria_reparada_e_mesmo_dano_visto_de_novo_cria_nova_com_ciclo_anterior(ambiente):
    _como(ambiente, "CONTROLADOR")
    primeira = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1173", "zona_codigo": "RETROVISOR_ESQ", "tipo_codigo": "TRINCADO",
    })
    avaria_id = primeira.json()["id"]

    _como(ambiente, "MECANICO")
    encerrar = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/encerrar", json={"encerramento": "REPARADA"})
    assert encerrar.status_code == 200, encerrar.text

    _como(ambiente, "CONTROLADOR")
    nova = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1173", "zona_codigo": "RETROVISOR_ESQ", "tipo_codigo": "TRINCADO",
    })
    assert nova.status_code == 201, nova.text
    corpo = nova.json()
    assert corpo["id"] != avaria_id
    assert corpo["ciclo_anterior"]["avaria_id"] == avaria_id
    assert corpo["ciclo_anterior"]["encerramento"] == "REPARADA"


def test_042_reabrir_id_volta_avaria_encerrada_pra_aberta_sem_linha_nova(ambiente):
    _como(ambiente, "CONTROLADOR")
    primeira = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "2344", "zona_codigo": "RETROVISOR_ESQ", "tipo_codigo": "TRINCADO",
    })
    avaria_id = primeira.json()["id"]
    _como(ambiente, "MECANICO")
    ambiente["http"].post(f"/portaria/avarias/{avaria_id}/encerrar", json={"encerramento": "NAO_EXISTIA"})

    _como(ambiente, "CONTROLADOR")
    reaberta = ambiente["http"].post("/portaria/avarias", json={"prefixo": "2344", "reabrir_id": avaria_id})
    assert reaberta.status_code == 200, reaberta.text
    assert reaberta.json()["status"] == "ABERTA"
    assert reaberta.json()["id"] == avaria_id

    with Session(ambiente["engine"]) as db:
        total = len(db.execute(select(AvariaSaida).where(AvariaSaida.prefixo == "2344")).scalars().all())
    assert total == 1


def test_042_regra_das_duas_e_funcao_pura_de_pessoa_e_dia(ambiente):
    """Unidade da regra das duas (independe de wall-clock/get_data_servico):
    só fecha com um PAR pessoa-diferente + dia-diferente de verdade."""
    from app.routers.portaria_avarias import _duas_contestacoes_fecham

    class _C:
        def __init__(self, quem, dia):
            self.registrado_por = quem
            self.data_servico = dia

    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    p1, p2 = uuid4(), uuid4()

    assert _duas_contestacoes_fecham([_C(p1, d1)]) is False
    assert _duas_contestacoes_fecham([_C(p1, d1), _C(p1, d2)]) is False   # mesma pessoa
    assert _duas_contestacoes_fecham([_C(p1, d1), _C(p2, d1)]) is False  # mesmo dia
    assert _duas_contestacoes_fecham([_C(p1, d1), _C(p2, d2)]) is True


def test_042_duas_contestacoes_pessoas_e_dias_diferentes_encerra_sozinha_via_api(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1206", "zona_codigo": "RETROVISOR_ESQ", "tipo_codigo": "TRINCADO",
    })
    avaria_id = UUID(resp.json()["id"])

    # primeira contestação, semeada num dia de serviço anterior
    with Session(ambiente["engine"]) as db:
        db.add(AvariaContestacao(
            id=uuid4(), avaria_id=avaria_id, data_servico=date(2026, 9, 1),
            nota="não vi (turno 1)", registrado_por=_CONTROLADOR.id,
        ))
        db.commit()

    # mesmo usuário de novo -> 409 (UNIQUE barra o autoencerramento sozinho)
    _como(ambiente, "CONTROLADOR")
    repetida = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/contestar", json={"nota": "de novo"})
    assert repetida.status_code == 409, repetida.text

    # segunda pessoa, dia de serviço de hoje (diferente) -> fecha sozinha
    _como(ambiente, "ADMIN")
    segunda = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/contestar", json={"nota": "também não vi (turno 2)"})
    assert segunda.status_code == 201, segunda.text

    with Session(ambiente["engine"]) as db:
        avaria = db.get(AvariaSaida, avaria_id)
    assert avaria.status == "INEXISTENTE"
    assert avaria.encerramento == "NAO_EXISTIA"
    assert avaria.encerrada_por is None  # 🔴 encerramento do SISTEMA, não de pessoa


def test_042_expira_em_365_dias_avaria_antiga_ainda_vista_ontem_aparece(ambiente):
    avaria_id = uuid4()
    antiga = datetime.now(timezone.utc) - timedelta(days=200)
    ontem = datetime.now(timezone.utc) - timedelta(days=1)
    with Session(ambiente["engine"]) as db:
        db.add(AvariaSaida(
            id=avaria_id, prefixo="1500", data_servico=date(2026, 3, 1),
            descricao="Retrovisor trincado.", zona_codigo="RETROVISOR_ESQ", tipo_codigo="TRINCADO",
            status="ABERTA", primeira_vez_em=antiga, ultima_vez_em=ontem, vezes_vista=1,
            registrado_por=_CONTROLADOR.id, expira_em=ontem + timedelta(days=365),
        ))
        db.commit()

    _como(ambiente, "CONTROLADOR")
    # com a regra velha (60 dias) esta avaria NÃO apareceria — é a prova de que a 042 pegou.
    resp = ambiente["http"].get("/portaria/avarias", params={"prefixo": "1500"})
    assert resp.status_code == 200, resp.text
    assert any(item["id"] == str(avaria_id) for item in resp.json())

    seguiu = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1500", "zona_codigo": "RETROVISOR_ESQ", "tipo_codigo": "TRINCADO",
    })
    assert seguiu.status_code == 200, seguiu.text
    with Session(ambiente["engine"]) as db:
        atualizado = db.get(AvariaSaida, avaria_id)
    # SQLite devolve datetime naive depois do round-trip; normaliza pra UTC
    # antes de comparar (Postgres/produção usa TIMESTAMPTZ de verdade).
    expira_lida = atualizado.expira_em
    if expira_lida.tzinfo is None:
        expira_lida = expira_lida.replace(tzinfo=timezone.utc)
    assert expira_lida > ontem + timedelta(days=365) - timedelta(seconds=5)


def test_042_encerrar_exige_manutencao_contestar_so_acesso_veicular(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1600", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    avaria_id = resp.json()["id"]

    negar = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/encerrar", json={"encerramento": "REPARADA"})
    assert negar.status_code == 403, negar.text

    contestar = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/contestar", json={"nota": "não vi"})
    assert contestar.status_code == 201, contestar.text

    _como(ambiente, "MECANICO")
    permitir = ambiente["http"].post(f"/portaria/avarias/{avaria_id}/encerrar", json={"encerramento": "REPARADA"})
    assert permitir.status_code == 200, permitir.text


def test_042_get_motorista_re_devolve_avarias_que_ele_constatou(ambiente):
    """🟢 'Tudo que aquele RE já marcou' — via CONSTATAÇÃO, não só o RE de
    quem abriu a avaria (P2)."""
    _como(ambiente, "CONTROLADOR")
    primeira = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1700", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
        "motorista_re": "14249",
    })
    avaria_id = primeira.json()["id"]
    ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1700", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
        "motorista_re": "4338",
    })
    outro = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "9999", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
        "motorista_re": "1111",
    })

    resp = ambiente["http"].get("/portaria/avarias", params={"motorista_re": "4338"})
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()]
    assert avaria_id in ids
    assert outro.json()["id"] not in ids


def test_042_seed_da_migration_toda_regiao_ocorrencia_dentro_do_vocabulario():
    """Estático — parseia o seed da própria migration 042 (⛔ não depende de
    banco) e prova, de novo, os 58 zonas + os 10 valores de
    REGIOES_AVARIA (ocorrencia.vocabulario.js / migration 012)."""
    caminho = Path(__file__).resolve().parents[2] / "database" / "migrations" / "042-avaria-mapa-e-deduplicacao.sql"
    texto = caminho.read_text(encoding="utf-8")
    permitidos = {
        "FRENTE", "TRASEIRA", "LATERAL_ESQUERDA", "LATERAL_DIREITA", "TETO",
        "INTERIOR", "RODADO", "RETROVISOR", "PARABRISA", "OUTRO",
    }
    inicio = texto.index("INSERT INTO portaria.avaria_zona (codigo")
    fim = texto.index("ON CONFLICT (codigo) DO NOTHING;", inicio)
    bloco = texto[inicio:fim]
    linhas = re.findall(r"\('([A-Z0-9_]+)',\s*'([^']*)',\s*'([A-Z_]+)',\s*'([A-Z_]+)',", bloco)
    assert len(linhas) == 59, f"esperava 59 zonas no seed, achei {len(linhas)}"
    regioes = {regiao for (_codigo, _nome, _vista, regiao) in linhas}
    assert regioes <= permitidos, regioes - permitidos


def test_042_post_sem_linha_codigo_devolve_201(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1800", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    assert resp.status_code == 201, resp.text


def test_042_linha_codigo_fora_do_catalogo_aceito_como_snapshot(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "1900", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
        "linha_codigo": "9999-INEXISTENTE",
    })
    assert resp.status_code == 201, resp.text
    with Session(ambiente["engine"]) as db:
        constatacao = db.execute(
            select(AvariaConstatacao).where(AvariaConstatacao.avaria_id == UUID(resp.json()["id"]))
        ).scalar_one()
    assert constatacao.linha_codigo == "9999-INEXISTENTE"


def test_042_duas_constatacoes_linhas_diferentes_convivem(ambiente):
    _como(ambiente, "CONTROLADOR")
    payload = {"prefixo": "2000", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO"}
    primeira = ambiente["http"].post("/portaria/avarias", json={**payload, "linha_codigo": "1726-10"})
    avaria_id = primeira.json()["id"]
    segunda = ambiente["http"].post("/portaria/avarias", json={**payload, "linha_codigo": "8022"})
    assert segunda.status_code == 200, segunda.text

    with Session(ambiente["engine"]) as db:
        linhas = {
            c.linha_codigo for c in db.execute(
                select(AvariaConstatacao).where(AvariaConstatacao.avaria_id == UUID(avaria_id))
            ).scalars().all()
        }
    assert linhas == {"1726-10", "8022"}


def test_042_historico_devolve_re_e_nome_e_e_compartilhado(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "2100", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    assert resp.status_code == 201, resp.text

    _como(ambiente, "ADMIN")  # outro controlador lendo o que o CONTROLADOR registrou
    hist = ambiente["http"].get("/portaria/avarias/historico", params={"prefixo": "2100"})
    assert hist.status_code == 200, hist.text
    itens = hist.json()
    assert len(itens) == 1
    assert itens[0]["registrado_por_re"] == _CONTROLADOR.re
    assert itens[0]["registrado_por_nome"] == _CONTROLADOR.nome


def test_042_historico_e_catalogo_leitura_tambem_por_manutencao(ambiente):
    """17/09, item [4] — a fila de avarias abertas do módulo Manutenção
    (Fase 2) lista via este mesmo /historico. MECANICO não tem
    acesso_veicular (migration 037 tirou até recolhida_anormal dele de
    propósito), mas tem `manutencao` — LeituraAcessoOuManutencao
    (exige_qualquer) deixa passar. Sem isso a fila não lista nada."""
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/avarias", json={
        "prefixo": "2200", "zona_codigo": "PARACHOQUE_DIANT", "tipo_codigo": "AMASSADO",
    })
    assert resp.status_code == 201, resp.text

    _como(ambiente, "MECANICO")
    cat = ambiente["http"].get("/portaria/avarias/catalogo")
    assert cat.status_code == 200, cat.text

    hist = ambiente["http"].get("/portaria/avarias/historico", params={"status": "ABERTA"})
    assert hist.status_code == 200, hist.text
    assert any(item["prefixo"] == "2200" for item in hist.json())


def test_042_historico_sem_acesso_veicular_e_sem_manutencao_e_403(ambiente):
    """A trava continua existindo — exige_qualquer só alargou pras duas
    pontas certas (acesso_veicular OU manutencao), não virou leitura
    livre. Nega a dependência combinada direto, porque nenhum papel do
    fixture hoje fica sem as duas pontas ao mesmo tempo."""
    _como(ambiente, "CONTROLADOR")
    dep = ambiente["leitura_acesso_ou_manutencao_avarias"]
    ambiente["http"].app.dependency_overrides[dep] = _negar()
    resp = ambiente["http"].get("/portaria/avarias/historico")
    assert resp.status_code == 403, resp.text


def test_042_rotas_literais_de_avaria_antes_do_path_param_no_openapi(ambiente):
    """⚠️ Lê pelo OpenAPI, não por app.routes (armadilha_contagem_rotas_fastapi)."""
    caminhos = list(app.openapi()["paths"].keys())
    for esperado in (
        "/portaria/avarias/catalogo", "/portaria/avarias/mapa", "/portaria/avarias/historico",
        "/portaria/avarias/{avaria_id}", "/portaria/avarias/{avaria_id}/contestar",
        "/portaria/avarias/{avaria_id}/encerrar", "/portaria/constatacoes/{constatacao_id}/anular",
        "/portaria/catalogo/linhas",
    ):
        assert esperado in caminhos, esperado

    assert caminhos.index("/portaria/avarias/catalogo") < caminhos.index("/portaria/avarias/{avaria_id}")
    assert caminhos.index("/portaria/avarias/mapa") < caminhos.index("/portaria/avarias/{avaria_id}")
    assert caminhos.index("/portaria/avarias/historico") < caminhos.index("/portaria/avarias/{avaria_id}")


# ============================================================================
# Bloco 1 — POST /portaria/ler-placa (leitura de placa por câmera, motor
# pluggável). Ver _handoff-claude/PROMPT-leitura-placa.md, P1-P14 e §5.
# ============================================================================

_JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 16
_EXE_DISFARCADO = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 16


class _SettingsLeituraPlaca:
    """Fake mínimo de Settings — só o único campo que o endpoint lê. Mesmo
    padrão de _SettingsComN8N em test_pre_ocorrencia.py."""

    def __init__(self, ativa: bool):
        self.leitura_placa_ativa = ativa


def _ativar_leitura_placa(monkeypatch, ativa: bool = True):
    monkeypatch.setattr(portaria_router_mod, "get_settings", lambda: _SettingsLeituraPlaca(ativa))


def test_ler_placa_sem_token_recebe_401(ambiente):
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 401


def test_ler_placa_sem_recurso_acesso_veicular_recebe_403(ambiente, monkeypatch):
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "MECANICO")  # leitura_acesso=False (§Bloco D)
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 403


def test_ler_placa_desativada_por_padrao_recusa_mesmo_com_permissao(ambiente, monkeypatch):
    """P14 — o interruptor vem antes de qualquer outra checagem, mesmo
    pra quem tem o recurso todo."""
    _ativar_leitura_placa(monkeypatch, False)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 403
    assert "desativada" in resp.json()["erro"].lower()


def test_ler_placa_formato_nao_suportado_recebe_415(ambiente, monkeypatch):
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("relatorio.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert resp.status_code == 415


def test_ler_placa_assinatura_nao_bate_recebe_415(ambiente, monkeypatch):
    """SEV-13: Content-Type mentindo (.exe disfarçado de JPEG) — mesmo
    cuidado de test_seguranca_upload.py aplicado aqui."""
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _EXE_DISFARCADO, "image/jpeg")},
    )
    assert resp.status_code == 415


def test_ler_placa_stub_sem_engine_devolve_placa_lida_nula(ambiente, monkeypatch):
    """Sem engine plugada (stub padrão de app/services/leitura_placa.py), o
    endpoint responde 200 com 'não achou' — nunca 500. É o que sustenta
    P6/P7 mesmo antes de qualquer motor real existir."""
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"placa_lida": None, "confianca": 0.0}


def test_ler_placa_normaliza_saida_da_engine(ambiente, monkeypatch):
    """abc-1d23 chega como ABC1D23 — mesma normalizar_placa usada em todo
    o resto do módulo (§5 do prompt)."""
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    monkeypatch.setattr(
        leitura_placa_service_mod, "reconhecer_placa",
        lambda imagem: LeituraPlacaResultado(placa_lida="abc-1d23", confianca=0.87),
    )
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"placa_lida": "ABC1D23", "confianca": 0.87}


def test_ler_placa_engine_devolve_placa_atipica_sem_recusar(ambiente, monkeypatch):
    """D10 — placa fora do formato (provisória, outro país) não vira 422
    aqui, igual ao resto do módulo: só normaliza, nunca valida formato."""
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    monkeypatch.setattr(
        leitura_placa_service_mod, "reconhecer_placa",
        lambda imagem: LeituraPlacaResultado(placa_lida="XX 999", confianca=0.4),
    )
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"placa_lida": "XX999", "confianca": 0.4}


def test_ler_placa_nenhum_arquivo_novo_aparece_no_disco(ambiente, monkeypatch, tmp_path):
    """🔴 P4 — a garantia escrita: mesmo com a engine 'achando' uma placa,
    nenhum byte da imagem toca o disco. cwd isolado num diretório vazio
    (mesma convenção de UPLOAD_ROOT = Path('uploads'), relativo ao cwd, em
    ocorrencias.py) — se algum código futuro tentar 'salvar a foto pra
    depois' com um caminho relativo, este teste denuncia."""
    monkeypatch.chdir(tmp_path)
    _ativar_leitura_placa(monkeypatch, True)
    _como(ambiente, "CONTROLADOR")
    monkeypatch.setattr(
        leitura_placa_service_mod, "reconhecer_placa",
        lambda imagem: LeituraPlacaResultado(placa_lida="ABC1D23", confianca=0.9),
    )
    resp = ambiente["http"].post(
        "/portaria/ler-placa",
        files={"arquivo": ("placa.jpg", _JPEG_VALIDO, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    assert list(tmp_path.rglob("*")) == []


# ============================================================================
# Motor fast-alpr (PROMPT-leitura-placa-engine.md, Bloco 1) — testa
# app/services/leitura_placa.py diretamente, isolado do endpoint. ⛔ NUNCA
# deixa `_construir_alpr()` rodar de verdade — nem baixa modelo, nem carrega
# a engine. Todo teste que toca `_alpr`/`_get_alpr` restaura via monkeypatch
# (reversão automática do pytest), então não vaza estado entre testes.
# ============================================================================

@pytest.fixture
def _imagem_jpeg_valida():
    """Um JPEG mínimo de verdade (não só magic bytes) — precisa passar por
    cv2.imdecode() de verdade, o que _JPEG_VALIDO (bytes fabricados à mão
    pra teste de assinatura) não garante fazer sem erro."""
    import cv2
    import numpy as np
    quadro = np.zeros((10, 10, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", quadro)
    assert ok
    return bytes(buf)


def test_reconhecer_placa_bytes_invalidos_devolve_nao_achou_sem_excecao(monkeypatch):
    """R4 — bytes que não decodificam como imagem nenhuma (nem chegam a
    tentar a engine) -> 'não achou', nunca uma exceção."""
    monkeypatch.setattr(leitura_placa_service_mod, "_alpr", None)
    resultado = leitura_placa_service_mod.reconhecer_placa(b"isto nao e uma imagem")
    assert resultado == LeituraPlacaResultado(placa_lida=None, confianca=0.0)


def test_reconhecer_placa_engine_lancando_erro_devolve_nao_achou(monkeypatch, _imagem_jpeg_valida):
    """R4 — imagem válida, mas a engine (._get_alpr()) explode -> 'não
    achou', sem propagar a exceção pro chamador (P7: nunca 500)."""
    def _get_alpr_que_falha():
        raise RuntimeError("engine fora do ar (simulado no teste)")

    monkeypatch.setattr(leitura_placa_service_mod, "_get_alpr", _get_alpr_que_falha)
    resultado = leitura_placa_service_mod.reconhecer_placa(_imagem_jpeg_valida)
    assert resultado == LeituraPlacaResultado(placa_lida=None, confianca=0.0)


def test_get_alpr_constroi_uma_vez_so_e_reaproveita(monkeypatch):
    """R1 — chamadas seguidas usam a MESMA instância; a construção (cara,
    ~1,6s de verdade) roda uma vez só."""
    chamadas = []

    class _AlprFalso:
        pass

    def _construir_falso():
        instancia = _AlprFalso()
        chamadas.append(instancia)
        return instancia

    monkeypatch.setattr(leitura_placa_service_mod, "_alpr", None)
    monkeypatch.setattr(leitura_placa_service_mod, "_construir_alpr", _construir_falso)

    primeira = leitura_placa_service_mod._get_alpr()
    segunda = leitura_placa_service_mod._get_alpr()

    assert len(chamadas) == 1
    assert primeira is segunda is chamadas[0]


def test_reconhecer_placa_escolhe_resultado_de_maior_confianca_ocr(monkeypatch, _imagem_jpeg_valida):
    """R4 — mais de uma placa na foto (carro atrás na fila) -> fica com a
    de maior confiança de OCR, não a primeira nem a última da lista."""
    class _Ocr:
        def __init__(self, text, confidence):
            self.text = text
            self.confidence = confidence

    class _Resultado:
        def __init__(self, ocr):
            self.ocr = ocr

    class _AlprFalso:
        def predict(self, quadro):
            return [
                _Resultado(_Ocr("ZZZ9999", 0.42)),
                _Resultado(_Ocr("ABC1D23", 0.97)),
                _Resultado(None),  # detecção sem OCR — precisa ser ignorada, não quebrar
            ]

    monkeypatch.setattr(leitura_placa_service_mod, "_alpr", _AlprFalso())
    resultado = leitura_placa_service_mod.reconhecer_placa(_imagem_jpeg_valida)
    assert resultado.placa_lida == "ABC1D23"
    assert resultado.confianca == pytest.approx(0.97)


def test_reconhecer_placa_confianca_por_caractere_vira_media(monkeypatch, _imagem_jpeg_valida):
    """OcrResult.confidence pode vir como lista (um valor por caractere) —
    normaliza pra média, não quebra nem devolve a lista crua."""
    class _Ocr:
        text = "ABC1D23"
        confidence = [0.9, 0.8, 1.0, 0.95]

    class _Resultado:
        ocr = _Ocr()

    class _AlprFalso:
        def predict(self, quadro):
            return [_Resultado()]

    monkeypatch.setattr(leitura_placa_service_mod, "_alpr", _AlprFalso())
    resultado = leitura_placa_service_mod.reconhecer_placa(_imagem_jpeg_valida)
    assert resultado.placa_lida == "ABC1D23"
    assert resultado.confianca == pytest.approx((0.9 + 0.8 + 1.0 + 0.95) / 4)


def test_warmup_engine_falhando_nao_propaga_excecao(monkeypatch):
    """R2 — warmup() nunca derruba o app: falha vira log, não exceção."""
    monkeypatch.setattr(leitura_placa_service_mod, "_alpr", None)

    def _construir_que_falha():
        raise RuntimeError("sem internet pra baixar o modelo (simulado)")

    monkeypatch.setattr(leitura_placa_service_mod, "_construir_alpr", _construir_que_falha)
    leitura_placa_service_mod.warmup()  # não deve lançar


# ============================================================================
# GET /portaria/recursos (PROMPT-leitura-placa-engine.md, Bloco 2, R6) —
# flags de recursos opcionais da Portaria, pra tela não depender do
# localStorage da sessão (armadilha já conhecida: sessão aberta não vê
# permissão/config nova).
# ============================================================================

def test_recursos_sem_token_recebe_401(ambiente):
    resp = ambiente["http"].get("/portaria/recursos")
    assert resp.status_code == 401


def test_recursos_sem_recurso_acesso_veicular_recebe_403(ambiente):
    _como(ambiente, "MECANICO")  # leitura_acesso=False (§Bloco D)
    resp = ambiente["http"].get("/portaria/recursos")
    assert resp.status_code == 403


def test_recursos_leitura_placa_ativa_reflete_a_configuracao(ambiente, monkeypatch):
    _como(ambiente, "CONTROLADOR")

    _ativar_leitura_placa(monkeypatch, True)
    ligado = ambiente["http"].get("/portaria/recursos")
    assert ligado.status_code == 200, ligado.text
    assert ligado.json() == {"leitura_placa_ativa": True}

    _ativar_leitura_placa(monkeypatch, False)
    desligado = ambiente["http"].get("/portaria/recursos")
    assert desligado.status_code == 200, desligado.text
    assert desligado.json() == {"leitura_placa_ativa": False}


# ============================================================================
# Migration 041 — frota de apoio, reservado e setor de destino (D18, R1-R6).
# Ver _handoff-claude/PROMPT-portaria-frota-e-empresa-2026-09-08.md, seção
# "Testes" (14 itens) — numeração dos comentários abaixo segue aquela lista.
# ============================================================================

def _entrada_retroativa(ambiente, *, placa=None, prefixo=None, minutos_atras, **extra):
    """Mesmo truque de test_dentro_deriva_ultimo_movimento_por_placa: RETROATIVO
    com `momento` explícito, minutos apartados — o relógio do SQLite só tem
    resolução de 1s."""
    agora = datetime.now(FUSO_OPERACAO)
    payload = {
        "sentido": extra.pop("sentido", "ENTRADA"),
        "origem": "RETROATIVO",
        "momento": (agora - timedelta(minutes=minutos_atras)).isoformat(),
        "observacao": "Lançamento de teste",
        **extra,
    }
    if placa:
        payload["placa"] = placa
    if prefixo:
        payload["prefixo"] = prefixo
    resp = ambiente["http"].post("/portaria/movimentos", json=payload)
    assert resp.status_code == 201, resp.text
    return resp


def _criar_empresa_terceira(ambiente, nome="Empresa Teste 041") -> UUID:
    empresa_id = uuid4()
    with Session(ambiente["engine"]) as db:
        db.add(EmpresaTerceira(id=empresa_id, nome=nome, ativo=True))
        db.commit()
    return empresa_id


# ─── 1 — P1 (backend): terceiro sem empresa cadastrada nunca é recusado ──

def test_terceiro_avulso_com_empresa_em_texto_registra_201(ambiente):
    """Garantia do P1: o frontend some (empresa deixa de travar o modal),
    o teste do backend fica — registrar_movimento já suportava isto antes
    da 041."""
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "ZZZ8888",
        "terceiro_empresa": "Posto Ipiranga (não cadastrado)",
        "terceiro_nome": "Fornecedor Teste",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["cadastrado"] is False
    assert corpo["terceiro_empresa"] == "Posto Ipiranga (não cadastrado)"
    assert any("não cadastrada" in a.lower() for a in corpo["avisos"])


# ─── 2 — GET /dentro separa particular/terceiro/avulso, exclui frota e ──
# reservado, contagem com as 3 chaves de GrupoPortaria (D18)

def test_dentro_separa_particular_terceiro_avulso_e_exclui_frota_e_reservado(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="PPP1111", funcionario_id=_DONO_A.id, situacao="AUTORIZADO", tipo="CARRO")
    empresa_id = _criar_empresa_terceira(ambiente)
    _criar_veiculo(ambiente, placa="TTT2222", propriedade="TERCEIRO", empresa_terceira_id=empresa_id, situacao="AUTORIZADO")
    _criar_veiculo(ambiente, placa="EEE3333", propriedade="EMPRESA", situacao="AUTORIZADO", tipo="GUINCHO")

    _entrada_retroativa(ambiente, placa="PPP1111", minutos_atras=40)
    _entrada_retroativa(ambiente, placa="TTT2222", minutos_atras=30)
    _entrada_retroativa(ambiente, placa="ZZZ9999", minutos_atras=20)  # avulso
    _entrada_retroativa(ambiente, placa="EEE3333", minutos_atras=10)  # frota — fora
    _entrada_retroativa(ambiente, prefixo="1234", minutos_atras=5)  # reservado — fora

    resp = ambiente["http"].get("/portaria/dentro")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    placas_dentro = {m["placa_registrada"] for m in corpo["dentro"]}
    assert placas_dentro == {"PPP1111", "TTT2222", "ZZZ9999"}
    assert corpo["contagem"] == {"PARTICULAR": 1, "TERCEIRO": 1, "AVULSO": 1}

    grupos = {m["placa_registrada"]: m["grupo"] for m in corpo["dentro"]}
    assert grupos == {"PPP1111": "PARTICULAR", "TTT2222": "TERCEIRO", "ZZZ9999": "AVULSO"}


# ─── 2b — P1: terceiro SEM cadastro (empresa sem cadastrar, ou placa ────
# desconhecida com condutor identificado) tem que cair em TERCEIRO, não
# em AVULSO — senão a correção do P1 empurra o prestador pra seção
# "Particular" da tela em vez de "Terceiros".

def test_dentro_terceiro_sem_cadastro_cai_em_terceiro_nao_em_avulso(ambiente):
    _como(ambiente, "CONTROLADOR")
    # __OUTRA__ sem marcar "cadastrar esta empresa": terceiro_empresa em
    # texto, sem veiculo_id (tabela 2 do P1 no prompt).
    _entrada_retroativa(
        ambiente, placa="OUT1111", minutos_atras=20,
        terceiro_empresa="Posto Ipiranga (não cadastrado)",
    )
    # Placa desconhecida, mas o controlador identificou quem dirigia —
    # ainda é visita de terceiro, não um avulso sem nome nenhum.
    _entrada_retroativa(
        ambiente, placa="OUT2222", minutos_atras=15,
        terceiro_nome="Motorista de Entrega",
    )
    # Avulso de verdade: nem placa cadastrada, nem terceiro_empresa, nem
    # terceiro_nome — aqui sim é AVULSO.
    _entrada_retroativa(ambiente, placa="OUT3333", minutos_atras=10)

    resp = ambiente["http"].get("/portaria/dentro")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    grupos = {m["placa_registrada"]: m["grupo"] for m in corpo["dentro"]}
    assert grupos == {"OUT1111": "TERCEIRO", "OUT2222": "TERCEIRO", "OUT3333": "AVULSO"}
    assert corpo["contagem"] == {"PARTICULAR": 0, "TERCEIRO": 2, "AVULSO": 1}


# ─── 3 — 🔴 exclusão de frota tem que estar DENTRO da subquery ──────────
# (senão uma entrada de frota antiga ainda vaza em sem_saida, que D17 não
# filtra por propriedade por conta própria)

def test_dentro_e_sem_saida_nunca_trazem_movimento_de_frota(ambiente):
    _como(ambiente, "CONTROLADOR")
    veiculo_id = _criar_veiculo(ambiente, placa="FFF4444", propriedade="EMPRESA", situacao="AUTORIZADO", tipo="VAN")

    with Session(ambiente["engine"]) as db:
        db.add(MovimentoPortaria(
            id=uuid4(), local_codigo="LEVES", sentido="ENTRADA",
            momento=datetime.now(timezone.utc) - timedelta(hours=40),
            data_referencia=date.today(), veiculo_id=veiculo_id,
            placa_registrada="FFF4444", cadastrado=True, origem="MANUAL",
            registrado_por=_CONTROLADOR.id,
        ))
        db.commit()

    resp = ambiente["http"].get("/portaria/dentro")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    placas = {m["placa_registrada"] for m in corpo["dentro"]} | {m["placa_registrada"] for m in corpo["sem_saida"]}
    assert "FFF4444" not in placas


# ─── 4 — GET /dentro não dispara query por item ─────────────────────────

def test_dentro_nao_dispara_query_por_item(ambiente):
    _como(ambiente, "CONTROLADOR")
    placas = [f"NQQ{i:04d}" for i in range(6)]
    for i, placa in enumerate(placas):
        _criar_veiculo(ambiente, placa=placa, funcionario_id=_DONO_A.id, situacao="AUTORIZADO")
        _entrada_retroativa(ambiente, placa=placa, minutos_atras=i + 1)

    queries: list[str] = []

    def _contar(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(ambiente["engine"], "before_cursor_execute", _contar)
    try:
        resp = ambiente["http"].get("/portaria/dentro")
    finally:
        event.remove(ambiente["engine"], "before_cursor_execute", _contar)

    assert resp.status_code == 200, resp.text
    selects = [q for q in queries if q.strip().upper().startswith("SELECT")]
    # Fixo (último-movimento + veículos, nunca setores aqui pois nenhum
    # setor_codigo foi usado) — bem menor que 1 por veículo (6).
    assert len(selects) <= 3, f"esperado poucas SELECTs fixas, veio {len(selects)}: {selects}"


# ─── 5 — GET /frota: na_rua × disponíveis (D18) ─────────────────────────

def test_frota_separa_na_rua_e_disponiveis(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_veiculo(ambiente, placa="MOT0001", propriedade="EMPRESA", situacao="AUTORIZADO", tipo="MOTO")
    _criar_veiculo(ambiente, placa="VAN0002", propriedade="EMPRESA", situacao="AUTORIZADO", tipo="VAN")
    _criar_veiculo(ambiente, placa="GUI0003", propriedade="EMPRESA", situacao="AUTORIZADO", tipo="GUINCHO")
    # GUI0003 nunca teve movimento -> disponível por ausência de histórico.

    _entrada_retroativa(
        ambiente, placa="MOT0001", minutos_atras=20, sentido="SAIDA",
        re_registrado="4102", nome_registrado="José Silva",
    )
    _entrada_retroativa(ambiente, placa="VAN0002", minutos_atras=30, sentido="SAIDA")
    _entrada_retroativa(ambiente, placa="VAN0002", minutos_atras=10, sentido="ENTRADA")

    resp = ambiente["http"].get("/portaria/frota")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    na_rua = {item["placa"]: item for item in corpo["na_rua"]}
    disponiveis = {item["placa"] for item in corpo["disponiveis"]}

    assert set(na_rua) == {"MOT0001"}
    assert na_rua["MOT0001"]["condutor_nome"] == "José Silva"
    assert na_rua["MOT0001"]["condutor_re"] == "4102"
    assert na_rua["MOT0001"]["desde"] is not None
    assert disponiveis == {"VAN0002", "GUI0003"}


# ─── 6 — cadastro aceita a frota de apoio, mas reservado não se cadastra ─

def test_cadastro_aceita_tipos_da_frota_de_apoio_mas_nao_onibus(ambiente):
    _como(ambiente, "CONTROLADOR")
    ok = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "EMPRESA", "placa": "GUI9999", "tipo": "GUINCHO",
    })
    assert ok.status_code == 201, ok.text

    recusado = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "EMPRESA", "placa": "ONI0001", "tipo": "ONIBUS",
    })
    assert recusado.status_code == 422, recusado.text


# ─── 7 — reservado com prefixo válido resolve onibus_id e fica fora do ──
# "dentro agora" (R1/P4)

def test_reservado_com_prefixo_valido_resolve_onibus_e_fica_fora_do_dentro(ambiente):
    _como(ambiente, "CONTROLADOR")
    onibus_id = _criar_onibus(ambiente["engine"], 1234)

    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "prefixo": "1234",
        "re_registrado": "5001", "nome_registrado": "Motorista Reservado",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["onibus_id"] == str(onibus_id)
    assert corpo["placa_registrada"] is None

    dentro = ambiente["http"].get("/portaria/dentro")
    assert dentro.status_code == 200, dentro.text
    assert "1234" not in [m.get("prefixo") for m in dentro.json()["dentro"]]


# ─── 7b — reservado sem hodômetro avisa (nunca bloqueia): reservado não ──
# tem veiculo cadastrado, então nunca passa pelo aviso de exige_hodometro
# de um VeiculoPortaria — mas o KM é pedido sempre mesmo assim (P4).

def test_reservado_sem_hodometro_avisa_mas_registra(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "prefixo": "9999"})
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert any("hodômetro" in a.lower() for a in corpo["avisos"])


def test_reservado_com_hodometro_nao_avisa(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "prefixo": "9999", "hodometro_km": 12345,
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert not any("hodômetro" in a.lower() for a in corpo["avisos"])


# ─── 8/9 — setor opcional: válido preenche destino, inexistente nunca ───
# bloqueia (regra número um aplicada ao campo novo — R4/R6)

def test_movimento_com_setor_valido_preenche_destino_pelo_nome(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "SET0001", "setor_codigo": "MANUTENCAO",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["setor_codigo"] == "MANUTENCAO"
    assert corpo["terceiro_destino"] == "Manutenção"


def test_movimento_com_setor_inexistente_nunca_bloqueia(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "SET0002", "setor_codigo": "NAO_EXISTE",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["setor_codigo"] is None
    assert any("setor" in a.lower() for a in corpo["avisos"])


# ─── 10 — histórico filtra por setor_codigo e por apenas_reservados ─────

def test_listar_movimentos_filtra_por_setor_e_por_reservado(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/movimentos", json={
        "sentido": "ENTRADA", "placa": "SET0003", "setor_codigo": "OPERACAO",
    })
    ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "SET0004"})
    ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "prefixo": "5678"})

    por_setor = ambiente["http"].get("/portaria/movimentos", params={"setor_codigo": "OPERACAO"})
    assert por_setor.status_code == 200, por_setor.text
    assert [m["placa_registrada"] for m in por_setor.json()] == ["SET0003"]

    reservados = ambiente["http"].get("/portaria/movimentos", params={"apenas_reservados": True})
    assert reservados.status_code == 200, reservados.text
    assert [m["prefixo"] for m in reservados.json()] == ["5678"]


# ─── 11 — GET /movimentos continua devolvendo MovimentoRead, nunca os ───
# campos de MovimentoDentroRead

def test_listar_movimentos_nunca_devolve_campos_de_dentro_enriquecido(ambiente):
    _como(ambiente, "CONTROLADOR")
    ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "placa": "SET0005"})
    resp = ambiente["http"].get("/portaria/movimentos", params={"placa": "SET0005"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()[0]
    for campo in ("veiculo_propriedade", "veiculo_tipo", "grupo"):
        assert campo not in corpo


# ─── 12 — placa/prefixo: um OU outro, nunca os dois nulos (espelho do ──
# ck_movimento_identificacao da migration 041 — R1.b)

def test_reservado_sem_placa_mantem_placa_registrada_nula(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "prefixo": "9999"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["placa_registrada"] is None


def test_movimento_sem_placa_e_sem_prefixo_e_422(ambiente):
    """A ÚNICA recusa nova deste trabalho — não é sobre a situação de
    ninguém (regra número um intacta), é sobre linha órfã."""
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA"})
    assert resp.status_code == 422, resp.text


def test_reservado_com_onibus_cadastrado_resolve_id_mas_nao_preenche_placa(ambiente):
    _como(ambiente, "CONTROLADOR")
    # Faixa válida de numero_frota é 1000-2999 (services/portaria.py::
    # resolver_onibus_por_prefixo) — mesma regra de routers/ocorrencias.py.
    onibus_id = _criar_onibus(ambiente["engine"], 2222)
    resp = ambiente["http"].post("/portaria/movimentos", json={"sentido": "ENTRADA", "prefixo": "2222"})
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["onibus_id"] == str(onibus_id)
    assert corpo["placa_registrada"] is None


# ─── 13 — R5: veículo EMPRESA nasce AUTORIZADO, com histórico auditável ─

def test_veiculo_empresa_nasce_autorizado_e_grava_historico_com_decidido_por(ambiente):
    _como(ambiente, "CONTROLADOR")
    resp = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "EMPRESA", "placa": "EMP0001", "tipo": "VAN",
    })
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["situacao"] == "AUTORIZADO"

    with Session(ambiente["engine"]) as db:
        hist = db.execute(
            select(VeiculoSituacaoHist).where(VeiculoSituacaoHist.veiculo_id == UUID(corpo["id"]))
        ).scalars().all()
    assert len(hist) == 1
    assert hist[0].situacao_de is None
    assert hist[0].situacao_para == "AUTORIZADO"
    # 🔴 decidido_por é NOT NULL e aqui não existe dono — usa quem cadastrou.
    assert hist[0].decidido_por == _CONTROLADOR.id

    # R5 só vale pra EMPRESA — PARTICULAR sem função de gestão continua
    # nascendo PENDENTE (não-regressão da D6).
    particular = ambiente["http"].post("/portaria/veiculos", json={
        "propriedade": "PARTICULAR", "funcionario_id": str(_DONO_B.id), "placa": "PAR0001",
    })
    assert particular.status_code == 201, particular.text
    assert particular.json()["situacao"] == "PENDENTE"


# ─── extra — GET /resolver-prefixo e GET /setores (superfície nova) ─────

def test_resolver_prefixo_acesso_encontrado_e_nao_encontrado(ambiente):
    _como(ambiente, "CONTROLADOR")
    _criar_onibus(ambiente["engine"], 1111)

    encontrado = ambiente["http"].get("/portaria/resolver-prefixo", params={"prefixo": "1111"})
    assert encontrado.status_code == 200, encontrado.text
    assert encontrado.json()["encontrado"] is True

    nao_encontrado = ambiente["http"].get("/portaria/resolver-prefixo", params={"prefixo": "9876"})
    assert nao_encontrado.status_code == 200, nao_encontrado.text
    assert nao_encontrado.json() == {"encontrado": False, "placa": None}


def test_listar_setores_filtra_ativos_por_padrao(ambiente):
    _como(ambiente, "CONTROLADOR")
    with Session(ambiente["engine"]) as db:
        db.add(PortariaSetor(codigo="DESATIVADO_041", nome="Desativado", ordem=9, ativo=False))
        db.commit()

    resp = ambiente["http"].get("/portaria/setores")
    assert resp.status_code == 200, resp.text
    codigos = [s["codigo"] for s in resp.json()]
    assert codigos == ["MANUTENCAO", "OPERACAO", "ADMINISTRACAO"]

    todos = ambiente["http"].get("/portaria/setores", params={"apenas_ativos": False})
    assert "DESATIVADO_041" in [s["codigo"] for s in todos.json()]
