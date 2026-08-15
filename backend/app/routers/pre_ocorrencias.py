"""Endpoints AUTENTICADOS da pré-ocorrência — abrir, acompanhar, converter.

O endpoint que o motorista usa (sem login) está em
pre_ocorrencias_publico.py — nunca neste arquivo. Ver
_handoff-claude/DESENHO-pre-ocorrencia.md.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.ocorrencia import Ocorrencia, TipoOcorrencia
from app.models.pre_ocorrencia import PreOcorrencia, PreOcorrenciaAnexo, PreOcorrenciaAutorizacao
from app.schemas.pre_ocorrencia import (
    AbrirAutorizacaoRequest, AutorizacaoAbertaResponse, AutorizacaoResumo, AutorizacaoResumoCCO,
    ConverterRequest, ConverterResponse, PreOcorrenciaDetalhe, PreOcorrenciaFilaItem,
)
from app.services.n8n import notificar_autorizacao_aberta
from app.services.pre_ocorrencia_token import EXPIRACAO_HORAS, gerar_token

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
    """Mesmo conjunto de funções de _ve_todas_ocorrencias() em
    ocorrencias.py (ADMIN, ENCARREGADO, GERENTE_GERAL,
    GERENTE_OPERACIONAL) — duplicado aqui de propósito: os dois recursos
    (`ocorrencia` e `pre_ocorrencia`) são independentes no RBAC, e uma
    função helper compartilhada exigiria um módulo novo só pra isso."""
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo "
            "AND f.codigo IN ('ADMIN','ENCARREGADO','GERENTE_GERAL','GERENTE_OPERACIONAL')"
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
    if autorizacao.usada_em is not None:
        return f"enviada, com o coordenador {coordenador_nome}"
    if _como_utc(autorizacao.expira_em) < datetime.now(timezone.utc):
        return "expirada, sem resposta do motorista"
    return "aguardando o motorista"


@router.get("/autorizacoes", summary="O que a pessoa abriu e está aguardando")
def minhas_autorizacoes(usuario: EscritaPreOcorrencia, db: Annotated[Session, Depends(get_db)]):
    linhas = db.execute(
        select(PreOcorrenciaAutorizacao)
        .where(PreOcorrenciaAutorizacao.aberta_por == usuario.id)
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
    q = (
        select(PreOcorrencia, PreOcorrenciaAutorizacao)
        .join(PreOcorrenciaAutorizacao, PreOcorrenciaAutorizacao.id == PreOcorrencia.autorizacao_id)
        .order_by(PreOcorrencia.criado_em.desc())
    )
    if not _ve_todas_pre_ocorrencias(db, usuario.id):
        q = q.where(PreOcorrenciaAutorizacao.coordenador_id == usuario.id)

    linhas = db.execute(q).all()
    return [
        PreOcorrenciaFilaItem(
            id=pre_oc.id,
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
# Converter em Ocorrencia definitiva
# ============================================================================

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

    pre_oc.status = "CONVERTIDA"
    pre_oc.convertida_em = datetime.now(timezone.utc)
    pre_oc.ocorrencia_id = nova.id
    db.commit()
    db.refresh(nova)

    return ConverterResponse(ocorrencia_id=nova.id, ocorrencia_numero=nova.numero)
