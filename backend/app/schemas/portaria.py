"""Schemas Pydantic v2 do módulo Portaria — controle de acesso veicular.

Espelha database/migrations/024-modulo-portaria.sql. Campos com conjunto
fechado de opções (Literal) espelham os CHECK constraints da migration —
validação acontece na API antes de chegar no banco, mas a fonte da verdade
dos valores permitidos é o SQL.
"""
from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.placa import PlacaNormalizada, PlacaNormalizadaOpcional
from app.core.registro import ReNormalizado
from app.schemas.base import AuditoriaSchema, ORMBase

Propriedade = Literal["PARTICULAR", "EMPRESA", "TERCEIRO"]
TipoVeiculo = Literal["CARRO", "MOTO", "OUTRO"]
SituacaoVeiculo = Literal["PENDENTE", "AUTORIZADO", "SUSPENSO", "BAIXADO"]
Sentido = Literal["ENTRADA", "SAIDA"]
OrigemMovimento = Literal["MANUAL", "RETROATIVO", "QR", "TAG", "LPR", "CAMERA"]

# normalizar_placa/placa_valida/PlacaNormalizada vivem em app/core/placa.py
# (D10 — mesma regra em portaria/ocorrência/pré-ocorrência, ver docstring
# lá). normalizar_re/ReNormalizado vivem em app/core/registro.py, mesmo
# arranjo — antes os dois viviam duplicados só neste arquivo.


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
    # C1 (migration 039): RE digitado pelo controlador quando a busca por
    # funcionario_id não achou ninguém — regra número um, o registro nunca é
    # recusado. Só faz sentido junto de PARTICULAR sem funcionario_id.
    re_dono_texto: ReNormalizado = Field(None, max_length=20)
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
            if not self.funcionario_id and not self.re_dono_texto:
                raise ValueError("PARTICULAR exige funcionario_id ou re_dono_texto (o dono).")
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
    # C1 (migration 039): snapshot do RE digitado quando funcionario_id não
    # resolveu — ⛔ nunca a fonte de verdade do dono, só fila de Divergências.
    re_dono_texto: Optional[str] = None
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
    # D10: nunca bloqueia o cadastro — só sinaliza pra revisão (migration
    # 031, calculado por placa_valida() em routers/portaria_veiculos.py).
    placa_atipica: bool = False

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
    id é o próprio propósito do endpoint. Nunca cpf/rg/cnh/telefone.

    `auto_autorizado` (Bloco D) é só o booleano — ⛔ sem o nome da função nem
    qualquer outro dado da pessoa. O controlador não precisa saber o cargo de
    ninguém, só se o carro que está cadastrando entra liberado."""

    id: UUID
    re: str
    nome: str
    auto_autorizado: bool = False


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
    # Migration 040/P13 — texto CRU que POST /portaria/ler-placa devolveu,
    # antes da confirmação/correção do controlador. Só faz sentido junto de
    # origem='CAMERA'; a tela manda mesmo quando o controlador corrigiu a
    # placa (P13: a diferença entre os dois campos é o dado que interessa).
    placa_lida_bruta: PlacaNormalizadaOpcional = Field(None, max_length=8)

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
    placa_lida_bruta: Optional[str] = None
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


# ============================================================================
# RECOLHIDA ANORMAL (Bloco F + G) — ônibus que recolhe fora de hora. Evento
# de OPERAÇÃO, não de manutenção. Existe pra melhoria de processo e de
# frota — a associação motorista↔defeito é do VEÍCULO, não da pessoa.
#
# 🔴 Dois schemas de leitura, e isso é a trava de verdade: RecolhidaRead
# nunca carrega motorista/cobrador; RecolhidaGerencialRead acrescenta esses
# campos e só sai de endpoint que exige recolhida_gerencial. A correção de
# escopo do §2.9 NÃO muda essa trava — muda só quem digita o RE (o
# controlador, não mais uma resolução automática pela escala).
#
# 🔧 Bloco G: motivo é mais amplo que "defeito" — colisão e falta de
# motorista/cobrador também são recolhida anormal, e não abrem ficha.
# ============================================================================

StatusRecolhida = Literal["AGUARDANDO", "AVALIADA", "DESCARTADA", "ENCERRADA"]
AvaliacaoRecolhida = Literal["LIBERADO", "RETIDO"]
# Migration 032 — fechamento do ciclo. Só existe quando status=ENCERRADA.
DesfechoRecolhida = Literal["SEM_DEFEITO", "SERVICO_EXECUTADO"]
# PORTARIA = controlador digitou. ESCALA = sugestão da escala confirmada
# sem alterar (só pro motorista — cobrador nunca tem sugestão). ⛔ Sem
# valor MANUAL — §2.9-0 fechou o buraco que esse valor tentava cobrir.
OrigemIdentificacaoRecolhida = Literal["PORTARIA", "ESCALA", "NAO_INFORMADO"]
MotivoRecolhida = Literal["DEFEITO", "COLISAO", "FALTA_MOTORISTA", "FALTA_COBRADOR", "OUTRO"]


class RecolhidaCreate(BaseModel):
    """POST /portaria/recolhidas.

    🔴 §2.9-0: motorista_re/cobrador_re SÃO digitados aqui pelo
    controlador — a separação da regra número um não é sobre o campo, é
    sobre o acumulado (histórico/análise, que exige recolhida_gerencial).
    Ambos opcionais: em branco é NAO_INFORMADO, nunca bloqueia o registro.

    motorista_nome/cobrador_nome só fazem sentido quando o RE digitado não
    resolveu em GET /portaria/resolver-re (§5.3) — alimentam o pré-cadastro
    do Bloco H; se o RE resolveu, o nome vem do cadastro, não do payload.

    tipo_defeito_codigo só é obrigatório quando motivo=DEFEITO (Bloco G).

    🔑 §5.2b: SEM local_codigo — recolhida anormal é sempre de coletivo
    (prefixo/frota), e coletivo sempre entra pelo mesmo portão. O portão é
    consequência de o veículo ser coletivo, não uma classificação própria.
    """

    prefixo: str = Field(..., min_length=1, max_length=10)
    linha_codigo: Optional[str] = Field(None, max_length=20)
    motivo: MotivoRecolhida = "DEFEITO"
    tipo_defeito_codigo: Optional[str] = Field(None, max_length=20)
    relato: Optional[str] = None

    motorista_re: ReNormalizado = Field(None, max_length=20)
    motorista_nome: Optional[str] = Field(None, max_length=120)
    cobrador_re: ReNormalizado = Field(None, max_length=20)
    cobrador_nome: Optional[str] = Field(None, max_length=120)

    @model_validator(mode="after")
    def _tipo_defeito_obrigatorio_quando_defeito(self) -> "RecolhidaCreate":
        if self.motivo == "DEFEITO" and not (self.tipo_defeito_codigo or "").strip():
            raise ValueError("tipo_defeito_codigo é obrigatório quando motivo=DEFEITO.")
        return self


class RecolhidaRead(ORMBase):
    """SEM motorista/cobrador. É o que a portaria e a manutenção recebem."""

    id: UUID
    momento: datetime
    data_referencia: date
    prefixo: str
    onibus_id: Optional[UUID] = None
    linha_codigo: Optional[str] = None
    motivo: MotivoRecolhida
    tipo_defeito_codigo: Optional[str] = None
    relato: Optional[str] = None
    ficha_id: Optional[UUID] = None
    ficha_falhou_motivo: Optional[str] = None
    avaliacao: Optional[AvaliacaoRecolhida] = None
    prazo_minutos: Optional[int] = None
    avaliacao_relato: Optional[str] = None
    avaliado_por: Optional[UUID] = None
    avaliado_em: Optional[datetime] = None
    # Fechamento do ciclo (migration 032) — só preenchido quando status=ENCERRADA.
    desfecho: Optional[DesfechoRecolhida] = None
    encerramento_relato: Optional[str] = None
    encerrado_por: Optional[UUID] = None
    encerrado_em: Optional[datetime] = None
    status: StatusRecolhida
    registrado_por: UUID
    criado_em: datetime


class RecolhidaGerencialRead(RecolhidaRead):
    """Acrescenta motorista/cobrador/origem — só de endpoint que exige
    recolhida_gerencial (§2.5). ⛔ Nunca devolvido de endpoint sem essa
    exigência — esconder no frontend não é proteção, o dado não pode nem
    sair da API."""

    motorista_re: Optional[str] = None
    motorista_nome: Optional[str] = None
    cobrador_re: Optional[str] = None
    cobrador_nome: Optional[str] = None
    origem_identificacao: OrigemIdentificacaoRecolhida


class RecolhidaAvaliacaoRequest(BaseModel):
    """PATCH /portaria/recolhidas/{id}/avaliacao — recurso `manutencao`
    escrever (já existente, nenhum recurso novo pra isso)."""

    avaliacao: AvaliacaoRecolhida
    prazo_minutos: Optional[int] = Field(None, ge=0)
    avaliacao_relato: Optional[str] = None

    @model_validator(mode="after")
    def _prazo_obrigatorio_quando_liberado(self) -> "RecolhidaAvaliacaoRequest":
        if self.avaliacao == "LIBERADO" and self.prazo_minutos is None:
            raise ValueError("prazo_minutos é obrigatório quando avaliacao=LIBERADO.")
        return self


class RecolhidaEncerramentoRequest(BaseModel):
    """PATCH /portaria/recolhidas/{id}/encerramento — recurso `manutencao`
    escrever (mesmo de avaliar; quem avalia é quem encerra). Migration 032:
    fechamento do ciclo, dois passos de propósito (avaliar != encerrar) —
    ver router pra regra de status exigido."""

    desfecho: DesfechoRecolhida
    encerramento_relato: Optional[str] = None


class ContagemPendentesResponse(BaseModel):
    total: int


class ResolverPrefixoResponse(BaseModel):
    """🔧 Adição fora do desenho original do prompt (§2.6): a tela da
    portaria precisa mostrar 'prefixo cadastrado' ou 'não cadastrado' ao
    sair do campo, antes de enviar — sem isso não tem como a UI saber.

    §2.9-0: também devolve a SUGESTÃO de motorista pela escala (pré-
    preenchimento, nunca fonte única) — só motorista, nunca cobrador (a
    escala não tem esse campo). ⛔ Nunca cpf/rg/cnh/telefone."""

    encontrado: bool
    placa: Optional[str] = None
    motorista_re_sugerido: Optional[str] = None
    motorista_nome_sugerido: Optional[str] = None


class RecursosPortariaResponse(BaseModel):
    """GET /portaria/recursos (PROMPT-leitura-placa-engine.md, R6) — flags
    de recursos opcionais da Portaria. Existe pra tela decidir sem depender
    do localStorage da sessão: sessão aberta antes de uma flag mudar no
    servidor não veria a mudança (mesma armadilha de permissão nova, já
    documentada no projeto). Nasce genérico (objeto, não um único bool)
    pra um próximo recurso entrar como campo novo, sem endpoint novo."""

    leitura_placa_ativa: bool


class LeituraPlacaResponse(BaseModel):
    """POST /portaria/ler-placa — motor pluggável (ver
    app/services/leitura_placa.py). `placa_lida=None` é resultado válido
    (imagem sem placa nenhuma legível), nunca erro 500 — P7 do
    PROMPT-leitura-placa.md: a câmera é aceleração, nunca pré-requisito, e
    o card de erro cai de volta pra digitação. ⛔ Sem indicador de formato
    aqui (D10 já resolve isso na normalização — `placa_valida()` nunca
    recusa)."""

    placa_lida: Optional[str] = None
    confianca: float = Field(0.0, ge=0.0, le=1.0)


class ResolverReResponse(BaseModel):
    """GET /portaria/resolver-re (§5.3) — confirma visualmente quem é o RE
    digitado, sem devolver nada além do necessário. Procura em
    public.funcionario e public.motorista (são dois cadastros de pessoa
    distintos — ver services/identidade.py). ⛔ Nunca cpf/rg/cnh/telefone
    nem histórico. ⛔ Nunca 404 — RE inexistente é resultado válido."""

    encontrado: bool = False
    nome: Optional[str] = None
    origem: Optional[Literal["FUNCIONARIO", "MOTORISTA"]] = None
    ativo: Optional[bool] = None


class RecolhidaAnaliseItem(BaseModel):
    chave: str
    total: int


class RecolhidaAnaliseResponse(BaseModel):
    """§2.7 — agregados por período, ordenados por contagem decrescente.
    ⛔ Sem gráfico nesta fase — tabela resolve e alimenta o Pareto fora do
    sistema. Finalidade: melhoria de processo e de frota, nunca avaliação
    de pessoa."""

    por_prefixo: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    por_linha: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    por_motorista: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    por_tipo_defeito: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    por_faixa_horario: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    # Bloco G — o corte mais valioso: separa problema de frota (DEFEITO/
    # COLISAO) de problema de escala (FALTA_MOTORISTA/FALTA_COBRADOR).
    por_motivo: list[RecolhidaAnaliseItem] = Field(default_factory=list)
    tempo_medio_avaliacao_minutos: Optional[float] = None


# ============================================================================
# AVARIA_SAIDA (Bloco G, migration 036) — dano visto na conferência de saída
# ============================================================================

class AvariaSaidaCreate(BaseModel):
    """POST /portaria/avarias. `motorista_re` é opcional (regra número um —
    nunca bloqueia); `motorista_nome` só é usado quando o RE digitado não
    resolve em public.funcionario/motorista (aí vira snapshot do que o
    controlador informou, e alimenta o pré-cadastro do Bloco H)."""

    prefixo: str = Field(..., min_length=1, max_length=10)
    motorista_re: ReNormalizado = Field(None, max_length=20)
    motorista_nome: Optional[str] = Field(None, max_length=120)
    descricao: str = Field(..., min_length=1)


class AvariaSaidaRead(ORMBase):
    id: UUID
    prefixo: str
    data_servico: date
    ocorrido_em: datetime
    motorista_re: Optional[str] = None
    motorista_nome: Optional[str] = None
    descricao: str
    registrado_por: UUID
    criado_em: datetime
    expira_em: datetime

