"""Saídas confirmadas pelo Rastro do SIM (v4.0 fase 3, bloco B).

Por quê: o robô lê a posição a cada 60 s e perde a saída de quem entra e sai
do terminal entre duas leituras (11/11: ICV SIM 53% contra ~228 viagens
confirmadas no GPS). O Rastro do SIM tem pontos a cada 10–15 s — mas é
caro, então o robô só puxa o Rastro dos CARROS EM DÚVIDA, um por vez, uma
janela de 20 min, a cada 5 min (decisão T2 do Alisson, 12/11/2026). Parar
quando o SIM pedir login é do lado do robô.

Carro em dúvida (no máximo 15 por ciclo, o mais antigo primeiro, um pedido
por carro):
- viagem programada nos últimos 90 min, já passada, sem saída do SIM casada
  → janela = os últimos 20 min (o carro pode estar saindo agora);
- viagem `done` no NIMER sem saída do SIM casada (qualquer hora do dia)
  → janela = 20 min a partir de (saída do NIMER, senão programada) − 15 min.
Janela já coberta por um envio do Rastro (monitoramento.rastro_coleta) não
é pedida de novo; a dos "últimos 20 min" volta a ser pedida 5 min depois.

Os pontos do Rastro vão para monitoramento.leitura_veiculo com fonte
'rastro' (a UNIQUE prefixo + posicao_em descarta repetido) — inclusive os
de OUTRA linha (é assim que se vê o carro da 1726 rodando na 2011). A
detecção de saída (`saidas_do_sim`) usa as leituras de qualquer fonte.
"""
import logging
import threading
import time as relogio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.monitoramento import LeituraVeiculo, RastroColeta

logger = logging.getLogger(__name__)

INTERVALO_RASTRO_SEG = 300
JANELA_DUVIDA = timedelta(minutes=90)
JANELA_RASTRO = timedelta(minutes=20)
ANTES_DA_SAIDA = timedelta(minutes=15)
MAX_CARROS = 15
FOLGA_COBERTURA = timedelta(seconds=60)
LIMITE_PONTOS = 5000
FONTE_RASTRO = "rastro"
FONTE_LEITURA = "leitura"
CACHE_SEGUNDOS = 60
AREA_SIM = "2"   # SIM = área (2) + carro de 4 dígitos (ver ocorrencias.normalizar_prefixo)

_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ─── Quem está em dúvida ─────────────────────────────────────────────────────

def candidatos_da_linha(viagens: list[dict], agora: datetime) -> list[dict]:
    """Núcleo puro: viagens já cruzadas (saida_sim) → [{carro, desde, ate, motivo}]."""
    saida = []
    for v in viagens:
        prog = v.get("prog_inicio")
        if v.get("duplicada") or v.get("saida_sim") is not None or not v.get("carro") or prog is None:
            continue
        if prog > agora:
            continue
        if v["status_efetivo"] == "done":
            desde = (v.get("real_inicio") or prog) - ANTES_DA_SAIDA
            ate = min(agora, desde + JANELA_RASTRO)
            motivo = "nimer_sem_saida"
        elif prog >= agora - JANELA_DUVIDA:
            desde = max(prog - ANTES_DA_SAIDA, agora - JANELA_RASTRO)
            ate = agora
            motivo = "programada_sem_saida"
        else:
            continue
        if ate > desde:
            saida.append({"carro": v["carro"], "desde": desde, "ate": ate, "motivo": motivo})
    return saida


def prefixos_sim(db: Session, desde: datetime) -> dict[str, str]:
    """Carro de 4 dígitos → prefixo do SIM (5 dígitos) visto nas leituras."""
    from app.services.monitoramento_linhas import prefixo_patio
    rows = db.execute(select(LeituraVeiculo.prefixo).where(LeituraVeiculo.posicao_em >= desde).distinct()).scalars()
    return {prefixo_patio(p): p for p in rows}


def prefixo_5dig(carro: str, conhecidos: dict[str, str]) -> Optional[str]:
    if carro in conhecidos:
        return conhecidos[carro]
    return AREA_SIM + carro if len(carro) == 4 and carro.isdigit() else None


def _coberta(coletas: list[RastroColeta], desde: datetime, ate: datetime) -> bool:
    return any(_utc(c.desde) is not None and _utc(c.ate) is not None
               and _utc(c.desde) <= desde + FOLGA_COBERTURA
               and _utc(c.ate) >= ate - timedelta(seconds=INTERVALO_RASTRO_SEG)
               for c in coletas)


