"""Schemas Pydantic v2 do módulo Fiscalização.

Espelha database/migrations/029-modulo-fiscalizacao.sql. Campos com conjunto
fechado de opções (Literal) espelham os CHECK constraints da migration — a
fonte da verdade dos valores permitidos é o SQL.
"""
from datetime import date, datetime, time
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.base import ORMBase

Terminal = Literal["TP", "TS"]
Periodo = Literal["1", "2"]
TipoDia = Literal["UTIL", "SABADO", "DOMINGO"]
StatusTurno = Literal["ABERTO", "FECHADO"]
Resultado = Literal["REALIZADA", "PERDIDA"]
Motivo = Literal[
    "FALTA_OPERADORES", "RA", "SOS", "ATRASO_GARAGEM", "TROCA_OPERACIONAL", "OUTRO"
]
# D4 — VIAGEM_EXTRA só existe como evento avulso, nunca como motivo de uma
# partida PERDIDA (uma viagem extra não é uma partida da grade perdida).
TipoEvento = Literal[
    "FALTA_OPERADORES", "RA", "SOS", "ATRASO_GARAGEM", "TROCA_OPERACIONAL", "VIAGEM_EXTRA"
]
TipoBaita = Literal["BAITA", "ANTI_BAITA"]
EstadoPartida = Literal["REALIZADA", "PERDIDA", "ATRASADA", "AGUARDANDO"]


# ============================================================================
# PONTO / PONTO_LINHA (D9, D11) — catálogo
# ============================================================================

class PontoRead(ORMBase):
    codigo: str
    nome: str
    terminal: Terminal
    ativo: bool
    linhas: list[str] = Field(default_factory=list)


# ============================================================================
# TURNO (D8, D9, D14, D15)
# ============================================================================

class TurnoAbrirRequest(BaseModel):
    """POST /fiscalizacao/turnos — ponto, período, linhas confirmadas.
    terminal, fiscal_re e data_referencia são derivados pelo backend, nunca
    aceitos do cliente (terminal vem do ponto; fiscal_re e funcionario_id do
    usuário logado; data_referencia de FUSO_OPERACAO)."""

    ponto_codigo: str = Field(..., min_length=1, max_length=20)
    periodo: Periodo
    linhas: list[str] = Field(..., min_length=1)


class TurnoUpdateRequest(BaseModel):
    """PATCH /fiscalizacao/turnos/{id} — refeição do fiscal (D15) e pastas
    (D14). extra='forbid' não é necessário aqui: os únicos campos que fazem
    sentido editar depois de aberto são estes três."""

    refeicao_inicio: Optional[time] = None
    refeicao_fim: Optional[time] = None
    pastas_prefixo: Optional[str] = Field(None, max_length=10)


class TurnoRead(ORMBase):
    id: UUID
    funcionario_id: UUID
    fiscal_re: str
    ponto_codigo: str
    terminal: Terminal
    periodo: Periodo
    data_referencia: date
    tipo_dia: Optional[TipoDia] = None
    status: StatusTurno
    refeicao_inicio: Optional[time] = None
    refeicao_fim: Optional[time] = None
    pastas_prefixo: Optional[str] = None
    aberto_em: Optional[datetime] = None
    fechado_em: Optional[datetime] = None
    criado_em: datetime
    atualizado_em: Optional[datetime] = None
    linhas: list[str] = Field(default_factory=list)


# ============================================================================
# PARTIDAS — a grade do turno com estado calculado na leitura (D7)
# ============================================================================

class RegistroPartidaUpsert(BaseModel):
    """PUT /fiscalizacao/turnos/{id}/partidas — upsert pela chave única
    (turno_id, linha_codigo, numero_tabela, terminal, horario_programado).
    partida_programada_id é opcional: uma partida fora da grade importada
    (ex.: grade não importada ainda) ainda pode ser marcada — snapshot
    completo, a FK é só conveniência quando existe."""

    partida_programada_id: Optional[UUID] = None
    linha_codigo: str = Field(..., min_length=1, max_length=20)
    numero_tabela: int = Field(..., ge=1)
    terminal: Terminal
    horario_programado: time

    resultado: Resultado
    horario_real: Optional[time] = None

    motivo: Optional[Motivo] = None
    motivo_outro: Optional[str] = None
    prefixo: Optional[str] = Field(None, max_length=10)
    operador_re: Optional[str] = Field(None, max_length=20)

    observacao: Optional[str] = None

    @model_validator(mode="after")
    def _valida_motivo(self) -> "RegistroPartidaUpsert":
        # Mesmas três regras do CHECK da migration (ck_registro_*) —
        # falhar aqui como 422 é mais claro pro chamador que um IntegrityError.
        if self.resultado == "PERDIDA" and self.motivo is None:
            raise ValueError("PERDIDA exige `motivo`.")
        if self.motivo == "OUTRO" and not (self.motivo_outro or "").strip():
            raise ValueError("motivo=OUTRO exige `motivo_outro`.")
        if self.motivo in ("RA", "SOS") and not (self.prefixo or "").strip():
            raise ValueError(f"motivo={self.motivo} exige `prefixo` (D18).")
        return self


