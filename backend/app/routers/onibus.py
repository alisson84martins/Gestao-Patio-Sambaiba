"""CRUD de ônibus."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Onibus, PerfilUsuarioEnum, StatusOnibusEnum, SetorEnum
from app.schemas import OnibusCreate, OnibusRead, OnibusUpdate

router = APIRouter(prefix="/onibus", tags=["ônibus"])


def _pode_escrever(user) -> bool:
    return user.perfil in (PerfilUsuarioEnum.ADMIN, PerfilUsuarioEnum.COORDENADOR)


@router.get("", response_model=list[OnibusRead], summary="Lista ônibus")
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    status_filter: Annotated[Optional[StatusOnibusEnum], Query(alias="status")] = None,
    setor: Optional[SetorEnum] = None,
    numero_frota: Optional[int] = None,
):
    q = select(Onibus)
    if status_filter:
        q = q.where(Onibus.status == status_filter)
    if setor:
        q = q.where(Onibus.setor == setor)
    if numero_frota:
        q = q.where(Onibus.numero_frota == numero_frota)
    q = q.order_by(Onibus.numero_frota).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/frota/{numero}", response_model=OnibusRead, summary="Busca pelo número da frota")
def por_frota(numero: int, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    o = db.execute(select(Onibus).where(Onibus.numero_frota == numero)).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail=f"Ônibus {numero} não encontrado")
    return o


@router.get("/{onibus_id}", response_model=OnibusRead)
def buscar(onibus_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    o = db.get(Onibus, onibus_id)
    if not o:
        raise HTTPException(status_code=404, detail="Ônibus não encontrado")
    return o


@router.post("", response_model=OnibusRead, status_code=status.HTTP_201_CREATED)
def criar(payload: OnibusCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    if not _pode_escrever(user):
        raise HTTPException(status_code=403, detail="Apenas ADMIN ou COORDENADOR pode cadastrar")
    existente = db.execute(select(Onibus).where(Onibus.numero_frota == payload.numero_frota)).scalar_one_or_none()
    if existente:
        raise HTTPException(status_code=409, detail=f"Ônibus {payload.numero_frota} já cadastrado")
    o = Onibus(**payload.model_dump())
    set_create_audit(o, user)
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@router.patch("/{onibus_id}", response_model=OnibusRead)
def atualizar(
    onibus_id: UUID, payload: OnibusUpdate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
):
    if not _pode_escrever(user):
        raise HTTPException(status_code=403, detail="Apenas ADMIN ou COORDENADOR pode editar")
    o = db.get(Onibus, onibus_id)
    if not o:
        raise HTTPException(status_code=404, detail="Ônibus não encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    set_update_audit(o, user)
    db.commit()
    db.refresh(o)
    return o
