"""Montagem da escala de um dia (Fase 4) — a parte que fala com o banco.

As regras moram em escala_fiscais_regras.py (funções puras). Aqui:
  · carregar o contexto do dia (postos, quadro, ausências, trocas, dobras
    do fim de semana anterior, nomes por RE);
  · montar a PRÉVIA de um dia novo: copia o modelo (RN01), tira quem está
    ausente deixando o posto em branco com a nota do motivo, aplica as
    trocas e puxa o plantão dos coordenadores do cadastro;
  · salvar (rascunho, ou nova versão de escala publicada com registro em
    escala_fiscal_alteracao) e publicar (congela a dobra — RN03/RN14).

Armadilha conhecida: alocação tem UNIQUE (escala_dia_id, posto_id,
periodo). Salvar ATUALIZA a linha que já existe na mesma chave, apaga só as
que saíram e dá flush antes de inserir — nunca "apaga tudo e insere" na
mesma unidade de trabalho.
"""
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.cadastro import Funcionario
from app.models.escala_fiscais import (
    EscalaFiscalAlocacao,
    EscalaFiscalAlteracao,
    EscalaFiscalAusencia,
    EscalaFiscalCoordenadorHorario,
    EscalaFiscalDia,
    EscalaFiscalModelo,
    EscalaFiscalModeloPosto,
    EscalaFiscalPlantao,
    EscalaFiscalPontoFinal,
    EscalaFiscalPosto,
    EscalaFiscalQuadro,
    EscalaFiscalTroca,
)
from app.services import escala_fiscais_regras as regras
from app.services.escala_fiscais_regras import Aloc

CAMPOS_ALOC = ("situacao", "re", "outra_garagem", "marcador", "hora_inicio", "hora_termino")


def nomes_por_re(db: Session, res: Iterable[Optional[str]]) -> dict[str, str]:
    """D-A: RE → nome, só dos REs que têm cadastro. Junção pelo RE na leitura."""
    res = {r for r in res if r}
    if not res:
        return {}
    return {f.re: f.nome for f in db.execute(select(Funcionario).where(Funcionario.re.in_(res))).scalars()}


# ─── Contexto ─────────────────────────────────────────────────────────────────

def carregar_postos(db: Session) -> dict[UUID, regras.PostoInfo]:
    pontos = {p.id: p.nome for p in db.execute(select(EscalaFiscalPontoFinal)).scalars()}
    return {
        p.id: regras.PostoInfo(
            id=p.id, lado=p.lado, cod_jb=p.cod_jb, lote=p.lote, linhas=[pl.linha for pl in p.linhas],
            ponto_final_id=p.ponto_final_id, ponto_final_nome=pontos.get(p.ponto_final_id),
        )
        for p in db.execute(select(EscalaFiscalPosto)).scalars()
    }


def _quadro(db: Session) -> dict[str, regras.FiscalQuadro]:
    return {
        q.re: regras.FiscalQuadro(re=q.re, periodo=q.periodo, folga_base=q.folga_base, ativo=q.ativo)
        for q in db.execute(select(EscalaFiscalQuadro)).scalars()
    }


def _ausencias(db: Session, de: date, ate: date) -> list[regras.Ausencia]:
    linhas = db.execute(
        select(EscalaFiscalAusencia).where(EscalaFiscalAusencia.data_inicio <= ate)
        .order_by(EscalaFiscalAusencia.data_inicio)
    ).scalars()
    return [
        regras.Ausencia(re=a.re, tipo=a.tipo, data_inicio=a.data_inicio, data_fim=a.data_fim)
        for a in linhas if a.data_fim is None or a.data_fim >= de
    ]


