"""Fechamento automático do fim de semana (29/10/2026) — funções PURAS.

O problema que resolve: toda semana o coordenador abre o modelo do sábado e
do domingo e caça, posto por posto, quem está de folga, quem está de férias,
quem sobrou e onde ficou buraco. Aqui o sistema faz essa conta e devolve uma
SUGESTÃO para os dois dias. ⛔ Nada é gravado: a sugestão cai na grade da
Montagem como mudança não salva, e o coordenador confere e salva como sempre
(o salvar passa por todas as regras RN01–RN14 de novo).

Como monta cada dia (sábado primeiro, depois o domingo):
  1. Parte do que já existe: o rascunho salvo, ou a prévia do modelo (que já
     tirou os ausentes e aplicou as trocas).
  2. LIBERA o posto de quem está escalado num dia que é folga dele (pelo
     modelo, o fiscal de folga viraria dobra sem ninguém perceber).
  3. Monta a lista de quem está LIVRE: do quadro, trabalha no dia (folga
     conhecida = não), não está ausente e não está em posto nenhum.
  4. Preenche as VAGAS na ordem de prioridade:
       a) obrigatórias — todo posto TP (fim de semana não fica sem fiscal) e
          os TS marcados "obrigatório no fim de semana" (as rendições);
       b) as do TS que o modelo tinha com fiscal (só se sobrar gente).
     Para cada vaga escolhe o livre do mesmo período que melhor conhece o
     posto: o mesmo posto em algum modelo > o mesmo ponto final > qualquer.
     O fiscal do TS que fica sem posto no fim de semana cai aqui e vai para
     o TP, que é a regra da garagem.
  5. Sem livre: o fiscal que está num TS COMUM (não obrigatório) vai para o
     TP — no fim de semana o TS comum pode ficar em branco, o TP não.
  6. Ainda sem ninguém: ACÚMULO — um fiscal já escalado no mesmo ponto final, no
     mesmo horário, cobre também esta vaga (regra RN08: mesmo ponto final é
     permitido). Quando já se sabe que vai faltar gente, o acúmulo vem
     ANTES, para o livre ir a um posto que não tem essa saída.
  7. Ainda vaga obrigatória e modo "com_dobra": sugere DOBRA de quem está
     de folga pela folga base (nunca quem folga por troca), não dobrou no fim
     de semana anterior (RN04), não dobra no outro dia deste fim de semana e
     dobrou menos nas últimas semanas (rodízio justo).
  8. O que sobra fica EM BRANCO, com a nota do porquê. No modo "sem_dobra"
     (padrão) a vaga obrigatória fica em branco e entra na conta do déficit.

Só mexe nos períodos que o usuário edita (RN10). Escala publicada não é
mexida: a sugestão é só para prévia e rascunho.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, time
from typing import Optional
from uuid import UUID

from app.services import escala_fiscais_regras as regras
from app.services.escala_fiscais_regras import Aloc

MODOS = ("sem_dobra", "com_dobra")
SITUACOES_A_PREENCHER = ("escalado", "descoberto")


# ─── Estruturas ───────────────────────────────────────────────────────────────

@dataclass
class DiaEntrada:
    """O que o fechamento precisa saber de um dia."""
    ctx: regras.Contexto
    alocs: list[Aloc]                      # base: rascunho salvo ou prévia do modelo
    notas: dict = field(default_factory=dict)
    # (posto_id, periodo) → RE padrão do MODELO do dia (para as notas)
    padrao_modelo: dict = field(default_factory=dict)
    ordem: dict = field(default_factory=dict)  # (posto_id, periodo) → ordem no modelo


@dataclass
class Familiaridade:
    """Quem conhece qual posto, tirado de TODOS os modelos (útil, sábado e
    domingo, ímpar e par): RE → postos onde ele é o padrão."""
    postos_por_re: dict[str, set[UUID]] = field(default_factory=dict)

    def nota(self, re_txt: str, posto: regras.PostoInfo, postos: dict[UUID, regras.PostoInfo]) -> int:
        conhecidos = self.postos_por_re.get(re_txt, set())
        if posto.id in conhecidos:
            return 2
        if posto.ponto_final_id and any(
            postos.get(p) and postos[p].ponto_final_id == posto.ponto_final_id for p in conhecidos
        ):
            return 1
        return 0


@dataclass
class DiaSugerido:
    data: date
    alocs: list[Aloc]
    notas: dict
    mudancas: list[dict] = field(default_factory=list)
    vagas: list[dict] = field(default_factory=list)      # obrigatórias que ficaram em branco
    dobras: list[str] = field(default_factory=list)       # REs com dobra SUGERIDA
    dobras_info: list[dict] = field(default_factory=list)
    acumulos: list[dict] = field(default_factory=list)
    remanejados: list[dict] = field(default_factory=list)  # fiscal do TS comum levado ao TP
    sobrando: list[str] = field(default_factory=list)     # livres que não precisaram entrar
    liberados: list[dict] = field(default_factory=list)   # padrão do modelo que está de folga
    fora_do_periodo: list[dict] = field(default_factory=list)  # vagas em período que o usuário não edita
    # Postos obrigatórios do dia por período do FISCAL (cobertos ou não).
    obrigatorios: dict = field(default_factory=lambda: {1: 0, 2: 0})


# ─── Ajudantes ────────────────────────────────────────────────────────────────

def _minutos(t: time) -> int:
    return t.hour * 60 + t.minute


def periodo_do_fiscal(a: Aloc, padroes: dict[int, tuple[time, time]]) -> int:
    """Que fiscal (1º ou 2º período) cobre esta linha, pelo HORÁRIO. A
    rendição das 12:00 às 20:20 aparece na coluna do 1º período da planilha,
    mas quem cobre é fiscal da tarde (pega mais horas do 2º)."""
    ini, fim = regras._intervalo(a, padroes)
    melhor, maior = a.periodo, -1
    for p, (p_ini, p_fim) in sorted(padroes.items()):
        sobra = min(_minutos(fim), _minutos(p_fim)) - max(_minutos(ini), _minutos(p_ini))
        if sobra > maior or (sobra == maior and p == a.periodo):
            melhor, maior = p, sobra
    return melhor


def posto_obrigatorio(posto: Optional[regras.PostoInfo]) -> bool:
    """TP nunca fica sem fiscal no fim de semana; TS só os marcados."""
    if posto is None:
        return False
    return posto.lado == "TP" or bool(posto.obrigatorio_fds)


def _escalar(a: Aloc, re_txt: str) -> None:
    a.situacao, a.re, a.marcador, a.outra_garagem = "escalado", re_txt, None, None
    a.dobra_seguida_confirmada_por = a.dobra_seguida_confirmada_em = a.dobra_publicada = None


def _esvaziar(a: Aloc) -> None:
    a.situacao, a.re, a.marcador, a.outra_garagem = "descoberto", None, "", None
    a.dobra_seguida_confirmada_por = a.dobra_seguida_confirmada_em = a.dobra_publicada = None


def _copia(a: Aloc) -> Aloc:
    return Aloc(**{k: getattr(a, k) for k in a.__dataclass_fields__})


def _ordenar_res(res) -> list[str]:
    return sorted(res, key=lambda r: (len(r), r))


def _texto_folga(re_txt: str, d: date, ctx: regras.Contexto) -> str:
    fiscal = ctx.quadro.get(re_txt)
    par = "ímpar" if regras.mes_de_referencia(d) % 2 else "par"
    base = fiscal.folga_base if fiscal and fiscal.folga_base else "?"
    return f"RE {re_txt} folga neste dia (folga base {base}, mês {par})"


# ─── Um dia ───────────────────────────────────────────────────────────────────

def sugerir_dia(
    entrada: DiaEntrada,
    *,
    modo: str,
    editaveis: set[int],
    familiaridade: Familiaridade,
    historico_dobras: Counter,
    nao_dobrar: set[str],
) -> DiaSugerido:
    """Sugestão de UM dia. `nao_dobrar` = quem não pode receber dobra neste
    dia (dobrou no fim de semana anterior ou já dobra no outro dia deste)."""
    if modo not in MODOS:
        raise ValueError(f"modo inválido: {modo}")
    ctx = entrada.ctx
    d = ctx.data
    alocs = [_copia(a) for a in entrada.alocs]
    notas = dict(entrada.notas)
    saida = DiaSugerido(data=d, alocs=alocs, notas=notas)
    originais = {a.chave: (a.re, a.marcador, a.situacao) for a in entrada.alocs}

    def pode(a: Aloc) -> bool:
        return a.periodo in editaveis

    def rotulo(a: Aloc) -> str:
        p = ctx.postos.get(a.posto_id)
        return f"{p.rotulo if p else a.posto_id} ({a.periodo}º período)"

    # ── 2. libera quem está de folga no dia ──
    for a in alocs:
        if a.situacao != "escalado" or not a.re or not pode(a):
            continue
        folga, motivo = regras.folga_no_dia(a.re, d, ctx.quadro, ctx.trocas)
        if folga is True:
            saida.liberados.append({"re": a.re, "posto": rotulo(a), "motivo": motivo})
            notas[a.chave] = _texto_folga(a.re, d, ctx) if motivo == "base" else \
                f"RE {a.re} folga neste dia pela troca ({motivo.replace('troca_', '')})"
            _esvaziar(a)

    # ── 3. quem está livre ──
    escalados = {a.re for a in alocs if a.situacao == "escalado" and a.re}
    livres: dict[int, list[str]] = {1: [], 2: []}
    for f in ctx.quadro.values():
        if not f.ativo or f.re in escalados or regras.ausencia_no_dia(f.re, d, ctx.ausencias):
            continue
        folga, _ = regras.folga_no_dia(f.re, d, ctx.quadro, ctx.trocas)
        if folga is False and f.periodo in livres:
            livres[f.periodo].append(f.re)
    for p in livres:
        livres[p] = _ordenar_res(livres[p])

    # ── vagas, em ordem de prioridade ──
    ordem = entrada.ordem
    vagas_obrig: list[Aloc] = []
    vagas_opc: list[Aloc] = []
    for a in alocs:
        if a.situacao in SITUACOES_A_PREENCHER and posto_obrigatorio(ctx.postos.get(a.posto_id)):
            saida.obrigatorios[periodo_do_fiscal(a, ctx.padroes)] += 1
    for a in sorted(alocs, key=lambda x: (ordem.get(x.chave, 10_000), x.periodo)):
        if a.situacao != "descoberto":
            continue
        posto = ctx.postos.get(a.posto_id)
        obrig = posto_obrigatorio(posto)
        if not pode(a):
            if obrig:
                saida.fora_do_periodo.append({"posto": rotulo(a), "posto_id": a.posto_id, "periodo": a.periodo})
            continue
        if obrig:
            vagas_obrig.append(a)
        elif originais.get(a.chave, (None,))[0] or entrada.padrao_modelo.get(a.chave):
            # TS que o modelo (ou o rascunho) tinha com fiscal: só com sobra.
            vagas_opc.append(a)

    # Intervalos já ocupados por RE (para nunca pôr alguém em dois lugares).
    ocupado: dict[str, list[tuple[Aloc, tuple[time, time]]]] = {}
    for a in alocs:
        if a.situacao == "escalado" and a.re:
            ocupado.setdefault(a.re, []).append((a, regras._intervalo(a, ctx.padroes)))

    def livre_no_horario(re_txt: str, a: Aloc, aceitar_mesmo_ponto: bool) -> bool:
        intervalo = regras._intervalo(a, ctx.padroes)
        posto = ctx.postos.get(a.posto_id)
        for outra, iv in ocupado.get(re_txt, []):
            if not regras._sobrepoe(iv, intervalo):
                continue
            p2 = ctx.postos.get(outra.posto_id)
            mesmo_ponto = (posto and p2 and posto.ponto_final_id and posto.ponto_final_id == p2.ponto_final_id)
            if not (aceitar_mesmo_ponto and mesmo_ponto):
                return False
        return True

    def ocupar(re_txt: str, a: Aloc) -> None:
        _escalar(a, re_txt)
        ocupado.setdefault(re_txt, []).append((a, regras._intervalo(a, ctx.padroes)))

    def escolher_livre(a: Aloc) -> Optional[str]:
        posto = ctx.postos.get(a.posto_id)
        lista = livres[periodo_do_fiscal(a, ctx.padroes)]
        candidatos = [r for r in lista if livre_no_horario(r, a, aceitar_mesmo_ponto=False)]
        if not candidatos or posto is None:
            return candidatos[0] if candidatos else None
        return max(candidatos, key=lambda r: (familiaridade.nota(r, posto, ctx.postos), -lista.index(r)))

    def escolher_acumulo(a: Aloc) -> Optional[str]:
        posto = ctx.postos.get(a.posto_id)
        if posto is None or not posto.ponto_final_id:
            return None
        per = periodo_do_fiscal(a, ctx.padroes)
        for outra in alocs:
            if outra is a or outra.situacao != "escalado" or not outra.re:
                continue
            p2 = ctx.postos.get(outra.posto_id)
            if not p2 or p2.ponto_final_id != posto.ponto_final_id:
                continue
            if periodo_do_fiscal(outra, ctx.padroes) != per:
                continue
            if not regras._sobrepoe(regras._intervalo(outra, ctx.padroes), regras._intervalo(a, ctx.padroes)):
                continue
            if livre_no_horario(outra.re, a, aceitar_mesmo_ponto=True):
                return outra.re
        return None

    def escolher_dobra(a: Aloc) -> Optional[str]:
        posto = ctx.postos.get(a.posto_id)
        per = periodo_do_fiscal(a, ctx.padroes)
        candidatos = []
        for f in ctx.quadro.values():
            if not f.ativo or f.periodo != per or f.re in nao_dobrar or f.re in ocupado:
                continue
            if regras.ausencia_no_dia(f.re, d, ctx.ausencias):
                continue
            folga, motivo = regras.folga_no_dia(f.re, d, ctx.quadro, ctx.trocas)
            if folga is not True or motivo != "base":
                continue
            candidatos.append(f.re)
        if not candidatos:
            return None
        return min(
            _ordenar_res(candidatos),
            key=lambda r: (historico_dobras.get(r, 0), -(familiaridade.nota(r, posto, ctx.postos) if posto else 0)),
        )

    def remanejaveis(per: int, a: Optional[Aloc] = None) -> list[Aloc]:
        """Fiscais escalados em TS COMUM (não obrigatório) que podem ir para
        o TP — a regra da garagem no fim de semana."""
        saida_r = []
        for b in alocs:
            if b is a or b.situacao != "escalado" or not b.re or not pode(b):
                continue
            if posto_obrigatorio(ctx.postos.get(b.posto_id)) or periodo_do_fiscal(b, ctx.padroes) != per:
                continue
            if sum(1 for x in alocs if x.re == b.re and x.situacao == "escalado") != 1:
                continue  # está em mais de um posto: não mexe
            saida_r.append(b)
        return saida_r

    def remanejar(a: Aloc) -> Optional[str]:
        per = periodo_do_fiscal(a, ctx.padroes)
        posto = ctx.postos.get(a.posto_id)
        opcoes = remanejaveis(per, a)
        if not opcoes:
            return None
        b = max(opcoes, key=lambda x: familiaridade.nota(x.re, posto, ctx.postos) if posto else 0)
        re_txt = b.re
        ocupado[re_txt] = [(o, iv) for o, iv in ocupado.get(re_txt, []) if o is not b]
        notas[b.chave] = f"RE {re_txt} foi para o {rotulo(a)} (TS comum fica em branco no fim de semana)"
        _esvaziar(b)
        ocupar(re_txt, a)
        return re_txt

    def faltam_obrigatorias(per: int) -> int:
        return sum(1 for v in vagas_obrig if v.situacao == "descoberto" and periodo_do_fiscal(v, ctx.padroes) == per)

    def preencher(a: Aloc, obrigatoria: bool) -> None:
        per = periodo_do_fiscal(a, ctx.padroes)
        # Gente de menos para as vagas que faltam: o posto que dá para cobrir
        # por acúmulo (mesmo ponto final) fica com quem já está lá e o livre
        # vai para um posto que não tem essa saída.
        if obrigatoria and len(livres[per]) + len(remanejaveis(per, a)) < faltam_obrigatorias(per):
            re_txt = escolher_acumulo(a)
            if re_txt:
                ocupar(re_txt, a)
                posto = ctx.postos.get(a.posto_id)
                saida.acumulos.append({"re": re_txt, "posto": rotulo(a),
                                       "ponto_final": posto.ponto_final_nome if posto else None})
                notas[a.chave] = (f"{notas[a.chave]} → " if a.chave in notas else "") + \
                    f"Acúmulo: RE {re_txt} já está no mesmo ponto final"
                return
        re_txt = escolher_livre(a)
        if re_txt:
            livres[per].remove(re_txt)
            ocupar(re_txt, a)
            padrao = entrada.padrao_modelo.get(a.chave)
            motivo = notas.get(a.chave)
            notas[a.chave] = (f"{motivo} → " if motivo else "") + f"RE {re_txt} estava livre"
            if padrao and padrao != re_txt and not motivo:
                notas[a.chave] = f"No modelo: RE {padrao}. RE {re_txt} estava livre"
            return
        if obrigatoria:
            re_txt = remanejar(a)
            if re_txt:
                saida.remanejados.append({"re": re_txt, "posto": rotulo(a)})
                notas[a.chave] = (f"{notas[a.chave]} → " if a.chave in notas else "") + \
                    f"RE {re_txt} veio do TS (no fim de semana o TS comum vai para o TP)"
                return
        re_txt = escolher_acumulo(a)
        if re_txt:
            ocupar(re_txt, a)
            posto = ctx.postos.get(a.posto_id)
            saida.acumulos.append({"re": re_txt, "posto": rotulo(a),
                                   "ponto_final": posto.ponto_final_nome if posto else None})
            notas[a.chave] = (f"{notas[a.chave]} → " if a.chave in notas else "") + \
                f"Acúmulo: RE {re_txt} já está no mesmo ponto final"
            return
        if obrigatoria and modo == "com_dobra":
            re_txt = escolher_dobra(a)
            if re_txt:
                ocupar(re_txt, a)
                saida.dobras.append(re_txt)
                saida.dobras_info.append({"re": re_txt, "posto": rotulo(a), "periodo_fiscal": per,
                                          "vezes_antes": historico_dobras.get(re_txt, 0)})
                vezes = historico_dobras.get(re_txt, 0)
                notas[a.chave] = (f"{notas[a.chave]} → " if a.chave in notas else "") + \
                    f"Dobra sugerida: RE {re_txt} (dobrou {vezes}x nas últimas semanas)"
                return
        if obrigatoria:
            saida.vagas.append({"posto": rotulo(a), "posto_id": a.posto_id, "periodo": a.periodo,
                                "periodo_fiscal": per})
            notas[a.chave] = (f"{notas[a.chave]} → " if a.chave in notas else "") + \
                ("Sem fiscal livre: posto em branco" if modo == "sem_dobra"
                 else "Sem fiscal livre e sem quem possa dobrar: posto em branco")

    for a in vagas_obrig:
        preencher(a, obrigatoria=True)
    for a in vagas_opc:
        if livres[periodo_do_fiscal(a, ctx.padroes)]:
            preencher(a, obrigatoria=False)

    saida.sobrando = _ordenar_res(livres[1] + livres[2])

    # ── o que mudou em relação à base ──
    for a in alocs:
        antes = originais.get(a.chave)
        depois = (a.re, a.marcador, a.situacao)
        virou_dobra = bool(a.re and a.re in saida.dobras and any(
            x["re"] == a.re and x["posto"] == rotulo(a) for x in saida.dobras_info))
        if antes is None or (antes == depois and not virou_dobra):
            continue
        de = antes[0] or (antes[1] if antes[1] else "em branco")
        para = a.re or (a.marcador if a.marcador else "em branco")
        tipo = ("dobra" if virou_dobra else
                "remanejado" if any(x["re"] == a.re and x["posto"] == rotulo(a) for x in saida.remanejados) else
                "acumulo" if any(x["re"] == a.re and x["posto"] == rotulo(a) for x in saida.acumulos) else
                "livre" if a.re else "branco")
        saida.mudancas.append({"posto_id": a.posto_id, "periodo": a.periodo, "posto": rotulo(a),
                               "antes": de, "depois": para, "tipo": tipo, "nota": notas.get(a.chave)})
    return saida


# ─── Fim de semana ────────────────────────────────────────────────────────────

def sugerir_fim_de_semana(
    sabado: Optional[DiaEntrada],
    domingo: Optional[DiaEntrada],
    *,
    modo: str,
    editaveis: set[int],
    familiaridade: Familiaridade,
    historico_dobras: Counter,
) -> dict[str, Optional[DiaSugerido]]:
    """Sábado primeiro, depois o domingo. Quem recebeu dobra no sábado não
    recebe no domingo. None = dia que não entra (ex.: já publicado)."""
    saida: dict[str, Optional[DiaSugerido]] = {"sabado": None, "domingo": None}
    ja_dobrou: set[str] = set()
    for nome, entrada in (("sabado", sabado), ("domingo", domingo)):
        if entrada is None:
            continue
        bloqueados = set(entrada.ctx.dobras_anteriores) | ja_dobrou
        dia = sugerir_dia(entrada, modo=modo, editaveis=editaveis, familiaridade=familiaridade,
                          historico_dobras=historico_dobras, nao_dobrar=bloqueados)
        ja_dobrou |= set(dia.dobras)
        saida[nome] = dia
    return saida


def conta_do_quadro(
    sab: Optional[DiaSugerido], dom: Optional[DiaSugerido], *, quadro_por_periodo: dict[int, int],
    periodos: set[int],
) -> list[dict]:
    """A conta do déficit do fim de semana, por período: cada fiscal trabalha
    UM dos dois dias, então o que pesa é a SOMA dos postos obrigatórios de
    sábado e domingo. Quadro ideal = soma ÷ 0,9 (reserva de ~10% para
    férias, atestado e falta)."""
    saida = []
    for p in sorted(periodos):
        obrig = sum(d.obrigatorios.get(p, 0) for d in (sab, dom) if d)
        vagas = sum(1 for d in (sab, dom) if d for v in d.vagas if v.get("periodo_fiscal") == p)
        dobras = sum(1 for d in (sab, dom) if d for x in d.dobras_info if x["periodo_fiscal"] == p)
        no_quadro = quadro_por_periodo.get(p, 0)
        ideal = -(-obrig * 10 // 9) if obrig else 0  # teto de obrig ÷ 0,9
        saida.append({
            "periodo": p,
            "postos_obrigatorios": obrig,
            "fiscais_no_quadro": no_quadro,
            "falta_sem_dobra": max(0, obrig - no_quadro),
            "vagas_em_branco": vagas,
            "dobras_sugeridas": dobras,
            "quadro_ideal": ideal,
            "contratar": max(0, ideal - no_quadro),
        })
    return saida
