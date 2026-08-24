"""Avaria na saída da frota (Bloco G) — o controlador confere o carro
saindo do pátio e anota um dano visível (para-choque quebrado, retrovisor
rachado, risco na lateral). Não é recolhida (o carro está saindo, não
voltando) e não é ocorrência (não houve sinistro); serve para responder
"esse risco já estava aí ontem?" quando o carro volta com dano maior.

Router próprio, fora de routers/portaria.py (⛔ já tem 18 KB) — mesmo
motivo que levou routers/portaria_recolhidas.py a existir separado.

RBAC: reaproveita `acesso_veicular` (migration 024) — quem confere o carro
saindo (POST /portaria/movimentos) é a mesma pessoa que registra a avaria
vista na saída. Nenhum recurso novo (menor privilégio, padrão da migration
020 — ver database/migrations/036-avaria-saida-frota.sql).

🔴 REGRA NÚMERO UM do módulo vale aqui também: POST nunca recusa por
prefixo desconhecido ou RE que não resolve — registra assim mesmo.

⚠️ Retenção de 60 dias (`avaria_saida.expira_em`) é aplicada por FILTRO no
GET, não por job de background — o projeto não tem scheduler (mesma
decisão da migration 028, pré-cadastro)."""
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.portaria import AvariaSaida
from app.routers.alocacoes import get_data_servico
from app.schemas.portaria import AvariaSaidaCreate, AvariaSaidaRead
from app.services.identidade import resolver_por_re
from app.services.pre_cadastro import registrar_pessoa_vista

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular"))]
EscritaAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular", escrever=True))]


@router.post(
    "/avarias",
    response_model=AvariaSaidaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra avaria vista na saída do carro — nunca recusa (regra número um)",
)
def registrar_avaria(
    payload: AvariaSaidaCreate, usuario: EscritaAcesso, db: Annotated[Session, Depends(get_db)]
):
    # RE resolvido -> nome vem do cadastro (snapshot mais confiável que o
    # que foi digitado); RE não resolvido -> fica o que o controlador
    # informou, se informou algo.
    motorista_nome = payload.motorista_nome
    if payload.motorista_re:
        pessoa = resolver_por_re(db, payload.motorista_re)
        if pessoa is not None:
            motorista_nome = pessoa.nome

    nova = AvariaSaida(
        prefixo=payload.prefixo,
        # ⛔ Nunca date.today() — o ciclo operacional vira às 20h. Divergência
        # PROPOSITAL de portaria.movimento.data_referencia (D9, 24h corrido,
        # migration 024): a avaria acompanha o dia de OPERAÇÃO do carro.
        data_servico=get_data_servico(),
        motorista_re=payload.motorista_re,
        motorista_nome=motorista_nome,
        descricao=payload.descricao,
        registrado_por=usuario.id,
    )
    db.add(nova)

    # Bloco F/H, mesma costura de portaria_recolhidas.py: RE que não
    # resolveu alimenta o pré-cadastro — de graça, nunca bloqueia
    # (registrar_pessoa_vista nunca propaga exceção).
    registrar_pessoa_vista(
        db, re=payload.motorista_re, papel="MOTORISTA", origem="PORTARIA_AVARIA",
        nome=payload.motorista_nome,
    )

    db.commit()
    db.refresh(nova)
    return nova


@router.get(
    "/avarias",
    response_model=list[AvariaSaidaRead],
    summary="Histórico por prefixo (60 dias) e/ou por data de serviço do turno — nunca registro vencido",
)
def listar_avarias(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    prefixo: Optional[str] = Query(None, min_length=1, max_length=10),
    data: Optional[date] = None,
):
    stmt = select(AvariaSaida).where(AvariaSaida.expira_em > datetime.now(timezone.utc))
    if prefixo:
        stmt = stmt.where(AvariaSaida.prefixo == prefixo.strip())
    if data:
        stmt = stmt.where(AvariaSaida.data_servico == data)
    stmt = stmt.order_by(AvariaSaida.ocorrido_em.desc())
    return db.execute(stmt).scalars().all()
