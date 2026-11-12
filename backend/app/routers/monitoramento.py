"""Monitoramento ao vivo da linha (SIM SPTrans + NIMER → sistema → fiscal e coordenação).

Robô (extensão do Chrome no PC da coordenação). ⛔ Sem JWT: autentica pelo
header X-Monitor-Token (MONITOR_INGEST_TOKEN do .env). Token vazio na config
= recebimento DESLIGADO (503). Sem CORS: o robô envia pelo service worker.
  POST /monitoramento/leituras — posições do SIM, a cada minuto, de todas as
    linhas de monitoramento.linha_monitorada (054).
  POST /monitoramento/nimer — Relatório de Linha do NIMER, a cada 3 min,
    uma linha por envio.
  POST /monitoramento/nimer/fiscais — Escala de Fiscais do NIMER (pegada,
    local, batida de início com foto) — abre o turno do fiscal sozinho.
  POST /monitoramento/rastro — Rastro do SIM (pontos a cada 10–15 s) de um
    carro em dúvida; o robô pega a lista em /robo/config (carros_em_duvida).
  GET  /monitoramento/robo/config — quais linhas acompanhar.

GET /monitoramento/linhas, /linhas/{codigo}/tracado, /linhas/{codigo}/ao-vivo
e /linhas/{codigo}/carros/{prefixo}/rastro — telas do fiscal (recurso
`fiscalizacao`) e da coordenação (`fiscalizacao_painel`).
"""
import json
import logging
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import exige, exige_qualquer
from app.core.linha import codigo_linha
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno
from app.models.monitoramento import (
    AlertaTratativa,
    Coleta,
    LeituraVeiculo,
    NimerColeta,
    NimerViagem,
    RastroColeta,
    SimCodigoLinha,
    ViagemCorrecao,
)
from app.services import fiscalizacao_alertas as alertas_op
from app.services import fiscalizacao_tempo_real as tempo_real
from app.services import monitoramento_acesso as acesso
from app.services import monitoramento_ao_vivo as ao_vivo
from app.services.fechamento_fiscal import montar_fechamento_tempo_real
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
from app.services.monitoramento_nimer import (
    LIMITE_VIAGENS,
    gravar_coleta_nimer,
    normalizar_carro,
    parse_data_servico,
)

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
        "fonte": "leitura",
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
    # Antes de as leituras saírem (3 dias), os dias fechados vão para o
    # histórico de velocidade da previsão de chegada — numa thread própria,
    # o robô não espera. Só no Postgres (a thread abre a própria sessão).
    if db.bind.dialect.name == "postgresql":
        from app.services.monitoramento_eta import agendar_agregacao
        agendar_agregacao()
    db.execute(delete(LeituraVeiculo).where(LeituraVeiculo.posicao_em < agora - RETENCAO_LEITURA))
    db.execute(delete(Coleta).where(Coleta.recebido_em < agora - RETENCAO_COLETA))
    db.execute(delete(NimerColeta).where(NimerColeta.recebido_em < agora - RETENCAO_COLETA))
    db.execute(delete(RastroColeta).where(RastroColeta.recebido_em < agora - RETENCAO_COLETA))


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


# ─── Escala de fiscais do NIMER (v4.0 fase 3, bloco A) ──────────────────────

class PayloadFiscais(BaseModel):
    data_servico: Optional[str] = None
    coletado_em: Optional[int | float | str] = None
    fiscais: list[Any] = []


def _gravar_fiscais(db: Session, payload: PayloadFiscais) -> dict:
    from app.services.monitoramento_fiscais import gravar_escala
    agora = datetime.now(timezone.utc)
    data_servico = parse_data_servico(payload.data_servico) or data_servico_de(agora)
    resultado = gravar_escala(db, data_servico, payload.fiscais, _instante_robo(payload.coletado_em), agora)
    db.commit()
    return resultado


