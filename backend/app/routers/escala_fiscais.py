"""Aba Escala de Fiscais (módulo COORDENADORIA) — Fase 2: cadastros e
importação dos modelos.

⛔ Só cadastros. Montar a escala do dia, publicar, a tela do fiscal e a
impressão são da próxima fase — nenhuma rota aqui escreve em
escala_fiscal_dia, alocacao, plantao ou alteracao.

Regras que atravessam o arquivo:
  · Leitura com exige("escala_fiscal"); escrita com exige("escala_fiscal",
    escrever=True). O fiscal (escala_fiscal_propria) não entra aqui.
  · D-A (24/09): o FISCAL é o RE em TEXTO, sem cadastro obrigatório (quadro,
    fiscal padrão do modelo, ausência, troca). O nome vem de funcionario por
    junção pelo RE NA LEITURA; sem cadastro, nome nulo. RE normalizado pela
    MESMA função do cadastro de Pessoas (app/core/registro.py: trim +
    maiúsculas, nunca vira número, zero à esquerda preservado).
  · O COORDENADOR continua exigindo cadastro: RE fora de funcionario → 422
    "RE X não está cadastrado. Cadastre em Pessoas antes".
  · Horário do fiscal: término > início (não passa da meia-noite). Do
    coordenador de plantão: pode passar da meia-noite, só não início = fim.
    O padrão de cada período (D-E) vem de Settings, nunca fixo aqui.
  · Datas de calendário (vira à meia-noite): "hoje" vem de FUSO_OPERACAO,
    ⛔ nunca date.today() nem datetime.now(timezone.utc).date().
  · ⛔ Rotas literais ANTES das rotas com parâmetro (mesma classe de bug de
    /autopreencher × /{ocorrencia_id} em ocorrencias.py).
"""
import json
import re
from datetime import date, datetime, time, timedelta
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import get_db
from app.core.deps import exige
from app.core.registro import normalizar_re
from app.core.uploads import ler_upload_limitado
from app.models.cadastro import Funcionario
from app.models.escala_fiscais import (
    EscalaFiscalAusencia,
    EscalaFiscalCoordenadorHorario,
    EscalaFiscalCoordenadorPeriodo,
    EscalaFiscalModelo,
    EscalaFiscalModeloPosto,
    EscalaFiscalPontoFinal,
    EscalaFiscalPosto,
    EscalaFiscalPostoLinha,
    EscalaFiscalQuadro,
    EscalaFiscalTroca,
)
from app.schemas.escala_fiscais import (
    AusenciaCreate,
    AusenciaRead,
    AusenciaUpdate,
    CoordenadorHorarioCreate,
    CoordenadorHorarioRead,
    CoordenadorHorarioUpdate,
    CoordenadorPeriodoCreate,
    CoordenadorPeriodoRead,
    FiscalResumo,
    ModeloCreate,
    ModeloDetalhe,
    ModeloPostoCreate,
    ModeloPostoRead,
    ModeloPostoUpdate,
    ModeloRead,
    PessoaBusca,
    PontoFinalCreate,
    PontoFinalRead,
    PontoFinalUpdate,
    PostoCreate,
    PostoRead,
    PostoUpdate,
    QuadroCreate,
    QuadroRead,
    QuadroUpdate,
    TrocaCreate,
    TrocaRead,
)
from app.services import escala_fiscais_importacao as importacao

router = APIRouter(prefix="/escala-fiscais", tags=["escala de fiscais"])

LeituraEscala = Annotated[Funcionario, Depends(exige("escala_fiscal"))]
EscritaEscala = Annotated[Funcionario, Depends(exige("escala_fiscal", escrever=True))]
DbSession = Annotated[Session, Depends(get_db)]

TAMANHO_MAXIMO_JSON = 2 * 1024 * 1024  # 2 MB — o JSON real tem ~120 KB
_RE_OUTRA_GARAGEM = re.compile(r"^G\d+$")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def hoje_operacao() -> date:
    """Data de CALENDÁRIO em São Paulo (vira à meia-noite)."""
    return datetime.now(FUSO_OPERACAO).date()


def padroes_periodo() -> dict[int, tuple[time, time]]:
    """D-E: horário padrão de cada período, configurável no .env."""
    s = get_settings()
    return {
        1: importacao.padrao_de_texto(s.escala_fiscal_padrao_periodo_1),
        2: importacao.padrao_de_texto(s.escala_fiscal_padrao_periodo_2),
    }


def _coordenador_por_re(db: Session, re_txt: str) -> Funcionario:
    """Coordenador precisa de cadastro (FK em funcionario)."""
    re_limpo = normalizar_re(re_txt) or ""
    func_ = db.execute(select(Funcionario).where(Funcionario.re == re_limpo)).scalar_one_or_none()
    if func_ is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"RE {re_limpo} não está cadastrado. Cadastre em Pessoas antes",
        )
    return func_


