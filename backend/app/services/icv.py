"""Cálculo de ICV, agregado por coordenador e prioridade (D20-D23, D28, D30).

Espelha as views fiscalizacao.vw_icv_linha_dia / vw_icv_coordenador_dia /
vw_prioridade_linha (migration 030) em Python — mesmo precedente já
registrado em vw_fechamento_turno / calcular_fechamento_linha
(services/fechamento_fiscal.py, migration 029): os testes rodam em
SQLite, que não tem FULL OUTER JOIN/LATERAL do mesmo jeito que o
Postgres, então a leitura usada pelos endpoints e pelos testes é esta
reimplementação Python — roda igual nos dois bancos. Se as duas
divergirem, ver o débito já registrado em FISCALIZACAO-02-onde-paramos.md
§7 ("a conta existe duas vezes").

⛔ Nada aqui grava nada — todo valor (icv, perda_absoluta, divergência) é
calculado a cada chamada, nunca cacheado nem persistido (mesma disciplina
do estado ATRASADA, D7).

Não existe entidade "bacia" — o agrupamento é fiscalizacao.linha_coordenador
(uma linha, um período, um funcionário) e não tem vigência: a composição é
sempre a de HOJE, porque o histórico do ICV é por linha (icv_apurado), não
por coordenador. Ver PROMPT-fiscalizacao-refatora-coordenador.md.
"""
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fiscalizacao import (
    EventoTurno, IcvApurado, LinhaCoordenador, Parametro, PartidaProgramada,
    RegistroPartida, Turno,
)


def _tipo_dia(d: date) -> str:
    """Mesma lógica de app/routers/fiscalizacao.py::_tipo_dia — duplicada
    aqui (não importada do router) para não criar import circular
    (o router importa deste módulo, não o contrário)."""
    dow = d.weekday()  # Monday=0 ... Sunday=6
    if dow == 5:
        return "SABADO"
    if dow == 6:
        return "DOMINGO"
    return "UTIL"


def _parametros(db: Session) -> dict[str, float]:
    """D29 — meta e aceitável, sempre lidos daqui. ⛔ Nunca hardcode 98 nem
    95 em nenhum outro lugar do código."""
    linhas = db.execute(select(Parametro.chave, Parametro.valor)).all()
    return {chave: float(valor) for chave, valor in linhas}


def _programado_grade(db: Session, linha_codigo: str, data_referencia: date, tipo_dia: str) -> Optional[int]:
    """Total de partida_programada (os dois períodos somados) para a
    vigência mais recente <= data_referencia. Usado tanto para
    "programadas_campo" (D20) quanto para o denominador da escala em
    ranking_prioridade (D28) — é o mesmo número nos dois casos."""
    vigencia = db.execute(
        select(func.max(PartidaProgramada.vigencia)).where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == tipo_dia,
            PartidaProgramada.vigencia <= data_referencia,
        )
    ).scalar_one_or_none()
    if vigencia is None:
        return None
    return db.execute(
        select(func.count()).select_from(PartidaProgramada).where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == tipo_dia,
            PartidaProgramada.vigencia == vigencia,
        )
    ).scalar_one()


def _realizadas_campo(db: Session, linha_codigo: str, data_referencia: date) -> int:
    """realizadas_programadas + extras (D6), somado dos dois períodos do
    dia — mesmo cálculo de vw_ipp_diario, mas por linha+dia sem passar
    por vw_fechamento_turno (aqui não há um turno_id só, pode haver vários
    fiscais/turnos cobrindo a mesma linha no mesmo dia)."""
    realizadas = db.execute(
        select(func.count()).select_from(RegistroPartida)
        .join(Turno, Turno.id == RegistroPartida.turno_id)
        .where(
            RegistroPartida.linha_codigo == linha_codigo,
            Turno.data_referencia == data_referencia,
            RegistroPartida.resultado == "REALIZADA",
        )
    ).scalar_one()
    extras = db.execute(
        select(func.count()).select_from(EventoTurno)
        .join(Turno, Turno.id == EventoTurno.turno_id)
        .where(
            EventoTurno.linha_codigo == linha_codigo,
            Turno.data_referencia == data_referencia,
            EventoTurno.tipo == "VIAGEM_EXTRA",
        )
    ).scalar_one()
    return realizadas + extras


