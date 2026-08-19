"""Endpoints PÚBLICOS da pré-ocorrência — o link do motorista.

⛔ SEM AUTENTICAÇÃO. Autorizados só pelo token (cabeçalho
X-Pre-Ocorrencia-Token — ver _handoff-claude/DESENHO-pre-ocorrencia.md,
item 6, sobre por que não é {token} no path). É a parte mais sensível do
sistema inteiro — nenhum import de `exige`/`Current*` deve aparecer neste
arquivo; se aparecer, algo saiu errado.

Regras inegociáveis (PROMPT-pre-ocorrencia-motorista.md §2.3), cada uma
com o comentário apontando pra onde ela é aplicada:
1. Comparação do token em tempo constante — token_confere()
2. Token inválido/expirado/usado → MESMA resposta — _carregar_autorizacao()
3. Nunca dado de outra pré-ocorrência — o token amarra 1:1 a uma linha
4. Rate limit por token e por IP — checar_rate_limit_publico()
5. Nenhuma leitura de Ocorrencia definitiva — este arquivo nunca importa
   o modelo Ocorrencia
6. Token nunca no path/log — cabeçalho, nunca querystring
7. enviar() marca usada_em; GET segue liberado até expirar (protocolo
   consultável), PATCH/anexos passam a rejeitar
"""
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.uploads import ler_upload_limitado, validar_assinatura
from app.models.pre_ocorrencia import PreOcorrencia, PreOcorrenciaAnexo, PreOcorrenciaAutorizacao
from app.schemas.pre_ocorrencia import (
    EnviarResponse, PreOcorrenciaAnexoPublicoRead, PreOcorrenciaPublicoRead,
    PreOcorrenciaPublicoUpdate,
)
from app.services.pre_ocorrencia_token import (
    RateLimitExcedido, checar_rate_limit_publico, hash_token, registrar_falha_rate_limit_ip, token_confere,
)
from app.services.uploads_pre_ocorrencia import PRE_OC_UPLOAD_ROOT, salvar_anexo_pre_ocorrencia

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pre-ocorrencias/publico", tags=["pré-ocorrência (público)"])

ANEXO_MIME_PERMITIDOS = {"image/jpeg", "image/png", "application/pdf"}
ANEXO_TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB
MAX_ANEXOS_POR_PRE_OCORRENCIA = 5

_RESPOSTA_GENERICA = "Link inválido ou expirado."

