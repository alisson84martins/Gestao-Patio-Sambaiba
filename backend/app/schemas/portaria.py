"""Schemas Pydantic v2 do módulo Portaria — controle de acesso veicular.

Espelha database/migrations/024-modulo-portaria.sql. Campos com conjunto
fechado de opções (Literal) espelham os CHECK constraints da migration —
validação acontece na API antes de chegar no banco, mas a fonte da verdade
dos valores permitidos é o SQL.
"""
import re
from datetime import date, datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.schemas.base import AuditoriaSchema, ORMBase

Propriedade = Literal["PARTICULAR", "EMPRESA", "TERCEIRO"]
TipoVeiculo = Literal["CARRO", "MOTO", "OUTRO"]
SituacaoVeiculo = Literal["PENDENTE", "AUTORIZADO", "SUSPENSO", "BAIXADO"]
Sentido = Literal["ENTRADA", "SAIDA"]
OrigemMovimento = Literal["MANUAL", "RETROATIVO", "QR", "TAG", "LPR"]


_PLACA_LIMPA_RE = re.compile(r"[^A-Z0-9]")


def normalizar_placa(v: Optional[str]) -> Optional[str]:
    """Maiúscula, sem hífen/espaço/ponto. 'abc-1d23' -> 'ABC1D23'.

    D10: não valida formato (nem AAA9999, nem Mercosul AAA9A99) — placa de
    outro país ou provisória existe, e a regra número um do módulo (nunca
    impedir um registro) vale também para o cadastro e a busca.
    """
    if v is None:
        return v
    return _PLACA_LIMPA_RE.sub("", v.strip().upper())


PlacaNormalizada = Annotated[str, BeforeValidator(normalizar_placa)]


# ============================================================================
# EMPRESA_TERCEIRA (D5)
# ============================================================================

class EmpresaTerceiraCreate(BaseModel):
    nome: str = Field(..., min_length=1, max_length=120)
    cnpj: Optional[str] = Field(None, max_length=18)
    observacao: Optional[str] = None


class EmpresaTerceiraRead(ORMBase):
    id: UUID
    nome: str
    cnpj: Optional[str] = None
    observacao: Optional[str] = None
    ativo: bool
    criado_em: datetime


# ============================================================================
# VEICULO (D1, D6, D7, D12)
# ============================================================================

class VeiculoCreate(BaseModel):
    """Cadastro pelo controlador — nasce sempre PENDENTE (D6). `situacao`
    não existe neste schema de propósito: quem cadastra não autoriza."""

    propriedade: Propriedade
    funcionario_id: Optional[UUID] = None
    empresa_terceira_id: Optional[UUID] = None
    placa: PlacaNormalizada = Field(..., max_length=8)
    tipo: TipoVeiculo = "CARRO"
    marca_modelo: Optional[str] = Field(None, max_length=60)
    cor: Optional[str] = Field(None, max_length=30)
    # None = backend decide (TRUE se EMPRESA, FALSE senão — D7).
    exige_hodometro: Optional[bool] = None
    observacao: Optional[str] = None

    @model_validator(mode="after")
    def _valida_dono(self) -> "VeiculoCreate":
        # Mesmo par de exigências do CHECK ck_veiculo_dono da migration —
        # falhar aqui como 422 é mais claro pro chamador que um IntegrityError.
        if self.propriedade == "PARTICULAR":
            if not self.funcionario_id:
                raise ValueError("PARTICULAR exige funcionario_id (o dono).")
            if self.empresa_terceira_id:
                raise ValueError("PARTICULAR não pode ter empresa_terceira_id.")
        elif self.propriedade == "EMPRESA":
            if self.funcionario_id or self.empresa_terceira_id:
                raise ValueError("EMPRESA não tem dono individual nem empresa terceira.")
        elif self.propriedade == "TERCEIRO":
            if not self.empresa_terceira_id:
                raise ValueError("TERCEIRO exige empresa_terceira_id.")
            if self.funcionario_id:
                raise ValueError("TERCEIRO não pode ter funcionario_id.")
        return self


class VeiculoUpdate(BaseModel):
    """PATCH /portaria/veiculos/{id} — só dados cadastrais. 🔴 Não tem (nem
    aceita) `situacao`/`situacao_por`/`situacao_em`/`situacao_motivo` de
    propósito: mudar situação é o endpoint /situacao, outro recurso do RBAC
    (autorizacao_veicular). extra='forbid' garante 422 se alguém tentar
    mandar esses campos aqui, em vez de ignorá-los em silêncio."""

    model_config = ConfigDict(extra="forbid")

    placa: Optional[PlacaNormalizada] = Field(None, max_length=8)
    tipo: Optional[TipoVeiculo] = None
    marca_modelo: Optional[str] = Field(None, max_length=60)
    cor: Optional[str] = Field(None, max_length=30)
    exige_hodometro: Optional[bool] = None
    observacao: Optional[str] = None
    # 🔴 Sem `ativo` de propósito (revisão 20/08 — §3.6-A). Desativar é ato
    # de AUTORIZAÇÃO, não de cadastro: com `ativo` aqui, o controlador (que
    # só tem escrever em `veiculo_portaria`) conseguia apagar um veículo
    # AUTORIZADO da busca/listagem sem ter `autorizacao_veicular` — e nem
    # encarregado nem gerência conseguiam desfazer pela API, porque os
    # endpoints de situação filtravam `ativo` também. BAIXADO já cobre a
    # intenção legítima de "esse carro não vale mais".


