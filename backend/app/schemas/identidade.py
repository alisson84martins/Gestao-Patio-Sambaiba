"""Schemas Pydantic v2 — GET /identidade/re/{re} (Bloco A2).

Endpoint compartilhado de identidade por RE, pra pré-preenchimento em
qualquer módulo (portaria, ocorrência, pré-ocorrência) sem duplicar a
busca em public.funcionario/public.motorista (ver app/services/identidade.py,
que já resolve as duas tabelas — §5.3).

⛔ Nunca cpf/rg/cnh/telefone nem histórico — este endpoint só confirma
visualmente quem é o RE e o que a portaria precisa pra acelerar a entrada.
O autopreenchimento mais rico da Ocorrência (que já traz documentos)
continua em GET /ocorrencias/autopreencher/pessoa, não aqui.
"""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class VeiculoParticularIdentidade(BaseModel):
    id: UUID
    placa: str
    marca_modelo: Optional[str] = None
    cor: Optional[str] = None


class IdentidadeReResponse(BaseModel):
    # ⛔ Nunca 404 — RE inexistente é resultado válido (regra número um,
    # mesma convenção de ResolverReResponse em schemas/portaria.py).
    encontrado: bool = False
    id: Optional[UUID] = None
    nome: Optional[str] = None
    origem: Optional[Literal["FUNCIONARIO", "MOTORISTA"]] = None
    ativo: Optional[bool] = None
    # Só preenchido quando origem=FUNCIONARIO — motorista (legado) não tem
    # vínculo de função no cadastro central.
    funcoes: list[str] = []
    # PARTICULAR de portaria.veiculo, dono = esta pessoa, ativo=true. Lista
    # porque a mesma pessoa pode ter mais de um veículo particular
    # cadastrado.
    veiculo_particular: list[VeiculoParticularIdentidade] = []