# Item 2: hora_ocorrencia é hora de parede que uma pessoa lê e digita, não
# um instante — por isso é uma exceção à regra de gravar tudo em UTC neste
# arquivo (enviada_em/convertida_em/expira_em/atualizado_em continuam UTC, e
# é assim que deve continuar). FUSO_OPERACAO agora vem de app.core.config —
# pre_ocorrencias.py também precisa dela (data_ocorrencia na conversão).


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _como_utc(dt: datetime) -> datetime:
    """Postgres (TIMESTAMPTZ, produção) sempre devolve datetime aware via
    psycopg3. SQLite (usado nos testes) não tem tipo timezone-aware nativo
    e pode devolver naive — trata como UTC nesse caso, pra comparação
    nunca estourar TypeError independente do banco por trás."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _carregar_autorizacao(
    db: Session, token: str, ip: Optional[str], *, exigir_nao_usada: bool
) -> PreOcorrenciaAutorizacao:
    """🔴 Regras 1, 2 e 4. Levanta HTTPException genérica e idêntica pra
    token inexistente, expirado e (quando exigir_nao_usada) já usado —
    nunca diferencia o motivo na resposta."""
    token_h = hash_token(token)

    try:
        checar_rate_limit_publico(ip, token_h)
    except RateLimitExcedido:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas. Aguarde um pouco.")

    generica = HTTPException(status.HTTP_404_NOT_FOUND, detail=_RESPOSTA_GENERICA)

    linha = db.execute(
        select(PreOcorrenciaAutorizacao).where(PreOcorrenciaAutorizacao.token_hash == token_h)
    ).scalar_one_or_none()
    if linha is None:
        registrar_falha_rate_limit_ip(ip)
        raise generica
    # Regra 1 — mesmo já tendo casado pelo índice único de token_hash,
    # a comparação final é explícita e em tempo constante.
    if not token_confere(token, linha.token_hash):
        registrar_falha_rate_limit_ip(ip)
        raise generica
    if _como_utc(linha.expira_em) < datetime.now(timezone.utc):
        registrar_falha_rate_limit_ip(ip)
        raise generica
    # Item 1 (15/08/2026): autorização cancelada morre na hora, para TODAS
    # as 4 rotas públicas — inclusive GET, diferente de usada_em (regra 7),
    # que continua liberado depois de enviado pra mostrar o protocolo.
    # Cancelar não é "concluir", é revogar — token continuar aceito depois
    # de cancelado é buraco de segurança, não detalhe de tela.
    if linha.status == "CANCELADA":
        registrar_falha_rate_limit_ip(ip)
        raise generica
    if exigir_nao_usada and linha.usada_em is not None:
        registrar_falha_rate_limit_ip(ip)
        raise generica
    return linha


def _carregar_ou_criar_pre_ocorrencia(db: Session, autorizacao: PreOcorrenciaAutorizacao) -> PreOcorrencia:
    pre_oc = db.execute(
        select(PreOcorrencia).where(PreOcorrencia.autorizacao_id == autorizacao.id)
    ).scalar_one_or_none()
    if pre_oc is None:
        # hora_ocorrencia é NOT NULL no banco — hora atual como ponto de
        # partida editável; o motorista corrige no formulário (nunca
        # bloqueia o autosave inicial por falta desse campo). No fuso de
        # operação, não em UTC — é hora de parede, não instante (ver
        # FUSO_OPERACAO acima).
        pre_oc = PreOcorrencia(
            autorizacao_id=autorizacao.id,
            hora_ocorrencia=datetime.now(FUSO_OPERACAO).time(),
            relato="",
            status="AGUARDANDO",
        )
        db.add(pre_oc)
        db.commit()
        db.refresh(pre_oc)
    return pre_oc


# ============================================================================
# GET — formulário e o que já foi preenchido
# ============================================================================

@router.get("", response_model=PreOcorrenciaPublicoRead)
def obter(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_pre_ocorrencia_token: Annotated[str, Header(min_length=1)],
):
    # Regra 7: GET fica liberado mesmo depois de "enviar" (usada_em
    # setado) — é o que permite mostrar o protocolo/confirmação ao
    # reabrir o link, e não muda a superfície de risco: GET nunca grava.
    autorizacao = _carregar_autorizacao(db, x_pre_ocorrencia_token, _ip(request), exigir_nao_usada=False)
    pre_oc = _carregar_ou_criar_pre_ocorrencia(db, autorizacao)

    # Item 3.1 (19/08/2026): PreOcorrencia não tem relationship `anexos`
    # (de propósito — cascade/lazy do lado público é risco desnecessário),
    # então o Pydantic sozinho sempre caía no default `[]`. Consulta
    # explícita, mesmo padrão de detalhar() no router autenticado — sem
    # isso o motorista que reabre o link nunca via que já tinha enviado a
    # CNH e mandava de novo.
    anexos = db.execute(
        select(PreOcorrenciaAnexo).where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc.id)
    ).scalars().all()
    resultado = PreOcorrenciaPublicoRead.model_validate(pre_oc)
    resultado.anexos = anexos
    return resultado


# ============================================================================
# PATCH — autosave
# ============================================================================

@router.patch("", response_model=PreOcorrenciaPublicoRead)
def atualizar(
    payload: PreOcorrenciaPublicoUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_pre_ocorrencia_token: Annotated[str, Header(min_length=1)],
):
    autorizacao = _carregar_autorizacao(db, x_pre_ocorrencia_token, _ip(request), exigir_nao_usada=True)
    pre_oc = _carregar_ou_criar_pre_ocorrencia(db, autorizacao)

    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(pre_oc, campo, valor)
    pre_oc.atualizado_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pre_oc)
    return pre_oc


# ============================================================================
# POST anexos — CNH e documento do veículo
# ============================================================================

@router.post(
    "/anexos",
    response_model=PreOcorrenciaAnexoPublicoRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_anexo(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_pre_ocorrencia_token: Annotated[str, Header(min_length=1)],
    arquivo: Annotated[UploadFile, File(description="image/jpeg, image/png ou application/pdf — máx 10 MB")],
    tipo: Annotated[str, Form(description="CNH | DOC_VEICULO | OUTRO")],
):
    autorizacao = _carregar_autorizacao(db, x_pre_ocorrencia_token, _ip(request), exigir_nao_usada=True)
    pre_oc = _carregar_ou_criar_pre_ocorrencia(db, autorizacao)

    if tipo not in ("CNH", "DOC_VEICULO", "OUTRO"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Tipo de anexo inválido: {tipo}")

    total_atual = db.execute(
        select(func.count()).select_from(PreOcorrenciaAnexo)
        .where(PreOcorrenciaAnexo.pre_ocorrencia_id == pre_oc.id)
    ).scalar()
    if total_atual >= MAX_ANEXOS_POR_PRE_OCORRENCIA:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Máximo de {MAX_ANEXOS_POR_PRE_OCORRENCIA} arquivos por pré-ocorrência.",
        )

    if arquivo.content_type not in ANEXO_MIME_PERMITIDOS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato não suportado: {arquivo.content_type}. Envie JPEG, PNG ou PDF.",
        )

    # SEV-12/13 (mesma correção do Bloco B desta sessão — não replicadas
    # de propósito, é o mesmo módulo, ver app/core/uploads.py).
    conteudo = await ler_upload_limitado(arquivo, ANEXO_TAMANHO_MAXIMO, request)
    if not validar_assinatura(conteudo, arquivo.content_type):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="O conteúdo do arquivo não corresponde a um JPEG, PNG ou PDF válido.",
        )

    anexo = salvar_anexo_pre_ocorrencia(db, pre_oc.id, tipo, arquivo.filename, arquivo.content_type, conteudo)
    return anexo


# ============================================================================
# POST enviar — finaliza, devolve protocolo
# ============================================================================

@router.post("/enviar", response_model=EnviarResponse)
def enviar(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_pre_ocorrencia_token: Annotated[str, Header(min_length=1)],
):
    autorizacao = _carregar_autorizacao(db, x_pre_ocorrencia_token, _ip(request), exigir_nao_usada=True)
    pre_oc = _carregar_ou_criar_pre_ocorrencia(db, autorizacao)

    # Único ponto de validação "obrigatória" do fluxo público — mesmo
    # assim, nunca bloqueia por falta de ANEXO (decisão explícita do
    # prompt: documento é obrigatório no processo, nunca no formulário).
    if not pre_oc.relato or not pre_oc.relato.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Conte o que aconteceu antes de enviar.")

    agora = datetime.now(timezone.utc)
    pre_oc.status = "ENVIADA"
    pre_oc.enviada_em = agora
    autorizacao.usada_em = agora
    autorizacao.status = "ENVIADA"
    db.commit()

    protocolo = f"PO-{str(pre_oc.id)[:8].upper()}"
    return EnviarResponse(protocolo=protocolo)
