"""Enums Python espelhando os tipos enumerados do PostgreSQL."""
from enum import Enum


class SetorEnum(str, Enum):
    E2 = "E2"
    AR2 = "AR2"


class StatusOnibusEnum(str, Enum):
    ATIVO = "ATIVO"
    MANUTENCAO = "MANUTENCAO"
    INATIVO = "INATIVO"
    RESERVA = "RESERVA"


class StatusMotoristaEnum(str, Enum):
    ATIVO = "ATIVO"
    AFASTADO = "AFASTADO"
    FERIAS = "FERIAS"
    DESLIGADO = "DESLIGADO"


class PerfilUsuarioEnum(str, Enum):
    ADMIN = "ADMIN"
    COORDENADOR = "COORDENADOR"
    OPERADOR_PATIO = "OPERADOR_PATIO"
    MOTORISTA = "MOTORISTA"
    MECANICO = "MECANICO"


class TipoFilaEnum(str, Enum):
    NUMERICA = "NUMERICA"
    ESPECIAL = "ESPECIAL"
    MANUTENCAO = "MANUTENCAO"


class TipoAlertaEnum(str, Enum):
    PRESO = "PRESO"
    AMOSTRAL = "AMOSTRAL"


class StatusFichaEnum(str, Enum):
    ABERTA = "ABERTA"
    EM_ANDAMENTO = "EM_ANDAMENTO"
    CONCLUIDA = "CONCLUIDA"
    CANCELADA = "CANCELADA"


class TipoEscalaEnum(str, Enum):
    MANOBRA = "MANOBRA"
    PLANTAO_E2 = "PLANTAO_E2"
    PLANTAO_AR2 = "PLANTAO_AR2"


class OrigemEscalaEnum(str, Enum):
    IMPORTACAO_EXCEL = "IMPORTACAO_EXCEL"
    MANUAL = "MANUAL"


class StatusImportacaoEnum(str, Enum):
    SUCESSO = "SUCESSO"
    ERRO = "ERRO"
    PARCIAL = "PARCIAL"
