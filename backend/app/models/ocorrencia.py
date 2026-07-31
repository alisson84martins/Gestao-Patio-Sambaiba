"""Modelos do schema coordenadoria: Relatório de Ocorrências.

Espelha fielmente database/migrations/012-modulo-coordenadoria-ocorrencias.sql.
Prefixo, linha e RE de motorista/cobrador são TEXTO (nunca FK) — regra de
fronteira do módulo: Coordenadoria não depende do Pátio. A única FK para fora
do schema coordenadoria é public.funcionario (identidade compartilhada).
"""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Identity, SmallInteger,
    String, Text, Time, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

SCHEMA = "coordenadoria"


# ============================================================================
# 1. CATÁLOGOS
# ============================================================================

class TipoOcorrencia(Base):
    __tablename__ = "tipo_ocorrencia"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    exige_vitima: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exige_terceiro: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exige_analise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=99)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OrgaoAutoridade(Base):
    __tablename__ = "orgao_autoridade"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=99)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ============================================================================
# 2. OCORRENCIA — capa do relatório (página 1)
# ============================================================================

class Ocorrencia(Base):
    __tablename__ = "ocorrencia"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    numero: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    tipo_ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tipo_ocorrencia.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="RASCUNHO")

    # ── Quando ────────────────────────────────────────────────────────────
    data_ocorrencia: Mapped[date] = mapped_column(Date, nullable=False)
    hora_ocorrencia: Mapped[time] = mapped_column(Time, nullable=False)

    # ── Nosso veículo (texto, sem FK — ver regra de fronteira) ────────────
    prefixo: Mapped[str] = mapped_column(String(10), nullable=False)
    placa: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # ── Condutor ──────────────────────────────────────────────────────────
    condutor_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    condutor_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    condutor_funcao: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    condutor_cnh: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    condutor_rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    condutor_cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    direcao_defensiva: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── Cobrador ──────────────────────────────────────────────────────────
    cobrador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cobrador_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # ── Velocidades e atendimento ─────────────────────────────────────────
    velocidade_via: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    velocidade_onibus: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    foi_ao_local: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    confirmado: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── Marcadores da via ─────────────────────────────────────────────────
    via_urbana: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    via_rodoviaria: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    area_interna: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    corredor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tem_fotos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    monitoramento: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sentido: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # ── Local ─────────────────────────────────────────────────────────────
    local_ocorrido: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    numero_local: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bairro: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cidade: Mapped[str] = mapped_column(String(80), nullable=False, default="São Paulo")

    # ── Contagem ──────────────────────────────────────────────────────────
    quant_acidentes: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    isentos: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    culpados: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    # ── Manutenção ────────────────────────────────────────────────────────
    problemas_mecanicos: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    problemas_mecanicos_qual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    condutor_avisou_manutencao: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    manutencao_avisado_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # ── Narrativas (página 1) ─────────────────────────────────────────────
    descricao_coordenador: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    descricao_motorista: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    descricao_terceiro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Ocorrência policial (página 4) ────────────────────────────────────
    ocorrencia_policial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    viatura_numero: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    bpm: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    cia: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    distrito: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    numero_to: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    numero_bo: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    protocolo: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    houve_policia_tecnica: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nome_perito: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Campos que a mensagem do sinistro exige e o papel não tem ─────────
    controlador_acesso: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # ── Auditoria ─────────────────────────────────────────────────────────
    registrado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id", ondelete="SET NULL"), nullable=True
    )
    finalizada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    excluida_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tipo_ocorrencia: Mapped["TipoOcorrencia"] = relationship("TipoOcorrencia")
    analise: Mapped[Optional["OcorrenciaAnalise"]] = relationship(
        "OcorrenciaAnalise", back_populates="ocorrencia", uselist=False, cascade="all, delete-orphan"
    )
    veiculos_terceiro: Mapped[list["OcorrenciaVeiculoTerceiro"]] = relationship(
        "OcorrenciaVeiculoTerceiro", back_populates="ocorrencia",
        cascade="all, delete-orphan", order_by="OcorrenciaVeiculoTerceiro.ordem",
    )
    avarias: Mapped[list["OcorrenciaAvaria"]] = relationship(
        "OcorrenciaAvaria", back_populates="ocorrencia", cascade="all, delete-orphan"
    )
    vitimas: Mapped[list["OcorrenciaVitima"]] = relationship(
        "OcorrenciaVitima", back_populates="ocorrencia",
        cascade="all, delete-orphan", order_by="OcorrenciaVitima.ordem",
    )
    testemunhas: Mapped[list["OcorrenciaTestemunha"]] = relationship(
        "OcorrenciaTestemunha", back_populates="ocorrencia",
        cascade="all, delete-orphan", order_by="OcorrenciaTestemunha.ordem",
    )
    autoridades: Mapped[list["OcorrenciaAutoridade"]] = relationship(
        "OcorrenciaAutoridade", back_populates="ocorrencia",
        cascade="all, delete-orphan", order_by="OcorrenciaAutoridade.ordem",
    )
    anexos: Mapped[list["OcorrenciaAnexo"]] = relationship(
        "OcorrenciaAnexo", back_populates="ocorrencia",
        cascade="all, delete-orphan", order_by="OcorrenciaAnexo.ordem",
    )


