"""Gerador do texto de fechamento para o grupo do sinistro no WhatsApp.

Mesmo molde de app/services/mensagem_sinistro.py: monta o texto NA HORA a
partir do que está gravado — o texto não se grava em lugar nenhum. Segue o
"Formato exato" da seção 6 de _handoff-claude/PROMPT-fiscalizacao-blocos-A-B-C.md
ao pé da letra, inclusive os espaços e a ausência de dois-pontos em algumas
linhas (ex.: "ATRASO DE GARAGEM 00", sem ":").

⛔ Nenhum `None`, nenhum `null`, nenhum rótulo órfão pode sair deste gerador
— mesma dívida que mensagem_sinistro.py já pagou uma vez (_linha_contato_vitima).
Campo obrigatório que faltar sai como "—".
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fiscalizacao import (
    Baita, EventoTurno, ObservacaoTurno, PartidaProgramada, RegistroPartida, Turno, TurnoLinha,
)

_TIPOS_EVENTO = (
    "FALTA_OPERADORES", "RA", "SOS", "ATRASO_GARAGEM", "TROCA_OPERACIONAL", "VIAGEM_EXTRA"
)


def _contar_programadas(db: Session, linha_codigo: str, turno: Turno) -> int:
    if turno.tipo_dia is None:
        return 0
    vigencia = db.execute(
        select(func.max(PartidaProgramada.vigencia)).where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == turno.tipo_dia,
            PartidaProgramada.vigencia <= turno.data_referencia,
        )
    ).scalar_one_or_none()
    if vigencia is None:
        return 0
    return db.execute(
        select(func.count()).select_from(PartidaProgramada).where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == turno.tipo_dia,
            PartidaProgramada.vigencia == vigencia,
            PartidaProgramada.periodo == turno.periodo,
        )
    ).scalar_one()


def _contar_realizadas_programadas(db: Session, turno_id: UUID, linha_codigo: str) -> int:
    return db.execute(
        select(func.count()).select_from(RegistroPartida).where(
            RegistroPartida.turno_id == turno_id,
            RegistroPartida.linha_codigo == linha_codigo,
            RegistroPartida.resultado == "REALIZADA",
        )
    ).scalar_one()


def _contar_eventos(db: Session, turno_id: UUID, linha_codigo: str) -> dict[str, int]:
    contagem = {tipo: 0 for tipo in _TIPOS_EVENTO}
    linhas = db.execute(
        select(EventoTurno.tipo, func.count())
        .where(EventoTurno.turno_id == turno_id, EventoTurno.linha_codigo == linha_codigo)
        .group_by(EventoTurno.tipo)
    ).all()
    for tipo, total in linhas:
        contagem[tipo] = total
    return contagem


def _bloco_baita(registro: Optional[Baita]) -> list[str]:
    """Regra 8 do gerador: campo obrigatório que faltar sai como "—" —
    nunca em branco, nunca None. Vale tanto quando não há Baita nenhuma
    cadastrada para esta linha/tipo quanto para os campos internos vazios."""
    prefixo = registro.prefixo if registro is not None else None
    motorista = registro.motorista_re if registro is not None else None
    cobrador = registro.cobrador_re if registro is not None else None

    saida_tp_texto = "—"
    saida_ts_texto = "—"
    if registro is not None:
        if registro.saida_tp is not None:
            saida_tp_texto = registro.saida_tp.strftime("%H:%M")
        if registro.ts_circular:
            saida_ts_texto = "circular"
        elif registro.saida_ts is not None:
            saida_ts_texto = registro.saida_ts.strftime("%H:%M")

    return [
        f"CARRO: {prefixo or '—'}",
        f"MOT: {motorista or '—'}",
        f"COB: {cobrador or '—'}",
        f"SAÍDA DO TP {saida_tp_texto}",
        f"SAÍDA DO TS {saida_ts_texto}",
    ]


def calcular_fechamento_linha(db: Session, turno: Turno, linha_codigo: str) -> dict:
    """Equivalente em Python de fiscalizacao.vw_fechamento_turno (uma linha
    da view) — reimplementado aqui porque o gerador roda igual em SQLite
    (testes) e Postgres (produção), mesmo motivo de portaria.py não
    depender de vw_dentro. D6: expõe programadas/realizadas_programadas/
    extras SEPARADOS, além do realizadas_total somado e perdidas."""
    programadas = _contar_programadas(db, linha_codigo, turno)
    realizadas_programadas = _contar_realizadas_programadas(db, turno.id, linha_codigo)
    contadores = _contar_eventos(db, turno.id, linha_codigo)
    extras = contadores["VIAGEM_EXTRA"]
    realizadas_total = realizadas_programadas + extras
    return {
        "programadas": programadas,
        "realizadas_programadas": realizadas_programadas,
        "extras": extras,
        "realizadas_total": realizadas_total,
        "perdidas": programadas - realizadas_programadas,
        "contadores": contadores,
    }


def _montar_linha(
    db: Session,
    turno: Turno,
    linha_codigo: str,
    baitas: dict[tuple[str, str], Baita],
    observacoes: list[ObservacaoTurno],
    perdas_outro: list[RegistroPartida],
) -> str:
    agregado = calcular_fechamento_linha(db, turno, linha_codigo)
    programadas = agregado["programadas"]
    realizadas_total = agregado["realizadas_total"]
    contagem = agregado["contadores"]

    linhas_texto = [
        "Sambaiba G3",
        f"SEGUE ABAIXO FECHAMENTO... {turno.data_referencia.strftime('%d/%m/%Y')}",
        f"LINHA: {linha_codigo}",
        f"PARTIDAS PROGRAMADAS: {turno.periodo}° período {programadas}",
        f"PARTIDAS REALIZADAS: {turno.periodo}° período {realizadas_total}",
        f" FALTA DE OPERADORES: {contagem['FALTA_OPERADORES']:02d}",
        f" R.A: {contagem['RA']:02d}",
        f" S.O.S: {contagem['SOS']:02d}",
        f" ATRASO DE GARAGEM {contagem['ATRASO_GARAGEM']:02d}",
        f" TROCA OPERACIONAL {contagem['TROCA_OPERACIONAL']:02d}",
        f" VIAGEM EXTRA {contagem['VIAGEM_EXTRA']:02d}",
        "OBS:",
    ]

    # Regra 3 — a primeira linha do OBS é sempre a refeição do fiscal (D15),
    # quando informada.
    if turno.refeicao_inicio is not None and turno.refeicao_fim is not None:
        linhas_texto.append(
            f" 👉🏼 FISCAL {turno.fiscal_re}, refeição das {turno.refeicao_inicio.strftime('%H:%M')}"
            f" às {turno.refeicao_fim.strftime('%H:%M')}."
        )

    # Regra 4 — observações livres, na ordem em que foram criadas. Uma
    # observação sem linha_codigo (turno inteiro) aparece em toda linha;
    # uma observação de OUTRA linha específica não aparece aqui.
    for obs in observacoes:
        if obs.linha_codigo is not None and obs.linha_codigo != linha_codigo:
            continue
        linhas_texto.append(f" 👉🏼 {obs.texto}")

    # Regra 5 — uma linha automática por perda com motivo OUTRO (D5: é
    # assim que "sem condobus" aparece no fechamento sem virar contador).
    for registro in perdas_outro:
        if registro.linha_codigo != linha_codigo:
            continue
        linhas_texto.append(
            f" 👉🏼 Tabela {registro.numero_tabela:02d}, {registro.motivo_outro}, "
            f"viagem das {registro.horario_programado.strftime('%H:%M')} não realizada."
        )

    linhas_texto.append("ANTI-BAITA")
    linhas_texto.extend(_bloco_baita(baitas.get((linha_codigo, "ANTI_BAITA"))))
    linhas_texto.append("BAITA")
    linhas_texto.extend(_bloco_baita(baitas.get((linha_codigo, "BAITA"))))

    # Regra 6 — bloco PASTAS TP/TS só sai se pastas_prefixo estiver
    # preenchido (D14 — é opcional).
    if turno.pastas_prefixo:
        linhas_texto.append("PASTAS TP/TS")
        linhas_texto.append(f"CARRO: {turno.pastas_prefixo}")

    linhas_texto.append(f"FISCAL RE... {turno.fiscal_re}")

    return "\n".join(linhas_texto)


def montar_fechamento(db: Session, turno_id: UUID) -> list[str]:
    """Uma string por linha do turno (o fiscal 13733 manda duas linhas numa
    mensagem só, mas cada uma sai pronta para ser colada separadamente)."""
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise ValueError(f"Turno {turno_id} não encontrado")

    linhas = list(
        db.execute(
            select(TurnoLinha.linha_codigo).where(TurnoLinha.turno_id == turno_id).order_by(TurnoLinha.linha_codigo)
        ).scalars().all()
    )

    baitas = {
        (b.linha_codigo, b.tipo): b
        for b in db.execute(select(Baita).where(Baita.turno_id == turno_id)).scalars().all()
    }
    observacoes = list(
        db.execute(
            select(ObservacaoTurno).where(ObservacaoTurno.turno_id == turno_id).order_by(ObservacaoTurno.criado_em)
        ).scalars().all()
    )
    perdas_outro = list(
        db.execute(
            select(RegistroPartida)
            .where(
                RegistroPartida.turno_id == turno_id,
                RegistroPartida.resultado == "PERDIDA",
                RegistroPartida.motivo == "OUTRO",
            )
            .order_by(RegistroPartida.horario_programado)
        ).scalars().all()
    )

    return [
        _montar_linha(db, turno, linha_codigo, baitas, observacoes, perdas_outro)
        for linha_codigo in linhas
    ]
