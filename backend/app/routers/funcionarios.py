"""Endpoints do cadastro central de funcionários."""
import re as _re
from datetime import date, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.core.security import hash_password
from app.models.cadastro import Funcao, FuncionarioFuncao, Funcionario, UsuarioLogin
from app.schemas.cadastro import (
    FuncionarioComFuncoes,
    FuncionarioCreate,
    FuncionarioFuncaoCreate,
    FuncionarioFuncaoRead,
    FuncionarioRead,
    FuncionarioUpdate,
    UsuarioLoginAtivoUpdate,
    UsuarioLoginRead,
)

router = APIRouter(prefix="/funcionarios", tags=["funcionários"])


def _carregar_com_vinculos(db: Session, funcionario_id: UUID) -> Optional[Funcionario]:
    """Carrega Funcionario com vinculos (+ funcao) e login em uma query."""
    return db.execute(
        select(Funcionario)
        .options(
            joinedload(Funcionario.vinculos).joinedload(FuncionarioFuncao.funcao),
            joinedload(Funcionario.login),
        )
        .where(Funcionario.id == funcionario_id)
    ).unique().scalar_one_or_none()


# ─── VERIFICAÇÃO PRÉVIA (deve vir ANTES de /{funcionario_id} no router) ───────

@router.get(
    "/verificar",
    response_model=Optional[dict],
    summary="Verifica se RE ou CPF já existe antes de criar",
)
def verificar_funcionario(
    _: AdminUser,
    re: Optional[str] = Query(None, max_length=20),
    cpf: Optional[str] = Query(None, description="CPF com ou sem formatação"),
    db: Annotated[Session, Depends(get_db)] = None,
) -> Optional[dict]:
    """Retorna dados do conflito se RE ou CPF já existirem; null se estiver limpo."""
    if not re and not cpf:
        return None

    if re:
        existente = db.execute(
            select(Funcionario).where(Funcionario.re == re.strip())
        ).scalar_one_or_none()
        if existente:
            return {
                "campo": "re",
                "funcionario_id": str(existente.id),
                "re": existente.re,
                "nome": existente.nome,
            }

    if cpf:
        cpf_norm = _re.sub(r"\D", "", cpf)
        existente = db.execute(
            select(Funcionario).where(Funcionario.cpf == cpf_norm)
        ).scalar_one_or_none()
        if existente:
            return {
                "campo": "cpf",
                "funcionario_id": str(existente.id),
                "re": existente.re,
                "nome": existente.nome,
            }

    return None


