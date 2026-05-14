"""CRUD de motoristas."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Motorista, PerfilUsuarioEnum, StatusMotoristaEnum
from app.schemas import MotoristaCreate, MotoristaRead, MotoristaUpdate

router = APIRouter(prefix="/motoristas", tags=["motoristas"])


def _pode_escrever(user) -> bool:
    return user.perfil in (PerfilUsuarioEnum.ADMIN, PerfilUsuarioEnum.COORDENADOR)


@router.get("", response_model=list[MotoristaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    status_filter: Annotated[Optional[StatusMotoristaEnum], Query(alias="status")] = None,
    busca: Annotated[Optional[str], Query(description="Busca por RE ou nome")] = None,
):
    q = select(Motorista)
    if status_filter:
        q = q.where(Motorista.status == status_filter)
    if busca:
        like = f"%{busca}%"
        q = q.where(or_(Motorista.re.ilike(like), Motorista.nome.ilike(like)))
    q = q.order_by(Motorista.nome).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{motorista_id}", response_model=MotoristaRead)
def buscar(motorista_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    m = db.get(Motorista, motorista_id)
    if not m:
        raise HTTPException(status_code=404, detail="Motorista não encontrado")
    return m


@router.post("", response_model=MotoristaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: MotoristaCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    if not _pode_escrever(user):
        raise HTTPException(status_code=403, detail="Apenas ADMIN ou COORDENADOR pode cadastrar")
    if db.execute(select(Motorista).where(Motorista.re == payload.re)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Motorista com RE {payload.re} já existe")
    m = Motorista(**payload.model_dump())
    set_create_audit(m, user)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.patch("/{motorista_id}", response_model=MotoristaRead)
def atualizar(
    motorista_id: UUID, payload: MotoristaUpdate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    if not _pode_escrever(user):
        raise HTTPException(status_code=403, detail="Apenas ADMIN ou COORDENADOR pode editar")
    m = db.get(Motorista, motorista_id)
    if not m:
        raise HTTPException(status_code=404, detail="Motorista não encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    set_update_audit(m, user)
    db.commit()
    db.refresh(m)
    return m
