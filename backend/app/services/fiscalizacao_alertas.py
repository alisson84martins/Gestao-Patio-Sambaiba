"""Alertas operacionais da fiscalização em tempo real (fase 2, bloco E).

⛔ CALCULADOS a cada chamada (D24): nada é gravado na tabela `alerta` do
Pátio nem em lugar nenhum. O "resolvido" da Central fica em
monitoramento.alerta_tratativa, pela `chave` do alerta.

Tipos novos:
1. sem_motorista — tabela com "Sem Motorista" no NIMER e a 1ª partida em
   ≤ 60 min (ou já passou e a tabela não rodou). Caso real: 032116 em 10/11
   perdeu 4 viagens.
2. partida_atrasada — horário programado passou há > 5 min, a viagem não
   está `done` e o carro programado está PARADO NO TERMINAL DE ORIGEM pelo
   SIM. ⚠️ "Parado no terminal ≥ 60 min" sozinho NÃO serve (19 de 22 carros
   passam de 1 h no entrepico) — só com o horário programado. Casos reais:
   2400 no TP 10:31–14:19 → perdida 13:09; 2293 no TS às 17:59 com a das
   17:23 faltante.
3. carro_trocado_linha — o carro programado na tabela aparece no SIM em
   OUTRA linha (caso 2742 em 10/11: tabela da 1726, rodou o dia na 2011-10).
4. buraco_intervalo — intervalo entre saídas seguidas do mesmo sentido
   > 1,5 × o programado.
5. nao_contabilizada — NIMER `done` e o SIM não viu saída no mesmo sentido
   em ±15 min (afeta o ICV oficial).
6. Os da fase 1 continuam (desvio, virada, retorno, parado, sem transmissão).
7. nao_liberar_antes (11/11, regra do IPP) — partida em HORÁRIO CHEIO
   (07:00, 08:00…) com o carro programado parado no terminal de origem, a
   partir de 5 min antes: aviso de atenção "Não liberar antes das HH:00"
   (sair antes perde o ICV da faixa HH).
8. saiu_adiantado — a partida do horário cheio saiu antes da hora (NIMER
   ou SIM): alerta "saiu adiantado (perde ICV da faixa HH)", por 30 min.

Cada alerta leva o RE de motorista/cobrador COMO ESCALADO NO NIMER — não é
prova de quem faltou.
"""
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import FUSO_OPERACAO as FUSO
from app.services.fiscalizacao_tempo_real import (
    RAIO_TERMINAL_M,
    _distancia,
    _origem,
    hora_local,
    horario_cheio,
)

SEM_MOTORISTA_ANTECEDENCIA = timedelta(minutes=60)
SEM_MOTORISTA_URGENTE = timedelta(minutes=15)
ATRASO_TOLERANCIA = timedelta(minutes=5)
ATRASO_MAX = timedelta(minutes=120)        # depois disso a viagem já é perda, não alerta
LEITURA_RECENTE = timedelta(minutes=5)
PARADO_MIN_LEITURAS = 3
OUTRA_LINHA_RECENTE = timedelta(minutes=15)
BURACO_FATOR = 1.5
BURACO_GRAVE = 2.0
BURACO_JANELA = timedelta(minutes=60)
JANELA_ALERTA_ENCERRADO = timedelta(minutes=20)
NAO_LIBERAR_ANTECEDENCIA = timedelta(minutes=5)
SAIU_ADIANTADO_JANELA = timedelta(minutes=30)

