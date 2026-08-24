"""Endpoints do módulo Fiscalização — turnos, partidas, eventos e fechamento.

🔴 O produto é o fechamento (D2): todo o resto existe para que a mensagem
saia pronta às 23h45. Prontidão AVISA, nunca bloqueia (D3) — POST /fechar
funciona com pendências, mesma filosofia da regra número um da Portaria.

⛔ Ordem de registro de rotas: `/turnos/ativo` (literal) é declarada ANTES
de `/turnos/{turno_id}` (path parameter) — mesmo bug que já aconteceu duas
vezes neste projeto (autopreencher/{id}, pre-ocorrencias/publico/{id}).
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, Response,
    UploadFile, status,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige
from app.core.uploads import ler_upload_limitado
from app.models.cadastro import Funcionario
from app.models.catalogos import Linha
from app.models.fiscalizacao import (
    AcaoCoordenacao, Baita, EventoTurno, LinhaCoordenador, ObservacaoTurno, Parametro,
    PartidaProgramada, Ponto, PontoLinha, RegistroPartida, Turno, TurnoLinha,
)
from app.models.portaria import RecolhidaAnormal
from app.schemas.fiscalizacao import (
    AcaoCoordenacaoCreate, AcaoCoordenacaoRead, BaitaRead, BaitaUpsert, CascataItem, CatalogoLinhaItem,
    EventoTurnoCreate, EventoTurnoRead, IcvCoordenadorDiaRead, IcvLinhaDiaRead,
    MinhaLinhaCreate, MinhaLinhaItem, MotivoLivreItem, ObservacaoTurnoCreate, ObservacaoTurnoRead,
    PainelAoVivoItem, PainelLinhaResponse, PainelPartidaItem, PainelTurnoAbertoItem,
    ParametrosRead, PartidaEstadoItem, PendenciaItem, Periodo, PlacarLinhaRead, PontoCreate, PontoRead,
    PontoUpdate, PrioridadeLinhaItem, ProntidaoResponse, RegistroPartidaRead,
    RegistroPartidaUpsert, TipoDia, TurnoAbrirRequest, TurnoLinhaContagemUpdate, TurnoLinhaRead,
    TurnoRead, TurnoUpdateRequest,
)
from app.services.fechamento_fiscal import _totais_da_linha, montar_fechamento
from app.services.icv import (
    calcular_icv_coordenador_dia, calcular_icv_linha_dia, detectar_cascata,
    linhas_do_coordenador, montar_placar_linha, motivos_livres_frequentes, ranking_prioridade,
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


def _eh_admin(db: Session, funcionario_id: UUID) -> bool:
    """Mesmo helper de app/routers/ocorrencias.py::_eh_admin — checa a
    função pelo modelo RBAC (funcionario_funcao → funcao), não por um
    campo legado."""
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'ADMIN'"
        ),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def _normalizar_linhas(linhas: list[str]) -> list[str]:
    """D37 — remove vazias e repetidas preservando a ordem; a lista vazia
    resultante é responsabilidade de quem chama recusar com 422."""
    vistas: list[str] = []
    for linha in linhas:
        codigo = (linha or "").strip()
        if not codigo or codigo in vistas:
            continue
        vistas.append(codigo)
    return vistas


def _exige_linha_no_catalogo(db: Session, linhas: list[str]) -> None:
    """A tela impede o erro (seletor em vez de texto livre); isto impede o
    que passar por fora dela — a API não pode aceitar lixo. Recusa com 422
    quando o código não existe no catálogo (app/models/catalogos.py::Linha)
    ou existe mas está inativo — nunca corrige/completa o código sozinho:
    adivinhar linha (ex.: "1726" → "1726-10") é pior que recusar. Origem:
    R.A registrada com "1726" nunca apareceu pro coordenador de "1726-10" —
    nenhum erro, nenhum log, só sumiu."""
    for codigo in linhas:
        linha = db.execute(select(Linha).where(Linha.codigo == codigo)).scalar_one_or_none()
        if linha is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"A linha {codigo} não existe no catálogo. Confira o código completo (ex.: 1726-10).",
            )
        if not linha.ativa:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"A linha {codigo} está inativa no catálogo.",
            )


def _aware_utc(dt: datetime) -> datetime:
    """Postgres devolve datetime aware em coluna DateTime(timezone=True);
    SQLite (testes) devolve naive mesmo nessa coluna. Mesmo padrão de
    app/routers/pre_ocorrencias_publico.py — trata naive como UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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
    """A grade do turno para uma linha, cada horário com seu estado (D7),
    unida aos registros que não casam com nenhum horário da grade (D34).

    🔴 D34 — antes desta correção, uma anormalidade registrada numa linha
    sem grade importada ficava gravada e NUNCA aparecia (nem para o
    fiscal, nem no painel, nem na prontidão): esta função devolvia []
    assim que `_vigencia_vigente` não achava vigência, e o laço percorria
    só `partida_programada`. Agora ela sempre une duas fontes: os horários
    da grade (quando existe) e os RegistroPartida do turno cuja chave
    (linha, numero_tabela, terminal, horario_programado) não bateu com
    nenhum horário programado — marcados com `fora_da_grade=True`. Quando
    não há vigência nenhuma, a função devolve só os registros avulsos —
    nunca mais `[]` com registro existindo.

    🔴 ATRASADA/AGUARDANDO são calculados aqui, a cada chamada — nunca
    gravados. Item fora da grade nunca é ATRASADA — só existe porque
    alguém respondeu. Partida com periodo IS NULL entra pelo filtro de
    horário (§7.3): aparece na listagem de qualquer período do mesmo
    dia/tipo_dia, porque o parser não conseguiu decidir a qual período ela
    pertence — melhor mostrar duas vezes do que nunca aparecer para
    ninguém responder.
    """
    vigencia = _vigencia_vigente(db, linha_codigo, turno.tipo_dia, turno.data_referencia)

    partidas_prog: list[PartidaProgramada] = []
    if vigencia is not None:
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
    chaves_da_grade: set = set()
    for pp in partidas_prog:
        chave = (pp.linha_codigo, pp.numero_tabela, pp.terminal, pp.horario)
        chaves_da_grade.add(chave)
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
            fora_da_grade=False,
            registro=RegistroPartidaRead.model_validate(registro) if registro is not None else None,
        ))

    for chave, registro in registros.items():
        if chave in chaves_da_grade:
            continue
        itens.append(PartidaEstadoItem(
            partida_programada_id=None,
            numero_tabela=registro.numero_tabela,
            terminal=registro.terminal,
            horario_programado=registro.horario_programado,
            periodo=None,
            estado=registro.resultado,
            fora_da_grade=True,
            registro=RegistroPartidaRead.model_validate(registro),
        ))

    itens.sort(key=lambda i: i.horario_programado)
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
# CATÁLOGO DE LINHAS — leitura do catálogo do Pátio, servida por aqui
# ============================================================================

