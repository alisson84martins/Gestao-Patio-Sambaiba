"""Endpoints do módulo Coordenadoria — Relatório de Ocorrências.

Regra de fronteira: nada aqui escreve em alocacao_patio, fila, alerta ou
escala. O router só lê catálogos do Pátio (onibus, linhas, motoristas) —
essa leitura acontece no FRONTEND para autocompletar prefixo/linha/condutor,
que chegam aqui sempre como texto, nunca FK.
"""
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.schemas.ocorrencia import (
    MensagemSinistroResponse, OcorrenciaAnexoRead, OcorrenciaCatalogos,
    OcorrenciaCompleta, OcorrenciaCreate, OcorrenciaListaResponse,
    OcorrenciaRead, OcorrenciaResumo, OcorrenciaUpdate,
)
from app.services.mensagem_sinistro import gerar_mensagem_sinistro

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ocorrencias", tags=["ocorrências"])

LeituraOcorrencia = Annotated[Funcionario, Depends(exige("ocorrencia"))]
EscritaOcorrencia = Annotated[Funcionario, Depends(exige("ocorrencia", escrever=True))]

TIPOS_ANEXO = {"FOTO_ACIDENTE", "FOTO_RELATORIO", "CROQUI", "BO_PDF", "OUTRO"}
ANEXO_MIME_PERMITIDOS = {"image/jpeg", "image/png", "application/pdf"}
ANEXO_TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB

# Relativo ao diretório de trabalho do processo (backend/) — mesmo padrão
# usado pelos scripts do projeto. Criado sob demanda, nunca versionado
# (backend/.gitignore cobre uploads/).
UPLOAD_ROOT = Path("uploads") / "ocorrencias"


def _carregar_completa(db: Session, ocorrencia_id: UUID) -> Optional[Ocorrencia]:
    """Carrega a ocorrência com todas as filhas.

    selectinload (não joinedload) nas coleções — uma ocorrência tem até 6
    coleções filhas diferentes; usar joinedload em todas ao mesmo tempo
    multiplicaria linhas via produto cartesiano (fan-out).
    """
    return db.execute(
        select(Ocorrencia)
        .options(
            joinedload(Ocorrencia.tipo_ocorrencia),
            joinedload(Ocorrencia.analise),
            selectinload(Ocorrencia.veiculos_terceiro),
            selectinload(Ocorrencia.avarias),
            selectinload(Ocorrencia.vitimas),
            selectinload(Ocorrencia.testemunhas),
            selectinload(Ocorrencia.autoridades).joinedload(OcorrenciaAutoridade.orgao),
            selectinload(Ocorrencia.anexos),
        )
        .where(Ocorrencia.id == ocorrencia_id, Ocorrencia.excluida_em.is_(None))
    ).unique().scalar_one_or_none()


# ─── CATÁLOGOS (deve vir ANTES de /{ocorrencia_id}) ───────────────────────────

@router.get(
    "/catalogos",
    response_model=OcorrenciaCatalogos,
    summary="Tipos + órgãos + regiões de avaria — uma chamada para montar os selects",
)
def catalogos(_: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]):
    tipos = db.execute(
        select(TipoOcorrencia).where(TipoOcorrencia.ativo.is_(True)).order_by(TipoOcorrencia.ordem)
    ).scalars().all()
    orgaos = db.execute(
        select(OrgaoAutoridade).where(OrgaoAutoridade.ativo.is_(True)).order_by(OrgaoAutoridade.ordem)
    ).scalars().all()
    return OcorrenciaCatalogos(tipos=tipos, orgaos=orgaos)


# ─── LISTAGEM ─────────────────────────────────────────────────────────────────
# Usa a view coordenadoria.vw_ocorrencia_resumo (migration 012) — ela já faz
# as contagens de filhas e filtra excluida_em; reimplementar isso em ORM só
# duplicaria a mesma lógica com joins fan-out mais caros.

_FILTROS_SQL = """
       WHERE (CAST(:data_inicio AS date) IS NULL OR data_ocorrencia >= CAST(:data_inicio AS date))
         AND (CAST(:data_fim AS date) IS NULL OR data_ocorrencia <= CAST(:data_fim AS date))
         AND (CAST(:tipo AS text) IS NULL OR tipo_codigo = :tipo)
         AND (CAST(:prefixo AS text) IS NULL OR prefixo ILIKE '%' || :prefixo || '%')
         AND (CAST(:linha AS text) IS NULL OR linha_codigo ILIKE '%' || :linha || '%')
         AND (CAST(:status AS text) IS NULL OR status = :status)
"""


