"""Schemas Pydantic v2 do módulo Coordenadoria — Relatório de Ocorrências.

Os campos com conjunto fechado de opções (Literal) espelham os CHECK
constraints da migration 012 — validação acontece na API antes de chegar
no banco, mas a fonte da verdade dos valores permitidos é o SQL.
"""
from datetime import date, datetime, time
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import ORMBase

StatusOcorrencia = Literal["RASCUNHO", "FINALIZADA", "CANCELADA"]


# ============================================================================
# CATÁLOGOS
# ============================================================================

class TipoOcorrenciaRead(ORMBase):
    id: UUID
    codigo: str
    nome: str
    exige_vitima: bool
    exige_terceiro: bool
    exige_analise: bool
    ordem: int
    ativo: bool


class OrgaoAutoridadeRead(ORMBase):
    id: UUID
    codigo: str
    nome: str
    ordem: int
    ativo: bool


REGIOES_AVARIA = [
    "FRENTE", "TRASEIRA", "LATERAL_ESQUERDA", "LATERAL_DIREITA",
    "TETO", "INTERIOR", "RODADO", "RETROVISOR", "PARABRISA", "OUTRO",
]


class OcorrenciaCatalogos(BaseModel):
    """Uma chamada só para a tela montar todos os selects do formulário."""
    tipos: list[TipoOcorrenciaRead]
    orgaos: list[OrgaoAutoridadeRead]
    regioes_avaria: list[str] = REGIOES_AVARIA


# ============================================================================
# ANÁLISE DO ACIDENTE (página 3) — 1:1
# ============================================================================

class OcorrenciaAnaliseIn(BaseModel):
    colisao: Optional[Literal["FRONTAL", "TRASEIRA", "LATERAL"]] = None
    acidente: Optional[Literal["CAPOTAMENTO", "TOMBAMENTO", "ENGAVETAMENTO"]] = None
    condicoes: Optional[Literal["TRANSITANDO", "MANOBRANDO", "PARADO"]] = None
    deslocamento: Optional[Literal["EM_FRENTE", "EM_RE", "REBOCADO"]] = None

    reta: Optional[Literal["EM_PLANO", "EM_ACLIVE", "EM_DECLIVE"]] = None
    curva: Optional[Literal["EM_PLANO", "EM_ACLIVE", "EM_DECLIVE", "DEPRESSAO", "LOMBADA", "NORMAL"]] = None
    via: Optional[Literal["TREVO", "CRUZAMENTO", "BIFURCACAO", "NORMAL"]] = None
    numero_faixas: Optional[Literal["UMA", "DUAS", "TRES", "MAIS_DE_TRES"]] = None
    mao_direcao: Optional[Literal["UNICA", "DUPLA", "PRIVATIVA_COLETIVO"]] = None
    preferencial: Optional[Literal["SIM", "NAO", "NAO_SE_APLICA"]] = None
    condicao_pista: Optional[Literal["SECA", "MOLHADA", "OLEOSA", "ENLAMEADA"]] = None
    pavimentacao: Optional[Literal["ASFALTO", "CONCRETO", "PARALELEPIPEDO", "OUTROS"]] = None
    conservacao: Optional[Literal["BOM", "DANIFICADO", "EM_OBRAS"]] = None

    sinal_horizontal: Optional[Literal[
        "NAO_EXISTE", "FAIXA_SIMPLES", "FAIXA_DUPLA", "TRAV_PEDESTRE", "PARE", "OUTROS"
    ]] = None
    sinal_horizontal_outros: Optional[str] = Field(None, max_length=120)
    sinal_vertical: Optional[Literal[
        "NAO_EXISTE", "PARE", "ESCOLA", "MAO_DE_DIRECAO", "VELOCIDADE", "PREFERENCIAL", "OUTROS"
    ]] = None
    sinal_vertical_outros: Optional[str] = Field(None, max_length=120)
    dispositivos_aux: Optional[Literal["NAO_EXISTE", "NORMAL", "DESLIGADO", "COM_DEFEITO", "ATENCAO"]] = None

    iluminacao: Optional[Literal[
        "DIA", "NOITE_COM_ILUMINACAO", "NOITE_SEM_ILUMINACAO", "ANOITECER_AMANHECER", "OUTROS"
    ]] = None
    tempo: Optional[Literal["BOM", "NUBLADO", "CHUVA", "GAROA", "NEBLINA"]] = None
    visibilidade: Optional[Literal["BOM", "REGULAR", "MA"]] = None


