"""Mover ônibus entre filas/posições do pátio."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import AlocacaoPatio, Fila, Onibus, TipoFilaEnum
from app.schemas import AlocacaoBlocoCreate, AlocacaoPatioCreate, AlocacaoPatioRead, AlocacaoPatioUpdate

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


@router.post("/bloco", response_model=AlocacaoPatioRead,
             status_code=status.HTTP_201_CREATED,
             summary="Modo bloco: aloca recebendo identificadores amigáveis")
def alocar_bloco(payload: AlocacaoBlocoCreate, user: CurrentUser,
                 db: Annotated[Session, Depends(get_db)]):
    """Endpoint atômico do modo bloco da Fase 5.3.

    - Resolve numero_frota → onibus_id e fila (numero ou nome) → fila_id
    - Sentido 'ida': insere na próxima posição livre
    - Sentido 'volta': empurra todas as ativas (posicao += 1) e insere em 1
    - Tudo em uma única transação (rollback automático em erro)
    """
    # 1) Resolve ônibus por numero_frota
    onibus = db.execute(
        select(Onibus).where(Onibus.numero_frota == payload.numero_frota)
    ).scalar_one_or_none()
    if not onibus:
        raise HTTPException(404, f"Ônibus {payload.numero_frota} não encontrado na frota")

    # 2) Resolve fila: tenta como número primeiro (ex: "5"), depois como nome (ex: "Lavador")
    fila = None
    fila_input = payload.fila.strip()
    if fila_input.isdigit():
        fila = db.execute(
            select(Fila).where(
                Fila.numero == int(fila_input),
                Fila.tipo == TipoFilaEnum.NUMERICA,
                Fila.ativa.is_(True),
            )
        ).scalar_one_or_none()
    if not fila:
        # Busca por nome (case-insensitive) para especiais e remotas
        fila = db.execute(
            select(Fila).where(
                Fila.nome.ilike(fila_input),
                Fila.ativa.is_(True),
            )
        ).scalar_one_or_none()
    if not fila:
        raise HTTPException(404, f"Fila '{payload.fila}' não encontrada")

    # 3) Calcula posição conforme sentido
    if payload.sentido == "ida":
        # Próxima posição livre = max(posicao ativa) + 1, ou 1 se vazia
        max_pos = db.execute(text("""
            SELECT COALESCE(MAX(posicao), 0)
              FROM alocacao_patio
             WHERE fila_id = :fila_id AND ativa = TRUE
        """), {"fila_id": fila.id}).scalar()
        nova_posicao = (max_pos or 0) + 1
    else:
        # VOLTA: truque pra escapar do unique constraint
        # Passo 1: nega temporariamente todas as posições ativas (sem conflito de unique)
        db.execute(text("""
            UPDATE alocacao_patio
               SET posicao = -posicao
             WHERE fila_id = :fila_id AND ativa = TRUE
        """), {"fila_id": fila.id})
        # Passo 2: volta pra positivo e incrementa
        db.execute(text("""
            UPDATE alocacao_patio
               SET posicao = ABS(posicao) + 1
             WHERE fila_id = :fila_id AND ativa = TRUE
        """), {"fila_id": fila.id})
        db.flush()
        nova_posicao = 1

    # 4) Insere a nova alocação (trigger desativa alocação anterior do mesmo ônibus)
    nova = AlocacaoPatio(
        onibus_id=onibus.id,
        fila_id=fila.id,
        posicao=nova_posicao,
        ativa=True,
        alocado_por=user.id,
    )
    set_create_audit(nova, user)
    db.add(nova)

    # 5) Commit único — se qualquer passo acima falhou, transação inteira reverte
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(409, f"Conflito ao alocar: {str(e)}")
    db.refresh(nova)
    return nova
