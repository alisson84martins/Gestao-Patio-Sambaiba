"""Handlers globais de exceções para respostas padronizadas."""
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def _resposta_erro(status_code: int, mensagem: str, detalhes=None):
    body = {"erro": mensagem, "status_code": status_code}
    if detalhes:
        body["detalhes"] = detalhes
    return JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    """Registra handlers padronizados na app FastAPI."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return _resposta_erro(exc.status_code, exc.detail or "Erro HTTP")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Simplifica os erros de validação para o frontend
        detalhes = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            detalhes.append({
                "campo": loc,
                "mensagem": err.get("msg", "inválido"),
                "tipo": err.get("type", ""),
            })
        return _resposta_erro(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Dados inválidos",
            detalhes=detalhes,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        msg = str(exc.orig) if exc.orig else str(exc)
        # Trata constraints conhecidas
        if "uq_alocacao_onibus_ativa" in msg:
            return _resposta_erro(409, "Ônibus já está alocado em outra fila ativa")
        if "uq_alerta_onibus_tipo_ativo" in msg:
            return _resposta_erro(409, "Já existe alerta ativo desse tipo para o ônibus")
        if "uq_permissao_usuario_recurso" in msg:
            return _resposta_erro(409, "Permissão duplicada para esse recurso")
        if "duplicate key value" in msg.lower() or "violates unique" in msg.lower():
            return _resposta_erro(409, "Registro duplicado", detalhes={"causa": msg.split("\n")[0]})
        if "violates foreign key" in msg.lower():
            return _resposta_erro(409, "Referência inexistente", detalhes={"causa": msg.split("\n")[0]})
        if "violates check constraint" in msg.lower():
            return _resposta_erro(422, "Dados violam regra de negócio", detalhes={"causa": msg.split("\n")[0]})
        logger.exception("Erro de integridade não mapeado")
        return _resposta_erro(409, "Conflito de dados", detalhes={"causa": msg.split("\n")[0]})

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("Erro SQLAlchemy")
        return _resposta_erro(500, "Erro no banco de dados")

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception("Erro não tratado")
        return _resposta_erro(500, "Erro interno do servidor")
