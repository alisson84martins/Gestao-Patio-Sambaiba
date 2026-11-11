"""Previsão de chegada (ETA) de cada carro ao terminal de destino (fase 2, bloco F).

Distância que falta PELO TRAÇADO ÷ velocidade estimada, trecho a trecho
(trecho = 500 m do traçado do sentido). ⛔ Nada de API paga de trânsito.

Velocidade de cada trecho = média ponderada do que houver:
  (a) trânsito ao vivo da própria frota — mediana da velocidade dos carros
      que passaram pelo trecho nos últimos 20 min (qualquer linha monitorada
      que use a via: a leitura vale se cai a ≤ 60 m deste traçado e o carro
      anda NO SENTIDO dele)                                      peso 0,5
  (b) histórico do mesmo trecho, faixa de 30 min e tipo de dia
      (monitoramento.velocidade_trecho)                          peso 0,3
  (c) velocidade recente do próprio carro (últimos 10 min), só nos
      próximos 1,5 km                                            peso 0,2
  Os pesos se redistribuem entre as fontes que existirem. A velocidade é a
  EFETIVA (avanço no traçado ÷ tempo): já inclui ponto, farol e parada.
Faixa de erro: ±15% do tempo restante (±25% sem trânsito ao vivo), mínimo
2 min. "Sem previsão" quando falta mapa, o carro não está na rota, ou mais
da metade do caminho não tem nenhuma fonte de velocidade.

Histórico: `agregar_dias_pendentes` soma cada dia fechado (antes da retenção
de 3 dias apagar as leituras) em velocidade_trecho como média das medianas
diárias ponderada pelo número de amostras (peso do passado limitado a 200,
para o histórico continuar se ajustando).
"""
import logging
import math
import statistics
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.core.config import FUSO_OPERACAO
from app.services.monitoramento_regras import FAIXA, acumulado, comprimento, metros

logger = logging.getLogger(__name__)

TRECHO_M = 500
FAIXA_MIN = 30
JANELA_AO_VIVO = timedelta(minutes=20)
JANELA_PROPRIA = timedelta(minutes=10)
PROPRIA_ALCANCE_M = 1500
MESMA_VIA_M = 60
LACUNA_AMOSTRA = timedelta(minutes=3)
VEL_MIN_KMH, VEL_MAX_KMH = 4.0, 60.0
PESO_AO_VIVO, PESO_HISTORICO, PESO_PROPRIA = 0.5, 0.3, 0.2
MIN_AMOSTRAS_AO_VIVO = 2
MIN_AMOSTRAS_HISTORICO = 3
ERRO_COM_AO_VIVO, ERRO_SEM_AO_VIVO, ERRO_MIN_MIN = 0.15, 0.25, 2
COBERTURA_MIN = 0.5
PESO_PASSADO_MAX = 200


def tipo_dia(d: date) -> str:
    """Mesma regra de app/services/icv.py::_tipo_dia (duplicada para não importar o módulo de ICV)."""
    return "SABADO" if d.weekday() == 5 else "DOMINGO" if d.weekday() == 6 else "UTIL"


def faixa_de(instante: datetime) -> int:
    local = instante.astimezone(FUSO_OPERACAO)
    return (local.hour * 60 + local.minute) // FAIXA_MIN


def _projetar(p, pts, acum, ini=0, fim=None) -> tuple[float, float, int]:
    """(distância ao traçado, avanço em m, índice do segmento) — busca em pts[ini:fim]."""
    fim = len(pts) if fim is None else min(fim, len(pts))
    melhor, prog, idx = math.inf, 0.0, ini
    ax, ay = metros(p, pts[max(ini, 0)])
    for i in range(max(ini, 0) + 1, fim):
        bx, by = metros(p, pts[i])
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2)) if l2 else 0.0
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < melhor:
            melhor, prog, idx = d, acum[i - 1] + t * (acum[i] - acum[i - 1]), i
        ax, ay = bx, by
    return melhor, prog, idx


