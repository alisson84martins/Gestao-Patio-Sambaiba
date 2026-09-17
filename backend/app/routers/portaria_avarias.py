"""Avaria na saída da frota (Bloco G) — o controlador confere o carro
saindo do pátio e anota um dano visível (para-choque quebrado, retrovisor
rachado, risco na lateral). Não é recolhida (o carro está saindo, não
voltando) e não é ocorrência (não houve sinistro).

🔴 ENQUADRAMENTO PROBATÓRIO (migration 042, Fase 1 de
PROMPT-avaria-mapa-visual-2026-09-15.md): a marcação PROTEGE o motorista
que pegou o carro já avariado — o conserto não é cobrado dele porque ficou
registrado que ele viu o dano. A AUSÊNCIA de marcação o RESPONSABILIZA.
Isto não é log operacional, é documento usado em apuração de
responsabilidade sobre pessoa. Por isso:
  - Avaria (`AvariaSaida`) e Constatação (`AvariaConstatacao`) são coisas
    diferentes — dano ≠ declaração. Ver docstrings em app/models/portaria.py.
  - Constatação é IMUTÁVEL: este router nunca expõe PUT/DELETE nela.
  - Retenção de 365 dias (services/avarias.py), não 60.

🔵 COMPATIBILIDADE COM A TELA ANTIGA (036) — REGRA NÚMERO UM DO DEPLOY:
POST /avarias aceita `zona_codigo`/`tipo_codigo` ausentes (a tela antiga só
manda `descricao`) e preenche NAO_INFORMADA/NAO_INFORMADO. A migration 042
torna essas colunas NOT NULL no banco — sem esta compatibilidade, o
primeiro REGISTRAR da tela antiga depois do deploy da 042 estoura
`null value in column "zona_codigo"` e derruba a guarita. A 042 e este
backend sobem NO MESMO deploy — ver PROGRESSO-2026-09-16.md.

Router próprio, fora de routers/portaria.py (⛔ já tem 18 KB) — mesmo
motivo que levou routers/portaria_recolhidas.py a existir separado.

RBAC: marcar/contestar/consultar reaproveitam `acesso_veicular` (migration
024) — nenhum recurso novo (menor privilégio, padrão da migration 020).
`/encerrar` exige `manutencao` escrever: quem conserta não é quem confere
a saída (Fase 2) — ver cabeçalho da migration 042.

🔴 REGRA NÚMERO UM do módulo vale aqui também: POST nunca recusa por
prefixo desconhecido, RE que não resolve, zona/tipo ausentes ou linha fora
do catálogo — registra assim mesmo.
"""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige, exige_qualquer
from app.core.registro import normalizar_re
from app.models.cadastro import Funcionario
from app.models.catalogos import Linha
from app.models.operacoes import Escala
from app.models.portaria import AvariaConstatacao, AvariaContestacao, AvariaSaida, AvariaTipo, AvariaZona
from app.routers.alocacoes import get_data_servico
from app.schemas.portaria import (
    AvariaCatalogoResponse, AvariaConstatacaoAnularRequest, AvariaConstatacaoRead, AvariaContestacaoRead,
    AvariaContestarRequest, AvariaDetalheRead, AvariaEncerrarRequest, AvariaHistoricoItem, AvariaMapaItem,
    AvariaMapaResponse, AvariaSaidaCreate, AvariaSaidaRead, AvariaSaidaUpsertResponse, AvariaZonaRead,
    AvariaTipoRead, CatalogoLinhaPortariaItem, CicloAnteriorInfo,
)
from app.services.avarias import agora_utc, calcular_expira_em
from app.services.identidade import resolver_por_re
from app.services.portaria import resolver_onibus_por_prefixo
from app.services.pre_cadastro import registrar_pessoa_vista

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular"))]
EscritaAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular", escrever=True))]
# Fase 2 — quem dá baixa é a manutenção, não o controlador (ver cabeçalho).
EscritaManutencao = Annotated[Funcionario, Depends(exige("manutencao", escrever=True))]
# 17/09 — fila de avarias abertas (módulo Manutenção, item [4]): o MECANICO
# não tem `acesso_veicular` (migration 037 tirou até `recolhida_anormal`
# dele de propósito, separando REGISTRAR de TRATAR) e não vai ganhar agora
# — RBAC segue "nenhum recurso novo" (mesma regra da 042). Mesmo padrão de
# exige_qualquer já usado por GET /portaria/recolhidas (LeituraRecolhidaOuTratativa,
# portaria_recolhidas.py) para o problema irmão: endpoint de LEITURA
# compartilhado por quem registra (acesso_veicular) e quem trata
# (manutencao), sem alargar nada de ESCRITA. Só nos dois GETs que a fila
# da funilaria precisa (catálogo e histórico) — /mapa, /{id} e as escritas
# continuam só acesso_veicular, como sempre.
LeituraAcessoOuManutencao = Annotated[Funcionario, Depends(exige_qualquer("acesso_veicular", "manutencao"))]

