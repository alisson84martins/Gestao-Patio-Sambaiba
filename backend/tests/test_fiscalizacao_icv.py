"""Módulo Fiscalização — Bloco E: ICV, bacia e painel do coordenador.

Testes escritos JUNTO de cada bloco (não represados para o fim — ver
_handoff-claude/PROMPT-fiscalizacao-bloco-E-icv.md, instrução extra do
Alisson). Mesmo padrão de test_fiscalizacao.py: SQLite em memória, sem
conftest.py, sqlite3.register_adapter para UUID.

⛔ Nenhum dado pessoal real — RE, prefixo e nome fictícios. Os números de
ICV usados nos testes são FICTÍCIOS/didáticos — não temos o arquivo real de
planilha à disposição (§10 do prompt) — escolhidos só para provar a
matemática (ponderação D22, prioridade D23, suspeita D25), não para
reproduzir uma garagem real.

⚠️ Assim como o resto do módulo (ver RegistroPartida em models/
fiscalizacao.py), os CHECK constraints deste bloco (lote IN ('E2','AR2'),
programadas >= 0, faixa_hora BETWEEN 0 AND 23) existem SÓ na migration SQL
— nenhum model do projeto declara CheckConstraint no SQLAlchemy. Por isso
os testes aqui não tentam provar CHECK via INSERT direto em SQLite (não
seria enforced nessa camada); a validação de entrada da API é coberta nos
schemas Pydantic (ver test_acao_coordenacao_* nos blocos seguintes, que
testam via endpoint HTTP, não via Session direta).
"""
import io
import sqlite3
import typing
import uuid as _uuid_mod
from datetime import date
from uuid import uuid4

import openpyxl
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import AcaoCoordenacao, Bacia, BaciaLinha, IcvApurado
from app.routers import fiscalizacao as fiscalizacao_router_mod

sqlite3.register_adapter(_uuid_mod.UUID, lambda u: u.hex)

_TABELAS = [
    Funcionario.__table__, Bacia.__table__, BaciaLinha.__table__,
    IcvApurado.__table__, AcaoCoordenacao.__table__,
]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS fiscalizacao")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)
    with Session(engine) as session:
        yield session


# ============================================================================
# BLOCO 1 — migration 030: tabelas, defaults e constraints declaradas no
# SQLAlchemy (UniqueConstraint — os CHECK ficam só na migration SQL)
# ============================================================================

def test_bacia_meta_icv_default_98(db):
    """D29 — meta_icv tem DEFAULT 98.00 no schema, não hardcoded em cada
    INSERT do código de aplicação."""
    b = Bacia(codigo="BACIA_TESTE", nome="Bacia Teste")
    db.add(b)
    db.commit()
    db.refresh(b)
    assert float(b.meta_icv) == 98.00


