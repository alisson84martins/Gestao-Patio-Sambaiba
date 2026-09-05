"""Endpoints do cadastro central de funcionários."""
import logging
import re as _re
from datetime import date, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import exige, get_current_funcionario
from app.core.registro import normalizar_re
from app.core.security import hash_password, verify_password
from app.models.cadastro import Funcao, FuncionarioFuncao, Funcionario, UsuarioLogin
from app.models.enums import PerfilUsuarioEnum
from app.models.pessoas import Usuario
from app.routers.ocorrencias import _eh_admin
from app.schemas.cadastro import (
    FuncionarioBusca,
    FuncionarioComFuncoes,
    FuncionarioCreate,
    FuncionarioFuncaoCreate,
    FuncionarioFuncaoRead,
    FuncionarioRead,
    FuncionarioUpdate,
    TrocaSenhaRequest,
    UsuarioLoginAtivoUpdate,
    UsuarioLoginRead,
)

router = APIRouter(prefix="/funcionarios", tags=["funcionários"])
logger = logging.getLogger(__name__)

# Gate RBAC: exige escrita em "usuarios" (não mais o perfil legado ADMIN,
# que deixaria de fora quem foi criado inteiramente pelo sistema novo).
GerenciaUsuarios = Annotated[Funcionario, Depends(exige("usuarios", escrever=True))]

# Leitura em "usuarios" — gerência (GERENTE_GERAL/GERENTE_OPERACIONAL) só lê:
# entra em Cadastros e Permissões pra acompanhar, não cria/edita/exclui nada.
LeituraUsuarios = Annotated[Funcionario, Depends(exige("usuarios"))]

# Mapa de função (nova) → perfil legado (usuario.perfil, NOT NULL, 5 valores).
# Usado só para satisfazer a constraint da linha espelho em `usuario` — ver
# _criar_ou_atualizar_espelho_usuario(). Campo deprecado, sem efeito no RBAC.
_PERFIL_LEGADO_POR_FUNCAO = {
    "ADMIN": PerfilUsuarioEnum.ADMIN,
    "COORDENADOR_TRAFEGO": PerfilUsuarioEnum.COORDENADOR,
    "OPERADOR_PATIO": PerfilUsuarioEnum.OPERADOR_PATIO,
    "MECANICO": PerfilUsuarioEnum.MECANICO,
}


def _perfil_legado_de(funcao_codigo: Optional[str]) -> PerfilUsuarioEnum:
    """Deriva o usuario.perfil (legado) a partir do código da função principal."""
    return _PERFIL_LEGADO_POR_FUNCAO.get(funcao_codigo, PerfilUsuarioEnum.MOTORISTA)