class OcorrenciaAnaliseRead(OcorrenciaAnaliseIn, ORMBase):
    criado_em: datetime


# ============================================================================
# VEÍCULO DE TERCEIRO (página 2)
# ============================================================================

class OcorrenciaVeiculoTerceiroIn(BaseModel):
    ordem: int = 1
    danos: Optional[Literal["GRANDE", "MEDIO", "PEQUENO"]] = None
    marca: Optional[str] = Field(None, max_length=40)
    modelo: Optional[str] = Field(None, max_length=60)
    ano: Optional[str] = Field(None, max_length=9)
    cor: Optional[str] = Field(None, max_length=30)
    placa: Optional[str] = Field(None, max_length=10)
    cidade_placa: Optional[str] = Field(None, max_length=80)
    estado_placa: Optional[str] = Field(None, max_length=2)
    renavam: Optional[str] = Field(None, max_length=20)

    proprietario: Optional[str] = Field(None, max_length=120)
    fones: Optional[str] = Field(None, max_length=60)
    email: Optional[str] = Field(None, max_length=120)
    endereco: Optional[str] = Field(None, max_length=200)
    cidade: Optional[str] = Field(None, max_length=80)
    rg: Optional[str] = Field(None, max_length=20)
    cpf: Optional[str] = Field(None, max_length=14)
    cnh: Optional[str] = Field(None, max_length=20)

    seguradora: Optional[str] = Field(None, max_length=80)
    seguradora_fone: Optional[str] = Field(None, max_length=30)
    sinistro_numero: Optional[str] = Field(None, max_length=40)
    partes_avariadas: Optional[str] = None


class OcorrenciaVeiculoTerceiroRead(OcorrenciaVeiculoTerceiroIn, ORMBase):
    id: UUID
    criado_em: datetime


# ============================================================================
# AVARIA DO NOSSO VEÍCULO — por região
# ============================================================================

class OcorrenciaAvariaIn(BaseModel):
    regiao: Literal[
        "FRENTE", "TRASEIRA", "LATERAL_ESQUERDA", "LATERAL_DIREITA",
        "TETO", "INTERIOR", "RODADO", "RETROVISOR", "PARABRISA", "OUTRO",
    ]
    descricao: Optional[str] = None


class OcorrenciaAvariaRead(OcorrenciaAvariaIn, ORMBase):
    id: UUID


# ============================================================================
# VÍTIMAS (página 4) — DADO PESSOAL DE TERCEIRO
# ============================================================================

class OcorrenciaVitimaIn(BaseModel):
    ordem: int = 1
    nome: str = Field(..., max_length=120)
    rg: Optional[str] = Field(None, max_length=20)
    cpf: Optional[str] = Field(None, max_length=14)
    idade: Optional[int] = None
    fone: Optional[str] = Field(None, max_length=30)
    endereco: Optional[str] = Field(None, max_length=200)
    numero: Optional[str] = Field(None, max_length=20)
    bairro: Optional[str] = Field(None, max_length=80)
    cidade: Optional[str] = Field(None, max_length=80)
    dados_pessoais: Optional[str] = None

    era_passageiro: Optional[bool] = None

    destino_socorro: Optional[str] = Field(None, max_length=120)
    contato_parentesco: Optional[str] = Field(None, max_length=40)
    contato_nome: Optional[str] = Field(None, max_length=120)
    contato_fone: Optional[str] = Field(None, max_length=30)