def nomes_por_re(db: Session, res) -> dict[str, str]:
    """D-A: RE → nome, só dos REs que têm cadastro. Junção pelo RE na leitura."""
    res = {r for r in res if r}
    if not res:
        return {}
    return {f.re: f.nome for f in db.execute(select(Funcionario).where(Funcionario.re.in_(res))).scalars()}


def _fiscal(re_txt: str, nomes: dict[str, str]) -> dict:
    return {"re": re_txt, "nome": nomes.get(re_txt), "cadastrado": re_txt in nomes}


def _pessoas(db: Session, ids) -> dict[UUID, Funcionario]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {f.id: f for f in db.execute(select(Funcionario).where(Funcionario.id.in_(ids))).scalars()}


def _coordenador(f: Funcionario) -> dict:
    return {"funcionario_id": f.id, "re": f.re, "nome": f.nome}


def _nao_encontrado(o_que: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{o_que} não encontrado")


def _validar_horario_fiscal(inicio: Optional[time], termino: Optional[time]) -> None:
    if inicio and termino and termino <= inicio:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O término tem que ser depois do início: posto de fiscal não passa da meia-noite",
        )


def _validar_horario_coordenador(inicio: time, fim: time) -> None:
    if inicio == fim:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Início e fim do plantão não podem ser iguais",
        )


def _passa_meia_noite(inicio: time, fim: time) -> bool:
    return fim < inicio


def _validar_periodo_ausencia(inicio: date, fim: Optional[date]) -> None:
    if fim is not None and fim < inicio:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A data fim não pode ser antes da data início",
        )


# ─── BUSCA DE PESSOA (autocomplete de RE) ─────────────────────────────────────

@router.get("/pessoas", response_model=list[PessoaBusca], summary="Autocomplete: RE e nome por RE ou pedaço do nome")
def buscar_pessoas(
    _: LeituraEscala,
    db: DbSession,
    q: str = Query(..., min_length=2, max_length=40),
):
    termo = q.strip()
    rows = db.execute(
        select(Funcionario)
        .where(
            Funcionario.status != "DESLIGADO",
            or_(Funcionario.re.ilike(f"{termo}%"), Funcionario.nome.ilike(f"%{termo}%")),
        )
        .order_by(func.length(Funcionario.re), Funcionario.re)
        .limit(10)
    ).scalars().all()
    return [PessoaBusca(re=f.re, nome=f.nome) for f in rows]


# ─── IMPORTAÇÃO (literal — antes de qualquer /{id}) ───────────────────────────

async def _ler_json_enviado(arquivo: UploadFile, request: Request):
    conteudo = await ler_upload_limitado(arquivo, TAMANHO_MAXIMO_JSON, request)
    try:
        return json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"O arquivo não é um JSON válido ({exc.__class__.__name__}).",
        )


def _analisar(db: Session, dados) -> importacao.Analise:
    return importacao.analisar(
        dados, lambda res: nomes_por_re(db, res), padroes_periodo(),
        modelos_existentes=importacao.modelos_com_postos(db),
        postos_existentes=importacao.chaves_postos_existentes(db),
    )


@router.post("/importacao/simular", summary="SIMULA a importação dos modelos — não grava nada, devolve o relatório")
async def simular_importacao(
    _: EscritaEscala,
    db: DbSession,
    request: Request,
    arquivo: Annotated[UploadFile, File(description="JSON extraído da planilha de escala de fiscais")],
):
    dados = await _ler_json_enviado(arquivo, request)
    analise = _analisar(db, dados)
    # ⛔ Nenhum add/flush/commit aqui — a simulação só lê.
    db.rollback()
    return analise.relatorio


@router.post("/importacao/confirmar", summary="CONFIRMA a importação: grava postos, linhas, modelos e postos do modelo")
async def confirmar_importacao(
    _: EscritaEscala,
    db: DbSession,
    request: Request,
    arquivo: Annotated[UploadFile, File(description="O MESMO JSON da simulação, já corrigido na tela")],
):
    dados = await _ler_json_enviado(arquivo, request)
    analise = _analisar(db, dados)
    if not analise.pode_confirmar:
        bloqueantes = analise.relatorio["resumo"]["problemas_bloqueantes"]
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Nada foi gravado: a simulação ainda tem {bloqueantes} problema(s) que impedem "
                f"a importação. Simule de novo e corrija na tela."
                if bloqueantes else "Nada foi gravado: o arquivo não tem nenhum posto para importar."
            ),
        )
    try:
        resultado = importacao.gravar(db, analise)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"gravado": True, **resultado, "relatorio": analise.relatorio}


# ─── QUADRO DE FISCAIS ───────────────────────────────────────────────────────

