"""Schemas Pydantic para autenticação."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import PerfilUsuarioEnum


class LoginRequest(BaseModel):
    """Credenciais para o endpoint /auth/login."""
    re: str = Field(..., max_length=20, description="Registro de funcionário")
    senha: str = Field(..., min_length=1, max_length=72)


class TokenResponse(BaseModel):
    """Resposta do endpoint /auth/login."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Tempo de vida em segundos")
    # Se TRUE, o frontend deve redirecionar para a tela de troca de senha
    # antes de liberar o acesso ao sistema.
    primeiro_acesso: bool = False


class TokenPayload(BaseModel):
    """Conteúdo decodificado do JWT."""
    sub: UUID
    exp: int
    iat: Optional[int] = None
    perfil: Optional[PerfilUsuarioEnum] = None
    re: Optional[str] = None
