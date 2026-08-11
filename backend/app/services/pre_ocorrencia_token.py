"""Token de uso único da pré-ocorrência — geração, hash e comparação segura.

Ver _handoff-claude/DESENHO-pre-ocorrencia.md, item 5: SHA-256 (não bcrypt)
porque o token tem 256 bits de entropia própria (secrets.token_urlsafe) —
força bruta já é inviável independente da velocidade do hash, e bcrypt só
acrescentaria latência a cada chamada dos 4 endpoints públicos sem ganho
real. `hash_password`/`verify_password` de app/core/security.py continuam
sendo só para senha (baixa entropia, onde a lentidão proposital importa).
"""
import hashlib
import secrets
import time
from collections import defaultdict

TOKEN_BYTES = 32  # secrets.token_urlsafe(32) → 256 bits de entropia
EXPIRACAO_HORAS = 2


def gerar_token() -> tuple[str, str]:
    """Gera um token novo. Retorna (token_claro, token_hash) — o claro é
    devolvido ao chamador UMA vez (resposta do POST que cria a
    autorização); só o hash é gravado no banco."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_confere(token_candidato: str, token_hash_armazenado: str) -> bool:
    """🔴 Regra 1: comparação em tempo constante. Recalcula o hash do
    candidato e compara com secrets.compare_digest — mesmo já tendo
    localizado a linha por WHERE token_hash = :hash (índice único), esta
    segunda checagem é o que a regra pede ao pé da letra, e não custa nada."""
    return secrets.compare_digest(hash_token(token_candidato), token_hash_armazenado)


# ─── Rate limit dos endpoints públicos — mesmo padrão de auth.py ─────────────
# (_LOGIN_TENTATIVAS / _TENTATIVAS_POR_CONTA), sem infra nova.

_TENTATIVAS_POR_IP: dict[str, list[float]] = defaultdict(list)
_TENTATIVAS_POR_TOKEN: dict[str, list[float]] = defaultdict(list)
_IP_MAX = 30
_IP_JANELA = 60
_TOKEN_MAX = 60
_TOKEN_JANELA = 60


class RateLimitExcedido(Exception):
    pass


def checar_rate_limit_publico(ip: str | None, token_hash: str) -> None:
    """Levanta RateLimitExcedido se o IP ou o token (pelo hash — nunca o
    claro fica em memória além do necessário para calcular o hash)
    estourou o limite. Dois limites porque um token comprometido sendo
    martelado é um problema diferente de alguém varrendo tokens por IP."""
    agora = time.time()
    chave_ip = ip or "unknown"

    tentativas_ip = [t for t in _TENTATIVAS_POR_IP[chave_ip] if agora - t < _IP_JANELA]
    if len(tentativas_ip) >= _IP_MAX:
        _TENTATIVAS_POR_IP[chave_ip] = tentativas_ip
        raise RateLimitExcedido()
    tentativas_ip.append(agora)
    _TENTATIVAS_POR_IP[chave_ip] = tentativas_ip

    tentativas_token = [t for t in _TENTATIVAS_POR_TOKEN[token_hash] if agora - t < _TOKEN_JANELA]
    if len(tentativas_token) >= _TOKEN_MAX:
        _TENTATIVAS_POR_TOKEN[token_hash] = tentativas_token
        raise RateLimitExcedido()
    tentativas_token.append(agora)
    _TENTATIVAS_POR_TOKEN[token_hash] = tentativas_token