def _quadro_read(q: EscalaFiscalQuadro, nomes: dict[str, str]) -> QuadroRead:
    return QuadroRead(**_fiscal(q.re, nomes), periodo=q.periodo, folga_base=q.folga_base, ativo=q.ativo)


@router.get("/quadro", response_model=list[QuadroRead], summary="Fiscais do quadro, com período e folga base")
def listar_quadro(_: LeituraEscala, db: DbSession):
    linhas = db.execute(select(EscalaFiscalQuadro)).scalars().all()
    nomes = nomes_por_re(db, (q.re for q in linhas))
    itens = [_quadro_read(q, nomes) for q in linhas]
    return sorted(itens, key=lambda i: (i.periodo, len(i.re), i.re))


@router.post("/quadro", response_model=QuadroRead, status_code=status.HTTP_201_CREATED, summary="Põe um fiscal no quadro (RE sem cadastro é aceito)")
def adicionar_quadro(payload: QuadroCreate, _: EscritaEscala, db: DbSession):
    if db.get(EscalaFiscalQuadro, payload.re) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"RE {payload.re} já está no quadro")
    q = EscalaFiscalQuadro(re=payload.re, periodo=payload.periodo, folga_base=payload.folga_base, ativo=payload.ativo)
    db.add(q)
    db.commit()
    return _quadro_read(q, nomes_por_re(db, [q.re]))


# ─── COORDENADORES ───────────────────────────────────────────────────────────

@router.get("/coordenadores/periodos", response_model=list[CoordenadorPeriodoRead], summary="Qual coordenador edita qual período")
def listar_coordenador_periodo(_: LeituraEscala, db: DbSession):
    linhas = db.execute(select(EscalaFiscalCoordenadorPeriodo)).scalars().all()
    pessoas = _pessoas(db, (c.funcionario_id for c in linhas))
    itens = [
        CoordenadorPeriodoRead(**_coordenador(pessoas[c.funcionario_id]), periodo=c.periodo)
        for c in linhas if c.funcionario_id in pessoas
    ]
    return sorted(itens, key=lambda i: (i.periodo, i.nome))


@router.post("/coordenadores/periodos", response_model=CoordenadorPeriodoRead, status_code=status.HTTP_201_CREATED, summary="Liga um coordenador a um período")
def adicionar_coordenador_periodo(payload: CoordenadorPeriodoCreate, _: EscritaEscala, db: DbSession):
    f = _coordenador_por_re(db, payload.re)
    if db.get(EscalaFiscalCoordenadorPeriodo, (f.id, payload.periodo)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"RE {f.re} já responde pelo período {payload.periodo}")
    db.add(EscalaFiscalCoordenadorPeriodo(funcionario_id=f.id, periodo=payload.periodo))
    db.commit()
    return CoordenadorPeriodoRead(**_coordenador(f), periodo=payload.periodo)


def _horario_read(h: EscalaFiscalCoordenadorHorario, f: Funcionario) -> CoordenadorHorarioRead:
    return CoordenadorHorarioRead(
        **_coordenador(f), id=h.id, turno=h.turno, hora_inicio=h.hora_inicio, hora_fim=h.hora_fim,
        ativo=h.ativo, passa_meia_noite=_passa_meia_noite(h.hora_inicio, h.hora_fim),
    )


@router.get("/coordenadores/horarios", response_model=list[CoordenadorHorarioRead], summary="Horário padrão dos coordenadores de plantão")
def listar_coordenador_horario(_: LeituraEscala, db: DbSession):
    linhas = db.execute(select(EscalaFiscalCoordenadorHorario)).scalars().all()
    pessoas = _pessoas(db, (h.funcionario_id for h in linhas))
    itens = [_horario_read(h, pessoas[h.funcionario_id]) for h in linhas if h.funcionario_id in pessoas]
    return sorted(itens, key=lambda i: (i.turno != "manha", i.hora_inicio, i.nome))


@router.post("/coordenadores/horarios", response_model=CoordenadorHorarioRead, status_code=status.HTTP_201_CREATED, summary="Cadastra o horário padrão de um coordenador (pode passar da meia-noite)")
def adicionar_coordenador_horario(payload: CoordenadorHorarioCreate, _: EscritaEscala, db: DbSession):
    f = _coordenador_por_re(db, payload.re)
    _validar_horario_coordenador(payload.hora_inicio, payload.hora_fim)
    existe = db.execute(
        select(EscalaFiscalCoordenadorHorario).where(
            EscalaFiscalCoordenadorHorario.funcionario_id == f.id,
            EscalaFiscalCoordenadorHorario.turno == payload.turno,
        )
    ).scalar_one_or_none()
    if existe is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"RE {f.re} já tem horário no turno {payload.turno}")
    h = EscalaFiscalCoordenadorHorario(
        funcionario_id=f.id, turno=payload.turno, hora_inicio=payload.hora_inicio,
        hora_fim=payload.hora_fim, ativo=payload.ativo,
    )
    db.add(h)
    db.commit()
    return _horario_read(h, f)