class VeiculoRead(ORMBase, AuditoriaSchema):
    id: UUID
    propriedade: Propriedade
    funcionario_id: Optional[UUID] = None
    empresa_terceira_id: Optional[UUID] = None
    placa: str
    tipo: TipoVeiculo
    marca_modelo: Optional[str] = None
    cor: Optional[str] = None
    exige_hodometro: bool
    situacao: SituacaoVeiculo
    situacao_por: Optional[UUID] = None
    situacao_em: Optional[datetime] = None
    situacao_motivo: Optional[str] = None
    observacao: Optional[str] = None
    ativo: bool

    # Resolvidos pelo backend via join — não existem como coluna própria.
    funcionario_nome: Optional[str] = None
    funcionario_re: Optional[str] = None
    empresa_terceira_nome: Optional[str] = None
    criado_por_nome: Optional[str] = None
    situacao_por_nome: Optional[str] = None


class VeiculoDivergenciaRead(VeiculoRead):
    """D13 — painel 'veículos AUTORIZADOS de pessoas que não estão ATIVAS'.
    Só mostra; não decide nada."""

    funcionario_status: str


class VeiculoSituacaoUpdate(BaseModel):
    """PATCH /portaria/veiculos/{id}/situacao — exige autorizacao_veicular
    escrever. `motivo` obrigatório em SUSPENSO/BAIXADO (D11); opcional em
    AUTORIZADO."""

    situacao: Literal["AUTORIZADO", "SUSPENSO", "BAIXADO"]
    motivo: Optional[str] = None

    @model_validator(mode="after")
    def _motivo_obrigatorio_para_suspender_ou_baixar(self) -> "VeiculoSituacaoUpdate":
        if self.situacao in ("SUSPENSO", "BAIXADO") and not (self.motivo or "").strip():
            raise ValueError(f"motivo é obrigatório para {self.situacao}.")
        return self


class VeiculoSituacaoHistRead(ORMBase):
    id: UUID
    veiculo_id: UUID
    situacao_de: Optional[SituacaoVeiculo] = None
    situacao_para: SituacaoVeiculo
    motivo: Optional[str] = None
    decidido_por: UUID
    decidido_por_nome: Optional[str] = None
    decidido_em: datetime


class FuncionarioPortariaBusca(BaseModel):
    """Autocomplete de RE/nome -> id, só pra resolver `funcionario_id` no
    cadastro de veículo PARTICULAR (Bloco C). Existe porque /funcionarios/busca
    (app/routers/funcionarios.py) é gated por `exige("ocorrencia")` — recurso
    que o CONTROLADOR_ACESSO não tem — e de propósito não devolve `id`
    (FuncionarioBusca vira RE+nome snapshot em ocorrência, nunca FK). Aqui o
    id é o próprio propósito do endpoint. Nunca cpf/rg/cnh/telefone."""

    id: UUID
    re: str
    nome: str


class BloquearPorReRequest(BaseModel):
    re: str = Field(..., min_length=1, max_length=20)
    motivo: str = Field(..., min_length=1)


class BloquearPorReResponse(BaseModel):
    funcionario_id: UUID
    funcionario_nome: str
    re: str
    veiculos_suspensos: list[VeiculoRead]
    # §3.6-D.1: quem já estava SUSPENSO não entra em veiculos_suspensos de
    # novo — rebloquear gerava linha SUSPENSO→SUSPENSO no histórico e
    # sobrescrevia o motivo anterior. Separado aqui pra tela dizer o que fez
    # e o que não precisou fazer.
    ja_suspensos: list[VeiculoRead] = Field(default_factory=list)


# ============================================================================
# MOVIMENTO (D2, D3, D4, D9, D16)
# ============================================================================