# ============================================================================
# 3. ANALISE DO ACIDENTE (página 3) — 1:1
# ============================================================================

class OcorrenciaAnalise(Base):
    __tablename__ = "ocorrencia_analise"
    __table_args__ = {"schema": SCHEMA}

    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), primary_key=True
    )

    colisao: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    acidente: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    condicoes: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    deslocamento: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    reta: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    curva: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    via: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    numero_faixas: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    mao_direcao: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    preferencial: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    condicao_pista: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    pavimentacao: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    conservacao: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    sinal_horizontal: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sinal_horizontal_outros: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    sinal_vertical: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sinal_vertical_outros: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    dispositivos_aux: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    iluminacao: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    tempo: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    visibilidade: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="analise")


# ============================================================================
# 4. VEICULOS DE TERCEIRO (página 2)
# ============================================================================

class OcorrenciaVeiculoTerceiro(Base):
    __tablename__ = "ocorrencia_veiculo_terceiro"
    __table_args__ = (
        UniqueConstraint("ocorrencia_id", "ordem", name="uq_veiculo_terceiro_ordem"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    danos: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    marca: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    ano: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)
    cor: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    placa: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    cidade_placa: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    estado_placa: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    renavam: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Dados pessoais do condutor/proprietário terceiro — DADO PESSOAL DE TERCEIRO
    proprietario: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    fones: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    endereco: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cidade: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    cnh: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    seguradora: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    seguradora_fone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    sinistro_numero: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    partes_avariadas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="veiculos_terceiro")


# ============================================================================
# 5. AVARIAS DO NOSSO VEICULO — por região
# ============================================================================

class OcorrenciaAvaria(Base):
    __tablename__ = "ocorrencia_avaria"
    __table_args__ = (
        UniqueConstraint("ocorrencia_id", "regiao", name="uq_avaria_regiao"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    regiao: Mapped[str] = mapped_column(String(25), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="avarias")


# ============================================================================
# 6. VITIMAS (página 4)
# ============================================================================

class OcorrenciaVitima(Base):
    __tablename__ = "ocorrencia_vitima"
    __table_args__ = (
        UniqueConstraint("ocorrencia_id", "ordem", name="uq_vitima_ordem"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    idade: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    fone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    endereco: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    numero: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bairro: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cidade: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    dados_pessoais: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    era_passageiro: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Exigidos pela mensagem do sinistro, não existem no papel
    destino_socorro: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contato_parentesco: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    contato_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contato_fone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="vitimas")


# ============================================================================
# 7. TESTEMUNHAS (página 4)
# ============================================================================

class OcorrenciaTestemunha(Base):
    __tablename__ = "ocorrencia_testemunha"
    __table_args__ = (
        UniqueConstraint("ocorrencia_id", "ordem", name="uq_testemunha_ordem"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    rg: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    endereco: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    numero: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bairro: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cidade: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    fone1: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    fone2: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="testemunhas")


# ============================================================================
# 8. AUTORIDADES NO LOCAL
# ============================================================================

class OcorrenciaAutoridade(Base):
    __tablename__ = "ocorrencia_autoridade"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    orgao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.orgao_autoridade.id"), nullable=False
    )

    identificacao: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    responsavel: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="autoridades")
    orgao: Mapped["OrgaoAutoridade"] = relationship("OrgaoAutoridade")


# ============================================================================
# 9. ANEXOS — fotos, croqui, BO em PDF
# ============================================================================

class OcorrenciaAnexo(Base):
    __tablename__ = "ocorrencia_anexo"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ocorrencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ocorrencia.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    caminho: Mapped[str] = mapped_column(String(255), nullable=False)
    nome_original: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    tamanho_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    largura: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    altura: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    enviado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ocorrencia: Mapped["Ocorrencia"] = relationship("Ocorrencia", back_populates="anexos")