# ─── PONTOS FINAIS ───────────────────────────────────────────────────────────

@router.get("/pontos-finais", response_model=list[PontoFinalRead], summary="Pontos finais (base da regra de acúmulo)")
def listar_pontos_finais(_: LeituraEscala, db: DbSession):
    pontos = db.execute(select(EscalaFiscalPontoFinal).order_by(EscalaFiscalPontoFinal.nome)).scalars().all()
    contagem = dict(db.execute(
        select(EscalaFiscalPosto.ponto_final_id, func.count())
        .where(EscalaFiscalPosto.ponto_final_id.is_not(None))
        .group_by(EscalaFiscalPosto.ponto_final_id)
    ).all())
    return [PontoFinalRead(id=p.id, nome=p.nome, ativo=p.ativo, qtd_postos=contagem.get(p.id, 0)) for p in pontos]


@router.post("/pontos-finais", response_model=PontoFinalRead, status_code=status.HTTP_201_CREATED, summary="Cadastra ponto final")
def criar_ponto_final(payload: PontoFinalCreate, _: EscritaEscala, db: DbSession):
    nome = " ".join(payload.nome.split())
    if db.execute(select(EscalaFiscalPontoFinal).where(func.lower(EscalaFiscalPontoFinal.nome) == nome.lower())).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe o ponto final \"{nome}\"")
    p = EscalaFiscalPontoFinal(nome=nome, ativo=payload.ativo)
    db.add(p)
    db.commit()
    return PontoFinalRead(id=p.id, nome=p.nome, ativo=p.ativo)


# ─── POSTOS ──────────────────────────────────────────────────────────────────

def _posto_read(p: EscalaFiscalPosto, nomes_ponto: dict[UUID, str]) -> PostoRead:
    return PostoRead(
        id=p.id, lado=p.lado, cod_jb=p.cod_jb, lote=p.lote, ponto_final_id=p.ponto_final_id,
        ponto_final_nome=nomes_ponto.get(p.ponto_final_id) if p.ponto_final_id else None,
        linhas=[pl.linha for pl in p.linhas], ativo=p.ativo,
    )


def _nomes_pontos(db: Session) -> dict[UUID, str]:
    return {p.id: p.nome for p in db.execute(select(EscalaFiscalPontoFinal)).scalars()}


def _checar_ponto_final(db: Session, ponto_final_id: Optional[UUID]) -> None:
    if ponto_final_id is not None and db.get(EscalaFiscalPontoFinal, ponto_final_id) is None:
        raise _nao_encontrado("Ponto final")


def _checar_posto_duplicado(db: Session, lado: str, linhas: list[str], ignorar: Optional[UUID] = None) -> None:
    chave = importacao.posto_chave(lado, linhas)
    for p in db.execute(select(EscalaFiscalPosto)).scalars():
        if p.id != ignorar and importacao.posto_chave(p.lado, [pl.linha for pl in p.linhas]) == chave:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Já existe posto {lado} com as linhas {', '.join(linhas)}",
            )


@router.get("/postos", response_model=list[PostoRead], summary="Postos com as suas linhas")
def listar_postos(_: LeituraEscala, db: DbSession, ponto_final_id: Optional[UUID] = None):
    consulta = select(EscalaFiscalPosto)
    if ponto_final_id is not None:
        consulta = consulta.where(EscalaFiscalPosto.ponto_final_id == ponto_final_id)
    postos = db.execute(consulta).scalars().all()
    nomes = _nomes_pontos(db)
    itens = [_posto_read(p, nomes) for p in postos]
    return sorted(itens, key=lambda i: (i.lado, i.linhas))


@router.post("/postos", response_model=PostoRead, status_code=status.HTTP_201_CREATED, summary="Cadastra posto com as linhas")
def criar_posto(payload: PostoCreate, _: EscritaEscala, db: DbSession):
    _checar_ponto_final(db, payload.ponto_final_id)
    _checar_posto_duplicado(db, payload.lado, payload.linhas)
    p = EscalaFiscalPosto(
        lado=payload.lado, cod_jb=payload.cod_jb or None, lote=payload.lote or None,
        ponto_final_id=payload.ponto_final_id, ativo=payload.ativo,
    )
    p.linhas = [EscalaFiscalPostoLinha(linha=ln, ordem=i + 1) for i, ln in enumerate(payload.linhas)]
    db.add(p)
    db.commit()
    return _posto_read(p, _nomes_pontos(db))


# ─── MODELOS ─────────────────────────────────────────────────────────────────

def _validar_paridade(tipo_dia: str, paridade: Optional[str]) -> None:
    if tipo_dia in ("sabado", "domingo") and paridade is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Sábado e domingo precisam da paridade do mês (ímpar ou par)")
    if tipo_dia not in ("sabado", "domingo") and paridade is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Paridade só vale para sábado e domingo")


