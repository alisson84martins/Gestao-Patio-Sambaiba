"""Cadastro das linhas monitoradas no banco (migration 054) e data de serviço.

- `linhas_ativas`: monitoramento.linha_monitorada (ativo) → {codigo: nimer_line_id}.
- `mapa_sim`: monitoramento.sim_codigo_linha → {li: (linha, sentido)}.
- `prefixos_escalados`: carros (4 dígitos) das tabelas do NIMER do dia nas
  linhas monitoradas — o robô também manda esses carros quando aparecem em
  OUTRA linha no SIM (alerta "carro trocado de linha").
- `data_servico_de`: a data de serviço de um instante. Depois da meia-noite
  o serviço ainda é o dia anterior até a virada das 4h (mesma
  _VIRADA_MADRUGADA de app/routers/fiscalizacao.py — a operação começa
  ~4h10 e só cruza a meia-noite no fim do 2º período). ⛔ Nunca
  `toISOString().slice(0,10)` / date.today() (armadilha do fuso).

Sem cache: são consultas pequenas, uma por envio do robô (~1/min).
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.monitoramento import LinhaMonitoradaCadastro, NimerViagem, SimCodigoLinha

VIRADA_SERVICO = time(4, 0)


def data_servico_de(instante: Optional[datetime] = None) -> date:
    """Data de serviço (SP) do instante: antes das 4h ainda é o dia anterior."""
    instante = instante or datetime.now(timezone.utc)
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    local = instante.astimezone(FUSO_OPERACAO)
    if local.time() < VIRADA_SERVICO:
        return local.date() - timedelta(days=1)
    return local.date()


def linhas_ativas(db: Session) -> dict[str, Optional[int]]:
    linhas = db.execute(
        select(LinhaMonitoradaCadastro.linha_codigo, LinhaMonitoradaCadastro.nimer_line_id)
        .where(LinhaMonitoradaCadastro.ativo.is_(True))
        .order_by(LinhaMonitoradaCadastro.linha_codigo)
    ).all()
    return {codigo: nimer for codigo, nimer in linhas}


def mapa_sim(db: Session) -> dict[int, tuple[str, Optional[int]]]:
    linhas = db.execute(select(SimCodigoLinha.sim_codigo, SimCodigoLinha.linha_codigo, SimCodigoLinha.sentido)).all()
    return {li: (linha, sentido) for li, linha, sentido in linhas}


def prefixos_escalados(db: Session, data_servico: date, linhas: Optional[list[str]] = None) -> set[str]:
    query = select(NimerViagem.carro).where(
        NimerViagem.data_servico == data_servico,
        NimerViagem.carro.is_not(None),
        NimerViagem.removida_em.is_(None),
    ).distinct()
    if linhas is not None:
        query = query.where(NimerViagem.linha_codigo.in_(linhas))
    return {c for c in db.execute(query).scalars().all() if c}
