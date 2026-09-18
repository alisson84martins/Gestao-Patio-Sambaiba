"""Funções compartilhadas do módulo Portaria.

§3.6-D.2 (revisão 20/08): `veiculo_read` morava em portaria_veiculos.py e
era importada por portaria.py — função privada cruzando módulo, sem ciclo,
mas num repo que é vitrine isso pertence a services, não a um router
importando outro.

`resolver_onibus_por_prefixo` (migration 041/P4): morava como função
privada em routers/portaria_recolhidas.py. Move pra cá porque o reservado
(routers/portaria.py, recurso acesso_veicular) precisa da mesma resolução
que a recolhida anormal (recurso recolhida_anormal) já fazia — dois
routers, mesma lógica, nunca um importando o outro. A rota antiga
GET /portaria/recolhidas/resolver-prefixo continua intacta; a nova
GET /portaria/resolver-prefixo existe porque aquela exige
`recolhida_anormal` e quem registra passagem é gated por `acesso_veicular`.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cadastro import Funcionario
from app.models.frota import Onibus
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


def ligar_veiculo_a_funcionario(veiculo: VeiculoPortaria, *, funcionario_id: UUID, usuario_id: UUID) -> None:
    """Item 3 (18/09/2026) — vincula UM veículo PARTICULAR já resolvido a um
    funcionário e limpa `re_dono_texto` (o snapshot vira histórico,
    migration 039/ck_veiculo_dono). Base compartilhada por "Completar dono"
    (um carro por chamada, routers/portaria_veiculos.py) e pela promoção de
    pré-cadastro em lote (`ligar_veiculos_por_re` abaixo). ⛔ Não muda
    `situacao` — autorizar é outro ato (D6)."""
    veiculo.funcionario_id = funcionario_id
    veiculo.re_dono_texto = None
    veiculo.atualizado_em = datetime.now(timezone.utc)
    veiculo.atualizado_por = usuario_id


def ligar_veiculos_por_re(
    db: Session, *, re: str, funcionario_id: UUID, usuario_id: UUID
) -> list[VeiculoPortaria]:
    """Promoção de pré-cadastro (3b, routers/pre_cadastro.py) — ao contrário
    de "Completar dono" (sempre um carro por chamada: o Alisson confere a
    placa física antes de ligar mais de uma), a promoção parte da PESSOA e
    por isso liga TODOS os veículos PARTICULAR/ativos que ainda estão sem
    dono com esse `re_dono_texto`. Mora aqui (não em pre_cadastro.py) pela
    fronteira de schema — routers/pre_cadastro.py nunca importa
    VeiculoPortaria direto, só chama esta função."""
    veiculos = db.execute(
        select(VeiculoPortaria).where(
            VeiculoPortaria.propriedade == "PARTICULAR",
            VeiculoPortaria.ativo.is_(True),
            VeiculoPortaria.funcionario_id.is_(None),
            VeiculoPortaria.re_dono_texto == re,
        )
    ).scalars().all()
    for veiculo in veiculos:
        ligar_veiculo_a_funcionario(veiculo, funcionario_id=funcionario_id, usuario_id=usuario_id)
    db.flush()
    return veiculos


def resolver_onibus_por_prefixo(db: Session, prefixo: str) -> Optional[Onibus]:
    """Mesma regra de routers/ocorrencias.py:normalizar_prefixo — número de
    frota é '1' + 4 dígitos (ex.: 21234) ou só os 4 dígitos (1234), sempre
    na faixa 1000-2999. ⛔ Nunca recusa quem chama: prefixo fora do padrão
    ou não cadastrado devolve None, nunca levanta exceção (regra número
    um — quem chama decide o aviso)."""
    digitos = prefixo.strip()
    if not digitos.isdigit():
        return None
    if len(digitos) == 5 and digitos[0] == "2":
        numero = int(digitos[1:])
    elif len(digitos) == 4:
        numero = int(digitos)
    else:
        return None
    if not (1000 <= numero <= 2999):
        return None
    return db.execute(select(Onibus).where(Onibus.numero_frota == numero)).scalar_one_or_none()
