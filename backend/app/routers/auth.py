"""Endpoints de autenticação: login e dados do usuário corrente."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.security import create_access_token, verify_password
from app.models import Usuario
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.pessoas import UsuarioRead

router = APIRouter(prefix="/auth", tags=["autenticação"])


def _autenticar_e_gerar_token(re: str, senha: str, db: Session) -> TokenResponse:
    """Lógica compartilhada entre /auth/login (JSON) e /auth/token (form OAuth2)."""
    settings = get_settings()
    user = db.execute(select(Usuario).where(Usuario.re == re)).scalar_one_or_none()

    if user is None or not verify_password(senha, user.senha_hash):
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

    token = create_access_token(
        subject=user.id,
        extra_claims={"re": user.re, "perfil": user.perfil.value},
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login via JSON (uso pelo frontend)",
)
def login_json(
    credentials: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Login com `{re, senha}` em JSON. Usado pelo frontend."""
    return _autenticar_e_gerar_token(credentials.re, credentials.senha, db)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Login via form OAuth2 (uso pelo Swagger Authorize)",
)
def login_oauth2(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Login no padrão OAuth2 (form-urlencoded).

    O campo `username` recebe o **RE** do funcionário.
    """
    return _autenticar_e_gerar_token(form_data.username, form_data.password, db)


@router.get(
    "/me",
    response_model=UsuarioRead,
    summary="Retorna dados do usuário autenticado",
)
def get_me(user: CurrentUser) -> Usuario:
    return user
