"""Schemas Pydantic para autenticação."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.registro import ReNormalizadoObrigatorio
from app.models.enums import PerfilUsuarioEnum
from app.schemas.cadastro import ModuloDisponivel


class LoginRequest(BaseModel):
    """Credenciais para o endpoint /auth/login."""
    re: ReNormalizadoObrigatorio = Field(..., min_length=1, max_length=20, description="Registro de funcionário")
    senha: str = Field(..., min_length=1, max_length=72)


class TokenResponse(BaseModel):
    """Resposta do endpoint /auth/login."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Tempo de vida em segundos")
    primeiro_acesso: bool = False
    # Login PROVISORIA (criado pelo sistema — app/services/acesso_motorista.py):
    # antes de qualquer módulo, a pessoa confirma "Você é NOME, RE X?" em
    # POST /auth/confirmar-identidade. Logins CPF/FORTE/PROPRIA: sempre false.
    confirmar_identidade: bool = False
    nome: Optional[str] = None
    re: Optional[str] = None


class ConfirmarIdentidadeRequest(BaseModel):
    """POST /auth/confirmar-identidade. sou_eu=true exige senha_nova
    (mínimo 6, diferente dos 4 últimos do CPF); sou_eu=false desativa o
    login."""
    sou_eu: bool
    senha_nova: Optional[str] = Field(None, max_length=72)


class ConfirmarIdentidadeResponse(BaseModel):
    situacao: str = Field(..., description="CONFIRMADA ou NEGADA")


class TokenPayload(BaseModel):
    """Conteúdo decodificado do JWT."""
    sub: UUID
    exp: int
    iat: Optional[int] = None
    perfil: Optional[PerfilUsuarioEnum] = None
    re: Optional[str] = None


class MeResponse(BaseModel):
    """Resposta de GET /auth/me — dados da pessoa + acesso RBAC completo."""
    # Identificadores
    id: UUID
    re: str
    nome: str
    # Sistema novo (populated when logged in via usuario_login)
    funcionario_id: Optional[UUID] = None
    funcoes: list[str] = Field(default_factory=list, description="Códigos das funções ativas")
    acesso: dict[str, dict[str, bool]] = Field(
        default_factory=dict,
        description='Acesso efetivo: {"alocacao": {"ler": true, "escrever": false}, ...}',
    )
    modulos: list[ModuloDisponivel] = Field(default_factory=list)
    modulo_padrao: Optional[str] = Field(
        None, description="Módulo de destino do login (vw_destino_login). Nulo = tela de escolha."
    )
    # Sistema antigo (populated when logged in via usuario — transição)
    perfil: Optional[str] = None
    primeiro_acesso: bool = False
