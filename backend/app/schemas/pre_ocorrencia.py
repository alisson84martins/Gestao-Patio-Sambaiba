"""Schemas Pydantic v2 — Pré-ocorrência do motorista.

🔴 Regra que atravessa este arquivo inteiro: os schemas de saída para o
CCO (`AutorizacaoResumoCCO`) e para o público (`PreOcorrenciaPublicoRead`)
são deliberadamente MAGROS — a decisão "CCO não lê conteúdo" e "endpoint
público é escrita sem leitura de OUTRA linha" vive aqui, no formato da
resposta, não só na permissão do endpoint.
"""
from datetime import date, datetime, time
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import ORMBase

StatusAutorizacao = Literal["AGUARDANDO", "ENVIADA", "EXPIRADA", "CANCELADA"]
StatusPreOcorrencia = Literal["AGUARDANDO", "ENVIADA", "CONVERTIDA", "DESCARTADA"]
TipoAnexoPreOcorrencia = Literal["CNH", "DOC_VEICULO", "OUTRO"]


# ============================================================================
# Abrir autorização (endpoint autenticado)
# ============================================================================

class AbrirAutorizacaoRequest(BaseModel):
    telefone_destino: str = Field(..., min_length=8, max_length=20)
    # Obrigatório quando quem abre é CCO; ignorado (vira o próprio) quando
    # quem abre é coordenador — checagem é no endpoint, não aqui, porque
    # o schema não sabe quem está chamando.
    coordenador_id: Optional[UUID] = None
    motorista_re: Optional[str] = Field(None, max_length=20)
    motorista_nome: Optional[str] = Field(None, max_length=120)
    prefixo: Optional[str] = Field(None, max_length=10)
    linha_codigo: Optional[str] = Field(None, max_length=20)


class AutorizacaoAbertaResponse(BaseModel):
    """🔴 O único lugar do sistema onde o token aparece em claro — uma vez,
    na resposta deste POST. Depois disso só existe o hash."""
    id: UUID
    token: str
    link: str
    expira_em: datetime


class AutorizacaoResumoCCO(BaseModel):
    """🔴 O que o CCO vê — status, nada mais. Decisão 4: CCO abre e roteia,
    não lê conteúdo. Sem relato, sem dado de terceiro, sem documento —
    e sem nem o que o próprio CCO digitou (nome/prefixo), pra não criar
    meio-termo: ou é status, ou é conteúdo."""
    id: UUID
    status_texto: str  # "aguardando o motorista" | "enviada, com o coordenador Fulano" | ...
    criada_em: datetime


class AutorizacaoResumo(ORMBase):
    """O que coordenador/ADMIN veem — inclui o que a autorização já tinha."""
    id: UUID
    status: StatusAutorizacao
    coordenador_id: UUID
    telefone_destino: str
    motorista_nome: Optional[str] = None
    prefixo: Optional[str] = None
    criada_em: datetime
    expira_em: datetime
    usada_em: Optional[datetime] = None


# ============================================================================
# Formulário público (token) — o motorista
# ============================================================================

class PreOcorrenciaAnexoPublicoRead(ORMBase):
    id: UUID
    tipo: TipoAnexoPreOcorrencia
    nome_original: Optional[str] = None


class PreOcorrenciaPublicoRead(ORMBase):
    """GET /pre-ocorrencias/publico — só os campos que o PRÓPRIO motorista
    preencheu nesta pré-ocorrência (não devolve nada da autorização nem de
    qualquer outra linha — decisão 5, escrita sem leitura de dado alheio).
    """
    motorista_re: Optional[str] = None
    motorista_nome: Optional[str] = None
    motorista_telefone: Optional[str] = None
    motorista_cpf: Optional[str] = None
    motorista_rg: Optional[str] = None
    motorista_cnh: Optional[str] = None

    cobrador_re: Optional[str] = None
    cobrador_nome: Optional[str] = None
    cobrador_telefone: Optional[str] = None

    prefixo: Optional[str] = None

    data_ocorrencia: Optional[date] = None
    hora_ocorrencia: Optional[time] = None
    local_logradouro: Optional[str] = None
    local_numero: Optional[str] = None
    local_bairro: Optional[str] = None
    local_cidade: Optional[str] = None
    local_referencia: Optional[str] = None

    relato: Optional[str] = None

    terceiro_placa: Optional[str] = None
    terceiro_modelo_cor: Optional[str] = None
    terceiro_nome: Optional[str] = None
    terceiro_telefone: Optional[str] = None
    terceiro_seguradora: Optional[str] = None

    status: StatusPreOcorrencia
    anexos: list[PreOcorrenciaAnexoPublicoRead] = []