class RegistroPartidaRead(ORMBase):
    id: UUID
    turno_id: UUID
    partida_programada_id: Optional[UUID] = None
    linha_codigo: str
    numero_tabela: int
    terminal: Terminal
    horario_programado: time
    resultado: Resultado
    horario_real: Optional[time] = None
    motivo: Optional[Motivo] = None
    motivo_outro: Optional[str] = None
    prefixo: Optional[str] = None
    operador_re: Optional[str] = None
    observacao: Optional[str] = None
    registrado_em: datetime
    atualizado_em: Optional[datetime] = None


class PartidaEstadoItem(BaseModel):
    """Um horário programado com seu estado calculado na leitura (D7).
    🔴 ATRASADA/AGUARDANDO nunca são gravados — computados a cada chamada
    comparando horario_programado com o relógio atual em FUSO_OPERACAO."""

    partida_programada_id: Optional[UUID] = None
    numero_tabela: int
    terminal: Terminal
    horario_programado: time
    periodo: Optional[Periodo] = None
    estado: EstadoPartida
    registro: Optional[RegistroPartidaRead] = None


# ============================================================================
# EVENTOS avulsos (D4) e OBSERVAÇÕES (D5)
# ============================================================================

class EventoTurnoCreate(BaseModel):
    """POST /fiscalizacao/turnos/{id}/eventos — contador avulso, sem perda
    associada (D4). registro_partida_id nunca vem do cliente: só o backend
    preenche, ao criar o evento vinculado de uma partida marcada PERDIDA."""

    linha_codigo: str = Field(..., min_length=1, max_length=20)
    tipo: TipoEvento
    horario: Optional[time] = None
    numero_tabela: Optional[int] = Field(None, ge=1)
    prefixo: Optional[str] = Field(None, max_length=10)
    observacao: Optional[str] = None


class EventoTurnoRead(ORMBase):
    id: UUID
    turno_id: UUID
    linha_codigo: str
    tipo: TipoEvento
    horario: Optional[time] = None
    numero_tabela: Optional[int] = None
    prefixo: Optional[str] = None
    observacao: Optional[str] = None
    registro_partida_id: Optional[UUID] = None
    criado_em: datetime


class ObservacaoTurnoCreate(BaseModel):
    linha_codigo: Optional[str] = Field(None, max_length=20)
    numero_tabela: Optional[int] = Field(None, ge=1)
    horario: Optional[time] = None
    texto: str = Field(..., min_length=1)


class ObservacaoTurnoRead(ORMBase):
    id: UUID
    turno_id: UUID
    linha_codigo: Optional[str] = None
    numero_tabela: Optional[int] = None
    horario: Optional[time] = None
    texto: str
    criado_em: datetime


# ============================================================================
# BAITA / ANTI-BAITA (D13)
# ============================================================================

class BaitaUpsert(BaseModel):
    """PUT /fiscalizacao/turnos/{id}/baita — upsert por (turno, linha, tipo)."""

    linha_codigo: str = Field(..., min_length=1, max_length=20)
    tipo: TipoBaita
    prefixo: str = Field(..., min_length=1, max_length=10)
    motorista_re: Optional[str] = Field(None, max_length=20)
    cobrador_re: Optional[str] = Field(None, max_length=20)
    saida_tp: Optional[time] = None
    saida_ts: Optional[time] = None
    ts_circular: bool = False

    @model_validator(mode="after")
    def _circular_sem_horario(self) -> "BaitaUpsert":
        if self.ts_circular and self.saida_ts is not None:
            raise ValueError("ts_circular=true não aceita saida_ts preenchido.")
        return self


class BaitaRead(ORMBase):
    id: UUID
    turno_id: UUID
    linha_codigo: str
    tipo: TipoBaita
    prefixo: str
    motorista_re: Optional[str] = None
    cobrador_re: Optional[str] = None
    saida_tp: Optional[time] = None
    saida_ts: Optional[time] = None
    ts_circular: bool
    criado_em: datetime
    atualizado_em: Optional[datetime] = None


# ============================================================================
# PRONTIDÃO (D3) — o medidor. Avisa, nunca bloqueia.
# ============================================================================

class PendenciaItem(BaseModel):
    tipo: Literal[
        "PARTIDAS_SEM_RESPOSTA", "BAITA_FALTANDO", "ANTI_BAITA_FALTANDO", "PASTAS_NAO_INFORMADAS"
    ]
    quantidade: Optional[int] = None
    por_linha: Optional[dict[str, int]] = None
    linhas: Optional[list[str]] = None


class ProntidaoResponse(BaseModel):
    pronto: bool
    pendencias: list[PendenciaItem] = Field(default_factory=list)


# ============================================================================
# PAINEL do coordenador (D12, D17) — exige("fiscalizacao_painel")
# ============================================================================

