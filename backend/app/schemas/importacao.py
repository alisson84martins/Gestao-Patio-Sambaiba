"""Schemas para o endpoint de importação Excel."""
from typing import Optional

from pydantic import BaseModel

from app.schemas.operacoes import ImportacaoEscalaRead


class ErroLinha(BaseModel):
    """Erro encontrado em uma linha da planilha."""
    linha: int
    motivo: str
    valor_recebido: Optional[str] = None


class ImportacaoUploadResponse(BaseModel):
    """Resposta do upload de planilha de escala."""
    importacao: ImportacaoEscalaRead
    total_lidos: int
    total_inseridos: int
    total_erros: int
    erros: list[ErroLinha] = []
    substituidas: int = 0  # qtde de escalas antigas marcadas como deletadas
