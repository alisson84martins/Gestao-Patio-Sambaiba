"""Modelos do schema portaria: controle de acesso veicular.

Espelha fielmente database/migrations/024-modulo-portaria.sql. Regra de
fronteira (mesma da migration 012 — Coordenadoria): Portaria é módulo
separado do Pátio, sem FK para tabela operacional (onibus, linha, fila,
alocacao_patio, alerta, escala). A única FK para fora do schema portaria é
funcionario (public, identidade compartilhada por toda a Suite) — escrita
sem prefixo de schema, mesmo padrão de app/models/ocorrencia.py e
pre_ocorrencia.py (Funcionario não declara __table_args__ com schema, então
"funcionario.id" é a chave correta no MetaData; "public.funcionario.id"
não bate com a tabela já registrada).
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.mixins import AuditoriaMixin

SCHEMA = "portaria"


class PortariaLocal(Base):
    __tablename__ = "local"
    __table_args__ = {"schema": SCHEMA}

    codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PortariaSetor(Base):
    """Para onde a visita vai dentro da garagem (R4/R6). Espelha
    database/migrations/041-portaria-frota-reservado-e-setor.sql — mesmo
    desenho de PortariaLocal. ⛔ Não é atributo do veículo: mora em
    MovimentoPortaria.setor_codigo."""

    __tablename__ = "setor"
    __table_args__ = {"schema": SCHEMA}

    codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EmpresaTerceira(Base):
    __tablename__ = "empresa_terceira"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    cnpj: Mapped[Optional[str]] = mapped_column(String(18), nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class VeiculoPortaria(Base, AuditoriaMixin):
    """Cadastro de veículo (D1: discriminador `propriedade`, não três
    tabelas). Nasce PENDENTE — quem cadastra (controlador) não é quem
    autoriza (encarregado/gerência); ver D6."""

    __tablename__ = "veiculo"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    propriedade: Mapped[str] = mapped_column(String(12), nullable=False)
    # Dono do veículo — só PARTICULAR. NUNCA "quem dirige agora": isso mora
    # em MovimentoPortaria.funcionario_id (D2).
    funcionario_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    empresa_terceira_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.empresa_terceira.id"), nullable=True
    )
    # C1 (migration 039): SNAPSHOT do RE digitado pelo controlador quando o
    # dono PARTICULAR não estava no cadastro de funcionário — regra número
    # um, o registro nunca é recusado. ⛔ Nunca a fonte de verdade do dono —
    # funcionario_id é. Vira histórico quando a pessoa é promovida (Bloco F).
    re_dono_texto: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    placa: Mapped[str] = mapped_column(String(8), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False, default="CARRO")
    marca_modelo: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    cor: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # D7: coluna, não regra chumbada — o backend decide o default TRUE para
    # propriedade=EMPRESA na criação, mas o campo continua editável.
    exige_hodometro: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    situacao: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDENTE")
    situacao_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    situacao_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    situacao_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # D10 (migration 031): nunca bloqueia o cadastro por causa do formato
    # da placa — só sinaliza pra revisão. Calculado por placa_valida() em
    # routers/portaria_veiculos.py a cada create/update de placa.
    placa_atipica: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ⛔ Sem campo `validade` — D12: autorização não vence.


class VeiculoSituacaoHist(Base):
    """Extrato de toda mudança de situação (D14). Escrita é responsabilidade
    do BACKEND a cada troca, nunca de trigger — ver routers/portaria_veiculos.py."""

    __tablename__ = "veiculo_situacao_hist"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    veiculo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.veiculo.id", ondelete="CASCADE"), nullable=False
    )
    situacao_de: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    situacao_para: Mapped[str] = mapped_column(String(12), nullable=False)
    motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decidido_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    decidido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MovimentoPortaria(Base):
    """Registro de entrada/saída — a tabela que importa. "Dentro agora" (D3)
    é derivado do último movimento por placa via vw_dentro, nunca de um par
    entrada/saída. Migration 041 (D18): movimento de RESERVADO (prefixo
    preenchido) e de FROTA DE APOIO (veiculo.propriedade='EMPRESA') ficam
    fora de "dentro agora" e de contador algum — ver
    routers/portaria.py::_dentro_e_sem_saida."""

    __tablename__ = "movimento"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    local_codigo: Mapped[str] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.local.codigo"), nullable=False, default="LEVES"
    )
    sentido: Mapped[str] = mapped_column(String(8), nullable=False)
    # Instante que o SISTEMA carimba — UTC (D16). Não confundir com
    # data_referencia, que é dia que uma PESSOA lê.
    momento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 🔴 D16: sempre preenchida explicitamente pelo router a partir de
    # FUSO_OPERACAO (app.core.config) — nunca deixada cair no default do
    # banco nem calculada com date.today()/get_data_servico(). O default do
    # SQL é só rede de segurança para INSERT direto.
    data_referencia: Mapped[date] = mapped_column(Date, nullable=False)

    veiculo_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.veiculo.id"), nullable=True
    )
    # CONDUTOR desta passagem (D2) — nunca o dono do veículo.
    funcionario_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )

    # Snapshots (D4), sempre preenchidos, mesmo com veiculo_id. Migration 041
    # (R1.b): aceita NULL só na passagem de RESERVADO, onde `prefixo` é quem
    # identifica o coletivo — ck_movimento_identificacao garante placa OU
    # prefixo, nunca os dois nulos.
    placa_registrada: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    re_registrado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    nome_registrado: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Terceiro (D5) — condutor da visita é texto puro, nunca FK.
    terceiro_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_destino: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_empresa: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Migration 041 (R1/P4): prefixo do coletivo RESERVADO — passagem, nunca
    # cadastro de veículo. ⛔ SEM FK (regra de fronteira), mesmo arranjo de
    # RecolhidaAnormal.prefixo/onibus_id. NULL fora de passagem de reservado.
    # D18: movimento com prefixo não entra em "dentro agora" nem em
    # contador algum — ver routers/portaria.py::_dentro_e_sem_saida.
    prefixo: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    onibus_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # Migration 041 (R4/R6): setor de destino da VISITA — opcional, nunca
    # atributo do veículo. NULL quando o controlador digitou fora da lista
    # (vale terceiro_destino, texto) ou quando não se aplica.
    setor_codigo: Mapped[Optional[str]] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.setor.codigo"), nullable=True
    )

    hodometro_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    cadastrado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    origem: Mapped[str] = mapped_column(String(12), nullable=False, default="MANUAL")
    # Migration 040 — texto CRU que o reconhecimento de placa por câmera
    # devolveu, antes da confirmação/correção do controlador (P13 do
    # PROMPT-leitura-placa.md). NULL fora de origem='CAMERA'.
    placa_lida_bruta: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    movimento_entrada_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.movimento.id"), nullable=True
    )

    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Credencial(Base):
    """QR/TAG/cartão do veículo (Bloco E). Espelha
    database/migrations/025-portaria-credencial.sql. Tabela, não coluna em
    VeiculoPortaria — reemissão é rotina (adesivo descola/desbota/carro é
    vendido) e o código antigo precisa parar de valer sem apagar o registro
    de que existiu."""

    __tablename__ = "credencial"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    veiculo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.veiculo.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(10), nullable=False, default="QR")
    # Token opaco (secrets.token_urlsafe(16)) — NUNCA placa/RE/nome/URL. Ver
    # comentário da migration 025 sobre por quê.
    codigo: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    emitida_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    emitida_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    revogada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revogada_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    motivo_revogacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # FALSE = revogada. ⚠️ Nunca impede o veículo de entrar — proibição é
    # VeiculoPortaria.situacao, não isto.
    ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RecolhidaAnormal(Base):
    """Ônibus que recolhe fora de hora (Bloco F + Bloco G). Espelha
    database/migrations/026-recolhida-anormal.sql. Evento de OPERAÇÃO, não
    de manutenção — existe pra melhoria de processo e de frota, nunca pra
    avaliar pessoa.

    🔴 motorista_re/motorista_nome/cobrador_re/cobrador_nome: o controlador
    DIGITA os dois REs (§2.9-0 — ele está com o carro na frente, é a melhor
    fonte do dado). O que a tela da portaria nunca recebe é o ACUMULADO —
    esses campos nunca saem de um endpoint que não exige recolhida_gerencial.

    🔧 motivo (Bloco G): recolhida nem sempre é defeito — pode ser colisão
    ou falta de motorista/cobrador. Só motivo=DEFEITO abre ficha de
    manutenção automaticamente; tipo_defeito_codigo só é exigido nesse caso.

    ⚠️ ficha_id é a ÚNICA FK deste módulo pra fora do schema portaria (além
    de funcionario) — ver services/manutencao_recolhida.py.

    🔑 §5.2b: SEM local_codigo de propósito. Recolhida anormal é sempre de
    COLETIVO (identificado por prefixo/número de frota, nunca placa) — o
    discriminador é a natureza do veículo, não o portão por onde ele passa.
    O portão é consequência (todo coletivo entra pelos mesmos pesados), não
    classificação. `portaria.local` continua servindo só o movimento de
    veículo de passeio (portaria.veiculo/MovimentoPortaria), onde local
    (LEVES/PESADOS) é informação real."""

    __tablename__ = "recolhida_anormal"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    momento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_referencia: Mapped[date] = mapped_column(Date, nullable=False)

    # ── o que a PORTARIA informa ────────────────────────────────────────
    prefixo: Mapped[str] = mapped_column(String(10), nullable=False)
    # ⛔ Sem FK (regra de fronteira) — resolvido pelo backend quando existe
    # ônibus cadastrado com aquele número de frota.
    onibus_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Bloco G: DEFEITO/COLISAO/FALTA_MOTORISTA/FALTA_COBRADOR/OUTRO.
    motivo: Mapped[str] = mapped_column(String(20), nullable=False, default="DEFEITO")
    # Só obrigatório (CHECK no banco) quando motivo=DEFEITO.
    tipo_defeito_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    relato: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── digitado pelo CONTROLADOR (§2.9-0) — 🔴 GERENCIAL só na LEITURA:
    # nunca exposto por endpoint que não exige recolhida_gerencial ────────
    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # ⚠️ Nunca sugerido automaticamente — a tabela escala não tem campo de
    # cobrador (decisão de 21/08/2026) — mas o controlador digita mesmo assim.
    cobrador_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cobrador_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # PORTARIA (digitado) | ESCALA (sugestão confirmada sem alterar) | NAO_INFORMADO.
    origem_identificacao: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NAO_INFORMADO"
    )

    # ── ligação com a manutenção — ÚNICA exceção à regra de fronteira ───
    ficha_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ficha_manutencao.id"), nullable=True
    )
    ficha_falhou_motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── resposta da MANUTENÇÃO ──────────────────────────────────────────
    avaliacao: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    prazo_minutos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    avaliacao_relato: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avaliado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    avaliado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── fechamento do ciclo — resposta da MANUTENÇÃO ao ENCERRAR (migration
    # 032): diferente de avaliação (LIBERADO/RETIDO), que só diz se o carro
    # volta — desfecho diz se havia defeito de verdade e efetivamente fecha
    # a ficha que a recolhida abriu. Só preenchido quando status=ENCERRADA
    # (CHECK ck_recolhida_desfecho_status).
    desfecho: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    encerramento_relato: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encerrado_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    encerrado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(12), nullable=False, default="AGUARDANDO")

    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AvariaZona(Base):
    """Catálogo de partes do ônibus onde uma avaria pode ser marcada.
    Espelha database/migrations/042-avaria-mapa-e-deduplicacao.sql. Tabela,
    não ENUM — cresce por INSERT, sem pegadinha de ALTER TYPE (mesmo motivo
    de PortariaLocal/PortariaSetor)."""

    __tablename__ = "avaria_zona"
    __table_args__ = {"schema": SCHEMA}

    codigo: Mapped[str] = mapped_column(String(30), primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    # Onde desenhar no mapa (P3): FRENTE|TRASEIRA|LATERAL_ESQ|LATERAL_DIR|INTERNO|TETO
    vista: Mapped[str] = mapped_column(String(20), nullable=False)
    # Vocabulário da coordenadoria (REGIOES_AVARIA / migration 012) —
    # coluna de PROPÓSITO diferente de `vista`, não fundir as duas.
    regiao_ocorrencia: Mapped[str] = mapped_column(String(20), nullable=False)
    # NULL = zona de qualquer carro. ARTICULADO/ELETRICO = só entra no
    # catálogo de um carro com essa característica (Fase 4, migration 044).
    requer_caracteristica: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AvariaTipo(Base):
    """Catálogo de tipos de dano. Espelha a migration 042 — mesmo motivo de
    tabela (não ENUM) de AvariaZona."""

    __tablename__ = "avaria_tipo"
    __table_args__ = {"schema": SCHEMA}

    codigo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(40), nullable=False)
    # TRUE para danos graves (amassado/quebrado/faltando/trincado) — decide,
    # na tela (P3), se confirmar uma avaria aberta há mais de 30 dias pede
    # um segundo toque.
    exige_conferencia: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AvariaSaida(Base):
    """Avaria vista na conferência de saída da frota (Bloco G). Espelha
    database/migrations/036-avaria-saida-frota.sql, evoluída pela 042
    (mapa clicável, deduplicação, ciclo de vida — Fase 1 de
    PROMPT-avaria-mapa-visual-2026-09-15.md). Não é recolhida (o carro está
    saindo, não voltando) e não é ocorrência (não houve sinistro).

    🔴 ENQUADRAMENTO PROBATÓRIO (042): a marcação PROTEGE o motorista que
    pegou o carro já avariado; a ausência dela o RESPONSABILIZA. Por isso a
    retenção é de 365 dias (era 60 na 036) e por isso avaria_constatacao
    (abaixo) é imutável e nunca é apagada em cascata.

    SNAPSHOT de texto, não FK (mesma decisão de recolhida_anormal, migration
    026): prefixo e motorista_nome são o que o controlador VIU naquele
    momento; renumeração de frota não pode reescrever o passado.

    Retenção (365 dias a partir da 042, `expira_em`) — o projeto não tem
    scheduler (mesma decisão da migration 028), então o expurgo é por
    FILTRO (`routers/portaria_avarias.py::listar_avarias` exige
    `expira_em > NOW()`), nunca por job de background. Recalculada pelo
    router a cada constatação e no encerramento — ver
    services/avarias.py::calcular_expira_em."""

    __tablename__ = "avaria_saida"
    __table_args__ = (
        # Espelha uq_avaria_aberta_por_zona_tipo (migration 042, seção 5) —
        # a trava contra duplicata. Parcial: só entre linhas ABERTA e fora
        # de NAO_INFORMADA (compat com a tela antiga, que nunca dedup).
        Index(
            "uq_avaria_aberta_por_zona_tipo", "prefixo", "zona_codigo", "tipo_codigo",
            unique=True,
            postgresql_where=text("status = 'ABERTA' AND zona_codigo <> 'NAO_INFORMADA'"),
            sqlite_where=text("status = 'ABERTA' AND zona_codigo <> 'NAO_INFORMADA'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    prefixo: Mapped[str] = mapped_column(String(10), nullable=False)
    # Ciclo operacional (get_data_servico(), vira às 20h) — ⛔ divergência
    # proposital de portaria.movimento.data_referencia (D9, 24h corrido,
    # migration 024): a avaria acompanha o dia de OPERAÇÃO do carro.
    data_servico: Mapped[date] = mapped_column(Date, nullable=False)
    ocorrido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # NOT NULL desde a 036 — vira a observação livre da abertura a partir
    # da 042 (⛔ nunca dropar, tem dado de produção).
    descricao: Mapped[str] = mapped_column(Text, nullable=False)

    # ── 042: mapa clicável + deduplicação ───────────────────────────────
    zona_codigo: Mapped[str] = mapped_column(
        String(30), ForeignKey(f"{SCHEMA}.avaria_zona.codigo"), nullable=False
    )
    tipo_codigo: Mapped[str] = mapped_column(
        String(20), ForeignKey(f"{SCHEMA}.avaria_tipo.codigo"), nullable=False
    )
    severidade: Mapped[str] = mapped_column(String(10), nullable=False, default="LEVE")
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="ABERTA")
    primeira_vez_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ultima_vez_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    vezes_vista: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    encerrada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    encerrada_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    encerramento: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    encerramento_nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 365 dias a partir da 042 (era 60 na 036) — ver COMMENT da migration.
    expira_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(days=365),
    )


class AvariaConstatacao(Base):
    """A DECLARAÇÃO: fulano pegou este carro nesta data e o dano já estava
    aqui. É a PROVA, e é IMUTÁVEL — este router (routers/portaria_avarias.py)
    nunca expõe PUT nem DELETE nesta tabela; correção só por anulação
    (anulada_em/anulada_por/anulacao_nota), que preserva a linha. Espelha
    database/migrations/042-avaria-mapa-e-deduplicacao.sql.

    ON DELETE RESTRICT em avaria_id (⛔ nunca CASCADE): apagar a avaria
    levaria junto a defesa de todos os motoristas que passaram por ela."""

    __tablename__ = "avaria_constatacao"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    avaria_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.avaria_saida.id", ondelete="RESTRICT"), nullable=False
    )
    data_servico: Mapped[date] = mapped_column(Date, nullable=False)
    ocorrido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    motorista_re: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motorista_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    piorou: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # SNAPSHOT da linha (fiscalizacao.linha) que o carro ia operar nesta
    # saída — ⛔ SEM FK de propósito: aceito fora do catálogo, o dono da
    # trava é o seletor na tela (P3), não o banco (teste 17 da Fase 1).
    linha_codigo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    anulada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    anulada_por: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=True
    )
    anulacao_nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AvariaContestacao(Base):
    """O controlador diz "não vi mais essa". A avaria continua ABERTA — não
    apaga nem esconde o dano enquanto ele não encerrar. REGRA DAS DUAS
    (routers/portaria_avarias.py): duas pessoas DIFERENTES, em dias de
    serviço DIFERENTES, contestando a mesma avaria encerram ela sozinhas
    (encerramento=NAO_EXISTIA, encerrada_por=NULL — encerramento do
    SISTEMA). Espelha a migration 042."""

    __tablename__ = "avaria_contestacao"
    __table_args__ = (
        # Espelha uq_contestacao_por_pessoa (migration 042) — é o que
        # impede o mesmo controlador contestar duas vezes e acionar a
        # regra das duas sozinho.
        UniqueConstraint("avaria_id", "registrado_por", name="uq_contestacao_por_pessoa"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    avaria_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.avaria_saida.id", ondelete="RESTRICT"), nullable=False
    )
    ocorrido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_servico: Mapped[date] = mapped_column(Date, nullable=False)
    nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
