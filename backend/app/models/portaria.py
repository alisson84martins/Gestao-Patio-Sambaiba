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
from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, SmallInteger, String, Text,
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
    entrada/saída."""

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

    # Snapshots (D4), sempre preenchidos, mesmo com veiculo_id.
    placa_registrada: Mapped[str] = mapped_column(String(8), nullable=False)
    re_registrado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    nome_registrado: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Terceiro (D5) — condutor da visita é texto puro, nunca FK.
    terceiro_nome: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_destino: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    terceiro_empresa: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    hodometro_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    cadastrado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    origem: Mapped[str] = mapped_column(String(12), nullable=False, default="MANUAL")
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

    status: Mapped[str] = mapped_column(String(12), nullable=False, default="AGUARDANDO")

    registrado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("funcionario.id"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
