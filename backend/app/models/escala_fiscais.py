"""Modelos da aba Escala de Fiscais (schema coordenadoria).

Espelha database/migrations/045-escala-fiscais.sql — 14 tabelas, todas com
prefixo escala_fiscal_. Regra de fronteira da migration: a única FK que sai
do schema é funcionario (identidade compartilhada da Suite), escrita sem
prefixo de schema — mesmo padrão de app/models/fiscalizacao.py — e só para
COORDENADOR e para QUEM FEZ A AÇÃO.

D-A (24/09): o FISCAL é o RE em TEXTO, sem FK — a escala aceita RE ainda
não cadastrado em Pessoas; o nome vem de funcionario por junção pelo RE na
leitura. D-B: `marcador` guarda o texto original da planilha quando a célula
não é RE (vazio, ****, xxx, G1, DIRETO, -).
⛔ escala_fiscal_posto_linha.linha é texto, sem FK para o catálogo de linhas
do Pátio; escala_fiscal_ponto_final é catálogo próprio, não fiscalizacao.ponto.

Os CHECKs (horário do fiscal término > início, 'escalado' ⇔ fiscal,
outra_garagem ≠ G3, coordenador início ≠ fim) ficam no banco; o router
valida antes de gravar para devolver mensagem legível em vez de
IntegrityError.
"""
from datetime import date, datetime, time
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.core.database import Base

SCHEMA = "coordenadoria"

# JSONB no Postgres; JSON genérico no SQLite dos testes.
_JSON = JSON().with_variant(JSONB(), "postgresql")


# ─── 1 · CADASTROS ────────────────────────────────────────────────────────────

class EscalaFiscalQuadro(Base):
    __tablename__ = "escala_fiscal_quadro"
    __table_args__ = {"schema": SCHEMA}

    # D-A: RE em texto, sem FK.
    re: Mapped[str] = mapped_column(String(20), primary_key=True)
    periodo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Dia de folga nos meses ÍMPARES; nos pares inverte.
    folga_base: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EscalaFiscalCoordenadorPeriodo(Base):
    __tablename__ = "escala_fiscal_coordenador_periodo"
    __table_args__ = {"schema": SCHEMA}

    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), primary_key=True
    )
    periodo: Mapped[int] = mapped_column(SmallInteger, primary_key=True)


class EscalaFiscalPontoFinal(Base):
    __tablename__ = "escala_fiscal_ponto_final"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EscalaFiscalPosto(Base):
    """Posto = grupo de linhas marcado numa ponta (TP ou TS)."""

    __tablename__ = "escala_fiscal_posto"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    lado: Mapped[str] = mapped_column(String(2), nullable=False)
    cod_jb: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    lote: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ponto_final_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_ponto_final.id"), nullable=True
    )
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    linhas: Mapped[list["EscalaFiscalPostoLinha"]] = relationship(
        "EscalaFiscalPostoLinha",
        cascade="all, delete-orphan",
        order_by="EscalaFiscalPostoLinha.ordem",
    )


class EscalaFiscalPostoLinha(Base):
    __tablename__ = "escala_fiscal_posto_linha"
    __table_args__ = {"schema": SCHEMA}

    posto_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_posto.id"), primary_key=True
    )
    # Texto, ⛔ sem FK para o catálogo de linhas do Pátio (fronteira).
    linha: Mapped[str] = mapped_column(String(10), primary_key=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)


# ─── 2 · MODELOS ──────────────────────────────────────────────────────────────

class EscalaFiscalModelo(Base):
    __tablename__ = "escala_fiscal_modelo"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo_dia: Mapped[str] = mapped_column(String(20), nullable=False)
    paridade: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)


class EscalaFiscalModeloPosto(Base):
    __tablename__ = "escala_fiscal_modelo_posto"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    modelo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_modelo.id"), nullable=False
    )
    posto_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_posto.id"), nullable=False
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    periodo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hora_inicio: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    hora_termino: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    situacao_padrao: Mapped[str] = mapped_column(String(15), nullable=False)
    # D-A: RE em texto, sem FK. 'escalado' ⇔ tem RE (CHECK no banco).
    re_padrao: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    outra_garagem: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    # D-B: texto original quando não é RE; NULL quando tem RE.
    marcador: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)


# ─── 3 · ESCALA DO DIA ────────────────────────────────────────────────────────
# Fase 3 (montagem) — os models existem agora só para o mapeamento ficar
# completo; nenhuma rota desta fase escreve nelas.

class EscalaFiscalDia(Base):
    """data = data de CALENDÁRIO (vira à meia-noite, ⚠️ não às 20h como o Pátio)."""

    __tablename__ = "escala_fiscal_dia"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    modelo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_modelo.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="rascunho")
    versao: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    publicada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    publicada_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )


class EscalaFiscalAlocacao(Base):
    __tablename__ = "escala_fiscal_alocacao"
    __table_args__ = (
        UniqueConstraint("escala_dia_id", "posto_id", "periodo"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    escala_dia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_dia.id"), nullable=False
    )
    posto_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_posto.id"), nullable=False
    )
    periodo: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hora_inicio: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    hora_termino: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    situacao: Mapped[str] = mapped_column(String(15), nullable=False)
    # D-A: RE em texto, sem FK — a futura "Minha escala" busca por ele.
    re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    outra_garagem: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    # D-B: texto original quando não é RE.
    marcador: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    alterado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    alterado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EscalaFiscalAusencia(Base):
    """Férias, atestado, afastado. data_fim INCLUSIVA; NULL = sem previsão."""

    __tablename__ = "escala_fiscal_ausencia"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # D-A: RE em texto, sem FK.
    re: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    data_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    data_fim: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EscalaFiscalTroca(Base):
    __tablename__ = "escala_fiscal_troca"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[str] = mapped_column(String(3), nullable=False)
    # D-A: RE dos dois fiscais em texto, sem FK.
    re_a: Mapped[str] = mapped_column(String(20), nullable=False)
    re_b: Mapped[str] = mapped_column(String(20), nullable=False)
    data_sabado: Mapped[date] = mapped_column(Date, nullable=False)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EscalaFiscalCoordenadorHorario(Base):
    """Horário padrão do coordenador de plantão. ⚠️ Pode passar da
    meia-noite: hora_fim < hora_inicio = termina no dia seguinte."""

    __tablename__ = "escala_fiscal_coordenador_horario"
    __table_args__ = (
        UniqueConstraint("funcionario_id", "turno"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    turno: Mapped[str] = mapped_column(String(5), nullable=False)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fim: Mapped[time] = mapped_column(Time, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EscalaFiscalPlantao(Base):
    __tablename__ = "escala_fiscal_plantao"
    __table_args__ = {"schema": SCHEMA}

    escala_dia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_dia.id"), primary_key=True
    )
    turno: Mapped[str] = mapped_column(String(5), primary_key=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fim: Mapped[time] = mapped_column(Time, nullable=False)


class EscalaFiscalAlteracao(Base):
    __tablename__ = "escala_fiscal_alteracao"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    escala_dia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.escala_fiscal_dia.id"), nullable=False
    )
    versao: Mapped[int] = mapped_column(Integer, nullable=False)
    alterado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    alterado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    antes: Mapped[Optional[Any]] = mapped_column(_JSON, nullable=True)
    depois: Mapped[Optional[Any]] = mapped_column(_JSON, nullable=True)
