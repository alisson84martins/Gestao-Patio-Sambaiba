"""Endpoint de upload de planilha Excel de escala."""
from datetime import date as date_type, datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.utils import PaginationParams
from app.models import Escala, ImportacaoEscala, PerfilUsuarioEnum, TipoEscalaEnum
from app.schemas import ImportacaoEscalaRead
from app.schemas.importacao import ErroLinha, ImportacaoUploadResponse
from app.services.importacao_excel import importar_escala

router = APIRouter(prefix="/importacoes", tags=["importação Excel"])

ALLOWED_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls (alguns navegadores)
}


def _pode_importar(user) -> bool:
    return user.perfil in (PerfilUsuarioEnum.ADMIN, PerfilUsuarioEnum.COORDENADOR)


@router.post(
    "/escala",
    response_model=ImportacaoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload de planilha Excel de escala",
)
async def importar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
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
    if not _pode_importar(user):
        raise HTTPException(403, "Apenas ADMIN ou COORDENADOR pode importar escala")

    if arquivo.content_type and arquivo.content_type not in ALLOWED_TYPES and not arquivo.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(415, f"Formato não suportado: {arquivo.content_type}. Envie um .xlsx")

    conteudo = await arquivo.read()
    if len(conteudo) > 10 * 1024 * 1024:  # 10 MB
        raise HTTPException(413, "Arquivo muito grande (máx 10 MB)")

    try:
        imp, erros, substituidas = importar_escala(
            db=db,
            arquivo_nome=arquivo.filename or "sem_nome.xlsx",
            conteudo=conteudo,
            data_escala=data_escala,
            tipo_default=tipo_default,
            importado_por_id=user.id,
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
    )


@router.get("", response_model=list[ImportacaoEscalaRead])
def listar(
    user: CurrentUser,
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
def buscar(imp_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    imp = db.get(ImportacaoEscala, imp_id)
    if not imp:
        raise HTTPException(404, "Importação não encontrada")
    return imp


@router.post(
    "/{imp_id}/reverter",
    response_model=dict,
    summary="Reverte uma importação (soft delete das escalas geradas)",
)
def reverter(imp_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    if not _pode_importar(user):
        raise HTTPException(403, "Apenas ADMIN ou COORDENADOR pode reverter")
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
        "escalas_revertidas": result.rowcount or 0,
    }
