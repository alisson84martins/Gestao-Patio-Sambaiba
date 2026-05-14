"""Mixins reutilizáveis para os modelos SQLAlchemy."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func


class AuditoriaMixin:
    """Adiciona colunas de auditoria — quem/quando criou e alterou."""

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    atualizado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )


class SoftDeleteMixin:
    """Adiciona coluna deletado_em para soft delete."""

    deletado_em: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SyncMixin:
    """Adiciona controle de sincronização para offline-first."""

    sincronizado_em: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    versao: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