ACAO = {
    "sem_motorista": "Acionar a escala/plantão para cobrir a tabela antes da 1ª partida.",
    "partida_atrasada": "Falar com o terminal: liberar o carro ou trocar antes de perder a viagem.",
    "carro_trocado_linha": "Confirmar com a garagem onde está o carro e repor a tabela.",
    "buraco_intervalo": "Regular o intervalo: segurar o próximo ou adiantar a saída no terminal.",
    "nao_contabilizada": "Conferir no SIM e pedir o registro da viagem para não perder no ICV oficial.",
    "desvio": "Contatar o motorista: carro fora do itinerário.",
    "recolhida": "Confirmar a recolhida com a garagem e registrar o motivo.",
    "virada": "Verificar se o carro pulou o terminal.",
    "retorno": "Verificar por que o carro voltou no itinerário.",
    "parado": "Verificar o carro parado fora do terminal (quebra? trânsito? acidente?).",
    "semtx": "Verificar o GPS/validador do carro.",
    "terminal": "Verificar o carro parado no terminal.",
    "nao_liberar_antes": "Segurar o carro no terminal até o horário cheio.",
    "saiu_adiantado": "Orientar terminal e motorista: no horário cheio o carro não sai antes da hora.",
}
NIVEL_FASE1 = {"desvio": "alerta", "virada": "alerta", "retorno": "alerta",
               "parado": "atencao", "semtx": "atencao", "terminal": "atencao", "recolhida": "atencao"}


def _alerta(tipo, nivel, linha, inicio: Optional[datetime], texto, *, tabela=None, carro=None,
            viagem: Optional[dict] = None, ativo=True, chave_extra="") -> dict:
    motorista = cobrador = None
    if viagem:
        motorista, cobrador = viagem.get("motorista_re"), viagem.get("cobrador_re")
        tabela = tabela or viagem.get("tabela")
        carro = carro or viagem.get("carro")
    marco = inicio.strftime("%Y%m%d%H%M") if inicio else ""
    return {
        "chave": f"{tipo}|{linha}|{tabela or ''}|{carro or ''}|{marco}{chave_extra}"[:160],
        "tipo": tipo,
        "nivel": nivel,
        "ativo": ativo,
        "linha": linha,
        "tabela": tabela,
        "carro": carro,
        "escalado_nimer": {"motorista_re": motorista, "cobrador_re": cobrador},
        "inicio_em": inicio.isoformat() if inicio else None,
        "inicio": hora_local(inicio),
        "texto": texto,
        "acao_sugerida": ACAO.get(tipo, ""),
    }


# ─── 1. Tabela sem motorista ─────────────────────────────────────────────────

def _sem_motorista(linha, viagens, agora) -> list[dict]:
    por_tabela: dict[str, list[dict]] = defaultdict(list)
    for v in viagens:
        if v.get("tabela"):
            por_tabela[v["tabela"]].append(v)
    saida = []
    for tabela, lista in sorted(por_tabela.items()):
        if not any(v.get("sem_motorista") for v in lista):
            continue
        progs = sorted((v for v in lista if v.get("prog_inicio")), key=lambda v: v["prog_inicio"])
        if not progs:
            continue
        primeira = progs[0]
        falta = primeira["prog_inicio"] - agora
        rodou = any(v["status_efetivo"] == "done" for v in lista)
        if rodou or falta > SEM_MOTORISTA_ANTECEDENCIA:
            continue
        perdidas = sum(v["status_efetivo"] != "done" and v["prog_inicio"] <= agora for v in progs)
        if falta >= timedelta(0):
            texto = f"Tabela {tabela} sem motorista no NIMER — 1ª partida às {hora_local(primeira['prog_inicio'])}"
        else:
            texto = (f"Tabela {tabela} sem motorista no NIMER e ainda não rodou — "
                     f"{perdidas} partida(s) desde {hora_local(primeira['prog_inicio'])}")
        nivel = "alerta" if falta <= SEM_MOTORISTA_URGENTE else "atencao"
        saida.append(_alerta("sem_motorista", nivel, linha.codigo, primeira["prog_inicio"], texto, viagem=primeira))
    return saida


# ─── 2. Partida atrasada no terminal ─────────────────────────────────────────

def _parado_no_terminal(leituras_carro: list[dict], centros, desde: datetime, agora: datetime) -> bool:
    """O carro está no terminal agora e não saiu dele desde o horário programado."""
    recentes = [r for r in leituras_carro if r["em"] <= agora]
    if not recentes or agora - recentes[-1]["em"] > LEITURA_RECENTE:
        return False
    depois = [r for r in recentes if r["em"] >= desde]
    if len(depois) < PARADO_MIN_LEITURAS:
        return False
    return all(min(_distancia((r["la"], r["lo"]), c) for c in centros) <= RAIO_TERMINAL_M for r in depois)