@router.get(
    "/catalogo/linhas", response_model=list[CatalogoLinhaItem],
    summary="Linhas do catálogo (Pátio), servidas pela própria Fiscalização",
)
def catalogo_linhas(usuario: LeituraFiscalizacao, db: DbSession, incluir_inativas: bool = Query(False)):
    """Existe aqui — e não em GET /linhas (app/routers/linhas.py) — porque
    o fiscal não tem acesso ao módulo Pátio, e a Fiscalização não pode
    depender do RBAC de outro módulo pra saber que linhas existem. Somente
    leitura: quem cria/edita linha continua sendo o Pátio; nenhuma FK nova
    é criada, esta consulta é só leitura do catálogo por código (regra de
    fronteira do módulo, §5 do desenho)."""
    query = select(Linha)
    if not incluir_inativas:
        query = query.where(Linha.ativa.is_(True))
    return db.execute(query.order_by(Linha.codigo)).scalars().all()


# ============================================================================
# CATÁLOGO DE PONTOS
# ============================================================================

@router.get("/pontos", response_model=list[PontoRead], summary="Pontos com suas linhas — só ativos por padrão")
def listar_pontos(usuario: LeituraFiscalizacao, db: DbSession, incluir_inativos: bool = Query(False)):
    query = select(Ponto)
    if not incluir_inativos:
        query = query.where(Ponto.ativo.is_(True))
    pontos = db.execute(query.order_by(Ponto.codigo)).scalars().all()
    resultado = []
    for p in pontos:
        linhas = db.execute(
            select(PontoLinha.linha_codigo)
            .where(PontoLinha.ponto_codigo == p.codigo, PontoLinha.ativo.is_(True))
            .order_by(PontoLinha.linha_codigo)
        ).scalars().all()
        resultado.append(PontoRead(codigo=p.codigo, nome=p.nome, terminal=p.terminal, ativo=p.ativo, linhas=list(linhas)))
    return resultado


