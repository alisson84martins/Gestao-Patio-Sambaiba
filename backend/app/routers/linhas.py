"""CRUD do catálogo de linhas (E2 e AR2)."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.core.utils import PaginationParams
from app.models import Escala, Linha, SetorEnum
from app.schemas import LinhaCreate, LinhaRead, LinhaUpdate

router = APIRouter(prefix="/linhas", tags=["linhas"])


@router.get("", response_model=list[LinhaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    setor: Optional[SetorEnum] = None,
    ativa: Optional[bool] = None,
):
    q = select(Linha)
    if setor:
        q = q.where(Linha.setor == setor)
    if ativa is not None:
        q = q.where(Linha.ativa == ativa)
    q = q.order_by(Linha.codigo).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{linha_id}", response_model=LinhaRead)
def buscar(linha_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    return linha


@router.post("", response_model=LinhaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: LinhaCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    if db.execute(select(Linha).where(Linha.codigo == payload.codigo)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Linha {payload.codigo} já cadastrada")
    linha = Linha(**payload.model_dump())
    db.add(linha)
    db.commit()
    db.refresh(linha)
    return linha


@router.patch("/{linha_id}", response_model=LinhaRead)
def atualizar(
    linha_id: UUID, payload: LinhaUpdate, user: AdminUser, db: Annotated[Session, Depends(get_db)]
):
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    # Se o código mudou, verificar unicidade
    novo_codigo = payload.codigo if payload.codigo is not None else None
    if novo_codigo and novo_codigo != linha.codigo:
        existe = db.execute(select(Linha).where(Linha.codigo == novo_codigo)).scalar_one_or_none()
        if existe:
            raise HTTPException(status_code=409, detail=f"Já existe uma linha com código {novo_codigo}")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(linha, k, v)
    db.commit()
    db.refresh(linha)
    return linha


@router.delete("/{linha_id}", status_code=200, summary="Remove linha do catálogo")
def deletar(linha_id: UUID, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    """Remove a linha. Falha se ainda houver escalas ativas usando-a."""
    from app.models import Escala
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    em_uso = db.execute(
        select(Escala).where(Escala.linha_id == linha_id, Escala.deletado_em.is_(None)).limit(1)
    ).scalar_one_or_none()
    if em_uso:
        raise HTTPException(
            status_code=409,
            detail=f"Linha {linha.codigo} está em uso na escala ativa — remova ou troque antes de excluir"
        )
    codigo = linha.codigo
    db.delete(linha)
    db.commit()
    return {"ok": True, "codigo": codigo}