@router.get("/modelos", response_model=list[ModeloRead], summary="Modelos (gabaritos) de cada tipo de dia")
def listar_modelos(_: LeituraEscala, db: DbSession):
    modelos = db.execute(select(EscalaFiscalModelo).order_by(EscalaFiscalModelo.nome)).scalars().all()
    contagem = dict(db.execute(
        select(EscalaFiscalModeloPosto.modelo_id, func.count()).group_by(EscalaFiscalModeloPosto.modelo_id)
    ).all())
    return [
        ModeloRead(id=m.id, tipo_dia=m.tipo_dia, paridade=m.paridade, nome=m.nome, qtd_postos=contagem.get(m.id, 0))
        for m in modelos
    ]


@router.post("/modelos", response_model=ModeloRead, status_code=status.HTTP_201_CREATED, summary="Cria modelo vazio")
def criar_modelo(payload: ModeloCreate, _: EscritaEscala, db: DbSession):
    _validar_paridade(payload.tipo_dia, payload.paridade)
    nome = " ".join(payload.nome.split())
    if db.execute(select(EscalaFiscalModelo).where(EscalaFiscalModelo.nome == nome)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe o modelo \"{nome}\"")
    m = EscalaFiscalModelo(tipo_dia=payload.tipo_dia, paridade=payload.paridade, nome=nome)
    db.add(m)
    db.commit()
    return ModeloRead(id=m.id, tipo_dia=m.tipo_dia, paridade=m.paridade, nome=m.nome)


# ─── AUSÊNCIAS ───────────────────────────────────────────────────────────────

def _ausencia_read(a: EscalaFiscalAusencia, nomes: dict[str, str]) -> AusenciaRead:
    return AusenciaRead(
        **_fiscal(a.re, nomes), id=a.id, tipo=a.tipo, data_inicio=a.data_inicio, data_fim=a.data_fim,
        observacao=a.observacao,
    )


@router.get("/ausencias", response_model=list[AusenciaRead], summary="Ausências: de um dia (data fim inclusiva), vigentes de hoje em diante, ou todas")
def listar_ausencias(
    _: LeituraEscala,
    db: DbSession,
    data: Optional[date] = Query(None, description="Quem está fora NESTE dia (data fim inclusiva)"),
    todas: bool = Query(False, description="Inclui as que já terminaram"),
):
    consulta = select(EscalaFiscalAusencia)
    if data is not None:
        consulta = consulta.where(
            EscalaFiscalAusencia.data_inicio <= data,
            or_(EscalaFiscalAusencia.data_fim.is_(None), EscalaFiscalAusencia.data_fim >= data),
        )
    elif not todas:
        hoje = hoje_operacao()
        consulta = consulta.where(
            or_(EscalaFiscalAusencia.data_fim.is_(None), EscalaFiscalAusencia.data_fim >= hoje)
        )
    linhas = db.execute(consulta.order_by(EscalaFiscalAusencia.data_inicio)).scalars().all()
    nomes = nomes_por_re(db, (a.re for a in linhas))
    return [_ausencia_read(a, nomes) for a in linhas]


@router.post("/ausencias", response_model=AusenciaRead, status_code=status.HTTP_201_CREATED, summary="Lança férias, atestado ou afastamento (RE sem cadastro é aceito)")
def criar_ausencia(payload: AusenciaCreate, usuario: EscritaEscala, db: DbSession):
    _validar_periodo_ausencia(payload.data_inicio, payload.data_fim)
    a = EscalaFiscalAusencia(
        re=payload.re, tipo=payload.tipo, data_inicio=payload.data_inicio,
        data_fim=payload.data_fim, observacao=(payload.observacao or "").strip() or None,
        criado_por=usuario.id,
    )
    db.add(a)
    db.commit()
    return _ausencia_read(a, nomes_por_re(db, [a.re]))


# ─── TROCAS ──────────────────────────────────────────────────────────────────

def _troca_read(t: EscalaFiscalTroca, nomes: dict[str, str]) -> TrocaRead:
    return TrocaRead(
        id=t.id, tipo=t.tipo, data_sabado=t.data_sabado, observacao=t.observacao,
        a=FiscalResumo(**_fiscal(t.re_a, nomes)), b=FiscalResumo(**_fiscal(t.re_b, nomes)),
    )


@router.get("/trocas", response_model=list[TrocaRead], summary="Trocas de folga 2x2 e 1x1")
def listar_trocas(_: LeituraEscala, db: DbSession, todas: bool = False):
    consulta = select(EscalaFiscalTroca)
    if not todas:
        # O fim de semana termina no domingo: troca de sábado de ontem ainda vale hoje.
        consulta = consulta.where(EscalaFiscalTroca.data_sabado >= hoje_operacao() - timedelta(days=1))
    linhas = db.execute(consulta.order_by(EscalaFiscalTroca.data_sabado)).scalars().all()
    nomes = nomes_por_re(db, [t.re_a for t in linhas] + [t.re_b for t in linhas])
    return [_troca_read(t, nomes) for t in linhas]


