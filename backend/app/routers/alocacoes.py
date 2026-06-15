"""Mover ônibus entre filas/posições do pátio."""
from datetime import date, datetime, timedelta
from typing import Annotated, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, OperadorOuAdmin
from app.core.utils import PaginationParams, set_create_audit, set_update_audit
from pydantic import BaseModel as _BaseModel

from app.models import AlocacaoPatio, Escala, Fila, Linha, Onibus, TipoFilaEnum
from app.schemas import AlocacaoBlocoCreate, AlocacaoPatioCreate, AlocacaoPatioRead, AlocacaoPatioUpdate


class _LinhaPatch(_BaseModel):
    linha_codigo: str

router = APIRouter(prefix="/alocacoes", tags=["alocações / pátio"])

_SP = ZoneInfo("America/Sao_Paulo")


def get_data_servico() -> date:
    """Retorna a data de serviço operacional.

    O turno de alocação começa às ~23:30 e vai até ~04:30.
    Após as 20h (horário de Brasília) o pátio já está alocando para o
    dia seguinte — então data_referencia = amanhã.
    Antes das 20h = hoje.

    Exemplos:
        23:45 de segunda → data_referencia = terça  ✅
        02:00 de terça   → data_referencia = terça  ✅
        18:00 de terça   → data_referencia = terça  ✅
        21:00 de terça   → data_referencia = quarta ✅ (próximo ciclo)
    """
    agora = datetime.now(_SP)
    if agora.hour >= 20:
        return (agora + timedelta(days=1)).date()
    return agora.date()


@router.post("", response_model=AlocacaoPatioRead, status_code=status.HTTP_201_CREATED,
             summary="Aloca ônibus em fila/posição")
