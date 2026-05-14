"""Funções de segurança: hash de senha (bcrypt) e tokens JWT."""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# Contexto bcrypt (12 rounds — equilíbrio entre segurança e performance)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """Gera hash bcrypt da senha em texto plano."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto plano bate com o hash armazenado."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: str | UUID,
    extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Gera JWT assinado com a SECRET_KEY do .env."""
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica e valida JWT. Lança JWTError se inválido/expirado."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


__all__ = ["hash_password", "verify_password", "create_access_token", "decode_access_token", "JWTError"]
