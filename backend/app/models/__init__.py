"""Modelos SQLAlchemy 2.0 + ENUMs Python."""
from app.models.catalogos import Linha, Permissao, TipoDefeito
from app.models.enums import (
    OrigemEscalaEnum,
    PerfilUsuarioEnum,
    SetorEnum,
    StatusFichaEnum,
    StatusImportacaoEnum,
    StatusMotoristaEnum,
    StatusOnibusEnum,
    TipoAlertaEnum,
    TipoEscalaEnum,
    TipoFilaEnum,
)
from app.models.frota import AlocacaoPatio, Fila, Onibus
from app.models.operacoes import Alerta, Escala, FichaManutencao, ImportacaoEscala
from app.models.pessoas import Motorista, Usuario

__all__ = [
    "Usuario", "Motorista", "Onibus", "Fila", "AlocacaoPatio",
    "Escala", "Alerta", "FichaManutencao", "Linha", "TipoDefeito",
    "Permissao", "ImportacaoEscala",
    "SetorEnum", "StatusOnibusEnum", "StatusMotoristaEnum",
    "PerfilUsuarioEnum", "TipoFilaEnum", "TipoAlertaEnum",
    "StatusFichaEnum", "TipoEscalaEnum", "OrigemEscalaEnum",
    "StatusImportacaoEnum",
]