def amostras(leituras_carro: list[dict], linha, sentido: int, raio: float = MESMA_VIA_M) -> list[tuple]:
    """Leituras de UM carro (qualquer linha) → [(trecho, instante do meio, km/h)] no traçado `sentido`.

    Só conta o par de leituras seguidas que cai a ≤ `raio` do traçado e anda
    para a FRENTE nele (carro no sentido contrário da mesma rua fica de fora)."""
    pts = linha.tracados.get(sentido)
    if not pts:
        return []
    acum = acumulado(linha, sentido)
    saida = []
    anterior = None
    idx = None
    for r in sorted(leituras_carro, key=lambda r: r["em"]):
        if idx is None:
            d, prog, idx = _projetar((r["la"], r["lo"]), pts, acum)
        else:
            d, prog, idx2 = _projetar((r["la"], r["lo"]), pts, acum, max(0, idx - 30), idx + 80)
            if d > raio:  # pode ter pulado: busca no traçado todo
                d, prog, idx2 = _projetar((r["la"], r["lo"]), pts, acum)
            idx = idx2
        atual = (r["em"], prog) if d <= raio else None
        if anterior and atual:
            dt = (atual[0] - anterior[0]).total_seconds()
            dp = atual[1] - anterior[1]
            if 0 < dt <= LACUNA_AMOSTRA.total_seconds() and dp > 0:
                kmh = dp / dt * 3.6
                if kmh <= VEL_MAX_KMH * 1.5:
                    meio = anterior[1] + dp / 2
                    saida.append((int(meio // TRECHO_M), anterior[0] + (atual[0] - anterior[0]) / 2, kmh))
        anterior = atual
    return saida


def _limitar(v: float) -> float:
    return max(VEL_MIN_KMH, min(VEL_MAX_KMH, v))


def prever(
    *,
    linha,
    sentido: int,
    progresso_m: Optional[float],
    agora: datetime,
    ao_vivo: dict[int, list[float]],
    historico: dict[tuple[int, int], tuple[float, int]],
    propria_kmh: Optional[float],
) -> Optional[dict]:
    """ETA de um carro. `ao_vivo`: trecho → velocidades (km/h) dos últimos 20 min;
    `historico`: (trecho, faixa) → (mediana km/h, n). None = sem previsão."""
    if progresso_m is None or not linha.tracados.get(sentido):
        return None
    total = comprimento(linha, sentido)
    falta = max(0.0, total - progresso_m)
    if falta < 50:
        return None  # já chegando
    segundos = 0.0
    coberto = 0.0
    usou_ao_vivo = False
    pos = progresso_m
    faixa = faixa_de(agora)
    while pos < total - 1e-6:
        trecho = int(pos // TRECHO_M)
        fim = min(total, (trecho + 1) * TRECHO_M)
        comp = fim - pos
        fontes = []
        vivos = ao_vivo.get(trecho) or []
        if len(vivos) >= MIN_AMOSTRAS_AO_VIVO:
            fontes.append((PESO_AO_VIVO, statistics.median(vivos)))
            usou_ao_vivo = True
        hist = historico.get((trecho, faixa)) or historico.get((trecho, faixa - 1)) or historico.get((trecho, faixa + 1))
        if hist and hist[1] >= MIN_AMOSTRAS_HISTORICO:
            fontes.append((PESO_HISTORICO, hist[0]))
        if propria_kmh and pos - progresso_m < PROPRIA_ALCANCE_M:
            fontes.append((PESO_PROPRIA, propria_kmh))
        if fontes:
            v = _limitar(sum(p * x for p, x in fontes) / sum(p for p, _ in fontes))
            coberto += comp
        else:
            v = None
        if v is not None:
            segundos += comp / (v / 3.6)
        else:
            segundos += comp / (15 / 3.6)  # trecho sem dado: 15 km/h só para não sumir com o pedaço
        pos = fim
        # a faixa horária avança junto com a viagem
        faixa = faixa_de(agora + timedelta(seconds=segundos))
    if coberto / falta < COBERTURA_MIN:
        return None
    minutos = segundos / 60
    erro = max(ERRO_MIN_MIN, round(minutos * (ERRO_COM_AO_VIVO if usou_ao_vivo else ERRO_SEM_AO_VIVO)))
    chegada = agora + timedelta(seconds=segundos)
    return {
        "chegada_em": chegada.isoformat(),
        "chegada": chegada.astimezone(FUSO_OPERACAO).strftime("%H:%M"),
        "minutos": round(minutos),
        "mais_menos_min": int(erro),
        "falta_m": round(falta),
        "fontes": {"ao_vivo": usou_ao_vivo, "historico": bool(historico), "propria": bool(propria_kmh)},
    }


def texto_previsao(eta: Optional[dict]) -> str:
    if not eta:
        return "sem previsão"
    return f"previsão: chega ~{eta['chegada']} (±{eta['mais_menos_min']} min)"


# ─── Banco ───────────────────────────────────────────────────────────────────

def _utc(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def velocidades_ao_vivo(leituras_por_carro: dict[str, list[dict]], linha, sentido: int, agora: datetime) -> dict:
    por_trecho: dict[int, list[float]] = defaultdict(list)
    desde = agora - JANELA_AO_VIVO
    for lista in leituras_por_carro.values():
        recentes = [r for r in lista if desde <= r["em"] <= agora]
        for trecho, _, kmh in amostras(recentes, linha, sentido):
            por_trecho[trecho].append(kmh)
    return por_trecho


def historico_da_linha(db, linha_codigo: str, sentido: int, tipo: str) -> dict:
    from sqlalchemy import select

    from app.models.monitoramento import VelocidadeTrecho
    linhas = db.execute(
        select(VelocidadeTrecho.trecho, VelocidadeTrecho.faixa, VelocidadeTrecho.mediana_kmh, VelocidadeTrecho.n)
        .where(VelocidadeTrecho.linha_codigo == linha_codigo, VelocidadeTrecho.sentido == sentido,
               VelocidadeTrecho.tipo_dia == tipo)
    ).all()
    return {(t, f): (m, n) for t, f, m, n in linhas}


def velocidade_propria(leituras: list[dict], linha, sentido: int, agora: datetime) -> Optional[float]:
    recentes = [r for r in leituras if agora - JANELA_PROPRIA <= r["em"] <= agora and r["s"] == sentido]
    vs = [kmh for _, _, kmh in amostras(recentes, linha, sentido, raio=FAIXA)]
    return statistics.median(vs) if vs else None


def prever_carros(db, linha, carros: list[dict], leituras_por_carro: dict[str, list[dict]], agora: datetime) -> None:
    """Grava `eta` e `eta_texto` em cada carro do ao-vivo (só os que estão rodando).

    `leituras_por_carro`: prefixo (SIM) → [{em, s, la, lo}] das linhas monitoradas, últimos 20 min+."""
    if not linha.tem_mapa:
        for c in carros:
            c["eta"], c["eta_texto"] = None, "sem previsão (linha sem traçado)"
        return
    tipo = tipo_dia(agora.astimezone(FUSO_OPERACAO).date())
    cache_vivo: dict[int, dict] = {}
    cache_hist: dict[int, dict] = {}
    for c in carros:
        s = c.get("sentido")
        eta = None
        if c.get("zona") == "rota" and not c.get("sumiu") and s in linha.tracados:
            if s not in cache_vivo:
                cache_vivo[s] = velocidades_ao_vivo(leituras_por_carro, linha, s, agora)
                cache_hist[s] = historico_da_linha(db, linha.codigo, s, tipo)
            propria = velocidade_propria(leituras_por_carro.get(c["prefixo"], []), linha, s, agora)
            eta = prever(linha=linha, sentido=s, progresso_m=c.get("progresso_m"), agora=agora,
                         ao_vivo=cache_vivo[s], historico=cache_hist[s], propria_kmh=propria)
        c["eta"] = eta
        c["eta_texto"] = texto_previsao(eta)


# ─── Histórico diário ────────────────────────────────────────────────────────

def agregar_dia(db, linha, data_servico: date) -> int:
    """Soma o dia em velocidade_trecho. Devolve quantas amostras entraram."""
    from sqlalchemy import select

    from app.models.monitoramento import LeituraVeiculo, VelocidadeAgregacao, VelocidadeTrecho
    from app.services.fiscalizacao_tempo_real import inicio_servico

    if db.get(VelocidadeAgregacao, (data_servico, linha.codigo)) is not None:
        return 0
    ini = inicio_servico(data_servico)
    fim = ini + timedelta(days=1)
    total_leituras = 0
    if linha.tem_mapa:
        rows = db.execute(
            select(LeituraVeiculo.prefixo, LeituraVeiculo.posicao_em, LeituraVeiculo.sentido,
                   LeituraVeiculo.lat, LeituraVeiculo.lon)
            .where(LeituraVeiculo.linha_codigo == linha.codigo, LeituraVeiculo.posicao_em >= ini,
                   LeituraVeiculo.posicao_em < fim)
        ).all()
        total_leituras = len(rows)
        por_carro_sentido: dict[tuple, list[dict]] = defaultdict(list)
        for p, em, s, la, lo in rows:
            por_carro_sentido[(p, s)].append({"em": _utc(em), "s": s, "la": la, "lo": lo})
        brutas: dict[tuple[int, int, int], list[float]] = defaultdict(list)
        for (_, s), lista in por_carro_sentido.items():
            for trecho, meio, kmh in amostras(lista, linha, s, raio=FAIXA):
                brutas[(s, trecho, faixa_de(meio))].append(kmh)
        tipo = tipo_dia(data_servico)
        for (s, trecho, faixa), vs in brutas.items():
            med, n = statistics.median(vs), len(vs)
            atual = db.get(VelocidadeTrecho, (linha.codigo, s, trecho, faixa, tipo))
            if atual is None:
                db.add(VelocidadeTrecho(linha_codigo=linha.codigo, sentido=s, trecho=trecho, faixa=faixa,
                                        tipo_dia=tipo, mediana_kmh=round(med, 2), n=n))
            else:
                peso = min(atual.n, PESO_PASSADO_MAX)
                atual.mediana_kmh = round((atual.mediana_kmh * peso + med * n) / (peso + n), 2)
                atual.n = atual.n + n
                atual.atualizado_em = datetime.now(timezone.utc)
    db.add(VelocidadeAgregacao(data_servico=data_servico, linha_codigo=linha.codigo, leituras=total_leituras))
    db.commit()
    return total_leituras


def agregar_dias_pendentes(db, agora: Optional[datetime] = None, dias: int = 3) -> int:
    """Agrega os dias FECHADOS (antes de hoje) dos últimos `dias` que ainda não entraram."""
    from app.services.monitoramento_cadastro import data_servico_de
    from app.services.monitoramento_linhas import linhas_monitoradas

    agora = agora or datetime.now(timezone.utc)
    hoje = data_servico_de(agora)
    feitos = 0
    for linha in linhas_monitoradas(db).values():
        for k in range(dias, 0, -1):
            feitos += agregar_dia(db, linha, hoje - timedelta(days=k))
    return feitos


_agregando = threading.Lock()


def agendar_agregacao() -> None:
    """Roda a agregação numa thread própria (o recebimento do robô não espera)."""
    if not _agregando.acquire(blocking=False):
        return

    def _rodar():
        from app.core.database import SessionLocal
        try:
            with SessionLocal() as db:
                agregar_dias_pendentes(db)
        except Exception:
            logger.exception("Falha ao agregar o histórico de velocidade")
        finally:
            _agregando.release()

    threading.Thread(target=_rodar, name="agregar-velocidade", daemon=True).start()
