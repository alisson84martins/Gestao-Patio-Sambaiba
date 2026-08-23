"""Endpoints do módulo Fiscalização — turnos, partidas, eventos e fechamento.

🔴 O produto é o fechamento (D2): todo o resto existe para que a mensagem
saia pronta às 23h45. Prontidão AVISA, nunca bloqueia (D3) — POST /fechar
funciona com pendências, mesma filosofia da regra número um da Portaria.

⛔ Ordem de registro de rotas: `/turnos/ativo` (literal) é declarada ANTES
de `/turnos/{turno_id}` (path parameter) — mesmo bug que já aconteceu duas
vezes neste projeto (autopreencher/{id}, pre-ocorrencias/publico/{id}).
"""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, Response,
    UploadFile, status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige
from app.core.uploads import ler_upload_limitado
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import (
    AcaoCoordenacao, Bacia, Baita, EventoTurno, ObservacaoTurno, PartidaProgramada,
    Ponto, PontoLinha, RegistroPartida, Turno, TurnoLinha,
)
from app.models.portaria import RecolhidaAnormal
from app.schemas.fiscalizacao import (
    AcaoCoordenacaoCreate, AcaoCoordenacaoRead, BaciaRead, BaitaRead, BaitaUpsert,
    CascataItem, EventoTurnoCreate, EventoTurnoRead, IcvBaciaDiaRead, IcvLinhaDiaRead,
    MotivoLivreItem, ObservacaoTurnoCreate, ObservacaoTurnoRead, PainelLinhaResponse,
    PainelPartidaItem, PartidaEstadoItem, PendenciaItem, PlacarLinhaRead, PontoRead,
    PrioridadeLinhaItem, ProntidaoResponse, RegistroPartidaRead, RegistroPartidaUpsert,
    TipoDia, TurnoAbrirRequest, TurnoRead, TurnoUpdateRequest,
)
from app.services.fechamento_fiscal import montar_fechamento
from app.services.icv import (
    calcular_icv_bacia_dia, calcular_icv_linha_dia, detectar_cascata,
    montar_placar_linha, motivos_livres_frequentes, ranking_prioridade,
)
from app.services.importacao_escala_fiscal import importar_escala
from app.services.importacao_icv import importar_icv

router = APIRouter(prefix="/fiscalizacao", tags=["fiscalização"])

LeituraFiscalizacao = Annotated[Funcionario, Depends(exige("fiscalizacao"))]
EscritaFiscalizacao = Annotated[Funcionario, Depends(exige("fiscalizacao", escrever=True))]
LeituraPainel = Annotated[Funcionario, Depends(exige("fiscalizacao_painel"))]
EscritaPainel = Annotated[Funcionario, Depends(exige("fiscalizacao_painel", escrever=True))]
EscritaEscala = Annotated[Funcionario, Depends(exige("escala", escrever=True))]
DbSession = Annotated[Session, Depends(get_db)]

TAMANHO_MAXIMO_ICV = 10 * 1024 * 1024  # 10 MB, mesmo limite dos outros três endpoints de upload

# Eventos com contador (D4) — os cinco que custam viagem quando a partida é
# marcada PERDIDA. VIAGEM_EXTRA só existe como evento avulso (não é motivo de
# partida perdida); OUTRO nunca cria evento (não existe contador para ele).
_MOTIVOS_COM_EVENTO = {"FALTA_OPERADORES", "RA", "SOS", "ATRASO_GARAGEM", "TROCA_OPERACIONAL"}

TAMANHO_MAXIMO_ESCALA = 10 * 1024 * 1024  # 10 MB, mesmo limite de importacao.py

# Janela de correlação com a recolhida (D17) — mesma constante da view
# fiscalizacao.vw_partida_recolhida (JANELA_RECOLHIDA), duplicada aqui
# porque a leitura cruzada nos endpoints roda em Python/SQLAlchemy — mesmo
# motivo de portaria.py não depender de vw_dentro: os testes deste módulo
# rodam em SQLite (sem LATERAL/views do Postgres).
_JANELA_RECOLHIDA = timedelta(hours=6)

