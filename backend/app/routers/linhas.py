"""CRUD do cadastro ÚNICO de linhas (E2 e AR2) — migration 048.

public.linha é a tabela que o Pátio, a Escala de Fiscais e a Fiscalização
leem e apontam. Estas são as ÚNICAS rotas que gravam nela (R0): a tela
Cadastros → Linhas e a sub-aba Linhas da Escala de Fiscais usam as mesmas.
Gravar é só ADMIN (R4); ler, cada módulo pela própria porta
(GET /linhas, /escala-fiscais/linhas, /fiscalizacao/linhas).

Quem grava manda número + sufixo; o backend monta `codigo` (271A-51).
271A-10 e 271A-51 são linhas DIFERENTES (R1).
"""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.core.linha import codigo_canonico, normalizar_linha
from app.core.utils import PaginationParams
from app.models import Escala, Linha, SetorEnum
from app.models.escala_fiscais import EscalaFiscalPostoLinha
from app.schemas import LinhaCreate, LinhaRead, LinhaUpdate

router = APIRouter(prefix="/linhas", tags=["linhas"])


@router.get("", response_model=list[LinhaRead])
def listar(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pag: Annotated[PaginationParams, Depends()],
    setor: Optional[SetorEnum] = None,
    ativa: Optional[bool] = None,
):
    q = select(Linha)
    if setor:
        q = q.where(Linha.setor == setor)
    if ativa is not None:
        q = q.where(Linha.ativa == ativa)
    q = q.order_by(Linha.codigo).offset(pag.skip).limit(pag.limit)
    return db.execute(q).scalars().all()


@router.get("/{linha_id}", response_model=LinhaRead)
def buscar(linha_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    return linha


def _numero_sufixo(numero: str, sufixo: str) -> tuple[str, str]:
    """Mesma regra única (R2) de toda a aplicação — 422 se não bate."""
    par = normalizar_linha(f"{numero}-{sufixo}")
    if par is None:
        raise HTTPException(
            status_code=422,
            detail=f"Linha {numero}-{sufixo} fora do formato: número com 4 caracteres (com ao menos "
                   "um dígito) e código só com dígitos.",
        )
    return par


def _checar_repetida(db: Session, numero: str, sufixo: str, ignorar: Optional[UUID] = None) -> None:
    existe = db.execute(
        select(Linha).where(Linha.numero == numero, Linha.sufixo == sufixo)
    ).scalar_one_or_none()
    if existe is not None and existe.id != ignorar:
        raise HTTPException(status_code=409, detail=f"Linha {codigo_canonico(numero, sufixo)} já cadastrada")


def _ligar_postos_da_escala(db: Session, linha: Linha) -> None:
    """Posto da Escala de Fiscais que tinha esta linha só em texto (linha sem
    cadastro — importação ou 048) passa a apontar para ela."""
    db.execute(
        update(EscalaFiscalPostoLinha)
        .where(EscalaFiscalPostoLinha.linha == linha.codigo, EscalaFiscalPostoLinha.linha_id.is_(None))
        .values(linha_id=linha.id)
    )


@router.post("", response_model=LinhaRead, status_code=status.HTTP_201_CREATED)
def criar(payload: LinhaCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    numero, sufixo = _numero_sufixo(payload.numero, payload.sufixo)
    _checar_repetida(db, numero, sufixo)
    codigo = codigo_canonico(numero, sufixo)
    if db.execute(select(Linha).where(Linha.codigo == codigo)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Linha {codigo} já cadastrada")
    linha = Linha(
        codigo=codigo, numero=numero, sufixo=sufixo,
        nome=payload.nome or codigo,  # sem nome: o próprio código (R5)
        setor=payload.setor, ativa=payload.ativa,
    )
    db.add(linha)
    db.flush()
    _ligar_postos_da_escala(db, linha)
    db.commit()
    db.refresh(linha)
    return linha


@router.patch("/{linha_id}", response_model=LinhaRead)
def atualizar(
    linha_id: UUID, payload: LinhaUpdate, user: AdminUser, db: Annotated[Session, Depends(get_db)]
):
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    dados = payload.model_dump(exclude_unset=True)
    numero, sufixo = dados.pop("numero", None), dados.pop("sufixo", None)
    if numero is not None or sufixo is not None:
        if linha.numero is None and (numero is None or sufixo is None):
            raise HTTPException(status_code=422, detail="Informe número e código juntos.")
        numero, sufixo = _numero_sufixo(numero or linha.numero, sufixo or linha.sufixo)
        _checar_repetida(db, numero, sufixo, ignorar=linha.id)
        codigo = codigo_canonico(numero, sufixo)
        if codigo != linha.codigo:
            if db.execute(select(Linha).where(Linha.codigo == codigo)).scalar_one_or_none():
                raise HTTPException(status_code=409, detail=f"Linha {codigo} já cadastrada")
            if linha.nome == linha.codigo:
                linha.nome = codigo
            linha.codigo = codigo
        linha.numero, linha.sufixo = numero, sufixo
    if "nome" in dados:
        # Vazio = sem nome: volta a ser o próprio código (coluna NOT NULL).
        dados["nome"] = dados["nome"] or linha.codigo
    for k, v in dados.items():
        if v is not None:
            setattr(linha, k, v)
    db.flush()
    _ligar_postos_da_escala(db, linha)
    db.commit()
    db.refresh(linha)
    return linha


@router.delete("/{linha_id}", status_code=200, summary="Remove linha do catálogo")
def deletar(linha_id: UUID, user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    """Remove a linha. Falha se ainda houver escalas ativas usando-a."""
    from app.models import Escala
    linha = db.get(Linha, linha_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Linha não encontrada")
    em_uso = db.execute(
        select(Escala).where(Escala.linha_id == linha_id, Escala.deletado_em.is_(None)).limit(1)
    ).scalar_one_or_none()
    if em_uso:
        raise HTTPException(
            status_code=409,
            detail=f"Linha {linha.codigo} está em uso na escala ativa — remova ou troque antes de excluir"
        )
    em_posto = db.execute(
        select(EscalaFiscalPostoLinha.posto_id).where(EscalaFiscalPostoLinha.linha_id == linha_id).limit(1)
    ).first()
    if em_posto:
        raise HTTPException(
            status_code=409,
            detail=f"Linha {linha.codigo} está num posto da Escala de Fiscais — desative em vez de excluir"
        )
    codigo = linha.codigo
    db.delete(linha)
    db.commit()
    return {"ok": True, "codigo": codigo}
