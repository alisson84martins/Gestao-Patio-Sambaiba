"""Recebimento do Relatório de Linha do NIMER (POST /monitoramento/nimer).

O robô (content script no sistema.nimer.com.br) chama
`POST /LineReport/GetReportData` a cada 3 min por linha monitorada e repassa
o `timeline` SEM o campo `html`. Aqui cada viagem vira uma linha de
monitoramento.nimer_viagem (upsert pela chave natural data_servico +
nimer_departure_id) e o envio vira uma linha de nimer_coleta (batimento).

Formato do timeline (mapeado em 10/11/2026):
  plannedTrips, realizedTrips, lostTrips, missingTrips, onTimePercent,
  tables[] → {tableId, name (tabela "032104"), vehicle, driver (RE ou
  "Sem Motorista"), charger, trips[]}
  trips[] → {idx, tpToTs, status: done|lost|missing|future, progStartMs,
  progEndMs, realStartMs, realEndMs, startSource/endSource: GPS|Fiscal,
  delayStart, delayEnd, lostReason, vehicle, departureId, startTripInputId}

⛔ Só leitura do NIMER: nada aqui escreve de volta. Campo que não vier ou
vier quebrado fica NULL — nunca chuta.
"""
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.monitoramento import NimerColeta, NimerViagem
from app.services.monitoramento_linhas import prefixo_patio

STATUS_VIAGEM = {"done", "lost", "missing", "future"}
LIMITE_VIAGENS = 3000


def _texto(valor: Any, n: int) -> Optional[str]:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:n] or None


def _instante(ms: Any) -> Optional[datetime]:
    if ms is None or isinstance(ms, bool):
        return None
    try:
        ms = float(ms)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _inteiro(valor: Any) -> Optional[int]:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(round(float(valor)))
    except (TypeError, ValueError, OverflowError):
        return None


def _smallint(valor: Optional[int]) -> Optional[int]:
    return None if valor is None else max(-32768, min(valor, 32767))


def normalizar_carro(valor: Any) -> Optional[str]:
    """NIMER usa o prefixo do pátio (4 dígitos); se vier o do SIM (5), corta."""
    texto = _texto(valor, 20)
    if not texto:
        return None
    digitos = "".join(c for c in texto if c.isdigit())
    if not digitos:
        return None
    return prefixo_patio(digitos)[:10]


def _pessoa(valor: Any) -> tuple[Optional[str], bool]:
    """(RE, sem_pessoa). "Sem Motorista"/"Sem Cobrador" → (None, True)."""
    texto = _texto(valor, 40)
    if not texto:
        return None, False
    if texto.lower().startswith("sem "):
        return None, True
    return texto[:20], False


def _sentido(tp_to_ts: Any) -> Optional[int]:
    """1 = TP→TS (no SIM da 1726-10, 1403 = sentido 1 = destino TS); 2 = TS→TP."""
    if tp_to_ts is True:
        return 1
    if tp_to_ts is False:
        return 2
    return None


def parse_data_servico(valor: Any) -> Optional[date]:
    texto = _texto(valor, 20)
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def viagens_do_timeline(timeline: dict, linha_codigo: str, data_servico: date) -> list[dict]:
    """timeline do NIMER → linhas de nimer_viagem (sem id)."""
    linhas: list[dict] = []
    vistos: set[str] = set()
    for tabela in timeline.get("tables") or []:
        if not isinstance(tabela, dict):
            continue
        nome_tabela = _texto(tabela.get("name"), 12)
        table_id = _texto(tabela.get("tableId"), 40)
        motorista, sem_motorista = _pessoa(tabela.get("driver"))
        cobrador, _ = _pessoa(tabela.get("charger"))
        carro_tabela = normalizar_carro(tabela.get("vehicle"))
        for viagem in tabela.get("trips") or []:
            if not isinstance(viagem, dict):
                continue
            idx = _inteiro(viagem.get("idx"))
            departure = _texto(viagem.get("departureId"), 40)
            if not departure:
                if table_id is None or idx is None:
                    continue
                departure = f"t{table_id}-{idx}"[:40]
            if departure in vistos:
                continue
            vistos.add(departure)

            status = (_texto(viagem.get("status"), 10) or "").lower()
            prog_inicio = _instante(viagem.get("progStartMs"))
            real_inicio = _instante(viagem.get("realStartMs"))
            if prog_inicio and real_inicio:
                atraso = round((real_inicio - prog_inicio).total_seconds() / 60)
            else:
                atraso = _inteiro(viagem.get("delayStart"))
            linhas.append({
                "data_servico": data_servico,
                "linha_codigo": linha_codigo,
                "tabela": nome_tabela,
                "nimer_table_id": table_id,
                "nimer_departure_id": departure,
                "idx": _smallint(idx),
                "sentido": _sentido(viagem.get("tpToTs")),
                "status": status if status in STATUS_VIAGEM else None,
                "prog_inicio": prog_inicio,
                "prog_fim": _instante(viagem.get("progEndMs")),
                "real_inicio": real_inicio,
                "real_fim": _instante(viagem.get("realEndMs")),
                "fonte_inicio": _texto(viagem.get("startSource"), 10),
                "fonte_fim": _texto(viagem.get("endSource"), 10),
                "atraso_inicio_min": _smallint(atraso),
                "motivo_perda_texto": _texto(viagem.get("lostReason"), 120),
                "carro": normalizar_carro(viagem.get("vehicle")) or carro_tabela,
                "motorista_re": motorista,
                "cobrador_re": cobrador,
                "sem_motorista": sem_motorista,
            })
    return linhas


