"""Leitura para as telas do monitoramento ao vivo (fiscal e coordenação).

Junta as leituras das últimas 4 h de uma linha, roda a cerca
(monitoramento_regras) por carro e monta o que a tela ao-vivo.html mostra.
Classe do carro, "alertas agora" e "ocorrências do dia" seguem o painel.js
da POC (mesmo critério que o Alisson validou na extensão).

"Sumiu" — o painel da POC sabia quais carros vieram na última consulta do
SIM; aqui o reenvio de posição repetida não grava linha nova, então a
aproximação é: a última leitura do carro chegou ao sistema há mais de
3 min antes da última coleta OK (o robô coleta a cada 1 min — perdeu ~3
consultas seguidas). Carro sumido há mais de 1 h sai da tela (igual à POC).
"""
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.monitoramento import Coleta, LeituraVeiculo
from app.services import monitoramento_regras as regras
from app.services.monitoramento_linhas import (
    GARAGEM_G3, NOME_GARAGEM, LinhaMonitorada, prefixo_patio,
)

JANELA_ANALISE = timedelta(hours=4)
JANELA_ALERTA_MIN = 20         # virada/retorno ficam em "Alertas agora" por 20 min
SUMIU_TOLERANCIA = timedelta(minutes=3)
SUMIU_SAI_DA_TELA_MIN = 60     # some da tela 1 h depois
RASTRO_MAX_MIN = 240
CACHE_SEGUNDOS = 10            # vários fiscais olhando a mesma linha: 1 cálculo a cada 10 s

ALERTA = {"desvio", "virada", "retorno"}
ATENCAO = {"parado", "semtx", "terminal"}
ZONAS_TERMINAL = ("TP", "TS")

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite (testes) devolve datetime sem fuso; o Postgres, com. Tudo em UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _ms(dt: datetime) -> int:
    return int(_utc(dt).timestamp() * 1000)


def _iso_ms(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).isoformat()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _utc(dt).isoformat() if dt else None


# ─── Coleta (batimento do robô) ──────────────────────────────────────────────

