"""Endpoints de catálogo: funções e recursos."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models.cadastro import Funcao, Recurso
from app.schemas.cadastro import FuncaoRead, RecursoRead

router = APIRouter(tags=["funções e recursos"])


@router.get("/funcoes", response_model=list[FuncaoRead], summary="Lista as 12 funções do catálogo")
def listar_funcoes(
    _: CurrentUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[Funcao]:
    return db.execute(
        select(Funcao).where(Funcao.ativo == True).order_by(Funcao.nivel, Funcao.nome)
    ).scalars().all()


@router.get("/recursos", response_model=list[RecursoRead], summary="Lista os 10 recursos do catálogo")
def listar_recursos(
    _: CurrentUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[Recurso]:
    return db.execute(
        select(Recurso).where(Recurso.ativo == True).order_by(Recurso.ordem)
    ).scalars().all()
