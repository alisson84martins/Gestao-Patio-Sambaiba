"""Sistema de Gestão de Pátio Sambaíba — API v3.0."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.routers import (
    alertas, alocacoes, auth, escalas, filas, funcoes, funcionarios,
    health, importacao, linhas, manutencao, motoristas, ocorrencias, onibus,
    patio, permissoes, portaria, portaria_recolhidas, portaria_veiculos,
    pre_cadastro, pre_ocorrencias, pre_ocorrencias_publico, tipos_defeito, usuarios,
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
    {"name": "autenticacao", "description": "Login JWT (RE + senha) e dados do usuário corrente"},
    {"name": "funcionários", "description": "Cadastro central de pessoas, funções e vínculos"},
    {"name": "funções e recursos", "description": "Catálogos de funções e recursos do RBAC"},
    {"name": "permissões", "description": "Pacote por função e overrides individuais"},
    {"name": "ônibus", "description": "Frota — CRUD com setor gerado pelo prefixo"},
    {"name": "motoristas", "description": "Cadastro de motoristas (separado de usuário)"},
    {"name": "linhas", "description": "Catálogo fechado de linhas E2 e AR2"},
    {"name": "tipos de defeito", "description": "Categorias de defeito para fichas de manutenção"},
    {"name": "filas / pátio", "description": "33 numéricas + posições especiais + manutenção"},
    {"name": "usuários", "description": "Gestão de usuários do sistema (apenas ADMIN — legado)"},
    {"name": "alocações / pátio", "description": "Mover ônibus entre filas/posições"},
    {"name": "escala", "description": "Escala diária — manual ou via importação"},
    {"name": "alertas", "description": "PRESO (retido na rua) e AMOSTRAL (SPTRANS)"},
    {"name": "manutenção", "description": "Fichas de defeito e serviços"},
    {"name": "pátio (visão consolidada)", "description": "Estado completo + remanejamento + busca por frota"},
    {"name": "importação Excel", "description": "Upload de planilha .xlsx para criar escalas em massa"},
    {"name": "ocorrências", "description": "Suite Coordenadoria — relatório de ocorrências, mensagem do sinistro e anexos"},
    {"name": "portaria", "description": "Controle de acesso veicular — entrada, saída, cadastro e autorização de veículos; QR do veículo e recolhida anormal"},
    {"name": "pré-cadastro", "description": "Cadastro preliminar de pessoas alimentado pela operação (portaria, pré-ocorrência) — nunca cria acesso ao sistema"},
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
    # SEV-09: mapa completo da API (rotas, schemas, parâmetros) só faz
    # sentido público em ambiente de desenvolvimento. `None` desliga a
    # rota inteira no FastAPI, não só o link — /docs, /redoc e
    # /openapi.json somem de verdade em produção, não ficam "escondidos".
    # ⚠️ Isto não tem efeito enquanto o `.env` de produção estiver com
    # ENVIRONMENT=development (pendência conhecida, fora do alcance desta
    # sessão — só o Alisson tem acesso ao servidor).
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # SEV-11 (fechamento, 11/08/2026): "*" com allow_credentials=True
    # deixava qualquer cabeçalho passar numa requisição já autenticada.
    # O frontend-v3 manda três cabeçalhos, não dois: Authorization e
    # Content-Type (api.js, importacao.js, ocorrencia.form.js — o upload
    # multipart tem o Content-Type com boundary posto pelo próprio
    # navegador, nunca manual) e X-Pre-Ocorrencia-Token
    # (pre-ocorrencia.page.js). Esse terceiro escapou da varredura
    # original porque o formulário público não tem JWT pra mandar — ele
    # tem um fetch helper próprio, deliberadamente fora de api.js, que se
    # autentica pelo token de uso único em cabeçalho. ⚠️ A lista de
    # ORIGENS (allow_origins) continua vindo do .env de produção, que só
    # o Alisson vê — não mexi nisso.
    allow_headers=["Authorization", "Content-Type", "X-Pre-Ocorrencia-Token"],
)

register_exception_handlers(app)

# Sistema
app.include_router(health.router)
app.include_router(auth.router)
# Cadastro central (Suite Coordenadoria — RBAC)
app.include_router(funcionarios.router)
app.include_router(funcoes.router)
app.include_router(permissoes.router)
# Catálogos legados
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
# Suite Coordenadoria — ocorrências (uploads são servidos por rota própria
# do router, protegida por exige("ocorrencia") — nunca por StaticFiles público)
app.include_router(ocorrencias.router)
# Pré-ocorrência do motorista — autenticado (RBAC) + público (token, sem
# autenticação nenhuma). Dois routers de propósito — ver
# pre_ocorrencias_publico.py sobre por que nunca podem se misturar.
# ⛔ Ordem importa: o público (/pre-ocorrencias/publico/...) precisa vir
# ANTES do autenticado, senão GET /pre-ocorrencias/{pre_ocorrencia_id}
# (rota registrada primeiro) casa com "publico" como se fosse o id e pede
# autenticação antes mesmo de validar o UUID — mesmo motivo pelo qual
# /autopreencher vem antes de /{ocorrencia_id} em ocorrencias.py.
app.include_router(pre_ocorrencias_publico.router)
app.include_router(pre_ocorrencias.router)
# Portaria — controle de acesso veicular. Dois routers, mesmo prefix
# /portaria: portaria.py (movimento, recurso acesso_veicular) e
# portaria_veiculos.py (cadastro/autorização, recursos veiculo_portaria e
# autorizacao_veicular — D6). Sem ambiguidade de rota entre eles: nenhum
# path+método colide (ver comentário no topo de portaria_veiculos.py).
app.include_router(portaria.router)
app.include_router(portaria_veiculos.router)
app.include_router(portaria_recolhidas.router)
# Bloco H — identidade compartilhada da Suite (public), não portaria.
app.include_router(pre_cadastro.router)
