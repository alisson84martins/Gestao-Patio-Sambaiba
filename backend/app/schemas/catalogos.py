"""Schemas de catálogos: linha, tipo_defeito, permissao."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import SetorEnum
from app.schemas.base import ORMBase


# =================== LINHA ===================
# Cadastro ÚNICO (migration 048): quem grava manda NÚMERO + SUFIXO e o backend
# monta `codigo` (271A-51) — ⛔ o código nunca é digitado. Formato validado
# pela mesma regra de app/core/linha.py (R2).
def _maiusculo(valor: Optional[str]) -> Optional[str]:
    return valor.strip().upper() if isinstance(valor, str) else valor


def _limpo(valor: Optional[str]) -> Optional[str]:
    return valor.strip() if isinstance(valor, str) else valor


class LinhaCreate(BaseModel):
    numero: str = Field(..., pattern=r"^[0-9A-Z]{4}$", description="Número da linha, 4 caracteres (ex.: 271A)")
    sufixo: str = Field("10", pattern=r"^[0-9]{1,3}$", description="Código da linha, só dígitos (padrão 10)")
    nome: Optional[str] = Field(None, max_length=120, description="Opcional (ex.: CANGAÍBA)")
    setor: SetorEnum
    ativa: bool = True

    _numero = field_validator("numero", mode="before")(_maiusculo)
    _sufixo = field_validator("sufixo", mode="before")(_limpo)
    _nome = field_validator("nome", mode="before")(_limpo)


class LinhaUpdate(BaseModel):
    numero: Optional[str] = Field(None, pattern=r"^[0-9A-Z]{4}$")
    sufixo: Optional[str] = Field(None, pattern=r"^[0-9]{1,3}$")
    nome: Optional[str] = Field(None, max_length=120, description="Vazio = sem nome (mostra só o código)")
    setor: Optional[SetorEnum] = None
    ativa: Optional[bool] = None

    _numero = field_validator("numero", mode="before")(_maiusculo)
    _sufixo = field_validator("sufixo", mode="before")(_limpo)
    _nome = field_validator("nome", mode="before")(_limpo)


class LinhaRead(ORMBase):
    # Formato que o Pátio e Cadastros já consumiam (id, codigo, nome, setor,
    # ativa, criado_em) + numero/sufixo. numero/sufixo nulos: manobra MAN-*.
    id: UUID
    codigo: str
    numero: Optional[str] = None
    sufixo: Optional[str] = None
    nome: str
    setor: SetorEnum
    ativa: bool
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
