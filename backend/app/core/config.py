"""Configurações da aplicação carregadas de variáveis de ambiente / .env."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    jwt_expire_minutes: int = 480

    # Ambiente
    environment: str = Field(default="development", pattern="^(development|staging|production)$")

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500"

    # Logs
    log_level: str = "INFO"

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