@router.post("/trocas", response_model=TrocaRead, status_code=status.HTTP_201_CREATED, summary="Lança troca de folga (RE sem cadastro é aceito)")
def criar_troca(payload: TrocaCreate, usuario: EscritaEscala, db: DbSession):
    if payload.re_a == payload.re_b:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A troca precisa de dois REs diferentes")
    if payload.data_sabado.weekday() != 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A data da troca tem que ser o SÁBADO do fim de semana trocado")
    t = EscalaFiscalTroca(
        tipo=payload.tipo, re_a=payload.re_a, re_b=payload.re_b, data_sabado=payload.data_sabado,
        observacao=(payload.observacao or "").strip() or None, criado_por=usuario.id,
    )
    db.add(t)
    db.commit()
    return _troca_read(t, nomes_por_re(db, [t.re_a, t.re_b]))


# ═════════════════════════════════════════════════════════════════════════════
# Rotas COM parâmetro — daqui para baixo
# ═════════════════════════════════════════════════════════════════════════════

@router.patch("/quadro/{re_fiscal}", response_model=QuadroRead, summary="Altera período, folga base ou ativo")
def alterar_quadro(re_fiscal: str, payload: QuadroUpdate, _: EscritaEscala, db: DbSession):
    q = db.get(EscalaFiscalQuadro, normalizar_re(re_fiscal))
    if q is None:
        raise _nao_encontrado("Fiscal no quadro")
    dados = payload.model_dump(exclude_unset=True)
    if "periodo" in dados and dados["periodo"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Período é obrigatório")
    for campo, valor in dados.items():
        setattr(q, campo, valor)
    db.commit()
    return _quadro_read(q, nomes_por_re(db, [q.re]))


@router.delete("/quadro/{re_fiscal}", status_code=status.HTTP_204_NO_CONTENT, summary="Tira o fiscal do quadro (prefira desativar)")
def remover_quadro(re_fiscal: str, _: EscritaEscala, db: DbSession) -> None:
    q = db.get(EscalaFiscalQuadro, normalizar_re(re_fiscal))
    if q is None:
        raise _nao_encontrado("Fiscal no quadro")
    db.delete(q)
    db.commit()


@router.delete("/coordenadores/periodos/{funcionario_id}/{periodo}", status_code=status.HTTP_204_NO_CONTENT, summary="Desliga coordenador do período")
def remover_coordenador_periodo(funcionario_id: UUID, periodo: int, _: EscritaEscala, db: DbSession) -> None:
    c = db.get(EscalaFiscalCoordenadorPeriodo, (funcionario_id, periodo))
    if c is None:
        raise _nao_encontrado("Coordenador no período")
    db.delete(c)
    db.commit()


@router.patch("/coordenadores/horarios/{horario_id}", response_model=CoordenadorHorarioRead, summary="Altera horário padrão do coordenador")
def alterar_coordenador_horario(horario_id: UUID, payload: CoordenadorHorarioUpdate, _: EscritaEscala, db: DbSession):
    h = db.get(EscalaFiscalCoordenadorHorario, horario_id)
    if h is None:
        raise _nao_encontrado("Horário de coordenador")
    dados = payload.model_dump(exclude_unset=True)
    inicio = dados.get("hora_inicio") or h.hora_inicio
    fim = dados.get("hora_fim") or h.hora_fim
    _validar_horario_coordenador(inicio, fim)
    for campo, valor in dados.items():
        if valor is not None:
            setattr(h, campo, valor)
    db.commit()
    return _horario_read(h, db.get(Funcionario, h.funcionario_id))


@router.delete("/coordenadores/horarios/{horario_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apaga horário padrão do coordenador")
def remover_coordenador_horario(horario_id: UUID, _: EscritaEscala, db: DbSession) -> None:
    h = db.get(EscalaFiscalCoordenadorHorario, horario_id)
    if h is None:
        raise _nao_encontrado("Horário de coordenador")
    db.delete(h)
    db.commit()


@router.patch("/pontos-finais/{ponto_id}", response_model=PontoFinalRead, summary="Renomeia ou desativa ponto final")
def alterar_ponto_final(ponto_id: UUID, payload: PontoFinalUpdate, _: EscritaEscala, db: DbSession):
    p = db.get(EscalaFiscalPontoFinal, ponto_id)
    if p is None:
        raise _nao_encontrado("Ponto final")
    if payload.nome is not None:
        nome = " ".join(payload.nome.split())
        outro = db.execute(
            select(EscalaFiscalPontoFinal).where(
                func.lower(EscalaFiscalPontoFinal.nome) == nome.lower(), EscalaFiscalPontoFinal.id != ponto_id
            )
        ).first()
        if outro:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe o ponto final \"{nome}\"")
        p.nome = nome
    if payload.ativo is not None:
        p.ativo = payload.ativo
    db.commit()
    return PontoFinalRead(id=p.id, nome=p.nome, ativo=p.ativo)


