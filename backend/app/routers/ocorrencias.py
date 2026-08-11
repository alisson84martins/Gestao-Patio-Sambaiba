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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.deps import exige
from app.core.uploads import ler_upload_limitado, resolver_caminho_seguro, validar_assinatura
from app.services.auditoria import ip_do_request, registrar_log_acesso
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.frota import Onibus
from app.models.ocorrencia import (
    Ocorrencia, OcorrenciaAnalise, OcorrenciaAnexo, OcorrenciaAutoridade,
    OcorrenciaAvaria, OcorrenciaTestemunha, OcorrenciaVeiculoTerceiro,
    OcorrenciaVitima, OrgaoAutoridade, TipoOcorrencia,
)
from app.schemas.ocorrencia import (
    AutopreenchimentoPessoa, AutopreenchimentoVeiculo, MensagemSinistroResponse,
    OcorrenciaAnexoRead, OcorrenciaCatalogos, OcorrenciaCompleta, OcorrenciaCreate,
    OcorrenciaListaResponse, OcorrenciaRead, OcorrenciaResumo, OcorrenciaUpdate,
    OrigemPessoa,
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
UPLOADS_BASE = Path("uploads")
UPLOAD_ROOT = UPLOADS_BASE / "ocorrencias"


def _eh_admin(db: Session, funcionario_id: UUID) -> bool:
    """Checa a função pelo modelo RBAC (funcionario_funcao → funcao), não
    pelo campo legado `usuario.perfil` — este está em aposentadoria (ver
    SISTEMA-EM-PRODUCAO.md, pendências)."""
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'ADMIN'"
        ),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def _exige_autoria(oc: Ocorrencia, usuario: Funcionario, db: Session) -> None:
    """Só o autor mexe na própria ocorrência. ADMIN é a única exceção.
    Regra de Alisson (01/08/2026): ocorrência é documento assinado por quem
    registrou — outro coordenador não altera nem apaga o que não é dele.

    Ocorrência antiga com registrado_por nulo cai direto no 403 (a menos
    que quem peça seja ADMIN) — falhar fechado é o certo aqui, não dá pra
    provar autoria de um registro que nunca gravou quem o criou.
    """
    if oc.registrado_por == usuario.id:
        return
    if _eh_admin(db, usuario.id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="Esta ocorrência foi registrada por outro coordenador",
    )


# Decisão do Alisson (11/08/2026, Bloco B): Coordenador de Tráfego só vê e
# edita as PRÓPRIAS ocorrências; ADMIN, Encarregado e gerência veem todas.
# Multifunção: a mais permissiva vence — quem tem qualquer função desta
# lista vê tudo, mesmo acumulando COORDENADOR_TRAFEGO também.
_FUNCOES_VEEM_TODAS_OCORRENCIAS = ("ADMIN", "ENCARREGADO", "GERENTE_GERAL", "GERENTE_OPERACIONAL")


def _ve_todas_ocorrencias(usuario: Funcionario, db: Session) -> bool:
    """RBAC via funcionario_funcao, nunca o campo legado usuario.perfil —
    mesmo padrão de _eh_admin()."""
    row = db.execute(
        text(
            "SELECT 1 FROM funcionario_funcao ff "
            "JOIN funcao f ON f.id = ff.funcao_id "
            "WHERE ff.funcionario_id = :fid AND ff.ativo "
            "AND f.codigo IN ('ADMIN','ENCARREGADO','GERENTE_GERAL','GERENTE_OPERACIONAL')"
        ),
        {"fid": usuario.id},
    ).first()
    return row is not None


def _exige_pode_ver(oc: Ocorrencia, usuario: Funcionario, db: Session) -> None:
    """Trava de LEITURA por autoria — diferente de _exige_autoria() (que é
    de ESCRITA e só abre exceção pra ADMIN). Aqui quem "vê todas" (ver
    _ve_todas_ocorrencias) passa; Coordenador de Tráfego só passa na
    própria. registrado_por nulo cai fechado pro coordenador, mesma lógica
    de _exige_autoria — não dá pra provar autoria de um registro que nunca
    gravou quem o criou. Responde 403, nunca 404 — mesma política do resto
    do arquivo: quem não pode ver tem direito de saber que o registro
    existe e que o impedimento é de permissão, não de dado inexistente.
    """
    # Checa a comparação barata primeiro (mesma ordem de _exige_autoria) —
    # evita a consulta a funcionario_funcao no caso comum de alguém lendo
    # a própria ocorrência.
    if oc.registrado_por == usuario.id:
        return
    if _ve_todas_ocorrencias(usuario, db):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="Você só pode ver as ocorrências que registrou",
    )


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


