"""Endpoint de health check — confirma API, banco e modelos."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    Alerta, AlocacaoPatio, Escala, FichaManutencao, Fila,
    ImportacaoEscala, Linha, Motorista, Onibus, Permissao,
    TipoDefeito, Usuario,
)

router = APIRouter(tags=["sistema"])


@router.get("/health", summary="Verifica saúde da API, banco e modelos")
def health_check(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Retorna status e conta registros em todas as 12 tabelas."""
    settings = get_settings()
    response: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "timestamp": datetime.utcnow().isoformat(),
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
        response["error"] = str(exc)
    return response


@router.get("/", summary="Raiz da API")
def root() -> dict[str, str]:
    return {
        "sistema": "Gestão de Pátio Sambaíba",
        "versao": __version__,
        "docs": "/docs",
        "health": "/health",
    }
