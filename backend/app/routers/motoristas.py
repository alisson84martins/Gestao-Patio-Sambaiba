"""CRUD de motoristas."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, OperadorOuAdmin
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import Motorista, PerfilUsuarioEnum, StatusMotoristaEnum, Usuario
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
    # 1) Motoristas cadastrados na tabela própria
    q = select(Motorista)
    if status_filter:
        q = q.where(Motorista.status == status_filter)
    if busca:
        like = f"%{busca}%"
        q = q.where(or_(Motorista.re.ilike(like), Motorista.nome.ilike(like)))
    motoristas_db = db.execute(q).scalars().all()
    resultado: list[MotoristaRead] = [MotoristaRead.model_validate(m) for m in motoristas_db]

    # 2) Usuários com perfil MOTORISTA ainda não representados na tabela motorista.
    # Enquanto a integração com escala não for implementada, todo usuário MOTORISTA
    # do sistema deve aparecer aqui. No futuro, quando a escala vincular por motorista_id,
    # basta criar o registro na tabela motorista e ele deixa de entrar neste bloco.
    res_existentes = {m.re for m in motoristas_db}
    q_usuarios = (
        select(Usuario)
        .where(Usuario.perfil == PerfilUsuarioEnum.MOTORISTA)
        .where(Usuario.ativo.is_(True))
    )
    if res_existentes:
        q_usuarios = q_usuarios.where(~Usuario.re.in_(res_existentes))
    if busca:
        like = f"%{busca}%"
        q_usuarios = q_usuarios.where(or_(Usuario.re.ilike(like), Usuario.nome.ilike(like)))
    if status_filter and status_filter != StatusMotoristaEnum.ATIVO:
        # Usuários ativos só aparecem quando o filtro é ATIVO (ou sem filtro)
        usuarios_mot: list = []
    else:
        usuarios_mot = db.execute(q_usuarios).scalars().all()

    for u in usuarios_mot:
        resultado.append(MotoristaRead(
            id=u.id,
            re=u.re,
            nome=u.nome,
            cpf=u.cpf,
            status=StatusMotoristaEnum.ATIVO,
            codigo_externo=None,
            criado_em=u.criado_em,
            criado_por=u.criado_por,
            atualizado_em=u.atualizado_em,
            atualizado_por=u.atualizado_por,
        ))

    # Ordena tudo pelo nome e aplica paginação manualmente (já que as fontes são distintas)
    resultado.sort(key=lambda m: m.nome.lower())
    return resultado[pag.skip: pag.skip + pag.limit]


@router.get("/{motorista_id}", response_model=MotoristaRead)
def buscar(motorista_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    m = db.get(Motorista, motorista_id)
    if not m:
        raise HTTPException(status_code=404, detail="Motorista não encontrado")
    return m


@router.post("", response_model=MotoristaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: MotoristaCreate, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
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
    motorista_id: UUID, payload: MotoristaUpdate, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]
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
