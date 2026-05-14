"""CRUD dos tipos de defeito (catálogo)."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.core.utils import PaginationParams
from app.models import TipoDefeito
from app.schemas import TipoDefeitoCreate, TipoDefeitoRead, TipoDefeitoUpdate

router = APIRouter(prefix="/tipos-defeito", tags=["tipos de defeito"])


@router.get("", response_model=list[TipoDefeitoRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    categoria: Optional[str] = None,
    ativo: Optional[bool] = None,
):
    q = select(TipoDefeito)
    if categoria:
        q = q.where(TipoDefeito.categoria == categoria)
    if ativo is not None:
        q = q.where(TipoDefeito.ativo == ativo)
    q = q.order_by(TipoDefeito.categoria, TipoDefeito.nome).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{tipo_id}", response_model=TipoDefeitoRead)
def buscar(tipo_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    t = db.get(TipoDefeito, tipo_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tipo de defeito não encontrado")
    return t


@router.post("", response_model=TipoDefeitoRead, status_code=status.HTTP_201_CREATED)
def criar(payload: TipoDefeitoCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    if db.execute(select(TipoDefeito).where(TipoDefeito.codigo == payload.codigo)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Código {payload.codigo} já existe")
    t = TipoDefeito(**payload.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.patch("/{tipo_id}", response_model=TipoDefeitoRead)
def atualizar(
    tipo_id: UUID, payload: TipoDefeitoUpdate, user: AdminUser, db: Annotated[Session, Depends(get_db)]
):
    t = db.get(TipoDefeito, tipo_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tipo de defeito não encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t
