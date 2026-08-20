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
    EmpresaTerceiraRead, FuncionarioPortariaBusca, Propriedade, SituacaoVeiculo,
    VeiculoCreate, VeiculoDivergenciaRead, VeiculoRead, VeiculoSituacaoHistRead,
    VeiculoSituacaoUpdate, VeiculoUpdate,
)
from app.services.portaria import veiculo_read

router = APIRouter(prefix="/portaria", tags=["portaria"])

LeituraCadastro = Annotated[Funcionario, Depends(exige("veiculo_portaria"))]
EscritaCadastro = Annotated[Funcionario, Depends(exige("veiculo_portaria", escrever=True))]
LeituraAutorizacao = Annotated[Funcionario, Depends(exige("autorizacao_veicular"))]
EscritaAutorizacao = Annotated[Funcionario, Depends(exige("autorizacao_veicular", escrever=True))]


# ============================================================================
# CADASTRO — exige("veiculo_portaria", ...)
# ============================================================================

# 🔧 Adição do Bloco C (frontend), fora do desenho original: o cadastro de
# veículo PARTICULAR precisa resolver RE -> funcionario_id (UUID), e
# /funcionarios/busca é gated por exige("ocorrencia") — recurso que
# CONTROLADOR_ACESSO não tem — além de não devolver id. Registrada aqui
# (não em silêncio) por divergir do prompt original.
@router.get(
    "/funcionarios/busca",
    response_model=list[FuncionarioPortariaBusca],
    summary="Autocomplete RE/nome -> id, só pra resolver o dono do veículo PARTICULAR",
)
def buscar_funcionario_portaria(
    usuario: LeituraCadastro,
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(..., min_length=2, max_length=80),
):
    termo = f"%{q.strip()}%"
    funcionarios = db.execute(
        select(Funcionario)
        .where(Funcionario.status == "ATIVO", (Funcionario.re.ilike(termo) | Funcionario.nome.ilike(termo)))
        .order_by(Funcionario.nome)
        .limit(20)
    ).scalars().all()
    return [FuncionarioPortariaBusca(id=f.id, re=f.re, nome=f.nome) for f in funcionarios]


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
    return [veiculo_read(v, db) for v in veiculos]


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
    return veiculo_read(novo, db)


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
    return veiculo_read(veiculo, db)


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
    return [veiculo_read(v, db) for v in veiculos]


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
        VeiculoDivergenciaRead(**veiculo_read(v, db).model_dump(), funcionario_status=status_bruto)
        for v, status_bruto in linhas
    ]


# 🔴 §3.6-D.3: registrada AQUI, depois de /veiculos/pendentes e
# /veiculos/divergencias — se viesse antes, o FastAPI casaria "pendentes" e
# "divergencias" como se fossem {veiculo_id} (UUID inválido -> 422), e a
# fila dos responsáveis quebraria. Mesma armadilha de ordem que main.py já
# documenta para /pre-ocorrencias/publico. Fica com o recurso `veiculo_portaria`
# (mesmo da listagem/cadastro) mesmo estando fisicamente no bloco de
# autorização — é só posição, a permissão continua sendo a de cadastro.
@router.get(
    "/veiculos/{veiculo_id}",
    response_model=VeiculoRead,
    summary="Ficha do veículo (registrar sempre depois de /pendentes e /divergencias — ver comentário acima)",
)
def detalhar_veiculo(
    veiculo_id: UUID, usuario: LeituraCadastro, db: Annotated[Session, Depends(get_db)]
):
    veiculo = db.get(VeiculoPortaria, veiculo_id)
    if veiculo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Veículo não encontrado")
    return veiculo_read(veiculo, db)


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
    # 🔴 §3.6-A.2: SEM checar `ativo` aqui, ao contrário do PATCH cadastral.
    # Quem tem autorizacao_veicular precisa alcançar o registro pra
    # consertar mesmo que ele esteja `ativo=false` — senão o veículo fica
    # inalcançável pela API pra sempre.
    if veiculo is None:
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
    return veiculo_read(veiculo, db)


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

    # §3.6-D.1: quem já está SUSPENSO fica de fora do laço — rebloquear
    # gerava linha SUSPENSO->SUSPENSO no histórico e sobrescrevia o motivo
    # anterior sem necessidade nenhuma.
    a_suspender = [v for v in veiculos if v.situacao != "SUSPENSO"]
    ja_suspensos = [v for v in veiculos if v.situacao == "SUSPENSO"]

    agora = datetime.now(timezone.utc)
    for veiculo in a_suspender:
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
    for v in a_suspender:
        db.refresh(v)

    return BloquearPorReResponse(
        funcionario_id=funcionario.id,
        funcionario_nome=funcionario.nome,
        re=funcionario.re,
        veiculos_suspensos=[veiculo_read(v, db) for v in a_suspender],
        ja_suspensos=[veiculo_read(v, db) for v in ja_suspensos],
    )