class OcorrenciaVitimaRead(OcorrenciaVitimaIn, ORMBase):
    id: UUID
    criado_em: datetime


# ============================================================================
# TESTEMUNHAS (página 4) — DADO PESSOAL DE TERCEIRO
# ============================================================================

class OcorrenciaTestemunhaIn(BaseModel):
    ordem: int = 1
    nome: str = Field(..., max_length=120)
    rg: Optional[str] = Field(None, max_length=20)
    endereco: Optional[str] = Field(None, max_length=200)
    numero: Optional[str] = Field(None, max_length=20)
    bairro: Optional[str] = Field(None, max_length=80)
    cidade: Optional[str] = Field(None, max_length=80)
    fone1: Optional[str] = Field(None, max_length=30)
    fone2: Optional[str] = Field(None, max_length=30)


class OcorrenciaTestemunhaRead(OcorrenciaTestemunhaIn, ORMBase):
    id: UUID
    criado_em: datetime


# ============================================================================
# AUTORIDADES NO LOCAL
# ============================================================================

class OcorrenciaAutoridadeIn(BaseModel):
    orgao_id: UUID
    identificacao: Optional[str] = Field(None, max_length=40)
    responsavel: Optional[str] = Field(None, max_length=200)
    observacao: Optional[str] = None
    ordem: int = 1


class OcorrenciaAutoridadeRead(OcorrenciaAutoridadeIn, ORMBase):
    id: UUID
    criado_em: datetime
    orgao: OrgaoAutoridadeRead


# ============================================================================
# ANEXOS
# ============================================================================

TipoAnexo = Literal["FOTO_ACIDENTE", "FOTO_RELATORIO", "CROQUI", "BO_PDF", "OUTRO"]


class OcorrenciaAnexoRead(ORMBase):
    id: UUID
    tipo: TipoAnexo
    caminho: str
    nome_original: Optional[str] = None
    mime_type: Optional[str] = None
    tamanho_bytes: Optional[int] = None
    largura: Optional[int] = None
    altura: Optional[int] = None
    descricao: Optional[str] = None
    ordem: int
    enviado_por: Optional[UUID] = None
    criado_em: datetime


# ============================================================================
# OCORRENCIA — capa (página 1)
# ============================================================================

class OcorrenciaBase(BaseModel):
    tipo_ocorrencia_id: UUID
    data_ocorrencia: date
    hora_ocorrencia: time
    prefixo: str = Field(..., max_length=10)
    placa: Optional[str] = Field(None, max_length=10)
    linha_codigo: Optional[str] = Field(None, max_length=20)

    condutor_re: Optional[str] = Field(None, max_length=20)
    condutor_nome: Optional[str] = Field(None, max_length=120)
    condutor_funcao: Optional[str] = Field(None, max_length=40)
    condutor_cnh: Optional[str] = Field(None, max_length=20)
    condutor_rg: Optional[str] = Field(None, max_length=20)
    condutor_cpf: Optional[str] = Field(None, max_length=14)
    direcao_defensiva: Optional[bool] = None

    cobrador_re: Optional[str] = Field(None, max_length=20)
    cobrador_nome: Optional[str] = Field(None, max_length=120)

    velocidade_via: Optional[int] = None
    velocidade_onibus: Optional[int] = None
    foi_ao_local: Optional[bool] = None
    confirmado: Optional[bool] = None

    via_urbana: bool = False
    via_rodoviaria: bool = False
    area_interna: bool = False
    corredor: bool = False
    tem_fotos: bool = False
    monitoramento: bool = False
    sentido: Optional[str] = Field(None, max_length=40)

    local_ocorrido: Optional[str] = Field(None, max_length=200)
    numero_local: Optional[str] = Field(None, max_length=20)
    bairro: Optional[str] = Field(None, max_length=80)
    cidade: str = Field("São Paulo", max_length=80)

    quant_acidentes: Optional[int] = None
    isentos: Optional[int] = None
    culpados: Optional[int] = None

    problemas_mecanicos: Optional[bool] = None
    problemas_mecanicos_qual: Optional[str] = None
    condutor_avisou_manutencao: Optional[bool] = None
    manutencao_avisado_nome: Optional[str] = Field(None, max_length=120)

    descricao_coordenador: Optional[str] = None
    descricao_motorista: Optional[str] = None
    descricao_terceiro: Optional[str] = None

    ocorrencia_policial: bool = False
    viatura_numero: Optional[str] = Field(None, max_length=30)
    bpm: Optional[str] = Field(None, max_length=30)
    cia: Optional[str] = Field(None, max_length=30)
    distrito: Optional[str] = Field(None, max_length=60)
    numero_to: Optional[str] = Field(None, max_length=40)
    numero_bo: Optional[str] = Field(None, max_length=40)
    protocolo: Optional[str] = Field(None, max_length=60)
    houve_policia_tecnica: bool = False
    nome_perito: Optional[str] = Field(None, max_length=120)

    observacoes: Optional[str] = None
    controlador_acesso: Optional[str] = Field(None, max_length=120)


