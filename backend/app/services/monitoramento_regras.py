"""Cerca virtual da linha — porte fiel do `regras.js` v0.3 da POC
(C:\\dev\\poc-olhovivo\\extensao, validado com o dia inteiro de 10/11/2026).

É o "coordenador": olha o histórico de cada carro e decide se há
anormalidade — desvio de itinerário, recolhida, virada sem passar no
terminal, retorno no itinerário, parado fora de terminal e sem transmissão.

⛔ Constantes iguais às do JS. Não "melhorar" sem medir de novo:
- virada v0.3: só julga se a viagem andou ≥ 2 km da origem e não teve buraco
  de leitura > 3 min no trecho (a regra antiga deu 17 alertas falsos no dia);
- "parado no terminal" está DESLIGADO (19 de 22 carros passam de 1 h no TP
  por programação no entrepico — só com o horário do NIMER, fase 2).

Uma leitura é um dict com: t (epoch ms), la, lo, s (sentido), loc.
A classificação (zona, distância da rota etc.) não depende do resto do
histórico, então é cacheada por (linha, prefixo, t) em memória do processo —
a tela consulta a cada 30 s e não pode refazer ~1.000 segmentos por leitura.
"""
import math
import threading
from datetime import datetime
from typing import Optional

from app.core.config import FUSO_OPERACAO
from app.services.monitoramento_linhas import GARAGEM_G3, LinhaMonitorada

FAIXA = 150             # m de folga de cada lado do itinerário
RAIO_TERMINAL = 250     # m em volta do ponto real de volta no TP/TS
PARADO_TERMINAL_MIN = None  # DESLIGADO (ver docstring)
VIAGEM_MIN_M = 2000     # só julga "virada" se o carro andou pelo menos isso longe da origem
LACUNA_MIN = 3          # buraco entre leituras que impede julgar a viagem
RAIO_GARAGEM = 300      # m em volta da G3
LEITURAS_DESVIO = 3     # leituras seguidas fora da faixa para alertar
PARADO_MIN = 8          # min parado fora de terminal/garagem
PARADO_RAIO = 50        # m — considera "parado" se não saiu desse raio
SEM_TX_MIN = 5          # min sem posição nova
RETORNO_M = 800         # m — andou para trás no itinerário antes do terminal
RUMO_G3_M = 300         # m — fora da rota e já 300 m mais perto da G3 (T5, fase 3)
FIM_DE_VOLTA = "fim_de_volta"  # evento interno do T5 revisto: só os alertas da fiscalização usam

ZONAS_TERMINAL = ("TP", "TS")


def _arred(x: float) -> int:
    """Math.round do JS (meio sobe) — o round() do Python arredonda para o par."""
    return math.floor(x + 0.5)


# ─── Contas de distância (mesma aproximação plana do JS) ─────────────────────

def metros(o, p) -> tuple[float, float]:
    return ((p[1] - o[1]) * 111320 * math.cos(o[0] * math.pi / 180), (p[0] - o[0]) * 110540)


def distancia(a, b) -> float:
    x, y = metros(a, b)
    return math.hypot(x, y)


def projetar(p, pts, acum) -> tuple[float, float]:
    """Projeta o ponto no traçado: (distância até a rota, quanto já andou da linha) em m."""
    melhor, prog = math.inf, 0.0
    ax, ay = metros(p, pts[0])
    for i in range(1, len(pts)):
        bx, by = metros(p, pts[i])
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / l2)) if l2 else 0.0
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < melhor:
            melhor = d
            prog = acum[i - 1] + t * (acum[i] - acum[i - 1])
        ax, ay = bx, by
    return melhor, prog


_acum_cache: dict[tuple[str, int], list[float]] = {}


def acumulado(linha: LinhaMonitorada, sentido: int) -> list[float]:
    chave = (linha.codigo, sentido)
    if chave not in _acum_cache:
        pts = linha.tracados[sentido]
        acum = [0.0]
        for i in range(1, len(pts)):
            acum.append(acum[-1] + distancia(pts[i - 1], pts[i]))
        _acum_cache[chave] = acum
    return _acum_cache[chave]


def comprimento(linha: LinhaMonitorada, sentido: int) -> float:
    return acumulado(linha, sentido)[-1]


# ─── Classificação de uma leitura ────────────────────────────────────────────

_classe_cache: dict[tuple, tuple] = {}
_classe_lock = threading.Lock()
_CACHE_MAX = 200_000


