"""Schemas específicos do endpoint /patio (visão consolidada)."""
from datetime import datetime, time
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import (
    StatusFichaEnum,
    StatusOnibusEnum,
    TipoAlertaEnum,
    TipoFilaEnum,
)


class PatioOnibusInfo(BaseModel):
    """Info de um ônibus no pátio com escala/alerta/ficha."""
    onibus_id: UUID
    numero_frota: int
    setor: Optional[str] = None
    posicao: int
    status_onibus: StatusOnibusEnum
    alocado_em: datetime
    linha_codigo: Optional[str] = None
    linha_nome: Optional[str] = None
    horario_saida: Optional[time] = None
    alerta_tipo: Optional[TipoAlertaEnum] = None
    ficha_status: Optional[StatusFichaEnum] = None


class PatioFilaInfo(BaseModel):
    """Uma fila/posição com seus ônibus."""
    fila_id: UUID
    fila_nome: str
    fila_tipo: TipoFilaEnum
    fila_numero: Optional[int] = None
    onibus: list[PatioOnibusInfo] = []


class RemanejamentoItem(BaseModel):
    """Ônibus em manutenção que tem escala hoje (precisa remanejar)."""
    onibus_id: UUID
    numero_frota: int
    linha_codigo: str
    linha_nome: str
    horario_saida: time
    fila_manutencao: str
    tipo_defeito: Optional[str] = None
    status_ficha: Optional[StatusFichaEnum] = None
    ficha_aberta_em: Optional[datetime] = None


class PosicaoOnibus(BaseModel):
    """Resposta do 'onde está o ônibus X'."""
    numero_frota: int
    setor: Optional[str] = None
    fila_nome: str
    fila_tipo: TipoFilaEnum
    posicao: int
    alocado_em: datetime
