"""Schemas Pydantic v2 do pré-cadastro de pessoas (Bloco H).

Espelha database/migrations/028-pessoa-pre-cadastro.sql. ⛔ Nunca existe
schema/endpoint que devolva isto sem exigir `pre_cadastro` (ou `usuarios`,
no caso de promover) — ver routers/pre_cadastro.py.
"""
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import ORMBase

PapelPreCadastro = Literal["MOTORISTA", "COBRADOR", "INDEFINIDO"]
StatusPreCadastro = Literal["PENDENTE", "PROMOVIDO", "DESCARTADO"]


class PreCadastroRead(ORMBase):
    id: UUID
    re: str
    nome: Optional[str] = None
    cpf: Optional[str] = None
    rg: Optional[str] = None
    cnh: Optional[str] = None
    telefone: Optional[str] = None
    papel_sugerido: PapelPreCadastro
    vezes_visto: int
    primeira_vez_em: datetime
    ultima_vez_em: datetime
    ultima_origem: Optional[str] = None
    status: StatusPreCadastro
    funcionario_id: Optional[UUID] = None
    promovido_por: Optional[UUID] = None
    promovido_em: Optional[datetime] = None
    descartado_por: Optional[UUID] = None
    descartado_em: Optional[datetime] = None
    descarte_motivo: Optional[str] = None
    retencao_expira_em: Optional[datetime] = None
    criado_em: datetime


class PreCadastroUpdate(BaseModel):
    """PATCH /pre-cadastros/{id} — corrige campos capturados errado na
    operação (ex.: nome digitado torto na pré-ocorrência). ⛔ Não aceita
    `status`/`re` de propósito: mudar status é /promover ou /descartar,
    RE é a chave de deduplicação e não deve mudar por aqui."""

    nome: Optional[str] = Field(None, max_length=120)
    cpf: Optional[str] = Field(None, max_length=14)
    rg: Optional[str] = Field(None, max_length=20)
    cnh: Optional[str] = Field(None, max_length=20)
    telefone: Optional[str] = Field(None, max_length=20)
    papel_sugerido: Optional[PapelPreCadastro] = None


class PreCadastroDescartarRequest(BaseModel):
    motivo: str = Field(..., min_length=1)


class PreCadastroPromoverResponse(BaseModel):
    """POST /pre-cadastros/{id}/promover — devolve só o funcionario_id
    criado. ⛔ Nunca cria usuario_login: acesso segue o caminho normal de
    cadastro, com quem tem `usuarios` escrever."""

    funcionario_id: UUID
