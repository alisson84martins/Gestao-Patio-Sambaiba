"""Endpoints AUTENTICADOS da pré-ocorrência — abrir, acompanhar, converter.

O endpoint que o motorista usa (sem login) está em
pre_ocorrencias_publico.py — nunca neste arquivo. Ver
_handoff-claude/DESENHO-pre-ocorrencia.md.
"""
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import get_db
from app.core.deps import exige
from app.core.uploads import resolver_caminho_seguro
from app.models.cadastro import Funcionario
from app.models.ocorrencia import Ocorrencia, OcorrenciaAnexo, TipoOcorrencia
from app.models.pre_ocorrencia import PreOcorrencia, PreOcorrenciaAnexo, PreOcorrenciaAutorizacao
from app.schemas.pre_ocorrencia import (
    AbrirAutorizacaoRequest, AutopreenchimentoPessoaPreOcorrencia, AutorizacaoAbertaResponse,
    AutorizacaoResumo, AutorizacaoResumoCCO, CancelarAutorizacaoResponse, ConverterRequest,
    ConverterResponse, PreOcorrenciaDetalhe, PreOcorrenciaFilaItem,
)
from app.services.n8n import notificar_autorizacao_aberta
from app.services.pre_ocorrencia_token import EXPIRACAO_HORAS, gerar_token

# Mesma base de app/routers/ocorrencias.py e
# app/services/uploads_pre_ocorrencia.py (Path("uploads")) — anexo.caminho
# já inclui o prefixo "pre_ocorrencias/", por isso a base pra
# resolver_caminho_seguro() é a raiz de uploads, não PRE_OC_UPLOAD_ROOT.
UPLOADS_BASE = Path("uploads")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pre-ocorrencias", tags=["pré-ocorrência"])

LeituraPreOcorrencia = Annotated[Funcionario, Depends(exige("pre_ocorrencia"))]
EscritaPreOcorrencia = Annotated[Funcionario, Depends(exige("pre_ocorrencia", escrever=True))]