def _trocas(db: Session, d: date) -> list[regras.Troca]:
    sab = regras.sabado_do_fim_de_semana(d)
    if sab is None:
        return []
    linhas = db.execute(
        select(EscalaFiscalTroca).where(EscalaFiscalTroca.data_sabado.in_([sab, sab - timedelta(days=7)]))
        .order_by(EscalaFiscalTroca.criado_em)
    ).scalars()
    return [regras.Troca(tipo=t.tipo, re_a=t.re_a, re_b=t.re_b, data_sabado=t.data_sabado) for t in linhas]


def carregar_contexto(db: Session, d: date, padroes: dict, *, com_anteriores: bool = True) -> regras.Contexto:
    ctx = regras.Contexto(
        data=d, postos=carregar_postos(db), quadro=_quadro(db), ausencias=_ausencias(db, d, d),
        trocas=_trocas(db, d), padroes=padroes,
    )
    if com_anteriores:
        ctx.dobras_anteriores = dobras_do_fim_de_semana_anterior(db, d, padroes, postos=ctx.postos)
    return ctx


def dobras_do_dia(db: Session, d: date, padroes: dict, postos=None) -> set[str]:
    """REs que dobraram em `d`. Publicada: a dobra congelada. Rascunho:
    calculada agora (a dobra do rascunho nunca é gravada)."""
    dia = db.execute(select(EscalaFiscalDia).where(EscalaFiscalDia.data == d)).scalar_one_or_none()
    if dia is None:
        return set()
    alocs = [aloc_de(a) for a in alocacoes_do_dia(db, dia.id)]
    ctx = carregar_contexto(db, d, padroes, com_anteriores=False)
    if postos is not None:
        ctx.postos = postos
    av = regras.avaliar(alocs, ctx, base=alocs, usar_congelada=dia.status == "publicada")
    return {a.re for a in alocs if a.re and av.linhas[a.chave]["dobra"]}


def dobras_do_fim_de_semana_anterior(db: Session, d: date, padroes: dict, postos=None) -> dict[str, date]:
    sab = regras.sabado_do_fim_de_semana(d)
    if sab is None:
        return {}
    anterior = sab - timedelta(days=7)
    saida: dict[str, date] = {}
    for dia in (anterior, anterior + timedelta(days=1)):
        for re_txt in sorted(dobras_do_dia(db, dia, padroes, postos)):
            saida.setdefault(re_txt, dia)
    return saida


# ─── Leitura do dia ───────────────────────────────────────────────────────────

def buscar_dia(db: Session, d: date) -> Optional[EscalaFiscalDia]:
    return db.execute(select(EscalaFiscalDia).where(EscalaFiscalDia.data == d)).scalar_one_or_none()


def alocacoes_do_dia(db: Session, dia_id: UUID) -> list[EscalaFiscalAlocacao]:
    return list(db.execute(
        select(EscalaFiscalAlocacao).where(EscalaFiscalAlocacao.escala_dia_id == dia_id)
    ).scalars())


def aloc_de(a) -> Aloc:
    return Aloc(
        posto_id=a.posto_id, periodo=a.periodo, situacao=a.situacao, re=a.re,
        hora_inicio=a.hora_inicio, hora_termino=a.hora_termino, outra_garagem=a.outra_garagem,
        marcador=a.marcador,
        dobra_seguida_confirmada_por=getattr(a, "dobra_seguida_confirmada_por", None),
        dobra_seguida_confirmada_em=getattr(a, "dobra_seguida_confirmada_em", None),
        dobra_publicada=getattr(a, "dobra_publicada", None),
    )


def modelos_disponiveis(db: Session) -> list[EscalaFiscalModelo]:
    return list(db.execute(select(EscalaFiscalModelo).order_by(EscalaFiscalModelo.nome)).scalars())


def modelo_sugerido(db: Session, d: date) -> Optional[EscalaFiscalModelo]:
    """RN01: pelo mês do PRÓPRIO dia."""
    tipo, par = regras.modelo_sugerido(d)
    for m in modelos_disponiveis(db):
        if m.tipo_dia == tipo and m.paridade == par:
            return m
    return None


