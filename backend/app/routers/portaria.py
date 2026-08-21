"""Endpoints do módulo Portaria — registro de entrada/saída de veículos.

🔴 REGRA NÚMERO UM (§1.1 do prompt): o sistema NUNCA impede um registro.
Nenhum endpoint deste arquivo retorna 4xx por causa da SITUAÇÃO do veículo
— 4xx só para erro real: payload inválido, sem permissão, recurso
inexistente. Veículo suspenso/baixado, pendente ou nem cadastrado: o
POST /movimentos sempre registra, e só avisa.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.portaria import Credencial, MovimentoPortaria, VeiculoPortaria
from app.schemas.portaria import (
    BuscaVeiculoResponse, MovimentoCreate, MovimentoCreateResponse, MovimentoRead,
    PortariaDentroResponse, Propriedade, Sentido, VeiculoCandidato, normalizar_placa,
)
from app.services.portaria import veiculo_read

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular"))]
EscritaAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular", escrever=True))]


# ============================================================================
# "DENTRO AGORA" (D3, D17) — deriva o mesmo estado de portaria.vw_dentro via
# ORM, sem depender da view: a view usa DISTINCT ON (Postgres puro) e os
# testes deste módulo rodam em SQLite (Bloco D). ⛔ Não filtra dentro da
# view, e ⛔ não "fecha" movimento nenhum automaticamente — só separa em
# duas listas o que já existe.
# ============================================================================

def _dentro_e_sem_saida(
    db: Session, horas: int
) -> tuple[list[MovimentoPortaria], list[MovimentoPortaria]]:
    ultimo_por_placa = (
        select(
            MovimentoPortaria.placa_registrada.label("placa"),
            func.max(MovimentoPortaria.momento).label("momento_max"),
        )
        .group_by(MovimentoPortaria.placa_registrada)
        .subquery()
    )
    ultimos = db.execute(
        select(MovimentoPortaria)
        .join(
            ultimo_por_placa,
            (MovimentoPortaria.placa_registrada == ultimo_por_placa.c.placa)
            & (MovimentoPortaria.momento == ultimo_por_placa.c.momento_max),
        )
        .where(MovimentoPortaria.sentido == "ENTRADA")
        .order_by(MovimentoPortaria.momento.desc())
    ).scalars().all()

    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    dentro: list[MovimentoPortaria] = []
    sem_saida: list[MovimentoPortaria] = []
    for mov in ultimos:
        momento = mov.momento if mov.momento.tzinfo else mov.momento.replace(tzinfo=timezone.utc)
        (dentro if momento >= limite else sem_saida).append(mov)
    return dentro, sem_saida


@router.get(
    "/dentro",
    response_model=PortariaDentroResponse,
    summary="Quem está dentro agora, separado de quem provavelmente saiu sem registrar (D17)",
)
def dentro_agora(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    horas: int = Query(36, ge=1, description="Limite para separar 'dentro' de 'sem_saida'"),
):
    dentro, sem_saida = _dentro_e_sem_saida(db, horas)
    return PortariaDentroResponse(dentro=dentro, sem_saida=sem_saida, horas=horas)


@router.get(
    "/alertas/sem-saida",
    response_model=list[MovimentoRead],
    summary="Entrou e não saiu há mais de N horas (default 24) — termômetro do preenchimento",
)
def alertas_sem_saida(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    horas: int = Query(24, ge=1),
):
    _, sem_saida = _dentro_e_sem_saida(db, horas)
    return sem_saida


# ============================================================================
# BUSCA — autocomplete por placa/RE/nome. A leitura de QR do Bloco E
# (buscar-credencial) devolve o mesmo BuscaVeiculoResponse — mesmo card de
# confirmação, mesmo semáforo (D15).
# ============================================================================

_MAX_CANDIDATOS = 8


def _candidato(veiculo: VeiculoPortaria, db: Session) -> VeiculoCandidato:
    ultimo = db.execute(
        select(MovimentoPortaria)
        .where(MovimentoPortaria.veiculo_id == veiculo.id)
        .order_by(MovimentoPortaria.momento.desc())
        .limit(1)
    ).scalar_one_or_none()
    return VeiculoCandidato(
        veiculo=veiculo_read(veiculo, db),
        dentro=(ultimo is not None and ultimo.sentido == "ENTRADA"),
        ultimo_movimento=MovimentoRead.model_validate(ultimo) if ultimo else None,
    )


@router.get(
    "/buscar",
    response_model=BuscaVeiculoResponse,
    summary="Autocomplete por placa, RE ou nome — candidatos pro controlador confirmar, nunca um palpite (§3.6-C)",
)
def buscar(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(..., min_length=1, max_length=120),
):
    termo = q.strip()
    termo_placa = normalizar_placa(termo)

    # Match exato de placa -> índice único WHERE ativo garante no máximo 1
    # linha; vai direto ao card de confirmação (caminho de <=8s).
    exato = db.execute(
        select(VeiculoPortaria).where(VeiculoPortaria.placa == termo_placa, VeiculoPortaria.ativo.is_(True))
    ).scalar_one_or_none()
    if exato is not None:
        return BuscaVeiculoResponse(candidatos=[_candidato(exato, db)], exato=True)

    # 🔴 §3.6-B/C: sem match exato, junta candidatos de placa/RE/nome, todos
    # com .limit(_MAX_CANDIDATOS) — nunca .scalar_one_or_none() numa consulta
    # que pode devolver mais de uma linha (RE com 2 veículos quebrava com
    # MultipleResultsFound -> 500), e nunca escolhendo o primeiro em
    # silêncio (prefixo de placa ou nome batendo 2+ registrava o carro
    # errado sem ninguém perceber).
    candidatos: list[VeiculoPortaria] = []
    vistos: set[UUID] = set()

    def _acrescenta(veiculos: list[VeiculoPortaria]) -> None:
        for v in veiculos:
            if v.id not in vistos:
                vistos.add(v.id)
                candidatos.append(v)

    if len(termo_placa) >= 2:
        _acrescenta(db.execute(
            select(VeiculoPortaria)
            .where(VeiculoPortaria.ativo.is_(True), VeiculoPortaria.placa.ilike(f"{termo_placa}%"))
            .order_by(VeiculoPortaria.placa)
            .limit(_MAX_CANDIDATOS)
        ).scalars().all())

    if len(candidatos) < _MAX_CANDIDATOS:
        _acrescenta(db.execute(
            select(VeiculoPortaria)
            .join(Funcionario, Funcionario.id == VeiculoPortaria.funcionario_id)
            .where(VeiculoPortaria.ativo.is_(True), Funcionario.re == termo)
            .order_by(VeiculoPortaria.placa)
            .limit(_MAX_CANDIDATOS)
        ).scalars().all())

    if len(candidatos) < _MAX_CANDIDATOS:
        _acrescenta(db.execute(
            select(VeiculoPortaria)
            .join(Funcionario, Funcionario.id == VeiculoPortaria.funcionario_id)
            .where(VeiculoPortaria.ativo.is_(True), Funcionario.nome.ilike(f"%{termo}%"))
            .order_by(VeiculoPortaria.placa)
            .limit(_MAX_CANDIDATOS)
        ).scalars().all())

    candidatos.sort(key=lambda v: v.placa)
    candidatos = candidatos[:_MAX_CANDIDATOS]

    # [] é resultado válido — ⛔ nunca 404, a regra número um vale pra
    # busca também: sem candidato, o controlador segue pro fluxo de
    # "não encontrado" (cadastrar agora / registrar avulso).
    return BuscaVeiculoResponse(candidatos=[_candidato(v, db) for v in candidatos], exato=False)


@router.get(
    "/buscar-credencial",
    response_model=BuscaVeiculoResponse,
    summary="Resolve o código lido do QR pro mesmo card de confirmação da busca por placa (Bloco E, D15)",
)
def buscar_credencial(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    codigo: str = Query(..., min_length=1, max_length=64),
):
    # Código inexistente ou credencial revogada -> 200 com lista vazia, igual
    # a placa não encontrada (regra número um vale pra busca também — ⛔
    # nunca 404/403 aqui). ⚠️ credencial.ativa=FALSE não é proibição: é só
    # "adesivo velho" — o veículo continua registrável pela placa igual
    # sempre foi, só o atalho do QR é que para de funcionar.
    credencial = db.execute(
        select(Credencial).where(Credencial.codigo == codigo, Credencial.ativa.is_(True))
    ).scalar_one_or_none()
    if credencial is None:
        return BuscaVeiculoResponse(candidatos=[], exato=False)

    veiculo = db.execute(
        select(VeiculoPortaria).where(
            VeiculoPortaria.id == credencial.veiculo_id, VeiculoPortaria.ativo.is_(True)
        )
    ).scalar_one_or_none()
    if veiculo is None:
        return BuscaVeiculoResponse(candidatos=[], exato=False)

    return BuscaVeiculoResponse(candidatos=[_candidato(veiculo, db)], exato=True)


# ============================================================================
# REGISTRO DE MOVIMENTO — o coração da regra número um.
# ============================================================================

@router.post(
    "/movimentos",
    response_model=MovimentoCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registra ENTRADA ou SAÍDA — nunca recusa por causa da situação do veículo",
)
def registrar_movimento(
    payload: MovimentoCreate, usuario: EscritaAcesso, db: Annotated[Session, Depends(get_db)]
):
    avisos: list[str] = []

    veiculo = db.execute(
        select(VeiculoPortaria).where(VeiculoPortaria.placa == payload.placa, VeiculoPortaria.ativo.is_(True))
    ).scalar_one_or_none()

    # Snapshots (D4) — sempre preenchidos, mesmo com veiculo_id.
    re_registrado = payload.re_registrado
    nome_registrado = payload.nome_registrado
    if payload.funcionario_id:
        condutor = db.get(Funcionario, payload.funcionario_id)
        if condutor is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Condutor (funcionario_id) não encontrado")
        re_registrado = re_registrado or condutor.re
        nome_registrado = nome_registrado or condutor.nome
    elif veiculo is not None and veiculo.propriedade == "PARTICULAR" and not re_registrado:
        dono = db.get(Funcionario, veiculo.funcionario_id) if veiculo.funcionario_id else None
        if dono is not None:
            re_registrado = dono.re
            nome_registrado = dono.nome

    # 🔴 Regra número um: suspenso/baixado NUNCA bloqueia — registra,
    # exige observação, e avisa (D11).
    if veiculo is not None and veiculo.situacao in ("SUSPENSO", "BAIXADO"):
        if not (payload.observacao or "").strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Veículo {veiculo.situacao} — observação é obrigatória "
                    "para registrar a passagem mesmo assim."
                ),
            )
        quem = db.get(Funcionario, veiculo.situacao_por) if veiculo.situacao_por else None
        partes = [f"Veículo {veiculo.situacao}"]
        if veiculo.situacao_em:
            partes.append(f"desde {veiculo.situacao_em.astimezone(FUSO_OPERACAO):%d/%m/%Y %H:%M}")
        if quem is not None:
            partes.append(f"por {quem.nome}")
        if veiculo.situacao_motivo:
            partes.append(f"— motivo: {veiculo.situacao_motivo}")
        avisos.append(" ".join(partes))
    elif veiculo is not None and veiculo.situacao == "PENDENTE":
        avisos.append("Veículo cadastrado, aguardando autorização (PENDENTE).")

    if veiculo is not None and veiculo.exige_hodometro and payload.hodometro_km is None:
        avisos.append("Hodômetro não informado — ficou pendente.")

    if veiculo is None:
        avisos.append("Placa não cadastrada — passagem registrada como avulsa.")

    # Conveniência (D3) — nunca requisito. Se não bater, ignora o vínculo.
    movimento_entrada_id: Optional[UUID] = None
    if payload.sentido == "SAIDA" and payload.movimento_entrada_id is not None:
        entrada = db.get(MovimentoPortaria, payload.movimento_entrada_id)
        if entrada is not None and entrada.sentido == "ENTRADA" and entrada.placa_registrada == payload.placa:
            movimento_entrada_id = payload.movimento_entrada_id

    # D16: data_referencia sempre explícita a partir de FUSO_OPERACAO —
    # nunca get_data_servico() (essa regra é do Pátio), nunca date.today()
    # cru do servidor. momento continua NOW()/UTC do banco (D9), exceto em
    # RETROATIVO, onde é hora que uma PESSOA leu do papel e digitou.
    momento_retroativo: Optional[datetime] = None
    if payload.origem == "RETROATIVO":
        bruto = payload.momento
        momento_retroativo = (
            bruto.replace(tzinfo=FUSO_OPERACAO) if bruto.tzinfo is None else bruto.astimezone(FUSO_OPERACAO)
        )
        data_referencia = momento_retroativo.date()
    else:
        data_referencia = datetime.now(FUSO_OPERACAO).date()

    novo = MovimentoPortaria(
        local_codigo=payload.local_codigo,
        sentido=payload.sentido,
        data_referencia=data_referencia,
        veiculo_id=veiculo.id if veiculo else None,
        funcionario_id=payload.funcionario_id,
        placa_registrada=payload.placa,
        re_registrado=re_registrado,
        nome_registrado=nome_registrado,
        terceiro_nome=payload.terceiro_nome,
        terceiro_destino=payload.terceiro_destino,
        terceiro_empresa=payload.terceiro_empresa,
        hodometro_km=payload.hodometro_km,
        cadastrado=veiculo is not None,
        origem=payload.origem,
        movimento_entrada_id=movimento_entrada_id,
        registrado_por=usuario.id,
        observacao=payload.observacao,
    )
    if momento_retroativo is not None:
        novo.momento = momento_retroativo
    # senão: não seta `momento` — cai no server_default NOW() do banco
    # (⛔ não "consertar" isso pra simetria com data_referencia).

    db.add(novo)
    db.commit()
    db.refresh(novo)

    return MovimentoCreateResponse(**MovimentoRead.model_validate(novo).model_dump(), avisos=avisos)


# ============================================================================
# HISTÓRICO — consulta com filtros
# ============================================================================

@router.get(
    "/movimentos",
    response_model=list[MovimentoRead],
    summary="Histórico de movimentos com filtros (período, placa, RE, propriedade, local, sentido)",
)
def listar_movimentos(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    data_inicio: Optional[date] = None,
    data_fim: Optional[date] = None,
    placa: Optional[str] = None,
    re: Optional[str] = None,
    propriedade: Optional[Propriedade] = None,
    local_codigo: Optional[str] = None,
    sentido: Optional[Sentido] = None,
    apenas_nao_cadastrados: bool = False,
    apenas_suspensos: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = select(MovimentoPortaria)
    # apenas_suspensos filtra pela situação ATUAL do veículo — não existe
    # snapshot de situação por movimento nesta fase.
    if apenas_suspensos or propriedade:
        stmt = stmt.join(VeiculoPortaria, VeiculoPortaria.id == MovimentoPortaria.veiculo_id)
    if data_inicio:
        stmt = stmt.where(MovimentoPortaria.data_referencia >= data_inicio)
    if data_fim:
        stmt = stmt.where(MovimentoPortaria.data_referencia <= data_fim)
    if placa:
        stmt = stmt.where(MovimentoPortaria.placa_registrada == normalizar_placa(placa))
    if re:
        stmt = stmt.where(MovimentoPortaria.re_registrado == re.strip())
    if propriedade:
        stmt = stmt.where(VeiculoPortaria.propriedade == propriedade)
    if local_codigo:
        stmt = stmt.where(MovimentoPortaria.local_codigo == local_codigo)
    if sentido:
        stmt = stmt.where(MovimentoPortaria.sentido == sentido)
    if apenas_nao_cadastrados:
        stmt = stmt.where(MovimentoPortaria.cadastrado.is_(False))
    if apenas_suspensos:
        stmt = stmt.where(VeiculoPortaria.situacao == "SUSPENSO")

    stmt = stmt.order_by(MovimentoPortaria.momento.desc()).offset(skip).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/movimentos/{movimento_id}", response_model=MovimentoRead, summary="Detalhe de um movimento")
def detalhar_movimento(
    movimento_id: UUID, usuario: LeituraAcesso, db: Annotated[Session, Depends(get_db)]
):
    mov = db.get(MovimentoPortaria, movimento_id)
    if mov is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Movimento não encontrado")
    return mov