def limpar_cache() -> None:
    with _classe_lock:
        _classe_cache.clear()


def _calcular_classe(r: dict, linha: LinhaMonitorada, garagem) -> tuple:
    p = (r["la"], r["lo"])
    d_gar = distancia(p, garagem)
    if not linha.tem_mapa:
        # Linha sem traçado (bloco C): só sabe se está na garagem.
        return ("garagem" if d_gar <= RAIO_GARAGEM else "sem_mapa"), d_gar, math.inf, None
    term = None
    for nome, centros in linha.terminais.items():
        if min(distancia(p, c) for c in centros) <= RAIO_TERMINAL:
            term = nome
    d_rota, prog = math.inf, None
    for s, pts in linha.tracados.items():
        d, pr = projetar(p, pts, acumulado(linha, s))
        d_rota = min(d_rota, d)
        if str(s) == str(r.get("s")):
            prog = pr if d <= FAIXA else None
    if d_gar <= RAIO_GARAGEM:
        z = "garagem"
    elif term:
        z = term
    elif d_rota <= FAIXA:
        z = "rota"
    else:
        z = "fora"
    return z, d_gar, d_rota, prog


def classificar(r: dict, linha: LinhaMonitorada, garagem=GARAGEM_G3, prefixo: Optional[str] = None) -> dict:
    """Classifica uma leitura (grava _z, _dGar, _dRota, _prog nela mesma).

    Com `prefixo`, o resultado vai para o cache do processo (a classificação
    só depende da posição e do sentido daquela leitura)."""
    if r.get("_z"):
        return r
    chave = (linha.codigo, prefixo, r["t"], r.get("s"), r["la"], r["lo"]) if prefixo else None
    res = _classe_cache.get(chave) if chave else None
    if res is None:
        res = _calcular_classe(r, linha, garagem)
        if chave:
            with _classe_lock:
                if len(_classe_cache) >= _CACHE_MAX:
                    _classe_cache.clear()
                _classe_cache[chave] = res
    r["_z"], r["_dGar"], r["_dRota"], r["_prog"] = res
    return r


# ─── Análise do histórico de um carro ────────────────────────────────────────

def _origem(linha: LinhaMonitorada, sentido) -> str:
    return "TP" if linha.destino.get(sentido) == "TS" else "TS"