@router.post(
    "/pontos", response_model=PontoRead, status_code=status.HTTP_201_CREATED,
    summary="Cadastra ponto (D37) — o fiscal cria na hora, se não existir",
)
def criar_ponto(payload: PontoCreate, usuario: EscritaFiscalizacao, db: DbSession):
    codigo = payload.codigo.strip().upper()
    if not codigo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Código do ponto não pode ser vazio.")
    if db.get(Ponto, codigo) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe um ponto com o código '{codigo}'.")

    linhas = _normalizar_linhas(payload.linhas)
    if not linhas:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Informe ao menos uma linha.")
    _exige_linha_no_catalogo(db, linhas)

    ponto = Ponto(codigo=codigo, nome=payload.nome.strip(), terminal=payload.terminal, ativo=True)
    db.add(ponto)
    db.flush()
    for linha_codigo in linhas:
        db.add(PontoLinha(ponto_codigo=codigo, linha_codigo=linha_codigo))
    db.commit()
    return PontoRead(codigo=ponto.codigo, nome=ponto.nome, terminal=ponto.terminal, ativo=ponto.ativo, linhas=linhas)


@router.patch(
    "/pontos/{codigo}", response_model=PontoRead,
    summary="Renomeia, ativa/desativa e substitui as linhas do ponto (D37) — nunca DELETE",
)
def atualizar_ponto(codigo: str, payload: PontoUpdate, usuario: EscritaFiscalizacao, db: DbSession):
    ponto = db.get(Ponto, codigo)
    if ponto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ponto não encontrado")

    dados = payload.model_dump(exclude_unset=True)
    if "nome" in dados:
        ponto.nome = dados["nome"].strip()
    if "ativo" in dados:
        ponto.ativo = dados["ativo"]
    if "linhas" in dados:
        linhas = _normalizar_linhas(dados["linhas"] or [])
        if not linhas:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Informe ao menos uma linha.")
        _exige_linha_no_catalogo(db, linhas)
        existentes = {
            pl.linha_codigo: pl
            for pl in db.execute(select(PontoLinha).where(PontoLinha.ponto_codigo == codigo)).scalars().all()
        }
        for linha_codigo in linhas:
            if linha_codigo in existentes:
                existentes[linha_codigo].ativo = True
            else:
                db.add(PontoLinha(ponto_codigo=codigo, linha_codigo=linha_codigo))
        for linha_codigo, pl in existentes.items():
            if linha_codigo not in linhas:
                pl.ativo = False

    db.commit()
    db.refresh(ponto)
    linhas_atuais = db.execute(
        select(PontoLinha.linha_codigo)
        .where(PontoLinha.ponto_codigo == codigo, PontoLinha.ativo.is_(True))
        .order_by(PontoLinha.linha_codigo)
    ).scalars().all()
    return PontoRead(codigo=ponto.codigo, nome=ponto.nome, terminal=ponto.terminal, ativo=ponto.ativo, linhas=list(linhas_atuais))


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


