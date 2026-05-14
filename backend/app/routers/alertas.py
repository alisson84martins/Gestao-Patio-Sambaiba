"""Alertas PRESO (retido na rua) e AMOSTRAL (SPTRANS)."""
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Alerta, Onibus, TipoAlertaEnum
from app.schemas import AlertaCreate, AlertaRead, AlertaResolver

router = APIRouter(prefix="/alertas", tags=["alertas"])


@router.post("", response_model=AlertaRead, status_code=status.HTTP_201_CREATED,
             summary="Registra alerta PRESO ou AMOSTRAL")
def criar(payload: AlertaCreate, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    """Cria alerta. UNIQUE parcial impede duplicidade de alerta ativo do mesmo tipo."""
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Ônibus não encontrado")
    a = Alerta(**payload.model_dump(), registrado_por=user.id)
    set_create_audit(a, user)
    try:
        db.add(a)
        db.commit()
    except Exception as exc:
        db.rollback()
        if "uq_alerta_onibus_tipo_ativo" in str(exc) or "unique" in str(exc).lower():
            raise HTTPException(409, f"Já existe alerta {payload.tipo.value} ativo para este ônibus")
        raise
    db.refresh(a)
    return a


@router.get("", response_model=list[AlertaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    tipo: Optional[TipoAlertaEnum] = None,
    resolvido: Optional[bool] = None,
    onibus_id: Optional[UUID] = None,
    incluir_deletados: bool = False,
):
    q = select(Alerta)
    if not incluir_deletados:
        q = q.where(Alerta.deletado_em.is_(None))
    if tipo:
        q = q.where(Alerta.tipo == tipo)
    if resolvido is not None:
        q = q.where(Alerta.resolvido == resolvido)
    if onibus_id:
        q = q.where(Alerta.onibus_id == onibus_id)
    # Prioridade na ordenação: PRESO antes de AMOSTRAL, depois por data
    q = q.order_by(Alerta.tipo, Alerta.criado_em.desc()).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{alerta_id}", response_model=AlertaRead)
def buscar(alerta_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    a = db.get(Alerta, alerta_id)
    if not a:
        raise HTTPException(404, "Alerta não encontrado")
    return a


@router.patch("/{alerta_id}/resolver", response_model=AlertaRead,
              summary="Marca alerta como resolvido")
def resolver(alerta_id: UUID, payload: AlertaResolver, user: CurrentUser,
             db: Annotated[Session, Depends(get_db)]):
    a = db.get(Alerta, alerta_id)
    if not a:
        raise HTTPException(404, "Alerta não encontrado")
    if a.resolvido:
        raise HTTPException(409, "Alerta já estava resolvido")
    a.resolvido = True
    a.resolvido_em = datetime.now(timezone.utc)
    a.resolvido_por = user.id
    if payload.motivo:
        a.motivo = (a.motivo or "") + f"\n[Resolvido] {payload.motivo}"
    set_update_audit(a, user)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/{alerta_id}", response_model=AlertaRead, summary="Soft delete")
def deletar(alerta_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    a = db.get(Alerta, alerta_id)
    if not a:
        raise HTTPException(404, "Alerta não encontrado")
    a.deletado_em = datetime.now(timezone.utc)
    set_update_audit(a, user)
    db.commit()
    db.refresh(a)
    return a