def _itens_modelo(db: Session, modelo_id: UUID) -> list[EscalaFiscalModeloPosto]:
    return list(db.execute(
        select(EscalaFiscalModeloPosto).where(EscalaFiscalModeloPosto.modelo_id == modelo_id)
        .order_by(EscalaFiscalModeloPosto.ordem, EscalaFiscalModeloPosto.periodo)
    ).scalars())


@dataclass
class Previa:
    alocs: list[Aloc]
    notas: dict[tuple[UUID, int], str] = field(default_factory=dict)


def previa(db: Session, d: date, modelo: EscalaFiscalModelo, ctx: regras.Contexto) -> Previa:
    """Dia novo: copia o modelo, tira os ausentes (posto em branco com a
    nota do motivo) e aplica as trocas. ⛔ Não grava nada."""
    alocs: list[Aloc] = []
    notas: dict[tuple[UUID, int], str] = {}
    for mp in _itens_modelo(db, modelo.id):
        a = Aloc(
            posto_id=mp.posto_id, periodo=mp.periodo, situacao=mp.situacao_padrao, re=mp.re_padrao,
            hora_inicio=mp.hora_inicio, hora_termino=mp.hora_termino, outra_garagem=mp.outra_garagem,
            marcador=mp.marcador,
        )
        if a.re:
            aus = regras.ausencia_no_dia(a.re, d, ctx.ausencias)
            if aus is not None:
                notas[a.chave] = regras.mensagem_ausencia(aus) + ": posto em branco"
                a.situacao, a.re, a.marcador = "descoberto", None, ""
        alocs.append(a)
    _aplicar_trocas(alocs, notas, ctx)
    return Previa(alocs=alocs, notas=notas)


def _aplicar_trocas(alocs: list[Aloc], notas: dict, ctx: regras.Contexto) -> None:
    """Quem está escalado pelo modelo num dia que a troca deixou de folga dá
    o lugar ao parceiro da troca — só quando o parceiro TRABALHA nesse dia
    (sabido, não 'folga não confirmada'), não está escalado e não está
    ausente. Fora disso a escala fica como o modelo e a dobra aparece."""
    d = ctx.data
    escalados = {a.re for a in alocs if a.re}
    for t in ctx.trocas:
        for x, y in ((t.re_a, t.re_b), (t.re_b, t.re_a)):
            if x not in escalados or y in escalados:
                continue
            folga_x, _ = regras.folga_no_dia(x, d, ctx.quadro, ctx.trocas)
            folga_y, _ = regras.folga_no_dia(y, d, ctx.quadro, ctx.trocas)
            if folga_x is not True or folga_y is not False or regras.ausencia_no_dia(y, d, ctx.ausencias):
                continue
            for a in alocs:
                if a.re == x:
                    a.re = y
                    notas[a.chave] = f"Troca {t.tipo} {t.re_a} × {t.re_b}: {y} no lugar de {x}"
            escalados.discard(x)
            escalados.add(y)


def plantao_do_cadastro(db: Session) -> list[dict]:
    """Rodapé: coordenadores de plantão, copiados do cadastro (ativos)."""
    linhas = db.execute(
        select(EscalaFiscalCoordenadorHorario).where(EscalaFiscalCoordenadorHorario.ativo.is_(True))
    ).scalars().all()
    linhas = sorted(linhas, key=lambda h: (h.turno != "manha", h.hora_inicio))
    saida, ordem = [], {"manha": 0, "tarde": 0}
    for h in linhas:
        ordem[h.turno] += 1
        saida.append({"turno": h.turno, "ordem": ordem[h.turno], "funcionario_id": h.funcionario_id,
                      "hora_inicio": h.hora_inicio, "hora_fim": h.hora_fim})
    return saida


