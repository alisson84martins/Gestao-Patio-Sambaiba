"""Funções compartilhadas do módulo Portaria.

§3.6-D.2 (revisão 20/08): `veiculo_read` morava em portaria_veiculos.py e
era importada por portaria.py — função privada cruzando módulo, sem ciclo,
mas num repo que é vitrine isso pertence a services, não a um router
importando outro.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.cadastro import Funcionario
from app.models.portaria import EmpresaTerceira, VeiculoPortaria
from app.schemas.portaria import VeiculoRead


def veiculo_read(veiculo: VeiculoPortaria, db: Session) -> VeiculoRead:
    """Monta VeiculoRead com os nomes resolvidos por join simples — a tela
    do controlador e a ficha do veículo mostram nome, não UUID cru."""
    extras: dict[str, Optional[str]] = {}
    if veiculo.funcionario_id:
        dono = db.get(Funcionario, veiculo.funcionario_id)
        if dono is not None:
            extras["funcionario_nome"] = dono.nome
            extras["funcionario_re"] = dono.re
    if veiculo.empresa_terceira_id:
        empresa = db.get(EmpresaTerceira, veiculo.empresa_terceira_id)
        if empresa is not None:
            extras["empresa_terceira_nome"] = empresa.nome
    if veiculo.criado_por:
        autor = db.get(Funcionario, veiculo.criado_por)
        if autor is not None:
            extras["criado_por_nome"] = autor.nome
    if veiculo.situacao_por:
        decisor = db.get(Funcionario, veiculo.situacao_por)
        if decisor is not None:
            extras["situacao_por_nome"] = decisor.nome
    return VeiculoRead.model_validate(veiculo).model_copy(update=extras)
