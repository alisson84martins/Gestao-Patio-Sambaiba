"""Modelo do pré-cadastro de pessoas (Bloco H). Espelha
database/migrations/028-pessoa-pre-cadastro.sql.

Mora em `public` — identidade é a camada compartilhada da Suite, a única
coisa que todos os módulos legitimamente conhecem. Sem __table_args__ com
schema, mesmo padrão de app/models/cadastro.py:Funcionario.

⚠️ LGPD: acumula nome/CPF/RG/CNH/telefone automaticamente a partir de
eventos operacionais (portaria, pré-ocorrência) — ver
app/services/pre_cadastro.py para as regras de quando isso acontece e
COMMENT ON TABLE da migration 028 para a finalidade declarada.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class PessoaPreCadastro(Base):
    __tablename__ = "pessoa_pre_cadastro"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    re: Mapped[str] = mapped_column(String(20), nullable=False)
    nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cnh: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    papel_sugerido: Mapped[str] = mapped_column(String(12), nullable=False, default="INDEFINIDO")
    vezes_visto: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    primeira_vez_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ultima_vez_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Texto livre (ex.: 'PORTARIA_RECOLHIDA', 'PRE_OCORRENCIA') — ⛔ nunca
    # FK: um pré-cadastro sobrevive ao apagamento do evento que o originou.
    ultima_origem: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDENTE")

    funcionario_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    promovido_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    promovido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Auditoria do descarte — ver divergência registrada no cabeçalho da
    # migration 028 (a seção LGPD do prompt exige "descarte grava quem e
    # quando"; o esboço de tabela não tinha essas colunas).
    descartado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    descartado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    descarte_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # LGPD: prazo de retenção sugerido (12 meses) — coluna pronta, ⛔ sem
    # job de expurgo ainda (decisão explícita, ver migration 028).
    retencao_expira_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
