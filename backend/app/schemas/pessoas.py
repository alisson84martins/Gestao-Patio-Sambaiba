"""Schemas de motorista e usuario."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.registro import ReNormalizado, ReNormalizadoObrigatorio
from app.models.enums import PerfilUsuarioEnum, StatusMotoristaEnum
from app.schemas.base import AuditoriaSchema, ORMBase


# =================== MOTORISTA ===================
class MotoristaBase(BaseModel):
    re: ReNormalizadoObrigatorio = Field(..., min_length=1, max_length=20)
    nome: str = Field(..., max_length=120)
    cpf: Optional[str] = Field(None, max_length=14)
    status: StatusMotoristaEnum = StatusMotoristaEnum.ATIVO
    codigo_externo: Optional[str] = Field(None, max_length=50)


class MotoristaCreate(MotoristaBase):
    pass


class MotoristaUpdate(BaseModel):
    re: ReNormalizado = Field(None, max_length=20)
    nome: Optional[str] = Field(None, max_length=120)
    cpf: Optional[str] = Field(None, max_length=14)
    status: Optional[StatusMotoristaEnum] = None
    codigo_externo: Optional[str] = Field(None, max_length=50)


class MotoristaRead(MotoristaBase, ORMBase, AuditoriaSchema):
    id: UUID


# =================== USUARIO ===================
class UsuarioBase(BaseModel):
    re: ReNormalizadoObrigatorio = Field(..., min_length=1, max_length=20)
    nome: str = Field(..., max_length=120)
    perfil: PerfilUsuarioEnum
    ativo: bool = True
    motorista_id: Optional[UUID] = None


class UsuarioCreate(UsuarioBase):
    cpf: str = Field(..., min_length=11, max_length=14, description="CPF com ou sem formatação. Últimos 4 dígitos viram senha inicial.")


class UsuarioUpdate(BaseModel):
    nome: Optional[str] = Field(None, max_length=120)
    perfil: Optional[PerfilUsuarioEnum] = None
    ativo: Optional[bool] = None
    motorista_id: Optional[UUID] = None
    senha: Optional[str] = Field(None, min_length=6, max_length=72)


class UsuarioRead(UsuarioBase, ORMBase, AuditoriaSchema):
    """Saída segura: NUNCA inclui senha_hash."""
    id: UUID
    cpf: Optional[str] = None