def _criar_ou_atualizar_espelho_usuario(db: Session, func: Funcionario, senha_hash: str) -> None:
    """Mantém uma linha espelho em `usuario` para quem ganha login pelo fluxo novo.

    ⚠️ SHIM TRANSITÓRIO — existe porque get_current_user (app/core/deps.py)
    ainda cai no JWT novo → Funcionario → busca Usuario pelo RE; sem essa
    linha, todo endpoint que usa CurrentUser (os 14 routers do Pátio) nega
    401 pra quem foi cadastrado só em funcionario + usuario_login. Ver
    database/migrations/014-espelho-usuario-transicao.sql para o backfill
    de quem já tinha login antes deste fix.
    Sai quando os 14 routers legados passarem a usar `exige()`/Funcionario
    diretamente e a tabela `usuario` for aposentada.
    """
    funcao_principal = db.execute(
        select(Funcao.codigo)
        .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
        .where(FuncionarioFuncao.funcionario_id == func.id, FuncionarioFuncao.principal.is_(True))
    ).scalar_one_or_none()
    perfil_legado = _perfil_legado_de(funcao_principal)

    espelho = db.execute(select(Usuario).where(Usuario.re == func.re)).scalar_one_or_none()
    if espelho is None:
        db.add(Usuario(
            re=func.re,
            nome=func.nome,
            senha_hash=senha_hash,
            perfil=perfil_legado,
            ativo=True,
            cpf=func.cpf,
        ))
    else:
        espelho.senha_hash = senha_hash
        espelho.ativo = True
        espelho.nome = func.nome


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
    _: LeituraUsuarios,
    re: Optional[str] = Query(None, max_length=20),
    cpf: Optional[str] = Query(None, description="CPF com ou sem formatação"),
    db: Annotated[Session, Depends(get_db)] = None,
) -> Optional[dict]:
    """Retorna dados do conflito se RE ou CPF já existirem; null se estiver limpo."""
    if not re and not cpf:
        return None

    if re:
        existente = db.execute(
            select(Funcionario).where(Funcionario.re == normalizar_re(re))
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


# ─── BUSCA (autocomplete — deve vir ANTES de /{funcionario_id} no router) ─────
# Gate mais permissivo que o resto do arquivo de propósito: quem registra
# ocorrência (Coordenador de Tráfego, Encarregado) tem escrita em "ocorrencia"
# mas nem sempre em "usuarios" — sem isso, autocompletar condutor/cobrador
# no formulário de ocorrência ficava impossível pra metade de quem registra.

@router.get(
    "/busca",
    response_model=list[FuncionarioBusca],
    summary="Busca funcionários ativos por RE ou nome — autocomplete (ex.: condutor/cobrador em ocorrências)",
)
def buscar_funcionarios(
    _: Annotated[Funcionario, Depends(exige("ocorrencia"))],
    q: str = Query(..., min_length=2, max_length=80, description="Trecho do RE ou do nome"),
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[FuncionarioBusca]:
    termo = f"%{q.strip()}%"
    funcionarios = db.execute(
        select(Funcionario)
        .options(joinedload(Funcionario.vinculos).joinedload(FuncionarioFuncao.funcao))
        .where(
            Funcionario.status == "ATIVO",
            (Funcionario.re.ilike(termo) | Funcionario.nome.ilike(termo)),
        )
        .order_by(Funcionario.nome)
        .limit(20)
    ).unique().scalars().all()

    return [
        FuncionarioBusca(
            re=f.re,
            nome=f.nome,
            funcoes=[v.funcao.nome for v in f.vinculos if v.ativo],
        )
        for f in funcionarios
    ]


# ─── LISTAGEM ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[FuncionarioComFuncoes], summary="Lista funcionários com funções e status de login")
def listar_funcionarios(
    _: LeituraUsuarios,
    busca: Optional[str] = Query(None, max_length=80),
    status_filtro: Optional[str] = Query(None, alias="status"),
    funcao: Optional[str] = Query(None, description="Código da função"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[FuncionarioComFuncoes]:
    q = select(Funcionario).options(
        joinedload(Funcionario.vinculos).joinedload(FuncionarioFuncao.funcao),
        joinedload(Funcionario.login),
    )
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
    funcionarios = db.execute(q).unique().scalars().all()
    return [
        FuncionarioComFuncoes(
            **FuncionarioRead.model_validate(f).model_dump(),
            vinculos=[FuncionarioFuncaoRead.model_validate(v) for v in f.vinculos],
            tem_login=f.login is not None,
        )
        for f in funcionarios
    ]


# ─── DETALHE ──────────────────────────────────────────────────────────────────

@router.get(
    "/{funcionario_id}",
    response_model=FuncionarioComFuncoes,
    summary="Retorna funcionário com funções e status de login",
)
def detalhar_funcionario(
    funcionario_id: UUID,
    _: LeituraUsuarios,
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
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
):
    # Trava de duplicata por RE — dados.re já normalizado pelo schema.
    existente_re = db.execute(
        select(Funcionario).where(Funcionario.re == dados.re)
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
    usuario: GerenciaUsuarios,
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

    # C1 (PROMPT-RE-ALFANUMERICO): troca de RE tratada à parte, ANTES do
    # laço genérico de setattr. core/deps.py:87 resolve o espelho `usuario`
    # PELO RE — se ele ficar pra trás do funcionario.re, a pessoa continua
    # logando (login novo vai por funcionario_id) mas todos os routers
    # legados do Pátio passam a devolver 401, porque acham o funcionário
    # mas não acham mais o espelho pelo RE antigo.
    re_antigo = func.re
    troca_re = dados.re is not None and dados.re != re_antigo
    espelho = None

    if troca_re:
        conflito_re = db.execute(
            select(Funcionario).where(
                Funcionario.re == dados.re, Funcionario.id != funcionario_id
            )
        ).scalar_one_or_none()
        if conflito_re:
            return JSONResponse(
                status_code=409,
                content={
                    "erro": "Já existe cadastro com este RE",
                    "status_code": 409,
                    "conflito": {
                        "campo": "re",
                        "funcionario_id": str(conflito_re.id),
                        "re": conflito_re.re,
                        "nome": conflito_re.nome,
                    },
                },
            )

        # UNIQUE de usuario.re estouraria como 500 no meio da transação sem
        # esta checagem — inclui o caso raro de um espelho órfão (RE em
        # `usuario` sem `funcionario` correspondente).
        conflito_usuario = db.execute(
            select(Usuario).where(Usuario.re == dados.re)
        ).scalar_one_or_none()
        if conflito_usuario:
            return JSONResponse(
                status_code=409,
                content={
                    "erro": "Já existe usuário com este RE",
                    "status_code": 409,
                    "conflito": {
                        "campo": "re",
                        "funcionario_id": None,
                        "re": conflito_usuario.re,
                        "nome": conflito_usuario.nome,
                    },
                },
            )

        # Localiza o espelho PELO RE ANTIGO, antes de mudar qualquer coisa.
        espelho = db.execute(
            select(Usuario).where(Usuario.re == re_antigo)
        ).scalar_one_or_none()

    for campo, valor in dados.model_dump(exclude_unset=True, exclude={"re"}).items():
        setattr(func, campo, valor)

    if troca_re:
        func.re = dados.re
        if espelho is not None:
            espelho.re = dados.re
        logger.warning(
            "RE alterado: funcionario_id=%s re_antigo=%s re_novo=%s alterado_por=%s",
            funcionario_id, re_antigo, dados.re, usuario.id,
        )

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
    usuario: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> FuncionarioFuncao:
    func = db.get(Funcionario, funcionario_id)
    if func is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    funcao = db.get(Funcao, dados.funcao_id)
    if funcao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Função não encontrada")

    # SEV-01: ter escrita em "usuarios" não basta para conceder ADMIN — só
    # ADMIN atribui a função ADMIN. Sem esta trava, o gate de recurso sozinho
    # deixava qualquer COORDENADOR_TRAFEGO se autopromover numa chamada.
    # Comparação pelo CÓDIGO da função (nunca nome/posição) e pelo RBAC
    # (funcionario_funcao via _eh_admin), nunca pelo campo legado
    # usuario.perfil — mesmo padrão já usado em ocorrencias.py.
    if funcao.codigo == "ADMIN" and not _eh_admin(db, usuario.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Só ADMIN pode conceder a função ADMIN",
        )

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
    _: GerenciaUsuarios,
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
    _: GerenciaUsuarios,
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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="CPF não cadastrado; cadastre-o antes de criar o login",
        )

    senha_hash = hash_password(func.cpf[-4:])
    login = UsuarioLogin(
        funcionario_id=funcionario_id,
        senha_hash=senha_hash,
        politica_senha="CPF",
    )
    db.add(login)
    # Shim transitório — ver _criar_ou_atualizar_espelho_usuario(). Sem isso,
    # quem só existe em funcionario + usuario_login toma 401 em todo endpoint
    # dos 14 routers legados do Pátio (eles autenticam via `usuario`, não Funcionario).
    _criar_ou_atualizar_espelho_usuario(db, func, senha_hash)
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
    _: GerenciaUsuarios,
    db: Annotated[Session, Depends(get_db)] = None,
) -> UsuarioLogin:
    login = db.execute(
        select(UsuarioLogin).where(UsuarioLogin.funcionario_id == funcionario_id)
    ).scalar_one_or_none()
    if login is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Acesso não encontrado")

    login.ativo = dados.ativo
    login.atualizado_em = datetime.now(timezone.utc)

    # SEV-05: propaga para o espelho legado. Sem isso, desativar o acesso
    # pelo fluxo novo não bloqueia os 12 routers legados do Pátio, que
    # autenticam só pela linha em `usuario` (get_current_user) e nunca
    # consultam UsuarioLogin.
    func = db.get(Funcionario, funcionario_id)
    if func is not None:
        espelho = db.execute(select(Usuario).where(Usuario.re == func.re)).scalar_one_or_none()
        if espelho is not None:
            espelho.ativo = dados.ativo

    db.commit()
    db.refresh(login)
    return login


@router.patch(
    "/{funcionario_id}/login/senha",
    response_model=UsuarioLoginRead,
    summary="Troca a senha do login — autoatendimento (com senha atual) ou reset por ADMIN",
)
def trocar_senha(
    funcionario_id: UUID,
    dados: TrocaSenhaRequest,
    usuario: Annotated[Funcionario, Depends(get_current_funcionario)],
    db: Annotated[Session, Depends(get_db)] = None,
) -> UsuarioLogin:
    """SEV-03: até este endpoint existir, UsuarioLogin.senha_hash era
    escrito uma única vez (em criar_login()) e nunca mais — nem a própria
    pessoa, nem o ADMIN, nem um script de manutenção conseguiam trocar a
    senha de alguém do fluxo novo pela aplicação.

    ⛔ Ninguém além do próprio dono e do ADMIN — nem coordenador, nem
    encarregado, nem gerência. Não é um recurso RBAC (não usa exige()):
    é checagem de identidade, resolvida aqui mesmo.
    """
    login = db.execute(
        select(UsuarioLogin).where(UsuarioLogin.funcionario_id == funcionario_id)
    ).scalar_one_or_none()
    if login is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Acesso não encontrado")

    e_proprio_dono = usuario.id == funcionario_id
    e_admin = _eh_admin(db, usuario.id)

    if not e_proprio_dono and not e_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Só o próprio dono ou o ADMIN pode trocar esta senha",
        )

    # Autoatendimento (não-ADMIN trocando a própria senha) exige a atual.
    # Reset por ADMIN — inclusive o ADMIN trocando a própria — não exige.
    if e_proprio_dono and not e_admin:
        if not dados.senha_atual or not verify_password(dados.senha_atual, login.senha_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Senha atual incorreta")

    login.senha_hash = hash_password(dados.senha_nova)
    login.politica_senha = "PROPRIA"
    login.atualizado_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(login)
    return login
