"""Endpoints de recolhida anormal (Bloco F) — ônibus que recolhe fora de
hora, com defeito.

🔴 REGRA NÚMERO UM (mesma do resto do módulo): o sistema NUNCA impede um
registro. Prefixo não cadastrado, escala não encontrada, ficha que não pôde
nascer — registra assim mesmo e sinaliza. POST /recolhidas sempre responde
201 quando o payload é válido.

🔴 A REGRA DO MOTORISTA: esta tela nunca mostra, pede ou devolve motorista
nem cobrador pra quem só tem `recolhida_anormal` — isso é gerencial
(`recolhida_gerencial`). O backend resolve o motorista pela escala
(prefixo + data_referencia + hora); o cobrador fica sempre NULL porque a
tabela escala não tem esse campo (ver comentário de
models/portaria.py:RecolhidaAnormal.cobrador_re).

FINALIDADE DO DADO: melhoria de processo e de frota. A associação
motorista↔defeito serve pra encontrar padrão de operação e necessidade de
treinamento — o dado é do VEÍCULO, não da pessoa.
"""
from collections import Counter
from datetime import date, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.frota import Onibus
from app.models.operacoes import Escala
from app.models.pessoas import Motorista
from app.models.portaria import RecolhidaAnormal
from app.schemas.portaria import (
    ContagemPendentesResponse, RecolhidaAnaliseItem, RecolhidaAnaliseResponse,
    RecolhidaAvaliacaoRequest, RecolhidaCreate, RecolhidaGerencialRead, RecolhidaRead,
    ResolverPrefixoResponse, StatusRecolhida,
)
from app.services.manutencao_recolhida import abrir_ficha_de_recolhida

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraRecolhida = Annotated[Funcionario, Depends(exige("recolhida_anormal"))]
EscritaRecolhida = Annotated[Funcionario, Depends(exige("recolhida_anormal", escrever=True))]
LeituraGerencial = Annotated[Funcionario, Depends(exige("recolhida_gerencial"))]
# Avaliação usa o recurso `manutencao`, já existente — hoje só MECANICO e
# ADMIN têm escrever (conferido em seeds/08). Nenhum recurso novo pra isso.
EscritaManutencao = Annotated[Funcionario, Depends(exige("manutencao", escrever=True))]


# ============================================================================
# Resolução de prefixo -> ônibus e de escala -> motorista. Privadas deste
# router de propósito — regra de fronteira: portaria não importa lógica de
# outro router (mesma convenção duplicada, não compartilhada, de
# routers/ocorrencias.py:normalizar_prefixo).
# ============================================================================

def _resolver_onibus_por_prefixo(db: Session, prefixo: str) -> Optional[Onibus]:
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


def _resolver_motorista_pela_escala(
    db: Session, onibus_id: UUID, data_referencia: date, hora_local
):
    """Escala mais recente daquele ônibus, no dia, com horario_saida <= a
    hora da recolhida — é o turno que já estava em curso quando o carro
    voltou com defeito. Sem escala compatível -> não resolve (não chuta)."""
    escalas = db.execute(
        select(Escala)
        .where(
            Escala.onibus_id == onibus_id,
            Escala.data == data_referencia,
            Escala.deletado_em.is_(None),
        )
        .order_by(Escala.horario_saida.desc())
    ).scalars().all()
    escolhida = next((e for e in escalas if e.horario_saida <= hora_local), None)
    if escolhida is None or escolhida.motorista_id is None:
        return None
    motorista = db.get(Motorista, escolhida.motorista_id)
    return motorista


# ============================================================================
# FILA/CONTAGEM/GERENCIAL/ANÁLISE — registrados ANTES de qualquer rota com
# {recolhida_id}, mesma convenção do resto do módulo.
# ============================================================================

@router.get(
    "/recolhidas/resolver-prefixo",
    response_model=ResolverPrefixoResponse,
    summary="🔧 Adição fora do desenho original (§2.6) — a tela mostra 'cadastrado' ou 'não cadastrado' ao sair do campo",
)
def resolver_prefixo(
    usuario: LeituraRecolhida,
    db: Annotated[Session, Depends(get_db)],
    prefixo: str = Query(..., min_length=1, max_length=10),
):
    onibus = _resolver_onibus_por_prefixo(db, prefixo)
    if onibus is None:
        return ResolverPrefixoResponse(encontrado=False)
    return ResolverPrefixoResponse(encontrado=True, placa=onibus.placa)