# ─── AUTOPREENCHIMENTO (deve vir ANTES de /{ocorrencia_id}) ───────────────────
# O mesmo ônibus tem dois números: o Pátio usa 4 dígitos (1721), a Sambaíba
# usa 5 com a área na frente (21721 = área 2 + carro 1721 = E2; 22721 = área 2
# + carro 2721 = AR2). onibus.numero_frota só aceita 1000-2999 (migration
# 003) — cinco dígitos nunca cabem lá. A tradução é só na CONSULTA, nunca no
# armazenamento: ocorrencia.prefixo grava sempre o que foi digitado.

def normalizar_prefixo(prefixo: str) -> Optional[int]:
    """Converte o prefixo digitado no número de frota do Pátio (1000-2999).

    5 dígitos iniciados em 2 → derruba a área e devolve os 4 restantes:
        "21721" → 1721 (E2)   ·   "22721" → 2721 (AR2)
    4 dígitos → devolve como está, se estiver na faixa do Pátio.
    Qualquer outra coisa → None (o chamador segue sem autopreencher).

    Sem ambiguidade: o COMPRIMENTO decide, nunca o valor — "2172" (4
    dígitos) é um carro AR2 legítimo do Pátio; "21721" (5 dígitos) é o
    "1721" do E2. Nunca adivinhar pela faixa numérica.
    """
    if not prefixo:
        return None
    digitos = prefixo.strip()
    if not digitos.isdigit():
        return None

    if len(digitos) == 5 and digitos[0] == "2":
        numero = int(digitos[1:])
    elif len(digitos) == 4:
        numero = int(digitos)
    else:
        return None

    return numero if 1000 <= numero <= 2999 else None


@router.get(
    "/autopreencher/veiculo",
    response_model=AutopreenchimentoVeiculo,
    summary="Placa, setor e linha a partir do prefixo — cadastro do ônibus primeiro, última ocorrência como reserva",
)
def autopreencher_veiculo(
    _: LeituraOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    prefixo: str = Query(..., max_length=10),
):
    numero_frota = normalizar_prefixo(prefixo)
    if numero_frota is None:
        return AutopreenchimentoVeiculo(prefixo_informado=prefixo)

    onibus = db.execute(
        select(Onibus).where(Onibus.numero_frota == numero_frota)
    ).scalar_one_or_none()
    setor = onibus.setor.value if onibus is not None and onibus.setor else None
    placa = onibus.placa if onibus is not None and onibus.placa else None
    origem_placa: Optional[str] = "cadastro" if placa else None

    # Busca pelo prefixo TAL COMO DIGITADO (ocorrencia.prefixo guarda
    # "21721", não o "1721" normalizado) — as duas consultas usam chaves
    # diferentes de propósito.
    ultima = db.execute(
        select(Ocorrencia)
        .where(Ocorrencia.prefixo == prefixo, Ocorrencia.excluida_em.is_(None))
        .order_by(Ocorrencia.data_ocorrencia.desc(), Ocorrencia.hora_ocorrencia.desc())
        .limit(1)
    ).scalar_one_or_none()

    linha_codigo = ultima.linha_codigo if ultima is not None else None
    if placa is None and ultima is not None and ultima.placa:
        placa = ultima.placa
        origem_placa = "ocorrencia"

    return AutopreenchimentoVeiculo(
        prefixo_informado=prefixo,
        numero_frota=numero_frota,
        setor=setor,
        placa=placa,
        linha_codigo=linha_codigo,
        origem_placa=origem_placa,
    )