def analisar_carro(prefixo: str, leituras: list[dict], linha: LinhaMonitorada, agora_ms: int,
                   garagem=GARAGEM_G3) -> Optional[dict]:
    """Analisa o histórico de UM carro: estado + eventos (alertas). None sem leituras."""
    if not leituras:
        return None
    ev: list[dict] = []
    L = sorted(leituras, key=lambda r: r["t"])
    for r in L:
        classificar(r, linha, garagem, prefixo)

    em_deslocamento = False
    fora: list[dict] = []
    desvio_atual: Optional[dict] = None
    seg: Optional[dict] = None  # trecho num mesmo sentido
    # T5 (v4.0 fase 3): terminais tocados desde a última saída da G3 e a
    # distância da G3 na última leitura DENTRO (rota/terminal).
    passou: set[str] = set()
    dgar_dentro: Optional[float] = None
    volta_atual: Optional[dict] = None  # evento "fim_de_volta" aberto (T5 revisto)

    for r in L:
        if not linha.tem_mapa:
            # Sem traçado: desvio, virada e retorno desligados (a tela avisa).
            break
        z = r["_z"]
        if z == "garagem":
            # saiu da rota e chegou na G3: era recolhida, não desvio — e todo
            # desvio ativo do carro sai da caixinha (fica no histórico).
            if desvio_atual:
                desvio_atual["tipo"] = "recolhida"
                desvio_atual["ativo"] = False
            if volta_atual:
                volta_atual["ativo"] = False  # entrou na G3: o aviso some sozinho
                volta_atual = None
            em_deslocamento = True
            passou = set()
        if z in ZONAS_TERMINAL:
            em_deslocamento = False
            passou.add(z)
            if volta_atual:
                volta_atual["ativo"] = False  # voltou para um terminal: não era ida para a G3
                volta_atual = None
        if z != "fora":
            dgar_dentro = r["_dGar"]

        # T5 — FIM DE VOLTA RUMO À G3: já tocou o TP E o TS desde a última
        # saída da G3 e, fora da rota, está chegando perto da G3. Nunca é
        # "fora do itinerário" (decisão de 12/11/2026). T5 revisto (12/11
        # 22:46, caso 2735 em 11/11): a cerca não sabe se ainda há viagem na
        # tabela — emite o evento marcado `fim_de_volta` e quem conhece a
        # tabela (fiscalizacao_alertas.alertas_da_cerca) decide: no meio da
        # tabela → indo_garagem (possível R.A.); última viagem → nada.
        # O evento NÃO entra nos alertas/ocorrências/classe das telas da
        # fase 1/2 (monitoramento_ao_vivo separa).
        if (z == "fora" and not em_deslocamento and passou >= set(ZONAS_TERMINAL) and dgar_dentro is not None
                and dgar_dentro - r["_dGar"] > RUMO_G3_M):
            inicio = desvio_atual["inicio"] if desvio_atual else r["t"]
            if desvio_atual in ev:
                ev.remove(desvio_atual)
            desvio_atual = None
            fora = []
            em_deslocamento = True
            volta_atual = {"tipo": FIM_DE_VOLTA, "inicio": inicio, "ativo": True, "rumoG3": True,
                           "fim_de_volta": True, "s": r["s"], "loc": r.get("loc")}
            ev.append(volta_atual)
        elif volta_atual and z == "fora" and r.get("loc"):
            volta_atual["locAtual"] = r["loc"]

        # 1) DESVIO de itinerário (fora da faixa N leituras seguidas)
        if z == "fora" and not em_deslocamento:
            fora.append(r)
            if len(fora) == LEITURAS_DESVIO:
                desvio_atual = {
                    "tipo": "desvio", "inicio": fora[0]["t"], "fim": r["t"], "maxD": 0.0, "ativo": True,
                    "loc": fora[0].get("loc"), "dGar0": fora[0]["_dGar"], "s": r["s"],
                }
                ev.append(desvio_atual)
            if desvio_atual:
                desvio_atual["fim"] = r["t"]
                desvio_atual["maxD"] = max(desvio_atual["maxD"], r["_dRota"])
                desvio_atual["rumoG3"] = desvio_atual["dGar0"] - r["_dGar"] > 500  # se aproximando da garagem
                if r.get("loc"):
                    desvio_atual["locAtual"] = r["loc"]
        else:
            if desvio_atual:
                desvio_atual["ativo"] = False
            fora = []
            desvio_atual = None

        # 2) VIRADA sem passar no terminal (SIM trocou o sentido e o carro não
        #    entrou no terminal de destino)
        if seg is None or seg["s"] != r["s"]:
            # ⚠️ ÚNICA divergência do regras.js v0.3 (achada pelo teste 3 em
            # 10/11/2026): no JS, o buraco que cai JUSTO na troca de sentido
            # (última leitura do sentido 1 antes do TS, primeira do sentido 2
            # depois dele) nunca era contado — o trecho novo começava antes
            # de olhar a lacuna, e a passagem pelo terminal sem leitura virava
            # "virada" falsa. Aqui o buraco da troca também trava o julgamento.
            # Levar a mesma linha para o regras.js da extensão.
            if seg and r["t"] - seg["tAnt"] > LACUNA_MIN * 60000:
                seg["lacuna"] = True
            if (seg and seg["viuInicio"] and not seg["tocouDestino"] and not seg["garagem"]
                    and not seg["lacuna"] and seg["maxOrig"] >= VIAGEM_MIN_M):
                ev.append({"tipo": "virada", "inicio": r["t"], "ativo": False,
                           "terminal": linha.destino.get(seg["s"]), "loc": r.get("loc")})
            seg = {
                "s": r["s"],
                "viuInicio": seg is not None or z in ("TP", "TS", "garagem"),
                "tocouDestino": False, "garagem": False, "maxProg": -1.0, "abaixo": 0,
                "retorno": False, "maxOrig": 0.0, "lacuna": False, "tAnt": r["t"],
            }
        # distância da origem desta viagem (o terminal que não é o destino) e buracos de coleta
        centros_origem = linha.terminais[_origem(linha, r["s"])]
        seg["maxOrig"] = max(seg["maxOrig"], min(distancia((r["la"], r["lo"]), c) for c in centros_origem))
        if r["t"] - seg["tAnt"] > LACUNA_MIN * 60000:
            seg["lacuna"] = True
        seg["tAnt"] = r["t"]
        if z == linha.destino.get(r["s"]):
            seg["tocouDestino"] = True
        if z == "garagem":
            seg["garagem"] = True  # recolheu: não é virada

        # 3) RETORNO: andou para trás no itinerário antes de chegar ao terminal
        if not seg["tocouDestino"] and not em_deslocamento and r["_prog"] is not None:
            if r["_prog"] > seg["maxProg"]:
                seg["maxProg"] = r["_prog"]
                seg["abaixo"] = 0
            elif seg["maxProg"] - r["_prog"] > RETORNO_M:
                seg["abaixo"] += 1
                if seg["abaixo"] == 2 and not seg["retorno"]:
                    seg["retorno"] = True
                    ev.append({"tipo": "retorno", "inicio": r["t"], "ativo": False,
                               "terminal": linha.destino.get(r["s"]),
                               "recuo": seg["maxProg"] - r["_prog"], "loc": r.get("loc")})
            else:
                seg["abaixo"] = 0

    ult = L[-1]
    # 4) PARADO fora de terminal/garagem
    ini = ult
    i = len(L) - 1
    while i >= 0 and distancia((L[i]["la"], L[i]["lo"]), (ult["la"], ult["lo"])) <= PARADO_RAIO:
        ini = L[i]
        i -= 1
    parado_min = (ult["t"] - ini["t"]) / 60000
    if parado_min >= PARADO_MIN and ult["_z"] in ("rota", "fora"):
        ev.append({"tipo": "parado", "inicio": ini["t"], "ativo": True, "min": _arred(parado_min), "loc": ult.get("loc")})
    # 4b) PARADO NO TERMINAL tempo demais — DESLIGADO (PARADO_TERMINAL_MIN = None)
    if PARADO_TERMINAL_MIN and ult["_z"] in ZONAS_TERMINAL:
        ini_t = ult
        i = len(L) - 1
        while i >= 0 and L[i]["_z"] == ult["_z"]:
            ini_t = L[i]
            i -= 1
        m = (ult["t"] - ini_t["t"]) / 60000
        if m >= PARADO_TERMINAL_MIN:
            ev.append({"tipo": "terminal", "inicio": ini_t["t"], "ativo": True, "min": _arred(m), "terminal": ult["_z"]})
    # 5) SEM TRANSMISSÃO
    sem_tx = (agora_ms - ult["t"]) / 60000
    if sem_tx >= SEM_TX_MIN and ult["_z"] != "garagem":
        ev.append({"tipo": "semtx", "inicio": ult["t"], "ativo": True, "min": _arred(sem_tx), "loc": ult.get("loc")})

    for e in ev:
        e["id"] = f"{prefixo}|{e['tipo']}|{e['inicio']}"
    return {"prefixo": prefixo, "ult": ult, "zona": ult["_z"], "sentido": ult["s"], "eventos": ev,
            "paradoMin": parado_min, "emDeslocamento": em_deslocamento}


