"""CRUD de filas e posições do pátio."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.core.utils import PaginationParams
from app.models import Fila, TipoFilaEnum
from app.schemas import FilaCreate, FilaRead, FilaUpdate

router = APIRouter(prefix="/filas", tags=["filas / pátio"])


@router.get("", response_model=list[FilaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    tipo: Optional[TipoFilaEnum] = None,
    ativa: Optional[bool] = None,
):
    q = select(Fila)
    if tipo:
        q = q.where(Fila.tipo == tipo)
    if ativa is not None:
        q = q.where(Fila.ativa == ativa)
    q = q.order_by(Fila.tipo, Fila.ordem_exibicao).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{fila_id}", response_model=FilaRead)
def buscar(fila_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    f = db.get(Fila, fila_id)
    if not f:
        raise HTTPException(status_code=404, detail="Fila não encontrada")
    return f


@router.post("", response_model=FilaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: FilaCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    f = Fila(**payload.model_dump())
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@router.patch("/{fila_id}", response_model=FilaRead)
def atualizar(
    fila_id: UUID, payload: FilaUpdate, user: AdminUser, db: Annotated[Session, Depends(get_db)]
):
    f = db.get(Fila, fila_id)
    if not f:
        raise HTTPException(status_code=404, detail="Fila não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    db.commit()
    db.refresh(f)
    return f
