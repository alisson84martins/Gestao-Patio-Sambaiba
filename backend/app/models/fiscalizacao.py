"""Modelos do schema fiscalizacao: turnos do fiscal, partidas e fechamento.

Espelha fielmente database/migrations/029-modulo-fiscalizacao.sql. Regra de
fronteira (mesma da 012/024/026): Fiscalização é módulo separado do Pátio,
sem FK para tabela operacional (onibus, linha, fila, alocacao_patio, alerta,
escala, motorista). A única FK para fora do schema fiscalizacao é
funcionario (public, identidade compartilhada por toda a Suite) — escrita
sem prefixo de schema, mesmo padrão de app/models/portaria.py (Funcionario
não declara __table_args__ com schema, então "funcionario.id" é a chave
correta no MetaData; "public.funcionario.id" não bate com a tabela já
registrada).
"""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger,
    String, Text, Time, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

SCHEMA = "fiscalizacao"


class Ponto(Base):
    """Ponto final de fiscalização. O turno é do PONTO, não da linha (D9)."""

    __tablename__ = "ponto"
    __table_args__ = {"schema": SCHEMA}

    codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    terminal: Mapped[str] = mapped_column(String(2), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PontoLinha(Base):
    """Quais linhas saem de cada ponto — ponto com mais de uma linha é
    comum na G3 (D9). Alimenta as abas do app do fiscal (D11)."""

    __tablename__ = "ponto_linha"
    __table_args__ = (
        UniqueConstraint("ponto_codigo", "linha_codigo"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ponto_codigo: Mapped[str] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.ponto.codigo"), nullable=False
    )
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Turno(Base):
    """Pessoa + ponto + período + data, cobrindo N linhas (D9). Fundação
    diferente do turno_fiscal de julho, que amarrava a uma linha só."""

    __tablename__ = "turno"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    fiscal_re: Mapped[str] = mapped_column(String(20), nullable=False)

    ponto_codigo: Mapped[str] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.ponto.codigo"), nullable=False
    )
    terminal: Mapped[str] = mapped_column(String(2), nullable=False)
    periodo: Mapped[str] = mapped_column(String(1), nullable=False)

    data_referencia: Mapped[date] = mapped_column(Date, nullable=False)
    tipo_dia: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ABERTO")

    refeicao_inicio: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    refeicao_fim: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    pastas_prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    aberto_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fechado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TurnoLinha(Base):
    """Linhas que este turno efetivamente cobre hoje — uma linha do ponto
    pode não ser fiscalizada num dia, por isso é tabela própria."""

    __tablename__ = "turno_linha"
    __table_args__ = (
        UniqueConstraint("turno_id", "linha_codigo"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    turno_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.turno.id", ondelete="CASCADE"), nullable=False
    )
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)


class PartidaProgramada(Base):
    """A grade importada da escala gerencial (§7). ⚠️ UNIQUE inclui
    `terminal` além do texto literal do prompt — o parser numera sequencia
    de TP e de TS separadamente a partir de 1 (services/
    importacao_escala_fiscal.py), então sem terminal na chave a partida TP
    nº1 e a TS nº1 da mesma tabela colidiriam."""

    __tablename__ = "partida_programada"
    __table_args__ = (
        UniqueConstraint("linha_codigo", "tipo_dia", "numero_tabela", "sequencia", "terminal", "vigencia"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo_dia: Mapped[str] = mapped_column(String(8), nullable=False)
    numero_tabela: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sequencia: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    terminal: Mapped[str] = mapped_column(String(2), nullable=False)
    horario: Mapped[time] = mapped_column(Time, nullable=False)
    # NULL de propósito quando o parser não determina o período (§7.3) —
    # nunca chuta. Ver services/importacao_escala_fiscal.py.
    periodo: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)
    vigencia: Mapped[date] = mapped_column(Date, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegistroPartida(Base):
    """O que o fiscal respondeu — uma resposta por horário programado por
    turno, e ele pode mudar a resposta (upsert pela UNIQUE). Snapshot de
    linha/tabela/terminal/horário: o registro sobrevive à reimportação da
    grade. ⚠️ UNIQUE inclui `terminal` — ver PartidaProgramada."""

    __tablename__ = "registro_partida"
    __table_args__ = (
        UniqueConstraint("turno_id", "linha_codigo", "numero_tabela", "terminal", "horario_programado"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    turno_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.turno.id", ondelete="CASCADE"), nullable=False
    )
    partida_programada_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.partida_programada.id", ondelete="SET NULL"),
        nullable=True,
    )

    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    numero_tabela: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    terminal: Mapped[str] = mapped_column(String(2), nullable=False)
    horario_programado: Mapped[time] = mapped_column(Time, nullable=False)

    resultado: Mapped[str] = mapped_column(String(10), nullable=False)
    horario_real: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    # D5 — lista congelada nos seis do fechamento real (sem TRANSITO).
    motivo: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    motivo_outro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # D18 — só pedido ao fiscal quando motivo é RA ou SOS.
    prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    operador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    registrado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EventoTurno(Base):
    """🔑 D4 — a decisão mais importante da modelagem: contadores são
    EVENTOS, não perdas. Uma R.A pode acontecer sem custar viagem; uma
    viagem pode se perder sem R.A."""

    __tablename__ = "evento_turno"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    turno_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.turno.id", ondelete="CASCADE"), nullable=False
    )
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo: Mapped[str] = mapped_column(String(24), nullable=False)
    horario: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    numero_tabela: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Preenchido quando o evento nasceu de marcar uma partida PERDIDA.
    # NULL para evento avulso (POST .../eventos sem perda associada).
    registro_partida_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.registro_partida.id", ondelete="SET NULL"),
        nullable=True,
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ObservacaoTurno(Base):
    """D5 — o escape hatch: motivo que ainda não virou contador vive aqui
    em texto livre. Primeira linha do OBS impressa é sempre a refeição do
    fiscal (D15), gerada em services/fechamento_fiscal.py, não gravada aqui."""

    __tablename__ = "observacao_turno"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    turno_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.turno.id", ondelete="CASCADE"), nullable=False
    )
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    numero_tabela: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    horario: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Baita(Base):
    """D13 — dentro do primeiro corte. Duas duplas por turno inteiro
    (BAITA e ANTI_BAITA), não uma por tabela."""

    __tablename__ = "baita"
    __table_args__ = (
        UniqueConstraint("turno_id", "linha_codigo", "tipo"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    turno_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.turno.id", ondelete="CASCADE"), nullable=False
    )
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    prefixo: Mapped[str] = mapped_column(String(10), nullable=False)
    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cobrador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    saida_tp: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    saida_ts: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    # "SAÍDA DO TS: circular" — a linha não tem terminal secundário. ⛔ NÃO é
    # o rec_ts do sistema de julho ("recolheu direto do TS sem voltar ao TP").
    ts_circular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================================
# Bloco E (migration 030) — ICV, bacia e ação da coordenação
# ============================================================================

class LinhaCoordenador(Base):
    """Não existe entidade "bacia" — o que a planilha da gerência chama de
    bacia é o conjunto de linhas de um coordenador, e o nome dela é o nome
    dele. Aqui a relação é direta: esta linha, neste período, é deste
    coordenador. ⛔ Sem vigência de propósito — o histórico do ICV é por
    linha e por dia (icv_apurado), então nenhum número se perde quando a
    responsabilidade muda. Trocar o coordenador é um UPDATE."""

    __tablename__ = "linha_coordenador"
    __table_args__ = (
        UniqueConstraint("linha_codigo", "periodo"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    funcionario_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    # Mesmo domínio de Turno.periodo (D8) — os dois períodos de uma linha
    # são cobertos por pessoas diferentes, não dois agrupamentos distintos.
    periodo: Mapped[str] = mapped_column(String(1), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Parametro(Base):
    """D29 — configuração de negócio do módulo (meta e corte de
    aceitável do ICV), única para toda a operação — nunca por
    agrupamento, nunca hardcoded em código."""

    __tablename__ = "parametro"
    __table_args__ = {"schema": SCHEMA}

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class IcvApurado(Base):
    """D20 — o ICV OFICIAL, apurado pela SPTrans no SIM e importado da
    planilha semanal da gerência. ⛔ Sem coluna `icv` nem `realizadas` —
    ambos calculados sempre (D20/D30), nunca gravados como estado
    derivado."""

    __tablename__ = "icv_apurado"
    __table_args__ = (
        UniqueConstraint("linha_codigo", "data_referencia"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    data_referencia: Mapped[date] = mapped_column(Date, nullable=False)
    programadas: Mapped[int] = mapped_column(Integer, nullable=False)
    realizadas_tp_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realizadas_ts_tp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lote: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    origem: Mapped[str] = mapped_column(String(16), nullable=False, default="PLANILHA")
    # D25 — TRUE quando os dois contadores brutos repetem o dia anterior
    # já importado E o ICV do dia não é 100%. Nunca recusa a linha.
    suspeito: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspeito_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    arquivo_nome: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    importado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    # Snapshot puro do que a planilha da gerência chama de bacia — não é
    # entidade, não tem FK, serve só para conferir depois se o agrupamento
    # por coordenador do sistema bate com o da gerência.
    bacia_texto: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AcaoCoordenacao(Base):
    """D26 — "ação tomada" mora no coordenador, não no fiscal. Registrado
    no painel, por linha/data/faixa de hora, com o que foi feito e o
    resultado observado."""

    __tablename__ = "acao_coordenacao"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    linha_codigo: Mapped[str] = mapped_column(String(20), nullable=False)
    data_referencia: Mapped[date] = mapped_column(Date, nullable=False)
    faixa_hora: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    resultado_observado: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
