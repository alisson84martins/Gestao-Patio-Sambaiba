"""Schemas Pydantic v2 do cadastro central."""
import re as _re
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── MODULO ──────────────────────────────────────────────────────────────────

class ModuloRead(ORMBase):
    codigo: str
    nome: str
    descricao: Optional[str] = None
    ordem: int


# ─── RECURSO ─────────────────────────────────────────────────────────────────

class RecursoRead(ORMBase):
    codigo: str
    nome: str
    descricao: Optional[str] = None
    modulo_codigo: Optional[str] = None
    ordem: int


# ─── FUNCAO ──────────────────────────────────────────────────────────────────

class FuncaoRead(ORMBase):
    id: UUID
    codigo: str
    nome: str
    categoria: str
    nivel: int
    ativo: bool
    descricao: Optional[str] = None


# ─── FUNCIONARIO ─────────────────────────────────────────────────────────────

class FuncionarioCreate(BaseModel):
    re: str = Field(..., max_length=20)
    nome: str = Field(..., max_length=120)
    cpf: Optional[str] = Field(None, description="CPF com ou sem formatação; normalizado para 11 dígitos.")
    telefone: Optional[str] = Field(None, max_length=20)
    rg: Optional[str] = Field(None, max_length=20)
    cnh: Optional[str] = Field(None, max_length=20)
    status: str = Field("ATIVO", pattern="^(ATIVO|AFASTADO|FERIAS|DESLIGADO)$")
    data_admissao: Optional[date] = None
    codigo_externo: Optional[str] = Field(None, max_length=50)

    @field_validator("cpf", mode="before")
    @classmethod
    def normalizar_cpf(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        digits = _re.sub(r"\D", "", v)
        if digits and len(digits) != 11:
            raise ValueError("CPF deve ter 11 dígitos numéricos")
        return digits or None


class FuncionarioUpdate(BaseModel):
    nome: Optional[str] = Field(None, max_length=120)
    cpf: Optional[str] = None
    telefone: Optional[str] = Field(None, max_length=20)
    rg: Optional[str] = Field(None, max_length=20)
    cnh: Optional[str] = Field(None, max_length=20)
    status: Optional[str] = Field(None, pattern="^(ATIVO|AFASTADO|FERIAS|DESLIGADO)$")
    data_admissao: Optional[date] = None
    codigo_externo: Optional[str] = Field(None, max_length=50)

    @field_validator("cpf", mode="before")
    @classmethod
    def normalizar_cpf(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        digits = _re.sub(r"\D", "", v)
        if digits and len(digits) != 11:
            raise ValueError("CPF deve ter 11 dígitos numéricos")
        return digits or None


class FuncionarioRead(ORMBase):
    id: UUID
    re: str
    nome: str
    cpf: Optional[str] = None
    telefone: Optional[str] = None
    rg: Optional[str] = None
    cnh: Optional[str] = None
    status: str
    data_admissao: Optional[date] = None
    codigo_externo: Optional[str] = None
    criado_em: datetime


# ─── FUNCIONARIO_FUNCAO ───────────────────────────────────────────────────────

class FuncionarioFuncaoCreate(BaseModel):
    funcao_id: UUID
    principal: bool = False
    data_inicio: Optional[date] = None


class FuncionarioFuncaoRead(ORMBase):
    id: UUID
    funcao: FuncaoRead
    principal: bool
    data_inicio: date
    data_fim: Optional[date] = None
    ativo: bool


# ─── FUNCIONARIO COMPLETO (com funções e status de login) ────────────────────

class FuncionarioComFuncoes(FuncionarioRead):
    vinculos: list[FuncionarioFuncaoRead] = []
    tem_login: bool = False


# ─── BUSCA (autocomplete — ex.: condutor/cobrador no módulo Ocorrências) ──────

class FuncionarioBusca(BaseModel):
    """Resultado de busca para autocomplete. NUNCA inclui cpf, rg, cnh ou telefone."""
    re: str
    nome: str
    funcoes: list[str] = []


# ─── USUARIO_LOGIN ────────────────────────────────────────────────────────────

class UsuarioLoginRead(ORMBase):
    id: UUID
    ativo: bool
    politica_senha: str
    ultimo_acesso: Optional[datetime] = None


class UsuarioLoginAtivoUpdate(BaseModel):
    ativo: bool


class TrocaSenhaRequest(BaseModel):
    """SEV-03: troca de senha de um login do fluxo novo.

    `senha_atual` é exigida no autoatendimento (a própria pessoa trocando a
    própria senha) e ignorada quando quem chama é ADMIN resetando a de
    outra pessoa — verificado no router, não aqui, porque depende de quem
    está autenticado.
    """
    senha_atual: Optional[str] = Field(None, description="Exigida só no autoatendimento")
    senha_nova: str = Field(..., min_length=6, max_length=100)


# ─── FUNCAO_PERMISSAO ────────────────────────────────────────────────────────

class PermissaoRecurso(BaseModel):
    """Um par recurso × acesso, usado tanto em leitura quanto em escrita."""
    recurso: str
    pode_ler: bool
    pode_escrever: bool


class PermissaoRecursoEfetivo(PermissaoRecurso):
    """Inclui flag que indica se é exceção individual ou herança da função."""
    e_excecao: bool = False


# ─── MODULO DISPONIVEL (tela de seleção pós-login) ───────────────────────────

class ModuloDisponivel(BaseModel):
    modulo: str
    modulo_nome: str
    modulo_descricao: Optional[str] = None
    pode_escrever: bool
    recursos_liberados: int


# ─── CONFLITO DE CADASTRO (409) ──────────────────────────────────────────────

class ConflitoCadastro(BaseModel):
    campo: str
    funcionario_id: UUID
    re: str
    nome: str
