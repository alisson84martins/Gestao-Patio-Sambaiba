"""Cruzamento NIMER × SIM, partida a partida, e ICV/IPP ao vivo (fase 2, bloco D).

Núcleo PURO (dicts e datetimes, sem banco) + um carregador no fim
(`montar_linha_tempo_real`) que lê o banco e chama o núcleo. Os testes usam
o núcleo direto.

Regras (validadas no modelo de cruzamento de 10/11/2026):
- SAÍDA VISTA PELO SIM: o carro troca para o sentido S dentro do terminal de
  origem de S e sai do raio do terminal (250 m) andando de verdade (≥ 1 km
  da origem). A hora da saída é o meio entre a última leitura dentro do raio
  e a primeira fora (ou a primeira fora, se o buraco passar de 3 min).
- CASAMENTO viagem NIMER ↔ saída SIM: 1º mesma vaga — mesmo carro, mesmo
  sentido, até 30 min do horário programado; 2º mesmo sentido até 15 min,
  qualquer carro. Uma saída casa com UMA viagem só (a mais próxima).
- DUPLICADO DO NIMER: duas viagens `done` do mesmo carro, mesmo sentido,
  com saída real até 10 min uma da outra = a mesma saída registrada duas
  vezes → a segunda não conta (nem no ICV do NIMER).
- STATUS EFETIVO = NIMER, ou a correção mais recente do fiscal (bloco G).

ICV ao vivo, DUAS FONTES LADO A LADO (D20 — nunca numa coluna só):
  icv_nimer = realizadas (status efetivo done, sem duplicado) ÷ programadas até agora
  icv_sim   = saídas vistas pelo SIM ÷ programadas até agora
  (o ICV do SIM é a NOSSA leitura das saídas, não o número oficial — decisão 4)
  e os dois do dia inteiro (÷ programadas do dia). Podem passar de 100% (D30).
  Agregado de várias linhas: soma numerador e denominador antes de dividir (D22).
IPP = PONTUALIDADE DE PARTIDA (regra do Alisson, 11/11/2026 00:58):
  pontual = saída real em [programado − 3 min, programado + 3 min]; no
  HORÁRIO CHEIO (07:00, 08:00…) a janela é [programado, programado + 3 min]
  — sair antes perde o ICV da faixa horária seguinte. IPP = pontuais ÷
  programadas (perdida conta no denominador, extra não entra). Fonte da hora:
  NIMER (status efetivo, com correção do fiscal); ao lado, o IPP pela saída
  vista no SIM (nossa leitura, D20). A tolerância vem de
  fiscalizacao.parametro `ipp_tolerancia_min` (D29), padrão 3. A
  pontualidade que o próprio NIMER calcula (onTimePercent) fica ao lado só
  como conferência, com o nome dela.
Perda absoluta (D23) = programadas até agora − realizadas programadas.

R.A × S.O.S (decisão do Alisson, 10/11/2026 22:05): a viagem perdida com
"Recolhe Anormal / SOS" fica R.A (provisório); vira S.O.S se o MESMO carro
fizer depois uma viagem `done` na mesma linha (NIMER) ou sair de novo do
terminal pelo SIM; fica R.A se o SIM mostrou o carro na G3 e ele não voltou
até o fim do serviço. O fiscal pode corrigir.

⛔ Nada aqui grava nada (D24): tudo calculado a cada chamada.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional

from app.core.config import FUSO_OPERACAO

RAIO_TERMINAL_M = 250
SAIDA_MIN_M = 1000              # longe da origem para ser viagem de verdade
LACUNA_SAIDA = timedelta(minutes=3)
CASA_MESMA_VAGA = timedelta(minutes=30)
CASA_QUALQUER_CARRO = timedelta(minutes=15)
DUPLICADO_NIMER = timedelta(minutes=10)
RAIO_GARAGEM_M = 300
PROJECAO_JANELA = timedelta(hours=2)
IPP_TOLERANCIA_MIN = 3          # padrão; o valor vale de fiscalizacao.parametro (ipp_tolerancia_min)
PROJECAO_MIN_VIAGENS = 4

CONTADORES = ("FALTA_OPERADORES", "RA", "SOS", "ATRASO_GARAGEM", "TROCA_OPERACIONAL")
MOTIVOS_CORRIGIVEIS = CONTADORES + ("TRANSITO", "OUTRO")

# Motivo do NIMER → fechamento (decisão do Alisson, 11/11/2026 00:59: segue o
# padrão do NIMER). 🔑 TABELA ÚNICA — para ajustar, mexa só aqui. A chave é o
# texto do NIMER sem o número, sem acento e sem caixa ("6- Atraso…" e
# "6 - Atraso…" são o mesmo; ver normalizar_motivo).
#   RECOLHE  → R.A ou S.O.S pela regra da fase 2 (voltou para a linha = S.O.S)
#   TRANSITO → OBS (D27, fora dos contadores)
#   Qualquer motivo FORA desta tabela → linha própria no fechamento com o
#   nome do NIMER (ex.: "FALTA DE MOTORISTA: 01"), contada — ⛔ nunca no OBS.
MOTIVOS_NIMER = {
    "falta de operador": "FALTA_OPERADORES",
    "falta de operadores": "FALTA_OPERADORES",
    "atraso de garagem - falta de carro": "ATRASO_GARAGEM",
    "recolhe anormal / sos": "RECOLHE",
    "transito": "TRANSITO",
}


def _sem_acento(texto: str) -> str:
    tabela = str.maketrans("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ", "aaaaeeiooouc" + "AAAAEEIOOOUC")
    return texto.translate(tabela)


def _sem_numero(texto: str) -> str:
    """Tira o número do NIMER da frente ("6- Atraso", "6 - Atraso", "06) Atraso")."""
    return re.sub(r"^\s*\d+\s*[-–).:]?\s*", "", texto or "").strip()


def normalizar_motivo(texto: Optional[str]) -> str:
    """Chave de MOTIVOS_NIMER: sem número, sem acento, minúsculo, espaços únicos
    e um espaço em volta de "-" e "/"."""
    t = _sem_acento(_sem_numero(texto or "")).lower()
    t = re.sub(r"\s*([-–/])\s*", lambda m: f" {'-' if m.group(1) == '–' else m.group(1)} ", t)
    return re.sub(r"\s+", " ", t).strip()


def nome_motivo_nimer(texto: Optional[str]) -> str:
    """Nome do motivo como linha própria do fechamento: "FALTA DE MOTORISTA"."""
    return re.sub(r"\s+", " ", _sem_numero(texto or "")).upper()


def motivo_canonico(texto: Optional[str]) -> Optional[str]:
    """Texto do NIMER → código de MOTIVOS_NIMER, ou None (motivo fora da tabela)."""
    if not texto or not texto.strip():
        return None
    return MOTIVOS_NIMER.get(normalizar_motivo(texto))


# ─── Geometria mínima (mesma conta do regras.py, sem depender do traçado) ────

def _distancia(a, b) -> float:
    from app.services.monitoramento_regras import distancia
    return distancia(a, b)


def _perto(p, centros, raio) -> float:
    return min((_distancia(p, c) for c in centros), default=float("inf"))


@dataclass
class Saida:
    prefixo: str        # 4 dígitos (pátio)
    sentido: int
    em: datetime


def _origem(destino: dict[int, str], sentido: int) -> Optional[str]:
    dest = destino.get(sentido)
    outros = [t for t in set(destino.values()) if t != dest]
    return outros[0] if outros else None


def saidas_do_sim(leituras: Iterable[dict], terminais: dict[str, list], destino: dict[int, str]) -> list[Saida]:
    """Leituras {prefixo (4 díg.), em (datetime), s, la, lo} → saídas de terminal.

    Vazio quando a linha não tem centros de terminal (sem traçado)."""
    if not terminais:
        return []
    por_carro: dict[str, list[dict]] = defaultdict(list)
    for r in leituras:
        por_carro[r["prefixo"]].append(r)
    saidas: list[Saida] = []
    for prefixo, lista in por_carro.items():
        lista.sort(key=lambda r: r["em"])
        trechos: list[list[dict]] = []
        for r in lista:
            if trechos and trechos[-1][-1]["s"] == r["s"]:
                trechos[-1].append(r)
            else:
                trechos.append([r])
        for trecho in trechos:
            s = trecho[0]["s"]
            origem = _origem(destino, s)
            centros = terminais.get(origem) if origem else None
            if not centros:
                continue
            dentro = None
            saida = None
            for r in trecho:
                d = _perto((r["la"], r["lo"]), centros, RAIO_TERMINAL_M)
                if d <= RAIO_TERMINAL_M:
                    dentro = r
                    saida = None
                elif dentro is not None and saida is None:
                    saida = (dentro, r)
                if saida and d >= SAIDA_MIN_M:
                    a, b = saida
                    em = a["em"] + (b["em"] - a["em"]) / 2 if b["em"] - a["em"] <= LACUNA_SAIDA else b["em"]
                    saidas.append(Saida(prefixo=prefixo, sentido=s, em=em))
                    break
    saidas.sort(key=lambda x: x.em)
    return saidas


# ─── Status efetivo (NIMER + correção do fiscal) ─────────────────────────────

def aplicar_correcoes(viagem: dict, correcoes: dict[str, dict]) -> dict:
    """`correcoes`: {campo: {"valor": ..., "autor": ..., "em": ...}} (a mais recente de cada campo)."""
    v = dict(viagem)
    v["status_nimer"] = viagem.get("status")
    v["motivo_nimer"] = viagem.get("motivo")
    v["carro_nimer"] = viagem.get("carro")
    v["real_inicio_nimer"] = viagem.get("real_inicio")
    v["status_efetivo"] = viagem.get("status")
    v["motivo_corrigido"] = None
    v["corrigida"] = bool(correcoes)
    v["correcoes"] = {campo: c for campo, c in correcoes.items()}
    if "status" in correcoes:
        v["status_efetivo"] = correcoes["status"]["valor"]
    if "motivo" in correcoes:
        v["motivo_corrigido"] = correcoes["motivo"]["valor"]
    if "carro" in correcoes:
        v["carro"] = correcoes["carro"]["valor"]
    if "real_inicio" in correcoes:
        v["real_inicio"] = correcoes["real_inicio"]["valor"]
    return v


def marcar_duplicados(viagens: list[dict]) -> None:
    """Mesma saída do mesmo carro registrada para duas viagens (até 10 min) → só a 1ª conta."""
    feitas = sorted(
        (v for v in viagens if v["status_efetivo"] == "done" and v.get("carro") and v.get("real_inicio")),
        key=lambda v: v["real_inicio"],
    )
    ultima: dict[tuple, datetime] = {}
    for v in viagens:
        v["duplicada"] = False
    for v in feitas:
        chave = (v["carro"], v.get("sentido"))
        if chave in ultima and v["real_inicio"] - ultima[chave] <= DUPLICADO_NIMER:
            v["duplicada"] = True
            continue
        ultima[chave] = v["real_inicio"]


def casar(viagens: list[dict], saidas: list[Saida]) -> None:
    """Grava em cada viagem `saida_sim` (Saida ou None). Duas passadas gulosas
    pela menor diferença de horário: mesma vaga, depois qualquer carro."""
    for v in viagens:
        v["saida_sim"] = None
    livres = set(range(len(saidas)))

    def passada(mesma_vaga: bool, limite: timedelta):
        pares = []
        for i, v in enumerate(viagens):
            if v["saida_sim"] is not None or v.get("prog_inicio") is None or v.get("duplicada"):
                continue
            for j in livres:
                s = saidas[j]
                if s.sentido != v.get("sentido"):
                    continue
                if mesma_vaga and s.prefixo != v.get("carro"):
                    continue
                dif = abs(s.em - v["prog_inicio"])
                if dif <= limite:
                    pares.append((dif, i, j))
        pares.sort()
        for _, i, j in pares:
            if viagens[i]["saida_sim"] is None and j in livres:
                viagens[i]["saida_sim"] = saidas[j]
                livres.discard(j)

    passada(True, CASA_MESMA_VAGA)
    passada(False, CASA_QUALQUER_CARRO)


# ─── R.A × S.O.S ─────────────────────────────────────────────────────────────

def classificar_recolhe(v: dict, viagens: list[dict], saidas: list[Saida],
                        g3_visto: dict[str, datetime], fim_servico: bool) -> str:
    """'SOS' | 'RA' | 'RA_PROVISORIO' para uma viagem perdida com motivo de recolhe."""
    carro = v.get("carro")
    momento = v.get("prog_inicio")
    if not carro or momento is None:
        return "RA_PROVISORIO" if not fim_servico else "RA"
    voltou_nimer = any(
        o is not v and o.get("carro") == carro and o["status_efetivo"] == "done"
        and (o.get("real_inicio") or o.get("prog_inicio") or momento) > momento
        for o in viagens
    )
    voltou_sim = any(s.prefixo == carro and s.em > momento for s in saidas)
    if voltou_nimer or voltou_sim:
        return "SOS"
    if fim_servico and carro in g3_visto and g3_visto[carro] >= momento - timedelta(minutes=30):
        return "RA"
    return "RA_PROVISORIO"


def contador_da_viagem(v: dict) -> Optional[str]:
    """Contador fixo do fechamento desta viagem perdida (None = Trânsito/OBS
    ou motivo fora da tabela, que vira linha própria — ver `motivo_proprio`)."""
    if v["status_efetivo"] == "done":
        return None
    if v.get("motivo_corrigido"):
        m = v["motivo_corrigido"]
        return m if m in CONTADORES else None
    if v.get("recolhe"):
        return "SOS" if v["recolhe"] == "SOS" else "RA"
    canon = motivo_canonico(v.get("motivo_nimer"))
    return canon if canon in CONTADORES else None


def motivo_proprio(v: dict) -> Optional[str]:
    """Motivo do NIMER fora de MOTIVOS_NIMER → nome da linha própria no fechamento."""
    if v["status_efetivo"] == "done" or v.get("motivo_corrigido"):
        return None
    texto = v.get("motivo_nimer")
    if not texto or not _sem_numero(texto) or motivo_canonico(texto) is not None:
        return None
    return nome_motivo_nimer(texto)


# ─── Pontualidade de partida (IPP) ───────────────────────────────────────────

def horario_cheio(prog: datetime) -> bool:
    local = prog.astimezone(FUSO_OPERACAO)
    return local.minute == 0 and local.second == 0


def classificar_pontualidade(prog: Optional[datetime], real: Optional[datetime],
                             tolerancia_min: float = IPP_TOLERANCIA_MIN) -> dict:
    """{"classe": pontual | atrasada | adiantada | sem_dado, "desvio_min": int|None}.

    Horário cheio: qualquer saída ANTES do programado é adiantada (perde a
    faixa horária). Fora dele: ± tolerância."""
    if prog is None or real is None:
        return {"classe": "sem_dado", "desvio_min": None}
    desvio = (real - prog).total_seconds()
    tol = tolerancia_min * 60
    minimo = 0 if horario_cheio(prog) else -tol
    if desvio < minimo:
        classe = "adiantada"
    elif desvio > tol:
        classe = "atrasada"
    else:
        classe = "pontual"
    desvio_min = int(desvio // 60) if desvio < 0 else int(round(desvio / 60))
    return {"classe": classe, "desvio_min": desvio_min}


def pontualidade_da_viagem(v: dict, agora: datetime, tolerancia_min: float, fonte: str = "nimer") -> Optional[dict]:
    """Classe da partida programada (None = futura ou extra, fora do IPP)."""
    prog = v.get("prog_inicio")
    if prog is None or prog > agora:
        return None
    if fonte == "sim":
        s = v.get("saida_sim")
        if s is None:
            return {"classe": "sem_dado" if v.get("sim_viu") == "sem_dado" else "perdida", "desvio_min": None}
        return classificar_pontualidade(prog, s.em, tolerancia_min)
    if v["status_efetivo"] != "done":
        return {"classe": "perdida", "desvio_min": None}
    return classificar_pontualidade(prog, v.get("real_inicio"), tolerancia_min)


# ─── ICV por faixa horária ───────────────────────────────────────────────────

def _hora_sp(dt: datetime) -> int:
    return dt.astimezone(FUSO_OPERACAO).hour


def icv_por_faixa(viagens: list[dict], agora: datetime) -> tuple[list[dict], list[dict]]:
    """ICV por faixa horária (hora cheia de SP). O programado conta na faixa do
    HORÁRIO PROGRAMADO; o realizado, na faixa da HORA REAL DE SAÍDA — é assim
    que a saída adiantada antes da hora cheia (06:58 no lugar de 07:00) cai na
    faixa anterior e a faixa das 07h perde a viagem.

    Devolve (faixas, adiantamentos_de_faixa)."""
    prog: dict[int, int] = defaultdict(int)
    real: dict[int, int] = defaultdict(int)
    adiantamentos = []
    for v in viagens:
        if v.get("duplicada"):
            continue
        p = v.get("prog_inicio")
        if p is not None and p <= agora:
            prog[_hora_sp(p)] += 1
        if v["status_efetivo"] != "done":
            continue
        saida = v.get("real_inicio") or p
        if saida is None or saida > agora:
            continue
        real[_hora_sp(saida)] += 1
        if p is not None and saida < p and _hora_sp(saida) != _hora_sp(p):
            h = _hora_sp(p)
            adiantamentos.append({
                "faixa": h, "tabela": v.get("tabela"), "carro": v.get("carro"),
                "programado": hora_local(p), "saida": hora_local(saida),
                "texto": f"faixa {h:02d}h perdeu viagem por saída adiantada às {hora_local(saida)} "
                         f"(tabela {v.get('tabela') or '—'}, programada {hora_local(p)})",
            })
    faixas = [{"faixa": h, "programadas": prog.get(h, 0), "realizadas": real.get(h, 0),
               "icv": _pct(real.get(h, 0), prog.get(h, 0))}
              for h in sorted(set(prog) | set(real))]
    return faixas, adiantamentos


# ─── Linha inteira ───────────────────────────────────────────────────────────

def _sim_cobriu(instantes: list[datetime], momento: datetime) -> bool:
    import bisect
    i = bisect.bisect_left(instantes, momento - CASA_QUALQUER_CARRO)
    return i < len(instantes) and instantes[i] <= momento + CASA_QUALQUER_CARRO


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    return round(num / den * 100, 2) if den else None


def calcular_linha(
    *,
    viagens_nimer: list[dict],
    correcoes: dict[int, dict[str, dict]],
    leituras: list[dict],
    terminais: dict[str, list],
    destino: dict[int, str],
    garagem: tuple[float, float],
    agora: datetime,
    tem_sim: bool,
    pontualidade_nimer: Optional[float] = None,
    ipp_tolerancia_min: float = IPP_TOLERANCIA_MIN,
) -> dict:
    """Núcleo puro. `leituras`: SIM da própria linha, {prefixo (4 díg.), em, s, la, lo}."""
    viagens = [aplicar_correcoes(v, correcoes.get(v["id"], {})) for v in viagens_nimer]
    viagens.sort(key=lambda v: (v.get("prog_inicio") or datetime.max.replace(tzinfo=timezone.utc), v.get("tabela") or ""))
    marcar_duplicados(viagens)
    saidas = saidas_do_sim(leituras, terminais, destino) if tem_sim else []
    casar(viagens, saidas)

    g3_visto: dict[str, datetime] = {}
    for r in leituras:
        if _distancia((r["la"], r["lo"]), garagem) <= RAIO_GARAGEM_M:
            if r["prefixo"] not in g3_visto or r["em"] > g3_visto[r["prefixo"]]:
                g3_visto[r["prefixo"]] = r["em"]
    fins = [v.get("prog_fim") or v.get("prog_inicio") for v in viagens if v.get("prog_inicio")]
    fim_servico = bool(fins) and agora > max(fins)

    sim_ativo = tem_sim and bool(terminais)
    instantes = sorted(r["em"] for r in leituras)
    for v in viagens:
        s = v["saida_sim"]
        v["sim_saida_em"] = s.em if s else None
        v["carro_real"] = s.prefixo if s else None
        v["carro_trocado_sim"] = bool(s and v.get("carro") and s.prefixo != v["carro"])
        if s:
            v["sim_viu"] = "sim"
        elif not sim_ativo or v.get("prog_inicio") is None:
            v["sim_viu"] = "sem_dado"
        elif v["prog_inicio"] + CASA_QUALQUER_CARRO < agora:
            # Só afirma "o SIM não viu" se o SIM estava enxergando a linha
            # naquela hora (alguma leitura dela em ±15 min); senão, sem dado.
            v["sim_viu"] = "nao" if _sim_cobriu(instantes, v["prog_inicio"]) else "sem_dado"
        else:
            v["sim_viu"] = "aguardando"
        v["recolhe"] = None
        if v["status_efetivo"] in ("lost", "missing") and motivo_canonico(v.get("motivo_nimer")) == "RECOLHE":
            v["recolhe"] = classificar_recolhe(v, viagens, saidas, g3_visto, fim_servico)
        v["contador"] = contador_da_viagem(v)
        v["motivo_proprio"] = motivo_proprio(v)
        v["motivo_canonico"] = v.get("motivo_corrigido") or motivo_canonico(v.get("motivo_nimer"))

    programadas = [v for v in viagens if v.get("prog_inicio") is not None]
    ate_agora = [v for v in programadas if v["prog_inicio"] <= agora]
    conta = [v for v in viagens if not v["duplicada"]]
    realizadas_prog_agora = sum(v["status_efetivo"] == "done" for v in ate_agora if not v["duplicada"])
    extras = sum(v["status_efetivo"] == "done" for v in conta if v.get("prog_inicio") is None)
    realizadas_dia = sum(v["status_efetivo"] == "done" for v in conta)
    saidas_ate_agora = sum(s.em <= agora for s in saidas)

    n_agora, n_dia = len(ate_agora), len(programadas)
    realizadas_agora = realizadas_prog_agora + extras
    perda = n_agora - realizadas_prog_agora

    # Projeção simples: as programadas que faltam saem na mesma taxa das
    # últimas 2 h (ou do dia, se teve pouca viagem nesse intervalo), no máximo 100%.
    recentes = [v for v in ate_agora if v["prog_inicio"] >= agora - PROJECAO_JANELA and not v["duplicada"]]
    base = recentes if len(recentes) >= PROJECAO_MIN_VIAGENS else [v for v in ate_agora if not v["duplicada"]]
    taxa = (sum(v["status_efetivo"] == "done" for v in base) / len(base)) if base else None
    projecao = None
    if taxa is not None and n_dia:
        restantes = n_dia - n_agora
        projecao = _pct(realizadas_agora + restantes * min(1.0, taxa), n_dia)

    # IPP: pontuais ÷ programadas até agora (duplicado do NIMER e extra fora).
    pontuais = pontuais_sim = 0
    for v in viagens:
        if v["duplicada"]:
            v["pontualidade"] = v["pontualidade_sim"] = None
            continue
        v["pontualidade"] = pontualidade_da_viagem(v, agora, ipp_tolerancia_min)
        v["pontualidade_sim"] = pontualidade_da_viagem(v, agora, ipp_tolerancia_min, "sim") if sim_ativo else None
        pontuais += bool(v["pontualidade"] and v["pontualidade"]["classe"] == "pontual")
        pontuais_sim += bool(v["pontualidade_sim"] and v["pontualidade_sim"]["classe"] == "pontual")
    n_ipp = sum(1 for v in ate_agora if not v["duplicada"])
    faixas, adiantamentos = icv_por_faixa(viagens, agora)
    classes = defaultdict(int)
    for v in viagens:
        if v.get("pontualidade"):
            classes[v["pontualidade"]["classe"]] += 1

    return {
        "viagens": viagens,
        "saidas_sim": saidas,
        "totais": {
            "programadas_ate_agora": n_agora,
            "programadas_dia": n_dia,
            "realizadas_nimer_ate_agora": realizadas_agora,
            "realizadas_nimer_dia": realizadas_dia,
            "extras_nimer": extras,
            "saidas_sim_ate_agora": saidas_ate_agora if sim_ativo else None,
            "perda_absoluta": perda,
            "duplicadas_nimer": sum(v["duplicada"] for v in viagens),
            "pontuais_nimer": pontuais,
            "pontuais_sim": pontuais_sim if sim_ativo else None,
            "programadas_ipp": n_ipp,
            "pontualidade_classes": dict(classes),
        },
        "icv": {
            "nimer_ate_agora": _pct(realizadas_agora, n_agora),
            "sim_ate_agora": _pct(saidas_ate_agora, n_agora) if sim_ativo else None,
            "nimer_dia": _pct(realizadas_dia, n_dia),
            "sim_dia": _pct(len(saidas), n_dia) if sim_ativo else None,
            "projecao_fechamento": projecao,
            "faixas": faixas,
            "adiantamentos_de_faixa": adiantamentos,
        },
        "ipp": {
            "ate_agora": _pct(pontuais, n_ipp),
            "sim_ate_agora": _pct(pontuais_sim, n_ipp) if sim_ativo else None,
            "dia": _pct(pontuais, n_dia),
            "tolerancia_min": ipp_tolerancia_min,
            "definicao": (f"pontuais ÷ programadas; pontual = saída em ±{ipp_tolerancia_min:g} min do programado "
                          f"(horário cheio: só de 0 a +{ipp_tolerancia_min:g} min)"),
            "sim_rotulo": "IPP pelo SIM = nossa leitura das saídas, não o número oficial",
        },
        "pontualidade_nimer": pontualidade_nimer,
        "fim_servico": fim_servico,
    }


def agregar(resultados: list[dict]) -> dict:
    """D22 — ponderado: soma numerador e denominador de todas as linhas antes de dividir."""
    soma = defaultdict(int)
    tem_sim = False
    for r in resultados:
        t = r["totais"]
        for chave in ("programadas_ate_agora", "programadas_dia", "realizadas_nimer_ate_agora",
                      "realizadas_nimer_dia", "perda_absoluta", "pontuais_nimer", "programadas_ipp"):
            soma[chave] += t.get(chave) or 0
        if t["saidas_sim_ate_agora"] is not None:
            tem_sim = True
            soma["saidas_sim_ate_agora"] += t["saidas_sim_ate_agora"]
            soma["programadas_com_sim"] += t["programadas_ate_agora"]
            soma["pontuais_sim"] += t.get("pontuais_sim") or 0
            soma["programadas_ipp_com_sim"] += t.get("programadas_ipp") or 0
    return {
        **dict(soma),
        "icv_nimer_ate_agora": _pct(soma["realizadas_nimer_ate_agora"], soma["programadas_ate_agora"]),
        "icv_sim_ate_agora": _pct(soma["saidas_sim_ate_agora"], soma["programadas_com_sim"]) if tem_sim else None,
        "icv_nimer_dia": _pct(soma["realizadas_nimer_dia"], soma["programadas_dia"]),
        "ipp_ate_agora": _pct(soma["pontuais_nimer"], soma["programadas_ipp"]),
        "ipp_sim_ate_agora": _pct(soma["pontuais_sim"], soma["programadas_ipp_com_sim"]) if tem_sim else None,
    }


# ─── Carregador (banco → núcleo) ─────────────────────────────────────────────

def inicio_servico(data_servico: date) -> datetime:
    """A data de serviço começa às 4h de SP (VIRADA_SERVICO)."""
    from app.services.monitoramento_cadastro import VIRADA_SERVICO
    return datetime.combine(data_servico, VIRADA_SERVICO, tzinfo=FUSO_OPERACAO).astimezone(timezone.utc)


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def correcoes_vigentes(db, viagem_ids: list[int]) -> dict[int, dict[str, dict]]:
    """A correção mais recente de cada campo, por viagem."""
    from sqlalchemy import select

    from app.models.monitoramento import ViagemCorrecao
    if not viagem_ids:
        return {}
    linhas = db.execute(
        select(ViagemCorrecao).where(ViagemCorrecao.nimer_viagem_id.in_(viagem_ids))
        .order_by(ViagemCorrecao.criado_em, ViagemCorrecao.id)
    ).scalars().all()
    saida: dict[int, dict[str, dict]] = defaultdict(dict)
    for c in linhas:
        valor = c.valor_corrigido
        if c.campo == "real_inicio" and valor:
            valor = _utc(datetime.fromisoformat(valor))
        saida[c.nimer_viagem_id][c.campo] = {"valor": valor, "autor": c.autor_funcionario_id,
                                             "em": _utc(c.criado_em), "justificativa": c.justificativa}
    return saida


def viagem_como_dict(v) -> dict:
    return {
        "id": v.id, "tabela": v.tabela, "sentido": v.sentido, "status": v.status,
        "prog_inicio": _utc(v.prog_inicio), "prog_fim": _utc(v.prog_fim),
        "real_inicio": _utc(v.real_inicio), "real_fim": _utc(v.real_fim),
        "fonte_inicio": v.fonte_inicio, "atraso_inicio_min": v.atraso_inicio_min,
        "motivo": v.motivo_perda_texto, "carro": v.carro, "motorista_re": v.motorista_re,
        "cobrador_re": v.cobrador_re, "sem_motorista": v.sem_motorista,
    }


def leituras_do_dia(db, linhas: Optional[list[str]], desde: datetime, prefixos: Optional[set[str]] = None) -> list[dict]:
    """Leituras do SIM desde `desde`, com prefixo já em 4 dígitos e a linha em que estavam."""
    from sqlalchemy import select

    from app.models.monitoramento import LeituraVeiculo
    from app.services.monitoramento_linhas import prefixo_patio
    query = select(LeituraVeiculo.prefixo, LeituraVeiculo.linha_codigo, LeituraVeiculo.posicao_em,
                   LeituraVeiculo.sentido, LeituraVeiculo.lat, LeituraVeiculo.lon, LeituraVeiculo.velocidade) \
        .where(LeituraVeiculo.posicao_em >= desde)
    if linhas is not None:
        query = query.where(LeituraVeiculo.linha_codigo.in_(linhas))
    saida = []
    for p, linha, em, s, la, lo, vel in db.execute(query).all():
        pat = prefixo_patio(p)
        if prefixos is not None and pat not in prefixos:
            continue
        saida.append({"prefixo": pat, "linha": linha, "em": _utc(em), "s": s, "la": la, "lo": lo, "vel": vel})
    return saida


def montar_linha_tempo_real(db, linha, data_servico: date, agora: Optional[datetime] = None) -> dict:
    """Lê NIMER + correções + SIM do banco e calcula (`linha`: LinhaMonitorada)."""
    from sqlalchemy import select

    from app.models.monitoramento import Coleta, NimerColeta
    from app.services.monitoramento_linhas import GARAGEM_G3
    from app.services.monitoramento_nimer import viagens_da_linha

    agora = agora or datetime.now(timezone.utc)
    viagens = [viagem_como_dict(v) for v in viagens_da_linha(db, linha.codigo, data_servico)]
    correcoes = correcoes_vigentes(db, [v["id"] for v in viagens])
    desde = inicio_servico(data_servico)
    leituras = [r for r in leituras_do_dia(db, [linha.codigo], desde) if r["em"] <= agora]
    ultima_sim = db.execute(
        select(Coleta.recebido_em).where(Coleta.status == "ok").order_by(Coleta.recebido_em.desc()).limit(1)
    ).scalar_one_or_none()
    ultima_nimer = db.execute(
        select(NimerColeta).where(NimerColeta.linha_codigo == linha.codigo).order_by(NimerColeta.recebido_em.desc())
        .limit(1)
    ).scalar_one_or_none()
    nimer_ok = db.execute(
        select(NimerColeta.recebido_em).where(NimerColeta.linha_codigo == linha.codigo, NimerColeta.status == "ok")
        .order_by(NimerColeta.recebido_em.desc()).limit(1)
    ).scalar_one_or_none()
    from app.models.fiscalizacao import Parametro
    tolerancia = db.get(Parametro, "ipp_tolerancia_min")
    resultado = calcular_linha(
        ipp_tolerancia_min=float(tolerancia.valor) if tolerancia is not None else IPP_TOLERANCIA_MIN,
        viagens_nimer=viagens, correcoes=correcoes, leituras=leituras,
        terminais=linha.terminais if linha.tem_mapa else {}, destino=linha.destino, garagem=GARAGEM_G3,
        agora=agora, tem_sim=ultima_sim is not None,
        pontualidade_nimer=float(ultima_nimer.pontualidade_pct)
        if ultima_nimer is not None and ultima_nimer.pontualidade_pct is not None else None,
    )
    resultado["leituras"] = leituras
    resultado["saude"] = {
        "sim_ultima_ok_em": _utc(ultima_sim),
        "nimer_status": ultima_nimer.status if ultima_nimer else None,
        "nimer_ultima_em": _utc(ultima_nimer.recebido_em) if ultima_nimer else None,
        "nimer_ultima_ok_em": _utc(nimer_ok),
    }
    return resultado


def hora_local(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(FUSO_OPERACAO).strftime("%H:%M") if dt else None


def instante_do_horario(data_servico: date, hhmm: time) -> datetime:
    """'HH:MM' do dia de serviço → instante (antes das 4h = madrugada do dia seguinte)."""
    from app.services.monitoramento_cadastro import VIRADA_SERVICO
    dt = datetime.combine(data_servico, hhmm, tzinfo=FUSO_OPERACAO)
    if hhmm < VIRADA_SERVICO:
        dt += timedelta(days=1)
    return dt.astimezone(timezone.utc)


# ─── Saída para as telas ─────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def serializar_partida(v: dict, linha) -> dict:
    return {
        "id": v["id"],
        "tabela": v.get("tabela"),
        "sentido": v.get("sentido"),
        "destino": linha.destino.get(v.get("sentido")),
        "programado": hora_local(v.get("prog_inicio")),
        "prog_inicio": _iso(v.get("prog_inicio")),
        "saida_real": hora_local(v.get("real_inicio")),
        "fonte_inicio": v.get("fonte_inicio"),
        "sim_viu": v["sim_viu"],
        "sim_saida": hora_local(v.get("sim_saida_em")),
        "status_nimer": v["status_nimer"],
        "status_efetivo": v["status_efetivo"],
        "corrigida": v["corrigida"],
        "campos_corrigidos": sorted(v["correcoes"]),
        "motivo_nimer": v.get("motivo_nimer"),
        "motivo": v.get("motivo_canonico"),
        "contador": v.get("contador"),
        "motivo_proprio": v.get("motivo_proprio"),
        "recolhe": v.get("recolhe"),
        "carro_programado": v.get("carro"),
        "carro_nimer": v.get("carro_nimer"),
        "carro_real_sim": v.get("carro_real"),
        "carro_trocado_sim": v.get("carro_trocado_sim"),
        "duplicada_nimer": v.get("duplicada"),
        "pontualidade": v.get("pontualidade"),
        "pontualidade_sim": v.get("pontualidade_sim"),
        "horario_cheio": bool(v.get("prog_inicio") and horario_cheio(v["prog_inicio"])),
        # ⚠️ Como ESCALADO NO NIMER — não é prova de quem dirigiu ou faltou.
        "escalado_nimer": {"motorista_re": v.get("motorista_re"), "cobrador_re": v.get("cobrador_re"),
                           "sem_motorista": bool(v.get("sem_motorista"))},
    }


def serializar(resultado: dict, linha, data_servico: date) -> dict:
    saude = resultado.get("saude") or {}
    return {
        "linha": {"codigo": linha.codigo, "nome": linha.nome, "tem_mapa": linha.tem_mapa,
                  "tem_nimer": linha.nimer_line_id is not None},
        "data_servico": data_servico.isoformat(),
        "totais": resultado["totais"],
        "icv": resultado["icv"],
        "icv_sim_rotulo": "ICV do SIM = nossa leitura das saídas no SIM, não o número oficial",
        "ipp": resultado["ipp"],
        "pontualidade_nimer": resultado["pontualidade_nimer"],
        "fim_servico": resultado["fim_servico"],
        "saude": {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in saude.items()},
        "partidas": [serializar_partida(v, linha) for v in resultado["viagens"]],
    }
