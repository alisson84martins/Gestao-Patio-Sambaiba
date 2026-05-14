"""Schemas operacionais: escala, alerta, ficha_manutencao, importacao_escala."""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    OrigemEscalaEnum,
    StatusFichaEnum,
    StatusImportacaoEnum,
    TipoAlertaEnum,
    TipoEscalaEnum,
)
from app.schemas.base import AuditoriaSchema, ORMBase, SyncSchema


# =================== ESCALA ===================
class EscalaBase(BaseModel):
    data: date
    onibus_id: UUID
    motorista_id: Optional[UUID] = None
    linha_id: UUID
    horario_saida: time
    tipo: TipoEscalaEnum
    origem: OrigemEscalaEnum = OrigemEscalaEnum.MANUAL


class EscalaCreate(EscalaBase):
    importacao_id: Optional[UUID] = None


class EscalaUpdate(BaseModel):
    motorista_id: Optional[UUID] = None
    linha_id: Optional[UUID] = None
    horario_saida: Optional[time] = None
    tipo: Optional[TipoEscalaEnum] = None


class EscalaRead(EscalaBase, ORMBase, AuditoriaSchema, SyncSchema):
    id: UUID
    importacao_id: Optional[UUID] = None
    deletado_em: Optional[datetime] = None


# =================== ALERTA ===================
class AlertaBase(BaseModel):
    onibus_id: UUID
    tipo: TipoAlertaEnum
    motivo: Optional[str] = None


class AlertaCreate(AlertaBase):
    pass


class AlertaResolver(BaseModel):
    """Schema específico para resolver um alerta."""
    motivo: Optional[str] = None  # comentário opcional ao resolver


class AlertaRead(AlertaBase, ORMBase, AuditoriaSchema, SyncSchema):
    id: UUID
    registrado_por: Optional[UUID] = None
    resolvido: bool
    resolvido_em: Optional[datetime] = None
    resolvido_por: Optional[UUID] = None
    deletado_em: Optional[datetime] = None


# =================== FICHA_MANUTENCAO ===================
class FichaManutencaoBase(BaseModel):
    onibus_id: UUID
    motorista_id: Optional[UUID] = None
    mecanico_id: Optional[UUID] = None
    tipo_defeito_id: UUID
    descricao: Optional[str] = None
    status: StatusFichaEnum = StatusFichaEnum.ABERTA


class FichaManutencaoCreate(FichaManutencaoBase):
    pass


class FichaManutencaoUpdate(BaseModel):
    mecanico_id: Optional[UUID] = None
    descricao: Optional[str] = None
    status: Optional[StatusFichaEnum] = None


class FichaManutencaoRead(FichaManutencaoBase, ORMBase, AuditoriaSchema, SyncSchema):
    id: UUID
    aberta_em: datetime
    concluida_em: Optional[datetime] = None
    deletado_em: Optional[datetime] = None


# =================== IMPORTACAO_ESCALA ===================
class ImportacaoEscalaBase(BaseModel):
    arquivo_nome: str = Field(..., max_length=255)
    arquivo_hash: Optional[str] = Field(None, max_length=64)
    data_escala: date
    total_registros: int = 0
    registros_sucesso: int = 0
    registros_erro: int = 0
    status: StatusImportacaoEnum
    erro_detalhe: Optional[str] = None


class ImportacaoEscalaCreate(ImportacaoEscalaBase):
    pass


class ImportacaoEscalaRead(ImportacaoEscalaBase, ORMBase):
    id: UUID
    importado_por: Optional[UUID] = None
    importado_em: datetime
