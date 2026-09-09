"""Endpoints do módulo Portaria — registro de entrada/saída de veículos.

🔴 REGRA NÚMERO UM (§1.1 do prompt): o sistema NUNCA impede um registro.
Nenhum endpoint deste arquivo retorna 4xx por causa da SITUAÇÃO do veículo
— 4xx só para erro real: payload inválido, sem permissão, recurso
inexistente. Veículo suspenso/baixado, pendente ou nem cadastrado: o
POST /movimentos sempre registra, e só avisa.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import get_db
from app.core.deps import exige
from app.core.placa import normalizar_placa
from app.core.uploads import ler_upload_limitado, validar_assinatura
from app.models.cadastro import Funcionario
from app.models.portaria import Credencial, MovimentoPortaria, PortariaSetor, VeiculoPortaria
from app.schemas.portaria import (
    BuscaVeiculoResponse, FrotaItemRead, FrotaResponse, LeituraPlacaResponse, MovimentoCreate,
    MovimentoCreateResponse, MovimentoDentroRead, MovimentoRead, PortariaDentroResponse,
    Propriedade, RecursosPortariaResponse, ResolverPrefixoAcessoResponse, ResolverReResponse,
    Sentido, SetorRead, VeiculoCandidato,
)
from app.services import leitura_placa
from app.services.identidade import resolver_por_re
from app.services.portaria import resolver_onibus_por_prefixo, veiculo_read

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular"))]
EscritaAcesso = Annotated[Funcionario, Depends(exige("acesso_veicular", escrever=True))]


# ============================================================================
# RECURSOS (PROMPT-leitura-placa-engine.md, R6) — a tela pergunta o que
# está ligado em vez de assumir. ⛔ Não é recurso novo de RBAC (P12): usa o
# mesmo LeituraAcesso do resto do módulo.
# ============================================================================

@router.get(
    "/recursos",
    response_model=RecursosPortariaResponse,
    summary="Flags de recursos opcionais da Portaria (ex.: leitura de placa) — evita depender do localStorage da sessão",
)
def recursos(usuario: LeituraAcesso):
    return RecursosPortariaResponse(leitura_placa_ativa=get_settings().leitura_placa_ativa)


# ============================================================================
# "DENTRO AGORA" (D3, D17, D18) — deriva o mesmo estado de portaria.vw_dentro
# via ORM, sem depender da view: a view usa DISTINCT ON (Postgres puro) e os
# testes deste módulo rodam em SQLite (Bloco D). ⛔ Não filtra dentro da
# view, e ⛔ não "fecha" movimento nenhum automaticamente — só separa em
# duas listas o que já existe.
#
# 🔴 D18: frota de apoio (veiculo.propriedade='EMPRESA') e reservado
# (prefixo preenchido) ficam de fora — têm painel/contador próprios
# (GET /portaria/frota; reservado não soma em nenhum). A exclusão entra
# DENTRO da subquery que acha o último movimento por placa, nunca só no
# resultado final — senão a última passagem de uma placa excluída ainda
# mascararia a entrada anterior de um veículo de verdade com aquela placa.
# ============================================================================

def _dentro_e_sem_saida(
    db: Session, horas: int
) -> tuple[list[MovimentoPortaria], list[MovimentoPortaria]]:
    ultimo_por_placa = (
        select(
            MovimentoPortaria.placa_registrada.label("placa"),
            func.max(MovimentoPortaria.momento).label("momento_max"),
        )
        .outerjoin(VeiculoPortaria, VeiculoPortaria.id == MovimentoPortaria.veiculo_id)
        .where(
            MovimentoPortaria.prefixo.is_(None),
            MovimentoPortaria.placa_registrada.isnot(None),
            or_(VeiculoPortaria.id.is_(None), VeiculoPortaria.propriedade != "EMPRESA"),
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


def _enriquecer_dentro(db: Session, movimentos: list[MovimentoPortaria]) -> list[MovimentoDentroRead]:
    """D18: uma query pra veículo + uma pra setor, nunca uma por item — a
    tela faz polling a cada POLLING_INTERVAL_MS, N+1 aqui roda pra sempre."""
    veiculo_ids = {m.veiculo_id for m in movimentos if m.veiculo_id}
    veiculos = {
        v.id: v for v in db.execute(
            select(VeiculoPortaria).where(VeiculoPortaria.id.in_(veiculo_ids))
        ).scalars()
    } if veiculo_ids else {}

    setor_codigos = {m.setor_codigo for m in movimentos if m.setor_codigo}
    setores = {
        s.codigo: s.nome for s in db.execute(
            select(PortariaSetor).where(PortariaSetor.codigo.in_(setor_codigos))
        ).scalars()
    } if setor_codigos else {}

    resultado: list[MovimentoDentroRead] = []
    for mov in movimentos:
        veiculo = veiculos.get(mov.veiculo_id) if mov.veiculo_id else None
        # EMPRESA já saiu na subquery de _dentro_e_sem_saida — só resta
        # PARTICULAR/TERCEIRO (cadastrado). Sem veiculo_id (P1): terceiro
        # avulso — empresa sem cadastro (__OUTRA__ sem marcar "cadastrar")
        # ou placa desconhecida com condutor identificado — ainda é
        # TERCEIRO se veio terceiro_empresa OU terceiro_nome; só cai em
        # AVULSO de verdade quando não sobrou identificação nenhuma. Sem
        # isso, o prestador sem empresa cadastrada aparecia em "Particular"
        # na tela em vez de "Terceiros".
        if veiculo is not None:
            grupo = veiculo.propriedade
        elif mov.terceiro_empresa or mov.terceiro_nome:
            grupo = "TERCEIRO"
        else:
            grupo = "AVULSO"
        # setor_nome já é campo de MovimentoRead (resolvido aqui, não é
        # coluna) — entra no dump antes do spread, não como kwarg separado,
        # senão colide com o que o dump já carrega.
        base = MovimentoRead.model_validate(mov).model_dump()
        base["setor_nome"] = setores.get(mov.setor_codigo) if mov.setor_codigo else None
        resultado.append(MovimentoDentroRead(
            **base,
            veiculo_propriedade=veiculo.propriedade if veiculo else None,
            veiculo_tipo=veiculo.tipo if veiculo else None,
            marca_modelo=veiculo.marca_modelo if veiculo else None,
            grupo=grupo,
        ))
    return resultado


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
    dentro_enriquecido = _enriquecer_dentro(db, dentro)

    contagem = {"PARTICULAR": 0, "TERCEIRO": 0, "AVULSO": 0}
    for mov in dentro_enriquecido:
        contagem[mov.grupo] = contagem.get(mov.grupo, 0) + 1

    return PortariaDentroResponse(
        dentro=dentro_enriquecido, sem_saida=sem_saida, horas=horas, contagem=contagem,
    )


# ============================================================================
# FROTA DE APOIO (D18, migration 041) — leitura INVERTIDA: a pergunta é
# "cadê a moto", não "quem está dentro". ⛔ Duas queries no total (veículos
# + último movimento por placa), nunca uma por veículo.
# ============================================================================

@router.get(
    "/frota",
    response_model=FrotaResponse,
    summary="Painel da frota de apoio (moto/van/guincho/...) — quem está na rua, com quem e desde quando (D18)",
)
def frota(usuario: LeituraAcesso, db: Annotated[Session, Depends(get_db)]):
    veiculos = db.execute(
        select(VeiculoPortaria)
        .where(VeiculoPortaria.propriedade == "EMPRESA", VeiculoPortaria.ativo.is_(True))
        .order_by(VeiculoPortaria.placa)
    ).scalars().all()

    placas = [v.placa for v in veiculos]
    ultimo_por_placa = (
        select(
            MovimentoPortaria.placa_registrada.label("placa"),
            func.max(MovimentoPortaria.momento).label("momento_max"),
        )
        .where(MovimentoPortaria.placa_registrada.in_(placas))
        .group_by(MovimentoPortaria.placa_registrada)
        .subquery()
    ) if placas else None
    ultimos: dict[str, MovimentoPortaria] = {}
    if ultimo_por_placa is not None:
        linhas = db.execute(
            select(MovimentoPortaria).join(
                ultimo_por_placa,
                (MovimentoPortaria.placa_registrada == ultimo_por_placa.c.placa)
                & (MovimentoPortaria.momento == ultimo_por_placa.c.momento_max),
            )
        ).scalars().all()
        ultimos = {m.placa_registrada: m for m in linhas}

    na_rua: list[FrotaItemRead] = []
    disponiveis: list[FrotaItemRead] = []
    for v in veiculos:
        ultimo = ultimos.get(v.placa)
        base = dict(veiculo_id=v.id, placa=v.placa, tipo=v.tipo, marca_modelo=v.marca_modelo)
        if ultimo is not None and ultimo.sentido == "SAIDA":
            na_rua.append(FrotaItemRead(
                **base, na_rua=True, desde=ultimo.momento,
                condutor_re=ultimo.re_registrado, condutor_nome=ultimo.nome_registrado,
                hodometro_km=ultimo.hodometro_km,
            ))
        else:
            # ENTRADA ou nenhum movimento -> disponível na garagem.
            disponiveis.append(FrotaItemRead(**base, na_rua=False))

    return FrotaResponse(na_rua=na_rua, disponiveis=disponiveis)


# ============================================================================
# SETOR de destino da visita (P3/R4/R6, migration 041) — espelha
# listar_empresas. Lista fechada em três (R6), mas vem do banco, não de
# Literal — desativar é UPDATE, nunca exige deploy.
# ============================================================================

@router.get(
    "/setores",
    response_model=list[SetorRead],
    summary="Lista setores de destino da visita (Manutenção/Operação/Administração — R6)",
)
def listar_setores(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    apenas_ativos: bool = True,
):
    stmt = select(PortariaSetor)
    if apenas_ativos:
        stmt = stmt.where(PortariaSetor.ativo.is_(True))
    stmt = stmt.order_by(PortariaSetor.ordem)
    return db.execute(stmt).scalars().all()


# ============================================================================
# RESOLVER PREFIXO (P4, migration 041) — mesma UX de "cadastrado"/"não
# cadastrado" que /portaria/recolhidas/resolver-prefixo já dava, mas gated
# por acesso_veicular (quem registra passagem), não recolhida_anormal.
# ⛔ Não achar NÃO bloqueia — o reservado registra com o prefixo digitado.
# ============================================================================

@router.get(
    "/resolver-prefixo",
    response_model=ResolverPrefixoAcessoResponse,
    summary="Mostra 'cadastrado'/'não cadastrado' pro prefixo do reservado (P4) — nunca bloqueia o registro",
)
def resolver_prefixo_acesso(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    prefixo: str = Query(..., min_length=1, max_length=10),
):
    onibus = resolver_onibus_por_prefixo(db, prefixo)
    if onibus is None:
        return ResolverPrefixoAcessoResponse(encontrado=False)
    return ResolverPrefixoAcessoResponse(encontrado=True, placa=onibus.placa)


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
# LEITURA DE PLACA POR CÂMERA (Bloco 1) — substitui o QR do Bloco E.
# Ver _handoff-claude/PROMPT-leitura-placa.md, P1 a P14.
#
# 🔴 P1/P2: este endpoint NUNCA registra movimento — só devolve o texto
# lido pra tela mostrar "Confirma a placa?" (editável). A confirmação
# humana chama /portaria/buscar com a placa, igual à digitação — a mesma
# LeituraAcesso desta rota, nunca um recurso novo (P12).
#
# 🔴 P4: a imagem trafega até aqui, é lida em memória (`ler_upload_limitado`)
# e descartada quando a função termina — nunca grava em disco, nunca vira
# coluna, nunca vira arquivo temporário. `conteudo` (bytes) não escapa
# desta função.
# ============================================================================

_LEITURA_PLACA_MIME_PERMITIDOS = {"image/jpeg", "image/png"}
_LEITURA_PLACA_TAMANHO_MAXIMO = 8 * 1024 * 1024  # 8 MB — foto de celular já comprimida (P5)
# Orçamento de tempo da REQUISIÇÃO, não da engine — margem de segurança
# sobre o limiar de ~4s do Bloco 0 (acima disso o controlador desiste e
# volta a digitar). Quando a engine real for medida no servidor, este
# número pode ser ajustado sem tocar em mais nada.
_LEITURA_PLACA_TIMEOUT_SEGUNDOS = 8.0


@router.post(
    "/ler-placa",
    response_model=LeituraPlacaResponse,
    summary="Leitura de placa por foto (câmera) — motor pluggável, imagem nunca gravada (P4)",
)
async def ler_placa(
    usuario: LeituraAcesso,
    request: Request,
    arquivo: Annotated[UploadFile, File(description="image/jpeg ou image/png — máx 8 MB")],
):
    # P14 — interruptor. Desligado: nem chega a olhar o arquivo.
    if not get_settings().leitura_placa_ativa:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Leitura de placa por câmera está desativada.",
        )

    if arquivo.content_type not in _LEITURA_PLACA_MIME_PERMITIDOS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato não suportado: {arquivo.content_type}. Envie JPEG ou PNG.",
        )

    # SEV-12 (mesmo cuidado de ocorrencias.upload_anexo): lê em blocos,
    # abortando ao estourar — nunca o arquivo inteiro em memória antes de
    # saber se ele cabe.
    conteudo = await ler_upload_limitado(arquivo, _LEITURA_PLACA_TAMANHO_MAXIMO, request)

    # SEV-13: Content-Type é o que o cliente afirma — os primeiros bytes
    # do arquivo não mentem.
    if not validar_assinatura(conteudo, arquivo.content_type):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="O conteúdo do arquivo não corresponde a um JPEG/PNG válido.",
        )

    # `asyncio.to_thread` porque o motor plugado (Bloco 0) é síncrono e
    # pode ser CPU-bound; `wait_for` garante que a REQUISIÇÃO nunca fica
    # pendurada além do orçamento, mesmo que a thread da engine continue
    # rodando em segundo plano até terminar sozinha.
    try:
        resultado = await asyncio.wait_for(
            asyncio.to_thread(leitura_placa.reconhecer_placa, conteudo),
            timeout=_LEITURA_PLACA_TIMEOUT_SEGUNDOS,
        )
    except asyncio.TimeoutError:
        # P7 — falha de leitura devolve pra digitação, sem drama: 200 com
        # "não achou", não 5xx. A tela trata igual a qualquer outra falha.
        return LeituraPlacaResponse(placa_lida=None, confianca=0.0)
    # `conteudo` sai de escopo aqui sem nunca ter sido escrito em disco (P4).

    # D10: normaliza, nunca recusa por formato — placa_valida() não entra
    # aqui, igual ao resto do módulo.
    placa_lida = normalizar_placa(resultado.placa_lida) if resultado.placa_lida else None
    return LeituraPlacaResponse(placa_lida=placa_lida, confianca=resultado.confianca)


@router.get(
    "/resolver-re",
    response_model=ResolverReResponse,
    summary="§5.3 — confirma quem é um RE exato (funcionario ou motorista), sem devolver dado sensível",
)
def resolver_re(
    usuario: LeituraAcesso,
    db: Annotated[Session, Depends(get_db)],
    re: str = Query(..., min_length=3, max_length=20),
):
    # Mesmo cuidado do /funcionarios/busca (§4.5-A da revisão anterior):
    # RE exato, não busca por prefixo — nunca listagem aberta. ⛔ Nunca
    # 404 — RE inexistente é resultado válido (regra número um).
    pessoa = resolver_por_re(db, re)
    if pessoa is None:
        return ResolverReResponse(encontrado=False)
    return ResolverReResponse(
        encontrado=True, nome=pessoa.nome, origem=pessoa.origem, ativo=pessoa.ativo
    )


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

    # R1.b: reservado não tem placa — sem ela, ⛔ nem consultar veículo
    # (reservado nunca tem cadastro; e `placa == None` viraria `placa IS
    # NULL` em SQL, que casaria sorrateiramente com outro movimento sem
    # placa se isto fosse reaproveitado pra buscar veículo).
    veiculo = None
    if payload.placa:
        veiculo = db.execute(
            select(VeiculoPortaria).where(VeiculoPortaria.placa == payload.placa, VeiculoPortaria.ativo.is_(True))
        ).scalar_one_or_none()

    # P4/R1: resolve onibus_id por conveniência de leitura — ⛔ nunca
    # preenche placa a partir do prefixo (R1.b). Não achou: registra assim
    # mesmo, com o prefixo digitado (regra número um).
    onibus_id: Optional[UUID] = None
    if payload.prefixo:
        onibus = resolver_onibus_por_prefixo(db, payload.prefixo)
        onibus_id = onibus.id if onibus is not None else None

    # P3/R4/R6: setor opcional. Código inexistente -> aviso, nunca 422
    # (regra número um aplicada ao campo novo) — o destino fica só no
    # texto digitado. Setor válido sem texto -> terceiro_destino herda o
    # nome do setor (os dois convivem, nunca um substitui o outro).
    setor_codigo_valido: Optional[str] = None
    terceiro_destino = payload.terceiro_destino
    if payload.setor_codigo:
        setor = db.get(PortariaSetor, payload.setor_codigo)
        if setor is not None:
            setor_codigo_valido = setor.codigo
            if not (terceiro_destino or "").strip():
                terceiro_destino = setor.nome
        else:
            avisos.append(f"Setor '{payload.setor_codigo}' não encontrado — destino ficou só no texto.")

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
                status.HTTP_422_UNPROCESSABLE_CONTENT,
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

    # P4: reservado não tem veiculo cadastrado (nunca passa pelo `exige_hodometro`
    # acima), mas o hodômetro é pedido sempre mesmo assim — regra número um,
    # em branco avisa, nunca bloqueia.
    if payload.prefixo and payload.hodometro_km is None:
        avisos.append("Hodômetro não informado — ficou pendente.")

    if veiculo is None and payload.placa:
        avisos.append("Placa não cadastrada — passagem registrada como avulsa.")

    # Conveniência (D3) — nunca requisito. Se não bater, ignora o vínculo.
    # ⛔ Exige payload.placa — com ela None (reservado), `entrada.placa_registrada
    # == payload.placa` comparando None == None casaria com qualquer outra
    # entrada de reservado sem relação nenhuma com esta passagem.
    movimento_entrada_id: Optional[UUID] = None
    if payload.sentido == "SAIDA" and payload.movimento_entrada_id is not None and payload.placa:
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
        terceiro_destino=terceiro_destino,
        terceiro_empresa=payload.terceiro_empresa,
        prefixo=payload.prefixo,
        onibus_id=onibus_id,
        setor_codigo=setor_codigo_valido,
        hodometro_km=payload.hodometro_km,
        cadastrado=veiculo is not None,
        origem=payload.origem,
        placa_lida_bruta=payload.placa_lida_bruta,
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
    # P3/P4 (migration 041) — é o que faz setor e reservado valerem a pena:
    # "quantos terceiros foram à manutenção em setembro", "quantos
    # reservados saíram hoje".
    setor_codigo: Optional[str] = None,
    apenas_reservados: bool = False,
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
    if setor_codigo:
        stmt = stmt.where(MovimentoPortaria.setor_codigo == setor_codigo)
    if apenas_reservados:
        stmt = stmt.where(MovimentoPortaria.prefixo.isnot(None))
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
