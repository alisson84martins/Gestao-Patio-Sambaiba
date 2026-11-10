"""Monitoramento ao vivo da linha (SIM SPTrans → sistema) — espelha
database/migrations/053-monitoramento-tempo-real.sql.

LeituraVeiculo: uma posição de GPS de um carro (UNIQUE prefixo + posicao_em).
Coleta: o "batimento" do robô — uma linha por envio, inclusive sem veículos.
Sem dado pessoal.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# BIGSERIAL no Postgres; INTEGER no SQLite dos testes (só INTEGER PRIMARY KEY
# vira autoincremento lá).
_BIGID = BigInteger().with_variant(Integer(), "sqlite")


class LeituraVeiculo(Base):
    __tablename__ = "leitura_veiculo"
    __table_args__ = (
        UniqueConstraint("prefixo", "posicao_em", name="uq_leitura_veiculo_prefixo_posicao"),
        Index("idx_leitura_veiculo_linha_posicao", "linha_codigo", "posicao_em"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    recebido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posicao_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prefixo: Mapped[str] = mapped_column(String(10), nullable=False)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    sim_codigo: Mapped[int] = mapped_column(Integer, nullable=False)
    sentido: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    velocidade: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    local_sim: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


class Coleta(Base):
    __tablename__ = "coleta"
    __table_args__ = (
        Index("idx_coleta_recebido", "recebido_em"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    recebido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    origem: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    qtd_veiculos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mensagem: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
