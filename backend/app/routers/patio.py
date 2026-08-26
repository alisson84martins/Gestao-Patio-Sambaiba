"""Endpoints de visão consolidada do pátio."""
from collections import defaultdict
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from typing import Annotated, Any, Optional
from zoneinfo import ZoneInfo
import json as _json

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.database import get_db
from app.core.deps import CurrentUser, exige
from app.routers.alocacoes import get_data_servico
from app.models import (
    Alerta,
    AlocacaoPatio,
    Escala,
    FichaManutencao,
    Funcionario,
    Fila,
    Linha,
    Onibus,
    RecolhidaAnormal,
    StatusFichaEnum,
    TipoFilaEnum,
    TipoDefeito,
)
from app.schemas.patio import (
    PatioFilaInfo,
    PatioLiberadoItem,
    PatioOnibusInfo,
    PosicaoOnibus,
    RemanejamentoItem,
)

router = APIRouter(prefix="/patio", tags=["pátio (visão consolidada)"])

# Gate do Bloco J, em constante de módulo (e não inline no decorator) pelo
# mesmo motivo de routers/pre_cadastro.py: exige() devolve uma função nova a
# cada chamada, e teste só consegue sobrescrever a dependência se houver uma
# referência estável pra apontar.
LeituraAlocacao = Annotated[Funcionario, Depends(exige("alocacao"))]


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
            Fila.abreviacao,                                             # 16
        )
        .select_from(Fila)
        .outerjoin(AlocacaoPatio, and_(
            AlocacaoPatio.fila_id == Fila.id,
            AlocacaoPatio.ativa.is_(True),
            AlocacaoPatio.data_referencia == get_data_servico(),
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

    # Deduplicação: um ônibus pode ter N escalas no mesmo dia (planilha com
    # duplicatas ou bus listado em E2 e Manobra ao mesmo tempo). A query retorna
    # N linhas para a mesma alocação, causando N chips idênticos no pátio.
    # Mantemos apenas a melhor linha por alocacao_id: preferimos linha real
    # (não-MAN-*) sobre placeholder; em empate, a primeira encontrada vence.
    best: dict = {}   # alocacao_id → row com melhor escala
    for r in rows:
        aloc_id = r[15]
        if aloc_id is None:
            continue
        if aloc_id not in best:
            best[aloc_id] = r
        else:
            atual_linha = best[aloc_id][10]   # Linha.codigo da row vencedora
            nova_linha  = r[10]
            atual_real  = atual_linha and not atual_linha.startswith('MAN-')
            nova_real   = nova_linha  and not nova_linha.startswith('MAN-')
            if nova_real and not atual_real:
                best[aloc_id] = r  # troca: nova tem linha real, atual não tem

    # Reconstrói a lista de rows mantendo a ordem original (por fila/posição)
    # e usando apenas a melhor escala por alocação
    seen_aloc: set = set()
    rows_dedup = []
    for r in rows:
        fila_id = r[0]
        aloc_id = r[15]
        if aloc_id is None:
            rows_dedup.append(r)
        elif aloc_id not in seen_aloc:
            seen_aloc.add(aloc_id)
            rows_dedup.append(best[aloc_id])  # usa a row com a melhor escala

    grupos: dict = {}
    ordem: list = []
    for r in rows_dedup:
        fila_id = r[0]
        if fila_id not in grupos:
            grupos[fila_id] = PatioFilaInfo(
                fila_id=fila_id,
                fila_nome=r[1],
                fila_tipo=r[2],
                fila_numero=r[3],
                fila_abreviacao=r[16],
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
            AlocacaoPatio.data_referencia == get_data_servico(),
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
            AlocacaoPatio.data_referencia == get_data_servico(),
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


# ─────────────────────────────────────────────────────────────────────────────
# Bloco J — "Prontos para a frota": a Manutenção publica, o Pátio assina
# ─────────────────────────────────────────────────────────────────────────────
# Hoje o carro volta quebrado, a manutenção resolve, e o operador de pátio só
# descobre por rádio ou olhando o pátio. Nesse intervalo é frota pronta e
# parada. Este endpoint é o aviso — não uma tela nova de manutenção.
#
# 🔴 RBAC: `alocacao` LEITURA, ⛔ jamais `manutencao`. O ponto inteiro do bloco
#    é o operador saber SEM ter acesso ao módulo da manutenção. Se algum dia
#    alguém "consertar" isto trocando o recurso, o bloco perde a razão de ser.
#
# ⛔ Zero tabela, zero coluna, zero flag de "liberado" e zero "marcar como
#    visto". Os dois fatos já existem no banco e o filtro 2 (carro ainda na
#    fila MANUTENCAO) faz a baixa sozinho: quando o operador move o chip, o
#    carro some do quadro porque a alocação mudou. O ato de alocar É a baixa.


# Início do ciclo operacional corrente, em horário de Brasília.
# get_data_servico() vira às 20h (ver routers/alocacoes.py) — então o ciclo
# corrente começou às 20h do dia anterior à data de serviço. ⛔ Nunca
# date.today(): às 22h de terça o pátio já opera a quarta, e um carro
# liberado às 21h ficaria de fora do próprio quadro.
_SP_PATIO = ZoneInfo("America/Sao_Paulo")


def _inicio_ciclo_servico() -> datetime:
    return datetime.combine(
        get_data_servico() - timedelta(days=1), time_type(20, 0), tzinfo=_SP_PATIO
    )


_DESFECHO_LEGIVEL = {
    "SEM_DEFEITO": "Sem defeito",
    "SERVICO_EXECUTADO": "Serviço executado",
}


@router.get("/liberados", response_model=list[PatioLiberadoItem],
            summary="Carros liberados pela manutenção e ainda parados na fila Manutenção")
def patio_liberados(
    user: LeituraAlocacao,
    db: Annotated[Session, Depends(get_db)],
):
    """Carros que a manutenção liberou neste ciclo e que continuam na fila
    MANUTENCAO do pátio — ou seja, prontos e ainda parados.

    Duas origens, o mesmo significado para o pátio:
      • recolhida anormal ENCERRADA (o mecânico fechou pela aba RA)
      • ficha de manutenção CONCLUIDA

    ⚠️ Avaliação `LIBERADO` da recolhida NÃO entra: é prognóstico ("esse
    volta, prazo tal"), não fato consumado. Botar no quadro faria o operador
    ir buscar carro que ainda está no elevador.
    """
    data_servico = get_data_servico()
    inicio_ciclo = _inicio_ciclo_servico()

    # Filtro 2 (o coração do bloco), aplicado igual nas duas consultas: o
    # carro precisa estar AGORA numa fila de tipo MANUTENCAO.
    def _com_filtro_de_patio(stmt, coluna_onibus_id):
        return (
            stmt
            .join(Onibus, Onibus.id == coluna_onibus_id)
            .join(AlocacaoPatio, and_(
                AlocacaoPatio.onibus_id == Onibus.id,
                AlocacaoPatio.ativa.is_(True),
                AlocacaoPatio.data_referencia == data_servico,
            ))
            .join(Fila, and_(
                Fila.id == AlocacaoPatio.fila_id,
                Fila.tipo == TipoFilaEnum.MANUTENCAO,
            ))
        )

    stmt_recolhida = _com_filtro_de_patio(
        select(
            Onibus.numero_frota,
            RecolhidaAnormal.encerrado_em,
            RecolhidaAnormal.desfecho,
        ).select_from(RecolhidaAnormal),
        RecolhidaAnormal.onibus_id,
    ).where(
        RecolhidaAnormal.status == "ENCERRADA",
        RecolhidaAnormal.encerrado_em.is_not(None),
        RecolhidaAnormal.encerrado_em >= inicio_ciclo,
    )

    stmt_ficha = _com_filtro_de_patio(
        select(
            Onibus.numero_frota,
            FichaManutencao.concluida_em,
            TipoDefeito.nome,
        ).select_from(FichaManutencao),
        FichaManutencao.onibus_id,
    ).outerjoin(
        TipoDefeito, TipoDefeito.id == FichaManutencao.tipo_defeito_id
    ).where(
        FichaManutencao.status == StatusFichaEnum.CONCLUIDA,
        FichaManutencao.concluida_em.is_not(None),
        FichaManutencao.concluida_em >= inicio_ciclo,
        FichaManutencao.deletado_em.is_(None),
    )

    # ⚠️ Dedupe por carro: recolhida com motivo=DEFEITO abre ficha
    # automaticamente e encerrá-la fecha a ficha — o mesmo carro sai nas duas
    # consultas, com timestamps quase iguais. Sem isto, chip duplicado no
    # quadro. Fica a liberação mais recente; em empate, a RECOLHIDA vence,
    # que é o evento pelo qual o operador reconhece o carro.
    por_carro: dict[int, PatioLiberadoItem] = {}

    def _considerar(item: PatioLiberadoItem) -> None:
        atual = por_carro.get(item.prefixo)
        if atual is None or item.liberado_em > atual.liberado_em:
            por_carro[item.prefixo] = item

    for frota, concluida_em, defeito in db.execute(stmt_ficha).all():
        _considerar(PatioLiberadoItem(
            prefixo=frota, liberado_em=concluida_em, origem="FICHA",
            detalhe=defeito or "Ficha concluída",
        ))

    for frota, encerrado_em, desfecho in db.execute(stmt_recolhida).all():
        _considerar(PatioLiberadoItem(
            prefixo=frota, liberado_em=encerrado_em, origem="RECOLHIDA",
            detalhe=_DESFECHO_LEGIVEL.get(desfecho, desfecho) or "Recolhida encerrada",
        ))

    # Mais recente no topo — mesmo padrão da fila de RA.
    return sorted(por_carro.values(), key=lambda i: i.liberado_em, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Estado V2 — blob JSON para sincronização multi-usuário
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/v2estado", summary="Estado V2 do pátio (blob para sync multi-usuário)")
def get_v2estado(user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    row = db.execute(
        text("SELECT estado, atualizado_em, atualizado_por FROM patio_v2_estado WHERE id = 1")
    ).one_or_none()
    return {
        "estado": row[0] if row else {},
        "atualizado_em": row[1].isoformat() if row and row[1] else None,
        "atualizado_por": row[2] if row else None,
    }


@router.put("/v2estado", summary="Salva estado V2 do pátio (blob para sync multi-usuário)")
def put_v2estado(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    estado: Any = Body(...),
):
    db.execute(
        text(
            "INSERT INTO patio_v2_estado (id, estado, atualizado_em, atualizado_por) "
            "VALUES (1, :estado::jsonb, NOW(), :re) "
            "ON CONFLICT (id) DO UPDATE "
            "SET estado = :estado::jsonb, atualizado_em = NOW(), atualizado_por = :re"
        ),
        {"estado": _json.dumps(estado), "re": user.re},
    )
    db.commit()
    return {"ok": True}