def _eh_admin(db: Session, funcionario_id: UUID) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'ADMIN'"
        ),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def _eh_cco(db: Session, funcionario_id: UUID) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'CCO'"
        ),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def _ve_todas_pre_ocorrencias(db: Session, funcionario_id: UUID) -> bool:
    """Só ADMIN enxerga a fila inteira — decisão de Alisson (19/08/2026).

    Até essa data espelhava _ve_todas_ocorrencias() de ocorrencias.py
    (ADMIN, ENCARREGADO, GERENTE_GERAL, GERENTE_OPERACIONAL). O paralelo
    deixa de existir de propósito: pré-ocorrência carrega dado de
    terceiro cru, digitado pelo motorista e ainda não revisado por
    nenhum coordenador — gerência e encarregado não têm por que ver isso.
    Eles leem a OCORRÊNCIA DEFINITIVA depois de convertida, que é o
    documento deles. Na fila, cada coordenador vê só o que é dele: o que
    ele mesmo abriu ou o que o CCO direcionou a ele.

    Usada em listar() e em _exige_pode_ver_pre_ocorrencia() — a regra é
    uma só, não duplicar."""
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'ADMIN'"
        ),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def _exige_pode_ver_pre_ocorrencia(autorizacao: PreOcorrenciaAutorizacao, usuario: Funcionario, db: Session) -> None:
    """403, nunca 404 — mesma política de _exige_pode_ver() em
    ocorrencias.py. Quem não pode ver tem direito de saber que o registro
    existe e que o impedimento é de permissão."""
    if autorizacao.coordenador_id == usuario.id:
        return
    if _ve_todas_pre_ocorrencias(db, usuario.id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Você só pode ver as pré-ocorrências direcionadas a você")


def _preencher_pre_ocorrencia_com_dados_conhecidos(
    db: Session, autorizacao: PreOcorrenciaAutorizacao
) -> PreOcorrencia:
    """Cria a PreOcorrencia desta autorização já preenchida com o que se
    sabe ANTES do motorista abrir o link — decisão de Alisson (15/08/2026),
    flexibilização pontual da decisão 5 (ver aviso no topo de
    schemas/pre_ocorrencia.py). Roda aqui, no lado AUTENTICADO — o
    endpoint público (pre_ocorrencias_publico.py) continua só devolvendo o
    que já está gravado, nunca consulta o cadastro central por conta própria.

    Mesmos defaults de _carregar_ou_criar_pre_ocorrencia()
    (pre_ocorrencias_publico.py) pros dois campos NOT NULL: hora de
    parede no fuso de operação — nunca UTC, regra de 14/08 — e relato
    vazio que o motorista completa depois.

    Precedência (nunca sobrescreve o que a autorização já trouxe):
      1. autorização — o que quem abriu digitou
      2. cadastro central (Funcionario pelo RE) — só nos campos que
         sobraram vazios
    Cobrador e prefixo NUNCA vêm do cadastro (decisão 10) — prefixo só
    quando a autorização informou; cobrador não tem de onde vir aqui
    (a autorização não tem campo de cobrador).
    """
    pre_oc = PreOcorrencia(
        autorizacao_id=autorizacao.id,
        hora_ocorrencia=datetime.now(FUSO_OPERACAO).time(),
        relato="",
        status="AGUARDANDO",
        motorista_re=autorizacao.motorista_re,
        motorista_nome=autorizacao.motorista_nome,
        prefixo=autorizacao.prefixo,
    )

    if autorizacao.motorista_re:
        funcionario = db.execute(
            select(Funcionario).where(Funcionario.re == autorizacao.motorista_re.strip())
        ).scalar_one_or_none()
        if funcionario is not None:
            # RE não encontrado → segue sem preencher, nunca falha a
            # abertura da autorização por causa disso (mesmo padrão do
            # catch silencioso do autopreenchimento de ocorrencias.py).
            if not pre_oc.motorista_nome:
                pre_oc.motorista_nome = funcionario.nome
            pre_oc.motorista_telefone = funcionario.telefone or None
            pre_oc.motorista_cpf = funcionario.cpf or None
            pre_oc.motorista_rg = funcionario.rg or None
            pre_oc.motorista_cnh = funcionario.cnh or None

    db.add(pre_oc)
    return pre_oc


# ============================================================================
# Autopreencher pessoa (deve vir ANTES de /{pre_ocorrencia_id})
# ============================================================================

@router.get(
    "/autopreencher/pessoa",
    response_model=AutopreenchimentoPessoaPreOcorrencia,
    summary="Nome e telefone a partir do RE — confirma quem é sem devolver documento (decisão 4)",
)
def autopreencher_pessoa(
    _: EscritaPreOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    re: str = Query(..., max_length=20),
):
    funcionario = db.execute(
        select(Funcionario).where(Funcionario.re == re.strip())
    ).scalar_one_or_none()
    if funcionario is None:
        return AutopreenchimentoPessoaPreOcorrencia()
    return AutopreenchimentoPessoaPreOcorrencia(nome=funcionario.nome, telefone=funcionario.telefone)


# ============================================================================
# Abrir autorização
# ============================================================================

@router.post(
    "/autorizacoes",
    response_model=AutorizacaoAbertaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Abre uma janela de pré-ocorrência e devolve o link (token em claro, uma única vez)",
)
def abrir_autorizacao(
    payload: AbrirAutorizacaoRequest,
    usuario: EscritaPreOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    if _eh_cco(db, usuario.id):
        coordenador = None
        if payload.coordenador_id is not None:
            coordenador = db.get(Funcionario, payload.coordenador_id)
        elif payload.coordenador_re:
            # CCO não tem leitura em "usuarios" (decisão 4) — resolve RE
            # internamente aqui, sem depender de /funcionarios/verificar.
            coordenador = db.execute(
                select(Funcionario).where(Funcionario.re == payload.coordenador_re.strip())
            ).scalar_one_or_none()
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="CCO precisa informar o coordenador de destino (RE) — CCO só roteia, não é dono de pré-ocorrência.",
            )
        if coordenador is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coordenador de destino não encontrado")
        coordenador_id = coordenador.id
    else:
        # Coordenador abrindo pra si mesmo — decisão 3, o padrão é ele mesmo.
        coordenador_id = payload.coordenador_id or usuario.id
        coordenador = db.get(Funcionario, coordenador_id) if coordenador_id != usuario.id else usuario

    token_claro, token_hash = gerar_token()
    expira_em = datetime.now(timezone.utc) + timedelta(hours=EXPIRACAO_HORAS)

    autorizacao = PreOcorrenciaAutorizacao(
        aberta_por=usuario.id,
        coordenador_id=coordenador_id,
        telefone_destino=payload.telefone_destino,
        motorista_re=payload.motorista_re,
        motorista_nome=payload.motorista_nome,
        prefixo=payload.prefixo,
        linha_codigo=payload.linha_codigo,
        token_hash=token_hash,
        expira_em=expira_em,
        status="AGUARDANDO",
    )
    db.add(autorizacao)
    # 🔴 Regra 1 do §2.5: grava no banco PRIMEIRO — o commit abaixo já
    # aconteceu quando o webhook (via BackgroundTasks) dispara depois da
    # resposta ser devolvida.
    db.commit()
    db.refresh(autorizacao)

    # A PreOcorrencia já nasce preenchida com o que a autorização e o
    # cadastro central sabem sobre o motorista — ver
    # _preencher_pre_ocorrencia_com_dados_conhecidos() acima.
    _preencher_pre_ocorrencia_com_dados_conhecidos(db, autorizacao)
    db.commit()

    link = f"{get_settings().pre_ocorrencia_link_base}?token={token_claro}"
    background_tasks.add_task(
        notificar_autorizacao_aberta,
        autorizacao_id=autorizacao.id,
        link=link,
        telefone_destino=autorizacao.telefone_destino,
        coordenador_nome=coordenador.nome if coordenador else "",
        motorista_nome=autorizacao.motorista_nome,
        prefixo=autorizacao.prefixo,
    )

    return AutorizacaoAbertaResponse(
        id=autorizacao.id, token=token_claro, link=link, expira_em=autorizacao.expira_em,
    )


# ============================================================================
# Minhas autorizações
# ============================================================================

def _como_utc(dt: datetime) -> datetime:
    """Ver mesma função em pre_ocorrencias_publico.py — Postgres devolve
    aware, SQLite (testes) pode devolver naive."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _status_texto_cco(autorizacao: PreOcorrenciaAutorizacao, coordenador_nome: str) -> str:
    # Checado ANTES de usada_em/expira_em — cancelar é uma ação deliberada
    # do CCO (item 1, 15/08/2026) e precisa aparecer como tal, não como
    # "aguardando" pra sempre (nem usada_em nem expira_em mudam ao cancelar).
    if autorizacao.status == "CANCELADA":
        return "cancelada"
    if autorizacao.usada_em is not None:
        return f"enviada, com o coordenador {coordenador_nome}"
    if _como_utc(autorizacao.expira_em) < datetime.now(timezone.utc):
        return "expirada, sem resposta do motorista"
    return "aguardando o motorista"


@router.get("/autorizacoes", summary="O que a pessoa abriu e está aguardando")
def minhas_autorizacoes(usuario: EscritaPreOcorrencia, db: Annotated[Session, Depends(get_db)]):
    # Item 2 (19/08/2026): mesma regra de listar() — convertida/descartada
    # não têm mais o que fazer aqui. outerjoin porque a PreOcorrencia
    # pode não existir (autorização sem pré-ocorrência continua
    # aparecendo — não conte com abrir_autorizacao() sempre criar uma).
    linhas = db.execute(
        select(PreOcorrenciaAutorizacao)
        .outerjoin(PreOcorrencia, PreOcorrencia.autorizacao_id == PreOcorrenciaAutorizacao.id)
        .where(PreOcorrenciaAutorizacao.aberta_por == usuario.id)
        .where(or_(PreOcorrencia.id.is_(None), PreOcorrencia.status.notin_(("CONVERTIDA", "DESCARTADA"))))
        .order_by(PreOcorrenciaAutorizacao.criada_em.desc())
    ).scalars().all()

    if _eh_cco(db, usuario.id):
        # 🔴 Decisão 4 — CCO nunca vê conteúdo, nem o que ele mesmo digitou.
        resultado = []
        for a in linhas:
            coordenador = db.get(Funcionario, a.coordenador_id)
            resultado.append(AutorizacaoResumoCCO(
                id=a.id,
                status_texto=_status_texto_cco(a, coordenador.nome if coordenador else "—"),
                criada_em=a.criada_em,
            ))
        return resultado

    return [AutorizacaoResumo.model_validate(a) for a in linhas]


# ============================================================================
# Cancelar autorização (item 1) — autor ou ADMIN, registro permanece
# ============================================================================

def _carregar_autorizacao_para_gestao(db: Session, autorizacao_id: UUID) -> PreOcorrenciaAutorizacao:
    autorizacao = db.get(PreOcorrenciaAutorizacao, autorizacao_id)
    if autorizacao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Autorização não encontrada")
    return autorizacao


def _pre_ocorrencia_da_autorizacao(db: Session, autorizacao_id: UUID) -> Optional[PreOcorrencia]:
    return db.execute(
        select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == autorizacao_id)
    ).scalar_one_or_none()


def _exige_nao_convertida(pre_oc: Optional[PreOcorrencia]) -> None:
    """Bloqueio comum a cancelar() e apagar(): uma vez virada Ocorrencia
    definitiva, o registro deixa de ser um convite e vira documento — não
    se cancela nem se apaga mais pela tela."""
    if pre_oc is not None and (pre_oc.status == "CONVERTIDA" or pre_oc.convertida_em is not None):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Esta pré-ocorrência já virou ocorrência definitiva e não pode mais ser alterada por aqui",
        )


@router.patch(
    "/autorizacoes/{autorizacao_id}/cancelar",
    response_model=CancelarAutorizacaoResponse,
    summary="Cancela a autorização — quem abriu ou ADMIN; o token para de funcionar na hora",
)
def cancelar_autorizacao(
    autorizacao_id: UUID,
    usuario: EscritaPreOcorrencia,
    db: Annotated[Session, Depends(get_db)],
):
    autorizacao = _carregar_autorizacao_para_gestao(db, autorizacao_id)
    if autorizacao.aberta_por != usuario.id and not _eh_admin(db, usuario.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Só quem abriu esta autorização ou o ADMIN pode cancelá-la",
        )

    pre_oc = _pre_ocorrencia_da_autorizacao(db, autorizacao_id)
    _exige_nao_convertida(pre_oc)

    autorizacao.status = "CANCELADA"
    # Descarta a pré-ocorrência ligada, se ela existir e ainda não tiver
    # virado ocorrência (já garantido pelo _exige_nao_convertida acima).
    if pre_oc is not None:
        pre_oc.status = "DESCARTADA"
    db.commit()
    db.refresh(autorizacao)
    return CancelarAutorizacaoResponse(id=autorizacao.id, status=autorizacao.status)


# ============================================================================
# Apagar definitivamente (item 2) — só ADMIN, remove anexos do disco antes
# ============================================================================

@router.delete(
    "/autorizacoes/{autorizacao_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclusão definitiva — ADMIN ou o coordenador destinatário; apaga autorização, pré-ocorrência e os arquivos dos anexos",
)
def apagar_autorizacao(
    autorizacao_id: UUID,
    usuario: EscritaPreOcorrencia,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    autorizacao = _carregar_autorizacao_para_gestao(db, autorizacao_id)

    # ADMIN ou o coordenador destinatário (decisão de 19/08/2026, item 1)
    # — quem só abriu (CCO) não apaga, só cancela: cancelar_autorizacao()
    # acima já é a ação dele. Continua sendo exclusão definitiva, não
    # ação de rotina — mesma ordem de checagem de cancelar_autorizacao
    # (carrega primeiro, autoriza depois).
    if autorizacao.coordenador_id != usuario.id and not _eh_admin(db, usuario.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Só o coordenador destinatário ou o ADMIN podem apagar definitivamente esta autorização",
        )

    pre_oc = _pre_ocorrencia_da_autorizacao(db, autorizacao_id)
    _exige_nao_convertida(pre_oc)

    if pre_oc is not None:
        anexos = db.execute(
            select(PreOcorrenciaAnexo).where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc.id)
        ).scalars().all()
        # 🔴 Os arquivos são removidos do disco ANTES do DELETE — o
        # ON DELETE CASCADE da migration 022 apaga a LINHA, nunca o
        # arquivo. Anexo órfão de CNH é o dado mais sensível do sistema
        # (foto, filiação, CPF e RG numa imagem só) ficando invisível pra
        # qualquer auditoria. Arquivo ausente/sem permissão nunca pode
        # impedir a limpeza do banco — loga e segue.
        for anexo in anexos:
            try:
                caminho_disco = resolver_caminho_seguro(UPLOADS_BASE, anexo.caminho)
                caminho_disco.unlink(missing_ok=True)
            except Exception:
                logger.warning(
                    "Não foi possível remover do disco o arquivo do anexo %s (pré-ocorrência %s)",
                    anexo.id, pre_oc.id,
                )

    # O banco cascateia pre_ocorrencia → pre_ocorrencia_anexo a partir daqui.
    db.delete(autorizacao)
    db.commit()


# ============================================================================
# Fila do coordenador
# ============================================================================

def _pendencias(db: Session, pre_oc_id: UUID) -> list[str]:
    tipos_presentes = {
        row[0] for row in db.execute(
            select(PreOcorrenciaAnexo.tipo).where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc_id)
        ).all()
    }
    faltando = []
    if "CNH" not in tipos_presentes:
        faltando.append("CNH")
    if "DOC_VEICULO" not in tipos_presentes:
        faltando.append("DOC_VEICULO")
    return faltando


@router.get("", response_model=list[PreOcorrenciaFilaItem], summary="Pré-ocorrências direcionadas a você")
def listar(usuario: LeituraPreOcorrencia, db: Annotated[Session, Depends(get_db)]):
    # Item 2 (19/08/2026): convertida virou Ocorrência (vive na tela de
    # Ocorrências) e descartada morreu com o cancelamento — nenhuma das
    # duas tem o que fazer na fila. Sem filtro "ver convertidas": uma
    # pré-ocorrência ou vira ocorrência ou é descartada, nos dois casos
    # sai daqui.
    q = (
        select(PreOcorrencia, PreOcorrenciaAutorizacao)
        .join(PreOcorrenciaAutorizacao, PreOcorrenciaAutorizacao.id == PreOcorrencia.autorizacao_id)
        .where(PreOcorrencia.status.notin_(("CONVERTIDA", "DESCARTADA")))
        .order_by(PreOcorrencia.criado_em.desc())
    )
    if not _ve_todas_pre_ocorrencias(db, usuario.id):
        q = q.where(PreOcorrenciaAutorizacao.coordenador_id == usuario.id)

    linhas = db.execute(q).all()
    return [
        PreOcorrenciaFilaItem(
            id=pre_oc.id,
            # Item 1.2 (19/08/2026): o botão de apagar na fila (item 1.4)
            # chama o mesmo endpoint DELETE .../autorizacoes/{autorizacao_id}
            # dos cards de "Minhas autorizações" — precisa do id da
            # autorização, não só da pré-ocorrência.
            autorizacao_id=autorizacao.id,
            status=pre_oc.status,
            motorista_nome=pre_oc.motorista_nome or autorizacao.motorista_nome,
            prefixo=pre_oc.prefixo or autorizacao.prefixo,
            criado_em=pre_oc.criado_em,
            enviada_em=pre_oc.enviada_em,
            pendencias=_pendencias(db, pre_oc.id),
        )
        for pre_oc, autorizacao in linhas
    ]


# ============================================================================
# Detalhe
# ============================================================================

@router.get("/{pre_ocorrencia_id}", response_model=PreOcorrenciaDetalhe)
def detalhar(pre_ocorrencia_id: UUID, usuario: LeituraPreOcorrencia, db: Annotated[Session, Depends(get_db)]):
    pre_oc = db.get(PreOcorrencia, pre_ocorrencia_id)
    if pre_oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pré-ocorrência não encontrada")
    autorizacao = db.get(PreOcorrenciaAutorizacao, pre_oc.autorizacao_id)
    _exige_pode_ver_pre_ocorrencia(autorizacao, usuario, db)

    anexos = db.execute(
        select(PreOcorrenciaAnexo).where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc.id)
    ).scalars().all()
    resultado = PreOcorrenciaDetalhe.model_validate(pre_oc)
    resultado.anexos = anexos
    return resultado


# ============================================================================
# Baixar anexo (item 3.2, 19/08/2026) — espelha baixar_anexo() de
# ocorrencias.py: mesma ordem de checagens, mesma defesa SEV-14.
# ============================================================================

@router.get(
    "/{pre_ocorrencia_id}/anexos/{anexo_id}/arquivo",
    summary="Baixa o arquivo do anexo da pré-ocorrência — mesmo acesso de detalhar() (nunca público)",
)
def baixar_anexo(
    pre_ocorrencia_id: UUID, anexo_id: UUID, usuario: LeituraPreOcorrencia, db: Annotated[Session, Depends(get_db)]
):
    pre_oc = db.get(PreOcorrencia, pre_ocorrencia_id)
    if pre_oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pré-ocorrência não encontrada")
    autorizacao = db.get(PreOcorrenciaAutorizacao, pre_oc.autorizacao_id)
    _exige_pode_ver_pre_ocorrencia(autorizacao, usuario, db)

    anexo = db.execute(
        select(PreOcorrenciaAnexo).where(
            PreOcorrenciaAnexo.id == anexo_id, PreOcorrenciaAnexo.pre_ocorrencia_id == pre_ocorrencia_id
        )
    ).scalar_one_or_none()
    if anexo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Anexo não encontrado")

    # SEV-14 (mesma defesa de ocorrencias.py::baixar_anexo) — caminho
    # absoluto conferido contra UPLOADS_BASE antes de servir.
    caminho_disco = resolver_caminho_seguro(UPLOADS_BASE, anexo.caminho)
    if not caminho_disco.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado no servidor")

    return FileResponse(
        caminho_disco, media_type=anexo.mime_type or "application/octet-stream",
        filename=anexo.nome_original or caminho_disco.name,
    )


# ============================================================================
# Converter em Ocorrencia definitiva
# ============================================================================

def _copiar_anexos_para_ocorrencia(db: Session, pre_oc: PreOcorrencia, ocorrencia: Ocorrencia, usuario_id: UUID) -> None:
    """Item 3.4 (19/08/2026) — a CNH e o documento do veículo que o
    motorista mandou seguem pra ocorrência definitiva na conversão.
    COPIA o arquivo (nunca move nem aponta pro mesmo caminho): a
    pré-ocorrência tem ciclo de vida próprio e um dia entra em política
    de retenção — perder o anexo junto seria perder prova da ocorrência.

    tipo="OUTRO" porque o CHECK da migration 012 em ocorrencia_anexo só
    aceita ('FOTO_ACIDENTE','FOTO_RELATORIO','CROQUI','BO_PDF','OUTRO') —
    "CNH" não é valor válido ali e o CHECK não muda por isto.

    Falha ao copiar (arquivo sumiu do disco, permissão) não pode
    derrubar a conversão — mesmo padrão do except em
    apagar_autorizacao(): loga e segue, a conversão é o passo caro."""
    anexos = db.execute(
        select(PreOcorrenciaAnexo).where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc.id)
    ).scalars().all()

    destino_dir = UPLOADS_BASE / "ocorrencias" / str(ocorrencia.id)
    for ordem, anexo in enumerate(anexos, start=1):
        try:
            origem = resolver_caminho_seguro(UPLOADS_BASE, anexo.caminho)
            destino_dir.mkdir(parents=True, exist_ok=True)
            extensao = Path(anexo.nome_original or "").suffix
            nome_arquivo = f"{uuid4()}{extensao}"
            shutil.copy2(origem, destino_dir / nome_arquivo)

            if anexo.tipo == "CNH":
                descricao = "CNH enviada pelo motorista na pré-ocorrência"
            elif anexo.tipo == "DOC_VEICULO":
                descricao = "Documento do coletivo enviado pelo motorista na pré-ocorrência"
            else:
                descricao = "Anexo enviado pelo motorista na pré-ocorrência"

            db.add(OcorrenciaAnexo(
                ocorrencia_id=ocorrencia.id,
                tipo="OUTRO",
                caminho=f"ocorrencias/{ocorrencia.id}/{nome_arquivo}",
                nome_original=anexo.nome_original,
                mime_type=anexo.mime_type,
                tamanho_bytes=anexo.tamanho_bytes,
                descricao=descricao,
                ordem=ordem,
                enviado_por=usuario_id,
            ))
        except Exception:
            logger.warning(
                "Não foi possível copiar o anexo %s da pré-ocorrência %s para a ocorrência %s",
                anexo.id, pre_oc.id, ocorrencia.id,
            )


@router.post("/{pre_ocorrencia_id}/converter", response_model=ConverterResponse)
def converter(
    pre_ocorrencia_id: UUID,
    payload: ConverterRequest,
    usuario: EscritaPreOcorrencia,
    db: Annotated[Session, Depends(get_db)],
):
    pre_oc = db.get(PreOcorrencia, pre_ocorrencia_id)
    if pre_oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pré-ocorrência não encontrada")
    autorizacao = db.get(PreOcorrenciaAutorizacao, pre_oc.autorizacao_id)

    # 🔴 Mesma trava de _exige_pode_ver — CCO tem pode_escrever=TRUE no
    # recurso, mas NUNCA é o destinatário (autorizacao.coordenador_id),
    # então cai aqui mesmo tendo passado pelo exige() de escrita. É a
    # restrição "no endpoint, não só na permissão" que o prompt pede.
    if autorizacao.coordenador_id != usuario.id and not _eh_admin(db, usuario.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Só o coordenador destinatário converte esta pré-ocorrência")

    if pre_oc.ocorrencia_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Esta pré-ocorrência já foi convertida")

    tipo_id = payload.tipo_ocorrencia_id
    if tipo_id is None:
        outros = db.execute(select(TipoOcorrencia).where(TipoOcorrencia.codigo == "OUTROS")).scalar_one_or_none()
        if outros is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Catálogo 'Outros' não encontrado")
        tipo_id = outros.id
    elif db.get(TipoOcorrencia, tipo_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tipo de ocorrência não encontrado")

    partes_relato = [f"[Pré-ocorrência do motorista]\n{pre_oc.relato}"]
    if pre_oc.terceiro_nome or pre_oc.terceiro_placa:
        partes_relato.append(
            f"Terceiro informado pelo motorista: {pre_oc.terceiro_nome or '—'} "
            f"({pre_oc.terceiro_placa or 'placa não informada'})"
        )

    # 🔴 registrado_por = o COORDENADOR que converteu, nunca o motorista —
    # é o que decide quem edita, apaga e VÊ a ocorrência depois. Ver
    # _handoff-claude/DESENHO-pre-ocorrencia.md e §2.4 do prompt.
    nova = Ocorrencia(
        tipo_ocorrencia_id=tipo_id,
        status="RASCUNHO",
        # data_ocorrencia é data de parede que a pessoa lê/digita — fuso de
        # operação, não UTC (Item A do lote 2: uma pré-ocorrência sem data
        # preenchida perto da meia-noite gravava o dia seguinte).
        data_ocorrencia=pre_oc.data_ocorrencia or datetime.now(FUSO_OPERACAO).date(),
        hora_ocorrencia=pre_oc.hora_ocorrencia,
        prefixo=pre_oc.prefixo or "0000",
        condutor_re=pre_oc.motorista_re,
        condutor_nome=pre_oc.motorista_nome,
        condutor_cnh=pre_oc.motorista_cnh,
        condutor_rg=pre_oc.motorista_rg,
        condutor_cpf=pre_oc.motorista_cpf,
        cobrador_re=pre_oc.cobrador_re,
        cobrador_nome=pre_oc.cobrador_nome,
        local_ocorrido=pre_oc.local_logradouro,
        numero_local=pre_oc.local_numero,
        bairro=pre_oc.local_bairro,
        cidade=pre_oc.local_cidade or "São Paulo",
        descricao_motorista="\n\n".join(partes_relato),
        via_urbana=False, via_rodoviaria=False, area_interna=False, corredor=False,
        tem_fotos=False, monitoramento=False, ocorrencia_policial=False, houve_policia_tecnica=False,
        registrado_por=usuario.id,
    )
    db.add(nova)
    db.flush()

    _copiar_anexos_para_ocorrencia(db, pre_oc, nova, usuario.id)

    pre_oc.status = "CONVERTIDA"
    pre_oc.convertida_em = datetime.now(timezone.utc)
    pre_oc.ocorrencia_id = nova.id
    db.commit()
    db.refresh(nova)

    return ConverterResponse(ocorrencia_id=nova.id, ocorrencia_numero=nova.numero)
