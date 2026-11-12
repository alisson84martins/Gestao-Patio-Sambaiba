"""Tela do fiscal ao vivo (v4.0 fase 3, bloco E) — um endpoint agregado.

GET /monitoramento/linhas/{linha}/fiscal-ao-vivo?terminal=TS devolve, de uma
vez, o que o celular do fiscal mostra de cima para baixo:
  cabecalho  linha · terminal · fiscal/período · selo "NIMER ✓ 14:13" ou
             "Previsto" · linhas do ponto (abas)
  numeros    ICV/IPP REAIS (bloco C) total e por sentido, cor pela meta
             (fiscalizacao.parametro, D29), perdidas, "A conferir: N",
             "Faltam X viagens para a meta"
  a_conferir as viagens em que NIMER e GPS não batem, com o motivo
  alertas    só os ABERTOS (sem baixa, ou que voltaram a piscar — T4)
  saindo     sentido que PARTE do terminal do fiscal: as 2 últimas + as 4
             próximas partidas, cada uma com a situação do carro (T9)
  chegando   sentido oposto: carros a caminho, pela previsão, com a rua e a
             cor do trânsito (T9)
  robo       saúde da coleta (SIM, NIMER, Rastro) e carros em dúvida

Cor do trânsito (T9): mediana da velocidade efetiva AO VIVO (fonte (a) de
monitoramento_eta, últimos 20 min da frota) dos trechos que faltam até o
terminal. Sem dado ao vivo → sem selo (⛔ nunca pintar só pelo histórico).
Limites numa tabela só: TRANSITO_KMH.

Tudo separado por sentido (T10): sentido 1 = TP → TS (azul royal #1F4FD8),
2 = TS → TP (laranja #EF6C00) — a cor é do front; aqui vai o `sentido`.
⛔ Nada aqui grava nada.
"""
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services import fiscalizacao_tempo_real as tr

# 🔑 TABELA ÚNICA do trânsito (T9): ≥ livre = livre; ≥ parado = intenso; abaixo = parado.
TRANSITO_KMH = {"livre": 15, "parado": 7}
SAINDO_PROXIMAS = 4
SAINDO_ULTIMAS = 2
OUTRA_LINHA_RECENTE = timedelta(minutes=15)


# ─── Trânsito (T9) ───────────────────────────────────────────────────────────

def classificar_transito(kmh: Optional[float]) -> Optional[str]:
    if kmh is None:
        return None
    if kmh >= TRANSITO_KMH["livre"]:
        return "livre"
    if kmh >= TRANSITO_KMH["parado"]:
        return "intenso"
    return "parado"