def _upsert(db: Session, linhas: list[dict], agora: datetime) -> None:
    if not linhas:
        return
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # SQLite dos testes
        from sqlalchemy.dialects.sqlite import insert
    for inicio in range(0, len(linhas), 500):
        lote = [dict(linha, removida_em=None, atualizado_em=agora) for linha in linhas[inicio:inicio + 500]]
        stmt = insert(NimerViagem).values(lote)
        colunas = {c: stmt.excluded[c] for c in lote[0] if c not in ("data_servico", "nimer_departure_id")}
        db.execute(stmt.on_conflict_do_update(index_elements=["data_servico", "nimer_departure_id"], set_=colunas))


def gravar_coleta_nimer(
    db: Session,
    *,
    linha_codigo: str,
    data_servico: date,
    status: str,
    timeline: Optional[dict],
    coletado_em: Optional[datetime],
    mensagem: Optional[str],
    agora: datetime,
) -> dict:
    viagens: list[dict] = []
    if status == "ok" and isinstance(timeline, dict):
        viagens = viagens_do_timeline(timeline, linha_codigo, data_servico)
        _upsert(db, viagens, agora)
        if viagens:
            # Viagem que deixou de vir (tabela retirada, ou a futura que ganhou
            # departureId de verdade) sai das contas — sem apagar: pode ter
            # correção do fiscal pendurada.
            recebidas = [v["nimer_departure_id"] for v in viagens]
            db.execute(
                update(NimerViagem)
                .where(
                    NimerViagem.linha_codigo == linha_codigo,
                    NimerViagem.data_servico == data_servico,
                    NimerViagem.removida_em.is_(None),
                    NimerViagem.nimer_departure_id.not_in(recebidas),
                )
                .values(removida_em=agora)
            )

    tl = timeline if isinstance(timeline, dict) else {}
    pontualidade = tl.get("onTimePercent")
    try:
        pontualidade = None if pontualidade is None else round(float(pontualidade), 2)
    except (TypeError, ValueError):
        pontualidade = None
    db.add(NimerColeta(
        recebido_em=agora,
        coletado_em=coletado_em,
        linha_codigo=linha_codigo,
        data_servico=data_servico,
        status=status,
        planejadas=_inteiro(tl.get("plannedTrips")),
        realizadas=_inteiro(tl.get("realizedTrips")),
        perdidas=_inteiro(tl.get("lostTrips")),
        faltantes=_inteiro(tl.get("missingTrips")),
        pontualidade_pct=pontualidade,
        qtd_viagens=len(viagens),
        mensagem=_texto(mensagem, 200),
    ))
    return {"linha_codigo": linha_codigo, "data_servico": data_servico.isoformat(), "status": status,
            "viagens": len(viagens)}


def viagens_da_linha(db: Session, linha_codigo: str, data_servico: date) -> list[NimerViagem]:
    return list(db.execute(
        select(NimerViagem)
        .where(NimerViagem.linha_codigo == linha_codigo, NimerViagem.data_servico == data_servico,
               NimerViagem.removida_em.is_(None))
        .order_by(NimerViagem.prog_inicio, NimerViagem.tabela)
    ).scalars().all())
