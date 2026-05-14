"""Fichas de manutenção (defeitos abertos e concluídos)."""
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import FichaManutencao, Onibus, StatusFichaEnum, TipoDefeito
from app.schemas import FichaManutencaoCreate, FichaManutencaoRead, FichaManutencaoUpdate

router = APIRouter(prefix="/manutencao", tags=["manutenção"])


@router.post("", response_model=FichaManutencaoRead, status_code=status.HTTP_201_CREATED,
             summary="Abre ficha de manutenção")
def criar(payload: FichaManutencaoCreate, user: CurrentUser,
          db: Annotated[Session, Depends(get_db)]):
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Ônibus não encontrado")
    if not db.get(TipoDefeito, payload.tipo_defeito_id):
        raise HTTPException(404, "Tipo de defeito não encontrado")
    f = FichaManutencao(**payload.model_dump())
    set_create_audit(f, user)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@router.get("", response_model=list[FichaManutencaoRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    status_filter: Annotated[Optional[StatusFichaEnum], "alias=status"] = None,
    onibus_id: Optional[UUID] = None,
    incluir_deletadas: bool = False,
):
    q = select(FichaManutencao)
    if not incluir_deletadas:
        q = q.where(FichaManutencao.deletado_em.is_(None))
    if status_filter:
        q = q.where(FichaManutencao.status == status_filter)
    if onibus_id:
        q = q.where(FichaManutencao.onibus_id == onibus_id)
    q = q.order_by(FichaManutencao.aberta_em.desc()).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{ficha_id}", response_model=FichaManutencaoRead)
def buscar(ficha_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    f = db.get(FichaManutencao, ficha_id)
    if not f:
        raise HTTPException(404, "Ficha não encontrada")
    return f


@router.patch("/{ficha_id}", response_model=FichaManutencaoRead,
              summary="Atualiza ficha (concluir, mudar status, atribuir mecânico)")
def atualizar(ficha_id: UUID, payload: FichaManutencaoUpdate, user: CurrentUser,
              db: Annotated[Session, Depends(get_db)]):
    f = db.get(FichaManutencao, ficha_id)
    if not f:
        raise HTTPException(404, "Ficha não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    set_update_audit(f, user)
    # concluida_em é preenchido pelo trigger fn_ficha_concluida_em
    db.commit()
    db.refresh(f)
    return f


@router.delete("/{ficha_id}", response_model=FichaManutencaoRead, summary="Soft delete")
def deletar(ficha_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    f = db.get(FichaManutencao, ficha_id)
    if not f:
        raise HTTPException(404, "Ficha não encontrada")
    f.deletado_em = datetime.now(timezone.utc)
    set_update_audit(f, user)
    db.commit()
    db.refresh(f)
    return f