class PainelPartidaItem(BaseModel):
    """Uma linha do painel — D12: todos os horários do dia, não só o que furou."""

    numero_tabela: int
    terminal: Terminal
    horario_programado: time
    estado: EstadoPartida
    motivo: Optional[Motivo] = None
    # D17 — só preenchido quando estado=PERDIDA, motivo RA/SOS e a view
    # vw_partida_recolhida encontrou uma recolhida na janela de tempo.
    recolhida_momento: Optional[datetime] = None
    recolhida_avaliacao: Optional[Literal["LIBERADO", "RETIDO"]] = None
    recolhida_prazo_minutos: Optional[int] = None


class PainelLinhaResponse(BaseModel):
    linha_codigo: str
    data_referencia: date
    partidas: list[PainelPartidaItem] = Field(default_factory=list)


# ============================================================================
# Bloco E (migration 030) — BACIA (D21), ICV_APURADO (D20/D25/D28/D30),
# AÇÃO DA COORDENAÇÃO (D26)
# ============================================================================

Lote = Literal["E2", "AR2"]
OrigemIcv = Literal["PLANILHA", "MANUAL"]


class BaciaRead(ORMBase):
    codigo: str
    nome: str
    coordenador_funcionario_id: Optional[UUID] = None
    meta_icv: float
    ativo: bool
    criado_em: datetime


class BaciaLinhaRead(ORMBase):
    id: UUID
    bacia_codigo: str
    linha_codigo: str
    vigencia_inicio: date
    vigencia_fim: Optional[date] = None
    ativo: bool
    criado_em: datetime


class IcvApuradoRead(ORMBase):
    id: UUID
    linha_codigo: str
    data_referencia: date
    programadas: int
    realizadas_tp_ts: int
    realizadas_ts_tp: int
    lote: Optional[Lote] = None
    origem: OrigemIcv
    suspeito: bool
    suspeito_motivo: Optional[str] = None
    arquivo_nome: Optional[str] = None
    importado_por: Optional[UUID] = None
    criado_em: datetime
    atualizado_em: Optional[datetime] = None


class AcaoCoordenacaoCreate(BaseModel):
    """POST /fiscalizacao/acoes — D26, registrado no painel pelo coordenador."""

    linha_codigo: str = Field(..., min_length=1, max_length=20)
    data_referencia: date
    faixa_hora: Optional[int] = Field(None, ge=0, le=23)
    descricao: str = Field(..., min_length=1)
    resultado_observado: Optional[str] = None


class AcaoCoordenacaoRead(ORMBase):
    id: UUID
    linha_codigo: str
    data_referencia: date
    faixa_hora: Optional[int] = None
    descricao: str
    resultado_observado: Optional[str] = None
    registrado_por: UUID
    criado_em: datetime
    atualizado_em: Optional[datetime] = None


# ============================================================================
# ICV calculado (D20-D23, D28, D30) — payload de app/services/icv.py,
# NÃO ORMBase (vem de dict, não de linha de tabela).
# ============================================================================

FontePerdaAbsoluta = Literal["OFICIAL", "CAMPO"]


class IcvLinhaDiaRead(BaseModel):
    """Equivalente de fiscalizacao.vw_icv_linha_dia (D20) — as duas fontes
    lado a lado, nunca combinadas numa chave só."""

    linha_codigo: str
    data_referencia: date
    programadas_oficial: Optional[int] = None
    realizadas_oficial: Optional[int] = None
    icv_oficial: Optional[float] = None
    suspeito: bool = False
    programadas_campo: Optional[int] = None
    realizadas_campo: Optional[int] = None
    icv_campo: Optional[float] = None
    perda_absoluta: Optional[float] = None
    fonte_perda_absoluta: Optional[FontePerdaAbsoluta] = None


class IcvBaciaDiaRead(BaseModel):
    """Equivalente de fiscalizacao.vw_icv_bacia_dia (D22) — ponderado,
    nunca média simples."""

    bacia_codigo: str
    bacia_nome: str
    meta_icv: float
    data_referencia: date
    programadas: int
    realizadas: int
    icv_ponderado: Optional[float] = None


class PrioridadeLinhaItem(IcvLinhaDiaRead):
    """Equivalente de uma linha de fiscalizacao.vw_prioridade_linha (D23),
    com a divergência de denominador (D28) e a bacia da linha."""

    divergencia_denominador: Optional[int] = None
    bacia_codigo: Optional[str] = None
    bacia_nome: Optional[str] = None


class CascataItem(BaseModel):
    """D24 — condição calculada: 2+ perdas na mesma linha/faixa hora/dia."""

    linha_codigo: str
    faixa_hora: int = Field(..., ge=0, le=23)
    quantidade: int


class MotivoLivreItem(BaseModel):
    """D27 — agrupamento dos textos de motivo_outro mais frequentes."""

    texto: str
    quantidade: int


# ============================================================================
# Placar impresso por linha (§7) — ⛔ nenhum campo de pessoa aqui de propósito
# ============================================================================

class PlacarEvolucaoDia(BaseModel):
    data_referencia: date
    icv: Optional[float] = None
    fonte: Optional[FontePerdaAbsoluta] = None


class PlacarLinhaRead(BaseModel):
    linha_codigo: str
    data_referencia: date
    meta_icv: Optional[float] = None
    icv_semana_anterior: Optional[float] = None
    evolucao: list[PlacarEvolucaoDia] = Field(default_factory=list)
