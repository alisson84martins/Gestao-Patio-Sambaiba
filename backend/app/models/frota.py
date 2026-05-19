"""Modelos de frota e pátio: onibus, fila, alocacao_patio."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Computed, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import SetorEnum, StatusOnibusEnum, TipoFilaEnum
from app.models.mixins import AuditoriaMixin, SyncMixin

setor_pg = SQLEnum(SetorEnum, name="setor_enum", create_type=False, native_enum=True)
status_onibus_pg = SQLEnum(StatusOnibusEnum, name="status_onibus_enum", create_type=False, native_enum=True)
tipo_fila_pg = SQLEnum(TipoFilaEnum, name="tipo_fila_enum", create_type=False, native_enum=True)

# Expressão da coluna gerada (idêntica à do DDL)
SETOR_EXPR = (
    "CASE "
    "WHEN numero_frota BETWEEN 1000 AND 1999 THEN 'E2'::setor_enum "
    "WHEN numero_frota BETWEEN 2000 AND 2999 THEN 'AR2'::setor_enum "
    "END"
)


class Onibus(Base, AuditoriaMixin):
    __tablename__ = "onibus"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    numero_frota: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    placa: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Coluna GERADA pelo banco — SQLAlchemy não inclui em INSERT/UPDATE
    setor: Mapped[Optional[SetorEnum]] = mapped_column(
        setor_pg,
        Computed(SETOR_EXPR, persisted=True),
        nullable=True,
    )
    status: Mapped[StatusOnibusEnum] = mapped_column(
        status_onibus_pg, nullable=False, default=StatusOnibusEnum.ATIVO
    )
    codigo_externo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class Fila(Base):
    __tablename__ = "fila"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[TipoFilaEnum] = mapped_column(tipo_fila_pg, nullable=False)
    numero: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nome: Mapped[str] = mapped_column(String(50), nullable=False)
    ordem_exibicao: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlocacaoPatio(Base, AuditoriaMixin, SyncMixin):
    __tablename__ = "alocacao_patio"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    onibus_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("onibus.id", ondelete="CASCADE"), nullable=False
    )
    fila_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("fila.id", ondelete="RESTRICT"), nullable=False
    )
    posicao: Mapped[int] = mapped_column(Integer, nullable=False)
    alocado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    alocado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Data de serviço real (calculada pelo backend em get_data_servico).
    # Após 20h = amanhã, antes das 20h = hoje.
    data_referencia: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