DbSession = Annotated[Session, Depends(get_db)]

_ESCALA_SEVERIDADE = ["LEVE", "MEDIA", "GRAVE"]


def _sobe_severidade(atual: str) -> str:
    """LEVE→MEDIA→GRAVE, ⛔ nunca desce sozinha (P2, item 4)."""
    try:
        indice = _ESCALA_SEVERIDADE.index(atual)
    except ValueError:
        indice = 0
    return _ESCALA_SEVERIDADE[min(indice + 1, len(_ESCALA_SEVERIDADE) - 1)]


def _resolver_zona_tipo(
    db: Session, zona_codigo: Optional[str], tipo_codigo: Optional[str]
) -> tuple[AvariaZona, AvariaTipo]:
    """Compat (item 🔵 do cabeçalho): os dois ausentes -> NAO_INFORMADA/
    NAO_INFORMADO (tela antiga). Um só -> 422 (não dá pra montar a chave de
    dedup pela metade). Código que não existe no catálogo -> 422: zona/tipo
    são catálogo NOSSO, vindo de dropdown controlado pelo frontend (P3),
    não digitação do controlador — diferente de linha_codigo (teste 17),
    que aceita qualquer coisa porque é catálogo de OUTRO módulo."""
    if not zona_codigo and not tipo_codigo:
        zona = db.get(AvariaZona, "NAO_INFORMADA")
        tipo = db.get(AvariaTipo, "NAO_INFORMADO")
        if zona is None or tipo is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Catálogo de avaria sem NAO_INFORMADA/NAO_INFORMADO — seed da migration 042 ausente.",
            )
        return zona, tipo

    if not zona_codigo or not tipo_codigo:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="zona_codigo e tipo_codigo devem vir juntos (ou os dois ausentes, para compatibilidade com a tela antiga).",
        )
    zona = db.get(AvariaZona, zona_codigo)
    if zona is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"zona_codigo '{zona_codigo}' não existe no catálogo.")
    tipo = db.get(AvariaTipo, tipo_codigo)
    if tipo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"tipo_codigo '{tipo_codigo}' não existe no catálogo.")
    return zona, tipo


def _resolver_re_nome_em_lote(db: Session, funcionario_ids) -> dict:
    """Join em lote (⛔ nunca N chamadas, uma por linha — mesma armadilha do
    carregarUltimas() que esta frente corrige). Devolve {id: (re, nome)}."""
    ids = {i for i in funcionario_ids if i is not None}
    if not ids:
        return {}
    linhas = db.execute(
        select(Funcionario.id, Funcionario.re, Funcionario.nome).where(Funcionario.id.in_(ids))
    ).all()
    return {linha.id: (linha.re, linha.nome) for linha in linhas}


