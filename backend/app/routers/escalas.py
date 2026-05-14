"""CRUD de escala diária (importação Excel + manual)."""
from datetime import date as date_type, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Escala, Linha, Motorista, Onibus, OrigemEscalaEnum
from app.schemas import EscalaCreate, EscalaRead, EscalaUpdate

router = APIRouter(prefix="/escalas", tags=["escala"])


@router.post("", response_model=EscalaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: EscalaCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """Cria escala manual. Validação de setor cruzado é feita por trigger no banco."""
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Ônibus não encontrado")
    if not db.get(Linha, payload.linha_id):
        raise HTTPException(404, "Linha não encontrada")
    if payload.motorista_id and not db.get(Motorista, payload.motorista_id):
        raise HTTPException(404, "Motorista não encontrado")
    data = payload.model_dump()
    if not data.get("origem"):
        data["origem"] = OrigemEscalaEnum.MANUAL
    e = Escala(**data)
    set_create_audit(e, user)
    try:
        db.add(e)
        db.commit()
    except Exception as exc:
        db.rollback()
        # Trigger de validação de setor lança exception
        if "Setor incompatível" in str(exc) or "setor" in str(exc).lower():
            raise HTTPException(409, "Setor incompatível: linha E2 só em frota 1xxx; AR2 só em 2xxx")
        raise
    db.refresh(e)
    return e


@router.get("", response_model=list[EscalaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    data: Annotated[Optional[date_type], Query(description="Filtra por data (YYYY-MM-DD)")] = None,
    onibus_id: Optional[UUID] = None,
    motorista_id: Optional[UUID] = None,
    linha_id: Optional[UUID] = None,
    incluir_deletadas: bool = False,
):
    q = select(Escala)
    if not incluir_deletadas:
        q = q.where(Escala.deletado_em.is_(None))
    if data:
        q = q.where(Escala.data == data)
    if onibus_id:
        q = q.where(Escala.onibus_id == onibus_id)
    if motorista_id:
        q = q.where(Escala.motorista_id == motorista_id)
    if linha_id:
        q = q.where(Escala.linha_id == linha_id)
    q = q.order_by(Escala.data.desc(), Escala.horario_saida).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{escala_id}", response_model=EscalaRead)
def buscar(escala_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    e = db.get(Escala, escala_id)
    if not e:
        raise HTTPException(404, "Escala não encontrada")
    return e


@router.patch("/{escala_id}", response_model=EscalaRead)
def atualizar(escala_id: UUID, payload: EscalaUpdate, user: CurrentUser,
              db: Annotated[Session, Depends(get_db)]):
    e = db.get(Escala, escala_id)
    if not e:
        raise HTTPException(404, "Escala não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    set_update_audit(e, user)
    db.commit()
    db.refresh(e)
    return e


@router.delete("/{escala_id}", response_model=EscalaRead, summary="Soft delete")
def deletar(escala_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    e = db.get(Escala, escala_id)
    if not e:
        raise HTTPException(404, "Escala não encontrada")
    e.deletado_em = datetime.now(timezone.utc)
    set_update_audit(e, user)
    db.commit()
    db.refresh(e)
    return e
