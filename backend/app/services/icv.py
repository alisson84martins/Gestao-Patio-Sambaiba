"""Cálculo de ICV, bacia ponderada e prioridade (D20-D23, D28, D30).

Espelha as views fiscalizacao.vw_icv_linha_dia / vw_icv_bacia_dia /
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
"""
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fiscalizacao import (
    Bacia, BaciaLinha, EventoTurno, IcvApurado, PartidaProgramada, RegistroPartida, Turno,
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
    """Equivalente Python de fiscalizacao.vw_icv_linha_dia.

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


def _linhas_da_bacia(db: Session, bacia_codigo: str, data_referencia: date) -> list[str]:
    """D21 — resolve a composição da bacia PELA DATA DO DADO, nunca pela
    composição de hoje."""
    return list(db.execute(
        select(BaciaLinha.linha_codigo).where(
            BaciaLinha.bacia_codigo == bacia_codigo,
            BaciaLinha.ativo.is_(True),
            BaciaLinha.vigencia_inicio <= data_referencia,
            (BaciaLinha.vigencia_fim.is_(None)) | (BaciaLinha.vigencia_fim >= data_referencia),
        )
    ).scalars().all())


def _bacia_da_linha(db: Session, linha_codigo: str, data_referencia: date) -> Optional[BaciaLinha]:
    return db.execute(
        select(BaciaLinha).where(
            BaciaLinha.linha_codigo == linha_codigo,
            BaciaLinha.ativo.is_(True),
            BaciaLinha.vigencia_inicio <= data_referencia,
            (BaciaLinha.vigencia_fim.is_(None)) | (BaciaLinha.vigencia_fim >= data_referencia),
        )
    ).scalars().first()


def calcular_icv_bacia_dia(db: Session, bacia_codigo: str, data_referencia: date) -> Optional[dict]:
    """Equivalente Python de fiscalizacao.vw_icv_bacia_dia.

    🔴 D22 — soma numerador e denominador de TODAS as linhas da bacia
    (composição vigente na data, D21) antes de dividir. ⛔ Nunca faça
    média de percentuais aqui — é exatamente o erro que este módulo existe
    para não repetir."""
    bacia = db.get(Bacia, bacia_codigo)
    if bacia is None:
        return None

    soma_programadas = 0
    soma_realizadas = 0
    for linha_codigo in _linhas_da_bacia(db, bacia_codigo, data_referencia):
        item = calcular_icv_linha_dia(db, linha_codigo, data_referencia)
        if item["programadas_oficial"] is not None:
            programadas, realizadas = item["programadas_oficial"], item["realizadas_oficial"]
        else:
            programadas, realizadas = item["programadas_campo"], item["realizadas_campo"]
        if programadas:
            soma_programadas += programadas
            soma_realizadas += (realizadas or 0)

    icv_ponderado = round(soma_realizadas / soma_programadas * 100, 2) if soma_programadas else None
    return {
        "bacia_codigo": bacia_codigo,
        "bacia_nome": bacia.nome,
        "meta_icv": float(bacia.meta_icv),
        "data_referencia": data_referencia,
        "programadas": soma_programadas,
        "realizadas": soma_realizadas,
        "icv_ponderado": icv_ponderado,
    }


def _linhas_com_dado_no_dia(db: Session, data_referencia: date, bacia_codigo: Optional[str]) -> list[str]:
    if bacia_codigo is not None:
        return _linhas_da_bacia(db, bacia_codigo, data_referencia)
    linhas_oficial = db.execute(
        select(IcvApurado.linha_codigo).where(IcvApurado.data_referencia == data_referencia)
    ).scalars().all()
    linhas_campo = db.execute(
        select(RegistroPartida.linha_codigo)
        .join(Turno, Turno.id == RegistroPartida.turno_id)
        .where(Turno.data_referencia == data_referencia)
    ).scalars().all()
    return sorted(set(linhas_oficial) | set(linhas_campo))


def ranking_prioridade(db: Session, data_referencia: date, bacia_codigo: Optional[str] = None) -> list[dict]:
    """Equivalente Python de fiscalizacao.vw_prioridade_linha.

    D23 — ordenado por perda_absoluta DESC, NUNCA por percentual: uma
    linha "verde" a 95,22% que programa 212 perde mais viagem absoluta que
    uma a 91,53% que programa 13. D28 — 🔴 divergencia_denominador compara
    icv_apurado.programadas contra o total da grade importada (mesma
    linha/tipo de dia), sem escolher um dos dois nem converter — só
    reporta a diferença."""
    resultado = []
    for linha_codigo in _linhas_com_dado_no_dia(db, data_referencia, bacia_codigo):
        item = calcular_icv_linha_dia(db, linha_codigo, data_referencia)

        divergencia = None
        if item["programadas_oficial"] is not None:
            escala = _programado_grade(db, linha_codigo, data_referencia, _tipo_dia(data_referencia))
            if escala is not None and escala != item["programadas_oficial"]:
                divergencia = item["programadas_oficial"] - escala
        item["divergencia_denominador"] = divergencia

        vinculo = _bacia_da_linha(db, linha_codigo, data_referencia)
        item["bacia_codigo"] = vinculo.bacia_codigo if vinculo is not None else None
        bacia_obj = db.get(Bacia, vinculo.bacia_codigo) if vinculo is not None else None
        item["bacia_nome"] = bacia_obj.nome if bacia_obj is not None else None

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
