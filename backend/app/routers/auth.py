"""Endpoints de autenticação: login e dados do usuário corrente."""
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import oauth2_scheme
from app.core.security import JWTError, create_access_token, decode_access_token, verify_password
from app.models import Usuario
from app.models.cadastro import Funcao, FuncionarioFuncao, Funcionario, UsuarioLogin
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.services.acesso import acesso_efetivo, destino_login, modulos_disponiveis

router = APIRouter(prefix="/auth", tags=["autenticacao"])

_LOGIN_TENTATIVAS: dict[str, list[float]] = defaultdict(list)
_RATE_MAX = 10
_RATE_JANELA = 60

# SEV-08: bloqueio por CONTA (RE), independente do IP de origem. O limite
# por IP acima não protege uma conta específica de alguém que rotacione
# IP — 10.000 combinações de senha (4 dígitos do CPF) sem nunca travar.
# Conta só FALHA (RE/senha incorretos); sucesso zera. Janela com
# expiração automática — trancar até intervenção do admin seria DoS de
# graça (erre a senha de alguém 5x e ele não trabalha).
_TENTATIVAS_POR_CONTA: dict[str, list[float]] = defaultdict(list)
_CONTA_MAX = 5
_CONTA_JANELA = 15 * 60  # 15 minutos


def _checar_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    agora = time.time()
    tentativas = [t for t in _LOGIN_TENTATIVAS[ip] if agora - t < _RATE_JANELA]
    if len(tentativas) >= _RATE_MAX:
        _LOGIN_TENTATIVAS[ip] = tentativas
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de login. Aguarde 1 minuto.",
        )
    tentativas.append(agora)
    _LOGIN_TENTATIVAS[ip] = tentativas


def _checar_bloqueio_conta(re: str) -> None:
    """Bloqueio por RE — ver nota acima de _TENTATIVAS_POR_CONTA.

    ⚠️ Mesma mensagem/status para RE inexistente e senha errada (quem
    chama já garante isso ao só registrar falha nos dois casos da mesma
    forma) — um 429 que só aparecesse para RE cadastrado viraria oráculo
    de enumeração de conta.
    """
    agora = time.time()
    tentativas = [t for t in _TENTATIVAS_POR_CONTA[re] if agora - t < _CONTA_JANELA]
    if len(tentativas) >= _CONTA_MAX:
        _TENTATIVAS_POR_CONTA[re] = tentativas
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas para este RE. Aguarde alguns minutos.",
        )
    if tentativas:
        _TENTATIVAS_POR_CONTA[re] = tentativas
    elif re in _TENTATIVAS_POR_CONTA:
        del _TENTATIVAS_POR_CONTA[re]  # poda entrada vazia — não cresce à toa


def _registrar_falha_conta(re: str) -> None:
    _TENTATIVAS_POR_CONTA[re].append(time.time())


def _zerar_falhas_conta(re: str) -> None:
    _TENTATIVAS_POR_CONTA.pop(re, None)


def _autenticar_e_gerar_token(re: str, senha: str, db: Session) -> TokenResponse:
    """Login via sistema novo (usuario_login) com fallback para o sistema antigo (usuario)."""
    settings = get_settings()
    re = re.strip()
    _checar_bloqueio_conta(re)

    # ── Sistema novo: usuario_login → funcionario ──────────────────────────
    ul = db.execute(
        select(UsuarioLogin)
        .join(Funcionario, Funcionario.id == UsuarioLogin.funcionario_id)
        .where(Funcionario.re == re)
    ).scalar_one_or_none()

    if ul is not None:
        if not verify_password(senha, ul.senha_hash):
            _registrar_falha_conta(re)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="RE ou senha incorretos",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not ul.ativo:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuário inativo. Procure o administrador.",
            )
        func = db.get(Funcionario, ul.funcionario_id)
        ul.ultimo_acesso = datetime.now(timezone.utc)
        db.commit()
        _zerar_falhas_conta(re)

        token = create_access_token(
            subject=func.id,
            extra_claims={"re": func.re, "funcionario_id": str(func.id)},
        )
        return TokenResponse(
            access_token=token,
            expires_in=settings.jwt_expire_minutes * 60,
        )

    # ── Fallback: sistema antigo (usuario) ────────────────────────────────
    user = db.execute(select(Usuario).where(Usuario.re == re)).scalar_one_or_none()
    if user is None or not verify_password(senha, user.senha_hash):
        _registrar_falha_conta(re)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="RE ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo. Procure o administrador.",
        )
    user.ultimo_acesso = datetime.now(timezone.utc)
    db.commit()
    _zerar_falhas_conta(re)

    token = create_access_token(
        subject=user.id,
        extra_claims={"re": user.re, "perfil": user.perfil.value},
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        primeiro_acesso=user.primeiro_acesso,
    )