@router.patch("/postos/{posto_id}", response_model=PostoRead, summary="Altera posto; linhas enviadas substituem a lista inteira")
def alterar_posto(posto_id: UUID, payload: PostoUpdate, _: EscritaEscala, db: DbSession):
    p = db.get(EscalaFiscalPosto, posto_id)
    if p is None:
        raise _nao_encontrado("Posto")
    dados = payload.model_dump(exclude_unset=True)
    if "ponto_final_id" in dados:
        _checar_ponto_final(db, dados["ponto_final_id"])
    if dados.get("lado") is None:
        dados.pop("lado", None)
    novas_linhas = dados.pop("linhas", None)
    lado = dados.get("lado", p.lado)
    if novas_linhas is not None or "lado" in dados:
        _checar_posto_duplicado(db, lado, novas_linhas or [pl.linha for pl in p.linhas], ignorar=p.id)
    for campo, valor in dados.items():
        setattr(p, campo, (valor or None) if campo in ("cod_jb", "lote") else valor)
    if novas_linhas is not None:
        # Mesmo cuidado de ocorrencias.atualizar(): flush entre o clear e o
        # append, senão o INSERT da linha repetida vem antes do DELETE e
        # colide na PK (posto_id, linha).
        p.linhas.clear()
        db.flush()
        p.linhas.extend(EscalaFiscalPostoLinha(linha=ln, ordem=i + 1) for i, ln in enumerate(novas_linhas))
    db.commit()
    db.refresh(p)
    return _posto_read(p, _nomes_pontos(db))


def _modelo_posto_read(mp: EscalaFiscalModeloPosto, posto: EscalaFiscalPosto, nomes: dict[str, str]) -> ModeloPostoRead:
    return ModeloPostoRead(
        id=mp.id, modelo_id=mp.modelo_id, posto_id=mp.posto_id, lado=posto.lado, cod_jb=posto.cod_jb,
        lote=posto.lote, linhas=[pl.linha for pl in posto.linhas], ordem=mp.ordem, periodo=mp.periodo,
        hora_inicio=mp.hora_inicio, hora_termino=mp.hora_termino, situacao_padrao=mp.situacao_padrao,
        re_padrao=mp.re_padrao, nome_padrao=nomes.get(mp.re_padrao) if mp.re_padrao else None,
        outra_garagem=mp.outra_garagem, marcador=mp.marcador,
    )


@router.get("/modelos/{modelo_id}", response_model=ModeloDetalhe, summary="Modelo com os postos, na ordem da escala")
def detalhar_modelo(modelo_id: UUID, _: LeituraEscala, db: DbSession):
    m = db.get(EscalaFiscalModelo, modelo_id)
    if m is None:
        raise _nao_encontrado("Modelo")
    itens = db.execute(
        select(EscalaFiscalModeloPosto)
        .where(EscalaFiscalModeloPosto.modelo_id == modelo_id)
        .order_by(EscalaFiscalModeloPosto.ordem, EscalaFiscalModeloPosto.periodo)
    ).scalars().all()
    postos = {p.id: p for p in db.execute(
        select(EscalaFiscalPosto).where(EscalaFiscalPosto.id.in_({i.posto_id for i in itens}))
    ).scalars()} if itens else {}
    nomes = nomes_por_re(db, (i.re_padrao for i in itens))
    return ModeloDetalhe(
        id=m.id, tipo_dia=m.tipo_dia, paridade=m.paridade, nome=m.nome, qtd_postos=len(itens),
        postos=[_modelo_posto_read(i, postos[i.posto_id], nomes) for i in itens],
    )


def _aplicar_modelo_posto(mp: EscalaFiscalModeloPosto, dados: dict) -> None:
    """Aplica e valida a coerência situação × RE × garagem × marcador × horário.
    D-A: RE é texto, sem cadastro obrigatório. D-B: sem RE, o marcador guarda
    o texto que a impressão vai mostrar; quando a tela não manda, ele é
    derivado da situação (garagem, DIRETO, ou vazio = em branco)."""
    if "outra_garagem" in dados:
        dados["outra_garagem"] = (dados["outra_garagem"] or "").strip().upper() or None
    for campo, valor in dados.items():
        setattr(mp, campo, valor)

    _validar_horario_fiscal(mp.hora_inicio, mp.hora_termino)
    if mp.situacao_padrao == "escalado":
        if not mp.re_padrao:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Situação \"escalado\" precisa do RE do fiscal")
        mp.marcador = None
    elif mp.re_padrao is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="RE do fiscal só vale para a situação \"escalado\"")

    if mp.situacao_padrao == "outra_garagem":
        if not mp.outra_garagem or not _RE_OUTRA_GARAGEM.match(mp.outra_garagem):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Informe a garagem que cobre (G + número, ex.: G1)")
        if mp.outra_garagem == importacao.GARAGEM_PROPRIA:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="G3 é a própria garagem — não pode ser \"outra garagem\"")
    elif mp.outra_garagem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Garagem só vale para a situação \"outra garagem\"")

    if mp.situacao_padrao != "escalado" and ("marcador" not in dados or mp.marcador is None):
        mp.marcador = {"outra_garagem": mp.outra_garagem, "direto": "DIRETO"}.get(mp.situacao_padrao, mp.marcador or "")


