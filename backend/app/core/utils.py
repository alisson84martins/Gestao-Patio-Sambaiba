"""Utilidades compartilhadas: paginação e auditoria."""
from typing import Annotated
from uuid import UUID

from fastapi import Query

from app.models import Usuario


class PaginationParams:
    """Parâmetros padrão de paginação para listagens."""

    def __init__(
        self,
        skip: Annotated[int, Query(ge=0, description="Quantos registros pular")] = 0,
        limit: Annotated[int, Query(ge=1, le=200, description="Máximo por página (1–200)")] = 50,
    ):
        self.skip = skip
        self.limit = limit


def set_create_audit(modelo, user: Usuario) -> None:
    """Preenche criado_por com o usuário corrente."""
    if hasattr(modelo, "criado_por"):
        modelo.criado_por = user.id


def set_update_audit(modelo, user: Usuario) -> None:
    """Preenche atualizado_por (atualizado_em é setado pelo trigger no banco)."""
    if hasattr(modelo, "atualizado_por"):
        modelo.atualizado_por = user.id