def criar(payload: AlocacaoPatioCreate, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
    """Move um ônibus para uma fila. O trigger desativa automaticamente a alocação anterior.

    Restrito a Operadores de Pátio e Administradores.
    """
    if not db.get(Onibus, payload.onibus_id):
        raise HTTPException(404, "Ônibus não encontrado")
    if not db.get(Fila, payload.fila_id):
        raise HTTPException(404, "Fila não encontrada")
    a = AlocacaoPatio(
        **payload.model_dump(),
        alocado_por=user.id,
        data_referencia=get_data_servico(),
    )
    set_create_audit(a, user)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("", response_model=list[AlocacaoPatioRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    ativa: Optional[bool] = None,
    onibus_id: Optional[UUID] = None,
    fila_id: Optional[UUID] = None,
    data_referencia: Optional[date] = None,
):
    q = select(AlocacaoPatio)
    if ativa is not None:
        q = q.where(AlocacaoPatio.ativa == ativa)
    if onibus_id:
        q = q.where(AlocacaoPatio.onibus_id == onibus_id)
    if fila_id:
        q = q.where(AlocacaoPatio.fila_id == fila_id)
    if data_referencia is not None:
        q = q.where(AlocacaoPatio.data_referencia == data_referencia)
    q = q.order_by(AlocacaoPatio.alocado_em.desc()).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{aloc_id}", response_model=AlocacaoPatioRead)
def buscar(aloc_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    a = db.get(AlocacaoPatio, aloc_id)
    if not a:
        raise HTTPException(404, "Alocação não encontrada")
    return a


@router.delete("/{aloc_id}", response_model=AlocacaoPatioRead,
               summary="Desativa alocação (tira ônibus da fila)")
def desativar(aloc_id: UUID, user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
    """Marca a alocação como inativa. Não remove do banco — preserva o histórico."""
    a = db.get(AlocacaoPatio, aloc_id)
    if not a:
        raise HTTPException(404, "Alocação não encontrada")
    a.ativa = False
    set_update_audit(a, user)
    db.commit()
    db.refresh(a)
    return a


@router.patch("/{aloc_id}/linha", status_code=200,
              summary="Atualiza linha da escala sem mover o ônibus na fila")
def atualizar_linha(aloc_id: UUID, payload: _LinhaPatch, user: OperadorOuAdmin,
                    db: Annotated[Session, Depends(get_db)]):
    """Troca a linha da escala do dia sem remover/realocar o ônibus.

    Preserva a posição na fila — use quando o alocador muda só o código de linha.
    """
    aloc = db.get(AlocacaoPatio, aloc_id)
    if not aloc or not aloc.ativa:
        raise HTTPException(404, "Alocação ativa não encontrada")

    linha = db.execute(
        select(Linha).where(Linha.codigo == payload.linha_codigo)
    ).scalar_one_or_none()
    if not linha:
        raise HTTPException(404, f"Linha '{payload.linha_codigo}' não encontrada")

    data_hoje = get_data_servico()
    escala = db.execute(
        select(Escala).where(
            Escala.onibus_id == aloc.onibus_id,
            Escala.data == data_hoje,
            Escala.deletado_em.is_(None),
        )
    ).scalar_one_or_none()
    if not escala:
        raise HTTPException(404, "Escala não encontrada para este ônibus hoje")

    escala.linha_id = linha.id
    set_update_audit(escala, user)
    db.commit()
    return {"ok": True, "linha_codigo": linha.codigo}


@router.delete("", status_code=200,
               summary="Limpa todas as alocações ativas do dia de serviço (operação atômica)")
def limpar_tudo(user: OperadorOuAdmin, db: Annotated[Session, Depends(get_db)]):
    """Desativa todas as alocações ativas da data de serviço atual em uma única transação."""
    data = get_data_servico()
    result = db.execute(
        text("""
            UPDATE alocacao_patio
               SET ativa = FALSE
             WHERE ativa = TRUE AND data_referencia = :data
        """),
        {"data": data},
    )
    db.commit()
    return {"removidas": result.rowcount}


@router.post("/bloco", response_model=AlocacaoPatioRead,
             status_code=status.HTTP_201_CREATED,
             summary="Modo bloco: aloca recebendo identificadores amigáveis")
def alocar_bloco(payload: AlocacaoBlocoCreate, user: OperadorOuAdmin,
                 db: Annotated[Session, Depends(get_db)]):
    """Endpoint atômico do modo bloco da Fase 5.3.

    - Resolve numero_frota → onibus_id e fila (numero ou nome) → fila_id
    - Sentido 'ida': insere na próxima posição livre
    - Sentido 'volta': empurra todas as ativas (posicao += 1) e insere em 1
    - Tudo em uma única transação (rollback automático em erro)
    """
    # 1) Resolve ônibus por numero_frota — cria automaticamente se não existir
    onibus = db.execute(
        select(Onibus).where(Onibus.numero_frota == payload.numero_frota)
    ).scalar_one_or_none()
    if not onibus:
        onibus = Onibus(numero_frota=payload.numero_frota)
        set_create_audit(onibus, user)
        db.add(onibus)
        db.flush()  # obtém o id sem commitar ainda

    # 2) Resolve fila: tenta como número primeiro (ex: "5"), depois como nome (ex: "Lavador")
    fila = None
    fila_input = payload.fila.strip()
    if fila_input.isdigit():
        fila = db.execute(
            select(Fila).where(
                Fila.numero == int(fila_input),
                Fila.tipo == TipoFilaEnum.NUMERICA,
                Fila.ativa.is_(True),
            )
        ).scalar_one_or_none()
    if not fila:
        # Busca por nome (case-insensitive) para especiais e remotas
        fila = db.execute(
            select(Fila).where(
                Fila.nome.ilike(fila_input),
                Fila.ativa.is_(True),
            )
        ).scalar_one_or_none()
    if not fila:
        raise HTTPException(404, f"Fila '{payload.fila}' não encontrada")

    # 3) Calcula posição conforme sentido
    if payload.sentido == "ida":
        # Próxima posição livre = max(posicao ativa) + 1, ou 1 se vazia
        max_pos = db.execute(text("""
            SELECT COALESCE(MAX(posicao), 0)
              FROM alocacao_patio
             WHERE fila_id = :fila_id AND ativa = TRUE
        """), {"fila_id": fila.id}).scalar()
        nova_posicao = (max_pos or 0) + 1
    else:
        # VOLTA: empurra todas as posições ativas +1 e insere na posição 1.
        # uq_alocacao_fila_posicao_ativa é CREATE UNIQUE INDEX imediato (não DEFERRABLE),
        # então o PostgreSQL verifica unicidade linha a linha dentro de um UPDATE.
        # Solução: usar o ORM para deslocar da maior posição para a menor,
        # garantindo que nunca haverá duas linhas na mesma posição durante o processo.
        # O with_for_update() também previne race condition entre requests simultâneos.
        existentes = db.execute(
            select(AlocacaoPatio)
            .where(AlocacaoPatio.fila_id == fila.id, AlocacaoPatio.ativa.is_(True))
            .order_by(AlocacaoPatio.posicao.desc())
            .with_for_update()
        ).scalars().all()
        # Flush um a um da maior para a menor posição.
        # uq_alocacao_fila_posicao_ativa é verificado linha a linha pelo PostgreSQL.
        # Processar da maior posição para a menor garante que o slot de destino
        # sempre está livre antes de cada UPDATE individual:
        #   posicao 3→4 (4 livre)  →  flush
        #   posicao 2→3 (3 livre)  →  flush
        #   posicao 1→2 (2 livre)  →  flush
        for aloc in existentes:
            aloc.posicao += 1
            db.flush()
        nova_posicao = 1

    # 4) Insere a nova alocação (trigger desativa alocação anterior do mesmo ônibus)
    nova = AlocacaoPatio(
        onibus_id=onibus.id,
        fila_id=fila.id,
        posicao=nova_posicao,
        ativa=True,
        alocado_por=user.id,
        data_referencia=get_data_servico(),
    )
    set_create_audit(nova, user)
    db.add(nova)

    # 5) Commit único — se qualquer passo acima falhou, transação inteira reverte
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(409, f"Conflito ao alocar: {str(e)}")
    db.refresh(nova)
    return nova