def transito_restante(progresso_m: Optional[float], ao_vivo: dict[int, list[float]]) -> Optional[str]:
    """Mediana das medianas AO VIVO dos trechos (500 m) que faltam até o terminal."""
    from app.services.monitoramento_eta import MIN_AMOSTRAS_AO_VIVO, TRECHO_M
    if progresso_m is None:
        return None
    primeiro = int(progresso_m // TRECHO_M)
    medianas = [statistics.median(vs) for trecho, vs in ao_vivo.items()
                if trecho >= primeiro and len(vs) >= MIN_AMOSTRAS_AO_VIVO]
    return classificar_transito(statistics.median(medianas)) if medianas else None


# ─── Situação de cada partida que sai daqui ─────────────────────────────────

def _min(delta: timedelta) -> int:
    s = delta.total_seconds()
    return int(s // 60) if s < 0 else int(round(s / 60))


def situacao_saindo(v: dict, carro_estado: Optional[dict], outra_linha: Optional[str], terminal: str,
                    agora: datetime, tolerancia_min: float) -> dict:
    """{"tipo", "texto", "cor": verde|amarelo|vermelho|neutro} de UMA partida do terminal do fiscal.

    `carro_estado`: o carro (programado) no ao-vivo — zona, local, eta, sentido."""
    prog = v["prog_inicio"]
    saiu = tr.hora_de_saida_real(v) if (v.get("saida_sim") is not None or v["status_efetivo"] == "done"
                                         or "real_inicio" in (v.get("correcoes") or {})) else None
    if saiu is not None:
        cl = tr.classificar_pontualidade(prog, saiu, tolerancia_min)
        d = cl["desvio_min"] or 0
        if cl["classe"] == "adiantada":
            return {"tipo": "saiu", "texto": f"saiu {tr.hora_local(saiu)} · adiantada −{abs(d)}", "cor": "vermelho"}
        sinal = f"+{d}" if d > 0 else ("0" if d == 0 else str(d))
        cor = "verde" if cl["classe"] == "pontual" else ("amarelo" if d <= 2 * tolerancia_min else "vermelho")
        return {"tipo": "saiu", "texto": f"saiu {tr.hora_local(saiu)} · {sinal}", "cor": cor}
    if v.get("situacao") == "perdida":
        return {"tipo": "perdida", "texto": "perdida", "cor": "vermelho"}
    if outra_linha:
        return {"tipo": "outra_linha", "texto": f"em outra linha ({outra_linha.split('-')[0]}) · vai perder",
                "cor": "vermelho"}
    if carro_estado is None:
        return {"tipo": "sem_posicao", "texto": "sem posição no SIM", "cor": "neutro"}
    zona = carro_estado.get("zona")
    falta = _min(prog - agora)
    if zona == "garagem":
        return {"tipo": "garagem", "texto": "na G3 · vai perder", "cor": "vermelho"}
    if zona == terminal:
        if falta >= 0:
            return {"tipo": "terminal", "texto": f"no terminal · sai em {falta} min", "cor": "verde"}
        return {"tipo": "terminal", "texto": f"no terminal · atrasada {-falta} min", "cor": "amarelo"}
    rua = carro_estado.get("local") or "a caminho"
    eta = carro_estado.get("eta")
    if carro_estado.get("destino") == terminal and eta:
        chegada = datetime.fromisoformat(eta["chegada_em"])
        folga = _min(chegada - prog)
        atrasa = chegada > prog
        texto = f"a caminho · {rua} · chega {eta['chegada']}" + (f" · +{folga}" if atrasa else "")
        return {"tipo": "a_caminho", "texto": texto, "cor": "amarelo" if atrasa else "verde"}
    return {"tipo": "a_caminho", "texto": f"a caminho · {rua}", "cor": "neutro"}


def montar_saindo(viagens: list[dict], sentido: int, carros: dict[str, dict], outras: dict[str, str],
                  terminal: str, agora: datetime, tolerancia_min: float) -> list[dict]:
    do_sentido = sorted((v for v in viagens if v.get("sentido") == sentido and v.get("prog_inicio")
                         and not v.get("duplicada")), key=lambda v: v["prog_inicio"])
    passadas = [v for v in do_sentido if v["prog_inicio"] <= agora][-SAINDO_ULTIMAS:]
    proximas = [v for v in do_sentido if v["prog_inicio"] > agora][:SAINDO_PROXIMAS]
    saida = []
    for v in passadas + proximas:
        programado = v.get("carro")
        real = v["saida_sim"].prefixo if v.get("saida_sim") is not None else None
        carro = real or programado
        st = situacao_saindo(v, carros.get(programado) if programado else None,
                             outras.get(programado) if programado and not real else None,
                             terminal, agora, tolerancia_min)
        saida.append({"id": v["id"], "programado": tr.hora_local(v["prog_inicio"]), "tabela": v.get("tabela"),
                      "sentido": sentido, "carro": carro, "carro_programado": programado,
                      "trocado": bool(real and programado and real != programado),
                      "passada": v["prog_inicio"] <= agora, **st})
    return saida


def montar_chegando(carros_ao_vivo: list[dict], terminal: str, ao_vivo_por_sentido: dict[int, dict]) -> list[dict]:
    saida = []
    for c in carros_ao_vivo:
        if c.get("sumiu") or c.get("destino") != terminal or c.get("zona") not in ("rota", "fora"):
            continue
        eta = c.get("eta")
        saida.append({
            "carro": c.get("prefixo_patio"), "sentido": c.get("sentido"), "rua": c.get("local"),
            "minutos": eta["minutos"] if eta else None, "chegada": eta["chegada"] if eta else None,
            "transito": transito_restante(c.get("progresso_m"), ao_vivo_por_sentido.get(c.get("sentido"), {})),
        })
    saida.sort(key=lambda x: (x["minutos"] is None, x["minutos"] or 0))
    return saida


# ─── Carregador ──────────────────────────────────────────────────────────────

def _sentido_que_sai(linha, terminal: str) -> Optional[int]:
    """O sentido cujo DESTINO não é o terminal do fiscal (= parte dele)."""
    for s, destino in sorted(linha.destino.items()):
        if destino != terminal:
            return s
    return None


def _terminal_padrao(db: Session, linha, esc: dict) -> str:
    from app.models.fiscalizacao import Turno, TurnoLinha
    if esc.get("turno") is not None:
        return esc["turno"].terminal
    if esc.get("previsto"):
        return esc["previsto"]["terminal"]
    aberto = db.execute(
        select(Turno.terminal).join(TurnoLinha, TurnoLinha.turno_id == Turno.id)
        .where(Turno.status == "ABERTO", TurnoLinha.linha_codigo == linha.codigo).limit(1)
    ).scalar_one_or_none()
    return aberto or "TP"


def _cabecalho(db: Session, linha, esc: dict, terminal: str, monitoradas: set[str]) -> dict:
    from app.services.monitoramento_fiscais import escala_do_turno
    turno = esc.get("turno")
    selo = None
    fiscal = None
    if turno is not None:
        fiscal = {"re": turno.fiscal_re, "periodo": turno.periodo}
        if turno.origem == "NIMER":
            escala = escala_do_turno(db, turno)
            selo = {"tipo": "nimer", "texto": f"NIMER ✓ {tr.hora_local(tr._utc(escala.inicio_em))}"
                    if escala is not None else "NIMER ✓"}
        else:
            selo = {"tipo": "manual", "texto": "Turno aberto à mão"}
    elif esc.get("previsto"):
        p = esc["previsto"]
        fiscal = {"re": p["re"], "periodo": None}
        selo = {"tipo": "previsto", "texto": "Previsto", "faixa": p["texto"]}
    linhas_ponto = [c for c in (esc.get("linhas") or [linha.codigo]) if c in monitoradas] or [linha.codigo]
    return {
        "linha": linha.codigo, "nome": linha.nome, "terminal": terminal,
        "terminal_nome": linha.nomes_terminais.get(terminal, terminal), "fiscal": fiscal, "selo": selo,
        "modo": esc["tipo"], "linhas_do_ponto": linhas_ponto,
        "pode_corrigir": esc["tipo"] == "turno", "pode_baixar": esc["tipo"] in ("turno", "todas", "coordenador"),
        "tem_mapa": linha.tem_mapa, "tem_nimer": linha.nimer_line_id is not None,
    }


def _numeros(resultado: dict, parametros: dict) -> dict:
    from app.services.fiscalizacao_central import farol
    meta, aceitavel = parametros.get("icv_meta"), parametros.get("icv_aceitavel")
    real = resultado["real"]

    def bloco(b):
        return dict(b, farol=farol(b["icv"], meta, aceitavel), faltam_para_meta=tr.faltam_para_meta(b, meta))
    return {"total": bloco(real["total"]), "por_sentido": {s: bloco(b) for s, b in real["por_sentido"].items()},
            "meta": meta, "aceitavel": aceitavel, "tolerancia_min": resultado["ipp"]["tolerancia_min"]}


def _robo(db: Session, resultado: dict) -> dict:
    from app.models.monitoramento import RastroColeta
    from app.services.monitoramento_rastro import carros_em_duvida_com_cache
    saude = resultado.get("saude") or {}
    ultimo_rastro = db.execute(select(func.max(RastroColeta.recebido_em))).scalar_one_or_none()
    return {
        "sim_ultima_ok_em": tr._iso(saude.get("sim_ultima_ok_em")),
        "nimer_status": saude.get("nimer_status"),
        "nimer_ultima_ok_em": tr._iso(saude.get("nimer_ultima_ok_em")),
        "rastro_ultimo_em": tr._iso(tr._utc(ultimo_rastro)),
        "carros_em_duvida": len(carros_em_duvida_com_cache(db)),
    }


def _outras_linhas(db: Session, linha, carros: set[str], agora: datetime) -> dict[str, str]:
    """Carro (4 díg.) → a outra linha em que o SIM o viu nos últimos 15 min."""
    if not carros:
        return {}
    ultima: dict[str, tuple[datetime, str]] = {}
    for r in tr.leituras_do_dia(db, None, agora - OUTRA_LINHA_RECENTE, prefixos=carros):
        if r["linha"] == linha.codigo or r["em"] > agora:
            continue
        if r["prefixo"] not in ultima or r["em"] > ultima[r["prefixo"]][0]:
            ultima[r["prefixo"]] = (r["em"], r["linha"])
    return {p: linha_ for p, (_, linha_) in ultima.items()}


def _ao_vivo_por_sentido(db: Session, linha, agora: datetime) -> dict[int, dict]:
    from app.models.monitoramento import LeituraVeiculo
    from app.services import monitoramento_eta as eta
    if not linha.tem_mapa:
        return {}
    rows = db.execute(
        select(LeituraVeiculo.prefixo, LeituraVeiculo.posicao_em, LeituraVeiculo.sentido,
               LeituraVeiculo.lat, LeituraVeiculo.lon)
        .where(LeituraVeiculo.posicao_em >= agora - eta.JANELA_AO_VIVO)
    ).all()
    por_carro: dict[str, list] = defaultdict(list)
    for p, em, s, la, lo in rows:
        por_carro[p].append({"em": tr._utc(em), "s": s, "la": la, "lo": lo})
    return {s: eta.velocidades_ao_vivo(por_carro, linha, s, agora) for s in linha.tracados}


def montar(db: Session, linha, esc: dict, terminal: Optional[str], agora: Optional[datetime] = None) -> dict:
    from app.services import fiscalizacao_alertas as alertas_op
    from app.services import monitoramento_ao_vivo as ao_vivo
    from app.services.fiscalizacao_central import tratativas
    from app.services.icv import _parametros
    from app.services.monitoramento_cadastro import data_servico_de, linhas_ativas

    agora = agora or datetime.now(timezone.utc)
    data_servico = data_servico_de(agora)
    terminal = terminal if terminal in ("TP", "TS") else _terminal_padrao(db, linha, esc)
    resultado = tr.montar_linha_tempo_real(db, linha, data_servico, agora)
    vivo = ao_vivo.ao_vivo_com_cache(db, linha)
    carros = {c["prefixo_patio"]: c for c in vivo.get("carros", [])}
    viagens = resultado["viagens"]
    programados = {v["carro"] for v in viagens if v.get("carro")}

    alertas = alertas_op.aplicar_baixas(alertas_op.montar_alertas(db, linha, resultado, agora),
                                        tratativas(db, data_servico), agora)
    abertos = [a for a in alertas if a["aberto"]]

    sai = _sentido_que_sai(linha, terminal)
    chega = next((s for s, d in linha.destino.items() if d == terminal), None)
    tol = resultado["ipp"]["tolerancia_min"]
    return {
        "atualizado_em": agora.isoformat(),
        "data_servico": data_servico.isoformat(),
        "cabecalho": _cabecalho(db, linha, esc, terminal, set(linhas_ativas(db))),
        "faixa_previsto": (esc.get("previsto") or {}).get("texto"),
        "numeros": _numeros(resultado, _parametros(db)),
        "a_conferir": [dict(tr.serializar_partida(v, linha), carro_real=v.get("carro_real"))
                       for v in viagens if v.get("a_conferir")],
        "alertas": abertos,
        "alertas_com_baixa": sum(1 for a in alertas if a.get("baixa") and not a["aberto"]),
        "saindo": {"sentido": sai, "partidas": montar_saindo(viagens, sai, carros,
                                                             _outras_linhas(db, linha, programados, agora),
                                                             terminal, agora, tol) if sai else []},
        "chegando": {"sentido": chega, "carros": montar_chegando(vivo.get("carros", []), terminal,
                                                                 _ao_vivo_por_sentido(db, linha, agora))},
        "robo": _robo(db, resultado),
    }