# ─── Texto do evento ─────────────────────────────────────────────────────────

def fmt_hora(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=FUSO_OPERACAO).strftime("%H:%M")


def texto_evento(e: dict, linha: LinhaMonitorada) -> str:
    def nt(t):
        return linha.nomes_terminais.get(t, t) if t else ""

    tipo = e["tipo"]
    if tipo == "desvio":
        local = e.get("locAtual") or e.get("loc")
        return (f"Fora do itinerário desde {fmt_hora(e['inicio'])} (até {_arred(e['maxD'])} m da rota)"
                + (f" — {local}" if local else "")
                + (" · indo no rumo da G3 (pode ser recolhida)" if e.get("rumoG3") else ""))
    if tipo == "recolhida":
        return f"Saiu da rota às {fmt_hora(e['inicio'])} e entrou na G3 (recolhida)"
    if tipo == "virada":
        return f"Virou sem passar no {nt(e['terminal'])} ({fmt_hora(e['inicio'])})"
    if tipo == "retorno":
        return f"Voltou {_arred(e['recuo'])} m no itinerário antes do {nt(e['terminal'])} ({fmt_hora(e['inicio'])})"
    if tipo == "parado":
        return f"Parado há {e['min']} min fora de terminal" + (f" — {e['loc']}" if e.get("loc") else "")
    if tipo == "terminal":
        return (f"Parado no {nt(e['terminal'])} há {e['min']} min (desde {fmt_hora(e['inicio'])})"
                " — risco de partida perdida")
    if tipo == FIM_DE_VOLTA:
        return f"Fez a volta (TP e TS) e segue rumo à G3 desde {fmt_hora(e['inicio'])}"
    if tipo == "semtx":
        return f"Sem transmitir posição há {e['min']} min (última {fmt_hora(e['inicio'])})"
    return tipo