# Partidas antes das 4h contam como madrugada do dia SEGUINTE ao
# data_referencia do turno — a operação real começa por volta de 4h10/4h30
# (confirmado em Pasta1.xlsx) e só cruza a meia-noite no fim do 2º período.
_VIRADA_MADRUGADA = time(4, 0)


def _tipo_dia(d: date) -> str:
    dow = d.weekday()  # Monday=0 ... Sunday=6
    if dow == 5:
        return "SABADO"
    if dow == 6:
        return "DOMINGO"
    return "UTIL"


def _datetime_da_partida(data_referencia: date, horario_programado: time) -> datetime:
    dt = datetime.combine(data_referencia, horario_programado, tzinfo=FUSO_OPERACAO)
    if horario_programado < _VIRADA_MADRUGADA:
        dt += timedelta(days=1)
    return dt


def _exige_dono(turno: Turno, usuario: Funcionario) -> None:
    if turno.funcionario_id != usuario.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Este turno pertence a outro fiscal.")


def _linhas_do_turno(db: Session, turno_id: UUID) -> list[str]:
    return list(
        db.execute(
            select(TurnoLinha.linha_codigo).where(TurnoLinha.turno_id == turno_id).order_by(TurnoLinha.linha_codigo)
        ).scalars().all()
    )


def _turno_read(db: Session, turno: Turno) -> TurnoRead:
    schema = TurnoRead.model_validate(turno)
    schema.linhas = _linhas_do_turno(db, turno.id)
    return schema


def _vigencia_vigente(db: Session, linha_codigo: str, tipo_dia: Optional[str], data_referencia: date) -> Optional[date]:
    if tipo_dia is None:
        return None
    return db.execute(
        select(func.max(PartidaProgramada.vigencia)).where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == tipo_dia,
            PartidaProgramada.vigencia <= data_referencia,
        )
    ).scalar_one_or_none()


def _itens_partidas(db: Session, turno: Turno, linha_codigo: str, agora: datetime) -> list[PartidaEstadoItem]:
    """A grade do turno para uma linha, cada horário com seu estado (D7).
    🔴 ATRASADA/AGUARDANDO são calculados aqui, a cada chamada — nunca
    gravados. Partida com periodo IS NULL entra pelo filtro de horário
    (§7.3): aparece na listagem de qualquer período do mesmo dia/tipo_dia,
    porque o parser não conseguiu decidir a qual período ela pertence —
    melhor mostrar duas vezes do que nunca aparecer para ninguém responder.
    """
    vigencia = _vigencia_vigente(db, linha_codigo, turno.tipo_dia, turno.data_referencia)
    if vigencia is None:
        return []

    partidas_prog = db.execute(
        select(PartidaProgramada)
        .where(
            PartidaProgramada.linha_codigo == linha_codigo,
            PartidaProgramada.tipo_dia == turno.tipo_dia,
            PartidaProgramada.vigencia == vigencia,
            (PartidaProgramada.periodo == turno.periodo) | (PartidaProgramada.periodo.is_(None)),
        )
        .order_by(PartidaProgramada.horario)
    ).scalars().all()

    registros = {
        (r.linha_codigo, r.numero_tabela, r.terminal, r.horario_programado): r
        for r in db.execute(
            select(RegistroPartida).where(
                RegistroPartida.turno_id == turno.id, RegistroPartida.linha_codigo == linha_codigo
            )
        ).scalars().all()
    }

    itens: list[PartidaEstadoItem] = []
    for pp in partidas_prog:
        chave = (pp.linha_codigo, pp.numero_tabela, pp.terminal, pp.horario)
        registro = registros.get(chave)
        if registro is not None:
            estado = registro.resultado
        else:
            dt_prog = _datetime_da_partida(turno.data_referencia, pp.horario)
            estado = "ATRASADA" if dt_prog <= agora else "AGUARDANDO"
        itens.append(PartidaEstadoItem(
            partida_programada_id=pp.id,
            numero_tabela=pp.numero_tabela,
            terminal=pp.terminal,
            horario_programado=pp.horario,
            periodo=pp.periodo,
            estado=estado,
            registro=RegistroPartidaRead.model_validate(registro) if registro is not None else None,
        ))
    return itens


