"""Schemas Pydantic v2 — aba Escala de Fiscais (cadastros e importação).

D-A (24/09/2026): o FISCAL é o RE em TEXTO, sem cadastro obrigatório. O RE
passa pela MESMA normalização do cadastro de Pessoas (app/core/registro.py:
trim + maiúsculas, nunca vira número, zero à esquerda preservado) — a escala
e Pessoas tratam o RE do mesmo jeito, senão a junção pelo nome falha. O nome vem de funcionario quando existe cadastro com
aquele RE; sem cadastro, `nome` volta nulo e a tela mostra só o RE.

O COORDENADOR continua precisando de cadastro (FK em funcionario): RE fora
de funcionario → "RE X não está cadastrado. Cadastre em Pessoas antes".
"""
from datetime import date, time
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.registro import ReNormalizado, ReNormalizadoObrigatorio

Periodo = Literal[1, 2]
FolgaBase = Literal["sabado", "domingo"]
Lado = Literal["TP", "TS"]
Turno = Literal["manha", "tarde"]
TipoDia = Literal["util", "sabado", "domingo", "feriado", "natal", "ano_novo"]
Paridade = Literal["impar", "par"]
Situacao = Literal["escalado", "outra_garagem", "descoberto", "direto"]
TipoAusencia = Literal["ferias", "atestado", "afastado"]
TipoTroca = Literal["2x2", "1x1"]

# RE pela função oficial (app/core/registro.py) — ⛔ não duplicar aqui.
ReTexto = Annotated[ReNormalizadoObrigatorio, Field(min_length=1, max_length=20)]
ReOpcional = Annotated[ReNormalizado, Field(max_length=20)]


class FiscalResumo(BaseModel):
    """Fiscal pelo RE; nome só quando há cadastro em Pessoas."""
    re: str
    nome: Optional[str] = None
    cadastrado: bool = False


class CoordenadorResumo(BaseModel):
    """Coordenador: sempre cadastrado (FK em funcionario)."""
    funcionario_id: UUID
    re: str
    nome: str


# ─── Quadro de fiscais ────────────────────────────────────────────────────────

class QuadroCreate(BaseModel):
    re: ReTexto
    periodo: Periodo
    folga_base: Optional[FolgaBase] = None
    ativo: bool = True


class QuadroUpdate(BaseModel):
    periodo: Optional[Periodo] = None
    folga_base: Optional[FolgaBase] = None
    ativo: Optional[bool] = None


class QuadroRead(FiscalResumo):
    periodo: int
    folga_base: Optional[str] = None
    ativo: bool


# ─── Coordenadores ───────────────────────────────────────────────────────────

class CoordenadorPeriodoCreate(BaseModel):
    re: ReTexto
    periodo: Periodo


class CoordenadorPeriodoRead(CoordenadorResumo):
    periodo: int


class CoordenadorHorarioCreate(BaseModel):
    re: ReTexto
    turno: Turno
    hora_inicio: time
    # Menor que hora_inicio = termina no dia seguinte (ex.: 15:00 → 02:30).
    hora_fim: time
    ativo: bool = True


class CoordenadorHorarioUpdate(BaseModel):
    hora_inicio: Optional[time] = None
    hora_fim: Optional[time] = None
    ativo: Optional[bool] = None


class CoordenadorHorarioRead(CoordenadorResumo):
    id: UUID
    turno: str
    hora_inicio: time
    hora_fim: time
    ativo: bool
    passa_meia_noite: bool


# ─── Pontos finais ───────────────────────────────────────────────────────────

class PontoFinalCreate(BaseModel):
    nome: str = Field(..., min_length=1, max_length=80)
    ativo: bool = True


class PontoFinalUpdate(BaseModel):
    nome: Optional[str] = Field(None, min_length=1, max_length=80)
    ativo: Optional[bool] = None


class PontoFinalRead(BaseModel):
    id: UUID
    nome: str
    ativo: bool
    qtd_postos: int = 0


# ─── Postos ──────────────────────────────────────────────────────────────────

def _normalizar_linhas(valor: Optional[list[str]]) -> Optional[list[str]]:
    """Maiúsculas, sem vazio e sem repetição, na ordem digitada."""
    if valor is None:
        return None
    limpas: list[str] = []
    for linha in valor:
        txt = (linha or "").strip().upper()
        if not txt:
            continue
        if len(txt) > 10:
            raise ValueError(f"Linha \"{txt}\" tem mais de 10 caracteres")
        if txt not in limpas:
            limpas.append(txt)
    if not limpas:
        raise ValueError("Informe ao menos uma linha")
    return limpas


