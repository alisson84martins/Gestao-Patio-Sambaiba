"""Endpoints do módulo Portaria — cadastro de veículos/empresas e
autorização (D6: dois atos distintos, dois recursos de RBAC).

🔴 A separação entre "cadastrar" e "autorizar" vive neste arquivo: o PATCH
de dados cadastrais (recurso `veiculo_portaria`) rejeita qualquer tentativa
de mudar `situacao` — o schema VeiculoUpdate nem tem esse campo, com
`extra="forbid"` pra virar 422 em vez de ser ignorado em silêncio. Só o
PATCH /situacao (recurso `autorizacao_veicular`) muda situação. Os dois
NUNCA compartilham código de update — se compartilhassem, a separação
evaporaria.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.portaria import EmpresaTerceira, VeiculoPortaria, VeiculoSituacaoHist
from app.schemas.portaria import (
    BloquearPorReRequest, BloquearPorReResponse, EmpresaTerceiraCreate,
    EmpresaTerceiraRead, Propriedade, SituacaoVeiculo, VeiculoCreate,
    VeiculoDivergenciaRead, VeiculoRead, VeiculoSituacaoHistRead,
    VeiculoSituacaoUpdate, VeiculoUpdate,
)

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraCadastro = Annotated[Funcionario, Depends(exige("veiculo_portaria"))]
EscritaCadastro = Annotated[Funcionario, Depends(exige("veiculo_portaria", escrever=True))]
LeituraAutorizacao = Annotated[Funcionario, Depends(exige("autorizacao_veicular"))]
EscritaAutorizacao = Annotated[Funcionario, Depends(exige("autorizacao_veicular", escrever=True))]


def _veiculo_read(veiculo: VeiculoPortaria, db: Session) -> VeiculoRead:
    """Monta VeiculoRead com os nomes resolvidos por join simples — a tela
    do controlador e a ficha do veículo mostram nome, não UUID cru."""
    extras: dict[str, Optional[str]] = {}
    if veiculo.funcionario_id:
        dono = db.get(Funcionario, veiculo.funcionario_id)
        if dono is not None:
            extras["funcionario_nome"] = dono.nome
            extras["funcionario_re"] = dono.re
    if veiculo.empresa_terceira_id:
        empresa = db.get(EmpresaTerceira, veiculo.empresa_terceira_id)
        if empresa is not None:
            extras["empresa_terceira_nome"] = empresa.nome
    if veiculo.criado_por:
        autor = db.get(Funcionario, veiculo.criado_por)
        if autor is not None:
            extras["criado_por_nome"] = autor.nome
    if veiculo.situacao_por:
        decisor = db.get(Funcionario, veiculo.situacao_por)
        if decisor is not None:
            extras["situacao_por_nome"] = decisor.nome
    return VeiculoRead.model_validate(veiculo).model_copy(update=extras)


# ============================================================================
# CADASTRO — exige("veiculo_portaria", ...)
# ============================================================================

@router.get("/veiculos", response_model=list[VeiculoRead], summary="Lista veículos cadastrados")
def listar_veiculos(
    usuario: LeituraCadastro,
    db: Annotated[Session, Depends(get_db)],
    propriedade: Optional[Propriedade] = None,
    situacao: Optional[SituacaoVeiculo] = None,
    apenas_ativos: bool = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = select(VeiculoPortaria)
    if apenas_ativos:
        stmt = stmt.where(VeiculoPortaria.ativo.is_(True))
    if propriedade:
        stmt = stmt.where(VeiculoPortaria.propriedade == propriedade)
    if situacao:
        stmt = stmt.where(VeiculoPortaria.situacao == situacao)
    stmt = stmt.order_by(VeiculoPortaria.placa).offset(skip).limit(limit)

    veiculos = db.execute(stmt).scalars().all()
    return [_veiculo_read(v, db) for v in veiculos]


@router.post(
    "/veiculos",
    response_model=VeiculoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra veículo — nasce PENDENTE, quem cadastra não autoriza (D6)",
)
def cadastrar_veiculo(payload: VeiculoCreate, usuario: EscritaCadastro, db: Annotated[Session, Depends(get_db)]):
    if payload.funcionario_id and db.get(Funcionario, payload.funcionario_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário (dono) não encontrado")
    if payload.empresa_terceira_id and db.get(EmpresaTerceira, payload.empresa_terceira_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Empresa terceira não encontrada")

    # D7: None = backend decide (TRUE se EMPRESA), campo continua editável depois.
    exige_hodometro = payload.exige_hodometro
    if exige_hodometro is None:
        exige_hodometro = payload.propriedade == "EMPRESA"

    novo = VeiculoPortaria(
        propriedade=payload.propriedade,
        funcionario_id=payload.funcionario_id,
        empresa_terceira_id=payload.empresa_terceira_id,
        placa=payload.placa,
        tipo=payload.tipo,
        marca_modelo=payload.marca_modelo,
        cor=payload.cor,
        exige_hodometro=exige_hodometro,
        situacao="PENDENTE",
        observacao=payload.observacao,
        criado_por=usuario.id,
    )
    db.add(novo)
    db.commit()
    db.refresh(novo)
    return _veiculo_read(novo, db)


@router.patch(
    "/veiculos/{veiculo_id}",
    response_model=VeiculoRead,
    summary="Edita dados cadastrais — nunca a situação (ver PATCH /veiculos/{id}/situacao)",
)
def atualizar_veiculo(
    veiculo_id: UUID,
    payload: VeiculoUpdate,
    usuario: EscritaCadastro,
    db: Annotated[Session, Depends(get_db)],
):
    veiculo = db.get(VeiculoPortaria, veiculo_id)
    if veiculo is None or not veiculo.ativo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Veículo não encontrado")

    # VeiculoUpdate não tem (nem aceita, extra="forbid") campo de situação —
    # este loop nunca vê `situacao`, mesmo que alguém tente mandar no JSON.
    for campo, valor in payload.model_dump(exclude_unset=True).items():
        setattr(veiculo, campo, valor)
    veiculo.atualizado_em = datetime.now(timezone.utc)
    veiculo.atualizado_por = usuario.id

    db.commit()
    db.refresh(veiculo)
    return _veiculo_read(veiculo, db)


@router.get("/empresas", response_model=list[EmpresaTerceiraRead], summary="Lista empresas prestadoras/terceiras")
def listar_empresas(
    usuario: LeituraCadastro,
    db: Annotated[Session, Depends(get_db)],
    apenas_ativas: bool = True,
):
    stmt = select(EmpresaTerceira)
    if apenas_ativas:
        stmt = stmt.where(EmpresaTerceira.ativo.is_(True))
    stmt = stmt.order_by(EmpresaTerceira.nome)
    return db.execute(stmt).scalars().all()


@router.post(
    "/empresas",
    response_model=EmpresaTerceiraRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra empresa prestadora/terceira (D5)",
)
def cadastrar_empresa(
    payload: EmpresaTerceiraCreate, usuario: EscritaCadastro, db: Annotated[Session, Depends(get_db)]
):
    nova = EmpresaTerceira(**payload.model_dump(), criado_por=usuario.id)
    db.add(nova)
    db.commit()
    db.refresh(nova)
    return nova


# ============================================================================
# AUTORIZAÇÃO — exige("autorizacao_veicular", ...) (D6, D11, D12, D13, D14)
# ============================================================================

@router.get(
    "/veiculos/pendentes",
    response_model=list[VeiculoRead],
    summary="Fila de PENDENTES — os responsáveis conferem o que o controlador cadastrou",
)
def listar_pendentes(usuario: LeituraAutorizacao, db: Annotated[Session, Depends(get_db)]):
    veiculos = db.execute(
        select(VeiculoPortaria)
        .where(VeiculoPortaria.situacao == "PENDENTE", VeiculoPortaria.ativo.is_(True))
        .order_by(VeiculoPortaria.criado_em)
    ).scalars().all()
    return [_veiculo_read(v, db) for v in veiculos]


@router.get(
    "/veiculos/divergencias",
    response_model=list[VeiculoDivergenciaRead],
    summary="D13 — veículos AUTORIZADOS de pessoas que não estão ATIVAS. Só mostra, não decide.",
)
def listar_divergencias(usuario: LeituraAutorizacao, db: Annotated[Session, Depends(get_db)]):
    linhas = db.execute(
        select(VeiculoPortaria, Funcionario.status)
        .join(Funcionario, Funcionario.id == VeiculoPortaria.funcionario_id)
        .where(
            VeiculoPortaria.situacao == "AUTORIZADO",
            VeiculoPortaria.propriedade == "PARTICULAR",
            VeiculoPortaria.ativo.is_(True),
            Funcionario.status != "ATIVO",
        )
        .order_by(VeiculoPortaria.placa)
    ).all()
    return [
        VeiculoDivergenciaRead(**_veiculo_read(v, db).model_dump(), funcionario_status=status_bruto)
        for v, status_bruto in linhas
    ]


@router.get(
    "/veiculos/{veiculo_id}/historico",
    response_model=list[VeiculoSituacaoHistRead],
    summary="Extrato cronológico de mudanças de situação (D14)",
)
def historico_situacao(
    veiculo_id: UUID, usuario: LeituraAutorizacao, db: Annotated[Session, Depends(get_db)]
):
    if db.get(VeiculoPortaria, veiculo_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Veículo não encontrado")

    linhas = db.execute(
        select(VeiculoSituacaoHist)
        .where(VeiculoSituacaoHist.veiculo_id == veiculo_id)
        .order_by(VeiculoSituacaoHist.decidido_em.desc())
    ).scalars().all()

    resultado = []
    for h in linhas:
        decisor = db.get(Funcionario, h.decidido_por)
        resultado.append(
            VeiculoSituacaoHistRead.model_validate(h).model_copy(
                update={"decidido_por_nome": decisor.nome if decisor else None}
            )
        )
    return resultado


@router.patch(
    "/veiculos/{veiculo_id}/situacao",
    response_model=VeiculoRead,
    summary="Autoriza, suspende ou baixa — só quem tem autorizacao_veicular escrever",
)
def mudar_situacao(
    veiculo_id: UUID,
    payload: VeiculoSituacaoUpdate,
    usuario: EscritaAutorizacao,
    db: Annotated[Session, Depends(get_db)],
):
    veiculo = db.get(VeiculoPortaria, veiculo_id)
    if veiculo is None or not veiculo.ativo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Veículo não encontrado")

    # ⚠️ Armadilha conhecida do projeto: capturar o valor ANTIGO antes do
    # UPDATE, nunca depois do db.flush() — senão o histórico grava a
    # própria situação nova como "situacao_de" (já mordeu 2× neste repo).
    situacao_anterior = veiculo.situacao

    agora = datetime.now(timezone.utc)
    veiculo.situacao = payload.situacao
    veiculo.situacao_por = usuario.id
    veiculo.situacao_em = agora
    veiculo.situacao_motivo = payload.motivo
    veiculo.atualizado_em = agora
    veiculo.atualizado_por = usuario.id
    db.flush()

    db.add(VeiculoSituacaoHist(
        veiculo_id=veiculo.id,
        situacao_de=situacao_anterior,
        situacao_para=payload.situacao,
        motivo=payload.motivo,
        decidido_por=usuario.id,
    ))
    db.commit()
    db.refresh(veiculo)
    return _veiculo_read(veiculo, db)


@router.post(
    "/veiculos/bloquear-por-re",
    response_model=BloquearPorReResponse,
    summary="D12 — suspende de uma vez todos os veículos ativos da pessoa, com histórico em cada um",
)
def bloquear_por_re(
    payload: BloquearPorReRequest, usuario: EscritaAutorizacao, db: Annotated[Session, Depends(get_db)]
):
    funcionario = db.execute(
        select(Funcionario).where(Funcionario.re == payload.re.strip())
    ).scalar_one_or_none()
    if funcionario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Funcionário não encontrado")

    veiculos = db.execute(
        select(VeiculoPortaria).where(
            VeiculoPortaria.funcionario_id == funcionario.id,
            VeiculoPortaria.ativo.is_(True),
            VeiculoPortaria.situacao != "BAIXADO",
        )
    ).scalars().all()

    agora = datetime.now(timezone.utc)
    for veiculo in veiculos:
        situacao_anterior = veiculo.situacao
        veiculo.situacao = "SUSPENSO"
        veiculo.situacao_por = usuario.id
        veiculo.situacao_em = agora
        veiculo.situacao_motivo = payload.motivo
        veiculo.atualizado_em = agora
        veiculo.atualizado_por = usuario.id
        db.flush()
        db.add(VeiculoSituacaoHist(
            veiculo_id=veiculo.id,
            situacao_de=situacao_anterior,
            situacao_para="SUSPENSO",
            motivo=payload.motivo,
            decidido_por=usuario.id,
        ))

    db.commit()
    for v in veiculos:
        db.refresh(v)

    return BloquearPorReResponse(
        funcionario_id=funcionario.id,
        funcionario_nome=funcionario.nome,
        re=funcionario.re,
        veiculos_suspensos=[_veiculo_read(v, db) for v in veiculos],
    )
