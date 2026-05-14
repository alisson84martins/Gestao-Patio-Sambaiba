"""Sistema de Gestão de Pátio Sambaíba — API v3.0."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.routers import (
    alertas, alocacoes, auth, escalas, filas, health, importacao,
    linhas, manutencao, motoristas, onibus, patio,
    tipos_defeito, usuarios,
)

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Iniciando API Gestão de Pátio Sambaíba v%s [%s]",
        __version__, settings.environment,
    )
    yield
    logger.info("Encerrando API")


tags_metadata = [
    {"name": "sistema", "description": "Health check e raiz da API"},
    {"name": "autenticação", "description": "Login JWT (RE + senha) e dados do usuário corrente"},
    {"name": "ônibus", "description": "Frota — CRUD com setor gerado pelo prefixo"},
    {"name": "motoristas", "description": "Cadastro de motoristas (separado de usuário)"},
    {"name": "linhas", "description": "Catálogo fechado de linhas E2 e AR2"},
    {"name": "tipos de defeito", "description": "Categorias de defeito para fichas de manutenção"},
    {"name": "filas / pátio", "description": "33 numéricas + posições especiais + manutenção"},
    {"name": "usuários", "description": "Gestão de usuários do sistema (apenas ADMIN)"},
    {"name": "alocações / pátio", "description": "Mover ônibus entre filas/posições"},
    {"name": "escala", "description": "Escala diária — manual ou via importação"},
    {"name": "alertas", "description": "PRESO (retido na rua) e AMOSTRAL (SPTRANS)"},
    {"name": "manutenção", "description": "Fichas de defeito e serviços"},
    {"name": "pátio (visão consolidada)", "description": "Estado completo + remanejamento + busca por frota"},
    {"name": "importação Excel", "description": "Upload de planilha .xlsx para criar escalas em massa"},
]

app = FastAPI(
    title="Gestão de Pátio Sambaíba — API",
    description=(
        "API REST do Sistema de Gestão de Pátio Sambaíba (Garagem 3) v3.0.\n\n"
        "**Stack**: FastAPI + SQLAlchemy 2 + PostgreSQL 15+ + JWT.\n\n"
        "**Como usar:**\n"
        "1. Faça login em `POST /auth/login` ou clique em **Authorize** com seu RE e senha.\n"
        "2. O token JWT é válido por 8 horas.\n"
        "3. Endpoints protegidos exigem header `Authorization: Bearer <token>`.\n"
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Sistema
app.include_router(health.router)
app.include_router(auth.router)
# Catálogos
app.include_router(onibus.router)
app.include_router(motoristas.router)
app.include_router(linhas.router)
app.include_router(tipos_defeito.router)
app.include_router(filas.router)
app.include_router(usuarios.router)
# Operacionais
app.include_router(alocacoes.router)
app.include_router(escalas.router)
app.include_router(alertas.router)
app.include_router(manutencao.router)
app.include_router(patio.router)
# Importação
app.include_router(importacao.router)