class PostoCreate(BaseModel):
    lado: Lado
    cod_jb: Optional[str] = Field(None, max_length=40)
    lote: Optional[str] = Field(None, max_length=20)
    ponto_final_id: Optional[UUID] = None
    linhas: list[str] = Field(..., min_length=1)
    ativo: bool = True

    _linhas = field_validator("linhas")(_normalizar_linhas)


class PostoUpdate(BaseModel):
    lado: Optional[Lado] = None
    cod_jb: Optional[str] = Field(None, max_length=40)
    lote: Optional[str] = Field(None, max_length=20)
    ponto_final_id: Optional[UUID] = None
    linhas: Optional[list[str]] = None
    ativo: Optional[bool] = None

    _linhas = field_validator("linhas")(_normalizar_linhas)


class PostoRead(BaseModel):
    id: UUID
    lado: str
    cod_jb: Optional[str] = None
    lote: Optional[str] = None
    ponto_final_id: Optional[UUID] = None
    ponto_final_nome: Optional[str] = None
    linhas: list[str]
    ativo: bool


# ─── Modelos ─────────────────────────────────────────────────────────────────

class ModeloCreate(BaseModel):
    tipo_dia: TipoDia
    paridade: Optional[Paridade] = None
    nome: str = Field(..., min_length=1, max_length=60)


class ModeloRead(BaseModel):
    id: UUID
    tipo_dia: str
    paridade: Optional[str] = None
    nome: str
    qtd_postos: int = 0


class ModeloPostoCreate(BaseModel):
    posto_id: UUID
    ordem: int = Field(..., ge=1, le=32000)
    periodo: Periodo
    hora_inicio: Optional[time] = None
    hora_termino: Optional[time] = None
    situacao_padrao: Situacao
    re_padrao: ReOpcional = None
    outra_garagem: Optional[str] = Field(None, max_length=3)
    # D-B: texto que a impressão mostra quando não há RE. Vazio = em branco.
    marcador: Optional[str] = Field(None, max_length=10)


class ModeloPostoUpdate(BaseModel):
    ordem: Optional[int] = Field(None, ge=1, le=32000)
    periodo: Optional[Periodo] = None
    hora_inicio: Optional[time] = None
    hora_termino: Optional[time] = None
    situacao_padrao: Optional[Situacao] = None
    re_padrao: ReOpcional = None
    outra_garagem: Optional[str] = Field(None, max_length=3)
    marcador: Optional[str] = Field(None, max_length=10)


class ModeloPostoRead(BaseModel):
    id: UUID
    modelo_id: UUID
    posto_id: UUID
    lado: str
    cod_jb: Optional[str] = None
    lote: Optional[str] = None
    linhas: list[str]
    ordem: int
    periodo: int
    hora_inicio: Optional[time] = None
    hora_termino: Optional[time] = None
    situacao_padrao: str
    re_padrao: Optional[str] = None
    nome_padrao: Optional[str] = None
    outra_garagem: Optional[str] = None
    marcador: Optional[str] = None


class ModeloDetalhe(ModeloRead):
    postos: list[ModeloPostoRead]


# ─── Ausências e trocas ──────────────────────────────────────────────────────

class AusenciaCreate(BaseModel):
    re: ReTexto
    tipo: TipoAusencia
    data_inicio: date
    # Inclusiva. Vazio = sem previsão de volta.
    data_fim: Optional[date] = None
    # ⛔ Nada de CID/diagnóstico.
    observacao: Optional[str] = Field(None, max_length=500)


class AusenciaUpdate(BaseModel):
    tipo: Optional[TipoAusencia] = None
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    observacao: Optional[str] = Field(None, max_length=500)


class AusenciaRead(FiscalResumo):
    id: UUID
    tipo: str
    data_inicio: date
    data_fim: Optional[date] = None
    observacao: Optional[str] = None


class TrocaCreate(BaseModel):
    tipo: TipoTroca
    re_a: ReTexto
    re_b: ReTexto
    data_sabado: date
    observacao: Optional[str] = Field(None, max_length=500)


class TrocaRead(BaseModel):
    id: UUID
    tipo: str
    data_sabado: date
    observacao: Optional[str] = None
    a: FiscalResumo
    b: FiscalResumo


# ─── Busca de pessoa (autocomplete) ──────────────────────────────────────────

class PessoaBusca(BaseModel):
    re: str
    nome: str
