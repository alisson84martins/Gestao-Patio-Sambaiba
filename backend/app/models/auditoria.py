"""Trilha de auditoria mínima — migration 021.

Ver database/migrations/021-log-acesso.sql para o porquê e o escopo
(deliberadamente mínimo — não cobre todo 403 do sistema, só o que passa
por exige()).
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class LogAcesso(Base):
    __tablename__ = "log_acesso"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    evento: Mapped[str] = mapped_column(String(30), nullable=False)
    funcionario_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    re_tentativa: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    recurso: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ocorrencia_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