def calcular_icv_linha_dia(db: Session, linha_codigo: str, data_referencia: date) -> dict:
    """Equivalente Python de fiscalizacao.vw_icv_linha_dia. NÃO MUDOU com
    a saída de bacia — já era por linha.

    D20 — as duas fontes (oficial da planilha, campo do fiscal) sempre
    lado a lado, NUNCA numa chave combinada. D23 — perda_absoluta usa a
    fonte oficial quando existe (é o número que a empresa cobra), senão a
    de campo; fonte_perda_absoluta diz qual foi usada. D30 — icv_oficial e
    icv_campo não têm teto: podem passar de 100%."""
    oficial = db.execute(
        select(IcvApurado).where(
            IcvApurado.linha_codigo == linha_codigo, IcvApurado.data_referencia == data_referencia
        )
    ).scalar_one_or_none()

    tipo_dia = _tipo_dia(data_referencia)
    programadas_campo = _programado_grade(db, linha_codigo, data_referencia, tipo_dia)
    realizadas_campo = _realizadas_campo(db, linha_codigo, data_referencia) if programadas_campo is not None else None

    programadas_oficial = realizadas_oficial = icv_oficial = None
    suspeito = False
    if oficial is not None:
        programadas_oficial = oficial.programadas
        realizadas_oficial = oficial.realizadas_tp_ts + oficial.realizadas_ts_tp
        icv_oficial = round(realizadas_oficial / programadas_oficial * 100, 2) if programadas_oficial else None
        suspeito = oficial.suspeito

    icv_campo = None
    if programadas_campo:
        icv_campo = round((realizadas_campo or 0) / programadas_campo * 100, 2)

    if programadas_oficial:
        perda_absoluta = round(programadas_oficial * (1 - (icv_oficial or 0) / 100), 2)
        fonte = "OFICIAL"
    elif programadas_campo:
        perda_absoluta = round(programadas_campo * (1 - (icv_campo or 0) / 100), 2)
        fonte = "CAMPO"
    else:
        perda_absoluta = None
        fonte = None

    return {
        "linha_codigo": linha_codigo,
        "data_referencia": data_referencia,
        "programadas_oficial": programadas_oficial,
        "realizadas_oficial": realizadas_oficial,
        "icv_oficial": icv_oficial,
        "suspeito": suspeito,
        "programadas_campo": programadas_campo,
        "realizadas_campo": realizadas_campo,
        "icv_campo": icv_campo,
        "perda_absoluta": perda_absoluta,
        "fonte_perda_absoluta": fonte,
    }


def linhas_do_coordenador(db: Session, funcionario_id, periodo: Optional[str] = None) -> list[dict]:
    """As linhas que este funcionário coordena — em TODOS os períodos por
    padrão (periodo=None), ou só num período específico. Usado por
    GET /fiscalizacao/minhas-linhas e pela agregação abaixo."""
    query = select(LinhaCoordenador.linha_codigo, LinhaCoordenador.periodo).where(
        LinhaCoordenador.funcionario_id == funcionario_id, LinhaCoordenador.ativo.is_(True)
    )
    if periodo is not None:
        query = query.where(LinhaCoordenador.periodo == periodo)
    query = query.order_by(LinhaCoordenador.periodo, LinhaCoordenador.linha_codigo)
    return [{"linha_codigo": l, "periodo": p} for l, p in db.execute(query).all()]


