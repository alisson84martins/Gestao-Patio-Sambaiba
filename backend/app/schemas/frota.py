"""Schemas de frota e pátio: onibus, fila, alocacao_patio."""
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import SetorEnum, StatusOnibusEnum, TipoFilaEnum
from app.schemas.base import AuditoriaSchema, ORMBase, SyncSchema


# =================== ONIBUS ===================
class OnibusBase(BaseModel):
    numero_frota: int = Field(..., ge=1000, le=9999)
    placa: Optional[str] = Field(None, max_length=10)
    status: StatusOnibusEnum = StatusOnibusEnum.ATIVO
    codigo_externo: Optional[str] = Field(None, max_length=50)


class OnibusCreate(OnibusBase):
    pass


class OnibusUpdate(BaseModel):
    placa: Optional[str] = Field(None, max_length=10)
    status: Optional[StatusOnibusEnum] = None
    codigo_externo: Optional[str] = Field(None, max_length=50)


class OnibusRead(OnibusBase, ORMBase, AuditoriaSchema):
    id: UUID
    setor: Optional[SetorEnum] = None  # gerada pelo banco


# =================== FILA ===================
class FilaBase(BaseModel):
    tipo: TipoFilaEnum
    numero: Optional[int] = Field(None, ge=1, le=33)
    nome: str = Field(..., max_length=50)
    ordem_exibicao: int = 0
    ativa: bool = True


class FilaCreate(FilaBase):
    pass


class FilaUpdate(BaseModel):
    nome: Optional[str] = Field(None, max_length=50)
    ordem_exibicao: Optional[int] = None
    ativa: Optional[bool] = None


class FilaRead(FilaBase, ORMBase):
    id: UUID
    criado_em: datetime


# =================== ALOCACAO_PATIO ===================
class AlocacaoPatioBase(BaseModel):
    onibus_id: UUID
    fila_id: UUID
    posicao: int = Field(..., gt=0)
    ativa: bool = True


class AlocacaoPatioCreate(AlocacaoPatioBase):
    pass


class AlocacaoPatioUpdate(BaseModel):
    fila_id: Optional[UUID] = None
    posicao: Optional[int] = Field(None, gt=0)
    ativa: Optional[bool] = None


class AlocacaoPatioRead(AlocacaoPatioBase, ORMBase, AuditoriaSchema, SyncSchema):
    id: UUID
    alocado_por: Optional[UUID] = None
    alocado_em: datetime


class AlocacaoBlocoCreate(BaseModel):
    """Payload do modo bloco: aceita identificadores amigáveis."""
    numero_frota: int = Field(..., ge=1000, le=9999,
        description="Prefixo do ônibus (1xxx=E2, 2xxx=AR2)")
    fila: str = Field(..., min_length=1, max_length=120,
        description="Número da fila (ex: '5') ou nome da posição especial (ex: 'Lavador')")
    linha_codigo: Optional[str] = Field(None, max_length=20,
        description="Código da linha escalada (opcional, vai pra escala se preenchido)")
    sentido: Literal["ida", "volta"] = Field(...,
        description="Ida: insere na próxima posição livre. Volta: insere na posição 1 e empurra as outras.")
