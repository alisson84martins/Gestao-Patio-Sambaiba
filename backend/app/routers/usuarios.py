"""CRUD de usuários do sistema (apenas ADMIN)."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminUser
from app.core.security import hash_password
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from app.models import PerfilUsuarioEnum, Usuario
from app.schemas import UsuarioCreate, UsuarioRead, UsuarioUpdate

router = APIRouter(prefix="/usuarios", tags=["usuários"])


@router.get("", response_model=list[UsuarioRead])
def listar(
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    perfil: Optional[PerfilUsuarioEnum] = None,
    ativo: Optional[bool] = None,
    busca: Annotated[Optional[str], Query(description="Busca por RE ou nome")] = None,
):
    q = select(Usuario)
    if perfil:
        q = q.where(Usuario.perfil == perfil)
    if ativo is not None:
        q = q.where(Usuario.ativo == ativo)
    if busca:
        like = f"%{busca}%"
        q = q.where(or_(Usuario.re.ilike(like), Usuario.nome.ilike(like)))
    q = q.order_by(Usuario.nome).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{usuario_id}", response_model=UsuarioRead)
def buscar(usuario_id: UUID, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    u = db.get(Usuario, usuario_id)
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return u


@router.post("", response_model=UsuarioRead, status_code=status.HTTP_201_CREATED)
def criar(payload: UsuarioCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    if db.execute(select(Usuario).where(Usuario.re == payload.re)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Usuário com RE {payload.re} já existe")
    data = payload.model_dump()
    # Extrai últimos 4 dígitos do CPF como senha inicial.
    # O CPF é mantido em data → persiste em Usuario.cpf (nunca exposto na API).
    cpf_raw = data["cpf"]
    cpf_digits = ''.join(c for c in cpf_raw if c.isdigit())
    if len(cpf_digits) < 4:
        raise HTTPException(status_code=422, detail="CPF inválido — não foi possível extrair 4 dígitos")
    senha_inicial = cpf_digits[-4:]
    u = Usuario(**data, senha_hash=hash_password(senha_inicial))
    set_create_audit(u, user)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.patch("/{usuario_id}", response_model=UsuarioRead)
def atualizar(
    usuario_id: UUID, payload: UsuarioUpdate, user: AdminUser, db: Annotated[Session, Depends(get_db)]
):
    u = db.get(Usuario, usuario_id)
    if not u:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    data = payload.model_dump(exclude_unset=True)
    # Admin nao pode alterar o proprio perfil nem desativar a propria conta
    if str(u.id) == str(user.id) and ("perfil" in data or "ativo" in data):
        raise HTTPException(status_code=400, detail="Voce nao pode alterar o proprio perfil ou status ativo")
    # senha precisa ser hasheada — nao pode ir direto pro setattr
    if "senha" in data:
        u.senha_hash = hash_password(data.pop("senha"))
        u.primeiro_acesso = False  # reset de senha pelo admin cancela o primeiro_acesso
    for k, v in data.items():
        setattr(u, k, v)
    set_update_audit(u, user)
    db.commit()
    db.refresh(u)
    return u


@router.delete("/{usuario_id}", status_code=200, summary="Desativa um usuário (soft-delete via ativo=False)")
def deletar(usuario_id: UUID, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    """Desativa o usuário impedindo login. Não remove do banco (preserva auditoria)."""
    u = db.get(Usuario, usuario_id)
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if str(u.id) == str(user.id):
        raise HTTPException(status_code=400, detail="Você não pode desativar sua própria conta")
    u.ativo = False
    set_update_audit(u, user)
    db.commit()
    return {"ok": True, "re": u.re, "nome": u.nome}
