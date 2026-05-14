"""Schemas de catálogos: linha, tipo_defeito, permissao."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import SetorEnum
from app.schemas.base import ORMBase


# =================== LINHA ===================
class LinhaBase(BaseModel):
    codigo: str = Field(..., max_length=20)
    nome: str = Field(..., max_length=120)
    setor: SetorEnum
    ativa: bool = True


class LinhaCreate(LinhaBase):
    pass


class LinhaUpdate(BaseModel):
    codigo: Optional[str] = Field(None, max_length=20)
    nome: Optional[str] = Field(None, max_length=120)
    setor: Optional[SetorEnum] = None
    ativa: Optional[bool] = None


class LinhaRead(LinhaBase, ORMBase):
    id: UUID
    criado_em: datetime


# =================== TIPO_DEFEITO ===================
class TipoDefeitoBase(BaseModel):
    codigo: str = Field(..., max_length=20)
    nome: str = Field(..., max_length=120)
    categoria: Optional[str] = Field(None, max_length=50)
    ativo: bool = True


class TipoDefeitoCreate(TipoDefeitoBase):
    pass


class TipoDefeitoUpdate(BaseModel):
    codigo: Optional[str] = Field(None, max_length=20)
    nome: Optional[str] = Field(None, max_length=120)
    categoria: Optional[str] = Field(None, max_length=50)
    ativo: Optional[bool] = None


class TipoDefeitoRead(TipoDefeitoBase, ORMBase):
    id: UUID
    criado_em: datetime


# =================== PERMISSAO ===================
class PermissaoBase(BaseModel):
    usuario_id: UUID
    recurso: str = Field(..., max_length=50)
    pode_ler: bool = True
    pode_escrever: bool = False


class PermissaoCreate(PermissaoBase):
    concedido_por: Optional[UUID] = None


class PermissaoUpdate(BaseModel):
    pode_ler: Optional[bool] = None
    pode_escrever: Optional[bool] = None


class PermissaoRead(PermissaoBase, ORMBase):
    id: UUID
    concedido_por: Optional[UUID] = None
    criado_em: datetime