@router.get("", response_model=OcorrenciaListaResponse, summary="Lista ocorrências com filtros e paginação")
def listar(
    _: LeituraOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    data_inicio: Optional[date] = Query(None, description="Data da ocorrência, início do período"),
    data_fim: Optional[date] = Query(None, description="Data da ocorrência, fim do período"),
    tipo: Optional[str] = Query(None, description="Código do tipo de ocorrência"),
    prefixo: Optional[str] = None,
    linha: Optional[str] = None,
    status_filtro: Annotated[Optional[str], Query(alias="status")] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    params = {
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "tipo": tipo.upper() if tipo else None,
        "prefixo": prefixo,
        "linha": linha,
        "status": status_filtro.upper() if status_filtro else None,
    }

    total = db.execute(
        text(f"SELECT count(*) FROM coordenadoria.vw_ocorrencia_resumo{_FILTROS_SQL}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT id, numero, data_ocorrencia, hora_ocorrencia, tipo_codigo, tipo_nome,
                   prefixo, linha_codigo, bairro, status,
                   qtd_vitimas, qtd_testemunhas, qtd_autoridades, qtd_anexos
              FROM coordenadoria.vw_ocorrencia_resumo
            {_FILTROS_SQL}
             ORDER BY data_ocorrencia DESC, hora_ocorrencia DESC
             OFFSET :skip LIMIT :limit
            """
        ),
        {**params, "skip": skip, "limit": limit},
    ).mappings().all()

    itens = [OcorrenciaResumo(**dict(r)) for r in rows]
    return OcorrenciaListaResponse(total=total, itens=itens)


# ─── DETALHE ──────────────────────────────────────────────────────────────────

@router.get("/{ocorrencia_id}", response_model=OcorrenciaCompleta, summary="Ocorrência completa, com todas as filhas")
def detalhar(ocorrencia_id: UUID, _: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = _carregar_completa(db, ocorrencia_id)
    if oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    return oc


# ─── CRIAÇÃO ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=OcorrenciaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria ocorrência como RASCUNHO",
)
def criar(payload: OcorrenciaCreate, func: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    if db.get(TipoOcorrencia, payload.tipo_ocorrencia_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tipo de ocorrência não encontrado")

    nova = Ocorrencia(**payload.model_dump(), status="RASCUNHO", registrado_por=func.id)
    db.add(nova)
    db.commit()
    db.refresh(nova)
    return nova


# ─── EDIÇÃO (capa + filhas) ────────────────────────────────────────────────────

@router.patch(
    "/{ocorrencia_id}",
    response_model=OcorrenciaCompleta,
    summary="Atualização parcial da capa; listas de filhas enviadas substituem a coleção inteira",
)
def atualizar(
    ocorrencia_id: UUID, payload: OcorrenciaUpdate, func: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]
):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")

    dados_capa = payload.model_dump(
        exclude_unset=True,
        exclude={"analise", "veiculos_terceiro", "avarias", "vitimas", "testemunhas", "autoridades"},
    )
    if dados_capa.get("tipo_ocorrencia_id") and db.get(TipoOcorrencia, dados_capa["tipo_ocorrencia_id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tipo de ocorrência não encontrado")

    for campo, valor in dados_capa.items():
        setattr(oc, campo, valor)

    if payload.analise is not None:
        oc.analise = OcorrenciaAnalise(ocorrencia_id=oc.id, **payload.analise.model_dump())

    if payload.veiculos_terceiro is not None:
        oc.veiculos_terceiro.clear()
        for item in payload.veiculos_terceiro:
            oc.veiculos_terceiro.append(OcorrenciaVeiculoTerceiro(**item.model_dump()))

    if payload.avarias is not None:
        oc.avarias.clear()
        for item in payload.avarias:
            oc.avarias.append(OcorrenciaAvaria(**item.model_dump()))

    if payload.vitimas is not None:
        oc.vitimas.clear()
        for item in payload.vitimas:
            oc.vitimas.append(OcorrenciaVitima(**item.model_dump()))

    if payload.testemunhas is not None:
        oc.testemunhas.clear()
        for item in payload.testemunhas:
            oc.testemunhas.append(OcorrenciaTestemunha(**item.model_dump()))

    if payload.autoridades is not None:
        oc.autoridades.clear()
        for item in payload.autoridades:
            oc.autoridades.append(OcorrenciaAutoridade(**item.model_dump()))

    oc.atualizado_em = datetime.now(timezone.utc)
    oc.atualizado_por = func.id
    db.commit()

    return _carregar_completa(db, ocorrencia_id)


# ─── FINALIZAR ────────────────────────────────────────────────────────────────

@router.post(
    "/{ocorrencia_id}/finalizar",
    response_model=OcorrenciaRead,
    summary="Muda status para FINALIZADA e grava finalizada_em",
)
def finalizar(ocorrencia_id: UUID, func: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")

    agora = datetime.now(timezone.utc)
    oc.status = "FINALIZADA"
    oc.finalizada_em = agora
    oc.atualizado_em = agora
    oc.atualizado_por = func.id
    db.commit()
    db.refresh(oc)
    return oc


# ─── EXCLUSÃO (soft delete) ────────────────────────────────────────────────────

@router.delete(
    "/{ocorrencia_id}",
    response_model=OcorrenciaRead,
    summary="Soft delete — grava excluida_em, nunca apaga a linha",
)
def deletar(ocorrencia_id: UUID, func: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")

    oc.excluida_em = datetime.now(timezone.utc)
    oc.atualizado_em = oc.excluida_em
    oc.atualizado_por = func.id
    db.commit()
    db.refresh(oc)
    return oc


# ─── MENSAGEM DO SINISTRO ───────────────────────────────────────────────────────

@router.get(
    "/{ocorrencia_id}/mensagem-sinistro",
    response_model=MensagemSinistroResponse,
    summary="Texto pronto para o grupo do sinistro no WhatsApp",
)
def mensagem_sinistro(ocorrencia_id: UUID, _: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = _carregar_completa(db, ocorrencia_id)
    if oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")

    coordenador = db.get(Funcionario, oc.registrado_por) if oc.registrado_por else None
    texto = gerar_mensagem_sinistro(
        oc,
        coordenador_nome=coordenador.nome if coordenador else "",
        coordenador_re=coordenador.re if coordenador else "",
    )
    return MensagemSinistroResponse(texto=texto)


# ─── ANEXOS ───────────────────────────────────────────────────────────────────

@router.post(
    "/{ocorrencia_id}/anexos",
    response_model=OcorrenciaAnexoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload de foto, croqui ou PDF do B.O.",
)
async def upload_anexo(
    ocorrencia_id: UUID,
    func: EscritaOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    arquivo: Annotated[UploadFile, File(description="image/jpeg, image/png ou application/pdf — máx 10 MB")],
    tipo: Annotated[str, Form(description="FOTO_ACIDENTE | FOTO_RELATORIO | CROQUI | BO_PDF | OUTRO")],
    descricao: Annotated[Optional[str], Form()] = None,
):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")

    if tipo not in TIPOS_ANEXO:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Tipo de anexo inválido: {tipo}")

    if arquivo.content_type not in ANEXO_MIME_PERMITIDOS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato não suportado: {arquivo.content_type}. Envie JPEG, PNG ou PDF.",
        )

    conteudo = await arquivo.read()
    if len(conteudo) > ANEXO_TAMANHO_MAXIMO:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Arquivo muito grande (máx 10 MB)")

    pasta = UPLOAD_ROOT / str(ocorrencia_id)
    pasta.mkdir(parents=True, exist_ok=True)
    extensao = Path(arquivo.filename or "").suffix
    nome_arquivo = f"{uuid4()}{extensao}"
    (pasta / nome_arquivo).write_bytes(conteudo)

    anexo = OcorrenciaAnexo(
        ocorrencia_id=ocorrencia_id,
        tipo=tipo,
        caminho=f"ocorrencias/{ocorrencia_id}/{nome_arquivo}",
        nome_original=arquivo.filename,
        mime_type=arquivo.content_type,
        tamanho_bytes=len(conteudo),
        descricao=descricao,
        enviado_por=func.id,
    )
    db.add(anexo)
    db.commit()
    db.refresh(anexo)
    return anexo


@router.get(
    "/{ocorrencia_id}/anexos/{anexo_id}/arquivo",
    summary="Baixa o arquivo do anexo — protegido pelo mesmo acesso 'ocorrencia' (nunca público)",
)
def baixar_anexo(
    ocorrencia_id: UUID, anexo_id: UUID, _: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]
):
    anexo = db.execute(
        select(OcorrenciaAnexo).where(
            OcorrenciaAnexo.id == anexo_id, OcorrenciaAnexo.ocorrencia_id == ocorrencia_id
        )
    ).scalar_one_or_none()
    if anexo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Anexo não encontrado")

    caminho_disco = Path("uploads") / anexo.caminho
    if not caminho_disco.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado no servidor")

    return FileResponse(
        caminho_disco, media_type=anexo.mime_type or "application/octet-stream",
        filename=anexo.nome_original or caminho_disco.name,
    )


@router.delete(
    "/{ocorrencia_id}/anexos/{anexo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove arquivo e registro",
)
def deletar_anexo(
    ocorrencia_id: UUID, anexo_id: UUID, _: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]
) -> None:
    anexo = db.execute(
        select(OcorrenciaAnexo).where(
            OcorrenciaAnexo.id == anexo_id, OcorrenciaAnexo.ocorrencia_id == ocorrencia_id
        )
    ).scalar_one_or_none()
    if anexo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Anexo não encontrado")

    caminho_disco = Path("uploads") / anexo.caminho
    try:
        caminho_disco.unlink(missing_ok=True)
    except OSError:
        logger.warning("Não foi possível remover o arquivo do anexo %s do disco", anexo_id)

    db.delete(anexo)
    db.commit()