def _sincronizar_evento_vinculado(db: Session, registro: RegistroPartida) -> None:
    """D4 — marcar PERDIDA com motivo que custa viagem cria/mantém UM
    evento_turno vinculado. Mudar a resposta ajusta o mesmo evento; motivo
    OUTRO ou virar REALIZADA remove o vínculo (não existe contador p/ OUTRO)."""
    evento = db.execute(
        select(EventoTurno).where(EventoTurno.registro_partida_id == registro.id)
    ).scalar_one_or_none()

    deve_ter_evento = registro.resultado == "PERDIDA" and registro.motivo in _MOTIVOS_COM_EVENTO

    if not deve_ter_evento:
        if evento is not None:
            db.delete(evento)
        return

    if evento is None:
        evento = EventoTurno(turno_id=registro.turno_id, registro_partida_id=registro.id, tipo=registro.motivo)
        db.add(evento)

    evento.tipo = registro.motivo
    evento.linha_codigo = registro.linha_codigo
    evento.horario = registro.horario_programado
    evento.numero_tabela = registro.numero_tabela
    evento.prefixo = registro.prefixo


# ============================================================================
# CATÁLOGO
# ============================================================================

@router.get("/pontos", response_model=list[PontoRead], summary="Pontos ativos com suas linhas")
def listar_pontos(usuario: LeituraFiscalizacao, db: DbSession):
    pontos = db.execute(select(Ponto).where(Ponto.ativo.is_(True)).order_by(Ponto.codigo)).scalars().all()
    resultado = []
    for p in pontos:
        linhas = db.execute(
            select(PontoLinha.linha_codigo)
            .where(PontoLinha.ponto_codigo == p.codigo, PontoLinha.ativo.is_(True))
            .order_by(PontoLinha.linha_codigo)
        ).scalars().all()
        resultado.append(PontoRead(codigo=p.codigo, nome=p.nome, terminal=p.terminal, ativo=p.ativo, linhas=list(linhas)))
    return resultado


# ============================================================================
# TURNO — ⛔ /turnos/ativo ANTES de /turnos/{turno_id}
# ============================================================================

@router.post("/turnos", response_model=TurnoRead, status_code=status.HTTP_201_CREATED, summary="Abrir turno")
def abrir_turno(payload: TurnoAbrirRequest, usuario: EscritaFiscalizacao, db: DbSession):
    ponto = db.get(Ponto, payload.ponto_codigo)
    if ponto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ponto não encontrado")

    data_referencia = datetime.now(FUSO_OPERACAO).date()

    existente = db.execute(
        select(Turno).where(
            Turno.funcionario_id == usuario.id,
            Turno.ponto_codigo == payload.ponto_codigo,
            Turno.periodo == payload.periodo,
            Turno.data_referencia == data_referencia,
            Turno.status == "ABERTO",
        )
    ).scalar_one_or_none()
    if existente is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Já existe um turno ABERTO para esta pessoa, ponto, período e data.",
        )

    agora = datetime.now(FUSO_OPERACAO)
    novo = Turno(
        funcionario_id=usuario.id,
        fiscal_re=usuario.re,
        ponto_codigo=payload.ponto_codigo,
        terminal=ponto.terminal,
        periodo=payload.periodo,
        data_referencia=data_referencia,
        tipo_dia=_tipo_dia(data_referencia),
        status="ABERTO",
        aberto_em=agora,
    )
    db.add(novo)
    db.flush()
    for linha_codigo in dict.fromkeys(payload.linhas):
        db.add(TurnoLinha(turno_id=novo.id, linha_codigo=linha_codigo))
    db.commit()
    db.refresh(novo)
    return _turno_read(db, novo)


@router.get("/turnos/ativo", response_model=Optional[TurnoRead], summary="O turno aberto de quem está logado")
def turno_ativo(usuario: LeituraFiscalizacao, db: DbSession):
    turno = db.execute(
        select(Turno)
        .where(Turno.funcionario_id == usuario.id, Turno.status == "ABERTO")
        .order_by(Turno.aberto_em.desc())
    ).scalars().first()
    if turno is None:
        return None
    return _turno_read(db, turno)


