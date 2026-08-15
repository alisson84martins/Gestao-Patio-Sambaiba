"""Endpoint de upload de planilha Excel de escala."""
from datetime import date as date_type, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige
from app.core.uploads import ler_upload_limitado
from app.core.utils import PaginationParams
from app.models import Escala, ImportacaoEscala, TipoEscalaEnum, Usuario
from app.models.cadastro import Funcionario
from app.schemas import ImportacaoEscalaRead
from app.schemas.importacao import ErroLinha, ImportacaoUploadResponse
from app.services.importacao_excel import importar_escala

router = APIRouter(prefix="/importacoes", tags=["importação Excel"])

LeituraEscala = Annotated[Funcionario, Depends(exige("escala"))]
EscritaEscala = Annotated[Funcionario, Depends(exige("escala", escrever=True))]

ALLOWED_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls (alguns navegadores)
}
TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB


def _resolver_usuario_legado(db: Session, funcionario: Funcionario) -> Optional[UUID]:
    """ImportacaoEscala.importado_por e Alerta.registrado_por (ambos
    escritos por importar_escala()) ainda apontam pra usuario.id — schema
    legado, não migrado. funcionario.id e usuario.id são UUIDs DIFERENTES
    mesmo pra mesma pessoa: o espelho em `usuario` (criado por
    _criar_ou_atualizar_espelho_usuario em funcionarios.py, ver
    app/core/deps.py::get_current_user) tem id próprio, ligado ao
    Funcionario só pelo RE. Passar funcionario.id direto nesses campos
    quebraria a FK em produção (Postgres); resolve pelo RE, mesmo padrão
    já usado em get_current_user(). None é aceitável — as duas colunas são
    nullable com ON DELETE SET NULL."""
    espelho = db.execute(select(Usuario).where(Usuario.re == funcionario.re)).scalar_one_or_none()
    return espelho.id if espelho else None


@router.post(
    "/escala",
    response_model=ImportacaoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload de planilha Excel de escala",
)
async def importar(
    usuario: EscritaEscala,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    arquivo: Annotated[UploadFile, File(description=".xlsx (formato: numero_frota | linha_codigo | horario | re_motorista | tipo)")],
    data_escala: Annotated[date_type, Form(description="Data da escala (YYYY-MM-DD)")],
    tipo_default: Annotated[
        TipoEscalaEnum, Form(description="Tipo padrão se não vier na planilha")
    ] = TipoEscalaEnum.MANOBRA,
    substituir_existentes: Annotated[bool, Form()] = True,
):
    """Faz upload de uma planilha Excel e cria registros em escala.

    A planilha deve ter cabeçalho na linha 1 e dados a partir da linha 2:
    - **Coluna A**: numero_frota (4 dígitos)
    - **Coluna B**: linha_codigo (ex: 8500-10)
    - **Coluna C**: horario_saida (HH:MM)
    - **Coluna D**: re_motorista (opcional)
    - **Coluna E**: tipo (opcional: MANOBRA / PLANTAO_E2 / PLANTAO_AR2)

    Se `substituir_existentes` for true, todas as escalas dessa data viram soft delete.
    """
    if arquivo.content_type and arquivo.content_type not in ALLOWED_TYPES and not arquivo.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(415, f"Formato não suportado: {arquivo.content_type}. Envie um .xlsx")

    # SEV-12: lê em blocos, abortando ao estourar — nunca a planilha
    # inteira em memória antes de saber se ela cabe no limite.
    conteudo = await ler_upload_limitado(arquivo, TAMANHO_MAXIMO, request)

    importado_por_id = _resolver_usuario_legado(db, usuario)

    try:
        imp, erros, substituidas, presos_criados = importar_escala(
            db=db,
            arquivo_nome=arquivo.filename or "sem_nome.xlsx",
            conteudo=conteudo,
            data_escala=data_escala,
            tipo_default=tipo_default,
            importado_por_id=importado_por_id,
            substituir_existentes=substituir_existentes,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Erro ao processar planilha: {exc}")

    return ImportacaoUploadResponse(
        importacao=ImportacaoEscalaRead.model_validate(imp),
        total_lidos=imp.total_registros,
        total_inseridos=imp.registros_sucesso,
        total_erros=imp.registros_erro,
        erros=[ErroLinha(**e) for e in erros],
        substituidas=substituidas,
        presos_criados=presos_criados,
    )


@router.get("", response_model=list[ImportacaoEscalaRead])
def listar(
    _: LeituraEscala,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    data: Annotated[Optional[date_type], Query()] = None,
):
    q = select(ImportacaoEscala)
    if data:
        q = q.where(ImportacaoEscala.data_escala == data)
    q = q.order_by(ImportacaoEscala.importado_em.desc()).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{imp_id}", response_model=ImportacaoEscalaRead)
def buscar(imp_id: UUID, _: LeituraEscala, db: Annotated[Session, Depends(get_db)]):
    imp = db.get(ImportacaoEscala, imp_id)
    if not imp:
        raise HTTPException(404, "Importacao nao encontrada")
    return imp


@router.post(
    "/{imp_id}/reverter",
    response_model=dict,
    summary="Reverte uma importacao (soft delete das escalas geradas)",
)
def reverter(imp_id: UUID, _: EscritaEscala, db: Annotated[Session, Depends(get_db)]):
    imp = db.get(ImportacaoEscala, imp_id)
    if not imp:
        raise HTTPException(404, "Importação não encontrada")
    stmt = (
        update(Escala)
        .where(Escala.importacao_id == imp_id, Escala.deletado_em.is_(None))
        .values(deletado_em=datetime.now(timezone.utc))
    )
    result = db.execute(stmt)
    db.commit()
    return {
        "importacao_id": str(imp_id),
        "escalas_revertidas": result.rowcount,
    }
