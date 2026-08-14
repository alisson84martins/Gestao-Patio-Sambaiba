"""Configurações da aplicação carregadas de variáveis de ambiente / .env."""
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fuso de operação da garagem — data e hora que uma PESSOA lê e digita
# (data_ocorrencia, hora_ocorrencia da pré-ocorrência e da ocorrência
# definitiva) vão neste fuso. Momento que o SISTEMA registra (enviada_em,
# convertida_em, expira_em, atualizado_em, usada_em, log_acesso) continua em
# UTC — isso não muda. Módulo compartilhado pra não duplicar a constante
# entre pre_ocorrencias_publico.py e pre_ocorrencias.py (mesmo precedente de
# mascaras.js/escape.js: duplicata diverge em silêncio).
FUSO_OPERACAO = ZoneInfo("America/Sao_Paulo")


class Settings(BaseSettings):
    """Configurações lidas do .env. Validadas automaticamente pelo Pydantic."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Banco
    database_url: str = Field(
        ...,
        description="URL de conexão SQLAlchemy ao PostgreSQL",
    )

    # Segurança
    secret_key: str = Field(
        ...,
        description="Chave para assinar tokens JWT",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    # SEV-15 (fechamento, 11/08/2026): era 1440 (24h) aqui, mas 480 (8h) em
    # .env.example, no .env real (dev e, supostamente, produção) e no texto
    # da doc OpenAPI (main.py). Alinhado pro valor que já era o de fato
    # (480) — token sem revogação em localStorage é motivo pra prazo mais
    # curto, não mais longo, então não escolhi o maior dos dois por
    # segurança. O que vale de verdade em produção é o `.env` de lá, que
    # só o Alisson vê — isto só corrige o valor de fallback e a
    # documentação, que estavam divergentes entre si.
    jwt_expire_minutes: int = 480  # 8 horas — cobre um turno

    # Ambiente
    environment: str = Field(default="development", pattern="^(development|staging|production)$")

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500"

    # Logs
    log_level: str = "INFO"

    # Pré-ocorrência do motorista — webhook n8n (notifica o link por
    # WhatsApp/SMS). Opcionais: em dev/teste sem n8n configurado, a
    # autorização grava normalmente e a notificação só não dispara (loga
    # e segue — nunca falha a requisição por causa disso).
    n8n_webhook_url: Optional[str] = None
    n8n_webhook_token: Optional[str] = None

    @property
    def cors_origins_list(self) -> list[str]:
        """Retorna a lista de origens permitidas para CORS."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única das configurações (cacheada)."""
    return Settings()
