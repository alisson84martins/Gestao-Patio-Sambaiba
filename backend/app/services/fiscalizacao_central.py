"""Central da Fiscalização — o painel gerencial da coordenação (fase 2, bloco I).

Junta, para as linhas do coordenador (ou todas):
1. placar ao vivo — ICV/IPP REAIS (NIMER × GPS confirmados, fase 3 bloco
   C; NIMER e SIM sozinhos ficam só como conferência), perda e farol pela
   meta/aceitável de fiscalizacao.parametro (D29), ORDENADO POR PERDA
   ABSOLUTA (D23); agregado ponderado (D22);
2. fila de ação — os alertas do bloco E de todas as linhas, mais urgentes
   primeiro, com "registrar ação" (fiscalizacao.acao_coordenacao, D26) e
   "resolvido" (monitoramento.alerta_tratativa);
3. Pareto dos motivos de perda do dia (NIMER + correções);
4. cobertura — linhas monitoradas sem fiscal com turno aberto;
5. correções dos fiscais do dia (auditoria);
6. saúde dos robôs (última coleta do SIM e do NIMER por linha).

⛔ Nada calculado aqui é gravado (D24); só a tratativa e a ação, por clique.
"""
import threading
import time as relogio
from collections import Counter
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import AlertaTratativa
from app.services import fiscalizacao_alertas as alertas_op
from app.services import fiscalizacao_tempo_real as tempo_real

CACHE_SEGUNDOS = 15
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

ROTULOS_CONTADOR = {
    "FALTA_OPERADORES": "Falta de operadores", "RA": "R.A", "SOS": "S.O.S",
    "ATRASO_GARAGEM": "Atraso de garagem", "TROCA_OPERACIONAL": "Troca operacional",
    "TRANSITO": "Trânsito (OBS)", "OUTRO": "Outro (OBS)",
}


def farol(icv: Optional[float], meta: Optional[float], aceitavel: Optional[float]) -> str:
    if icv is None or meta is None or aceitavel is None:
        return "sem_dado"
    if icv >= meta:
        return "verde"
    if icv >= aceitavel:
        return "amarelo"
    return "vermelho"


def motivo_do_pareto(v: dict) -> str:
    if v.get("contador"):
        return ROTULOS_CONTADOR.get(v["contador"], v["contador"])
    if v.get("motivo_proprio"):
        return v["motivo_proprio"].capitalize()
    if v.get("motivo_canonico"):
        return ROTULOS_CONTADOR.get(v["motivo_canonico"], v["motivo_canonico"])
    return (v.get("motivo_nimer") or "Sem motivo no NIMER").strip()


def pareto(viagens: list[dict], agora: datetime) -> list[dict]:
    perdas = [v for v in viagens if v.get("prog_inicio") and v["prog_inicio"] <= agora
              and v["status_efetivo"] != "done" and not v.get("duplicada")]
    contagem = Counter(motivo_do_pareto(v) for v in perdas)
    total = sum(contagem.values())
    saida, acumulado = [], 0
    for motivo, n in sorted(contagem.items(), key=lambda x: (-x[1], x[0])):
        acumulado += n
        saida.append({"motivo": motivo, "perdas": n, "pct": round(n / total * 100, 1),
                      "acumulado_pct": round(acumulado / total * 100, 1)})
    return saida


def cobertura(db: Session, linhas: list[str]) -> list[dict]:
    """Quem está com turno ABERTO em cada linha (vários pontos ao mesmo tempo)."""
    rows = db.execute(
        select(TurnoLinha.linha_codigo, Turno.fiscal_re, Funcionario.nome, Turno.terminal, Turno.ponto_nome)
        .join(Turno, Turno.id == TurnoLinha.turno_id)
        .join(Funcionario, Funcionario.id == Turno.funcionario_id)
        .where(Turno.status == "ABERTO", TurnoLinha.linha_codigo.in_(linhas))
        .order_by(TurnoLinha.linha_codigo, Turno.fiscal_re)
    ).all() if linhas else []
    por_linha: dict[str, list[dict]] = {codigo: [] for codigo in linhas}
    for codigo, re_, nome, terminal, ponto in rows:
        por_linha[codigo].append({"re": re_, "nome": nome, "terminal": terminal, "ponto": ponto})
    return [{"linha_codigo": c, "fiscais": f, "coberta": bool(f)} for c, f in sorted(por_linha.items())]


def tratativas(db: Session, data_servico: date) -> dict[str, AlertaTratativa]:
    return {t.chave: t for t in db.execute(
        select(AlertaTratativa).where(AlertaTratativa.data_servico == data_servico)).scalars()}


