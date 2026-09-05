"""Resolvedor de RE (§5.3) — a peça que faz os módulos conversarem sem se
importarem uns aos outros.

Existem DOIS cadastros de pessoa com RE único no sistema:
  • public.funcionario — cadastro central da Suite (gestão, operação, pátio,
    portaria).
  • public.motorista   — LEGADO, os motoristas de ônibus. Normalmente está
    só aqui, não em funcionario.
⛔ Olhar só uma das duas é o erro que faz a fila de pré-cadastro (Bloco H)
nascer cheia de lixo — todo motorista digitado no portão viraria
pré-cadastro mesmo já sendo cadastrado.

Nenhum módulo pergunta nada a outro módulo: todos perguntam "quem é o RE
X?" pra este serviço, que não devolve nada além do necessário pra
confirmar visualmente quem é (⛔ nunca cpf/rg/cnh/telefone/histórico).
"""
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.registro import normalizar_re
from app.models.cadastro import Funcionario
from app.models.pessoas import Motorista


@dataclass
class PessoaResolvida:
    id: object
    nome: str
    origem: Literal["FUNCIONARIO", "MOTORISTA"]
    ativo: bool
    duplicado: bool = False


def resolver_por_re(db: Session, re: str) -> Optional[PessoaResolvida]:
    """Procura o RE em funcionario primeiro (cadastro que vale hoje), depois
    em motorista. Achou nos dois -> devolve o de funcionario e marca
    duplicado=True (fica pro relatório gerencial; ⛔ não tenta unificar os
    dois cadastros aqui, isso é outro projeto). Não achou em lugar nenhum
    -> None."""
    re_norm = normalizar_re(re)
    if not re_norm:
        return None

    funcionario = db.execute(
        select(Funcionario).where(Funcionario.re == re_norm)
    ).scalar_one_or_none()
    motorista = db.execute(
        select(Motorista).where(Motorista.re == re_norm)
    ).scalar_one_or_none()

    if funcionario is not None:
        return PessoaResolvida(
            id=funcionario.id,
            nome=funcionario.nome,
            origem="FUNCIONARIO",
            ativo=(funcionario.status == "ATIVO"),
            duplicado=motorista is not None,
        )
    if motorista is not None:
        return PessoaResolvida(
            id=motorista.id,
            nome=motorista.nome,
            origem="MOTORISTA",
            ativo=(motorista.status == "ATIVO"),
        )
    return None
