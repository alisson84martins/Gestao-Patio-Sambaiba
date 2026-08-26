"""Endpoint de health check — confirma API, banco e modelos."""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_funcionario
from app.models import (
    Alerta, AlocacaoPatio, Escala, FichaManutencao, Fila,
    ImportacaoEscala, Linha, Motorista, Onibus, Permissao,
    TipoDefeito, Usuario,
)
from app.models.cadastro import Funcionario

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sistema"])


@router.get("/health", summary="Verifica se a API está no ar")
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """SEV-09/SEV-10: endpoint público, sem autenticação — é o que a tela
    de login consulta antes de qualquer credencial existir, então precisa
    continuar aberto. Mas "aberto" não é "detalhado": até 11/08/2026 ele
    devolvia `environment` e a contagem de todas as 12 tabelas pra
    qualquer um na internet, sem token nenhum. Confirma só que o processo
    está de pé e o banco responde — nada que ajude quem está mapeando o
    sistema por fora. Detalhe de verdade (contagens, ambiente, versão)
    mudou para /health/detalhado, atrás de autenticação.
    """
    try:
        db.execute(select(1))
        return {"status": "ok"}
    except Exception:
        # Nunca devolve str(exc) ao cliente — o detalhe do erro de banco
        # (nome de host, porta, credencial no traceback) fica só no log
        # do servidor, não na resposta HTTP.
        logger.exception("Falha ao verificar conexão com o banco em /health")
        return {"status": "degraded"}


@router.get("/health/detalhado", summary="Diagnóstico completo — autenticado")
def health_detalhado(
    db: Session = Depends(get_db),
    _: Funcionario = Depends(get_current_funcionario),
) -> dict[str, Any]:
    """Versão completa do health check — contagem por tabela, ambiente e
    versão. Exige login (qualquer conta válida, não é RBAC de recurso —
    isto é diagnóstico, não dado de negócio). Antes desta sessão essas
    informações saíam por /health sem token nenhum (SEV-09/SEV-10)."""
    settings = get_settings()
    response: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "unknown",
    }
    try:
        db.execute(select(1))
        response["database"] = "connected"

        modelos = {
            "usuario": Usuario, "motorista": Motorista, "onibus": Onibus,
            "fila": Fila, "alocacao_patio": AlocacaoPatio, "escala": Escala,
            "alerta": Alerta, "ficha_manutencao": FichaManutencao,
            "linha": Linha, "tipo_defeito": TipoDefeito,
            "permissao": Permissao, "importacao_escala": ImportacaoEscala,
        }
        contagens = {}
        for nome, modelo in modelos.items():
            count = db.execute(select(func.count()).select_from(modelo)).scalar()
            contagens[nome] = count or 0
        response["registros_por_tabela"] = contagens
        response["total_tabelas"] = len(contagens)
    except Exception as exc:
        response["status"] = "degraded"
        response["database"] = "error"
        logger.exception("Falha ao montar /health/detalhado")
        response["error"] = "Erro ao consultar o banco de dados."
    return response


@router.get("/", summary="Raiz da API")
def root() -> dict[str, str]:
    settings = get_settings()
    return {
        "sistema": "Gestão de Pátio Sambaíba",
        "versao": __version__,
        "docs": "/docs" if not settings.is_production else "indisponível em produção",
        "health": "/health",
    }