def _linha_do_dia(db: Session, prefixo: str) -> tuple[Optional[str], Optional[str]]:
    """🟢 Autopreenchimento pela escala do dia (pedido do Alisson, 16/09) —
    simplificado em relação a
    portaria_recolhidas.py::_sugerir_motorista_pela_escala (que filtra pelo
    INSTANTE da recolhida): aqui só interessa "qual linha esse carro está
    escalado hoje", então pega a escala mais recente do dia de serviço
    (ou véspera, pra cobrir virada de madrugada) sem filtro de horário.
    Editável na tela, nunca sobrescreve o que o controlador já escolheu —
    isso é responsabilidade do frontend (P3), não deste helper."""
    onibus = resolver_onibus_por_prefixo(db, prefixo)
    if onibus is None:
        return None, None
    data_referencia = get_data_servico()
    escalas = db.execute(
        select(Escala)
        .where(
            Escala.onibus_id == onibus.id,
            Escala.data.in_([data_referencia, data_referencia - timedelta(days=1)]),
            Escala.deletado_em.is_(None),
        )
        .order_by(Escala.data.desc(), Escala.horario_saida.desc())
    ).scalars().all()
    if not escalas:
        return None, None
    melhor = escalas[0]
    linha = db.get(Linha, melhor.linha_id)
    if linha is None:
        return None, None
    return linha.codigo, linha.nome


def _duas_contestacoes_fecham(contestacoes: list[AvariaContestacao]) -> bool:
    """🟢 Regra das duas (P1, seção 4): existe PAR de contestações de
    PESSOAS diferentes em DIAS de serviço diferentes. Comparação par a par
    — checagem por conjuntos (pessoas distintas >=2 E dias distintos >=2)
    deixaria passar batido um caso onde as pessoas se repetem em dias
    cruzados sem nenhum par realmente válido; N é sempre pequeno (poucas
    contestações por avaria)."""
    for i, a in enumerate(contestacoes):
        for b in contestacoes[i + 1:]:
            if a.registrado_por != b.registrado_por and a.data_servico != b.data_servico:
                return True
    return False


def _resolver_motorista_nome(db: Session, motorista_re: Optional[str], motorista_nome: Optional[str]) -> Optional[str]:
    """RE resolvido -> nome vem do cadastro (snapshot mais confiável que o
    digitado); RE não resolvido -> fica o que o controlador informou."""
    if motorista_re:
        pessoa = resolver_por_re(db, motorista_re)
        if pessoa is not None:
            return pessoa.nome
    return motorista_nome


def _alimentar_pre_cadastro(db: Session, motorista_re: Optional[str], motorista_nome: Optional[str]) -> None:
    # Bloco F/H, mesma costura de portaria_recolhidas.py: RE que não
    # resolveu alimenta o pré-cadastro — de graça, nunca bloqueia.
    registrar_pessoa_vista(
        db, re=motorista_re, papel="MOTORISTA", origem="PORTARIA_AVARIA", nome=motorista_nome,
    )


def _nova_constatacao(
    *, avaria_id: UUID, payload: AvariaSaidaCreate, usuario: Funcionario, motorista_nome: Optional[str],
    data_servico: date, agora: datetime, observacao: Optional[str],
) -> AvariaConstatacao:
    return AvariaConstatacao(
        avaria_id=avaria_id, data_servico=data_servico, ocorrido_em=agora,
        motorista_re=payload.motorista_re, motorista_nome=motorista_nome,
        observacao=observacao, piorou=payload.piorou,
        linha_codigo=payload.linha_codigo, registrado_por=usuario.id,
    )


def _acrescentar_constatacao(
    db: Session, avaria: AvariaSaida, *, payload: AvariaSaidaCreate, usuario: Funcionario,
    motorista_nome: Optional[str], data_servico: date, agora: datetime,
) -> None:
    """P2, itens 3/5: NÃO cria linha nova — acrescenta constatação na
    avaria já ABERTA (ou recém-reaberta)."""
    if payload.piorou:
        avaria.severidade = _sobe_severidade(avaria.severidade)
    avaria.vezes_vista += 1
    avaria.ultima_vez_em = agora
    avaria.expira_em = calcular_expira_em(agora, avaria.encerrada_em)
    db.add(_nova_constatacao(
        avaria_id=avaria.id, payload=payload, usuario=usuario, motorista_nome=motorista_nome,
        data_servico=data_servico, agora=agora, observacao=payload.observacao,
    ))


# ============================================================================
# POST /avarias — upsert (P2). Rotas literais ("/avarias/catalogo",
# "/avarias/mapa", "/avarias/historico") são registradas ANTES de
# "/avarias/{avaria_id}" — Starlette casa por ordem de declaração.
# ============================================================================

