"""Endpoints do pré-cadastro de pessoas (Bloco H).

🔑 A ideia (Alisson, 21/08/2026): todo RE capturado em qualquer módulo
alimenta um cadastro preliminar (app/services/pre_cadastro.py) — a portaria
contribui pouco (só o RE), a pré-ocorrência contribui muito (nome, CPF,
RG, CNH, telefone). Quem alimenta a fila (`pre_cadastro` escrever, via o
serviço) e quem lê o acumulado são grupos deliberadamente diferentes —
🔴 CONTROLADOR_ACESSO nunca tem este recurso: a portaria escreve através
do serviço, mas nunca lê a fila.

🔴 /promover exige `usuarios` escrever, NÃO `pre_cadastro` — criar pessoa
no cadastro central é ato de RH/administração, o mesmo recurso que já
governa isso hoje. Promover NUNCA cria login (usuario_login) — devolve só
o funcionario_id, e o acesso ao sistema segue o caminho normal de
cadastro.

⚠️ LGPD: esta é a peça mais sensível do sistema até aqui — ver
COMMENT ON TABLE da migration 028 e o cabeçalho de
services/pre_cadastro.py para a finalidade declarada e a política de
retenção. ⛔ Nenhum endpoint de exportação (CSV ou outro) nesta fase.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.pre_cadastro import PessoaPreCadastro
from app.schemas.pre_cadastro import (
    PreCadastroDescartarRequest, PreCadastroPromoverResponse, PreCadastroRead,
    PreCadastroUpdate, StatusPreCadastro,
)

router = APIRouter(prefix="/pre-cadastros", tags=["pré-cadastro"])

LeituraPreCadastro = Annotated[Funcionario, Depends(exige("pre_cadastro"))]
EscritaPreCadastro = Annotated[Funcionario, Depends(exige("pre_cadastro", escrever=True))]
# Promover é ato de cadastro central — mesmo recurso que já governa
# criação/edição de funcionário/usuário hoje, não um recurso novo.
EscritaUsuarios = Annotated[Funcionario, Depends(exige("usuarios", escrever=True))]


def _get(db: Session, pre_cadastro_id: UUID) -> PessoaPreCadastro:
    registro = db.get(PessoaPreCadastro, pre_cadastro_id)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pré-cadastro não encontrado")
    return registro


@router.get("", response_model=list[PreCadastroRead])
def listar_pre_cadastros(
    usuario: LeituraPreCadastro,
    db: Annotated[Session, Depends(get_db)],
    status_filtro: Optional[StatusPreCadastro] = Query(None, alias="status"),
):
    stmt = select(PessoaPreCadastro)
    if status_filtro:
        stmt = stmt.where(PessoaPreCadastro.status == status_filtro)
    stmt = stmt.order_by(PessoaPreCadastro.ultima_vez_em.desc())
    return db.execute(stmt).scalars().all()


@router.patch("/{pre_cadastro_id}", response_model=PreCadastroRead)
def corrigir_pre_cadastro(
    pre_cadastro_id: UUID,
    payload: PreCadastroUpdate,
    usuario: EscritaPreCadastro,
    db: Annotated[Session, Depends(get_db)],
):
    registro = _get(db, pre_cadastro_id)
    dados = payload.model_dump(exclude_unset=True)
    for campo, valor in dados.items():
        setattr(registro, campo, valor)
    db.commit()
    db.refresh(registro)
    return registro


@router.post("/{pre_cadastro_id}/promover", response_model=PreCadastroPromoverResponse)
def promover_pre_cadastro(
    pre_cadastro_id: UUID,
    usuario: EscritaUsuarios,
    db: Annotated[Session, Depends(get_db)],
):
    registro = _get(db, pre_cadastro_id)
    if registro.status != "PENDENTE":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Pré-cadastro já está {registro.status}"
        )
    if not (registro.nome or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pré-cadastro sem nome — complete com PATCH antes de promover.",
        )

    novo_funcionario = Funcionario(
        re=registro.re, nome=registro.nome, cpf=registro.cpf,
        rg=registro.rg, cnh=registro.cnh, telefone=registro.telefone,
    )
    db.add(novo_funcionario)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Já existe funcionário com este RE ou CPF — não é possível promover.",
        ) from exc

    registro.status = "PROMOVIDO"
    registro.funcionario_id = novo_funcionario.id
    registro.promovido_por = usuario.id
    registro.promovido_em = datetime.now(timezone.utc)

    db.commit()
    return PreCadastroPromoverResponse(funcionario_id=novo_funcionario.id)


@router.post("/{pre_cadastro_id}/descartar", response_model=PreCadastroRead)
def descartar_pre_cadastro(
    pre_cadastro_id: UUID,
    payload: PreCadastroDescartarRequest,
    usuario: EscritaPreCadastro,
    db: Annotated[Session, Depends(get_db)],
):
    registro = _get(db, pre_cadastro_id)
    if registro.status != "PENDENTE":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Pré-cadastro já está {registro.status}"
        )

    registro.status = "DESCARTADO"
    registro.descartado_por = usuario.id
    registro.descartado_em = datetime.now(timezone.utc)
    registro.descarte_motivo = payload.motivo

    db.commit()
    db.refresh(registro)
    return registro