def plantao_do_dia(db: Session, dia_id: UUID) -> list[dict]:
    linhas = db.execute(
        select(EscalaFiscalPlantao).where(EscalaFiscalPlantao.escala_dia_id == dia_id)
    ).scalars().all()
    return [
        {"turno": p.turno, "ordem": p.ordem, "funcionario_id": p.funcionario_id,
         "hora_inicio": p.hora_inicio, "hora_fim": p.hora_fim}
        for p in sorted(linhas, key=lambda p: (p.turno != "manha", p.ordem))
    ]


# ─── Resposta montada (tela e impressão) ─────────────────────────────────────

def _hhmm(t: Optional[time]) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


def titulo(d: date, lado: str, modelo: Optional[EscalaFiscalModelo], data_util: Optional[date] = None) -> str:
    """Título como na planilha."""
    tipo = regras.tipo_do_dia(d)
    if tipo == "util":
        ref = data_util or d
        return f"ESCALA MENSAL DIAS UTIL A PARTIR DE {ref.strftime('%d/%m/%Y')} - {lado}"
    nome = "SABADO" if tipo == "sabado" else "DOMINGO"
    return f"ESCALA {nome} {d.strftime('%d / %m / %Y')} - {lado}"


def montar_resposta(
    db: Session,
    d: date,
    *,
    dia: Optional[EscalaFiscalDia],
    modelo: Optional[EscalaFiscalModelo],
    alocs: list[Aloc],
    plantao: list[dict],
    ctx: regras.Contexto,
    base: list[Aloc],
    notas: Optional[dict] = None,
) -> dict:
    notas = dict(notas or {})
    publicada = dia is not None and dia.status == "publicada"
    av = regras.avaliar(alocs, ctx, base=base, usar_congelada=publicada)

    # Nota do posto em branco porque o fiscal padrão está ausente (lida,
    # não gravada: vale também depois de salvo).
    if modelo is not None:
        padrao = {(mp.posto_id, mp.periodo): mp.re_padrao for mp in _itens_modelo(db, modelo.id)}
        ordem = {(mp.posto_id, mp.periodo): mp.ordem for mp in _itens_modelo(db, modelo.id)}
    else:
        padrao, ordem = {}, {}
    for a in alocs:
        re_padrao = padrao.get(a.chave)
        if a.chave not in notas and a.situacao == "descoberto" and re_padrao:
            aus = regras.ausencia_no_dia(re_padrao, d, ctx.ausencias)
            if aus is not None:
                notas[a.chave] = regras.mensagem_ausencia(aus) + ": posto em branco"

    pessoas_ids = {p["funcionario_id"] for p in plantao}
    pessoas_ids |= {a.dobra_seguida_confirmada_por for a in alocs if a.dobra_seguida_confirmada_por}
    if dia is not None and dia.publicada_por:
        pessoas_ids.add(dia.publicada_por)
    pessoas = {
        f.id: f for f in db.execute(select(Funcionario).where(Funcionario.id.in_(pessoas_ids))).scalars()
    } if pessoas_ids else {}

    def item(a: Aloc) -> dict:
        posto = ctx.postos.get(a.posto_id)
        marca = av.linhas.get(a.chave, {})
        conf = pessoas.get(a.dobra_seguida_confirmada_por) if a.dobra_seguida_confirmada_por else None
        return {
            "posto_id": a.posto_id, "periodo": a.periodo, "lado": posto.lado if posto else None,
            "linhas": posto.linhas if posto else [], "ponto_final": posto.ponto_final_nome if posto else None,
            "situacao": a.situacao, "re": a.re, "nome": ctx.nomes.get(a.re) if a.re else None,
            "cadastrado": bool(a.re and a.re in ctx.nomes),
            "hora_inicio": _hhmm(a.hora_inicio), "hora_termino": _hhmm(a.hora_termino),
            "marcador": a.marcador, "outra_garagem": a.outra_garagem,
            "dobra": marca.get("dobra", False), "acumulo": marca.get("acumulo", False),
            "folga": marca.get("folga"), "motivo_folga": marca.get("motivo_folga"),
            "ausencia": marca.get("ausencia"), "no_quadro": marca.get("no_quadro", False),
            "nota": notas.get(a.chave),
            "dobra_seguida_confirmada_por": conf.nome if conf else None,
            "dobra_seguida_confirmada_em": a.dobra_seguida_confirmada_em,
        }

    # Linhas no layout da planilha: uma por ordem do modelo, 1º e 2º período.
    por_ordem: dict[int, dict] = {}
    extra = 10_000
    for a in sorted(alocs, key=lambda x: (ordem.get(x.chave, 10_000), x.periodo)):
        o = ordem.get(a.chave)
        if o is None:
            extra += 1
            o = extra
        linha = por_ordem.setdefault(o, {"ordem": o, "p1": None, "p2": None})
        chave_p = f"p{a.periodo}"
        if linha[chave_p] is not None:  # posto fora do modelo caindo na mesma ordem
            extra += 1
            linha = por_ordem.setdefault(extra, {"ordem": extra, "p1": None, "p2": None})
        linha[chave_p] = item(a)
    linhas = []
    for o in sorted(por_ordem):
        linha = por_ordem[o]
        ref = linha["p1"] or linha["p2"]
        posto = ctx.postos.get(ref["posto_id"])
        linha.update({"lado": posto.lado if posto else "TP", "cod_jb": posto.cod_jb if posto else None,
                      "lote": posto.lote if posto else None})
        linhas.append(linha)

    def pessoa(fid):
        f = pessoas.get(fid)
        return {"re": f.re, "nome": f.nome} if f else {"re": None, "nome": None}

    sab = regras.sabado_do_fim_de_semana(d)
    resumo = av.resumo
    return {
        "data": d,
        "tipo_dia": regras.tipo_do_dia(d),
        "status": dia.status if dia else "novo",
        "versao": dia.versao if dia else None,
        "publicada_em": dia.publicada_em if dia else None,
        "publicada_por": pessoa(dia.publicada_por)["nome"] if dia and dia.publicada_por else None,
        "modelo": {"id": modelo.id, "nome": modelo.nome} if modelo else None,
        "modelo_sugerido": _modelo_dict(modelo_sugerido(db, d)),
        "modelos": [_modelo_dict(m) for m in modelos_disponiveis(db)],
        "fim_de_semana": {"sabado": sab, "domingo": sab + timedelta(days=1)} if sab else None,
        "virada_mes": regras.virada_de_mes(d),
        "titulo_tp": titulo(d, "TP", modelo),
        "titulo_ts": titulo(d, "TS", modelo),
        "linhas": linhas,
        "plantao": [
            {**p, "hora_inicio": _hhmm(p["hora_inicio"]), "hora_fim": _hhmm(p["hora_fim"]),
             "passa_meia_noite": p["hora_fim"] < p["hora_inicio"], **pessoa(p["funcionario_id"])}
            for p in plantao
        ],
        "validacao": {"bloqueios": av.bloqueios, "perguntas": av.perguntas, "avisos": av.avisos},
        "resumo": {
            **resumo,
            "folgas": {str(k): [{"re": r, "nome": ctx.nomes.get(r)} for r in v] for k, v in resumo["folgas"].items()},
            "ausentes": {t: {str(p): lst for p, lst in por_p.items()} for t, por_p in resumo["ausentes"].items()},
        },
    }


