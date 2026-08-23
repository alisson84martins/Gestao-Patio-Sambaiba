"""Schemas Pydantic v2 — Pré-ocorrência do motorista.

🔴 Regra que atravessa este arquivo inteiro: os schemas de saída para o
CCO (`AutorizacaoResumoCCO`) e para o público (`PreOcorrenciaPublicoRead`)
são deliberadamente MAGROS — a decisão "CCO não lê conteúdo" e "endpoint
público é escrita sem leitura de OUTRA linha" vive aqui, no formato da
resposta, não só na permissão do endpoint.

⚠️ Decisão 5 ("escrita sem leitura") flexibilizada em 15/08/2026 SÓ para
o dado do PRÓPRIO motorista daquela autorização: a abertura (lado
autenticado, ver abrir_autorizacao() em pre_ocorrencias.py) agora grava a
PreOcorrencia já preenchida com o que a autorização informou + o cadastro
central pelo RE — o GET público continua só devolvendo o que está
gravado, nunca consulta o cadastro por conta própria. Pra dado de
TERCEIRO, vítima e testemunha a decisão 5 original continua valendo
integralmente — nenhuma leitura ali.
"""
from datetime import date, datetime, time
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.placa import PlacaNormalizadaOpcional
from app.schemas.base import ORMBase

StatusAutorizacao = Literal["AGUARDANDO", "ENVIADA", "EXPIRADA", "CANCELADA"]
StatusPreOcorrencia = Literal["AGUARDANDO", "ENVIADA", "CONVERTIDA", "DESCARTADA"]
TipoAnexoPreOcorrencia = Literal["CNH", "DOC_VEICULO", "OUTRO"]


# ============================================================================
# Abrir autorização (endpoint autenticado)
# ============================================================================

class AbrirAutorizacaoRequest(BaseModel):
    telefone_destino: str = Field(..., min_length=8, max_length=20)
    # Um dos dois é obrigatório quando quem abre é CCO; ignorados (vira o
    # próprio) quando quem abre é coordenador — checagem é no endpoint,
    # não aqui, porque o schema não sabe quem está chamando.
    # `coordenador_re` existe porque o CCO não tem leitura em "usuarios"
    # (decisão 4 — não lê conteúdo, e o catálogo de pessoas é desse
    # recurso) — não dá pra resolver RE → id no frontend via
    # /funcionarios/verificar como o resto do sistema faz; o backend
    # resolve internamente, sem exigir esse gate.
    coordenador_id: Optional[UUID] = None
    coordenador_re: Optional[str] = Field(None, max_length=20)
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


class CancelarAutorizacaoResponse(BaseModel):
    """PATCH .../cancelar — id + status, nada de conteúdo. Serve tanto pra
    coordenador/ADMIN quanto pro CCO (que pode cancelar o que ele mesmo
    abriu — decisão 4 não muda: CCO continua sem ver relato/documento)."""
    id: UUID
    status: StatusAutorizacao


class AutopreenchimentoPessoaPreOcorrencia(BaseModel):
    """GET /pre-ocorrencias/autopreencher/pessoa — SÓ nome e telefone,
    nunca CPF/RG/CNH. Quem abre autorização inclui o CCO (decisão 4: CCO
    só roteia, não lê conteúdo) — os documentos são copiados pro servidor
    na abertura (ver abrir_autorizacao()), sem nunca passar por esta tela.
    Sempre 200, corpo vazio quando o RE não é encontrado."""
    nome: Optional[str] = None
    telefone: Optional[str] = None


# ============================================================================
# Formulário público (token) — o motorista
# ============================================================================

class PreOcorrenciaAnexoPublicoRead(ORMBase):
    id: UUID
    tipo: TipoAnexoPreOcorrencia
    nome_original: Optional[str] = None
    # Item 3.2 (19/08/2026): o front decide entre abrir imagem inline e
    # baixar PDF a partir disto — nome_original sozinho não diz o tipo.
    mime_type: Optional[str] = None


class PreOcorrenciaPublicoRead(ORMBase):
    """GET /pre-ocorrencias/publico — só os campos desta pré-ocorrência
    (nunca de qualquer OUTRA linha — decisão 5, escrita sem leitura de
    dado alheio). Alguns já podem vir preenchidos: a abertura da
    autorização (lado autenticado) grava a linha com o que já era
    conhecido antes do motorista abrir o link — ver
    AutopreenchimentoPessoaPreOcorrencia acima."""
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

    # D10 (app/core/placa.py): normaliza, nunca rejeita por formato — o
    # motorista digita em pé, no local do acidente.
    terceiro_placa: PlacaNormalizadaOpcional = Field(None, max_length=10)
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
    # Item 1.2 (19/08/2026): o botão de apagar na fila (item 1.4) chama o
    # mesmo endpoint DELETE /pre-ocorrencias/autorizacoes/{autorizacao_id}
    # dos cards de "Minhas autorizações" — precisa do id da autorização,
    # não só da pré-ocorrência.
    autorizacao_id: UUID
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
