"""GET /identidade/re/{re} (Bloco A2) — busca por RE compartilhada entre
módulos. Reusa app/services/identidade.py::resolver_por_re (§5.3), que já
olha public.funcionario e public.motorista (legado) — nenhum módulo
precisa mais perguntar a outro módulo quem é um RE.

RBAC: só get_current_funcionario (qualquer funcionário autenticado e
ativo), sem recurso específico — mesmo padrão de app/routers/health.py e
app/routers/funcionarios.py:534. Não há resurso mais estreito pra "leitura
em qualquer módulo" e o payload nunca carrega dado sensível (⛔
cpf/rg/cnh/telefone), então o gate de autenticação normal já basta.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_funcionario
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.portaria import VeiculoPortaria
from app.schemas.identidade import IdentidadeReResponse, VeiculoParticularIdentidade
from app.services.identidade import resolver_por_re

router = APIRouter(prefix="/identidade", tags=["identidade"])


@router.get(
    "/re/{re}",
    response_model=IdentidadeReResponse,
    summary="Confirma quem é um RE exato — nome, origem, funções e veículo particular, sem dado sensível",
)
def buscar_por_re(
    re: str,
    usuario: Annotated[Funcionario, Depends(get_current_funcionario)],
    db: Annotated[Session, Depends(get_db)],
):
    pessoa = resolver_por_re(db, re)
    if pessoa is None:
        return IdentidadeReResponse(encontrado=False)

    funcoes: list[str] = []
    veiculos: list[VeiculoParticularIdentidade] = []

    if pessoa.origem == "FUNCIONARIO":
        funcoes = list(db.execute(
            select(Funcao.codigo)
            .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(FuncionarioFuncao.funcionario_id == pessoa.id, FuncionarioFuncao.ativo.is_(True))
        ).scalars().all())

        veiculos_db = db.execute(
            select(VeiculoPortaria).where(
                VeiculoPortaria.propriedade == "PARTICULAR",
                VeiculoPortaria.funcionario_id == pessoa.id,
                VeiculoPortaria.ativo.is_(True),
            )
        ).scalars().all()
        veiculos = [
            VeiculoParticularIdentidade(
                id=v.id, placa=v.placa, marca_modelo=v.marca_modelo, cor=v.cor,
            )
            for v in veiculos_db
        ]

    return IdentidadeReResponse(
        encontrado=True,
        id=pessoa.id,
        nome=pessoa.nome,
        origem=pessoa.origem,
        ativo=pessoa.ativo,
        funcoes=funcoes,
        veiculo_particular=veiculos,
    )