def _modelo_dict(m: Optional[EscalaFiscalModelo]) -> Optional[dict]:
    return {"id": m.id, "nome": m.nome, "tipo_dia": m.tipo_dia, "paridade": m.paridade} if m else None


def todos_os_res(alocs: list[Aloc], ctx: regras.Contexto) -> set[str]:
    return ({a.re for a in alocs if a.re} | set(ctx.quadro) | {a.re for a in ctx.ausencias}
            | {t.re_a for t in ctx.trocas} | {t.re_b for t in ctx.trocas})


# ─── Gravação ─────────────────────────────────────────────────────────────────

def _foto(a, postos: dict) -> dict:
    p = postos.get(a.posto_id)
    return {
        "posto": p.rotulo if p else str(a.posto_id), "periodo": a.periodo, "situacao": a.situacao,
        "re": a.re, "marcador": a.marcador, "hora_inicio": _hhmm(a.hora_inicio),
        "hora_termino": _hhmm(a.hora_termino),
    }


def mudou(a, b) -> bool:
    return any(getattr(a, c) != getattr(b, c) for c in CAMPOS_ALOC)


def gravar_alocacoes(
    db: Session,
    dia: EscalaFiscalDia,
    novas: list[Aloc],
    *,
    usuario_id: UUID,
    confirmadas_rn04: set[tuple[UUID, int]],
    congelar: Optional[dict[tuple[UUID, int], bool]],
    postos: dict,
) -> tuple[list[dict], list[dict]]:
    """Atualiza no lugar, apaga o que saiu (flush) e só então insere.
    Devolve (antes, depois) só das linhas que mudaram. `congelar` = dobra a
    congelar nas linhas que mudaram (escala publicada)."""
    agora = datetime.now(FUSO_OPERACAO)
    existentes = {(a.posto_id, a.periodo): a for a in alocacoes_do_dia(db, dia.id)}
    novas_por_chave = {a.chave: a for a in novas}
    antes, depois = [], []

    removidas = [a for k, a in existentes.items() if k not in novas_por_chave]
    for a in removidas:
        antes.append(_foto(a, postos))
        db.delete(a)
    if removidas:
        db.flush()

    for chave, n in novas_por_chave.items():
        atual = existentes.get(chave)
        if atual is None:
            atual = EscalaFiscalAlocacao(escala_dia_id=dia.id, posto_id=n.posto_id, periodo=n.periodo,
                                         situacao=n.situacao)
            alterou = True
            db.add(atual)
        else:
            alterou = mudou(atual, n)
            if alterou:
                antes.append(_foto(atual, postos))
        if atual.re != n.re:
            atual.dobra_seguida_confirmada_por = atual.dobra_seguida_confirmada_em = None
        for campo in CAMPOS_ALOC:
            setattr(atual, campo, getattr(n, campo))
        if chave in confirmadas_rn04 and atual.dobra_seguida_confirmada_por is None:
            atual.dobra_seguida_confirmada_por, atual.dobra_seguida_confirmada_em = usuario_id, agora
        if alterou:
            atual.alterado_por, atual.alterado_em = usuario_id, agora
            depois.append(_foto(n, postos))
            if congelar is not None:
                atual.dobra_publicada = congelar.get(chave, False)
    db.flush()
    return antes, depois


def gravar_plantao(db: Session, dia: EscalaFiscalDia, plantao: list[dict]) -> tuple[list, list]:
    antigos = plantao_do_dia(db, dia.id)
    for p in db.execute(select(EscalaFiscalPlantao).where(EscalaFiscalPlantao.escala_dia_id == dia.id)).scalars():
        db.delete(p)
    db.flush()
    for p in plantao:
        db.add(EscalaFiscalPlantao(escala_dia_id=dia.id, **p))
    db.flush()

    def foto(lst):
        return [{"turno": p["turno"], "ordem": p["ordem"], "funcionario_id": str(p["funcionario_id"]),
                 "hora_inicio": _hhmm(p["hora_inicio"]), "hora_fim": _hhmm(p["hora_fim"])} for p in lst]
    a, b = foto(antigos), foto(plantao)
    return (a, b) if a != b else ([], [])


def registrar_alteracao(db: Session, dia: EscalaFiscalDia, usuario_id: UUID, antes: dict, depois: dict) -> None:
    db.add(EscalaFiscalAlteracao(
        escala_dia_id=dia.id, versao=dia.versao, alterado_por=usuario_id, antes=antes, depois=depois,
    ))
