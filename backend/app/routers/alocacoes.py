"""Mover ônibus entre filas/posições do pátio."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import AlocacaoPatio, Fila, Onibus
from app.schemas import AlocacaoPatioCreate, AlocacaoPatioRead, AlocacaoPatioUpdate

router = APIRouter(prefix="/alocacoes", tags=["alocações / pátio"])


@router.post("", response_model=AlocacaoPatioRead, status_code=status.HTTP_201_CREATED,
             summary="Aloca ônibus em fila/posição")
def criar(payload: AlocacaoPatioCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """Move um ônibus para uma fila. O trigger desativa automaticamente a alocação anterior."""
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Ônibus não encontrado")
    if not db.get(Fila, payload.fila_id):
        raise HTTPException(404, "Fila não encontrada")
    a = AlocacaoPatio(**payload.model_dump(), alocado_por=user.id)
    set_create_audit(a, user)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("", response_model=list[AlocacaoPatioRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    ativa: Optional[bool] = None,
    onibus_id: Optional[UUID] = None,
    fila_id: Optional[UUID] = None,
):
    q = select(AlocacaoPatio)
    if ativa is not None:
        q = q.where(AlocacaoPatio.ativa == ativa)
    if onibus_id:
        q = q.where(AlocacaoPatio.onibus_id == onibus_id)
    if fila_id:
        q = q.where(AlocacaoPatio.fila_id == fila_id)
    q = q.order_by(AlocacaoPatio.alocado_em.desc()).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{aloc_id}", response_model=AlocacaoPatioRead)
def buscar(aloc_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    a = db.get(AlocacaoPatio, aloc_id)
    if not a:
        raise HTTPException(404, "Alocação não encontrada")
    return a


@router.delete("/{aloc_id}", response_model=AlocacaoPatioRead,
               summary="Desativa alocação (tira ônibus da fila)")
def desativar(aloc_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """Marca a alocação como inativa. Não remove do banco — preserva o histórico."""
    a = db.get(AlocacaoPatio, aloc_id)
    if not a:
        raise HTTPException(404, "Alocação não encontrada")
    a.ativa = False
    set_update_audit(a, user)
    db.commit()
    db.refresh(a)
    return a