@router.post(
    "/avarias",
    response_model=AvariaSaidaUpsertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registra ou confirma avaria vista na saída — nunca recusa; upsert por prefixo+zona+tipo (042)",
)
def registrar_avaria(
    payload: AvariaSaidaCreate, usuario: EscritaAcesso, db: DbSession, response: Response,
):
    motorista_nome = _resolver_motorista_nome(db, payload.motorista_re, payload.motorista_nome)
    data_servico = get_data_servico()
    agora = agora_utc()

    # ── escape "avaria REPARADA, dano voltou" (P2, item 5) ──────────────
    if payload.reabrir_id is not None:
        avaria = db.get(AvariaSaida, payload.reabrir_id)
        if avaria is None or avaria.prefixo != payload.prefixo:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="Avaria indicada em reabrir_id não encontrada para este prefixo.",
            )
        avaria.status = "ABERTA"
        avaria.encerrada_em = None
        avaria.encerrada_por = None
        avaria.encerramento = None
        avaria.encerramento_nota = None
        _acrescentar_constatacao(
            db, avaria, payload=payload, usuario=usuario, motorista_nome=motorista_nome,
            data_servico=data_servico, agora=agora,
        )
        _alimentar_pre_cadastro(db, payload.motorista_re, payload.motorista_nome)
        db.commit()
        db.refresh(avaria)
        response.status_code = status.HTTP_200_OK
        resultado = AvariaSaidaUpsertResponse.model_validate(avaria)
        resultado.ja_existia = True
        return resultado

    zona, tipo = _resolver_zona_tipo(db, payload.zona_codigo, payload.tipo_codigo)

    # NAO_INFORMADA nunca deduplica de propósito (o índice único da 042 a
    # exclui) — cada POST da tela antiga é sempre avaria nova; é transitório
    # e aceito até o mapa (P3) substituí-la.
    existente = None
    if zona.codigo != "NAO_INFORMADA":
        existente = db.execute(
            select(AvariaSaida).where(
                AvariaSaida.prefixo == payload.prefixo,
                AvariaSaida.zona_codigo == zona.codigo,
                AvariaSaida.tipo_codigo == tipo.codigo,
                AvariaSaida.status == "ABERTA",
            )
        ).scalar_one_or_none()

    if existente is not None:
        _acrescentar_constatacao(
            db, existente, payload=payload, usuario=usuario, motorista_nome=motorista_nome,
            data_servico=data_servico, agora=agora,
        )
        _alimentar_pre_cadastro(db, payload.motorista_re, payload.motorista_nome)
        db.commit()
        db.refresh(existente)
        response.status_code = status.HTTP_200_OK
        resultado = AvariaSaidaUpsertResponse.model_validate(existente)
        resultado.ja_existia = True
        resultado.primeira_vez_em = existente.primeira_vez_em
        return resultado

    # ── nasce avaria nova ────────────────────────────────────────────────
    descricao = (payload.descricao or "").strip() or f"{zona.nome} — {tipo.nome}"
    observacao_abertura = payload.observacao
    if not observacao_abertura and payload.descricao:
        # Mesma costura do backfill da 042 (descricao -> observacao da
        # constatação) — descricao É a observação da abertura, só duplicada
        # historicamente em duas colunas (avaria.descricao é dado da 036).
        observacao_abertura = payload.descricao.strip() or None

    severidade_inicial = payload.severidade or "LEVE"
    if payload.piorou:
        severidade_inicial = _sobe_severidade(severidade_inicial)

    nova = AvariaSaida(
        prefixo=payload.prefixo, data_servico=data_servico, ocorrido_em=agora,
        motorista_re=payload.motorista_re, motorista_nome=motorista_nome, descricao=descricao,
        zona_codigo=zona.codigo, tipo_codigo=tipo.codigo,
        severidade=severidade_inicial, status="ABERTA",
        primeira_vez_em=agora, ultima_vez_em=agora, vezes_vista=1,
        registrado_por=usuario.id, expira_em=calcular_expira_em(agora),
    )
    db.add(nova)
    try:
        db.flush()
    except IntegrityError:
        # 🔴 armadilha_constraint_imediata_flush: alguém gravou junto, no
        # meio da mesma corrida. ⛔ Nunca vira erro na tela — recarrega e
        # acrescenta constatação na que apareceu (P1, seção 5).
        db.rollback()
        concorrente = db.execute(
            select(AvariaSaida).where(
                AvariaSaida.prefixo == payload.prefixo,
                AvariaSaida.zona_codigo == zona.codigo,
                AvariaSaida.tipo_codigo == tipo.codigo,
                AvariaSaida.status == "ABERTA",
            )
        ).scalar_one_or_none()
        if concorrente is None:
            raise
        _acrescentar_constatacao(
            db, concorrente, payload=payload, usuario=usuario, motorista_nome=motorista_nome,
            data_servico=data_servico, agora=agora,
        )
        _alimentar_pre_cadastro(db, payload.motorista_re, payload.motorista_nome)
        db.commit()
        db.refresh(concorrente)
        response.status_code = status.HTTP_200_OK
        resultado = AvariaSaidaUpsertResponse.model_validate(concorrente)
        resultado.ja_existia = True
        return resultado

    db.add(_nova_constatacao(
        avaria_id=nova.id, payload=payload, usuario=usuario, motorista_nome=motorista_nome,
        data_servico=data_servico, agora=agora, observacao=observacao_abertura,
    ))

    # Avisa a tela quando este mesmo dano já teve um ciclo anterior
    # encerrado (item 2 do P2) — só faz sentido fora do compat NAO_INFORMADA.
    ciclo_anterior = None
    if zona.codigo != "NAO_INFORMADA":
        anterior = db.execute(
            select(AvariaSaida)
            .where(
                AvariaSaida.prefixo == payload.prefixo,
                AvariaSaida.zona_codigo == zona.codigo,
                AvariaSaida.tipo_codigo == tipo.codigo,
                AvariaSaida.status != "ABERTA",
                AvariaSaida.id != nova.id,
            )
            .order_by(AvariaSaida.encerrada_em.desc())
        ).scalars().first()
        if anterior is not None:
            ciclo_anterior = CicloAnteriorInfo(
                avaria_id=anterior.id, encerrada_em=anterior.encerrada_em,
                encerramento=anterior.encerramento, vezes_vista=anterior.vezes_vista,
            )

    _alimentar_pre_cadastro(db, payload.motorista_re, payload.motorista_nome)
    db.commit()
    db.refresh(nova)
    resultado = AvariaSaidaUpsertResponse.model_validate(nova)
    resultado.ciclo_anterior = ciclo_anterior
    return resultado