def _partida_atrasada(linha, viagens, leituras_por_carro, agora) -> list[dict]:
    if not linha.tem_mapa:
        return []
    saida = []
    for v in viagens:
        prog = v.get("prog_inicio")
        if prog is None or v["status_efetivo"] == "done" or v.get("saida_sim") is not None or not v.get("carro"):
            continue
        atraso = agora - prog
        if atraso <= ATRASO_TOLERANCIA or atraso > ATRASO_MAX:
            continue
        origem = _origem(linha.destino, v.get("sentido"))
        centros = linha.terminais.get(origem) if origem else None
        if not centros:
            continue
        if _parado_no_terminal(leituras_por_carro.get(v["carro"], []), centros, prog, agora):
            minutos = int(atraso.total_seconds() // 60)
            texto = (f"Partida das {hora_local(prog)} (tabela {v.get('tabela')}) atrasada {minutos} min — "
                     f"carro {v['carro']} parado no {linha.nomes_terminais.get(origem, origem)}")
            saida.append(_alerta("partida_atrasada", "alerta", linha.codigo, prog, texto, viagem=v))
    return saida


# ─── 2b. Horário cheio: não liberar antes / saiu adiantado ───────────────────

def _horario_cheio(linha, viagens, leituras_por_carro, agora) -> list[dict]:
    saida = []
    for v in viagens:
        prog = v.get("prog_inicio")
        if prog is None or v.get("duplicada") or not horario_cheio(prog):
            continue
        faixa = f"{prog.astimezone(FUSO).hour:02d}h"
        real = v.get("real_inicio") if v["status_efetivo"] == "done" else None
        sim = v["saida_sim"].em if v.get("saida_sim") is not None else None
        saiu = real or sim
        if saiu is not None:
            if saiu < prog and agora - saiu <= SAIU_ADIANTADO_JANELA:
                fonte = "NIMER" if real else "SIM"
                texto = (f"Carro {v.get('carro') or '—'} (tabela {v.get('tabela')}) saiu adiantado às {hora_local(saiu)} "
                         f"({fonte}) — a partida das {hora_local(prog)} perde o ICV da faixa {faixa}")
                saida.append(_alerta("saiu_adiantado", "alerta", linha.codigo, saiu, texto, viagem=v))
            continue
        if not linha.tem_mapa or not v.get("carro") or not (prog - NAO_LIBERAR_ANTECEDENCIA <= agora < prog):
            continue
        origem = _origem(linha.destino, v.get("sentido"))
        centros = linha.terminais.get(origem) if origem else None
        if centros and _parado_no_terminal(leituras_por_carro.get(v["carro"], []), centros,
                                           agora - timedelta(minutes=PARADO_MIN_LEITURAS), agora):
            texto = (f"Não liberar antes das {hora_local(prog)} — carro {v['carro']} (tabela {v.get('tabela')}) "
                     f"no {linha.nomes_terminais.get(origem, origem)}: sair antes perde o ICV da faixa {faixa}")
            saida.append(_alerta("nao_liberar_antes", "atencao", linha.codigo, prog, texto, viagem=v))
    return saida


# ─── 3. Carro trocado de linha ───────────────────────────────────────────────

def _carro_trocado_linha(linha, viagens, leituras_outras_linhas, agora) -> list[dict]:
    """`leituras_outras_linhas`: leituras recentes de carros escalados aqui, rodando em outra linha."""
    ultima: dict[str, dict] = {}
    for r in leituras_outras_linhas:
        if r.get("linha") == linha.codigo or agora - r["em"] > OUTRA_LINHA_RECENTE or r["em"] > agora:
            continue
        if r["prefixo"] not in ultima or r["em"] > ultima[r["prefixo"]]["em"]:
            ultima[r["prefixo"]] = r
    saida = []
    vistos = set()
    for v in sorted(viagens, key=lambda v: v.get("prog_inicio") or agora):
        carro = v.get("carro")
        if not carro or carro in vistos or carro not in ultima:
            continue
        pendentes = [o for o in viagens if o.get("carro") == carro and o["status_efetivo"] != "done"
                     and o.get("prog_inicio") and o["prog_inicio"] + timedelta(hours=1) >= agora]
        if not pendentes:
            continue
        vistos.add(carro)
        r = ultima[carro]
        prox = min(pendentes, key=lambda o: o["prog_inicio"])
        texto = (f"Carro {carro} (tabela {prox.get('tabela')}) está rodando na {r['linha']} pelo SIM "
                 f"(última posição {hora_local(r['em'])})")
        saida.append(_alerta("carro_trocado_linha", "alerta", linha.codigo, prox["prog_inicio"], texto,
                             viagem=prox, chave_extra=f"|{r['linha']}"))
    return saida


# ─── 4. Buraco de intervalo ──────────────────────────────────────────────────

def _saidas_realizadas(viagens, saidas_sim, sentido) -> list[datetime]:
    """Saídas do sentido: o que o NIMER diz que saiu (real_inicio) + o que só o SIM viu."""
    tempos = [v["real_inicio"] for v in viagens
              if v.get("sentido") == sentido and v["status_efetivo"] == "done" and v.get("real_inicio")
              and not v.get("duplicada")]
    casadas = {id(v["saida_sim"]) for v in viagens if v.get("saida_sim") is not None}
    tempos += [s.em for s in saidas_sim if s.sentido == sentido and id(s) not in casadas]
    tempos.sort()
    dedup: list[datetime] = []
    for t in tempos:
        if not dedup or t - dedup[-1] > timedelta(minutes=1):
            dedup.append(t)
    return dedup


def _intervalo_programado(progs: list[datetime], a: datetime, b: datetime) -> Optional[timedelta]:
    janela = [p for p in progs if a - BURACO_JANELA <= p <= b + BURACO_JANELA]
    difs = [(y - x).total_seconds() for x, y in zip(janela, janela[1:]) if y > x]
    return timedelta(seconds=statistics.median(difs)) if difs else None


def _buraco_intervalo(linha, viagens, saidas_sim, agora) -> list[dict]:
    saida = []
    for sentido in sorted({v.get("sentido") for v in viagens if v.get("sentido")}):
        progs = sorted(v["prog_inicio"] for v in viagens if v.get("sentido") == sentido and v.get("prog_inicio"))
        if len(progs) < 2:
            continue
        reais = [t for t in _saidas_realizadas(viagens, saidas_sim, sentido) if t <= agora]
        destino = linha.nomes_terminais.get(linha.destino.get(sentido), "")
        marcos = list(zip(reais, reais[1:]))
        # buraco em aberto: da última saída até agora, se ainda havia partida programada
        if reais and any(reais[-1] < p <= agora for p in progs):
            marcos.append((reais[-1], None))
        for a, b in marcos:
            fim = b or agora
            h = _intervalo_programado(progs, a, fim)
            if h is None or h.total_seconds() <= 0:
                continue
            gap = fim - a
            if gap <= h * BURACO_FATOR:
                continue
            ativo = b is None
            if not ativo and agora - b > JANELA_ALERTA_ENCERRADO:
                continue  # buraco já fechado há mais de 20 min: sai da fila
            nivel = "alerta" if gap > h * BURACO_GRAVE else "atencao"
            gmin, hmin = int(gap.total_seconds() // 60), int(h.total_seconds() // 60)
            texto = (f"Buraco de {gmin} min sem saída no sentido {destino or sentido} desde {hora_local(a)} "
                     f"(programado: a cada {hmin} min)" + ("" if ativo else f" — fechou às {hora_local(b)}"))
            saida.append(_alerta("buraco_intervalo", nivel, linha.codigo, a, texto, ativo=ativo,
                                 chave_extra=f"|s{sentido}"))
    return saida


# ─── 5. Realizada e não contabilizada ────────────────────────────────────────

def _nao_contabilizada(linha, viagens) -> list[dict]:
    saida = []
    for v in viagens:
        if v["status_efetivo"] == "done" and v.get("sim_viu") == "nao" and not v.get("duplicada"):
            texto = (f"Partida das {hora_local(v['prog_inicio'])} (tabela {v.get('tabela')}) realizada no NIMER, "
                     "mas o SIM não viu a saída em ±15 min — pode não contar no ICV oficial")
            saida.append(_alerta("nao_contabilizada", "atencao", linha.codigo, v["prog_inicio"], texto, viagem=v))
    return saida


# ─── 6. Fase 1 (cerca virtual) ───────────────────────────────────────────────

def alertas_da_cerca(linha, alertas_ao_vivo: list[dict], viagens) -> list[dict]:
    """alertas_agora de monitoramento_ao_vivo → mesmo formato, com a tabela do carro."""
    tabela_do_carro = {}
    com_horario = sorted((v for v in viagens if v.get("prog_inicio")), key=lambda v: v["prog_inicio"])
    for v in com_horario + [v for v in viagens if not v.get("prog_inicio")]:
        if v.get("carro"):
            tabela_do_carro.setdefault(v["carro"], v)
    saida = []
    for a in alertas_ao_vivo:
        carro = a.get("prefixo_patio")
        inicio = datetime.fromisoformat(a["inicio_em"]) if a.get("inicio_em") else None
        saida.append(_alerta(a["tipo"], NIVEL_FASE1.get(a["tipo"], a.get("nivel", "atencao")), linha.codigo,
                             inicio, a["texto"], carro=carro, viagem=tabela_do_carro.get(carro),
                             ativo=a.get("ativo", True)))
    return saida


# ─── Tudo junto ──────────────────────────────────────────────────────────────

def calcular_alertas(linha, resultado: dict, *, leituras_por_carro: dict[str, list[dict]],
                     leituras_outras_linhas: list[dict], alertas_ao_vivo: list[dict], agora: datetime) -> list[dict]:
    viagens = resultado["viagens"]
    alertas = (
        _sem_motorista(linha, viagens, agora)
        + _partida_atrasada(linha, viagens, leituras_por_carro, agora)
        + _horario_cheio(linha, viagens, leituras_por_carro, agora)
        + _carro_trocado_linha(linha, viagens, leituras_outras_linhas, agora)
        + _buraco_intervalo(linha, viagens, resultado["saidas_sim"], agora)
        + _nao_contabilizada(linha, viagens)
        + alertas_da_cerca(linha, alertas_ao_vivo, viagens)
    )
    return ordenar(alertas)


def ordenar(alertas: list[dict]) -> list[dict]:
    """Mais urgentes primeiro: alerta antes de atenção; ativos antes de encerrados;
    dentro disso, o mais antigo primeiro (está esperando há mais tempo)."""
    return sorted(alertas, key=lambda a: (a["nivel"] != "alerta", not a["ativo"], a["inicio_em"] or ""))


def leituras_por_carro(leituras: list[dict]) -> dict[str, list[dict]]:
    por: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(leituras, key=lambda r: r["em"]):
        por[r["prefixo"]].append(r)
    return por


def montar_alertas(db, linha, resultado: dict, agora: Optional[datetime] = None) -> list[dict]:
    """Carregador: lê do banco o que os alertas precisam além do `resultado` do bloco D."""
    from datetime import timezone

    from app.services import monitoramento_ao_vivo as ao_vivo
    from app.services.fiscalizacao_tempo_real import leituras_do_dia

    agora = agora or datetime.now(timezone.utc)
    carros = {v["carro"] for v in resultado["viagens"] if v.get("carro")}
    outras = [r for r in leituras_do_dia(db, None, agora - OUTRA_LINHA_RECENTE, prefixos=carros)
              if r["linha"] != linha.codigo] if carros else []
    cerca = ao_vivo.ao_vivo_com_cache(db, linha).get("alertas_agora", []) if linha.tem_mapa else []
    return calcular_alertas(
        linha, resultado,
        leituras_por_carro=leituras_por_carro(resultado.get("leituras", [])),
        leituras_outras_linhas=outras, alertas_ao_vivo=cerca, agora=agora,
    )