@router.get(
    "/autopreencher/pessoa",
    response_model=AutopreenchimentoPessoa,
    summary="Nome, função, contato e documentos a partir do RE — cadastro central primeiro, última ocorrência como reserva",
)
def autopreencher_pessoa(
    _: LeituraOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    re: str = Query(..., max_length=20),
    papel: str = Query(..., pattern="^(condutor|cobrador)$"),
):
    re = re.strip()
    campos: dict[str, Optional[str]] = {
        "nome": None, "funcao": None, "telefone": None, "cpf": None, "rg": None, "cnh": None,
    }
    origem: dict[str, Optional[str]] = dict.fromkeys(campos)

    funcionario = db.execute(
        select(Funcionario).where(Funcionario.re == re)
    ).scalar_one_or_none()

    if funcionario is not None:
        for campo, valor in (
            ("nome", funcionario.nome), ("telefone", funcionario.telefone),
            ("cpf", funcionario.cpf), ("rg", funcionario.rg), ("cnh", funcionario.cnh),
        ):
            if valor:
                campos[campo] = valor
                origem[campo] = "cadastro"

        funcao_nome = db.execute(
            select(Funcao.nome)
            .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
            .where(
                FuncionarioFuncao.funcionario_id == funcionario.id,
                FuncionarioFuncao.ativo.is_(True),
                FuncionarioFuncao.principal.is_(True),
            )
        ).scalar_one_or_none()
        if funcao_nome:
            campos["funcao"] = funcao_nome
            origem["funcao"] = "cadastro"

    faltando = [campo for campo, valor in campos.items() if valor is None]
    if faltando:
        campo_re = Ocorrencia.condutor_re if papel == "condutor" else Ocorrencia.cobrador_re
        ultima = db.execute(
            select(Ocorrencia)
            .where(campo_re == re, Ocorrencia.excluida_em.is_(None))
            .order_by(Ocorrencia.data_ocorrencia.desc(), Ocorrencia.hora_ocorrencia.desc())
            .limit(1)
        ).scalar_one_or_none()

        if ultima is not None:
            # ⚠️ Ocorrencia só tem condutor_funcao/cnh/rg/cpf — não existe
            # cobrador_funcao nem documento de cobrador (ver docstring de
            # AutopreenchimentoPessoa). Pra papel="cobrador" só "nome" tem
            # de onde vir; os outros continuam None (não tem fallback).
            if papel == "condutor":
                mapa_ocorrencia = {
                    "nome": ultima.condutor_nome, "funcao": ultima.condutor_funcao,
                    "cpf": ultima.condutor_cpf, "rg": ultima.condutor_rg, "cnh": ultima.condutor_cnh,
                }
            else:
                mapa_ocorrencia = {"nome": ultima.cobrador_nome}

            for campo in faltando:
                valor = mapa_ocorrencia.get(campo)
                if valor:
                    campos[campo] = valor
                    origem[campo] = "ocorrencia"

    return AutopreenchimentoPessoa(re=re, **campos, origem=OrigemPessoa(**origem))


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
         AND (CAST(:coordenador_re AS text) IS NULL OR coordenador_re = :coordenador_re)