# ============================================================================
# GET /avarias/catalogo, /mapa, /historico — literais, ANTES de /{avaria_id}
# ============================================================================

@router.get(
    "/avarias/catalogo",
    response_model=AvariaCatalogoResponse,
    summary="Catálogo de zonas e tipos, só ativos — ⛔ nunca hardcodar no frontend",
)
def catalogo_avarias(usuario: LeituraAcessoOuManutencao, db: DbSession):
    zonas = db.execute(
        select(AvariaZona).where(AvariaZona.ativo.is_(True)).order_by(AvariaZona.ordem)
    ).scalars().all()
    tipos = db.execute(
        select(AvariaTipo).where(AvariaTipo.ativo.is_(True)).order_by(AvariaTipo.ordem)
    ).scalars().all()
    return AvariaCatalogoResponse(
        zonas=[AvariaZonaRead.model_validate(z) for z in zonas],
        tipos=[AvariaTipoRead.model_validate(t) for t in tipos],
    )


@router.get(
    "/avarias/mapa",
    response_model=AvariaMapaResponse,
    summary="Uma linha por avaria ABERTA do carro — pinta o mapa clicável (P3)",
)
def mapa_avarias(usuario: LeituraAcesso, db: DbSession, prefixo: str = Query(..., min_length=1, max_length=10)):
    prefixo = prefixo.strip()
    contagem_sub = (
        select(AvariaContestacao.avaria_id, func.count().label("total"))
        .group_by(AvariaContestacao.avaria_id)
        .subquery()
    )
    linhas = db.execute(
        select(AvariaSaida, contagem_sub.c.total)
        .outerjoin(contagem_sub, contagem_sub.c.avaria_id == AvariaSaida.id)
        .where(AvariaSaida.prefixo == prefixo, AvariaSaida.status == "ABERTA")
        .order_by(AvariaSaida.ultima_vez_em.desc())
    ).all()
    itens = [
        AvariaMapaItem(
            id=avaria.id, zona_codigo=avaria.zona_codigo, tipo_codigo=avaria.tipo_codigo,
            severidade=avaria.severidade, primeira_vez_em=avaria.primeira_vez_em,
            ultima_vez_em=avaria.ultima_vez_em, vezes_vista=avaria.vezes_vista,
            contestacoes=total or 0,
        )
        for avaria, total in linhas
    ]
    linha_codigo, linha_nome = _linha_do_dia(db, prefixo)
    return AvariaMapaResponse(avarias=itens, linha_sugerida_codigo=linha_codigo, linha_sugerida_nome=linha_nome)