@router.post(
    "/nimer/fiscais",
    summary="Recebe a Escala de Fiscais do NIMER (pegada, local, batida com foto) — X-Monitor-Token, sem JWT",
)
async def receber_fiscais_nimer(request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services.monitoramento_fiscais import LIMITE_FISCAIS
    _exige_token_robo(request)
    corpo = await _corpo_limitado(request, LIMITE_CORPO_NIMER_BYTES)
    try:
        payload = PayloadFiscais.model_validate(json.loads(corpo or b"{}"))
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Envio da escala de fiscais em formato inválido")
    if len(payload.fiscais) > LIMITE_FISCAIS:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Fiscais demais num envio (máximo {LIMITE_FISCAIS})")
    return await run_in_threadpool(_gravar_fiscais, db, payload)


# ─── Configuração do robô ────────────────────────────────────────────────────

def _config_robo(db: Session) -> dict:
    from app.services import monitoramento_rastro as rastro
    agora = datetime.now(timezone.utc)
    ativas = linhas_ativas(db)
    data_servico = data_servico_de(agora)
    return {
        "data_servico": data_servico.isoformat(),
        "linhas": [{"linha_codigo": codigo, "nimer_line_id": nimer} for codigo, nimer in ativas.items()],
        "intervalos": {"sim_seg": INTERVALO_SIM_SEG, "nimer_seg": INTERVALO_NIMER_SEG,
                       "rastro_seg": rastro.INTERVALO_RASTRO_SEG},
        # Carros (4 dígitos) escalados hoje no NIMER: o robô também manda
        # quando aparecem em OUTRA linha (alerta "carro trocado de linha").
        "prefixos_escalados": sorted(prefixos_escalados(db, data_servico, list(ativas))),
        # v4.0 fase 3 (bloco B): o robô puxa o Rastro do SIM só destes, um por
        # vez, a janela desde → até (máx. 15, o mais antigo primeiro).
        "carros_em_duvida": rastro.carros_em_duvida_com_cache(db),
    }


@router.get("/robo/config", summary="Linhas que o robô acompanha (X-Monitor-Token, sem JWT)")
def config_robo(request: Request, db: Annotated[Session, Depends(get_db)]):
    _exige_token_robo(request)
    return _config_robo(db)


# ─── Rastro do SIM (v4.0 fase 3, bloco B) ────────────────────────────────────

class PayloadRastro(BaseModel):
    prefixo: str = Field(..., max_length=10)
    desde: Optional[int | float | str] = None
    ate: Optional[int | float | str] = None
    status: Optional[str] = "ok"
    pontos: list[Any] = []

    @field_validator("prefixo")
    @classmethod
    def _prefixo(cls, v):
        v = str(v).strip()
        if not v.isdigit():
            raise ValueError("prefixo inválido")
        return v


def _gravar_rastro(db: Session, payload: PayloadRastro) -> dict:
    from app.services import monitoramento_rastro as rastro
    agora = datetime.now(timezone.utc)
    mapa = mapa_sim(db)
    vistos: set = set()
    linhas: list[dict] = []
    for bruto in payload.pontos:
        ponto = rastro.ponto_do_rastro(payload.prefixo, bruto, mapa, agora)
        if ponto is None or ponto["posicao_em"] in vistos:
            continue
        vistos.add(ponto["posicao_em"])
        linhas.append(ponto)
    gravados = _insert_ignorando_duplicado(db, linhas)
    status_coleta = (payload.status or "").strip().lower()
    if status_coleta not in STATUS_COLETA:
        status_coleta = "erro"
    instantes = [p["posicao_em"] for p in linhas]
    db.add(RastroColeta(
        recebido_em=agora, prefixo=payload.prefixo, status=status_coleta,
        desde=_instante_robo(payload.desde) or (min(instantes) if instantes else None),
        ate=_instante_robo(payload.ate) or (max(instantes) if instantes else None),
        pontos=len(payload.pontos), gravados=gravados,
    ))
    db.commit()
    rastro.limpar_cache()
    return {"prefixo": payload.prefixo, "recebidos": len(payload.pontos), "aceitos": len(linhas),
            "gravados": gravados, "status": status_coleta}


@router.post("/rastro", summary="Recebe o Rastro do SIM de um carro em dúvida (X-Monitor-Token, sem JWT)")
async def receber_rastro(request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services.monitoramento_rastro import LIMITE_PONTOS
    _exige_token_robo(request)
    corpo = await _corpo_limitado(request, LIMITE_CORPO_BYTES)
    try:
        payload = PayloadRastro.model_validate(json.loads(corpo or b"{}"))
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Envio do Rastro em formato inválido")
    if len(payload.pontos) > LIMITE_PONTOS:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Pontos demais num envio (máximo {LIMITE_PONTOS})")
    return await run_in_threadpool(_gravar_rastro, db, payload)


# ─── Leitura para as telas ───────────────────────────────────────────────────

def _linha_ou_404(db: Session, linha_codigo: str) -> LinhaMonitorada:
    linha = obter_linha(linha_codigo, db)
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"A linha {linha_codigo} não é monitorada ao vivo")
    return linha


def _linha_autorizada(db: Session, usuario: Funcionario, linha_codigo: str) -> LinhaMonitorada:
    """Bloco H — a regra de quem vê qual linha mora AQUI, no backend:
    fiscal só as do turno aberto (sem turno → 403), coordenação as que
    coordena, ADMIN todas. Ver app/services/monitoramento_acesso.py."""
    return _linha_e_escopo(db, usuario, linha_codigo)[0]


def _linha_e_escopo(db: Session, usuario: Funcionario, linha_codigo: str) -> tuple[LinhaMonitorada, dict]:
    esc = acesso.escopo(db, usuario.id)
    if esc["tipo"] == "sem_turno":
        raise HTTPException(status.HTTP_403_FORBIDDEN, acesso.MSG_SEM_TURNO)
    linha = _linha_ou_404(db, linha_codigo)
    if esc["linhas"] is not None and linha.codigo not in esc["linhas"]:
        mensagem = (acesso.MSG_FORA_DO_TURNO if esc["tipo"] in ("turno", "previsto")
                    else acesso.MSG_FORA_DA_COORDENACAO)
        raise HTTPException(status.HTTP_403_FORBIDDEN, mensagem)
    return linha, esc


@router.get("/linhas", summary="Linhas monitoradas ao vivo")
def listar_linhas(usuario: Leitura, db: Annotated[Session, Depends(get_db)]):
    """Só as linhas que a pessoa pode ver (fiscal sem turno aberto: lista vazia)."""
    esc = acesso.escopo(db, usuario.id)
    return [ao_vivo.resumo_linha(linha) for codigo, linha in linhas_monitoradas(db).items()
            if esc["linhas"] is None or codigo in esc["linhas"]]


@router.get("/linhas/{linha_codigo}/tracado", summary="Traçado, terminais e G3 da linha (estático)")
def tracado(linha_codigo: str, response: Response, usuario: Leitura, db: Annotated[Session, Depends(get_db)]):
    linha = _linha_autorizada(db, usuario, linha_codigo)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return ao_vivo.montar_tracado(linha)


@router.get("/linhas/{linha_codigo}/ao-vivo", summary="Carros, alertas e ocorrências da linha (últimas 4 h)")
def ao_vivo_linha(linha_codigo: str, usuario: Leitura, db: Annotated[Session, Depends(get_db)]):
    linha = _linha_autorizada(db, usuario, linha_codigo)
    return ao_vivo.ao_vivo_com_cache(db, linha)


@router.get("/linhas/{linha_codigo}/carros/{prefixo}/rastro", summary="Rastro de um carro (padrão: 90 min)")
def rastro(
    linha_codigo: str,
    prefixo: str,
    usuario: Leitura,
    db: Annotated[Session, Depends(get_db)],
    minutos: int = Query(90, ge=1, le=ao_vivo.RASTRO_MAX_MIN),
):
    linha = _linha_autorizada(db, usuario, linha_codigo)
    if not prefixo.isdigit() or len(prefixo) > 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Prefixo inválido")
    return ao_vivo.montar_rastro(db, linha, prefixo, minutos)


@router.get("/linhas/{linha_codigo}/tempo-real",
            summary="Partidas do dia (NIMER × SIM), ICV das duas fontes, IPP e perda absoluta")
def tempo_real_linha(
    linha_codigo: str,
    usuario: Leitura,
    db: Annotated[Session, Depends(get_db)],
    data: Optional[date] = Query(None, description="data de serviço (padrão: hoje, virada às 4h)"),
):
    linha = _linha_autorizada(db, usuario, linha_codigo)
    data_servico = data or data_servico_de()
    resultado = tempo_real.montar_linha_tempo_real(db, linha, data_servico)
    saida = tempo_real.serializar(resultado, linha, data_servico)
    # Alertas só fazem sentido no dia de serviço corrente (são "agora").
    saida["alertas"] = alertas_op.montar_alertas(db, linha, resultado) if data_servico == data_servico_de() else []
    return saida


@router.get("/linhas/{linha_codigo}/fiscal-ao-vivo",
            summary="Tela do fiscal ao vivo: cabeçalho, números reais, alertas abertos, saindo, chegando e robô")
def fiscal_ao_vivo_linha(
    linha_codigo: str,
    usuario: Leitura,
    db: Annotated[Session, Depends(get_db)],
    terminal: Optional[Literal["TP", "TS"]] = Query(None, description="terminal do fiscal (padrão: o do turno)"),
):
    from app.services import fiscal_ao_vivo
    linha, esc = _linha_e_escopo(db, usuario, linha_codigo)
    return fiscal_ao_vivo.montar(db, linha, esc, terminal)


# ─── Correção pelo fiscal (bloco G) ──────────────────────────────────────────
# ⛔ Nunca escreve no NIMER (só leitura): a correção vive em
# monitoramento.viagem_correcao e muda o STATUS EFETIVO aqui dentro.

EscritaFiscal = Annotated[Funcionario, Depends(exige("fiscalizacao", escrever=True))]
LeituraPainel = Annotated[Funcionario, Depends(exige("fiscalizacao_painel"))]
STATUS_CORRIGIVEIS = {"done": "realizada", "lost": "perdida"}


class CorrecaoCreate(BaseModel):
    campo: Literal["status", "motivo", "carro", "real_inicio"]
    valor: str = Field(..., max_length=120)
    justificativa: str = Field(..., max_length=500)

    @field_validator("justificativa")
    @classmethod
    def _justificativa(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("A justificativa é obrigatória")
        return v


def _valor_corrigido(campo: str, valor: str, viagem: NimerViagem) -> str:
    v = (valor or "").strip()
    if campo == "status":
        if v not in STATUS_CORRIGIVEIS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Status tem que ser done (realizada) ou lost (perdida)")
        return v
    if campo == "motivo":
        if v not in tempo_real.MOTIVOS_CORRIGIVEIS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Motivo inválido — use: " + ", ".join(tempo_real.MOTIVOS_CORRIGIVEIS))
        return v
    if campo == "carro":
        carro = normalizar_carro(v)
        if not carro or not (3 <= len(carro) <= 5):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Carro inválido (prefixo do pátio, ex.: 2412)")
        return carro
    try:
        hhmm = time.fromisoformat(v)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Horário inválido (use HH:MM)")
    return tempo_real.instante_do_horario(viagem.data_servico, hhmm.replace(second=0, microsecond=0)).isoformat()


def _valor_nimer(campo: str, viagem: NimerViagem) -> Optional[str]:
    if campo == "status":
        return viagem.status
    if campo == "motivo":
        return viagem.motivo_perda_texto
    if campo == "carro":
        return viagem.carro
    return viagem.real_inicio.isoformat() if viagem.real_inicio else None


@router.post("/viagens/{viagem_id}/correcoes", status_code=status.HTTP_201_CREATED,
             summary="Fiscal corrige status/motivo/carro/saída de uma viagem do NIMER (sem mexer no NIMER)")
def corrigir_viagem(viagem_id: int, payload: CorrecaoCreate, usuario: EscritaFiscal,
                    db: Annotated[Session, Depends(get_db)]):
    viagem = db.get(NimerViagem, viagem_id)
    if viagem is None or viagem.removida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Viagem não encontrada")
    turno = acesso.turno_aberto(db, usuario.id)
    if turno is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, acesso.MSG_SEM_TURNO)
    if viagem.linha_codigo not in acesso.linhas_do_turno(db, turno.id) or viagem.data_servico != turno.data_referencia:
        raise HTTPException(status.HTTP_403_FORBIDDEN, acesso.MSG_FORA_DO_TURNO)
    corrigido = _valor_corrigido(payload.campo, payload.valor, viagem)
    correcao = ViagemCorrecao(
        nimer_viagem_id=viagem.id, campo=payload.campo,
        valor_nimer=_corte(_valor_nimer(payload.campo, viagem), 120),
        valor_corrigido=corrigido, justificativa=payload.justificativa, autor_funcionario_id=usuario.id,
        criado_em=datetime.now(timezone.utc),
    )
    db.add(correcao)
    db.commit()
    ao_vivo.limpar_cache()
    return {"id": correcao.id, "viagem_id": viagem.id, "campo": correcao.campo, "valor_nimer": correcao.valor_nimer,
            "valor_corrigido": correcao.valor_corrigido, "justificativa": correcao.justificativa,
            "criado_em": correcao.criado_em.isoformat(), "aviso": "O NIMER não foi alterado (só leitura)."}


def auditoria_correcoes(db: Session, data_servico: date, linhas: Optional[list[str]]) -> list[dict]:
    query = (
        select(ViagemCorrecao, NimerViagem, Funcionario)
        .join(NimerViagem, NimerViagem.id == ViagemCorrecao.nimer_viagem_id)
        .join(Funcionario, Funcionario.id == ViagemCorrecao.autor_funcionario_id)
        .where(NimerViagem.data_servico == data_servico)
        .order_by(ViagemCorrecao.criado_em.desc(), ViagemCorrecao.id.desc())
    )
    if linhas is not None:
        query = query.where(NimerViagem.linha_codigo.in_(linhas))
    saida = []
    for c, v, f in db.execute(query).all():
        criado = tempo_real._utc(c.criado_em)
        saida.append({
            "id": c.id, "criado_em": criado.isoformat(), "hora": tempo_real.hora_local(criado),
            "autor": {"re": f.re, "nome": f.nome}, "linha_codigo": v.linha_codigo, "tabela": v.tabela,
            "programado": tempo_real.hora_local(tempo_real._utc(v.prog_inicio)), "campo": c.campo,
            "de": c.valor_nimer, "para": c.valor_corrigido, "justificativa": c.justificativa,
        })
    return saida


@router.get("/correcoes", summary="Correções dos fiscais do dia (auditoria da coordenação)")
def listar_correcoes(
    usuario: LeituraPainel,
    db: Annotated[Session, Depends(get_db)],
    data: Optional[date] = Query(None),
    linha_codigo: Optional[str] = Query(None),
):
    linhas = None
    if linha_codigo:
        canon = codigo_linha(linha_codigo)
        linhas = [canon] if canon else []
    return auditoria_correcoes(db, data or data_servico_de(), linhas)


@router.get("/turnos/{turno_id}/fechamento-previa",
            summary="Prévia do fechamento do WhatsApp pelo status efetivo (NIMER + correções)")
def fechamento_previa(turno_id: UUID, usuario: Leitura, db: Annotated[Session, Depends(get_db)]):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Turno não encontrado")
    if turno.funcionario_id != usuario.id and not acesso.tem_acesso(db, usuario.id, "fiscalizacao_painel"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Este turno pertence a outro fiscal.")
    return montar_fechamento_tempo_real(db, turno_id)


# ─── Central da Fiscalização (bloco I) ───────────────────────────────────────

EscritaPainel = Annotated[Funcionario, Depends(exige("fiscalizacao_painel", escrever=True))]


def _linhas_da_central(db: Session, usuario: Funcionario, todas: bool) -> tuple[list[LinhaMonitorada], str]:
    """ADMIN: todas. Coordenação: as linhas que coordena (ou todas, se pedir
    ou se ainda não tiver nenhuma atribuída)."""
    monitoradas = linhas_monitoradas(db)
    if todas or acesso.eh_admin(db, usuario.id):
        return list(monitoradas.values()), "todas"
    minhas = set(acesso.linhas_do_coordenador(db, usuario.id))
    escolhidas = [linha for codigo, linha in monitoradas.items() if codigo in minhas]
    if not escolhidas:
        return list(monitoradas.values()), "todas (sem linha atribuída ao coordenador)"
    return escolhidas, "coordenador"


@router.get("/central", summary="Central da Fiscalização: placar, fila de ação, Pareto, cobertura, correções e robôs")
def central(
    usuario: LeituraPainel,
    db: Annotated[Session, Depends(get_db)],
    todas: bool = Query(False, description="todas as linhas monitoradas, não só as do coordenador"),
):
    from app.services import fiscalizacao_central as servico_central
    from app.services.icv import _parametros

    linhas, escopo_txt = _linhas_da_central(db, usuario, todas)
    data_servico = data_servico_de()

    def fabricar():
        saida = servico_central.montar_central(
            db, linhas, data_servico, _parametros(db),
            auditoria_correcoes(db, data_servico, [linha.codigo for linha in linhas]),
        )
        saida["escopo"] = escopo_txt
        return saida

    chave = (data_servico, tuple(sorted(linha.codigo for linha in linhas)))
    return servico_central.com_cache(chave, fabricar)


# ─── Baixa do alerta (v4.0 fase 3, T4) ──────────────────────────────────────

class BaixaAlerta(BaseModel):
    linha_codigo: str
    chave: str = Field(..., min_length=3, max_length=160)
    motivo: Literal["alarme_falso", "atitude_tomada", "contatou_cco"]


def _perfil_da_baixa(db: Session, usuario: Funcionario, linha: LinhaMonitorada) -> str:
    """coordenação (ADMIN ou escrita em fiscalizacao_painel) ou o fiscal com a
    linha no turno ABERTO (o `previsto`, sem foto no NIMER, só lê)."""
    if acesso.eh_admin(db, usuario.id) or acesso.tem_acesso(db, usuario.id, "fiscalizacao_painel", escrever=True):
        return "coordenacao"
    esc = acesso.escopo(db, usuario.id)
    if esc["tipo"] != "turno":
        raise HTTPException(status.HTTP_403_FORBIDDEN, acesso.MSG_SEM_TURNO)
    if linha.codigo not in (esc["linhas"] or []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, acesso.MSG_FORA_DO_TURNO)
    if not acesso.tem_acesso(db, usuario.id, "fiscalizacao", escrever=True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem permissão para dar baixa em alerta")
    return "fiscal"


@router.post("/alertas/baixa", summary="Baixa de alerta: alarme falso · atitude tomada · contatei CCO/coordenação")
def baixa_alerta(payload: BaixaAlerta, usuario: Leitura, db: Annotated[Session, Depends(get_db)]):
    from app.services import fiscalizacao_central as servico_central
    linha = _linha_ou_404(db, payload.linha_codigo)
    perfil = _perfil_da_baixa(db, usuario, linha)
    agora = datetime.now(timezone.utc)
    data_servico = data_servico_de(agora)
    reabre = None if payload.motivo == "alarme_falso" else agora + alertas_op.REABRE_EM
    existente = db.execute(select(AlertaTratativa).where(
        AlertaTratativa.data_servico == data_servico, AlertaTratativa.chave == payload.chave)).scalar_one_or_none()
    if existente is None:
        db.add(AlertaTratativa(data_servico=data_servico, linha_codigo=linha.codigo, chave=payload.chave,
                               autor_funcionario_id=usuario.id, criado_em=agora, motivo=payload.motivo,
                               perfil=perfil, reabre_em=reabre))
    else:
        # Uma linha por (dia, chave): a baixa nova (do alerta que voltou) substitui a anterior.
        existente.motivo, existente.perfil, existente.reabre_em = payload.motivo, perfil, reabre
        existente.autor_funcionario_id, existente.criado_em = usuario.id, agora
    db.commit()
    servico_central.limpar_cache()
    return {"chave": payload.chave, "motivo": payload.motivo, "perfil": perfil,
            "reabre_em": reabre.isoformat() if reabre else None}


class AcaoAlerta(BaseModel):
    chave: str = Field(..., min_length=3, max_length=160)
    linha_codigo: str
    inicio_em: Optional[datetime] = None
    descricao: Optional[str] = Field(None, max_length=1000)
    resolver: bool = False


@router.post("/central/alertas", summary="Registrar ação (D26) e/ou marcar alerta como resolvido")
def tratar_alerta(payload: AcaoAlerta, usuario: EscritaPainel, db: Annotated[Session, Depends(get_db)]):
    from app.models.fiscalizacao import AcaoCoordenacao
    from app.services import fiscalizacao_central as servico_central

    linha = _linha_ou_404(db, payload.linha_codigo)
    descricao = (payload.descricao or "").strip()
    if not descricao and not payload.resolver:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Descreva a ação ou marque como resolvido")
    data_servico = data_servico_de()
    acao_id = None
    if descricao:
        inicio = tempo_real._utc(payload.inicio_em) if payload.inicio_em else datetime.now(timezone.utc)
        acao = AcaoCoordenacao(
            linha_codigo=linha.codigo, data_referencia=data_servico,
            faixa_hora=inicio.astimezone(tempo_real.FUSO_OPERACAO).hour,
            descricao=f"[Central · {payload.chave.split('|')[0]}] {descricao}", registrado_por=usuario.id,
        )
        db.add(acao)
        db.flush()
        acao_id = acao.id
    resolvido = False
    if payload.resolver:
        existente = db.execute(select(AlertaTratativa).where(
            AlertaTratativa.data_servico == data_servico, AlertaTratativa.chave == payload.chave)).scalar_one_or_none()
        if existente is None:
            db.add(AlertaTratativa(data_servico=data_servico, linha_codigo=linha.codigo, chave=payload.chave,
                                   acao_coordenacao_id=acao_id, autor_funcionario_id=usuario.id,
                                   criado_em=datetime.now(timezone.utc)))
        elif acao_id and existente.acao_coordenacao_id is None:
            existente.acao_coordenacao_id = acao_id
        resolvido = True
    db.commit()
    servico_central.limpar_cache()
    return {"chave": payload.chave, "acao_coordenacao_id": str(acao_id) if acao_id else None, "resolvido": resolvido}