class OcorrenciaCreate(OcorrenciaBase):
    pass


class OcorrenciaUpdate(BaseModel):
    """Atualização parcial da capa — só os campos enviados são alterados.

    Quando uma lista de filhas é enviada (não-None), ela SUBSTITUI por
    completo a coleção existente daquele tipo — é o formulário salvando
    a seção inteira de uma vez, não um merge campo a campo.
    """
    tipo_ocorrencia_id: Optional[UUID] = None
    data_ocorrencia: Optional[date] = None
    hora_ocorrencia: Optional[time] = None
    prefixo: Optional[str] = Field(None, max_length=10)
    placa: Optional[str] = Field(None, max_length=10)
    linha_codigo: Optional[str] = Field(None, max_length=20)

    condutor_re: Optional[str] = Field(None, max_length=20)
    condutor_nome: Optional[str] = Field(None, max_length=120)
    condutor_funcao: Optional[str] = Field(None, max_length=40)
    condutor_cnh: Optional[str] = Field(None, max_length=20)
    condutor_rg: Optional[str] = Field(None, max_length=20)
    condutor_cpf: Optional[str] = Field(None, max_length=14)
    direcao_defensiva: Optional[bool] = None

    cobrador_re: Optional[str] = Field(None, max_length=20)
    cobrador_nome: Optional[str] = Field(None, max_length=120)

    velocidade_via: Optional[int] = None
    velocidade_onibus: Optional[int] = None
    foi_ao_local: Optional[bool] = None
    confirmado: Optional[bool] = None

    via_urbana: Optional[bool] = None
    via_rodoviaria: Optional[bool] = None
    area_interna: Optional[bool] = None
    corredor: Optional[bool] = None
    tem_fotos: Optional[bool] = None
    monitoramento: Optional[bool] = None
    sentido: Optional[str] = Field(None, max_length=40)

    local_ocorrido: Optional[str] = Field(None, max_length=200)
    numero_local: Optional[str] = Field(None, max_length=20)
    bairro: Optional[str] = Field(None, max_length=80)
    cidade: Optional[str] = Field(None, max_length=80)

    quant_acidentes: Optional[int] = None
    isentos: Optional[int] = None
    culpados: Optional[int] = None

    problemas_mecanicos: Optional[bool] = None
    problemas_mecanicos_qual: Optional[str] = None
    condutor_avisou_manutencao: Optional[bool] = None
    manutencao_avisado_nome: Optional[str] = Field(None, max_length=120)

    descricao_coordenador: Optional[str] = None
    descricao_motorista: Optional[str] = None
    descricao_terceiro: Optional[str] = None

    ocorrencia_policial: Optional[bool] = None
    viatura_numero: Optional[str] = Field(None, max_length=30)
    bpm: Optional[str] = Field(None, max_length=30)
    cia: Optional[str] = Field(None, max_length=30)
    distrito: Optional[str] = Field(None, max_length=60)
    numero_to: Optional[str] = Field(None, max_length=40)
    numero_bo: Optional[str] = Field(None, max_length=40)
    protocolo: Optional[str] = Field(None, max_length=60)
    houve_policia_tecnica: Optional[bool] = None
    nome_perito: Optional[str] = Field(None, max_length=120)

    observacoes: Optional[str] = None
    controlador_acesso: Optional[str] = Field(None, max_length=120)

    status: Optional[StatusOcorrencia] = None

    # Filhas — None = não mexe; [] = apaga tudo; lista = substitui
    analise: Optional[OcorrenciaAnaliseIn] = None
    veiculos_terceiro: Optional[list[OcorrenciaVeiculoTerceiroIn]] = None
    avarias: Optional[list[OcorrenciaAvariaIn]] = None
    vitimas: Optional[list[OcorrenciaVitimaIn]] = None
    testemunhas: Optional[list[OcorrenciaTestemunhaIn]] = None
    autoridades: Optional[list[OcorrenciaAutoridadeIn]] = None