class PreOcorrenciaPublicoUpdate(BaseModel):
    """PATCH /pre-ocorrencias/publico — autosave. Tudo opcional; só
    relato e hora_ocorrencia acabam sendo exigidos no ENVIO (não aqui) —
    ver regra de ouro do projeto: máscara/campo nunca impede salvar."""
    motorista_re: Optional[str] = Field(None, max_length=20)
    motorista_nome: Optional[str] = Field(None, max_length=120)
    motorista_telefone: Optional[str] = Field(None, max_length=20)
    motorista_cpf: Optional[str] = Field(None, max_length=14)
    motorista_rg: Optional[str] = Field(None, max_length=20)
    motorista_cnh: Optional[str] = Field(None, max_length=20)

    cobrador_re: Optional[str] = Field(None, max_length=20)
    cobrador_nome: Optional[str] = Field(None, max_length=120)
    cobrador_telefone: Optional[str] = Field(None, max_length=20)

    prefixo: Optional[str] = Field(None, max_length=10)

    data_ocorrencia: Optional[date] = None
    hora_ocorrencia: Optional[time] = None
    local_logradouro: Optional[str] = Field(None, max_length=200)
    local_numero: Optional[str] = Field(None, max_length=20)
    local_bairro: Optional[str] = Field(None, max_length=80)
    local_cidade: Optional[str] = Field(None, max_length=80)
    local_referencia: Optional[str] = Field(None, max_length=200)

    relato: Optional[str] = None

    terceiro_placa: Optional[str] = Field(None, max_length=10)
    terceiro_modelo_cor: Optional[str] = Field(None, max_length=120)
    terceiro_nome: Optional[str] = Field(None, max_length=120)
    terceiro_telefone: Optional[str] = Field(None, max_length=20)
    terceiro_seguradora: Optional[str] = Field(None, max_length=80)


class EnviarResponse(BaseModel):
    protocolo: str


# ============================================================================
# Fila do coordenador (endpoint autenticado)
# ============================================================================

class PreOcorrenciaFilaItem(BaseModel):
    id: UUID
    status: StatusPreOcorrencia
    motorista_nome: Optional[str] = None
    prefixo: Optional[str] = None
    criado_em: datetime
    enviada_em: Optional[datetime] = None
    pendencias: list[str] = []  # ex.: ["CNH", "DOC_VEICULO"] — o que falta


class PreOcorrenciaDetalhe(ORMBase):
    id: UUID
    autorizacao_id: UUID
    motorista_re: Optional[str] = None
    motorista_nome: Optional[str] = None
    motorista_telefone: Optional[str] = None
    motorista_cpf: Optional[str] = None
    motorista_rg: Optional[str] = None
    motorista_cnh: Optional[str] = None
    cobrador_re: Optional[str] = None
    cobrador_nome: Optional[str] = None
    cobrador_telefone: Optional[str] = None
    prefixo: Optional[str] = None
    data_ocorrencia: Optional[date] = None
    hora_ocorrencia: Optional[time] = None
    local_logradouro: Optional[str] = None
    local_numero: Optional[str] = None
    local_bairro: Optional[str] = None
    local_cidade: Optional[str] = None
    local_referencia: Optional[str] = None
    relato: Optional[str] = None
    terceiro_placa: Optional[str] = None
    terceiro_modelo_cor: Optional[str] = None
    terceiro_nome: Optional[str] = None
    terceiro_telefone: Optional[str] = None
    terceiro_seguradora: Optional[str] = None
    status: StatusPreOcorrencia
    criado_em: datetime
    enviada_em: Optional[datetime] = None
    convertida_em: Optional[datetime] = None
    ocorrencia_id: Optional[UUID] = None
    anexos: list[PreOcorrenciaAnexoPublicoRead] = []


class ConverterRequest(BaseModel):
    """tipo_ocorrencia_id opcional — sem dado suficiente pra adivinhar
    (o motorista não escolhe tipo). Se não vier, o endpoint usa o
    catálogo 'OUTROS' (nunca bloqueia a conversão por falta de
    classificação)."""
    tipo_ocorrencia_id: Optional[UUID] = None


class ConverterResponse(BaseModel):
    ocorrencia_id: UUID
    ocorrencia_numero: int