"""


@router.get("", response_model=OcorrenciaListaResponse, summary="Lista ocorrências com filtros e paginação")
def listar(
    usuario: LeituraOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    data_inicio: Optional[date] = Query(None, description="Data da ocorrência, início do período"),
    data_fim: Optional[date] = Query(None, description="Data da ocorrência, fim do período"),
    tipo: Optional[str] = Query(None, description="Código do tipo de ocorrência"),
    prefixo: Optional[str] = None,
    linha: Optional[str] = None,
    status_filtro: Annotated[Optional[str], Query(alias="status")] = None,
    coordenador_re: Optional[str] = Query(
        None, description="Filtra por RE do coordenador — só tem efeito pra quem vê todas as ocorrências"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    # Bloco B, item 8: Coordenador de Tráfego só vê as próprias — o valor
    # que o cliente mandou em coordenador_re é IGNORADO e forçado pro
    # próprio RE. Esconder o campo no frontend é conveniência; a trava
    # real é aqui, no servidor (vw_ocorrencia_resumo.coordenador_re é o
    # mesmo funcionario.re de registrado_por via join — ver nota em
    # _FILTROS_SQL/migration 012; usar coordenador_re em vez de alterar a
    # view pra expor registrado_por direto foi decisão registrada em
    # EXECUCAO-2026-08-11.md, "Perguntas para o Alisson").
    ve_todas = _ve_todas_ocorrencias(usuario, db)
    coordenador_re_filtro = coordenador_re if ve_todas else usuario.re

    params = {
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "tipo": tipo.upper() if tipo else None,
        "prefixo": prefixo,
        "linha": linha,
        "status": status_filtro.upper() if status_filtro else None,
        "coordenador_re": coordenador_re_filtro,
    }

    total = db.execute(
        text(f"SELECT count(*) FROM coordenadoria.vw_ocorrencia_resumo{_FILTROS_SQL}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT id, numero, data_ocorrencia, hora_ocorrencia, tipo_codigo, tipo_nome,
                   prefixo, linha_codigo, bairro, status,
                   qtd_vitimas, qtd_testemunhas, qtd_autoridades, qtd_anexos,
                   coordenador_nome, coordenador_re
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
def detalhar(
    ocorrencia_id: UUID,
    usuario: LeituraOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    oc = _carregar_completa(db, ocorrencia_id)
    if oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_pode_ver(oc, usuario, db)
    if oc.registrado_por:
        autor = db.get(Funcionario, oc.registrado_por)
        oc.registrado_por_nome = autor.nome if autor else None
    # Trilha de auditoria mínima (migration 021) — quem, quando, qual.
    # Nunca o dado da ocorrência em si (nome/CPF de vítima), só o id.
    registrar_log_acesso(
        db, "LEITURA_OCORRENCIA",
        funcionario_id=usuario.id, ocorrencia_id=ocorrencia_id, ip=ip_do_request(request),
    )
    return oc


# ─── CRIAÇÃO ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=OcorrenciaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria ocorrência como RASCUNHO",
)
def criar(payload: OcorrenciaCreate, usuario: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    if db.get(TipoOcorrencia, payload.tipo_ocorrencia_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tipo de ocorrência não encontrado")

    nova = Ocorrencia(**payload.model_dump(), status="RASCUNHO", registrado_por=usuario.id)
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
    ocorrencia_id: UUID, payload: OcorrenciaUpdate, usuario: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]
):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_autoria(oc, usuario, db)

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

    # ⚠️ Cada .clear() precisa de um db.flush() ANTES do append de itens
    # novos. veiculos_terceiro, avarias, vitimas e testemunhas têm
    # UniqueConstraint(ocorrencia_id, ordem|regiao) — e o flush do
    # SQLAlchemy roda inserts/updates ANTES de deletes por padrão. Sem o
    # flush no meio, substituir um item que já existia (ordem/região
    # repetida — o caso normal de editar um veículo/vítima já salvo)
    # tenta inserir a linha nova antes de apagar a antiga e colide na
    # unique constraint: IntegrityError → 409, a edição não salva e some
    # da mensagem do sinistro. Reproduzido isoladamente (SQLite em
    # memória) antes de corrigir — era essa a causa real do item 4, não
    # falta de dado no payload nem bug em _bloco_terceiro/_bloco_vitima.
    if payload.veiculos_terceiro is not None:
        oc.veiculos_terceiro.clear()
        db.flush()
        for item in payload.veiculos_terceiro:
            oc.veiculos_terceiro.append(OcorrenciaVeiculoTerceiro(**item.model_dump()))

    if payload.avarias is not None:
        oc.avarias.clear()
        db.flush()
        for item in payload.avarias:
            oc.avarias.append(OcorrenciaAvaria(**item.model_dump()))

    if payload.vitimas is not None:
        oc.vitimas.clear()
        db.flush()
        for item in payload.vitimas:
            oc.vitimas.append(OcorrenciaVitima(**item.model_dump()))

    if payload.testemunhas is not None:
        oc.testemunhas.clear()
        db.flush()
        for item in payload.testemunhas:
            oc.testemunhas.append(OcorrenciaTestemunha(**item.model_dump()))

    if payload.autoridades is not None:
        oc.autoridades.clear()
        for item in payload.autoridades:
            oc.autoridades.append(OcorrenciaAutoridade(**item.model_dump()))

    oc.atualizado_em = datetime.now(timezone.utc)
    oc.atualizado_por = usuario.id
    db.commit()

    return _carregar_completa(db, ocorrencia_id)


# ─── FINALIZAR ────────────────────────────────────────────────────────────────

@router.post(
    "/{ocorrencia_id}/finalizar",
    response_model=OcorrenciaRead,
    summary="Muda status para FINALIZADA e grava finalizada_em",
)
def finalizar(ocorrencia_id: UUID, usuario: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_autoria(oc, usuario, db)

    agora = datetime.now(timezone.utc)
    oc.status = "FINALIZADA"
    oc.finalizada_em = agora
    oc.atualizado_em = agora
    oc.atualizado_por = usuario.id
    db.commit()
    db.refresh(oc)
    return oc


# ─── EXCLUSÃO (soft delete) ────────────────────────────────────────────────────

@router.delete(
    "/{ocorrencia_id}",
    response_model=OcorrenciaRead,
    summary="Soft delete — grava excluida_em, nunca apaga a linha",
)
def deletar(ocorrencia_id: UUID, usuario: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_autoria(oc, usuario, db)

    oc.excluida_em = datetime.now(timezone.utc)
    oc.atualizado_em = oc.excluida_em
    oc.atualizado_por = usuario.id
    db.commit()
    db.refresh(oc)
    return oc


# ─── MENSAGEM DO SINISTRO ───────────────────────────────────────────────────────

@router.get(
    "/{ocorrencia_id}/mensagem-sinistro",
    response_model=MensagemSinistroResponse,
    summary="Texto pronto para o grupo do sinistro no WhatsApp",
)
def mensagem_sinistro(ocorrencia_id: UUID, usuario: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]):
    oc = _carregar_completa(db, ocorrencia_id)
    if oc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    # Rota separada de detalhar() — NÃO herda a trava de lá automaticamente,
    # por isso checa de novo aqui (item 8.3 do prompt de execução: "confirme
    # que cobre, não presuma"). Sem isso, um coordenador que não pode ver a
    # ocorrência de outro ainda conseguiria gerar a mensagem do sinistro
    # dela.
    _exige_pode_ver(oc, usuario, db)

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
    usuario: EscritaOcorrencia,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    arquivo: Annotated[UploadFile, File(description="image/jpeg, image/png ou application/pdf — máx 10 MB")],
    tipo: Annotated[str, Form(description="FOTO_ACIDENTE | FOTO_RELATORIO | CROQUI | BO_PDF | OUTRO")],
    descricao: Annotated[Optional[str], Form()] = None,
):
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_autoria(oc, usuario, db)

    if tipo not in TIPOS_ANEXO:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Tipo de anexo inválido: {tipo}")

    if arquivo.content_type not in ANEXO_MIME_PERMITIDOS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato não suportado: {arquivo.content_type}. Envie JPEG, PNG ou PDF.",
        )

    # SEV-12: lê em blocos, abortando ao estourar — nunca o arquivo
    # inteiro em memória antes de saber se ele cabe.
    conteudo = await ler_upload_limitado(arquivo, ANEXO_TAMANHO_MAXIMO, request)

    # SEV-13: Content-Type é o que o cliente afirma, trivial de forjar
    # (ex.: um .exe renomeado com Content-Type: application/pdf). Os
    # primeiros bytes do arquivo não mentem.
    if not validar_assinatura(conteudo, arquivo.content_type):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="O conteúdo do arquivo não corresponde a um JPEG, PNG ou PDF válido.",
        )

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
        enviado_por=usuario.id,
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
    ocorrencia_id: UUID, anexo_id: UUID, usuario: LeituraOcorrencia, db: Annotated[Session, Depends(get_db)]
):
    # Achado ao aplicar o item 8.3 (não estava listado explicitamente no
    # prompt, mas é a mesma lacuna): baixar_anexo() nunca carregava a
    # Ocorrencia nem checava quem pode ver — um coordenador que soubesse
    # ocorrencia_id + anexo_id de outro conseguia baixar o arquivo mesmo
    # sem aparecer na lista nem no detalhe. Ver EXECUCAO-2026-08-11.md.
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_pode_ver(oc, usuario, db)

    anexo = db.execute(
        select(OcorrenciaAnexo).where(
            OcorrenciaAnexo.id == anexo_id, OcorrenciaAnexo.ocorrencia_id == ocorrencia_id
        )
    ).scalar_one_or_none()
    if anexo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Anexo não encontrado")

    # SEV-14: caminho absoluto, conferido contra UPLOADS_BASE — nenhum
    # valor de `caminho` (mesmo que corrompido ou de origem futura
    # diferente do upload atual) sai da pasta de upload.
    caminho_disco = resolver_caminho_seguro(UPLOADS_BASE, anexo.caminho)
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
    ocorrencia_id: UUID, anexo_id: UUID, usuario: EscritaOcorrencia, db: Annotated[Session, Depends(get_db)]
) -> None:
    # A autoria é da OCORRÊNCIA, não do anexo — carrega e checa antes de
    # sequer buscar o anexo (SEV-06).
    oc = db.get(Ocorrencia, ocorrencia_id)
    if oc is None or oc.excluida_em is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ocorrência não encontrada")
    _exige_autoria(oc, usuario, db)

    anexo = db.execute(
        select(OcorrenciaAnexo).where(
            OcorrenciaAnexo.id == anexo_id, OcorrenciaAnexo.ocorrencia_id == ocorrencia_id
        )
    ).scalar_one_or_none()
    if anexo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Anexo não encontrado")

    caminho_disco = resolver_caminho_seguro(UPLOADS_BASE, anexo.caminho)
    try:
        caminho_disco.unlink(missing_ok=True)
    except OSError:
        logger.warning("Não foi possível remover o arquivo do anexo %s do disco", anexo_id)

    db.delete(anexo)
    db.commit()