@router.get(
    "/avarias/historico",
    response_model=list[AvariaHistoricoItem],
    summary="Histórico COMPARTILHADO entre controladores, com quem anotou (pedido do Alisson, 16/09)",
)
def historico_avarias(
    usuario: LeituraAcessoOuManutencao, db: DbSession,
    prefixo: Optional[str] = Query(None, max_length=10),
    linha_codigo: Optional[str] = Query(None, max_length=20),
    motorista_re: Optional[str] = Query(None, max_length=20),
    registrado_por: Optional[str] = Query(None, max_length=20, description="RE de quem anotou"),
    desde: Optional[date] = None,
    ate: Optional[date] = None,
    status_filtro: Optional[Literal["ABERTA", "ENCERRADA"]] = Query(None, alias="status"),
):
    stmt = select(AvariaSaida)
    if prefixo:
        stmt = stmt.where(AvariaSaida.prefixo == prefixo.strip())
    if status_filtro == "ABERTA":
        stmt = stmt.where(AvariaSaida.status == "ABERTA")
    elif status_filtro == "ENCERRADA":
        stmt = stmt.where(AvariaSaida.status != "ABERTA")
    if desde:
        stmt = stmt.where(AvariaSaida.data_servico >= desde)
    if ate:
        stmt = stmt.where(AvariaSaida.data_servico <= ate)

    if linha_codigo or motorista_re or registrado_por:
        sub = select(AvariaConstatacao.avaria_id)
        if linha_codigo:
            sub = sub.where(AvariaConstatacao.linha_codigo == linha_codigo)
        if motorista_re:
            sub = sub.where(AvariaConstatacao.motorista_re == (normalizar_re(motorista_re) or motorista_re.strip()))
        if registrado_por:
            re_norm = normalizar_re(registrado_por) or registrado_por.strip()
            sub = sub.where(
                AvariaConstatacao.registrado_por.in_(select(Funcionario.id).where(Funcionario.re == re_norm))
            )
        stmt = stmt.where(AvariaSaida.id.in_(sub))

    avarias = db.execute(stmt.order_by(AvariaSaida.primeira_vez_em.desc())).scalars().all()
    if not avarias:
        return []

    todas_constatacoes = db.execute(
        select(AvariaConstatacao)
        .where(AvariaConstatacao.avaria_id.in_([a.id for a in avarias]))
        .order_by(AvariaConstatacao.avaria_id, AvariaConstatacao.ocorrido_em.asc())
    ).scalars().all()
    primeira_por_avaria: dict[UUID, AvariaConstatacao] = {}
    for c in todas_constatacoes:
        primeira_por_avaria.setdefault(c.avaria_id, c)

    nomes = _resolver_re_nome_em_lote(db, [c.registrado_por for c in primeira_por_avaria.values()])

    itens = []
    for avaria in avarias:
        primeira = primeira_por_avaria.get(avaria.id)
        re, nome = nomes.get(primeira.registrado_por, (None, None)) if primeira else (None, None)
        itens.append(AvariaHistoricoItem(
            id=avaria.id, prefixo=avaria.prefixo, zona_codigo=avaria.zona_codigo, tipo_codigo=avaria.tipo_codigo,
            severidade=avaria.severidade, status=avaria.status,
            primeira_vez_em=avaria.primeira_vez_em, ultima_vez_em=avaria.ultima_vez_em,
            vezes_vista=avaria.vezes_vista, encerrada_em=avaria.encerrada_em, encerramento=avaria.encerramento,
            registrado_por_re=re, registrado_por_nome=nome,
            linha_codigo=primeira.linha_codigo if primeira else None,
        ))
    return itens