def calcular_icv_coordenador_dia(db: Session, funcionario_id, data_referencia: date) -> dict:
    """Equivalente Python de fiscalizacao.vw_icv_coordenador_dia — com uma
    diferença de forma deliberada: a VIEW SQL agrupa por (funcionario_id,
    periodo) separadamente (útil para relatório direto no Postgres); esta
    função combina TODOS os períodos que o funcionário coordena num único
    resumo — é o que a tela "meu ICV hoje" do coordenador precisa (D22
    continua valendo: soma numerador e denominador de tudo antes de
    dividir, nunca média).

    🔁 SEM RESOLUÇÃO POR DATA de propósito: linha_coordenador não tem
    vigência — a composição usada é sempre a de HOJE. O histórico do ICV é
    por LINHA (icv_apurado), nunca por coordenador."""
    linhas = [item["linha_codigo"] for item in linhas_do_coordenador(db, funcionario_id)]

    soma_programadas = 0
    soma_realizadas = 0
    for linha_codigo in linhas:
        item = calcular_icv_linha_dia(db, linha_codigo, data_referencia)
        if item["programadas_oficial"] is not None:
            programadas, realizadas = item["programadas_oficial"], item["realizadas_oficial"]
        else:
            programadas, realizadas = item["programadas_campo"], item["realizadas_campo"]
        if programadas:
            soma_programadas += programadas
            soma_realizadas += (realizadas or 0)

    icv_ponderado = round(soma_realizadas / soma_programadas * 100, 2) if soma_programadas else None
    parametros = _parametros(db)
    return {
        "funcionario_id": funcionario_id,
        "data_referencia": data_referencia,
        "programadas": soma_programadas,
        "realizadas": soma_realizadas,
        "icv_ponderado": icv_ponderado,
        "icv_meta": parametros.get("icv_meta"),
        "icv_aceitavel": parametros.get("icv_aceitavel"),
        "linhas": linhas,
    }


def _linhas_com_dado_no_dia(db: Session, data_referencia: date) -> list[str]:
    linhas_oficial = db.execute(
        select(IcvApurado.linha_codigo).where(IcvApurado.data_referencia == data_referencia)
    ).scalars().all()
    linhas_campo = db.execute(
        select(RegistroPartida.linha_codigo)
        .join(Turno, Turno.id == RegistroPartida.turno_id)
        .where(Turno.data_referencia == data_referencia)
    ).scalars().all()
    return sorted(set(linhas_oficial) | set(linhas_campo))


def ranking_prioridade(db: Session, data_referencia: date, linhas: Optional[list[str]] = None) -> list[dict]:
    """Equivalente Python de fiscalizacao.vw_prioridade_linha — NÃO MUDOU
    com a saída de bacia (D23): mesma ordenação, mesmo cálculo de
    divergência. Só o agrupamento que enfeitava cada item (bacia_codigo/
    bacia_nome) saiu, porque não existe mais.

    D23 — ordenado por perda_absoluta DESC, NUNCA por percentual: uma
    linha "verde" a 95,22% que programa 212 perde mais viagem absoluta que
    uma a 91,53% que programa 13. D28 — 🔴 divergencia_denominador compara
    icv_apurado.programadas contra o total da grade importada (mesma
    linha/tipo de dia), sem escolher um dos dois nem converter — só
    reporta a diferença.

    `linhas`: quando informado, restringe o ranking a essas linhas (ex.:
    as do coordenador logado); None = todas as linhas com dado no dia.
    """
    codigos = linhas if linhas is not None else _linhas_com_dado_no_dia(db, data_referencia)
    resultado = []
    for linha_codigo in codigos:
        item = calcular_icv_linha_dia(db, linha_codigo, data_referencia)

        divergencia = None
        if item["programadas_oficial"] is not None:
            escala = _programado_grade(db, linha_codigo, data_referencia, _tipo_dia(data_referencia))
            if escala is not None and escala != item["programadas_oficial"]:
                divergencia = item["programadas_oficial"] - escala
        item["divergencia_denominador"] = divergencia

        resultado.append(item)

    resultado.sort(key=lambda i: (i["perda_absoluta"] is None, -(i["perda_absoluta"] or 0)))
    return resultado


