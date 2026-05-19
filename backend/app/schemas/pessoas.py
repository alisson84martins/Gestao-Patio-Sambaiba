"""Schemas de motorista e usuario."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import PerfilUsuarioEnum, StatusMotoristaEnum
from app.schemas.base import AuditoriaSchema, ORMBase


# =================== MOTORISTA ===================
class MotoristaBase(BaseModel):
    re: str = Field(..., max_length=20)
    nome: str = Field(..., max_length=120)
    cpf: Optional[str] = Field(None, max_length=14)
    status: StatusMotoristaEnum = StatusMotoristaEnum.ATIVO
    codigo_externo: Optional[str] = Field(None, max_length=50)


class MotoristaCreate(MotoristaBase):
    pass


class MotoristaUpdate(BaseModel):
    re: Optional[str] = Field(None, max_length=20)
    nome: Optional[str] = Field(None, max_length=120)
    cpf: Optional[str] = Field(None, max_length=14)
    status: Optional[StatusMotoristaEnum] = None
    codigo_externo: Optional[str] = Field(None, max_length=50)


class MotoristaRead(MotoristaBase, ORMBase, AuditoriaSchema):
    id: UUID


# =================== USUARIO ===================
class UsuarioBase(BaseModel):
    re: str = Field(..., max_length=20)
    nome: str = Field(..., max_length=120)
    perfil: PerfilUsuarioEnum
    ativo: bool = True
    motorista_id: Optional[UUID] = None


class UsuarioCreate(UsuarioBase):
    senha: str = Field(..., min_length=6, max_length=72)


class UsuarioUpdate(BaseModel):
    nome: Optional[str] = Field(None, max_length=120)
    perfil: Optional[PerfilUsuarioEnum] = None
    ativo: Optional[bool] = None
    motorista_id: Optional[UUID] = None
    senha: Optional[str] = Field(None, min_length=6, max_length=72)


class UsuarioRead(UsuarioBase, ORMBase, AuditoriaSchema):
    """Saída segura: NUNCA inclui senha_hash."""
    id: UUID
    ultimo_acesso: Optional[datetime] = None
    primeiro_acesso: bool = True
