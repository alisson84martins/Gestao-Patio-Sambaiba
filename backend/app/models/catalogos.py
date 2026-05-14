"""Modelos de catálogos: linha, tipo_defeito, permissao."""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import SetorEnum

setor_pg = SQLEnum(SetorEnum, name="setor_enum", create_type=False, native_enum=True)


class Linha(Base):
    __tablename__ = "linha"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    setor: Mapped[SetorEnum] = mapped_column(setor_pg, nullable=False)
    ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TipoDefeito(Base):
    __tablename__ = "tipo_defeito"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    categoria: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Permissao(Base):
    __tablename__ = "permissao"
    __table_args__ = (UniqueConstraint("usuario_id", "recurso", name="uq_permissao_usuario_recurso"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    recurso: Mapped[str] = mapped_column(String(50), nullable=False)
    pode_ler: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pode_escrever: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    concedido_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