# ─── LISTAGEM ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[FuncionarioRead], summary="Lista funcionários")
def listar_funcionarios(
    _: AdminUser,
    busca: Optional[str] = Query(None, max_length=80),
    status_filtro: Optional[str] = Query(None, alias="status"),
    funcao: Optional[str] = Query(None, description="Código da função"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[Funcionario]:
    q = select(Funcionario)
    if busca:
        termo = f"%{busca}%"
        q = q.where(Funcionario.nome.ilike(termo) | Funcionario.re.ilike(termo))
    if status_filtro:
        q = q.where(Funcionario.status == status_filtro.upper())
    if funcao:
        q = (
            q.join(FuncionarioFuncao, FuncionarioFuncao.funcionario_id == Funcionario.id)
            .join(Funcao, Funcao.id == FuncionarioFuncao.funcao_id)
            .where(Funcao.codigo == funcao.upper(), FuncionarioFuncao.ativo == True)
        )
    q = q.order_by(Funcionario.nome).offset(skip).limit(limit)
    return db.execute(q).scalars().all()


# ─── DETALHE ──────────────────────────────────────────────────────────────────

@router.get(
    "/{funcionario_id}",
    response_model=FuncionarioComFuncoes,
    summary="Retorna funcionário com funções e status de login",
)
def detalhar_funcionario(
    funcionario_id: UUID,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> FuncionarioComFuncoes:
    func = _carregar_com_vinculos(db, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    vinculos_lidos = [FuncionarioFuncaoRead.model_validate(v) for v in func.vinculos]
    return FuncionarioComFuncoes(
        **FuncionarioRead.model_validate(func).model_dump(),
        vinculos=vinculos_lidos,
        tem_login=func.login is not None,
    )


# ─── CRIAÇÃO ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=FuncionarioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria funcionário; 409 se RE ou CPF já existir",
)
def criar_funcionario(
    dados: FuncionarioCreate,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
):
    # Trava de duplicata por RE
    existente_re = db.execute(
        select(Funcionario).where(Funcionario.re == dados.re.strip())
    ).scalar_one_or_none()
    if existente_re:
        return JSONResponse(
            status_code=409,
            content={
                "erro": "Já existe cadastro com este RE",
                "status_code": 409,
                "conflito": {
                    "campo": "re",
                    "funcionario_id": str(existente_re.id),
                    "re": existente_re.re,
                    "nome": existente_re.nome,
                },
            },
        )

    # Trava de duplicata por CPF
    if dados.cpf:
        existente_cpf = db.execute(
            select(Funcionario).where(Funcionario.cpf == dados.cpf)
        ).scalar_one_or_none()
        if existente_cpf:
            return JSONResponse(
                status_code=409,
                content={
                    "erro": "Já existe cadastro com este CPF",
                    "status_code": 409,
                    "conflito": {
                        "campo": "cpf",
                        "funcionario_id": str(existente_cpf.id),
                        "re": existente_cpf.re,
                        "nome": existente_cpf.nome,
                    },
                },
            )

    novo = Funcionario(**dados.model_dump())
    db.add(novo)
    db.commit()
    db.refresh(novo)
    return novo


# ─── EDIÇÃO ───────────────────────────────────────────────────────────────────

@router.patch(
    "/{funcionario_id}",
    response_model=FuncionarioRead,
    summary="Atualiza dados do funcionário",
)
def atualizar_funcionario(
    funcionario_id: UUID,
    dados: FuncionarioUpdate,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
):
    func = db.get(Funcionario, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    if dados.cpf and dados.cpf != func.cpf:
        conflito = db.execute(
            select(Funcionario).where(
                Funcionario.cpf == dados.cpf, Funcionario.id != funcionario_id
            )
        ).scalar_one_or_none()
        if conflito:
            return JSONResponse(
                status_code=409,
                content={
                    "erro": "Já existe cadastro com este CPF",
                    "status_code": 409,
                    "conflito": {
                        "campo": "cpf",
                        "funcionario_id": str(conflito.id),
                        "re": conflito.re,
                        "nome": conflito.nome,
                    },
                },
            )

    for campo, valor in dados.model_dump(exclude_unset=True).items():
        setattr(func, campo, valor)

    func.atualizado_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(func)
    return func


# ─── FUNÇÕES (vínculos) ───────────────────────────────────────────────────────

@router.post(
    "/{funcionario_id}/funcoes",
    response_model=FuncionarioFuncaoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Atribui uma função ao funcionário",
)
def atribuir_funcao(
    funcionario_id: UUID,
    dados: FuncionarioFuncaoCreate,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> FuncionarioFuncao:
    func = db.get(Funcionario, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    funcao = db.get(Funcao, dados.funcao_id)
    if funcao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Função não encontrada")

    existente = db.execute(
        select(FuncionarioFuncao).where(
            FuncionarioFuncao.funcionario_id == funcionario_id,
            FuncionarioFuncao.funcao_id == dados.funcao_id,
            FuncionarioFuncao.ativo == True,
        )
    ).scalar_one_or_none()
    if existente:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Funcionário já possui esta função ativa")

    vinculo = FuncionarioFuncao(
        funcionario_id=funcionario_id,
        funcao_id=dados.funcao_id,
        principal=dados.principal,
        data_inicio=dados.data_inicio or date.today(),
    )
    db.add(vinculo)
    db.flush()
    db.execute(text("SELECT fn_ajustar_funcao_principal(:fid)"), {"fid": funcionario_id})
    db.commit()

    return db.execute(
        select(FuncionarioFuncao)
        .options(joinedload(FuncionarioFuncao.funcao))
        .where(FuncionarioFuncao.id == vinculo.id)
    ).unique().scalar_one()


@router.delete(
    "/{funcionario_id}/funcoes/{funcao_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Encerra vínculo de função (soft — define data_fim e ativo=false)",
)
def encerrar_funcao(
    funcionario_id: UUID,
    funcao_id: UUID,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> None:
    vinculo = db.execute(
        select(FuncionarioFuncao).where(
            FuncionarioFuncao.funcionario_id == funcionario_id,
            FuncionarioFuncao.funcao_id == funcao_id,
            FuncionarioFuncao.ativo == True,
        )
    ).scalar_one_or_none()
    if vinculo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vínculo ativo não encontrado")

    vinculo.ativo = False
    vinculo.data_fim = date.today()
    db.flush()
    db.execute(text("SELECT fn_ajustar_funcao_principal(:fid)"), {"fid": funcionario_id})
    db.commit()


# ─── LOGIN ────────────────────────────────────────────────────────────────────

@router.post(
    "/{funcionario_id}/login",
    response_model=UsuarioLoginRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria acesso ao sistema; senha = 4 últimos dígitos do CPF",
)
def criar_login(
    funcionario_id: UUID,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> UsuarioLogin:
    func = db.get(Funcionario, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    if db.execute(
        select(UsuarioLogin).where(UsuarioLogin.funcionario_id == funcionario_id)
    ).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Funcionário já possui acesso ao sistema")

    if not func.cpf or len(func.cpf) < 4:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CPF não cadastrado; cadastre-o antes de criar o login",
        )

    login = UsuarioLogin(
        funcionario_id=funcionario_id,
        senha_hash=hash_password(func.cpf[-4:]),
        politica_senha="CPF",
    )
    db.add(login)
    db.commit()
    db.refresh(login)
    return login


@router.patch(
    "/{funcionario_id}/login",
    response_model=UsuarioLoginRead,
    summary="Ativa ou desativa o acesso de um funcionário",
)
def atualizar_login(
    funcionario_id: UUID,
    dados: UsuarioLoginAtivoUpdate,
    _: AdminUser,
    db: Annotated[Session, Depends(get_db)] = None,
) -> UsuarioLogin:
    login = db.execute(
        select(UsuarioLogin).where(UsuarioLogin.funcionario_id == funcionario_id)
    ).scalar_one_or_none()
    if login is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Acesso não encontrado")

    login.ativo = dados.ativo
    login.atualizado_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(login)
    return login
