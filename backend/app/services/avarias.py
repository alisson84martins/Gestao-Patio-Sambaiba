"""Regras compartilhadas da avaria de saída (Bloco G, migration 042) —
routers/portaria_avarias.py é o único chamador; vive em módulo próprio
porque a retenção também precisa ser recalculada no backfill de dados
manuais e em testes, sem duplicar o número.

🔴 ENQUADRAMENTO PROBATÓRIO — ver o cabeçalho da migration 042 e de
app/models/portaria.py::AvariaSaida: a retenção não é capricho, é o prazo
mínimo pra um motorista conseguir se defender quando é chamado meses
depois de uma constatação.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

# ⚠️ Constante nomeada (pedido explícito do prompt de origem) — o Alisson
# ainda vai confirmar o prazo com RH/jurídico, e trocar o número não pode
# custar migration nem busca no código.
RETENCAO_AVARIA_DIAS = 365


def _como_utc(momento: datetime) -> datetime:
    """SQLite (só em teste — Postgres usa TIMESTAMPTZ de verdade) devolve
    datetime NAIVE depois do round-trip pelo banco; todo datetime desta
    tabela nasce de agora_utc(), então naive aqui É UTC. Mesmo padrão de
    routers/portaria.py (linha ~640, leitura_placa/P13)."""
    return momento if momento.tzinfo is not None else momento.replace(tzinfo=timezone.utc)


def calcular_expira_em(
    ultima_vez_em: datetime, encerrada_em: Optional[datetime] = None
) -> datetime:
    """expira_em = GREATEST(ultima_vez_em, COALESCE(encerrada_em,
    ultima_vez_em)) + RETENCAO_AVARIA_DIAS — mesma fórmula da migration 042
    (seção 6), recalculada a cada constatação e no encerramento."""
    base = _como_utc(ultima_vez_em)
    if encerrada_em is not None and _como_utc(encerrada_em) > base:
        base = _como_utc(encerrada_em)
    return base + timedelta(days=RETENCAO_AVARIA_DIAS)


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)