@router.get(
    "/recolhidas/pendentes",
    response_model=list[RecolhidaRead],
    summary="Fila da manutenção — AGUARDANDO primeiro, mais recente no topo",
)
def listar_pendentes(usuario: LeituraRecolhida, db: Annotated[Session, Depends(get_db)]):
    return db.execute(
        select(RecolhidaAnormal)
        .where(RecolhidaAnormal.status == "AGUARDANDO")
        .order_by(RecolhidaAnormal.momento.desc())
    ).scalars().all()


@router.get(
    "/recolhidas/contagem-pendentes",
    response_model=ContagemPendentesResponse,
    summary="Total de AGUARDANDO — o alerta da fila",
)
def contar_pendentes(usuario: LeituraRecolhida, db: Annotated[Session, Depends(get_db)]):
    total = len(db.execute(
        select(RecolhidaAnormal.id).where(RecolhidaAnormal.status == "AGUARDANDO")
    ).scalars().all())
    return ContagemPendentesResponse(total=total)


@router.get(
    "/recolhidas/gerencial",
    response_model=list[RecolhidaGerencialRead],
    summary="Visão gerencial — com motorista/cobrador. exige recolhida_gerencial",
)
def listar_gerencial(
    usuario: LeituraGerencial,
    db: Annotated[Session, Depends(get_db)],
    status_filtro: Optional[StatusRecolhida] = Query(None, alias="status"),
    data: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if status_filtro:
        stmt = stmt.where(RecolhidaAnormal.status == status_filtro)
    if data:
        stmt = stmt.where(RecolhidaAnormal.data_referencia == data)
    stmt = stmt.order_by(RecolhidaAnormal.momento.desc())
    return db.execute(stmt).scalars().all()


@router.get(
    "/recolhidas/analise",
    response_model=RecolhidaAnaliseResponse,
    summary="Agregados por período — melhoria de processo e frota, nunca avaliação de pessoa (§2.7)",
)
def analise_recolhidas(
    usuario: LeituraGerencial,
    db: Annotated[Session, Depends(get_db)],
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if data_inicio:
        stmt = stmt.where(RecolhidaAnormal.data_referencia >= data_inicio)
    if data_fim:
        stmt = stmt.where(RecolhidaAnormal.data_referencia <= data_fim)
    linhas = db.execute(stmt).scalars().all()

    def _ordenado(contador: Counter) -> list[RecolhidaAnaliseItem]:
        return [RecolhidaAnaliseItem(chave=chave, total=total) for chave, total in contador.most_common()]

    def _faixa_horario(momento: datetime) -> str:
        m = momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
        hora = m.astimezone(FUSO_OPERACAO).hour
        inicio = (hora // 4) * 4
        return f"{inicio:02d}h–{(inicio + 4) % 24:02d}h"

    por_prefixo = Counter(r.prefixo for r in linhas)
    por_linha = Counter(r.linha_codigo or "—" for r in linhas)
    por_motorista = Counter(
        f"{r.motorista_re} · {r.motorista_nome}" for r in linhas if r.motorista_re
    )
    por_tipo_defeito = Counter(r.tipo_defeito_codigo for r in linhas)
    por_faixa_horario = Counter(_faixa_horario(r.momento) for r in linhas)

    avaliadas = [r for r in linhas if r.avaliado_em is not None]
    tempo_medio: Optional[float] = None
    if avaliadas:
        total_segundos = 0.0
        for r in avaliadas:
            momento = r.momento if r.momento.tzinfo else r.momento.replace(tzinfo=timezone.utc)
            avaliado_em = r.avaliado_em if r.avaliado_em.tzinfo else r.avaliado_em.replace(tzinfo=timezone.utc)
            total_segundos += (avaliado_em - momento).total_seconds()
        tempo_medio = round(total_segundos / len(avaliadas) / 60, 1)

    return RecolhidaAnaliseResponse(
        por_prefixo=_ordenado(por_prefixo),
        por_linha=_ordenado(por_linha),
        por_motorista=_ordenado(por_motorista),
        por_tipo_defeito=_ordenado(por_tipo_defeito),
        por_faixa_horario=_ordenado(por_faixa_horario),
        tempo_medio_avaliacao_minutos=tempo_medio,
    )


# ============================================================================
# REGISTRO — o coração da regra número um deste bloco.
# ============================================================================

@router.post(
    "/recolhidas",
    response_model=RecolhidaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra recolhida anormal — nunca recusa por prefixo/escala/ficha ausentes",
)
def registrar_recolhida(
    payload: RecolhidaCreate, usuario: EscritaRecolhida, db: Annotated[Session, Depends(get_db)]
):
    agora_local = datetime.now(FUSO_OPERACAO)
    data_referencia = agora_local.date()

    onibus = _resolver_onibus_por_prefixo(db, payload.prefixo)

    motorista_re = motorista_nome = None
    motorista_id: Optional[UUID] = None
    origem_identificacao = "NAO_IDENTIFICADO"
    if onibus is not None:
        motorista = _resolver_motorista_pela_escala(db, onibus.id, data_referencia, agora_local.time())
        if motorista is not None:
            motorista_re = motorista.re
            motorista_nome = motorista.nome
            motorista_id = motorista.id
            origem_identificacao = "ESCALA"
    # ⚠️ cobrador_re/cobrador_nome ficam sempre NULL — a escala não tem
    # campo de cobrador, não há de onde resolver (ver docstring do módulo).

    ficha_id, ficha_falhou_motivo = abrir_ficha_de_recolhida(
        db,
        onibus_id=onibus.id if onibus is not None else None,
        motorista_id=motorista_id,
        tipo_defeito_codigo=payload.tipo_defeito_codigo,
        relato=payload.relato,
    )

    nova = RecolhidaAnormal(
        local_codigo=payload.local_codigo,
        data_referencia=data_referencia,
        prefixo=payload.prefixo,
        onibus_id=onibus.id if onibus is not None else None,
        linha_codigo=payload.linha_codigo,
        tipo_defeito_codigo=payload.tipo_defeito_codigo,
        relato=payload.relato,
        motorista_re=motorista_re,
        motorista_nome=motorista_nome,
        origem_identificacao=origem_identificacao,
        ficha_id=ficha_id,
        ficha_falhou_motivo=ficha_falhou_motivo,
        registrado_por=usuario.id,
    )
    db.add(nova)
    db.commit()
    db.refresh(nova)
    return nova


@router.get(
    "/recolhidas",
    response_model=list[RecolhidaRead],
    summary="Histórico com filtros (status, data)",
)
def listar_recolhidas(
    usuario: LeituraRecolhida,
    db: Annotated[Session, Depends(get_db)],
    status_filtro: Optional[StatusRecolhida] = Query(None, alias="status"),
    data: Optional[date] = None,
):
    stmt = select(RecolhidaAnormal)
    if status_filtro:
        stmt = stmt.where(RecolhidaAnormal.status == status_filtro)
    if data:
        stmt = stmt.where(RecolhidaAnormal.data_referencia == data)
    stmt = stmt.order_by(RecolhidaAnormal.momento.desc())
    return db.execute(stmt).scalars().all()


@router.patch(
    "/recolhidas/{recolhida_id}/avaliacao",
    response_model=RecolhidaRead,
    summary="Mecânico avalia: LIBERADO (com prazo) ou RETIDO",
)
def avaliar_recolhida(
    recolhida_id: UUID,
    payload: RecolhidaAvaliacaoRequest,
    usuario: EscritaManutencao,
    db: Annotated[Session, Depends(get_db)],
):
    recolhida = db.get(RecolhidaAnormal, recolhida_id)
    if recolhida is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recolhida não encontrada")

    recolhida.avaliacao = payload.avaliacao
    recolhida.prazo_minutos = payload.prazo_minutos if payload.avaliacao == "LIBERADO" else None
    recolhida.avaliacao_relato = payload.avaliacao_relato
    recolhida.avaliado_por = usuario.id
    recolhida.avaliado_em = datetime.now(timezone.utc)
    recolhida.status = "AVALIADA"

    db.commit()
    db.refresh(recolhida)
    return recolhida