def test_bacia_linha_unique_por_linha_vigencia_inicio(db):
    """A UNIQUE real (linha_codigo, vigencia_inicio) — não deixa duas
    vigências abrirem no mesmo dia para a mesma linha (ver "DESVIO" no
    cabeçalho da migration 030: não é UNIQUE(bacia_codigo, linha_codigo))."""
    db.add(Bacia(codigo="B1", nome="Bacia 1"))
    db.commit()
    db.add(BaciaLinha(bacia_codigo="B1", linha_codigo="0000-00", vigencia_inicio=date(2026, 8, 1)))
    db.commit()

    db.add(BaciaLinha(bacia_codigo="B1", linha_codigo="0000-00", vigencia_inicio=date(2026, 8, 1)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_bacia_linha_mesma_linha_troca_de_bacia_com_vigencia(db):
    """D21 — a mesma linha pode aparecer em DUAS bacias diferentes, em
    vigências que não se sobrepõem (o caso real: 1726-10 mudou de bacia
    entre 26/05 e 19-21/08/2026). O schema tem que aceitar isso sem erro."""
    db.add(Bacia(codigo="B1", nome="Bacia 1"))
    db.add(Bacia(codigo="B2", nome="Bacia 2"))
    db.commit()
    db.add(BaciaLinha(
        bacia_codigo="B1", linha_codigo="0000-00",
        vigencia_inicio=date(2026, 5, 26), vigencia_fim=date(2026, 8, 18),
    ))
    db.add(BaciaLinha(
        bacia_codigo="B2", linha_codigo="0000-00",
        vigencia_inicio=date(2026, 8, 19), vigencia_fim=None,
    ))
    db.commit()  # não pode levantar

    linhas = db.query(BaciaLinha).filter_by(linha_codigo="0000-00").all()
    assert len(linhas) == 2
    assert {l.bacia_codigo for l in linhas} == {"B1", "B2"}


def test_icv_apurado_unique_linha_data_referencia(db):
    """A UNIQUE que o endpoint de upload usa como alvo de upsert (D25/§5
    do prompt) — reimportar o mesmo dia tem que ATUALIZAR, não duplicar;
    aqui provamos que a constraint em si impede a duplicata."""
    db.add(IcvApurado(
        id=uuid4(), linha_codigo="1111-11", data_referencia=date(2026, 8, 19),
        programadas=100, realizadas_tp_ts=50, realizadas_ts_tp=45,
    ))
    db.commit()

    db.add(IcvApurado(
        id=uuid4(), linha_codigo="1111-11", data_referencia=date(2026, 8, 19),
        programadas=100, realizadas_tp_ts=48, realizadas_ts_tp=47,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_icv_apurado_sem_teto_realizadas_acima_de_programadas(db):
    """D30 — ICV pode passar de 100%: o banco não impede realizadas somadas
    maiores que programadas. Quem decide teto é a apresentação (services/
    icv.py), nunca o schema."""
    registro = IcvApurado(
        id=uuid4(), linha_codigo="2222-22", data_referencia=date(2026, 8, 19),
        programadas=20, realizadas_tp_ts=22, realizadas_ts_tp=0,
    )
    db.add(registro)
    db.commit()  # não deve levantar
    db.refresh(registro)
    assert registro.realizadas_tp_ts + registro.realizadas_ts_tp == 22 > registro.programadas


def test_icv_apurado_origem_default_planilha(db):
    registro = IcvApurado(
        id=uuid4(), linha_codigo="3333-33", data_referencia=date(2026, 8, 19), programadas=10,
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)
    assert registro.origem == "PLANILHA"
    assert registro.suspeito is False


def test_acao_coordenacao_grava_com_funcionario_real(db):
    """D26 — ação da coordenação, ligada a um funcionário real (FK)."""
    func = Funcionario(id=uuid4(), re="70010", nome="Coordenador Teste", status="ATIVO")
    db.add(func)
    db.commit()

    acao = AcaoCoordenacao(
        id=uuid4(), linha_codigo="1726-10", data_referencia=date(2026, 8, 19),
        faixa_hora=18, descricao="Equipe reforçou o TP às 18h.",
        resultado_observado="ICV subiu de 94,62% para 99,46% em dois dias.",
        registrado_por=func.id,
    )
    db.add(acao)
    db.commit()
    db.refresh(acao)
    assert acao.registrado_por == func.id
    assert acao.faixa_hora == 18


# ============================================================================
# Fixture HTTP — mesmo padrão de test_fiscalizacao.py: dependências de
# permissão sobrescritas (vw_acesso_efetivo é view Postgres, não existe em
# SQLite), TestClient sobre app real.
# ============================================================================

def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


_FISCAL = Funcionario(id=uuid4(), re="70020", nome="Fiscal ICV Teste")
_COORDENADOR = Funcionario(id=uuid4(), re="70021", nome="Coordenador ICV Teste")


@pytest.fixture
def ambiente():
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS fiscalizacao")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=_TABELAS)
    with Session(engine) as setup:
        setup.add(Funcionario(id=_FISCAL.id, re=_FISCAL.re, nome=_FISCAL.nome, status="ATIVO"))
        setup.add(Funcionario(id=_COORDENADOR.id, re=_COORDENADOR.re, nome=_COORDENADOR.nome, status="ATIVO"))
        setup.commit()

    def _get_db_teste():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    dep_leitura_painel = _dependency_de(fiscalizacao_router_mod.LeituraPainel)
    dep_escrita_painel = _dependency_de(fiscalizacao_router_mod.EscritaPainel)

    app.dependency_overrides[get_db] = _get_db_teste

    yield {"engine": engine, "http": TestClient(app), "leitura_painel": dep_leitura_painel, "escrita_painel": dep_escrita_painel}

    app.dependency_overrides.pop(dep_leitura_painel, None)
    app.dependency_overrides.pop(dep_escrita_painel, None)
    app.dependency_overrides.pop(get_db, None)


def _negar():
    def _dep():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sem permissão (fake, teste)")
    return _dep


def _permitir(usuario):
    def _dep():
        return usuario
    return _dep


def _como_fiscal(ambiente):
    app.dependency_overrides[ambiente["leitura_painel"]] = _negar()
    app.dependency_overrides[ambiente["escrita_painel"]] = _negar()


def _como_coordenador(ambiente):
    app.dependency_overrides[ambiente["leitura_painel"]] = _permitir(_COORDENADOR)
    app.dependency_overrides[ambiente["escrita_painel"]] = _permitir(_COORDENADOR)


# ============================================================================
# Fixture da planilha sintética — reproduz o layout de §5 do prompt.
# ⚠️ Dados FICTÍCIOS — não temos o arquivo real (§10 do prompt); falta
# validar contra um .xlsx real (ver PROGRESSO-2026-08-23.md).
# ============================================================================

_CABECALHO_ICV = ["aqa", "BACIA", "LOTE", "VIAGENS PROG.", "VIAGENS REAL TP-TS", "VIAGENS REAL TS-TP", "Total (% ICV)"]


def _construir_planilha_icv(data_str: str, linhas: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ICV GARAGEM 3"])
    ws.append([f"DATA: {data_str}"])
    ws.append(_CABECALHO_ICV)
    for linha in linhas:
        ws.append([
            linha["linha_codigo"],
            linha.get("bacia", ""),
            linha.get("lote", "E2"),
            linha["programadas"],
            linha.get("tp_ts", 0),
            linha.get("ts_tp", 0),
            linha.get("percentual"),
        ])
    total_prog = sum(l["programadas"] for l in linhas)
    ws.append(["TOTAL", "", "", total_prog, "", "", ""])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _planilha_sem_viagens_prog(data_str: str) -> bytes:
    """Formato de maio (D22) — sem coluna de contagem, só percentual."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ICV GARAGEM 3"])
    ws.append([f"DATA: {data_str}"])
    ws.append(["LINHA", "BACIA", "LOTE", "3 (% ICV)", "Media (% ICV)", "Total (% ICV)"])
    ws.append(["0000-00", "BACIA X", "E2", 95.0, 96.0, 95.5])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ============================================================================
# BLOCO 2 — importador da planilha de ICV (§5) + endpoint de upload
# ============================================================================

def test_upload_icv_fiscal_nega_403(ambiente):
    _como_fiscal(ambiente)
    conteudo = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "0000-00", "programadas": 100, "tp_ts": 50, "ts_tp": 45, "percentual": 95.0},
    ])
    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403, resp.text


def test_upload_icv_coordenador_importa(ambiente):
    _como_coordenador(ambiente)
    conteudo = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "0000-00", "bacia": "BACIA TESTE", "programadas": 100, "tp_ts": 50, "ts_tp": 45, "percentual": 95.0},
    ])
    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["linhas_lidas"] == 1
    assert corpo["linhas_gravadas"] == 1
    assert corpo["data_referencia"] == "2026-08-19"

    with Session(ambiente["engine"]) as db:
        registro = db.execute(select(IcvApurado).where(IcvApurado.linha_codigo == "0000-00")).scalar_one()
        assert registro.programadas == 100
        assert registro.realizadas_tp_ts == 50
        assert registro.realizadas_ts_tp == 45
        # D21 — a coluna BACIA alimenta bacia_linha da vigência.
        vinculo = db.execute(select(BaciaLinha).where(BaciaLinha.linha_codigo == "0000-00")).scalar_one()
        assert vinculo.vigencia_inicio == date(2026, 8, 19)
        assert vinculo.vigencia_fim is None


def test_upload_icv_formato_de_maio_recusado(ambiente):
    """D22 — sem VIAGENS PROG., o arquivo inteiro é recusado (400), nunca
    importado parcialmente."""
    _como_coordenador(ambiente)
    conteudo = _planilha_sem_viagens_prog("26/05/2026")
    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv-maio.xlsx", conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 400, resp.text
    with Session(ambiente["engine"]) as db:
        assert db.execute(select(IcvApurado)).scalars().all() == []


def test_upload_icv_reimportar_mesmo_dia_atualiza_nao_duplica(ambiente):
    _como_coordenador(ambiente)
    primeira = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "0000-00", "programadas": 100, "tp_ts": 50, "ts_tp": 45, "percentual": 95.0},
    ])
    resp1 = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", primeira, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["linhas_gravadas"] == 1

    segunda = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "0000-00", "programadas": 100, "tp_ts": 60, "ts_tp": 38, "percentual": 98.0},
    ])
    resp2 = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv2.xlsx", segunda, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["linhas_atualizadas"] == 1
    assert resp2.json()["linhas_gravadas"] == 0

    with Session(ambiente["engine"]) as db:
        registros = db.execute(select(IcvApurado).where(IcvApurado.linha_codigo == "0000-00")).scalars().all()
    assert len(registros) == 1
    assert registros[0].realizadas_tp_ts == 60


def test_upload_icv_suspeita_dois_contadores_iguais_e_icv_nao_100(ambiente):
    """D25 — dois dias com os DOIS contadores idênticos e ICV < 100% marca
    o segundo como suspeito."""
    _como_coordenador(ambiente)
    dia19 = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "1773-10", "programadas": 120, "tp_ts": 56, "ts_tp": 53, "percentual": 90.83},
    ])
    r1 = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv19.xlsx", dia19, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["suspeitas"] == []

    dia20 = _construir_planilha_icv("20/08/2026", [
        {"linha_codigo": "1773-10", "programadas": 120, "tp_ts": 56, "ts_tp": 53, "percentual": 90.83},
    ])
    r2 = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv20.xlsx", dia20, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["suspeitas"]) == 1
    assert r2.json()["suspeitas"][0]["linha_codigo"] == "1773-10"

    with Session(ambiente["engine"]) as db:
        registro = db.execute(
            select(IcvApurado).where(IcvApurado.linha_codigo == "1773-10", IcvApurado.data_referencia == date(2026, 8, 20))
        ).scalar_one()
        assert registro.suspeito is True
        assert registro.suspeito_motivo is not None


def test_upload_icv_falso_positivo_linha_100_por_cento_nao_e_suspeita(ambiente):
    """D25 — o teste que impede a detecção de virar ruído: linha a 100%
    repetindo os mesmos contadores NÃO é suspeita."""
    _como_coordenador(ambiente)
    dia19 = _construir_planilha_icv("19/08/2026", [
        {"linha_codigo": "1721-10", "programadas": 60, "tp_ts": 30, "ts_tp": 30, "percentual": 100.0},
    ])
    ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv19.xlsx", dia19, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    dia20 = _construir_planilha_icv("20/08/2026", [
        {"linha_codigo": "1721-10", "programadas": 60, "tp_ts": 30, "ts_tp": 30, "percentual": 100.0},
    ])
    r2 = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv20.xlsx", dia20, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["suspeitas"] == []
    with Session(ambiente["engine"]) as db:
        registro = db.execute(
            select(IcvApurado).where(IcvApurado.linha_codigo == "1721-10", IcvApurado.data_referencia == date(2026, 8, 20))
        ).scalar_one()
        assert registro.suspeito is False


def test_upload_icv_ts_tp_vazio_linha_circular_vira_zero(ambiente):
    """VIAGENS REAL TS-TP vazio é normal (linha circular, §5) → grava 0,
    não erro."""
    _como_coordenador(ambiente)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ICV GARAGEM 3"])
    ws.append(["DATA: 19/08/2026"])
    ws.append(_CABECALHO_ICV)
    ws.append(["2024-10", "BACIA X", "E2", 50, 50, None, 100.0])  # circular — TS-TP vazio
    ws.append(["TOTAL", "", "", 50, "", "", ""])
    buffer = io.BytesIO()
    wb.save(buffer)

    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["erros"] == []
    with Session(ambiente["engine"]) as db:
        registro = db.execute(select(IcvApurado).where(IcvApurado.linha_codigo == "2024-10")).scalar_one()
        assert registro.realizadas_ts_tp == 0


def test_upload_icv_codigo_ilegivel_reportado(ambiente):
    """Código corrompido pelo Excel em notação científica — importa como
    texto e reporta para conferência humana, não descarta."""
    _como_coordenador(ambiente)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ICV GARAGEM 3"])
    ws.append(["DATA: 19/08/2026"])
    ws.append(_CABECALHO_ICV)
    ws.append([2.13e-08, "BACIA X", "E2", 40, 20, 19, 97.5])
    ws.append(["TOTAL", "", "", 40, "", "", ""])
    buffer = io.BytesIO()
    wb.save(buffer)

    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert len(corpo["codigos_ilegiveis"]) == 1
    with Session(ambiente["engine"]) as db:
        registros = db.execute(select(IcvApurado)).scalars().all()
    assert len(registros) == 1  # não descartou a linha


def test_upload_icv_divergencia_percentual_reportada(ambiente):
    """D28 — recalcula o percentual e compara com a coluna da planilha;
    divergência além de 0,01 é reportada, não escondida."""
    _como_coordenador(ambiente)
    conteudo = _construir_planilha_icv("19/08/2026", [
        # (50+45)/100 = 95.0%, mas a planilha diz 90.0% — divergência.
        {"linha_codigo": "0000-00", "programadas": 100, "tp_ts": 50, "ts_tp": 45, "percentual": 90.0},
    ])
    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    divergentes = resp.json()["divergentes_percentual"]
    assert len(divergentes) == 1
    assert divergentes[0]["linha_codigo"] == "0000-00"
    assert divergentes[0]["icv_calculado"] == 95.0


def test_upload_icv_50mb_retorna_413(ambiente):
    _como_coordenador(ambiente)
    conteudo_grande = b"x" * (50 * 1024 * 1024)
    resp = ambiente["http"].post(
        "/fiscalizacao/icv/upload",
        files={"file": ("icv.xlsx", conteudo_grande, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 413, resp.text