def detectar_cascata(db: Session, data_referencia: date, linha_codigo: Optional[str] = None) -> list[dict]:
    """D24 — cascata é CONDIÇÃO CALCULADA, não linha gravada: duas ou mais
    partidas PERDIDA na mesma linha, na mesma faixa horária (hora cheia —
    17, 18, 19...), no mesmo dia. Avaliada a cada chamada; some sozinha
    quando deixa de ser verdade. ⛔ Nada gravado, nenhuma tabela, nenhum
    job/agendador — o painel do coordenador já faz polling."""
    query = (
        select(RegistroPartida.linha_codigo, RegistroPartida.horario_programado)
        .join(Turno, Turno.id == RegistroPartida.turno_id)
        .where(Turno.data_referencia == data_referencia, RegistroPartida.resultado == "PERDIDA")
    )
    if linha_codigo is not None:
        query = query.where(RegistroPartida.linha_codigo == linha_codigo)

    contagem: dict[tuple[str, int], int] = defaultdict(int)
    for linha, horario in db.execute(query).all():
        contagem[(linha, horario.hour)] += 1

    return [
        {"linha_codigo": linha, "faixa_hora": hora, "quantidade": quantidade}
        for (linha, hora), quantidade in sorted(contagem.items())
        if quantidade >= 2
    ]


def motivos_livres_frequentes(db: Session, data_inicio: date, data_fim: date, limite: int = 10) -> list[dict]:
    """D27 — TRANSITO continua fora dos contadores; este agrupamento dos
    textos de motivo_outro mais frequentes é a evidência que promove (ou
    não) um motivo a contador na versão seguinte."""
    query = (
        select(RegistroPartida.motivo_outro)
        .join(Turno, Turno.id == RegistroPartida.turno_id)
        .where(
            Turno.data_referencia >= data_inicio,
            Turno.data_referencia <= data_fim,
            RegistroPartida.motivo == "OUTRO",
            RegistroPartida.motivo_outro.is_not(None),
        )
    )
    contagem: dict[str, int] = defaultdict(int)
    for (texto,) in db.execute(query).all():
        texto_normalizado = (texto or "").strip()
        if texto_normalizado:
            contagem[texto_normalizado] += 1

    itens = sorted(contagem.items(), key=lambda par: (-par[1], par[0]))[:limite]
    return [{"texto": texto, "quantidade": quantidade} for texto, quantidade in itens]


def _icv_do_dia(item: dict) -> tuple[Optional[float], Optional[str]]:
    """Um único número + fonte para um dia — oficial quando existe (é o
    número que a empresa cobra), senão campo. Mesma prioridade de
    calcular_icv_linha_dia para perda_absoluta."""
    if item["icv_oficial"] is not None:
        return item["icv_oficial"], "OFICIAL"
    if item["icv_campo"] is not None:
        return item["icv_campo"], "CAMPO"
    return None, None


def montar_placar_linha(db: Session, linha_codigo: str, data_referencia: date) -> dict:
    """§7 — dado para o placar impresso por linha: código, ICV da semana
    anterior, meta ao lado, e a evolução dos últimos 7 dias (data_referencia
    incluída). A meta é a mesma para toda a operação (fiscalizacao.parametro,
    D29) — não depende de a linha ter coordenador cadastrado ou não.

    ⛔ Nenhum dado pessoal — nada aqui identifica fiscal, coordenador ou
    dupla; é responsabilidade de quem monta a tela impressa não acrescentar
    nenhum."""
    evolucao = []
    for offset in range(6, -1, -1):
        dia = data_referencia - timedelta(days=offset)
        item = calcular_icv_linha_dia(db, linha_codigo, dia)
        icv, fonte = _icv_do_dia(item)
        evolucao.append({"data_referencia": dia, "icv": icv, "fonte": fonte})

    semana_anterior = data_referencia - timedelta(days=7)
    item_semana_anterior = calcular_icv_linha_dia(db, linha_codigo, semana_anterior)
    icv_semana_anterior, _ = _icv_do_dia(item_semana_anterior)

    meta_icv = _parametros(db).get("icv_meta")

    return {
        "linha_codigo": linha_codigo,
        "data_referencia": data_referencia,
        "meta_icv": meta_icv,
        "icv_semana_anterior": icv_semana_anterior,
        "evolucao": evolucao,
    }