class OcorrenciaRead(OcorrenciaBase, ORMBase):
    id: UUID
    numero: int
    status: StatusOcorrencia
    registrado_por: Optional[UUID] = None
    # Só preenchido por GET /{id} (ver detalhar()) — é o que o formulário usa
    # pra mostrar "Registrada por <nome> — somente leitura" quando quem abre
    # não é o autor nem ADMIN. Nos demais endpoints fica None.
    registrado_por_nome: Optional[str] = None
    criado_em: datetime
    atualizado_em: Optional[datetime] = None
    atualizado_por: Optional[UUID] = None
    finalizada_em: Optional[datetime] = None
    excluida_em: Optional[datetime] = None


class OcorrenciaCompleta(OcorrenciaRead):
    """A ocorrência com todas as listas filhas — usada em GET /ocorrencias/{id}."""
    tipo_ocorrencia: TipoOcorrenciaRead
    analise: Optional[OcorrenciaAnaliseRead] = None
    veiculos_terceiro: list[OcorrenciaVeiculoTerceiroRead] = []
    avarias: list[OcorrenciaAvariaRead] = []
    vitimas: list[OcorrenciaVitimaRead] = []
    testemunhas: list[OcorrenciaTestemunhaRead] = []
    autoridades: list[OcorrenciaAutoridadeRead] = []
    anexos: list[OcorrenciaAnexoRead] = []


class OcorrenciaResumo(BaseModel):
    """Uma linha da listagem — espelha coordenadoria.vw_ocorrencia_resumo."""
    id: UUID
    numero: int
    data_ocorrencia: date
    hora_ocorrencia: time
    tipo_codigo: str
    tipo_nome: str
    prefixo: str
    linha_codigo: Optional[str] = None
    bairro: Optional[str] = None
    status: StatusOcorrencia
    qtd_vitimas: int = 0
    qtd_testemunhas: int = 0
    qtd_autoridades: int = 0
    qtd_anexos: int = 0
    coordenador_nome: Optional[str] = None
    # RE, não o UUID de registrado_por — a view já expõe os dois nomes de
    # coluna prontos (coordenador_nome/coordenador_re) e o frontend já usa
    # RE como identificador de pessoa (mesmo padrão do cabeçalho). Evita
    # expor o UUID interno numa listagem só pra decidir "é meu ou não".
    coordenador_re: Optional[str] = None


class OcorrenciaListaResponse(BaseModel):
    total: int
    itens: list[OcorrenciaResumo]


class MensagemSinistroResponse(BaseModel):
    texto: str
