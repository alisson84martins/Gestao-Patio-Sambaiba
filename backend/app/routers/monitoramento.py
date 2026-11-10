"""Monitoramento ao vivo da linha (SIM SPTrans → sistema → fiscal e coordenação).

POST /monitoramento/leituras — o robô (extensão do Chrome no PC da
  coordenação, logada no SIM) envia as posições a cada minuto. ⛔ Sem JWT:
  autentica pelo header X-Monitor-Token (MONITOR_INGEST_TOKEN do .env).
  Token vazio na config = recebimento DESLIGADO (503). Sem CORS: o robô
  envia pelo service worker da extensão.

GET /monitoramento/linhas, /linhas/{codigo}/tracado, /linhas/{codigo}/ao-vivo
e /linhas/{codigo}/carros/{prefixo}/rastro — telas do fiscal (recurso
`fiscalizacao`) e da coordenação (`fiscalizacao_painel`).
"""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import exige_qualquer
from app.models.cadastro import Funcionario
from app.models.monitoramento import Coleta, LeituraVeiculo
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.monitoramento_linhas import (
    LinhaMonitorada, linha_do_sim, linhas_monitoradas, obter_linha,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoramento", tags=["monitoramento"])

# Fiscal tem `fiscalizacao`; coordenador tem `fiscalizacao_painel`.
Leitura = Annotated[Funcionario, Depends(exige_qualquer("fiscalizacao", "fiscalizacao_painel"))]

LIMITE_CORPO_BYTES = 2 * 1024 * 1024
LIMITE_VEICULOS = 2000
RETENCAO_LEITURA = timedelta(days=3)
RETENCAO_COLETA = timedelta(days=7)
# Limpeza de retenção a cada N recebimentos (o robô envia ~1/min → ~1/hora),
# e sempre no 1º recebimento depois que o processo sobe.
LIMPEZA_A_CADA = 60
STATUS_COLETA = {"ok", "sessao", "erro"}

_contador_recebimentos = 0


# ─── Recebimento do robô ─────────────────────────────────────────────────────

class VeiculoSim(BaseModel):
    """Um carro como o SIM devolve (o robô repassa sem traduzir)."""
    p: int | str
    li: int
    l: Optional[str] = None  # noqa: E741 — nome do campo no SIM
    s: Optional[int] = None
    la: float
    lo: float
    vel: Optional[float] = None
    loc: Optional[str] = None
    d: int  # hora do GPS, epoch ms

    @field_validator("p")
    @classmethod
    def _prefixo(cls, v):
        v = str(v).strip()
        if not v.isdigit() or len(v) > 10:
            raise ValueError("prefixo inválido")
        return v


class PayloadLeituras(BaseModel):
    origem: Optional[str] = None
    status: Optional[str] = "ok"
    mensagem: Optional[str] = None
    veiculos: list[Any] = []


def _token_configurado() -> str:
    return (get_settings().monitor_ingest_token or "").strip()


def _corte(texto: Optional[str], n: int) -> Optional[str]:
    if texto is None:
        return None
    texto = str(texto).strip()
    return texto[:n] or None


def _leitura_do_veiculo(bruto: Any) -> Optional[dict]:
    """Veículo do SIM → linha de leitura_veiculo, ou None se não for de linha
    monitorada (descarte em silêncio) ou vier quebrado."""
    try:
        v = VeiculoSim.model_validate(bruto)
    except ValidationError:
        return None
    linha = linha_do_sim(v.li)
    if linha is None:
        return None
    if not (-90 <= v.la <= 90 and -180 <= v.lo <= 180) or (v.la == 0 and v.lo == 0):
        return None
    try:
        posicao_em = datetime.fromtimestamp(v.d / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    # O sentido que as regras usam é o `s` do SIM (igual à POC); sem ele,
    # vale o sentido do código linha-sentido.
    sentido = v.s if v.s in (1, 2) else linha.sentido_do_sim(v.li)
    vel = None if v.vel is None else max(0, min(int(round(v.vel)), 32767))
    return {
        "posicao_em": posicao_em,
        "prefixo": v.p,
        "linha_codigo": linha.codigo,
        "sim_codigo": v.li,
        "sentido": sentido,
        "lat": v.la,
        "lon": v.lo,
        "velocidade": vel,
        "local_sim": _corte(v.loc, 120),
    }


def _insert_ignorando_duplicado(db: Session, linhas: list[dict]) -> int:
    if not linhas:
        return 0
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # SQLite dos testes
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(LeituraVeiculo).values(linhas).on_conflict_do_nothing(
        index_elements=["prefixo", "posicao_em"]
    )
    return db.execute(stmt).rowcount or 0


def _limpar_retencao(db: Session, agora: datetime) -> None:
    db.execute(delete(LeituraVeiculo).where(LeituraVeiculo.posicao_em < agora - RETENCAO_LEITURA))
    db.execute(delete(Coleta).where(Coleta.recebido_em < agora - RETENCAO_COLETA))


def _gravar(db: Session, payload: PayloadLeituras) -> dict:
    global _contador_recebimentos
    agora = datetime.now(timezone.utc)

    vistos: set[tuple] = set()
    linhas: list[dict] = []
    for bruto in payload.veiculos:
        linha = _leitura_do_veiculo(bruto)
        if linha is None:
            continue
        chave = (linha["prefixo"], linha["posicao_em"])
        if chave in vistos:  # duplicado dentro do mesmo envio
            continue
        vistos.add(chave)
        # Mesmo instante da coleta: é o que a tela usa para saber se o carro
        # "sumiu" da consulta do SIM.
        linha["recebido_em"] = agora
        linhas.append(linha)

    gravados = _insert_ignorando_duplicado(db, linhas)

    status_coleta = (payload.status or "").strip().lower()
    if status_coleta not in STATUS_COLETA:
        status_coleta = "erro"
    db.add(Coleta(
        recebido_em=agora,
        origem=_corte(payload.origem, 40),
        status=status_coleta,
        qtd_veiculos=len(linhas),
        mensagem=_corte(payload.mensagem, 200),
    ))

    if _contador_recebimentos % LIMPEZA_A_CADA == 0:
        _limpar_retencao(db, agora)
    _contador_recebimentos += 1

    db.commit()
    return {
        "recebidos": len(payload.veiculos),
        "aceitos": len(linhas),
        "gravados": gravados,
        "status": status_coleta,
    }


@router.post(
    "/leituras",
    summary="Recebe as posições enviadas pelo robô do SIM (X-Monitor-Token, sem JWT)",
)
async def receber_leituras(request: Request, db: Annotated[Session, Depends(get_db)]):
    esperado = _token_configurado()
    if not esperado:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Recebimento do monitoramento desligado")
    recebido = request.headers.get("x-monitor-token") or ""
    if not secrets.compare_digest(recebido.encode("utf-8"), esperado.encode("utf-8")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token do monitoramento inválido")

    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > LIMITE_CORPO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Envio grande demais (máximo 2 MB)")
    corpo = await request.body()
    if len(corpo) > LIMITE_CORPO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Envio grande demais (máximo 2 MB)")

    try:
        payload = PayloadLeituras.model_validate(json.loads(corpo or b"{}"))
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Envio do robô em formato inválido")
    if len(payload.veiculos) > LIMITE_VEICULOS:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Veículos demais num envio (máximo {LIMITE_VEICULOS}) — filtre pela linha antes de enviar",
        )

    # Banco é síncrono: fora do event loop.
    return await run_in_threadpool(_gravar, db, payload)


# ─── Leitura para as telas ───────────────────────────────────────────────────

def _linha_ou_404(linha_codigo: str) -> LinhaMonitorada:
    linha = obter_linha(linha_codigo)
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"A linha {linha_codigo} não é monitorada ao vivo")
    return linha


@router.get("/linhas", summary="Linhas monitoradas ao vivo")
def listar_linhas(_: Leitura):
    return [ao_vivo.resumo_linha(linha) for linha in linhas_monitoradas().values()]


@router.get("/linhas/{linha_codigo}/tracado", summary="Traçado, terminais e G3 da linha (estático)")
def tracado(linha_codigo: str, response: Response, _: Leitura):
    linha = _linha_ou_404(linha_codigo)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return ao_vivo.montar_tracado(linha)


@router.get("/linhas/{linha_codigo}/ao-vivo", summary="Carros, alertas e ocorrências da linha (últimas 4 h)")
def ao_vivo_linha(linha_codigo: str, _: Leitura, db: Annotated[Session, Depends(get_db)]):
    linha = _linha_ou_404(linha_codigo)
    return ao_vivo.ao_vivo_com_cache(db, linha)


@router.get("/linhas/{linha_codigo}/carros/{prefixo}/rastro", summary="Rastro de um carro (padrão: 90 min)")
def rastro(
    linha_codigo: str,
    prefixo: str,
    _: Leitura,
    db: Annotated[Session, Depends(get_db)],
    minutos: int = Query(90, ge=1, le=ao_vivo.RASTRO_MAX_MIN),
):
    linha = _linha_ou_404(linha_codigo)
    if not prefixo.isdigit() or len(prefixo) > 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Prefixo inválido")
    return ao_vivo.montar_rastro(db, linha, prefixo, minutos)
