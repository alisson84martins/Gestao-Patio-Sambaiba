"""Schemas específicos do endpoint /patio (visão consolidada)."""
from datetime import datetime, time
from typing import Literal, Optional
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
    alocacao_id: UUID           # usado pelo DELETE /alocacoes/{id}
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
    fila_abreviacao: Optional[str] = None
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


class PatioLiberadoItem(BaseModel):
    """Bloco J — um carro liberado pela manutenção e ainda parado na fila
    MANUTENCAO. É uma VISTA, não um registro: nada aqui é gravado em lugar
    nenhum. Os dois fatos de origem já vivem em portaria.recolhida_anormal
    (ENCERRADA) e ficha_manutencao (CONCLUIDA) — ver routers/patio.py.

    ⛔ Sem campo de "visto"/"avisado": o carro sai do quadro quando o
    operador o move para outra fila, e é só isso que dá baixa."""

    prefixo: int
    liberado_em: datetime
    origem: Literal["RECOLHIDA", "FICHA"]
    detalhe: Optional[str] = None


class PosicaoOnibus(BaseModel):
    """Resposta do 'onde está o ônibus X'."""
    numero_frota: int
    setor: Optional[str] = None
    fila_nome: str
    fila_tipo: TipoFilaEnum
    posicao: int
    alocado_em: datetime