def estado_coleta(db: Session) -> dict:
    ultima = db.execute(select(Coleta).order_by(Coleta.recebido_em.desc(), Coleta.id.desc()).limit(1)).scalar_one_or_none()
    ultima_ok_em = db.execute(
        select(Coleta.recebido_em).where(Coleta.status == "ok").order_by(Coleta.recebido_em.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "status": ultima.status if ultima else None,
        "ultima_em": _iso(ultima.recebido_em) if ultima else None,
        "ultima_ok_em": _iso(ultima_ok_em),
        "mensagem": ultima.mensagem if ultima else None,
        "qtd_veiculos": ultima.qtd_veiculos if ultima else None,
        "_ultima_ok": _utc(ultima_ok_em),
    }


# ─── Classe do carro (painel.js::classeCarro) ────────────────────────────────

def _vale_agora(e: dict, agora_ms: int) -> bool:
    if e["tipo"] in ALERTA:
        return e["ativo"] or agora_ms - e["inicio"] < JANELA_ALERTA_MIN * 60000
    return e["tipo"] in ATENCAO and e["ativo"]


def classe_carro(analise: dict, sumiu: bool, agora_ms: int) -> str:
    ev = analise["eventos"]
    if any(e["tipo"] in ALERTA and _vale_agora(e, agora_ms) for e in ev):
        return "alerta"
    if any(e["tipo"] in ATENCAO and e["ativo"] for e in ev):
        return "atencao"
    if sumiu:
        return "sumiu"
    if analise["zona"] == "garagem":
        return "garagem"
    if analise["zona"] in ZONAS_TERMINAL:
        return "terminal"
    return "ok"


def _nivel(tipo: str) -> str:
    return "alerta" if tipo in ALERTA else "atencao"


def _evento_saida(prefixo: str, e: dict, linha: LinhaMonitorada) -> dict:
    return {
        "id": e["id"],
        "prefixo": prefixo,
        "prefixo_patio": prefixo_patio(prefixo),
        "tipo": e["tipo"],
        "nivel": _nivel(e["tipo"]),
        "ativo": bool(e["ativo"]),
        "inicio_em": _iso_ms(e["inicio"]),
        "texto": regras.texto_evento(e, linha),
    }


# ─── Ao vivo ─────────────────────────────────────────────────────────────────

def _leituras_por_carro(db: Session, linha: LinhaMonitorada, desde: datetime) -> dict[str, list]:
    linhas = db.execute(
        select(LeituraVeiculo)
        .where(LeituraVeiculo.linha_codigo == linha.codigo, LeituraVeiculo.posicao_em >= desde)
        .order_by(LeituraVeiculo.prefixo, LeituraVeiculo.posicao_em)
    ).scalars().all()
    por_carro: dict[str, list] = defaultdict(list)
    for lv in linhas:
        por_carro[lv.prefixo].append(lv)
    return por_carro


def _como_leitura(lv: LeituraVeiculo) -> dict:
    return {"t": _ms(lv.posicao_em), "la": lv.lat, "lo": lv.lon, "s": lv.sentido,
            "loc": lv.local_sim, "vel": lv.velocidade}


def montar_ao_vivo(db: Session, linha: LinhaMonitorada, agora: Optional[datetime] = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    agora_ms = _ms(agora)
    coleta = estado_coleta(db)
    ultima_ok = coleta.pop("_ultima_ok")
    inicio_dia_ms = _ms(agora.astimezone(FUSO_OPERACAO).replace(hour=0, minute=0, second=0, microsecond=0))

    carros, alertas, ocorrencias = [], [], []
    for prefixo, lvs in _leituras_por_carro(db, linha, agora - JANELA_ANALISE).items():
        analise = regras.analisar_carro(prefixo, [_como_leitura(lv) for lv in lvs], linha, agora_ms)
        if analise is None:
            continue
        recebido_ult = _utc(max((lv.recebido_em for lv in lvs if lv.recebido_em), default=None))
        sumiu = bool(ultima_ok and recebido_ult and recebido_ult < ultima_ok - SUMIU_TOLERANCIA)
        ult = analise["ult"]
        if sumiu and agora_ms - ult["t"] > SUMIU_SAI_DA_TELA_MIN * 60000:
            continue
        classe = classe_carro(analise, sumiu, agora_ms)
        destino = linha.destino.get(ult["s"])
        carros.append({
            "prefixo": prefixo,
            "prefixo_patio": prefixo_patio(prefixo),
            "sentido": ult["s"],
            "destino": destino,
            "chegou": analise["zona"] == destino,
            "lat": ult["la"],
            "lon": ult["lo"],
            "velocidade": ult.get("vel"),
            "local": ult.get("loc"),
            "zona": analise["zona"],
            "distancia_rota_m": None if ult["_dRota"] == float("inf") else round(ult["_dRota"]),
            "progresso_m": None if ult["_prog"] is None else round(ult["_prog"]),
            "posicao_em": _iso_ms(ult["t"]),
            "classe": classe,
            "sumiu": sumiu,
        })
        for e in analise["eventos"]:
            if _vale_agora(e, agora_ms):
                alertas.append(_evento_saida(prefixo, e, linha))
            if e["inicio"] >= inicio_dia_ms and e["tipo"] != "semtx":
                ocorrencias.append(_evento_saida(prefixo, e, linha))

    # alerta antes de atenção; dentro do nível, o mais recente primeiro
    alertas.sort(key=lambda a: a["inicio_em"], reverse=True)
    alertas.sort(key=lambda a: a["nivel"] != "alerta")
    ocorrencias.sort(key=lambda o: o["inicio_em"], reverse=True)
    carros.sort(key=lambda c: (c["sentido"] or 0, -(c["progresso_m"] if c["progresso_m"] is not None else -1)))

    contadores = {"rodando": 0, "terminal": 0, "garagem": 0, "alerta": 0, "sumiram": 0}
    for c in carros:
        if c["sumiu"]:
            contadores["sumiram"] += 1
            continue
        if c["zona"] == "garagem":
            contadores["garagem"] += 1
        elif c["zona"] in ZONAS_TERMINAL:
            contadores["terminal"] += 1
        else:
            contadores["rodando"] += 1
        if c["classe"] == "alerta":
            contadores["alerta"] += 1

    return {
        "linha": {"codigo": linha.codigo, "nome": linha.nome},
        "atualizado_em": agora.isoformat(),
        "janela_horas": int(JANELA_ANALISE.total_seconds() // 3600),
        "coleta": coleta,
        "contadores": contadores,
        "carros": carros,
        "alertas_agora": alertas,
        "ocorrencias_dia": ocorrencias,
    }


def ao_vivo_com_cache(db: Session, linha: LinhaMonitorada) -> dict:
    agora_mono = time.monotonic()
    with _cache_lock:
        item = _cache.get(linha.codigo)
        if item and agora_mono - item[0] < CACHE_SEGUNDOS:
            return item[1]
    resultado = montar_ao_vivo(db, linha)
    with _cache_lock:
        _cache[linha.codigo] = (agora_mono, resultado)
    return resultado


def limpar_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ─── Rastro de um carro ──────────────────────────────────────────────────────

def montar_rastro(db: Session, linha: LinhaMonitorada, prefixo: str, minutos: int,
                  agora: Optional[datetime] = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    minutos = max(1, min(int(minutos), RASTRO_MAX_MIN))
    lvs = db.execute(
        select(LeituraVeiculo)
        .where(LeituraVeiculo.linha_codigo == linha.codigo, LeituraVeiculo.prefixo == prefixo,
               LeituraVeiculo.posicao_em >= agora - timedelta(minutes=minutos))
        .order_by(LeituraVeiculo.posicao_em)
    ).scalars().all()
    pontos = []
    for lv in lvs:
        r = regras.classificar(_como_leitura(lv), linha, GARAGEM_G3, prefixo)
        pontos.append({"lat": lv.lat, "lon": lv.lon, "posicao_em": _iso(lv.posicao_em),
                       "sentido": lv.sentido, "zona": r["_z"]})
    return {"prefixo": prefixo, "prefixo_patio": prefixo_patio(prefixo), "minutos": minutos, "pontos": pontos}


# ─── Traçado (estático) ──────────────────────────────────────────────────────

def montar_tracado(linha: LinhaMonitorada) -> dict:
    return {
        "codigo": linha.codigo,
        "nome": linha.nome,
        "faixa_m": regras.FAIXA,
        "sentidos": [
            {"sentido": s, "destino": linha.destino[s], "destino_nome": linha.nomes_terminais[linha.destino[s]],
             "shape_gtfs": linha.shapes.get(s), "pontos": [[la, lo] for la, lo in pts]}
            for s, pts in sorted(linha.tracados.items())
        ],
        "terminais": [
            {"codigo": t, "nome": linha.nomes_terminais[t], "raio_m": regras.RAIO_TERMINAL,
             "centros": [[la, lo] for la, lo in centros]}
            for t, centros in linha.terminais.items()
        ],
        "garagem": {"codigo": NOME_GARAGEM, "nome": "Garagem 3", "raio_m": regras.RAIO_GARAGEM,
                    "centro": list(GARAGEM_G3)},
    }


def resumo_linha(linha: LinhaMonitorada) -> dict:
    return {
        "codigo": linha.codigo,
        "nome": linha.nome,
        "terminais": dict(linha.nomes_terminais),
        "sentidos": {str(s): {"destino": d, "destino_nome": linha.nomes_terminais[d]} for s, d in linha.destino.items()},
    }
