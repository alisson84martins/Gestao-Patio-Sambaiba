"""Modelos operacionais: escala, alerta, ficha_manutencao, importacao_escala."""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import (
    OrigemEscalaEnum,
    StatusFichaEnum,
    StatusImportacaoEnum,
    TipoAlertaEnum,
    TipoEscalaEnum,
)
from app.models.mixins import AuditoriaMixin, SoftDeleteMixin, SyncMixin

tipo_alerta_pg = SQLEnum(TipoAlertaEnum, name="tipo_alerta_enum", create_type=False, native_enum=True)
status_ficha_pg = SQLEnum(StatusFichaEnum, name="status_ficha_enum", create_type=False, native_enum=True)
tipo_escala_pg = SQLEnum(TipoEscalaEnum, name="tipo_escala_enum", create_type=False, native_enum=True)
origem_escala_pg = SQLEnum(OrigemEscalaEnum, name="origem_escala_enum", create_type=False, native_enum=True)
status_imp_pg = SQLEnum(StatusImportacaoEnum, name="status_importacao_enum", create_type=False, native_enum=True)


class Escala(Base, AuditoriaMixin, SoftDeleteMixin, SyncMixin):
    __tablename__ = "escala"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date] = mapped_column(Date, nullable=False)
    onibus_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("onibus.id", ondelete="CASCADE"), nullable=False
    )
    motorista_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("motorista.id", ondelete="SET NULL"), nullable=True
    )
    linha_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("linha.id", ondelete="RESTRICT"), nullable=False
    )
    horario_saida: Mapped[time] = mapped_column(Time, nullable=False)
    tipo: Mapped[TipoEscalaEnum] = mapped_column(tipo_escala_pg, nullable=False)
    origem: Mapped[OrigemEscalaEnum] = mapped_column(
        origem_escala_pg, nullable=False, default=OrigemEscalaEnum.MANUAL
    )
    importacao_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("importacao_escala.id", ondelete="SET NULL"),
        nullable=True,
    )


class Alerta(Base, AuditoriaMixin, SoftDeleteMixin, SyncMixin):
    __tablename__ = "alerta"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    onibus_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("onibus.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[TipoAlertaEnum] = mapped_column(tipo_alerta_pg, nullable=False)
    motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registrado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    resolvido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolvido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolvido_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )


class FichaManutencao(Base, AuditoriaMixin, SoftDeleteMixin, SyncMixin):
    __tablename__ = "ficha_manutencao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    onibus_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("onibus.id", ondelete="CASCADE"), nullable=False
    )
    motorista_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("motorista.id", ondelete="SET NULL"), nullable=True
    )
    mecanico_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    tipo_defeito_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tipo_defeito.id", ondelete="RESTRICT"), nullable=False
    )
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[StatusFichaEnum] = mapped_column(
        status_ficha_pg, nullable=False, default=StatusFichaEnum.ABERTA
    )
    aberta_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    concluida_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportacaoEscala(Base):
    __tablename__ = "importacao_escala"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    arquivo_nome: Mapped[str] = mapped_column(String(255), nullable=False)
    arquivo_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    data_escala: Mapped[date] = mapped_column(Date, nullable=False)
    total_registros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registros_sucesso: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registros_erro: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[StatusImportacaoEnum] = mapped_column(status_imp_pg, nullable=False)
    erro_detalhe: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    importado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="SET NULL"), nullable=True
    )
    importado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
