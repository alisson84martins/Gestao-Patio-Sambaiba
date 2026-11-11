"""Monitoramento ao vivo da linha (SIM SPTrans → sistema) — espelha
database/migrations/053-monitoramento-tempo-real.sql e 054-monitoramento-nimer.sql.

LeituraVeiculo: uma posição de GPS de um carro (UNIQUE prefixo + posicao_em).
Coleta: o "batimento" do robô — uma linha por envio, inclusive sem veículos.
Fase 2 (054): linhas monitoradas, códigos do SIM, viagens do NIMER,
correções do fiscal, tratativa de alerta e histórico de velocidade.
"""
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
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


# ═══ Fase 2 (migration 054) — NIMER + todas as linhas ═══════════════════════

class LinhaMonitoradaCadastro(Base):
    """Quais linhas o robô acompanha. Incluir linha = inserir aqui (+ traçado)."""
    __tablename__ = "linha_monitorada"
    __table_args__ = {"schema": "monitoramento"}

    linha_codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nimer_line_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimCodigoLinha(Base):
    """`li` do SIM -> linha e sentido (aprendido pelo recebimento)."""
    __tablename__ = "sim_codigo_linha"
    __table_args__ = {"schema": "monitoramento"}

    sim_codigo: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    sentido: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    visto_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NimerViagem(Base):
    """Uma viagem do Relatório de Linha do NIMER (só leitura do NIMER)."""
    __tablename__ = "nimer_viagem"
    __table_args__ = (
        UniqueConstraint("data_servico", "nimer_departure_id", name="uq_nimer_viagem_data_departure"),
        Index("idx_nimer_viagem_linha_data", "linha_codigo", "data_servico"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    data_servico: Mapped[date] = mapped_column(Date, nullable=False)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    tabela: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    nimer_table_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    nimer_departure_id: Mapped[str] = mapped_column(String(40), nullable=False)
    idx: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    sentido: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    prog_inicio: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    prog_fim: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    real_inicio: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    real_fim: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fonte_inicio: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    fonte_fim: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    atraso_inicio_min: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    motivo_perda_texto: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    carro: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cobrador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sem_motorista: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    removida_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NimerColeta(Base):
    """Batimento do robô do NIMER, por linha."""
    __tablename__ = "nimer_coleta"
    __table_args__ = (
        Index("idx_nimer_coleta_linha_recebido", "linha_codigo", "recebido_em"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    recebido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    coletado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    data_servico: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    planejadas: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    realizadas: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    perdidas: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    faltantes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pontualidade_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    qtd_viagens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mensagem: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class ViagemCorrecao(Base):
    """Correção do fiscal. ⛔ Nunca apagar — vale a mais recente de cada campo."""
    __tablename__ = "viagem_correcao"
    __table_args__ = (
        Index("idx_viagem_correcao_viagem", "nimer_viagem_id", "criado_em"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    nimer_viagem_id: Mapped[int] = mapped_column(
        _BIGID, ForeignKey("monitoramento.nimer_viagem.id"), nullable=False
    )
    campo: Mapped[str] = mapped_column(String(20), nullable=False)
    valor_nimer: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    valor_corrigido: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    justificativa: Mapped[str] = mapped_column(Text, nullable=False)
    autor_funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AlertaTratativa(Base):
    """'Resolvido' de um alerta calculado na Central (D24: o alerta não é gravado)."""
    __tablename__ = "alerta_tratativa"
    __table_args__ = (
        UniqueConstraint("data_servico", "chave", name="uq_alerta_tratativa"),
        {"schema": "monitoramento"},
    )

    id: Mapped[int] = mapped_column(_BIGID, primary_key=True, autoincrement=True)
    data_servico: Mapped[date] = mapped_column(Date, nullable=False)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    chave: Mapped[str] = mapped_column(String(160), nullable=False)
    acao_coordenacao_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    autor_funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class VelocidadeTrecho(Base):
    """Velocidade típica por trecho de 500 m, faixa de 30 min e tipo de dia."""
    __tablename__ = "velocidade_trecho"
    __table_args__ = {"schema": "monitoramento"}

    linha_codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    sentido: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    trecho: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    faixa: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    tipo_dia: Mapped[str] = mapped_column(String(8), primary_key=True)
    mediana_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VelocidadeAgregacao(Base):
    """Dia já somado em velocidade_trecho."""
    __tablename__ = "velocidade_agregacao"
    __table_args__ = {"schema": "monitoramento"}

    data_servico: Mapped[date] = mapped_column(Date, primary_key=True)
    linha_codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    leituras: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agregado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