@router.get(
    "/avarias/{avaria_id}",
    response_model=AvariaDetalheRead,
    summary="Avaria com todas as constatações em ordem cronológica — a janela de responsabilidade",
)
def detalhar_avaria(avaria_id: UUID, usuario: LeituraAcesso, db: DbSession):
    avaria = db.get(AvariaSaida, avaria_id)
    if avaria is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Avaria não encontrada.")

    constatacoes = db.execute(
        select(AvariaConstatacao)
        .where(AvariaConstatacao.avaria_id == avaria_id)
        .order_by(AvariaConstatacao.ocorrido_em.asc())
    ).scalars().all()
    nomes = _resolver_re_nome_em_lote(db, [c.registrado_por for c in constatacoes])

    itens = []
    for c in constatacoes:
        item = AvariaConstatacaoRead.model_validate(c)
        item.registrado_por_re, item.registrado_por_nome = nomes.get(c.registrado_por, (None, None))
        itens.append(item)

    resultado = AvariaDetalheRead.model_validate(avaria)
    resultado.constatacoes = itens
    return resultado


@router.get(
    "/avarias",
    response_model=list[AvariaSaidaRead],
    summary="Histórico por prefixo/data/RE do motorista/'minhas' — nunca registro vencido",
)
def listar_avarias(
    usuario: LeituraAcesso, db: DbSession,
    prefixo: Optional[str] = Query(None, min_length=1, max_length=10),
    data: Optional[date] = None,
    motorista_re: Optional[str] = Query(None, max_length=20),
    minhas: bool = False,
):
    stmt = select(AvariaSaida).where(AvariaSaida.expira_em > agora_utc())
    if prefixo:
        stmt = stmt.where(AvariaSaida.prefixo == prefixo.strip())
    if data:
        stmt = stmt.where(AvariaSaida.data_servico == data)
    if motorista_re:
        # 🟢 "tudo que aquele RE já marcou" (P2) — via CONSTATAÇÃO, não só
        # avaria_saida.motorista_re: um RE pode ter só confirmado um dano
        # aberto por outra pessoa, e essa é exatamente a defesa que ele
        # precisa poder achar.
        re_norm = normalizar_re(motorista_re) or motorista_re.strip()
        stmt = stmt.where(
            AvariaSaida.id.in_(select(AvariaConstatacao.avaria_id).where(AvariaConstatacao.motorista_re == re_norm))
        )
    if minhas:
        stmt = stmt.where(AvariaSaida.registrado_por == usuario.id)
    stmt = stmt.order_by(AvariaSaida.ocorrido_em.desc())
    return db.execute(stmt).scalars().all()


# ============================================================================
# Contestação / encerramento / anulação
# ============================================================================

@router.post(
    "/avarias/{avaria_id}/contestar",
    response_model=AvariaContestacaoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Controlador sinaliza 'não vi mais essa' — regra das duas pode encerrar sozinha",
)
def contestar_avaria(avaria_id: UUID, payload: AvariaContestarRequest, usuario: EscritaAcesso, db: DbSession):
    avaria = db.get(AvariaSaida, avaria_id)
    if avaria is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Avaria não encontrada.")

    contestacao = AvariaContestacao(
        avaria_id=avaria_id, data_servico=get_data_servico(), nota=payload.nota, registrado_por=usuario.id,
    )
    db.add(contestacao)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Você já contestou esta avaria.")

    if avaria.status == "ABERTA":
        todas = db.execute(
            select(AvariaContestacao).where(AvariaContestacao.avaria_id == avaria_id)
        ).scalars().all()
        if _duas_contestacoes_fecham(todas):
            agora = agora_utc()
            avaria.status = "INEXISTENTE"
            avaria.encerramento = "NAO_EXISTIA"
            avaria.encerrada_por = None  # 🔴 encerramento do SISTEMA, não de pessoa
            avaria.encerrada_em = agora
            avaria.expira_em = calcular_expira_em(avaria.ultima_vez_em or agora, agora)

    db.commit()
    db.refresh(contestacao)
    resultado = AvariaContestacaoRead.model_validate(contestacao)
    resultado.registrado_por_re = usuario.re
    resultado.registrado_por_nome = usuario.nome
    return resultado


