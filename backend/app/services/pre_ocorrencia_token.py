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
    """Levanta RateLimitExcedido se o IP ou o token estourou o limite.

    Os dois contadores medem coisas diferentes de propósito:
    - por TOKEN: toda tentativa conta, mesmo as que resolvem — cobre um
      token válido sendo martelado. Autosave legítimo bate nisso sem
      culpa nenhuma (por isso o limite é bem mais alto, _TOKEN_MAX).
    - por IP: só conta aqui a checagem de limite já atingido — quem
      INCREMENTA esse contador é registrar_falha_rate_limit_ip(), chamado
      só quando o token NÃO resolveu uma autorização válida (ver
      _carregar_autorizacao() em pre_ocorrencias_publico.py). Um motorista
      legítimo, com token válido, nunca consome essa cota — ela existe
      pra pegar quem varre tokens ao acaso, não quem digita um relato.
    """
    agora = time.time()
    chave_ip = ip or "unknown"

    tentativas_ip = [t for t in _TENTATIVAS_POR_IP[chave_ip] if agora - t < _IP_JANELA]
    _TENTATIVAS_POR_IP[chave_ip] = tentativas_ip
    if len(tentativas_ip) >= _IP_MAX:
        raise RateLimitExcedido()

    tentativas_token = [t for t in _TENTATIVAS_POR_TOKEN[token_hash] if agora - t < _TOKEN_JANELA]
    if len(tentativas_token) >= _TOKEN_MAX:
        _TENTATIVAS_POR_TOKEN[token_hash] = tentativas_token
        raise RateLimitExcedido()
    tentativas_token.append(agora)
    _TENTATIVAS_POR_TOKEN[token_hash] = tentativas_token


def registrar_falha_rate_limit_ip(ip: str | None) -> None:
    """Conta uma tentativa que NÃO resolveu (token inexistente, expirado,
    usado ou com hash divergente) pro limite por IP — a cota de varredura.
    Chamado só nos caminhos de falha de _carregar_autorizacao(), nunca
    quando o token resolve com sucesso."""
    agora = time.time()
    chave_ip = ip or "unknown"
    tentativas_ip = [t for t in _TENTATIVAS_POR_IP[chave_ip] if agora - t < _IP_JANELA]
    tentativas_ip.append(agora)
    _TENTATIVAS_POR_IP[chave_ip] = tentativas_ip