def carros_em_duvida(db: Session, agora: Optional[datetime] = None) -> list[dict]:
    """Lista para o robô: [{prefixo_5dig, carro, desde, ate, motivo}], no máximo 15."""
    from app.services.fiscalizacao_tempo_real import montar_linha_tempo_real
    from app.services.monitoramento_cadastro import data_servico_de
    from app.services.monitoramento_linhas import linhas_monitoradas

    agora = agora or datetime.now(timezone.utc)
    data_servico = data_servico_de(agora)
    por_carro: dict[str, dict] = {}
    for linha in linhas_monitoradas(db).values():
        if not linha.tem_mapa:
            continue  # sem traçado não há detecção de saída: o Rastro não ajudaria
        resultado = montar_linha_tempo_real(db, linha, data_servico, agora)
        for c in candidatos_da_linha(resultado["viagens"], agora):
            atual = por_carro.get(c["carro"])
            if atual is None or c["desde"] < atual["desde"]:
                por_carro[c["carro"]] = dict(c, linha_codigo=linha.codigo)
    if not por_carro:
        return []
    conhecidos = prefixos_sim(db, agora - timedelta(days=3))
    coletas: dict[str, list[RastroColeta]] = {}
    for c in db.execute(select(RastroColeta).where(RastroColeta.ate >= agora - timedelta(days=1))).scalars():
        coletas.setdefault(c.prefixo, []).append(c)
    saida = []
    for c in sorted(por_carro.values(), key=lambda c: c["desde"]):
        p5 = prefixo_5dig(c["carro"], conhecidos)
        if p5 is None or _coberta(coletas.get(p5, []), c["desde"], c["ate"]):
            continue
        saida.append({"prefixo_5dig": p5, "carro": c["carro"], "linha_codigo": c["linha_codigo"],
                      "desde": _iso(c["desde"]), "ate": _iso(c["ate"]),
                      "desde_ms": int(c["desde"].timestamp() * 1000), "ate_ms": int(c["ate"].timestamp() * 1000),
                      "motivo": c["motivo"]})
        if len(saida) >= MAX_CARROS:
            break
    return saida


def carros_em_duvida_com_cache(db: Session) -> list[dict]:
    """O robô consulta a configuração a cada ciclo: 1 cálculo por minuto basta."""
    agora_mono = relogio.monotonic()
    with _cache_lock:
        item = _cache.get("duvida")
        if item and agora_mono - item[0] < CACHE_SEGUNDOS:
            return item[1]
    try:
        lista = carros_em_duvida(db)
    except Exception:
        logger.exception("Falha ao montar os carros em dúvida do robô")
        lista = []
    with _cache_lock:
        _cache["duvida"] = (agora_mono, lista)
    return lista


def limpar_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ─── Recebimento ─────────────────────────────────────────────────────────────

def _instante_ms(valor: Any) -> Optional[datetime]:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return datetime.fromtimestamp(float(valor) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def ponto_do_rastro(prefixo: str, bruto: Any, mapa: dict, agora: datetime) -> Optional[dict]:
    """Ponto do Rastro → linha de leitura_veiculo (None = descarta)."""
    from app.core.linha import codigo_linha
    if not isinstance(bruto, dict):
        return None
    try:
        la, lo = float(bruto.get("la")), float(bruto.get("lo"))
        li = int(bruto.get("li"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or (la == 0 and lo == 0):
        return None
    posicao = _instante_ms(bruto.get("d"))
    if posicao is None:
        return None
    conhecido = mapa.get(li)
    linha = conhecido[0] if conhecido else (codigo_linha(str(bruto.get("l"))) if bruto.get("l") else None)
    if not linha:
        return None
    s = bruto.get("s")
    sentido = s if s in (1, 2) else (conhecido[1] if conhecido else None)
    if sentido not in (1, 2):
        return None
    vel = bruto.get("vel")
    try:
        vel = None if vel is None else max(0, min(int(round(float(vel))), 32767))
    except (TypeError, ValueError):
        vel = None
    loc = bruto.get("loc")
    return {"posicao_em": posicao, "prefixo": prefixo, "linha_codigo": linha, "sim_codigo": li, "sentido": sentido,
            "lat": la, "lon": lo, "velocidade": vel, "local_sim": (str(loc).strip()[:120] or None) if loc else None,
            "recebido_em": agora, "fonte": FONTE_RASTRO}
