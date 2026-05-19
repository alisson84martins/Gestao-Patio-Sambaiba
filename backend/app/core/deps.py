"""Dependencies de autenticação para os endpoints."""
from typing import Annotated, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import JWTError, decode_access_token
from app.models import Usuario

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=True)


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Usuario:
    """Decodifica o JWT do header Authorization e retorna o usuário do banco."""
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise cred_exc
        user_id = UUID(sub)
    except (JWTError, ValueError):
        raise cred_exc

    user = db.get(Usuario, user_id)
    if user is None or not user.ativo:
        raise cred_exc
    return user


def require_admin(
    user: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    """Garante que o usuário tem perfil ADMIN."""
    from app.models.enums import PerfilUsuarioEnum
    if user.perfil != PerfilUsuarioEnum.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas administradores podem realizar esta operação",
        )
    return user


def require_perfis(*perfis) -> Callable:
    """Factory de dependency: exige que o usuário tenha um dos perfis informados.

    Uso:
        OperadorOuAdmin = Annotated[Usuario, Depends(require_perfis(
            PerfilUsuarioEnum.OPERADOR_PATIO, PerfilUsuarioEnum.ADMIN
        ))]
    """
    def checker(user: Annotated[Usuario, Depends(get_current_user)]) -> Usuario:
        from app.models.enums import PerfilUsuarioEnum
        if user.perfil not in perfis:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Perfil sem permissão para esta operação",
            )
        return user
    return checker


CurrentUser = Annotated[Usuario, Depends(get_current_user)]
AdminUser = Annotated[Usuario, Depends(require_admin)]

# Escrita no pátio: operadores e admins; motoristas só leem
def _require_operador_ou_admin(
    user: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    from app.models.enums import PerfilUsuarioEnum
    permitidos = {PerfilUsuarioEnum.OPERADOR_PATIO, PerfilUsuarioEnum.ADMIN}
    if user.perfil not in permitidos:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas Operadores de Pátio e Administradores podem realizar esta operação",
        )
    return user


OperadorOuAdmin = Annotated[Usuario, Depends(_require_operador_ou_admin)]