@router.post(
    "/avarias/{avaria_id}/encerrar",
    response_model=AvariaSaidaRead,
    summary="Manutenção dá baixa (Fase 2) — quem conserta não é quem confere a saída",
)
def encerrar_avaria(avaria_id: UUID, payload: AvariaEncerrarRequest, usuario: EscritaManutencao, db: DbSession):
    avaria = db.get(AvariaSaida, avaria_id)
    if avaria is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Avaria não encontrada.")
    if avaria.status != "ABERTA":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Avaria já encerrada (status={avaria.status}).")

    agora = agora_utc()
    # ⚠️ DECISÃO TOMADA NESTA SESSÃO (não estava no prompt de origem): o
    # vocabulário de `status` (ABERTA|REPARADA|INEXISTENTE) não tem valor
    # pra DUPLICADA. Mapeada para INEXISTENTE — o estado do DANO ("não
    # existe como avaria própria") é o mesmo de NAO_EXISTIA; `encerramento`
    # continua guardando o motivo real (DUPLICADA), então nada se perde.
    # Ver RELATORIO-2026-09-16-P2-P3.md.
    avaria.status = "REPARADA" if payload.encerramento == "REPARADA" else "INEXISTENTE"
    avaria.encerramento = payload.encerramento
    avaria.encerramento_nota = payload.nota
    avaria.encerrada_em = agora
    avaria.encerrada_por = usuario.id
    avaria.expira_em = calcular_expira_em(avaria.ultima_vez_em or agora, agora)

    db.commit()
    db.refresh(avaria)
    return avaria


@router.post(
    "/constatacoes/{constatacao_id}/anular",
    response_model=AvariaConstatacaoRead,
    summary="Correção de constatação — nunca PUT nem DELETE, é prova",
)
def anular_constatacao(
    constatacao_id: UUID, payload: AvariaConstatacaoAnularRequest, usuario: EscritaAcesso, db: DbSession,
):
    constatacao = db.get(AvariaConstatacao, constatacao_id)
    if constatacao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Constatação não encontrada.")
    if constatacao.anulada_em is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Constatação já anulada.")

    constatacao.anulada_em = agora_utc()
    constatacao.anulada_por = usuario.id
    constatacao.anulacao_nota = payload.anulacao_nota

    db.commit()
    db.refresh(constatacao)
    resultado = AvariaConstatacaoRead.model_validate(constatacao)
    resultado.registrado_por_re, resultado.registrado_por_nome = _resolver_re_nome_em_lote(
        db, [constatacao.registrado_por]
    ).get(constatacao.registrado_por, (None, None))
    return resultado


# ============================================================================
# Catálogo de linhas servido pela Portaria (P3) — porta própria porque
# CONTROLADOR_ACESSO não tem (e não deveria ganhar) o recurso
# `fiscalizacao` (menor privilégio, migration 020). Mesma tabela que
# GET /fiscalizacao/catalogo/linhas lê — o catálogo é um só, o que muda é
# a porta.
# ============================================================================

@router.get(
    "/catalogo/linhas",
    response_model=list[CatalogoLinhaPortariaItem],
    summary="Linhas do catálogo do Pátio, servidas pela Portaria — mesma tabela de /fiscalizacao/catalogo/linhas",
)
def catalogo_linhas_portaria(usuario: LeituraAcesso, db: DbSession, incluir_inativas: bool = Query(False)):
    query = select(Linha)
    if not incluir_inativas:
        query = query.where(Linha.ativa.is_(True))
    return db.execute(query.order_by(Linha.codigo)).scalars().all()
