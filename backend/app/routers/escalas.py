"""CRUD de escala diaria (importacao Excel + manual)."""
from datetime import date as date_type, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, OperadorOuAdmin
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Escala, Linha, Motorista, Onibus, OrigemEscalaEnum, PerfilUsuarioEnum
from app.schemas import EscalaCreate, EscalaRead, EscalaUpdate

router = APIRouter(prefix="/escalas", tags=["escala"])


@router.post("", response_model=EscalaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: EscalaCreate, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
    """Cria escala manual. Validacao de setor cruzado e feita por trigger no banco."""
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Onibus nao encontrado")
    if not db.get(Linha, payload.linha_id):
        raise HTTPException(404, "Linha nao encontrada")
    if payload.motorista_id and not db.get(Motorista, payload.motorista_id):
        raise HTTPException(404, "Motorista nao encontrado")
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
        if "Setor incompativel" in str(exc) or "setor" in str(exc).lower():
            raise HTTPException(409, "Setor incompativel: linha E2 so em frota 1xxx; AR2 so em 2xxx")
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
    # Apenas ADMIN e COORDENADOR podem ver registros soft-deleted
    if incluir_deletadas and user.perfil not in (
        PerfilUsuarioEnum.ADMIN, PerfilUsuarioEnum.COORDENADOR
    ):
        raise HTTPException(403, "Apenas ADMIN ou COORDENADOR pode listar registros deletados")
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
        raise HTTPException(404, "Escala nao encontrada")
    return e


@router.patch("/{escala_id}", response_model=EscalaRead)
def atualizar(
    escala_id: UUID,
    payload: EscalaUpdate,
    user: OperadorOuAdmin,
    db: Annotated[Session, Depends(get_db)],
):
    e = db.get(Escala, escala_id)
    if not e:
        raise HTTPException(404, "Escala nao encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    set_update_audit(e, user)
    db.commit()
    db.refresh(e)
    return e


@router.delete("", status_code=200, summary="Soft delete em lote — limpa escalas de uma data")
def limpar_dia(
    user: OperadorOuAdmin,
    db: Annotated[Session, Depends(get_db)],
    data: Annotated[
        Optional[date_type],
        Query(description="Data a limpar (YYYY-MM-DD). Default: hoje UTC."),
    ] = None,
):
    """Soft-delete em lote das escalas de uma data. Usado pelo botao Limpar Escala."""
    if data is None:
        data = datetime.now(timezone.utc).date()
    result = db.execute(
        update(Escala)
        .where(Escala.data == data, Escala.deletado_em.is_(None))
        .values(deletado_em=datetime.now(timezone.utc))
    )
    db.commit()
    return {"removidas": result.rowcount}


@router.delete("/{escala_id}", response_model=EscalaRead, summary="Soft delete")
def deletar(escala_id: UUID, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
    e = db.get(Escala, escala_id)
    if not e:
        raise HTTPException(404, "Escala nao encontrada")
    e.deletado_em = datetime.now(timezone.utc)
    set_update_audit(e, user)
    db.commit()
    db.refresh(e)
    return e
