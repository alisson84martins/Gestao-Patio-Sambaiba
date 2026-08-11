"""Webhook do n8n — notifica a abertura de autorização de pré-ocorrência.

Ver PROMPT-pre-ocorrencia-motorista.md §2.5:
1. Grava no banco PRIMEIRO — esta função só é chamada depois que a
   autorização já foi commitada (ver pre_ocorrencias.py). Se o n8n estiver
   fora do ar, o motorista não perde nada.
2. Falha do webhook NUNCA faz a requisição falhar — só loga.
3. Chave de idempotência: id da autorização.
4. Payload mínimo — nunca dado de terceiro (não há terceiro nesta etapa
   mesmo, mas o princípio vale: só o que quem abriu já informou).
"""
import logging
from typing import Optional
from uuid import UUID

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 5.0


def notificar_autorizacao_aberta(
    autorizacao_id: UUID,
    link: str,
    telefone_destino: str,
    coordenador_nome: str,
    motorista_nome: Optional[str] = None,
    prefixo: Optional[str] = None,
) -> None:
    """Dispara o webhook. Chamar só depois do commit da autorização —
    nunca antes. Não lança: qualquer falha (URL não configurada, timeout,
    4xx/5xx do n8n) é logada e engolida."""
    settings = get_settings()
    if not settings.n8n_webhook_url:
        logger.info(
            "N8N_WEBHOOK_URL não configurada — notificação da autorização %s não disparada.",
            autorizacao_id,
        )
        return

    payload = {
        "idempotency_key": str(autorizacao_id),
        "link": link,
        "telefone_destino": telefone_destino,
        "coordenador_nome": coordenador_nome,
        "motorista_nome": motorista_nome,
        "prefixo": prefixo,
    }
    headers = {}
    if settings.n8n_webhook_token:
        headers["Authorization"] = f"Bearer {settings.n8n_webhook_token}"

    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
            resp = client.post(settings.n8n_webhook_url, json=payload, headers=headers)
            resp.raise_for_status()
    except Exception:
        logger.exception(
            "Falha ao notificar n8n da autorizacao %s — o registro já estava salvo, só a notificação falhou.",
            autorizacao_id,
        )
