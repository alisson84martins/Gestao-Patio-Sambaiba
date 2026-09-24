"""Regras da montagem da Escala de Fiscais (Fase 3) — funções PURAS.

Nada aqui toca no banco: quem chama (escala_fiscais_montagem.py) carrega o
contexto e passa. Assim cada regra é testável sozinha e a tela pode repetir
a mesma conta só para avisar mais rápido — a fonte da verdade é esta.

Datas são de CALENDÁRIO (viram à meia-noite, ⚠️ não às 20h como o Pátio).
O fim de semana é identificado pela data do SÁBADO; o fim de semana anterior
é o sábado menos 7 dias.

Regras (numeração do pedido do Alisson, 24/09/2026):
  RN01  modelo sugerido pelo mês do PRÓPRIO dia (ímpar/par; seg–sex = útil);
        sábado e domingo em meses diferentes = aviso de virada de mês.
  RN02  folga_base vale nos meses ímpares; no mês par inverte. Sem folga
        base confirmada: não calcula dobra e avisa.
  RN06  troca 1x1: cada um dos dois inverte o próprio dia de folga naquele
        fim de semana.
  RN07  troca 2x2: no fim de semana de data_sabado, re_a trabalha os dois
        dias e re_b folga os dois; no fim de semana seguinte, inverte.
        O dia trabalhado POR TROCA não é dobra (só é folga o que a troca
        deixou como folga).
  RN03  dobra = escalado em qualquer posto num dia que é folga dele.
        Calculada na leitura; congelada só ao publicar.
  RN04  dobra agora e dobra no fim de semana anterior: pergunta (não
        bloqueia); a confirmação é gravada com quem e quando.
  RN05  férias/atestado/afastado no dia (data_fim inclusiva): BLOQUEIA.
  RN08  mesmo RE em postos com horário sobreposto: mesmo ponto final =
        acúmulo permitido; pontos finais diferentes = BLOQUEIA; posto sem
        ponto final = pergunta.
  RN11  mesmo RE no 1º e no 2º período do dia: pergunta.
  RN12  fiscal do quadro sem posto, folga nem ausência no dia: aviso.
  RN13  situações escalado, outra_garagem, descoberto, direto — marcador
        preservado.
Só BLOQUEIAM: RN05, pontos finais diferentes no mesmo horário, G3, RE vazio
em "escalado" e término antes do início. Todo o resto é aviso ou pergunta.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
from uuid import UUID

from app.services.escala_fiscais_importacao import GARAGEM_PROPRIA

SABADO, DOMINGO = 5, 6
NOME_DIA = {SABADO: "sabado", DOMINGO: "domingo"}
DIA_POR_NOME = {"sabado": SABADO, "domingo": DOMINGO}
TIPO_AUSENCIA = {"ferias": "férias", "atestado": "atestado", "afastado": "afastamento"}


# ─── Estruturas ───────────────────────────────────────────────────────────────

@dataclass
class Aloc:
    """Uma linha da escala do dia: um posto num período."""
    posto_id: UUID
    periodo: int
    situacao: str
    re: Optional[str] = None
    hora_inicio: Optional[time] = None
    hora_termino: Optional[time] = None
    outra_garagem: Optional[str] = None
    marcador: Optional[str] = None
    dobra_seguida_confirmada_por: Optional[UUID] = None
    dobra_seguida_confirmada_em: Optional[datetime] = None
    dobra_publicada: Optional[bool] = None

    @property
    def chave(self) -> tuple[UUID, int]:
        return self.posto_id, self.periodo


@dataclass
class PostoInfo:
    id: UUID
    lado: str
    cod_jb: Optional[str]
    lote: Optional[str]
    linhas: list[str]
    ponto_final_id: Optional[UUID] = None
    ponto_final_nome: Optional[str] = None

    @property
    def rotulo(self) -> str:
        cod = f" {self.cod_jb}" if self.cod_jb else ""
        return f"{self.lado}{cod} · {' '.join(self.linhas)}"


@dataclass
class FiscalQuadro:
    re: str
    periodo: int
    folga_base: Optional[str]
    ativo: bool = True


@dataclass
class Ausencia:
    re: str
    tipo: str
    data_inicio: date
    data_fim: Optional[date]


@dataclass
class Troca:
    tipo: str
    re_a: str
    re_b: str
    data_sabado: date


@dataclass
class Contexto:
    data: date
    postos: dict[UUID, PostoInfo]
    quadro: dict[str, FiscalQuadro]
    ausencias: list[Ausencia]
    trocas: list[Troca]
    padroes: dict[int, tuple[time, time]]
    # RN04: RE → dia em que dobrou no fim de semana anterior.
    dobras_anteriores: dict[str, date] = field(default_factory=dict)
    nomes: dict[str, str] = field(default_factory=dict)


@dataclass
class Avaliacao:
    bloqueios: list[dict] = field(default_factory=list)
    perguntas: list[dict] = field(default_factory=list)
    avisos: list[dict] = field(default_factory=list)
    # (posto_id, periodo) → marcações calculadas da linha
    linhas: dict[tuple[UUID, int], dict] = field(default_factory=dict)
    resumo: dict = field(default_factory=dict)

    def pendentes(self, confirmadas: set[str]) -> list[dict]:
        return [p for p in self.perguntas if p["pendente"] and p["chave"] not in confirmadas]


# ─── Datas ────────────────────────────────────────────────────────────────────

def ddmm(d: date) -> str:
    return d.strftime("%d/%m")


def tipo_do_dia(d: date) -> str:
    return NOME_DIA.get(d.weekday(), "util")


def sabado_do_fim_de_semana(d: date) -> Optional[date]:
    if d.weekday() == SABADO:
        return d
    if d.weekday() == DOMINGO:
        return d - timedelta(days=1)
    return None


def paridade(d: date) -> str:
    return "impar" if d.month % 2 else "par"


def modelo_sugerido(d: date) -> tuple[str, Optional[str]]:
    """RN01: (tipo_dia, paridade) pelo mês do PRÓPRIO dia."""
    tipo = tipo_do_dia(d)
    return (tipo, paridade(d)) if tipo != "util" else ("util", None)


def virada_de_mes(d: date) -> Optional[dict]:
    """RN01: sábado e domingo em meses diferentes (ex.: 31/10 e 01/11)."""
    sab = sabado_do_fim_de_semana(d)
    if sab is None:
        return None
    dom = sab + timedelta(days=1)
    if sab.month == dom.month:
        return None
    return {
        "codigo": "virada_mes",
        "mensagem": (
            f"Virada de mês: o sábado {ddmm(sab)} é de mês {'ímpar' if sab.month % 2 else 'par'} e o "
            f"domingo {ddmm(dom)} é de mês {'ímpar' if dom.month % 2 else 'par'}. Cada dia usa o modelo "
            f"e a folga do PRÓPRIO mês: quem folga no sábado pelo mês de um pode folgar de novo, ou "
            f"trabalhar os dois dias, pelo mês do outro. Confira as folgas deste fim de semana."
        ),
    }


# ─── Folga (RN02, RN06, RN07) ────────────────────────────────────────────────

def _inverte(nome_dia: str) -> str:
    return "domingo" if nome_dia == "sabado" else "sabado"


def folga_pelo_mes(folga_base: str, d: date) -> bool:
    """RN02: folga_base vale no mês ímpar; no par inverte. Mês do próprio dia."""
    dia_folga = folga_base if d.month % 2 else _inverte(folga_base)
    return NOME_DIA.get(d.weekday()) == dia_folga


def folga_no_dia(re_txt: str, d: date, quadro: dict[str, FiscalQuadro], trocas: list[Troca]) -> tuple[Optional[bool], str]:
    """(é folga?, motivo). None = não dá para saber (sem folga base
    confirmada ou fora do quadro). Dia útil nunca é folga de fim de semana."""
    sab = sabado_do_fim_de_semana(d)
    if sab is None:
        return False, "util"
    # RN07 — 2x2: vale no fim de semana da troca e inverte no seguinte.
    for t in trocas:
        if t.tipo != "2x2" or re_txt not in (t.re_a, t.re_b):
            continue
        if t.data_sabado == sab:
            return re_txt == t.re_b, "troca_2x2"
        if t.data_sabado == sab - timedelta(days=7):
            return re_txt == t.re_a, "troca_2x2"
    fiscal = quadro.get(re_txt)
    if fiscal is None:
        return None, "fora_do_quadro"
    if not fiscal.folga_base:
        return None, "folga_nao_confirmada"
    folga = folga_pelo_mes(fiscal.folga_base, d)
    # RN06 — 1x1: cada um inverte o próprio dia de folga naquele fim de semana.
    if any(t.tipo == "1x1" and t.data_sabado == sab and re_txt in (t.re_a, t.re_b) for t in trocas):
        return not folga, "troca_1x1"
    return folga, "base"


def trocas_do_fim_de_semana(d: date, trocas: list[Troca]) -> list[dict]:
    """Trocas que valem no fim de semana de `d` (rodapé da escala)."""
    sab = sabado_do_fim_de_semana(d)
    if sab is None:
        return []
    saida = []
    for t in trocas:
        if t.data_sabado == sab:
            saida.append({"tipo": t.tipo, "re_a": t.re_a, "re_b": t.re_b, "invertida": False})
        elif t.tipo == "2x2" and t.data_sabado == sab - timedelta(days=7):
            saida.append({"tipo": t.tipo, "re_a": t.re_a, "re_b": t.re_b, "invertida": True})
    return saida


# ─── Ausência (RN05) ──────────────────────────────────────────────────────────

def ausencia_no_dia(re_txt: str, d: date, ausencias: list[Ausencia]) -> Optional[Ausencia]:
    for a in ausencias:
        if a.re == re_txt and a.data_inicio <= d and (a.data_fim is None or a.data_fim >= d):
            return a
    return None


def mensagem_ausencia(a: Ausencia) -> str:
    tipo = TIPO_AUSENCIA.get(a.tipo, a.tipo)
    if a.data_fim is None:
        return f"RE {a.re} está de {tipo} desde {ddmm(a.data_inicio)}, sem data de volta"
    if a.data_fim == a.data_inicio:
        return f"RE {a.re} está de {tipo} em {ddmm(a.data_inicio)}"
    return f"RE {a.re} está de {tipo} de {ddmm(a.data_inicio)} a {ddmm(a.data_fim)}"


# ─── Avaliação do dia ─────────────────────────────────────────────────────────

def _intervalo(a: Aloc, padroes: dict[int, tuple[time, time]]) -> tuple[time, time]:
    """Horário para comparar sobreposição. Linha sem horário (fiel ao Excel)
    é comparada pelo padrão do período."""
    p_ini, p_fim = padroes[a.periodo]
    return a.hora_inicio or p_ini, a.hora_termino or p_fim


def _sobrepoe(x: tuple[time, time], y: tuple[time, time]) -> bool:
    return x[0] < y[1] and y[0] < x[1]


def _rotulo(ctx: Contexto, a: Aloc) -> str:
    p = ctx.postos.get(a.posto_id)
    return f"{p.rotulo if p else a.posto_id} ({a.periodo}º período)"


def dobra_calculada(re_txt: Optional[str], ctx: Contexto) -> tuple[bool, str]:
    if not re_txt:
        return False, "sem_re"
    folga, motivo = folga_no_dia(re_txt, ctx.data, ctx.quadro, ctx.trocas)
    return bool(folga), motivo


def avaliar(
    alocs: list[Aloc],
    ctx: Contexto,
    *,
    base: Optional[list[Aloc]] = None,
    usar_congelada: bool = False,
) -> Avaliacao:
    """Avalia a escala de um dia. `base` = o que já está gravado (pergunta
    de RN08 sem ponto final e de RN11 só fica PENDENTE quando a situação é
    nova em relação ao gravado). `usar_congelada` = escala publicada: a
    dobra vem de dobra_publicada quando existe."""
    av = Avaliacao()
    d = ctx.data
    base_por_re: dict[str, list[Aloc]] = {}
    for b in base or []:
        if b.situacao == "escalado" and b.re:
            base_por_re.setdefault(b.re, []).append(b)

    virada = virada_de_mes(d)
    if virada:
        av.avisos.append(virada)

    por_re: dict[str, list[Aloc]] = {}
    for a in alocs:
        linha = {"dobra": False, "acumulo": False, "folga": None, "motivo_folga": None,
                 "ausencia": None, "nome": ctx.nomes.get(a.re) if a.re else None,
                 "no_quadro": bool(a.re and a.re in ctx.quadro)}
        av.linhas[a.chave] = linha

        # ── bloqueios de forma ──
        if a.situacao == "escalado" and not (a.re or "").strip():
            av.bloqueios.append({"codigo": "re_vazio", "posto_id": a.posto_id, "periodo": a.periodo,
                                 "mensagem": f"{_rotulo(ctx, a)}: situação \"escalado\" sem RE"})
        if (a.outra_garagem or "").upper() == GARAGEM_PROPRIA or (a.marcador or "").strip().upper() == GARAGEM_PROPRIA:
            av.bloqueios.append({"codigo": "g3", "posto_id": a.posto_id, "periodo": a.periodo,
                                 "mensagem": f"{_rotulo(ctx, a)}: G3 é a própria garagem, não pode ser \"outra garagem\""})
        if a.hora_inicio and a.hora_termino and a.hora_termino <= a.hora_inicio:
            av.bloqueios.append({"codigo": "horario", "posto_id": a.posto_id, "periodo": a.periodo,
                                 "mensagem": f"{_rotulo(ctx, a)}: o término tem que ser depois do início "
                                             f"(posto de fiscal não passa da meia-noite)"})
        if a.situacao != "escalado" or not a.re:
            continue
        por_re.setdefault(a.re, []).append(a)

        # ── RN05 ──
        aus = ausencia_no_dia(a.re, d, ctx.ausencias)
        if aus is not None:
            linha["ausencia"] = aus.tipo
            av.bloqueios.append({"codigo": "RN05", "posto_id": a.posto_id, "periodo": a.periodo, "re": a.re,
                                 "mensagem": mensagem_ausencia(aus)})

        # ── RN02/RN03/RN06/RN07 ──
        folga, motivo = folga_no_dia(a.re, d, ctx.quadro, ctx.trocas)
        linha["folga"], linha["motivo_folga"] = folga, motivo
        if usar_congelada and a.dobra_publicada is not None:
            linha["dobra"] = a.dobra_publicada
        else:
            linha["dobra"] = bool(folga)

        # ── RN04 ──
        if linha["dobra"] and a.re in ctx.dobras_anteriores:
            quando = ctx.dobras_anteriores[a.re]
            dia_nome = "sábado" if quando.weekday() == SABADO else "domingo"
            confirmada = a.dobra_seguida_confirmada_por is not None
            av.perguntas.append({
                "codigo": "RN04", "chave": f"RN04|{a.posto_id}|{a.periodo}|{a.re}",
                "posto_id": a.posto_id, "periodo": a.periodo, "re": a.re,
                "pendente": not confirmada,
                "confirmada_por": a.dobra_seguida_confirmada_por,
                "confirmada_em": a.dobra_seguida_confirmada_em,
                "mensagem": f"RE {a.re} dobrou no {dia_nome} {ddmm(quando)}. "
                            f"Deseja mesmo escalar de novo neste fim de semana?",
            })

    # ── RN08 e RN11 (por RE) ──
    acumulos: list[dict] = []
    for re_txt, lista in por_re.items():
        for i, x in enumerate(lista):
            for y in lista[i + 1:]:
                if not _sobrepoe(_intervalo(x, ctx.padroes), _intervalo(y, ctx.padroes)):
                    continue
                px, py = ctx.postos.get(x.posto_id), ctx.postos.get(y.posto_id)
                pfx = px.ponto_final_id if px else None
                pfy = py.ponto_final_id if py else None
                if pfx and pfy and pfx == pfy:
                    av.linhas[x.chave]["acumulo"] = av.linhas[y.chave]["acumulo"] = True
                    acumulos.append({"re": re_txt, "ponto_final": px.ponto_final_nome,
                                     "postos": [_rotulo(ctx, x), _rotulo(ctx, y)]})
                elif pfx and pfy:
                    av.bloqueios.append({
                        "codigo": "RN08", "posto_id": y.posto_id, "periodo": y.periodo, "re": re_txt,
                        "mensagem": f"RE {re_txt} já está no posto {px.rotulo}, ponto final "
                                    f"{px.ponto_final_nome}, nesse horário (e {py.rotulo} é no ponto "
                                    f"final {py.ponto_final_nome})",
                    })
                else:
                    chave = "RN08|" + re_txt + "|" + "|".join(sorted(f"{a.posto_id}:{a.periodo}" for a in (x, y)))
                    ja_havia = _par_no_base(base_por_re.get(re_txt, []), x, y)
                    av.perguntas.append({
                        "codigo": "RN08", "chave": chave, "re": re_txt, "pendente": not ja_havia,
                        "posto_id": y.posto_id, "periodo": y.periodo,
                        "mensagem": f"RE {re_txt} em dois postos no mesmo horário ({_rotulo(ctx, x)} e "
                                    f"{_rotulo(ctx, y)}), e posto sem ponto final cadastrado. "
                                    f"Confirmar acúmulo?",
                    })
                    av.linhas[x.chave]["acumulo"] = av.linhas[y.chave]["acumulo"] = True
                    acumulos.append({"re": re_txt, "ponto_final": None,
                                     "postos": [_rotulo(ctx, x), _rotulo(ctx, y)]})
        periodos = {a.periodo for a in lista}
        if periodos == {1, 2}:
            ja_havia = {b.periodo for b in base_por_re.get(re_txt, [])} == {1, 2}
            av.perguntas.append({
                "codigo": "RN11", "chave": f"RN11|{re_txt}", "re": re_txt, "pendente": not ja_havia,
                "mensagem": f"RE {re_txt} está no 1º e no 2º período do mesmo dia. Confirmar?",
            })

    av.resumo = _resumo(alocs, ctx, av, acumulos, por_re)
    if av.resumo["sumidos"]:
        av.avisos.append({"codigo": "RN12", "res": [s["re"] for s in av.resumo["sumidos"]],
                          "mensagem": "Fiscais do quadro sem posto, folga ou ausência neste dia: "
                                      + ", ".join(s["re"] for s in av.resumo["sumidos"])})
    if av.resumo["folga_nao_confirmada"]:
        av.avisos.append({"codigo": "folga_nao_confirmada",
                          "res": list(av.resumo["folga_nao_confirmada"]),
                          "mensagem": "Folga base não confirmada (o sistema não calcula dobra para eles): "
                                      + ", ".join(av.resumo["folga_nao_confirmada"])})
    return av


def _par_no_base(base: list[Aloc], x: Aloc, y: Aloc) -> bool:
    chaves = {b.chave for b in base}
    return x.chave in chaves and y.chave in chaves


def _resumo(alocs: list[Aloc], ctx: Contexto, av: Avaliacao, acumulos: list[dict], por_re: dict) -> dict:
    d = ctx.data
    fim_de_semana = sabado_do_fim_de_semana(d) is not None
    descobertos = [
        {"posto_id": a.posto_id, "periodo": a.periodo, "posto": _rotulo(ctx, a), "marcador": a.marcador}
        for a in alocs if a.situacao == "descoberto"
    ]
    dobras = [
        {"re": a.re, "nome": ctx.nomes.get(a.re), "posto_id": a.posto_id, "periodo": a.periodo,
         "posto": _rotulo(ctx, a)}
        for a in alocs if av.linhas.get(a.chave, {}).get("dobra")
    ]
    folgas: dict[int, list[str]] = {1: [], 2: []}
    ausentes: dict[str, dict[int, list[dict]]] = {t: {0: [], 1: [], 2: []} for t in TIPO_AUSENCIA}
    ausentes_res: set[str] = set()
    for a in ctx.ausencias:
        if a.data_inicio <= d and (a.data_fim is None or a.data_fim >= d) and a.re not in ausentes_res:
            ausentes_res.add(a.re)
            fiscal = ctx.quadro.get(a.re)
            ausentes.setdefault(a.tipo, {0: [], 1: [], 2: []})[fiscal.periodo if fiscal else 0].append({
                "re": a.re, "nome": ctx.nomes.get(a.re), "data_inicio": a.data_inicio, "data_fim": a.data_fim,
            })
    sem_base: list[str] = []
    sumidos: list[dict] = []
    for fiscal in sorted(ctx.quadro.values(), key=lambda f: (f.periodo, len(f.re), f.re)):
        if not fiscal.ativo:
            continue
        folga, motivo = folga_no_dia(fiscal.re, d, ctx.quadro, ctx.trocas)
        if folga:
            folgas[fiscal.periodo].append(fiscal.re)
        if fim_de_semana and folga is None and fiscal.re in por_re:
            sem_base.append(fiscal.re)
        if fiscal.re not in por_re and not folga and fiscal.re not in ausentes_res:
            sumidos.append({"re": fiscal.re, "nome": ctx.nomes.get(fiscal.re), "periodo": fiscal.periodo,
                            "folga_desconhecida": folga is None and fim_de_semana})
    fora_quadro = sorted({re_txt for re_txt in por_re if re_txt not in ctx.quadro}, key=lambda r: (len(r), r))
    if fim_de_semana:
        sem_base = sorted(set(sem_base) | set(fora_quadro), key=lambda r: (len(r), r))
    return {
        "descobertos": descobertos,
        "dobras": dobras,
        "acumulos": acumulos,
        "sumidos": sumidos,
        "folgas": folgas,
        "ausentes": ausentes,
        "folga_nao_confirmada": sem_base,
        "fora_do_quadro": fora_quadro,
        "trocas": trocas_do_fim_de_semana(d, ctx.trocas),
    }
