"""Modelos de pessoas: motorista e usuario."""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import PerfilUsuarioEnum, StatusMotoristaEnum
from app.models.mixins import AuditoriaMixin

perfil_pg = SQLEnum(PerfilUsuarioEnum, name="perfil_usuario_enum", create_type=False, native_enum=True)
status_mot_pg = SQLEnum(StatusMotoristaEnum, name="status_motorista_enum", create_type=False, native_enum=True)


class Motorista(Base, AuditoriaMixin):
    __tablename__ = "motorista"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    re: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    status: Mapped[StatusMotoristaEnum] = mapped_column(
        status_mot_pg, nullable=False, default=StatusMotoristaEnum.ATIVO
    )
    codigo_externo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class Usuario(Base, AuditoriaMixin):
    __tablename__ = "usuario"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    re: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    perfil: Mapped[PerfilUsuarioEnum] = mapped_column(perfil_pg, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    motorista_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("motorista.id", ondelete="SET NULL"),
        nullable=True,
    )
    ultimo_acesso: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # TRUE enquanto o usuário não trocou a senha inicial (= o RE).
    # O admin cria a conta e o usuário muda em /auth/trocar-senha.
    primeiro_acesso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