@router.post("/modelos/{modelo_id}/postos", response_model=ModeloPostoRead, status_code=status.HTTP_201_CREATED, summary="Põe um posto no modelo")
def adicionar_modelo_posto(modelo_id: UUID, payload: ModeloPostoCreate, _: EscritaEscala, db: DbSession):
    if db.get(EscalaFiscalModelo, modelo_id) is None:
        raise _nao_encontrado("Modelo")
    posto = db.get(EscalaFiscalPosto, payload.posto_id)
    if posto is None:
        raise _nao_encontrado("Posto")
    dados = payload.model_dump(exclude_unset=False)
    if dados.get("marcador") is None:
        dados.pop("marcador")
    mp = EscalaFiscalModeloPosto(modelo_id=modelo_id, posto_id=dados.pop("posto_id"))
    _aplicar_modelo_posto(mp, dados)
    db.add(mp)
    db.commit()
    return _modelo_posto_read(mp, posto, nomes_por_re(db, [mp.re_padrao]))


@router.patch("/modelos/{modelo_id}/postos/{item_id}", response_model=ModeloPostoRead, summary="Altera horário, situação, RE ou marcador de um posto do modelo")
def alterar_modelo_posto(modelo_id: UUID, item_id: UUID, payload: ModeloPostoUpdate, _: EscritaEscala, db: DbSession):
    mp = db.get(EscalaFiscalModeloPosto, item_id)
    if mp is None or mp.modelo_id != modelo_id:
        raise _nao_encontrado("Posto do modelo")
    dados = payload.model_dump(exclude_unset=True)
    for obrigatorio in ("ordem", "periodo", "situacao_padrao"):
        if obrigatorio in dados and dados[obrigatorio] is None:
            dados.pop(obrigatorio)
    if "marcador" in dados and dados["marcador"] is None:
        dados.pop("marcador")
    _aplicar_modelo_posto(mp, dados)
    db.commit()
    return _modelo_posto_read(mp, db.get(EscalaFiscalPosto, mp.posto_id), nomes_por_re(db, [mp.re_padrao]))


@router.delete("/modelos/{modelo_id}/postos/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Tira um posto do modelo")
def remover_modelo_posto(modelo_id: UUID, item_id: UUID, _: EscritaEscala, db: DbSession) -> None:
    mp = db.get(EscalaFiscalModeloPosto, item_id)
    if mp is None or mp.modelo_id != modelo_id:
        raise _nao_encontrado("Posto do modelo")
    db.delete(mp)
    db.commit()


@router.patch("/ausencias/{ausencia_id}", response_model=AusenciaRead, summary="Corrige ausência")
def alterar_ausencia(ausencia_id: UUID, payload: AusenciaUpdate, _: EscritaEscala, db: DbSession):
    a = db.get(EscalaFiscalAusencia, ausencia_id)
    if a is None:
        raise _nao_encontrado("Ausência")
    dados = payload.model_dump(exclude_unset=True)
    for obrigatorio in ("tipo", "data_inicio"):
        if obrigatorio in dados and dados[obrigatorio] is None:
            dados.pop(obrigatorio)
    _validar_periodo_ausencia(dados.get("data_inicio", a.data_inicio), dados.get("data_fim", a.data_fim))
    for campo, valor in dados.items():
        setattr(a, campo, valor)
    db.commit()
    return _ausencia_read(a, nomes_por_re(db, [a.re]))


@router.delete("/ausencias/{ausencia_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apaga ausência lançada errado")
def remover_ausencia(ausencia_id: UUID, _: EscritaEscala, db: DbSession) -> None:
    a = db.get(EscalaFiscalAusencia, ausencia_id)
    if a is None:
        raise _nao_encontrado("Ausência")
    db.delete(a)
    db.commit()


@router.delete("/trocas/{troca_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apaga troca lançada errado")
def remover_troca(troca_id: UUID, _: EscritaEscala, db: DbSession) -> None:
    t = db.get(EscalaFiscalTroca, troca_id)
    if t is None:
        raise _nao_encontrado("Troca")
    db.delete(t)
    db.commit()
