"""Endpoints de visão consolidada do pátio."""
from collections import defaultdict
from datetime import date as date_type, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import (
    Alerta,
    AlocacaoPatio,
    Escala,
    FichaManutencao,
    Fila,
    Linha,
    Onibus,
    StatusFichaEnum,
    TipoFilaEnum,
    TipoDefeito,
)
from app.schemas.patio import (
    PatioFilaInfo,
    PatioOnibusInfo,
    PosicaoOnibus,
    RemanejamentoItem,
)

router = APIRouter(prefix="/patio", tags=["pátio (visão consolidada)"])


@router.get("", response_model=list[PatioFilaInfo],
            summary="Estado completo do pátio (query master)")
def patio_completo(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    data_escala: Annotated[Optional[date_type], Query(
        description="Data da escala a cruzar. Default: hoje."
    )] = None,
):
    """Retorna todas as filas com seus ônibus alocados, escala do dia, alertas e fichas abertas.

    Esta é a query principal que alimenta a tela e a impressão do pátio.
    """
    if data_escala is None:
        data_escala = datetime.now(timezone.utc).date()

    stmt = (
        select(
            Fila.id, Fila.nome, Fila.tipo, Fila.numero,                  # 0-3
            Onibus.id, Onibus.numero_frota, Onibus.setor, Onibus.status, # 4-7
            AlocacaoPatio.posicao, AlocacaoPatio.alocado_em,             # 8-9
            Linha.codigo, Linha.nome,                                    # 10-11
            Escala.horario_saida,                                        # 12
            Alerta.tipo,                                                 # 13
            FichaManutencao.status,                                      # 14
            AlocacaoPatio.id,                                            # 15
        )
        .select_from(Fila)
        .outerjoin(AlocacaoPatio, and_(
            AlocacaoPatio.fila_id == Fila.id,
            AlocacaoPatio.ativa.is_(True),
        ))
        .outerjoin(Onibus, Onibus.id == AlocacaoPatio.onibus_id)
        .outerjoin(Escala, and_(
            Escala.onibus_id == Onibus.id,
            Escala.data == data_escala,
            Escala.deletado_em.is_(None),
        ))
        .outerjoin(Linha, Linha.id == Escala.linha_id)
        .outerjoin(Alerta, and_(
            Alerta.onibus_id == Onibus.id,
            Alerta.resolvido.is_(False),
            Alerta.deletado_em.is_(None),
        ))
        .outerjoin(FichaManutencao, and_(
            FichaManutencao.onibus_id == Onibus.id,
            FichaManutencao.status.in_([StatusFichaEnum.ABERTA, StatusFichaEnum.EM_ANDAMENTO]),
            FichaManutencao.deletado_em.is_(None),
        ))
        .where(Fila.ativa.is_(True))
        .order_by(Fila.tipo, Fila.ordem_exibicao, AlocacaoPatio.posicao)
    )
    rows = db.execute(stmt).all()

    grupos: dict = {}
    ordem: list = []
    for r in rows:
        fila_id = r[0]
        if fila_id not in grupos:
            grupos[fila_id] = PatioFilaInfo(
                fila_id=fila_id,
                fila_nome=r[1],
                fila_tipo=r[2],
                fila_numero=r[3],
                onibus=[],
            )
            ordem.append(fila_id)
        if r[4] is not None:  # tem ônibus alocado
            grupos[fila_id].onibus.append(PatioOnibusInfo(
                onibus_id=r[4],
                alocacao_id=r[15],
                numero_frota=r[5],
                setor=r[6].value if r[6] else None,
                status_onibus=r[7],
                posicao=r[8],
                alocado_em=r[9],
                linha_codigo=r[10],
                linha_nome=r[11],
                horario_saida=r[12],
                alerta_tipo=r[13],
                ficha_status=r[14],
            ))
    return [grupos[fid] for fid in ordem]


@router.get("/onibus/{numero_frota}", response_model=PosicaoOnibus,
            summary="Onde está o ônibus X agora?")
def onde_esta(numero_frota: int, user: CurrentUser,
              db: Annotated[Session, Depends(get_db)]):
    """Resposta rápida 'em que fila está o ônibus 1234?'."""
    stmt = (
        select(Onibus.numero_frota, Onibus.setor,
               Fila.nome, Fila.tipo,
               AlocacaoPatio.posicao, AlocacaoPatio.alocado_em)
        .select_from(Onibus)
        .join(AlocacaoPatio, and_(
            AlocacaoPatio.onibus_id == Onibus.id,
            AlocacaoPatio.ativa.is_(True),
        ))
        .join(Fila, Fila.id == AlocacaoPatio.fila_id)
        .where(Onibus.numero_frota == numero_frota)
    )
    row = db.execute(stmt).one_or_none()
    if not row:
        raise HTTPException(404, f"Ônibus {numero_frota} não está alocado em nenhuma fila")
    return PosicaoOnibus(
        numero_frota=row[0],
        setor=row[1].value if row[1] else None,
        fila_nome=row[2],
        fila_tipo=row[3],
        posicao=row[4],
        alocado_em=row[5],
    )


@router.get("/remanejamento", response_model=list[RemanejamentoItem],
            summary="Ônibus em manutenção que têm escala hoje")
def remanejamento(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    data_escala: Annotated[Optional[date_type], Query(
        description="Default: hoje"
    )] = None,
):
    """Lista ônibus que precisam de remanejamento: estão em manutenção mas têm linha escalada."""
    if data_escala is None:
        data_escala = datetime.now(timezone.utc).date()
    stmt = (
        select(
            Onibus.id, Onibus.numero_frota,
            Linha.codigo, Linha.nome,
            Escala.horario_saida,
            Fila.nome,
            TipoDefeito.nome,
            FichaManutencao.status,
            FichaManutencao.aberta_em,
        )
        .select_from(Onibus)
        .join(AlocacaoPatio, and_(
            AlocacaoPatio.onibus_id == Onibus.id,
            AlocacaoPatio.ativa.is_(True),
        ))
        .join(Fila, and_(
            Fila.id == AlocacaoPatio.fila_id,
            Fila.tipo == TipoFilaEnum.MANUTENCAO,
        ))
        .join(Escala, and_(
            Escala.onibus_id == Onibus.id,
            Escala.data == data_escala,
            Escala.deletado_em.is_(None),
        ))
        .join(Linha, Linha.id == Escala.linha_id)
        .outerjoin(FichaManutencao, and_(
            FichaManutencao.onibus_id == Onibus.id,
            FichaManutencao.status.in_([StatusFichaEnum.ABERTA, StatusFichaEnum.EM_ANDAMENTO]),
            FichaManutencao.deletado_em.is_(None),
        ))
        .outerjoin(TipoDefeito, TipoDefeito.id == FichaManutencao.tipo_defeito_id)
        .order_by(Escala.horario_saida)
    )
    return [
        RemanejamentoItem(
            onibus_id=r[0],
            numero_frota=r[1],
            linha_codigo=r[2],
            linha_nome=r[3],
            horario_saida=r[4],
            fila_manutencao=r[5],
            tipo_defeito=r[6],
            status_ficha=r[7],
            ficha_aberta_em=r[8],
        )
        for r in db.execute(stmt).all()
    ]
