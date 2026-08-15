"""Modelos do schema coordenadoria: Pré-ocorrência do motorista.

Espelha database/migrations/022-pre-ocorrencia.sql. Ver
_handoff-claude/DESENHO-pre-ocorrencia.md para o porquê das decisões de
modelagem (token só como hash, sem coluna de autor no anexo, etc.).
"""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

SCHEMA = "coordenadoria"


class PreOcorrenciaAutorizacao(Base):
    __tablename__ = "pre_ocorrencia_autorizacao"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    aberta_por: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False)
    coordenador_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False)
    telefone_destino: Mapped[str] = mapped_column(String(20), nullable=False)

    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="AGUARDANDO")
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PreOcorrencia(Base):
    __tablename__ = "pre_ocorrencia"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    autorizacao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.pre_ocorrencia_autorizacao.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )

    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    motorista_telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    motorista_rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_cnh: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    cobrador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cobrador_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cobrador_telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    data_ocorrencia: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    hora_ocorrencia: Mapped[time] = mapped_column(Time, nullable=False)
    local_logradouro: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    local_numero: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    local_bairro: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    local_cidade: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    local_referencia: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    relato: Mapped[str] = mapped_column(Text, nullable=False)

    terceiro_placa: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    terceiro_modelo_cor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    terceiro_seguradora: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    status: Mapped[str] = mapped_column(String(15), nullable=False, default="AGUARDANDO")

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    enviada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    convertida_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ocorrencia_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id"), nullable=True
    )


class PreOcorrenciaAnexo(Base):
    __tablename__ = "pre_ocorrencia_anexo"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    pre_ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.pre_ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(15), nullable=False)
    caminho: Mapped[str] = mapped_column(String(255), nullable=False)
    nome_original: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tamanho_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
