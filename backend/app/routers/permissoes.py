"""Endpoints de permissões: pacote por função e override por funcionário."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcao, FuncaoPermissao, Funcionario
from app.models.catalogos import Permissao
from app.schemas.cadastro import PermissaoRecurso, PermissaoRecursoEfetivo

router = APIRouter(prefix="/permissoes", tags=["permissões"])

# Gate RBAC: exige escrita em "usuarios" (não mais o perfil legado ADMIN,
# que deixaria de fora quem foi criado inteiramente pelo sistema novo).
GerenciaUsuarios = Annotated[Funcionario, Depends(exige("usuarios", escrever=True))]


# ─── PACOTE PADRÃO DA FUNÇÃO ─────────────────────────────────────────────────

@router.get(
    "/funcao/{codigo}",
    response_model=list[PermissaoRecurso],
    summary="Retorna o pacote de permissões da função",
)
def listar_permissoes_funcao(
    codigo: str,
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[FuncaoPermissao]:
    funcao = db.execute(
        select(Funcao).where(Funcao.codigo == codigo.upper())
    ).scalar_one_or_none()
    if funcao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Função não encontrada")

    return db.execute(
        select(FuncaoPermissao)
        .where(FuncaoPermissao.funcao_id == funcao.id)
        .order_by(FuncaoPermissao.recurso)
    ).scalars().all()


@router.put(
    "/funcao/{codigo}",
    response_model=list[PermissaoRecurso],
    summary="Substitui o pacote de permissões da função (operação completa)",
)
def atualizar_permissoes_funcao(
    codigo: str,
    permissoes: list[PermissaoRecurso],
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[FuncaoPermissao]:
    funcao = db.execute(
        select(Funcao).where(Funcao.codigo == codigo.upper())
    ).scalar_one_or_none()
    if funcao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Função não encontrada")

    # Substitui todo o pacote
    db.execute(delete(FuncaoPermissao).where(FuncaoPermissao.funcao_id == funcao.id))
    for item in permissoes:
        db.add(FuncaoPermissao(
            funcao_id=funcao.id,
            recurso=item.recurso,
            pode_ler=item.pode_ler,
            pode_escrever=item.pode_escrever,
        ))
    db.commit()

    return db.execute(
        select(FuncaoPermissao)
        .where(FuncaoPermissao.funcao_id == funcao.id)
        .order_by(FuncaoPermissao.recurso)
    ).scalars().all()


# ─── OVERRIDE INDIVIDUAL POR FUNCIONÁRIO ─────────────────────────────────────

@router.get(
    "/funcionario/{funcionario_id}",
    response_model=list[PermissaoRecursoEfetivo],
    summary="Retorna o acesso efetivo da pessoa (função + exceções individuais)",
)
def listar_acesso_funcionario(
    funcionario_id: UUID,
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[dict]:
    if db.get(Funcionario, funcionario_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    rows = db.execute(
        text(
            "SELECT recurso, pode_ler, pode_escrever, e_excecao "
            "FROM vw_acesso_efetivo "
            "WHERE funcionario_id = :fid "
            "ORDER BY recurso"
        ),
        {"fid": funcionario_id},
    ).fetchall()

    return [
        PermissaoRecursoEfetivo(
            recurso=r.recurso,
            pode_ler=r.pode_ler,
            pode_escrever=r.pode_escrever,
            e_excecao=r.e_excecao,
        )
        for r in rows
    ]


@router.put(
    "/funcionario/{funcionario_id}",
    response_model=list[PermissaoRecursoEfetivo],
    summary="Substitui as exceções individuais do funcionário",
)
def atualizar_acesso_funcionario(
    funcionario_id: UUID,
    permissoes: list[PermissaoRecurso],
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[dict]:
    func = db.get(Funcionario, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    # Remove todos os overrides atuais do funcionário
    db.execute(
        delete(Permissao).where(Permissao.funcionario_id == funcionario_id)
    )
    for item in permissoes:
        db.add(Permissao(
            funcionario_id=funcionario_id,
            recurso=item.recurso,
            pode_ler=item.pode_ler,
            pode_escrever=item.pode_escrever,
        ))
    db.commit()

    # Retorna acesso efetivo completo (herdado + novos overrides)
    rows = db.execute(
        text(
            "SELECT recurso, pode_ler, pode_escrever, e_excecao "
            "FROM vw_acesso_efetivo "
            "WHERE funcionario_id = :fid "
            "ORDER BY recurso"
        ),
        {"fid": funcionario_id},
    ).fetchall()

    return [
        PermissaoRecursoEfetivo(
            recurso=r.recurso,
            pode_ler=r.pode_ler,
            pode_escrever=r.pode_escrever,
            e_excecao=r.e_excecao,
        )
        for r in rows
    ]