@router.patch(
    "/turnos/{turno_id}/linhas/{linha_codigo}", response_model=TurnoLinhaRead,
    summary="Contagem informada pelo fiscal quando a linha não tem grade (D35)",
)
def atualizar_contagem_linha(
    turno_id: UUID, linha_codigo: str, payload: TurnoLinhaContagemUpdate, usuario: EscritaFiscalizacao, db: DbSession
):
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Turno não encontrado")
    _exige_dono(turno, usuario)
    turno_linha = db.execute(
        select(TurnoLinha).where(TurnoLinha.turno_id == turno_id, TurnoLinha.linha_codigo == linha_codigo)
    ).scalar_one_or_none()
    if turno_linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Esta linha não está no turno.")
    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(turno_linha, campo, valor)
    db.commit()
    db.refresh(turno_linha)
    return turno_linha


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

    # D33 — numero_tabela é opcional; a UNIQUE do banco não protege
    # duplicata quando é NULL (NULL não colide com NULL). A guarda é este
    # SELECT: `Coluna == None` é traduzido pelo SQLAlchemy para
    # `IS NULL` automaticamente (comportamento padrão de
    # ColumnOperators.__eq__), então quando payload.numero_tabela é None
    # este SELECT já encontra o registro sem tabela informada do mesmo
    # turno/linha/terminal/horário e cai no UPDATE abaixo — não precisa de
    # nenhum código a mais além do tipo do campo ter virado Optional.
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

    # D36 — sem grade não existe "partida sem resposta" para cobrar; o que
    # cobra é a contagem que o fiscal precisa digitar na mão. Mesma função
    # do fechamento (_totais_da_linha) decide se a linha tem grade — não
    # reimplementar esta checagem aqui (ver docstring da função).
    linhas_sem_contagem = []
    for linha_codigo in linhas:
        programadas, realizadas, _extras, fonte = _totais_da_linha(db, turno, linha_codigo)
        if fonte == "INFORMADO" and (programadas is None or realizadas is None):
            linhas_sem_contagem.append(linha_codigo)
    if linhas_sem_contagem:
        pendencias.append(PendenciaItem(tipo="CONTAGEM_NAO_INFORMADA", linhas=linhas_sem_contagem))

    # D15/D36 — refeição do fiscal é linha fixa do OBS; sem ela o
    # fechamento sai incompleto.
    if turno.refeicao_inicio is None or turno.refeicao_fim is None:
        pendencias.append(PendenciaItem(tipo="REFEICAO_NAO_INFORMADA"))

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
# CATÁLOGO DO COORDENADOR — quem coordena o quê (não existe mais "bacia")
# ============================================================================

@router.get(
    "/minhas-linhas", response_model=list[MinhaLinhaItem],
    summary="Linhas do funcionário logado, em todos os períodos em que ele coordena",
)
def minhas_linhas(usuario: LeituraPainel, db: DbSession):
    return linhas_do_coordenador(db, usuario.id)


@router.post(
    "/minhas-linhas", response_model=MinhaLinhaItem, status_code=status.HTTP_201_CREATED,
    summary="Coordenador atribui uma linha a si mesmo (D38); ADMIN pode atribuir a outro",
)
def atribuir_minha_linha(payload: MinhaLinhaCreate, usuario: EscritaPainel, db: DbSession):
    alvo_id = usuario.id
    if payload.funcionario_id is not None and payload.funcionario_id != usuario.id:
        if not _eh_admin(db, usuario.id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Só ADMIN pode atribuir linha a outro funcionário.")
        alvo_id = payload.funcionario_id

    linha_codigo = payload.linha_codigo.strip()
    _exige_linha_no_catalogo(db, [linha_codigo])
    existente = db.execute(
        select(LinhaCoordenador).where(
            LinhaCoordenador.linha_codigo == linha_codigo, LinhaCoordenador.periodo == payload.periodo,
        )
    ).scalar_one_or_none()
    # ⛔ A mensagem nunca diz de quem é a linha (nome próprio não trafega
    # em mensagem de erro) — D38.
    if existente is not None and existente.ativo and existente.funcionario_id != alvo_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Esta linha já está atribuída a outro coordenador neste período.",
        )

    if existente is not None:
        existente.funcionario_id = alvo_id
        existente.ativo = True
    else:
        db.add(LinhaCoordenador(linha_codigo=linha_codigo, funcionario_id=alvo_id, periodo=payload.periodo, ativo=True))
    db.commit()
    return MinhaLinhaItem(linha_codigo=linha_codigo, periodo=payload.periodo)