@router.post("/login", response_model=TokenResponse, summary="Login via JSON (uso pelo frontend)")
def login_json(
    request: Request,
    credentials: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Login com `{re, senha}` em JSON."""
    _checar_rate_limit(request)
    return _autenticar_e_gerar_token(credentials.re, credentials.senha, db)


@router.post("/token", response_model=TokenResponse, summary="Login via form OAuth2 (Swagger)")
def login_oauth2(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Login no padrão OAuth2 (form-urlencoded). O campo `username` recebe o RE."""
    _checar_rate_limit(request)
    return _autenticar_e_gerar_token(form_data.username, form_data.password, db)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Dados do usuário autenticado com acesso RBAC completo",
)
def get_me(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """Retorna pessoa + funções + acesso efetivo + módulos disponíveis.

    Funciona com tokens novos (sub = funcionario.id) e antigos (sub = usuario.id).
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        sub_id = UUID(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise cred_exc

    # ── JWT novo: sub = funcionario.id ────────────────────────────────────
    func = db.get(Funcionario, sub_id)
    if func is not None:
        funcoes = list(db.execute(
            select(Funcao.codigo)
            .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(
                FuncionarioFuncao.funcionario_id == func.id,
                FuncionarioFuncao.ativo == True,
            )
        ).scalars().all())
        return MeResponse(
            id=func.id,
            re=func.re,
            nome=func.nome,
            funcionario_id=func.id,
            funcoes=funcoes,
            acesso=acesso_efetivo(db, func.id),
            modulos=modulos_disponiveis(db, func.id),
            modulo_padrao=destino_login(db, func.id),
        )

    # ── JWT antigo: sub = usuario.id ─────────────────────────────────────
    user = db.get(Usuario, sub_id)
    if user is None or not user.ativo:
        raise cred_exc

    # Enriquece com RBAC se a pessoa estiver no sistema novo
    func_by_re = db.execute(
        select(Funcionario).where(Funcionario.re == user.re)
    ).scalar_one_or_none()

    funcoes: list[str] = []
    acesso: dict = {}
    modulos: list = []
    modulo_padrao = None
    func_id = None

    if func_by_re is not None:
        func_id = func_by_re.id
        funcoes = list(db.execute(
            select(Funcao.codigo)
            .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(
                FuncionarioFuncao.funcionario_id == func_by_re.id,
                FuncionarioFuncao.ativo == True,
            )
        ).scalars().all())
        acesso = acesso_efetivo(db, func_by_re.id)
        modulos = modulos_disponiveis(db, func_by_re.id)
        modulo_padrao = destino_login(db, func_by_re.id)

    return MeResponse(
        id=user.id,
        re=user.re,
        nome=user.nome,
        funcionario_id=func_id,
        funcoes=funcoes,
        acesso=acesso,
        modulos=modulos,
        modulo_padrao=modulo_padrao,
        perfil=user.perfil.value,
        primeiro_acesso=user.primeiro_acesso,
    )