@router.get("/turnos/{turno_id}", response_model=TurnoRead)
def detalhar_turno(turno_id: UUID, usuario: LeituraFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    return _turno_read(db, turno)


@router.patch("/turnos/{turno_id}", response_model=TurnoRead, summary="Refeição do fiscal (D15) e pastas (D14)")
def atualizar_turno(turno_id: UUID, payload: TurnoUpdateRequest, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(turno, campo, valor)
    turno.atualizado_em = datetime.now(FUSO_OPERACAO)
    db.commit()
    db.refresh(turno)
    return _turno_read(db, turno)


@router.post("/turnos/{turno_id}/fechar", response_model=TurnoRead)
def fechar_turno(turno_id: UUID, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    # D3 — prontidão avisa, nunca bloqueia: fecha com pendências igual.
    turno.status = "FECHADO"
    turno.fechado_em = datetime.now(FUSO_OPERACAO)
    db.commit()
    db.refresh(turno)
    return _turno_read(db, turno)


# ============================================================================
# PARTIDAS
# ============================================================================

@router.get("/turnos/{turno_id}/partidas", response_model=dict[str, list[PartidaEstadoItem]])
def listar_partidas_turno(turno_id: UUID, usuario: LeituraFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    agora = datetime.now(FUSO_OPERACAO)
    return {linha: _itens_partidas(db, turno, linha, agora) for linha in _linhas_do_turno(db, turno_id)}


@router.put("/turnos/{turno_id}/partidas", response_model=RegistroPartidaRead, summary="Upsert pela chave única")
def marcar_partida(turno_id: UUID, payload: RegistroPartidaUpsert, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)

    registro = db.execute(
        select(RegistroPartida).where(
            RegistroPartida.turno_id == turno_id,
            RegistroPartida.linha_codigo == payload.linha_codigo,
            RegistroPartida.numero_tabela == payload.numero_tabela,
            RegistroPartida.terminal == payload.terminal,
            RegistroPartida.horario_programado == payload.horario_programado,
        )
    ).scalar_one_or_none()

    if registro is None:
        registro = RegistroPartida(
            turno_id=turno_id,
            linha_codigo=payload.linha_codigo,
            numero_tabela=payload.numero_tabela,
            terminal=payload.terminal,
            horario_programado=payload.horario_programado,
        )
        db.add(registro)
    else:
        registro.atualizado_em = datetime.now(FUSO_OPERACAO)

    registro.partida_programada_id = payload.partida_programada_id
    registro.resultado = payload.resultado
    registro.horario_real = payload.horario_real
    registro.motivo = payload.motivo
    registro.motivo_outro = payload.motivo_outro
    registro.prefixo = payload.prefixo
    registro.operador_re = payload.operador_re
    registro.observacao = payload.observacao

    db.flush()  # precisa do registro.id antes de sincronizar o evento vinculado
    _sincronizar_evento_vinculado(db, registro)

    db.commit()
    db.refresh(registro)
    return registro


# ============================================================================
# EVENTOS avulsos (D4) — contador sem perda associada
# ============================================================================

@router.post(
    "/turnos/{turno_id}/eventos", response_model=EventoTurnoRead, status_code=status.HTTP_201_CREATED
)
def criar_evento_avulso(turno_id: UUID, payload: EventoTurnoCreate, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    evento = EventoTurno(
        turno_id=turno_id,
        linha_codigo=payload.linha_codigo,
        tipo=payload.tipo,
        horario=payload.horario,
        numero_tabela=payload.numero_tabela,
        prefixo=payload.prefixo,
        observacao=payload.observacao,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)
    return evento


@router.delete("/turnos/{turno_id}/eventos/{evento_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_evento_avulso(turno_id: UUID, evento_id: UUID, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    evento = db.get(EventoTurno, evento_id)
    if evento is None or evento.turno_id != turno_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Evento não encontrado")
    if evento.registro_partida_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Evento vinculado a uma partida — mude a resposta da partida para remover.",
        )
    db.delete(evento)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# OBSERVAÇÕES (D5) — o escape hatch
# ============================================================================

@router.post(
    "/turnos/{turno_id}/observacoes", response_model=ObservacaoTurnoRead, status_code=status.HTTP_201_CREATED
)
def criar_observacao(turno_id: UUID, payload: ObservacaoTurnoCreate, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    obs = ObservacaoTurno(
        turno_id=turno_id,
        linha_codigo=payload.linha_codigo,
        numero_tabela=payload.numero_tabela,
        horario=payload.horario,
        texto=payload.texto,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs


@router.delete("/turnos/{turno_id}/observacoes/{observacao_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_observacao(turno_id: UUID, observacao_id: UUID, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    obs = db.get(ObservacaoTurno, observacao_id)
    if obs is None or obs.turno_id != turno_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Observação não encontrada")
    db.delete(obs)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# BAITA / ANTI-BAITA (D13)
# ============================================================================

@router.put("/turnos/{turno_id}/baita", response_model=BaitaRead, summary="Upsert por (turno, linha, tipo)")
def upsert_baita(turno_id: UUID, payload: BaitaUpsert, usuario: EscritaFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)

    registro = db.execute(
        select(Baita).where(
            Baita.turno_id == turno_id, Baita.linha_codigo == payload.linha_codigo, Baita.tipo == payload.tipo
        )
    ).scalar_one_or_none()
    if registro is None:
        registro = Baita(turno_id=turno_id, linha_codigo=payload.linha_codigo, tipo=payload.tipo, prefixo=payload.prefixo)
        db.add(registro)
    else:
        registro.atualizado_em = datetime.now(FUSO_OPERACAO)

    registro.prefixo = payload.prefixo
    registro.motorista_re = payload.motorista_re
    registro.cobrador_re = payload.cobrador_re
    registro.saida_tp = payload.saida_tp
    registro.saida_ts = payload.saida_ts
    registro.ts_circular = payload.ts_circular

    db.commit()
    db.refresh(registro)
    return registro


# ============================================================================
# PRONTIDÃO (D3) — o medidor. Avisa, nunca bloqueia.
# ============================================================================

@router.get("/turnos/{turno_id}/prontidao", response_model=ProntidaoResponse)
def prontidao_turno(turno_id: UUID, usuario: LeituraFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)

    linhas = _linhas_do_turno(db, turno_id)
    agora = datetime.now(FUSO_OPERACAO)
    pendencias: list[PendenciaItem] = []

    por_linha: dict[str, int] = {}
    for linha_codigo in linhas:
        atrasadas = sum(1 for item in _itens_partidas(db, turno, linha_codigo, agora) if item.estado == "ATRASADA")
        if atrasadas:
            por_linha[linha_codigo] = atrasadas
    if por_linha:
        pendencias.append(
            PendenciaItem(tipo="PARTIDAS_SEM_RESPOSTA", quantidade=sum(por_linha.values()), por_linha=por_linha)
        )

    baitas = db.execute(
        select(Baita.linha_codigo, Baita.tipo).where(Baita.turno_id == turno_id)
    ).all()
    linhas_com_baita = {l for l, t in baitas if t == "BAITA"}
    linhas_com_anti = {l for l, t in baitas if t == "ANTI_BAITA"}

    faltando_baita = [l for l in linhas if l not in linhas_com_baita]
    if faltando_baita:
        pendencias.append(PendenciaItem(tipo="BAITA_FALTANDO", linhas=faltando_baita))

    faltando_anti = [l for l in linhas if l not in linhas_com_anti]
    if faltando_anti:
        pendencias.append(PendenciaItem(tipo="ANTI_BAITA_FALTANDO", linhas=faltando_anti))

    if not (turno.pastas_prefixo or "").strip():
        pendencias.append(PendenciaItem(tipo="PASTAS_NAO_INFORMADAS"))

    return ProntidaoResponse(pronto=not pendencias, pendencias=pendencias)


# ============================================================================
# FECHAMENTO (D2) — o texto pronto, nunca gravado (mesmo molde de mensagem_sinistro.py)
# ============================================================================

@router.get("/turnos/{turno_id}/fechamento", response_model=list[str])
def fechamento_turno(turno_id: UUID, usuario: LeituraFiscalizacao, db: DbSession):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    return montar_fechamento(db, turno_id)


# ============================================================================
# IMPORTAÇÃO DA GRADE (§7) — mesmo recurso de quem já importa escala hoje
# ============================================================================

@router.post("/escalas/upload", summary="Importa a grade a partir da escala gerencial (.xlsx)")
async def upload_escala(
    usuario: EscritaEscala,
    db: DbSession,
    request: Request,
    file: UploadFile = File(...),
    tipo_dia: TipoDia = "UTIL",
    vigencia: Optional[date] = None,
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos .xlsx são aceitos")
    conteudo = await ler_upload_limitado(file, TAMANHO_MAXIMO_ESCALA, request)
    resultado = importar_escala(
        db, conteudo, tipo_dia=tipo_dia, vigencia=vigencia or datetime.now(FUSO_OPERACAO).date()
    )
    db.commit()
    return resultado


# ============================================================================
# IMPORTAÇÃO DA PLANILHA DE ICV (D20, D25, D28, §5) — exige("fiscalizacao_painel", escrever=True)
# ============================================================================

@router.post("/icv/upload", summary="Importa a planilha semanal de ICV da gerência (D20)")
async def upload_icv(usuario: EscritaPainel, db: DbSession, request: Request, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos .xlsx são aceitos")
    conteudo = await ler_upload_limitado(file, TAMANHO_MAXIMO_ICV, request)
    try:
        resultado = importar_icv(db, conteudo, arquivo_nome=file.filename, importado_por=usuario.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    db.commit()
    return resultado


# ============================================================================
# ICV, BACIA E PRIORIDADE (D20-D23, D28, D30) — exige("fiscalizacao_painel")
# ============================================================================

@router.get("/bacias", response_model=list[BaciaRead], summary="Bacias ativas (D21)")
def listar_bacias(usuario: LeituraPainel, db: DbSession):
    return db.execute(select(Bacia).where(Bacia.ativo.is_(True)).order_by(Bacia.nome)).scalars().all()


@router.get("/icv/linha/{linha_codigo}", response_model=IcvLinhaDiaRead, summary="ICV das duas fontes, por linha e dia (D20)")
def icv_linha_dia(linha_codigo: str, usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return calcular_icv_linha_dia(db, linha_codigo, data_referencia)


@router.get("/icv/bacia/{bacia_codigo}", response_model=IcvBaciaDiaRead, summary="ICV ponderado da bacia, com a meta (D22, D29)")
def icv_bacia_dia(bacia_codigo: str, usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    resultado = calcular_icv_bacia_dia(db, bacia_codigo, data_referencia)
    if resultado is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bacia não encontrada")
    return resultado


@router.get("/icv/ranking", response_model=list[PrioridadeLinhaItem], summary="Ranking por perda absoluta, com divergência de denominador (D23, D28)")
def icv_ranking(
    usuario: LeituraPainel, db: DbSession,
    data: Optional[date] = Query(None), bacia_codigo: Optional[str] = Query(None),
):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return ranking_prioridade(db, data_referencia, bacia_codigo=bacia_codigo)


@router.get("/icv/cascata", response_model=list[CascataItem], summary="2+ perdas na mesma linha/faixa horária hoje (D24)")
def icv_cascata(
    usuario: LeituraPainel, db: DbSession,
    data: Optional[date] = Query(None), linha_codigo: Optional[str] = Query(None),
):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return detectar_cascata(db, data_referencia, linha_codigo=linha_codigo)


@router.get("/icv/motivos-livres", response_model=list[MotivoLivreItem], summary="Textos de motivo_outro mais frequentes (D27)")
def icv_motivos_livres(
    usuario: LeituraPainel, db: DbSession,
    data_inicio: date = Query(...), data_fim: date = Query(...),
):
    return motivos_livres_frequentes(db, data_inicio, data_fim)


@router.get(
    "/icv/placar/{linha_codigo}", response_model=PlacarLinhaRead,
    summary="Dado do placar impresso por linha (§7) — código, ICV da semana anterior, meta e evolução de 7 dias",
)
def icv_placar_linha(linha_codigo: str, usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return montar_placar_linha(db, linha_codigo, data_referencia)


# ============================================================================
# AÇÕES DA COORDENAÇÃO (D26) — "ação tomada" mora no coordenador, não no fiscal
# ============================================================================

@router.post("/acoes", response_model=AcaoCoordenacaoRead, status_code=status.HTTP_201_CREATED, summary="Registrar ação da coordenação (D26)")
def criar_acao_coordenacao(payload: AcaoCoordenacaoCreate, usuario: EscritaPainel, db: DbSession):
    acao = AcaoCoordenacao(
        id=uuid4(), linha_codigo=payload.linha_codigo, data_referencia=payload.data_referencia,
        faixa_hora=payload.faixa_hora, descricao=payload.descricao,
        resultado_observado=payload.resultado_observado, registrado_por=usuario.id,
    )
    db.add(acao)
    db.commit()
    db.refresh(acao)
    return acao


@router.get("/acoes", response_model=list[AcaoCoordenacaoRead], summary="Listar ações da coordenação (D26)")
def listar_acoes_coordenacao(
    usuario: LeituraPainel, db: DbSession,
    linha_codigo: Optional[str] = Query(None), data: Optional[date] = Query(None),
):
    query = select(AcaoCoordenacao).order_by(AcaoCoordenacao.criado_em.desc())
    if linha_codigo is not None:
        query = query.where(AcaoCoordenacao.linha_codigo == linha_codigo)
    if data is not None:
        query = query.where(AcaoCoordenacao.data_referencia == data)
    return db.execute(query).scalars().all()


# ============================================================================
# PAINEL DO COORDENADOR (D12, D17) — exige("fiscalizacao_painel")
# ============================================================================

def _recolhida_correlata(db: Session, prefixo: str, momento_programado: datetime) -> Optional[RecolhidaAnormal]:
    """D17, somente leitura — a recolhida mais próxima DEPOIS do horário
    programado, teto de _JANELA_RECOLHIDA. Mesma regra de
    fiscalizacao.vw_partida_recolhida, reimplementada em Python porque este
    endpoint precisa rodar igual em SQLite (testes) e Postgres (produção)."""
    return db.execute(
        select(RecolhidaAnormal)
        .where(
            RecolhidaAnormal.prefixo == prefixo,
            RecolhidaAnormal.momento >= momento_programado,
            RecolhidaAnormal.momento <= momento_programado + _JANELA_RECOLHIDA,
        )
        .order_by(RecolhidaAnormal.momento.asc())
        .limit(1)
    ).scalar_one_or_none()


@router.get(
    "/painel/{linha_codigo}", response_model=PainelLinhaResponse,
    summary="A linha inteira em ordem de hora (D12), com o cruzamento da recolhida (D17)",
)
def painel_linha(
    linha_codigo: str, usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)
):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    turnos = db.execute(
        select(Turno)
        .join(TurnoLinha, TurnoLinha.turno_id == Turno.id)
        .where(TurnoLinha.linha_codigo == linha_codigo, Turno.data_referencia == data_referencia)
    ).scalars().all()

    agora = datetime.now(FUSO_OPERACAO)
    itens: list[PainelPartidaItem] = []
    for turno in turnos:
        for item in _itens_partidas(db, turno, linha_codigo, agora):
            recolhida_momento = recolhida_avaliacao = recolhida_prazo = None
            if (
                item.estado == "PERDIDA"
                and item.registro is not None
                and item.registro.motivo in ("RA", "SOS")
                and item.registro.prefixo
            ):
                dt_programado = _datetime_da_partida(turno.data_referencia, item.horario_programado)
                recolhida = _recolhida_correlata(db, item.registro.prefixo, dt_programado)
                if recolhida is not None:
                    recolhida_momento = recolhida.momento
                    recolhida_avaliacao = recolhida.avaliacao
                    recolhida_prazo = recolhida.prazo_minutos
            itens.append(PainelPartidaItem(
                numero_tabela=item.numero_tabela,
                terminal=item.terminal,
                horario_programado=item.horario_programado,
                estado=item.estado,
                motivo=item.registro.motivo if item.registro is not None else None,
                recolhida_momento=recolhida_momento,
                recolhida_avaliacao=recolhida_avaliacao,
                recolhida_prazo_minutos=recolhida_prazo,
            ))

    itens.sort(key=lambda i: i.horario_programado)
    return PainelLinhaResponse(linha_codigo=linha_codigo, data_referencia=data_referencia, partidas=itens)
