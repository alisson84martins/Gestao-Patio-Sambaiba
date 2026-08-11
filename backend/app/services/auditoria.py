"""Trilha de auditoria mínima (migration 021, Bloco B item 7 do fechamento).

Ver database/migrations/021-log-acesso.sql para o porquê, o volume
esperado e o escopo deliberadamente mínimo (não cobre 403 de autoria em
ocorrência nem os 12 routers legados — ver o cabeçalho da migration).
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.auditoria import LogAcesso

logger = logging.getLogger(__name__)


def registrar_log_acesso(
    db: Session,
    evento: str,
    *,
    funcionario_id: Optional[UUID] = None,
    re_tentativa: Optional[str] = None,
    recurso: Optional[str] = None,
    ocorrencia_id: Optional[UUID] = None,
    ip: Optional[str] = None,
) -> None:
    """Grava uma linha em log_acesso e commita imediatamente — não espera
    o commit de fim de request, porque nos dois eventos mais importantes
    (LOGIN_FALHA, NEGADO_403) a requisição termina em erro logo em
    seguida, e `get_db()` não faz rollback automático no teardown (só
    `close()`), mas também não garante commit nenhum por conta própria.

    Nunca lança pra quem chama — auditoria não pode derrubar a ação que
    está tentando auditar. Se a gravação falhar, o login/leitura/negativa
    segue valendo, só não fica registrado; o problema vai pro log do
    servidor, não pro cliente.

    ⚠️ NUNCA passe senha, CPF, RG, endereço ou qualquer outro dado pessoal
    de terceiro (vítima/testemunha) aqui — só `ocorrencia_id`. O RE de
    quem está se autenticando não conta como dado de terceiro (é a
    própria pessoa se identificando, não uma vítima).
    """
    try:
        db.add(LogAcesso(
            evento=evento,
            funcionario_id=funcionario_id,
            re_tentativa=re_tentativa,
            recurso=recurso,
            ocorrencia_id=ocorrencia_id,
            ip=ip,
        ))
        db.commit()
    except Exception:
        # O próprio rollback pode falhar (sessão fake em teste, conexão já
        # morta) — nunca deixa escapar, mesmo assim.
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Falha ao gravar log_acesso (evento=%s)", evento)


def ip_do_request(request) -> Optional[str]:
    """Mesmo padrão de extração de IP já usado em auth.py
    (_checar_rate_limit) — client.host, ou None se o teste/ambiente não
    tiver um client de verdade."""
    return request.client.host if request is not None and request.client else None
