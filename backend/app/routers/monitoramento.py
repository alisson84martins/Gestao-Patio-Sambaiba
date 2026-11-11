"""Monitoramento ao vivo da linha (SIM SPTrans + NIMER → sistema → fiscal e coordenação).

Robô (extensão do Chrome no PC da coordenação). ⛔ Sem JWT: autentica pelo
header X-Monitor-Token (MONITOR_INGEST_TOKEN do .env). Token vazio na config
= recebimento DESLIGADO (503). Sem CORS: o robô envia pelo service worker.
  POST /monitoramento/leituras — posições do SIM, a cada minuto, de todas as
    linhas de monitoramento.linha_monitorada (054).
  POST /monitoramento/nimer — Relatório de Linha do NIMER, a cada 3 min,
    uma linha por envio.
  GET  /monitoramento/robo/config — quais linhas acompanhar.

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
from app.core.linha import codigo_linha
from app.models.cadastro import Funcionario
from app.models.monitoramento import Coleta, LeituraVeiculo, NimerColeta, SimCodigoLinha
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.monitoramento_cadastro import (
    data_servico_de,
    linhas_ativas,
    mapa_sim,
    prefixos_escalados,
)
from app.services.monitoramento_linhas import (
    LinhaMonitorada,
    linhas_monitoradas,
    obter_linha,
    prefixo_patio,
)
from app.services.monitoramento_nimer import LIMITE_VIAGENS, gravar_coleta_nimer, parse_data_servico

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoramento", tags=["monitoramento"])

# Fiscal tem `fiscalizacao`; coordenador tem `fiscalizacao_painel`.
Leitura = Annotated[Funcionario, Depends(exige_qualquer("fiscalizacao", "fiscalizacao_painel"))]

LIMITE_CORPO_BYTES = 2 * 1024 * 1024
LIMITE_CORPO_NIMER_BYTES = 3 * 1024 * 1024  # timeline sem o `html` (~850 KB com ele)
LIMITE_VEICULOS = 2000
INTERVALO_SIM_SEG = 60
INTERVALO_NIMER_SEG = 180
RETENCAO_LEITURA = timedelta(days=3)
RETENCAO_COLETA = timedelta(days=7)
# Limpeza de retenção a cada N recebimentos (o robô envia ~1/min → ~1/hora),
# e sempre no 1º recebimento depois que o processo sobe.
LIMPEZA_A_CADA = 60
STATUS_COLETA = {"ok", "sessao", "erro"}

_contador_recebimentos = 0


# ─── Autenticação do robô ────────────────────────────────────────────────────

def _token_configurado() -> str:
    return (get_settings().monitor_ingest_token or "").strip()


def _exige_token_robo(request: Request) -> None:
    """503 com o recebimento desligado (token vazio no .env), 401 se errado."""
    esperado = _token_configurado()
    if not esperado:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Recebimento do monitoramento desligado")
    recebido = request.headers.get("x-monitor-token") or ""
    if not secrets.compare_digest(recebido.encode("utf-8"), esperado.encode("utf-8")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token do monitoramento inválido")


async def _corpo_limitado(request: Request, limite: int) -> bytes:
    mensagem = f"Envio grande demais (máximo {limite // (1024 * 1024)} MB)"
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > limite:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, mensagem)
    corpo = await request.body()
    if len(corpo) > limite:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, mensagem)
    return corpo


def _insert_dialeto(db: Session):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # SQLite dos testes
        from sqlalchemy.dialects.sqlite import insert
    return insert


def _corte(texto: Optional[str], n: int) -> Optional[str]:
    if texto is None:
        return None
    texto = str(texto).strip()
    return texto[:n] or None


# ─── Recebimento do SIM ──────────────────────────────────────────────────────

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


class _ContextoSim:
    """O que o recebimento precisa do banco, lido uma vez por envio."""

    def __init__(self, db: Session, agora: datetime):
        self.ativas = linhas_ativas(db)
        self.mapa = mapa_sim(db)
        self.escalados = prefixos_escalados(db, data_servico_de(agora), list(self.ativas))
        self.novos_codigos: dict[int, tuple[str, int]] = {}
        self.codigos_vistos: set[int] = set()


def _leitura_do_veiculo(bruto: Any, ctx: _ContextoSim) -> Optional[dict]:
    """Veículo do SIM → linha de leitura_veiculo, ou None (descarte em
    silêncio) quando vem quebrado ou não interessa.

    Interessa: (a) carro de linha monitorada — pelo `li` já conhecido em
    sim_codigo_linha ou, se o `li` é novo, pelo `l` do próprio SIM (e o `li`
    é aprendido); (b) carro escalado hoje no NIMER numa linha monitorada que
    aparece em OUTRA linha (alerta "carro trocado de linha") — gravado com a
    linha em que ele está de fato."""
    try:
        v = VeiculoSim.model_validate(bruto)
    except ValidationError:
        return None
    if not (-90 <= v.la <= 90 and -180 <= v.lo <= 180) or (v.la == 0 and v.lo == 0):
        return None
    conhecido = ctx.mapa.get(v.li)
    linha = conhecido[0] if conhecido else (codigo_linha(v.l) if v.l else None)
    if linha is None:
        return None
    monitorada = linha in ctx.ativas
    if not monitorada and prefixo_patio(v.p) not in ctx.escalados:
        return None
    # O sentido que as regras usam é o `s` do SIM (igual à POC); sem ele,
    # vale o sentido do código linha-sentido.
    sentido = v.s if v.s in (1, 2) else (conhecido[1] if conhecido else None)
    if sentido not in (1, 2):
        return None
    try:
        posicao_em = datetime.fromtimestamp(v.d / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if monitorada:
        if conhecido is None:
            ctx.novos_codigos[v.li] = (linha, sentido)
        else:
            ctx.codigos_vistos.add(v.li)
    vel = None if v.vel is None else max(0, min(int(round(v.vel)), 32767))
    return {
        "posicao_em": posicao_em,
        "prefixo": v.p,
        "linha_codigo": linha,
        "sim_codigo": v.li,
        "sentido": sentido,
        "lat": v.la,
        "lon": v.lo,
        "velocidade": vel,
        "local_sim": _corte(v.loc, 120),
    }


def _aprender_codigos(db: Session, ctx: _ContextoSim, agora: datetime) -> None:
    """sim_codigo_linha se preenche sozinho com o `li` de cada linha monitorada."""
    if ctx.novos_codigos:
        insert = _insert_dialeto(db)
        valores = [{"sim_codigo": li, "linha_codigo": linha, "sentido": s, "visto_em": agora}
                   for li, (linha, s) in ctx.novos_codigos.items()]
        db.execute(insert(SimCodigoLinha).values(valores).on_conflict_do_nothing(index_elements=["sim_codigo"]))
    if ctx.codigos_vistos:
        db.execute(
            SimCodigoLinha.__table__.update()
            .where(SimCodigoLinha.sim_codigo.in_(ctx.codigos_vistos))
            .values(visto_em=agora)
        )


def _insert_ignorando_duplicado(db: Session, linhas: list[dict]) -> int:
    if not linhas:
        return 0
    insert = _insert_dialeto(db)
    stmt = insert(LeituraVeiculo).values(linhas).on_conflict_do_nothing(
        index_elements=["prefixo", "posicao_em"]
    )
    return db.execute(stmt).rowcount or 0


def _limpar_retencao(db: Session, agora: datetime) -> None:
    db.execute(delete(LeituraVeiculo).where(LeituraVeiculo.posicao_em < agora - RETENCAO_LEITURA))
    db.execute(delete(Coleta).where(Coleta.recebido_em < agora - RETENCAO_COLETA))
    db.execute(delete(NimerColeta).where(NimerColeta.recebido_em < agora - RETENCAO_COLETA))


def _gravar(db: Session, payload: PayloadLeituras) -> dict:
    global _contador_recebimentos
    agora = datetime.now(timezone.utc)
    ctx = _ContextoSim(db, agora)

    vistos: set[tuple] = set()
    linhas: list[dict] = []
    for bruto in payload.veiculos:
        linha = _leitura_do_veiculo(bruto, ctx)
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
    _aprender_codigos(db, ctx, agora)

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
    _exige_token_robo(request)
    corpo = await _corpo_limitado(request, LIMITE_CORPO_BYTES)
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


# ─── Recebimento do NIMER ────────────────────────────────────────────────────

class PayloadNimer(BaseModel):
    linha_codigo: str
    data_servico: Optional[str] = None
    coletado_em: Optional[int | float | str] = None
    origem: Optional[str] = None
    status: Optional[str] = "ok"
    mensagem: Optional[str] = None
    timeline: Optional[dict] = None


def _instante_robo(valor) -> Optional[datetime]:
    if valor is None:
        return None
    try:
        if isinstance(valor, (int, float)):
            return datetime.fromtimestamp(valor / 1000, tz=timezone.utc)
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _gravar_nimer(db: Session, payload: PayloadNimer) -> dict:
    agora = datetime.now(timezone.utc)
    linha = codigo_linha(payload.linha_codigo)
    if linha is None or linha not in linhas_ativas(db):
        # Linha fora de linha_monitorada (ou desativada): descarta sem gravar.
        return {"linha_codigo": payload.linha_codigo, "status": "descartada", "viagens": 0}
    data_servico = parse_data_servico(payload.data_servico) or data_servico_de(agora)
    status_coleta = (payload.status or "").strip().lower()
    if status_coleta not in STATUS_COLETA:
        status_coleta = "erro"
    resultado = gravar_coleta_nimer(
        db, linha_codigo=linha, data_servico=data_servico, status=status_coleta,
        timeline=payload.timeline, coletado_em=_instante_robo(payload.coletado_em),
        mensagem=payload.mensagem, agora=agora,
    )
    db.commit()
    return resultado


@router.post(
    "/nimer",
    summary="Recebe o Relatório de Linha do NIMER enviado pelo robô (X-Monitor-Token, sem JWT)",
)
async def receber_nimer(request: Request, db: Annotated[Session, Depends(get_db)]):
    _exige_token_robo(request)
    corpo = await _corpo_limitado(request, LIMITE_CORPO_NIMER_BYTES)
    try:
        payload = PayloadNimer.model_validate(json.loads(corpo or b"{}"))
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Envio do robô do NIMER em formato inválido")
    tabelas = (payload.timeline or {}).get("tables") or []
    if not isinstance(tabelas, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "timeline.tables tem que ser uma lista")
    total = sum(len(t.get("trips") or []) for t in tabelas if isinstance(t, dict))
    if total > LIMITE_VIAGENS:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Viagens demais num envio (máximo {LIMITE_VIAGENS})")
    return await run_in_threadpool(_gravar_nimer, db, payload)


# ─── Configuração do robô ────────────────────────────────────────────────────

def _config_robo(db: Session) -> dict:
    agora = datetime.now(timezone.utc)
    ativas = linhas_ativas(db)
    data_servico = data_servico_de(agora)
    return {
        "data_servico": data_servico.isoformat(),
        "linhas": [{"linha_codigo": codigo, "nimer_line_id": nimer} for codigo, nimer in ativas.items()],
        "intervalos": {"sim_seg": INTERVALO_SIM_SEG, "nimer_seg": INTERVALO_NIMER_SEG},
        # Carros (4 dígitos) escalados hoje no NIMER: o robô também manda
        # quando aparecem em OUTRA linha (alerta "carro trocado de linha").
        "prefixos_escalados": sorted(prefixos_escalados(db, data_servico, list(ativas))),
    }


@router.get("/robo/config", summary="Linhas que o robô acompanha (X-Monitor-Token, sem JWT)")
def config_robo(request: Request, db: Annotated[Session, Depends(get_db)]):
    _exige_token_robo(request)
    return _config_robo(db)


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