class MovimentoCreate(BaseModel):
    sentido: Sentido
    local_codigo: str = "LEVES"
    placa: PlacaNormalizada = Field(..., max_length=8)

    # Condutor desta passagem (D2) — quando é um funcionário cadastrado.
    funcionario_id: Optional[UUID] = None
    # Snapshot livre — usado quando não há funcionario_id (visitante,
    # terceiro avulso) ou para sobrepor o nome lido na hora.
    re_registrado: Optional[str] = Field(None, max_length=20)
    nome_registrado: Optional[str] = Field(None, max_length=120)

    terceiro_nome: Optional[str] = Field(None, max_length=120)
    terceiro_destino: Optional[str] = Field(None, max_length=120)
    terceiro_empresa: Optional[str] = Field(None, max_length=120)

    hodometro_km: Optional[int] = Field(None, ge=0)
    observacao: Optional[str] = None

    # Conveniência (D3) — só relevante em SAIDA; se não bater com uma
    # ENTRADA da mesma placa, o router ignora o vínculo e registra assim
    # mesmo (o par é conveniência, não requisito).
    movimento_entrada_id: Optional[UUID] = None

    origem: OrigemMovimento = "MANUAL"
    # Só usado (e exigido) quando origem == RETROATIVO — D16: é hora que
    # uma PESSOA leu do papel e digitou; o router interpreta em
    # FUSO_OPERACAO antes de gravar, nunca um instante do sistema.
    momento: Optional[datetime] = None

    @model_validator(mode="after")
    def _retroativo_exige_hora_e_observacao(self) -> "MovimentoCreate":
        if self.origem == "RETROATIVO":
            if self.momento is None:
                raise ValueError("origem RETROATIVO exige `momento` (hora lida do papel).")
            if not (self.observacao or "").strip():
                raise ValueError("origem RETROATIVO exige `observacao`.")
        return self


class MovimentoRead(ORMBase):
    id: UUID
    local_codigo: str
    sentido: Sentido
    momento: datetime
    data_referencia: date
    veiculo_id: Optional[UUID] = None
    funcionario_id: Optional[UUID] = None
    placa_registrada: str
    re_registrado: Optional[str] = None
    nome_registrado: Optional[str] = None
    terceiro_nome: Optional[str] = None
    terceiro_destino: Optional[str] = None
    terceiro_empresa: Optional[str] = None
    hodometro_km: Optional[int] = None
    cadastrado: bool
    origem: OrigemMovimento
    movimento_entrada_id: Optional[UUID] = None
    registrado_por: UUID
    observacao: Optional[str] = None
    criado_em: datetime


class MovimentoCreateResponse(MovimentoRead):
    # Regra número um: o registro nunca é recusado — avisos substituem
    # bloqueio (veículo suspenso/baixado, hodômetro faltando, etc.).
    avisos: list[str] = Field(default_factory=list)


# ============================================================================
# BUSCA — autocomplete (placa/RE/nome) e leitura de QR no Bloco E reusam o
# mesmo payload (D15: "cai exatamente no mesmo card de confirmação").
#
# §3.6-C: devolve CANDIDATOS, nunca um palpite. Antes da revisão de 20/08 a
# busca por prefixo de placa e por nome terminava em .limit(1) — duas placas
# batendo o mesmo prefixo (ou dois "Silva") faziam o app escolher uma em
# silêncio, e o controlador podia confirmar o carro errado sem perceber
# nada de errado na tela.
# ============================================================================

class VeiculoCandidato(BaseModel):
    veiculo: VeiculoRead
    dentro: bool = False
    ultimo_movimento: Optional[MovimentoRead] = None


class BuscaVeiculoResponse(BaseModel):
    # exato=True (placa completa bateu) -> sempre 1 item; a tela vai direto
    # ao card de confirmação (caminho de <=8s). exato=False -> até 8
    # candidatos pra tocar; [] é resultado válido (⛔ nunca 404 — regra
    # número um também vale pra busca).
    candidatos: list[VeiculoCandidato] = Field(default_factory=list)
    exato: bool = False


# ============================================================================
# "DENTRO AGORA" (D3, D17) — nunca soma dentro + sem_saida no mesmo número
# ============================================================================

class PortariaDentroResponse(BaseModel):
    dentro: list[MovimentoRead]
    sem_saida: list[MovimentoRead]
    horas: int


# ============================================================================
# CREDENCIAL (Bloco E) — QR do veículo. `codigo` é token opaco; nunca carrega
# placa/RE/nome (ver migration 025). O card de confirmação da leitura por QR
# reusa BuscaVeiculoResponse acima (D15) — não tem schema próprio.
# ============================================================================

TipoCredencial = Literal["QR", "TAG", "CARTAO"]


class CredencialRead(ORMBase):
    id: UUID
    veiculo_id: UUID
    tipo: TipoCredencial
    codigo: str
    emitida_em: datetime
    emitida_por: Optional[UUID] = None
    revogada_em: Optional[datetime] = None
    revogada_por: Optional[UUID] = None
    motivo_revogacao: Optional[str] = None
    ativa: bool


class CredencialEmitirRequest(BaseModel):
    """POST /portaria/veiculos/{id}/credencial. `motivo` só é obrigatório
    quando já existe credencial ativa (reemissão) — o router valida isso
    olhando o estado atual, não dá pra expressar como regra estática aqui."""

    motivo: Optional[str] = None


class CredencialRevogarRequest(BaseModel):
    motivo: str = Field(..., min_length=1)
