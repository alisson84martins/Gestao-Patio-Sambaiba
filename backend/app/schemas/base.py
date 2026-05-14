"""Schemas base reutilizáveis."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ORMBase(BaseModel):
    """Permite criar schema a partir de um modelo SQLAlchemy."""
    model_config = ConfigDict(from_attributes=True)


class AuditoriaSchema(BaseModel):
    """Campos de auditoria comuns às tabelas operacionais."""
    criado_em: datetime
    criado_por: Optional[UUID] = None
    atualizado_em: Optional[datetime] = None
    atualizado_por: Optional[UUID] = None


class SyncSchema(BaseModel):
    """Campos de sincronização offline-first."""
    sincronizado_em: Optional[datetime] = None
    versao: int = 1