def alarmes_falsos(tratadas) -> list[dict]:
    contagem = Counter(t.chave.split("|")[0] for t in tratadas if getattr(t, "motivo", None) == "alarme_falso")
    return [{"tipo": tipo, "quantidade": n} for tipo, n in sorted(contagem.items(), key=lambda x: (-x[1], x[0]))]


def montar_central(db: Session, linhas_obj: list, data_servico: date, parametros: dict,
                   correcoes: list[dict], agora: Optional[datetime] = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    meta, aceitavel = parametros.get("icv_meta"), parametros.get("icv_aceitavel")
    resultados, placar, fila, todas_viagens, saude, adiantamentos = [], [], [], [], [], []
    for linha in linhas_obj:
        r = tempo_real.montar_linha_tempo_real(db, linha, data_servico, agora)
        resultados.append(r)
        todas_viagens += r["viagens"]
        adiantamentos += [dict(a, linha_codigo=linha.codigo) for a in r["icv"]["adiantamentos_de_faixa"]]
        placar.append({
            "linha_codigo": linha.codigo,
            "nome": linha.nome,
            "icv_real": r["icv"]["real_ate_agora"],
            "ipp_real": r["ipp"]["real_ate_agora"],
            "perdidas_reais": r["real"]["total"]["perdidas"],
            "a_conferir": r["real"]["total"]["a_conferir"],
            "icv_nimer": r["icv"]["nimer_ate_agora"],
            "icv_sim": r["icv"]["sim_ate_agora"],
            "ipp": r["ipp"]["ate_agora"],
            "ipp_sim": r["ipp"]["sim_ate_agora"],
            "icv_dia_nimer": r["icv"]["nimer_dia"],
            "projecao": r["icv"]["projecao_fechamento"],
            "faixas": r["icv"]["faixas"],
            "perda_absoluta": r["totais"]["perda_absoluta"],
            "programadas_ate_agora": r["totais"]["programadas_ate_agora"],
            "pontualidade_nimer": r["pontualidade_nimer"],
            # v4.0 fase 3 (T3): o farol segue o número real (NIMER × GPS confirmados)
            "farol": farol(r["icv"]["real_ate_agora"], meta, aceitavel),
            "tem_nimer": linha.nimer_line_id is not None,
        })
        fila += alertas_op.montar_alertas(db, linha, r, agora)
        s = r["saude"]
        saude.append({"linha_codigo": linha.codigo,
                      "sim_ultima_ok_em": s["sim_ultima_ok_em"].isoformat() if s["sim_ultima_ok_em"] else None,
                      "nimer_status": s["nimer_status"],
                      "nimer_ultima_ok_em": s["nimer_ultima_ok_em"].isoformat() if s["nimer_ultima_ok_em"] else None,
                      "tem_nimer": linha.nimer_line_id is not None})
    # D23: por perda absoluta, nunca por percentual
    placar.sort(key=lambda p: (p["perda_absoluta"] is None, -(p["perda_absoluta"] or 0), p["linha_codigo"]))
    tratadas = tratativas(db, data_servico)
    fila = alertas_op.aplicar_baixas(alertas_op.ordenar(fila), tratadas, agora)
    for a in fila:
        t = tratadas.get(a["chave"])
        # "resolvido" = com baixa e sem ter voltado a piscar (T4, fase 3)
        a["resolvido"] = t is not None and not a["reaberto"]
        a["resolvido_em"] = tempo_real.hora_local(tempo_real._utc(t.criado_em)) if t else None
    fila.sort(key=lambda a: a["resolvido"])  # resolvidos no fim, ordem de urgência mantida
    agregado = tempo_real.agregar(resultados) if resultados else None
    if agregado:
        agregado["farol"] = farol(agregado["icv_real_ate_agora"], meta, aceitavel)
    return {
        "data_servico": data_servico.isoformat(),
        "atualizado_em": agora.isoformat(),
        "meta": {"icv_meta": meta, "icv_aceitavel": aceitavel},
        "agregado": agregado,
        "placar": placar,
        "fila": fila,
        "pareto": pareto(todas_viagens, agora),
        # ICV por faixa usa a hora REAL de saída: a adiantada antes da hora cheia cai na faixa anterior
        "adiantamentos_de_faixa": sorted(adiantamentos, key=lambda a: (a["faixa"], a["linha_codigo"], a["saida"] or "")),
        "cobertura": cobertura(db, [linha.codigo for linha in linhas_obj]),
        "correcoes": correcoes,
        "saude": saude,
        # T4: alarme falso por tipo de alerta — serve para calibrar as regras
        "alarmes_falsos": alarmes_falsos(tratadas.values()),
        "icv_sim_rotulo": "ICV do SIM = nossa leitura das saídas no SIM, não o número oficial",
    }


def com_cache(chave: tuple, fabricar) -> dict:
    agora = relogio.monotonic()
    with _cache_lock:
        item = _cache.get(chave)
        if item and agora - item[0] < CACHE_SEGUNDOS:
            return item[1]
    resultado = fabricar()
    with _cache_lock:
        _cache[chave] = (agora, resultado)
    return resultado


def limpar_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ─── Ao vivo · minhas linhas (v4.0 fase 3, bloco F) ─────────────────────────

def fiscais_no_ponto(db: Session, linhas: list[str]) -> dict[str, list[dict]]:
    """Linha → fiscais com turno ABERTO: RE, terminal e o selo "NIMER ✓ 14:13"."""
    from app.services.monitoramento_fiscais import escala_do_turno
    if not linhas:
        return {}
    rows = db.execute(
        select(TurnoLinha.linha_codigo, Turno)
        .join(Turno, Turno.id == TurnoLinha.turno_id)
        .where(Turno.status == "ABERTO", TurnoLinha.linha_codigo.in_(linhas))
        .order_by(TurnoLinha.linha_codigo, Turno.terminal, Turno.fiscal_re)
    ).all()
    saida: dict[str, list[dict]] = {codigo: [] for codigo in linhas}
    for codigo, turno in rows:
        selo = "aberto à mão"
        if turno.origem == "NIMER":
            escala = escala_do_turno(db, turno)
            selo = f"NIMER ✓ {tempo_real.hora_local(tempo_real._utc(escala.inicio_em))}" if escala else "NIMER ✓"
        saida[codigo].append({"re": turno.fiscal_re, "terminal": turno.terminal, "selo": selo})
    return saida


def montar_minhas_linhas(db: Session, linhas_obj: list, data_servico: date, parametros: dict,
                         agora: Optional[datetime] = None) -> dict:
    """Um cartão por linha (ordem: mais alertas abertos primeiro), a fila de ação
    de todas elas (mais recentes primeiro) e o bloco Robô (saúde + alarmes falsos)."""
    agora = agora or datetime.now(timezone.utc)
    meta, aceitavel = parametros.get("icv_meta"), parametros.get("icv_aceitavel")
    tratadas = tratativas(db, data_servico)
    fiscais = fiscais_no_ponto(db, [linha.codigo for linha in linhas_obj])
    cartoes, fila, saude = [], [], []
    for linha in linhas_obj:
        r = tempo_real.montar_linha_tempo_real(db, linha, data_servico, agora)
        alertas = alertas_op.aplicar_baixas(alertas_op.montar_alertas(db, linha, r, agora), tratadas, agora)
        abertos = [dict(a, linha_codigo=linha.codigo) for a in alertas if a["aberto"]]
        fila += abertos
        real = r["real"]
        cartoes.append({
            "linha_codigo": linha.codigo,
            "nome": linha.nome,
            "icv": real["total"]["icv"],
            "farol": farol(real["total"]["icv"], meta, aceitavel),
            "por_sentido": {s: {"realizadas": b["realizadas"], "programadas": b["programadas_ate_agora"],
                                "perdidas": b["perdidas"]} for s, b in real["por_sentido"].items()},
            "a_conferir": real["total"]["a_conferir"],
            "alertas_abertos": len(abertos),
            "tem_alerta": any(a["nivel"] == "alerta" for a in abertos),
            "fiscais": fiscais.get(linha.codigo, []),
        })
        s = r["saude"]
        saude.append({"linha_codigo": linha.codigo,
                      "sim_ultima_ok_em": tempo_real._iso(s["sim_ultima_ok_em"]),
                      "nimer_status": s["nimer_status"],
                      "nimer_ultima_ok_em": tempo_real._iso(s["nimer_ultima_ok_em"]),
                      "tem_nimer": linha.nimer_line_id is not None})
    cartoes.sort(key=lambda c: (-c["alertas_abertos"], not c["tem_alerta"], c["linha_codigo"]))
    fila.sort(key=lambda a: a["inicio_em"] or "", reverse=True)
    return {
        "data_servico": data_servico.isoformat(),
        "atualizado_em": agora.isoformat(),
        "meta": {"icv_meta": meta, "icv_aceitavel": aceitavel},
        "cartoes": cartoes,
        "fila": fila,
        "robo": {"saude": saude, "alarmes_falsos": alarmes_falsos(tratadas.values())},
    }