@router.delete(
    "/minhas-linhas/{linha_codigo}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a própria atribuição (D38); ADMIN remove de qualquer funcionário",
)
def remover_minha_linha(linha_codigo: str, usuario: EscritaPainel, db: DbSession, periodo: Periodo = Query(...)):
    # A linha do banco é achada só por (linha_codigo, periodo) — a UNIQUE
    # da tabela garante que não há ambiguidade. ADMIN passa aqui de
    # qualquer forma (exceção de D38); qualquer outro só remove a própria.
    registro = db.execute(
        select(LinhaCoordenador).where(
            LinhaCoordenador.linha_codigo == linha_codigo,
            LinhaCoordenador.periodo == periodo,
            LinhaCoordenador.ativo.is_(True),
        )
    ).scalar_one_or_none()
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Atribuição não encontrada.")
    if registro.funcionario_id != usuario.id and not _eh_admin(db, usuario.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Esta linha é de outro coordenador.")
    registro.ativo = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/parametros", response_model=ParametrosRead, summary="Meta e aceitável do ICV (D29)")
def listar_parametros(usuario: LeituraPainel, db: DbSession):
    linhas = db.execute(select(Parametro.chave, Parametro.valor)).all()
    valores = {chave: float(valor) for chave, valor in linhas}
    return ParametrosRead(icv_meta=valores.get("icv_meta"), icv_aceitavel=valores.get("icv_aceitavel"))


# ============================================================================
# ICV E PRIORIDADE (D20-D23, D28, D30) — exige("fiscalizacao_painel")
# ============================================================================

@router.get("/icv/linha/{linha_codigo}", response_model=IcvLinhaDiaRead, summary="ICV das duas fontes, por linha e dia (D20)")
def icv_linha_dia(linha_codigo: str, usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return calcular_icv_linha_dia(db, linha_codigo, data_referencia)


@router.get(
    "/icv/coordenador", response_model=IcvCoordenadorDiaRead,
    summary="ICV ponderado das linhas do funcionário logado, com meta e aceitável (D22, D29)",
)
def icv_coordenador_dia(usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return calcular_icv_coordenador_dia(db, usuario.id, data_referencia)


@router.get("/icv/ranking", response_model=list[PrioridadeLinhaItem], summary="Ranking por perda absoluta, com divergência de denominador (D23, D28)")
def icv_ranking(usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    return ranking_prioridade(db, data_referencia)


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


# ============================================================================
# PAINEL AO VIVO (D39) — o topo da tela do coordenador
#
# 🔴 Estas duas rotas LITERAIS precisam ficar ANTES de /painel/{linha_codigo}:
# com o path parameter declarado primeiro, "ao-vivo" e "turnos" chegariam
# como codigo de linha. Mesmo bug que ja aconteceu tres vezes neste projeto
# (autopreencher/{id}, pre-ocorrencias/publico/{id}, turnos/ativo).
# ============================================================================

def _momento_registro(registro: RegistroPartida) -> datetime:
    """Quando este registro passou a valer — a ultima resposta do fiscal,
    nao a primeira: ele pode corrigir o que marcou (upsert)."""
    return _aware_utc(registro.atualizado_em or registro.registrado_em)


def _turnos_das_linhas(db: Session, data_referencia: date, linhas: set[str]) -> list[Turno]:
    """Turnos do dia que cobrem pelo menos uma destas linhas. Conjunto vazio
    devolve lista vazia sem ir ao banco — coordenador sem linha atribuida
    (D40) e' caso normal, nao erro."""
    if not linhas:
        return []
    return list(
        db.execute(
            select(Turno)
            .join(TurnoLinha, TurnoLinha.turno_id == Turno.id)
            .where(Turno.data_referencia == data_referencia, TurnoLinha.linha_codigo.in_(linhas))
            .distinct()
        ).scalars().all()
    )


def _ultimo_momento_do_turno(db: Session, turno_id: UUID) -> Optional[datetime]:
    """O registro OU evento mais recente deste turno. None = o fiscal abriu
    o turno e ainda nao marcou nada — diferente de "parou ha muito tempo"."""
    momentos: list[datetime] = [
        _momento_registro(r)
        for r in db.execute(
            select(RegistroPartida).where(RegistroPartida.turno_id == turno_id)
        ).scalars().all()
    ]
    momentos += [
        _aware_utc(e.criado_em)
        for e in db.execute(
            select(EventoTurno).where(EventoTurno.turno_id == turno_id)
        ).scalars().all()
    ]
    return max(momentos) if momentos else None


@router.get(
    "/painel/ao-vivo", response_model=list[PainelAoVivoItem],
    summary="O que os fiscais registraram hoje nas linhas do coordenador logado (D39)",
)
def painel_ao_vivo(
    usuario: LeituraPainel, db: DbSession,
    data: Optional[date] = Query(None),
    limite: int = Query(100, ge=1, le=500, description="Quantos itens mais recentes devolver"),
):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    minhas = {item["linha_codigo"] for item in linhas_do_coordenador(db, usuario.id)}
    turnos = _turnos_das_linhas(db, data_referencia, minhas)
    if not turnos:
        return []

    por_turno = {turno.id: turno for turno in turnos}
    ids = list(por_turno.keys())
    agora = datetime.now(timezone.utc)
    pares: list[tuple[datetime, PainelAoVivoItem]] = []

    registros = db.execute(
        select(RegistroPartida).where(
            RegistroPartida.turno_id.in_(ids), RegistroPartida.linha_codigo.in_(minhas)
        )
    ).scalars().all()
    for registro in registros:
        turno = por_turno[registro.turno_id]
        momento = _momento_registro(registro)
        perdida = registro.resultado == "PERDIDA"
        pares.append((momento, PainelAoVivoItem(
            linha_codigo=registro.linha_codigo,
            numero_tabela=registro.numero_tabela,
            tipo=(registro.motivo or "OUTRO") if perdida else "REALIZADA",
            custou_viagem=perdida,
            horario=registro.horario_programado,
            ponto_codigo=turno.ponto_codigo,
            fiscal_re=turno.fiscal_re,
            minutos_atras=max(0, int((agora - momento).total_seconds() // 60)),
        )))

    # D4 — so' os eventos AVULSOS. O evento vinculado a uma partida perdida
    # ja' entrou acima pelo registro; lista-lo de novo contaria duas vezes na
    # tela o que o banco guarda uma vez so'.
    eventos = db.execute(
        select(EventoTurno).where(
            EventoTurno.turno_id.in_(ids),
            EventoTurno.linha_codigo.in_(minhas),
            EventoTurno.registro_partida_id.is_(None),
        )
    ).scalars().all()
    for evento in eventos:
        turno = por_turno[evento.turno_id]
        momento = _aware_utc(evento.criado_em)
        pares.append((momento, PainelAoVivoItem(
            linha_codigo=evento.linha_codigo,
            numero_tabela=evento.numero_tabela,
            tipo=evento.tipo,
            custou_viagem=False,
            horario=evento.horario,
            ponto_codigo=turno.ponto_codigo,
            fiscal_re=turno.fiscal_re,
            minutos_atras=max(0, int((agora - momento).total_seconds() // 60)),
        )))

    pares.sort(key=lambda par: par[0], reverse=True)
    return [item for _, item in pares[:limite]]


@router.get(
    "/painel/turnos", response_model=list[PainelTurnoAbertoItem],
    summary="Quem esta' na rua agora nas linhas do coordenador logado (D39)",
)
def painel_turnos_abertos(
    usuario: LeituraPainel, db: DbSession, data: Optional[date] = Query(None)
):
    data_referencia = data or datetime.now(FUSO_OPERACAO).date()
    minhas = {item["linha_codigo"] for item in linhas_do_coordenador(db, usuario.id)}
    agora = datetime.now(timezone.utc)

    itens: list[PainelTurnoAbertoItem] = []
    for turno in _turnos_das_linhas(db, data_referencia, minhas):
        if turno.status != "ABERTO":
            continue
        funcionario = db.get(Funcionario, turno.funcionario_id)
        ultimo = _ultimo_momento_do_turno(db, turno.id)
        itens.append(PainelTurnoAbertoItem(
            turno_id=turno.id,
            fiscal_nome=funcionario.nome if funcionario is not None else "—",
            fiscal_re=turno.fiscal_re,
            ponto_codigo=turno.ponto_codigo,
            terminal=turno.terminal,
            periodo=turno.periodo,
            linhas=_linhas_do_turno(db, turno.id),
            aberto_em=turno.aberto_em,
            minutos_sem_registrar=(
                None if ultimo is None else max(0, int((agora - ultimo).total_seconds() // 60))
            ),
        ))

    # ⛔ Nao ordenar por (is None, aberto_em) puro: com dois turnos sem
    # aberto_em, o desempate compararia None com None e levantaria
    # TypeError. E aberto_em pode vir aware (Postgres) ou naive (SQLite).
    _INICIO = datetime.min.replace(tzinfo=timezone.utc)
    itens.sort(key=lambda i: _aware_utc(i.aberto_em) if i.aberto_em else _INICIO)
    return itens


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
