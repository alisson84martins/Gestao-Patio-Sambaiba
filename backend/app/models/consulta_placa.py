"""Consulta de placa paga (consultadeplaca.net) — espelha
database/migrations/051-consulta-placa.sql.

Uma linha por consulta PAGA (ENCONTRADA / NAO_ENCONTRADA): cache de 30 dias,
limite diário e histórico de gasto. Erro sem cobrança não grava.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base

# JSONB no Postgres; JSON genérico no SQLite dos testes (mesmo padrão de
# app/models/escala_fiscais.py).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class ConsultaPlaca(Base):
    __tablename__ = "consulta_placa"
    __table_args__ = (
        CheckConstraint(
            "resultado IN ('ENCONTRADA', 'NAO_ENCONTRADA')",
            name="chk_consulta_placa_resultado",
        ),
        Index("idx_consulta_placa_placa_data", "placa", "consultado_em"),
        {"schema": "coordenadoria"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    placa: Mapped[str] = mapped_column(String(10), nullable=False)
    resultado: Mapped[str] = mapped_column(String(12), nullable=False)
    dados: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSON, nullable=True)
    preco: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    consultado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consultado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
