"""Painel Gerencial — tudo que a Portaria e o Pátio registram, ao vivo e
histórico. SÓ LEITURA (migration 046, view public.vw_painel_evento).

Quem vê: ADMIN, GERENTE_GERAL, GERENTE_OPERACIONAL, ENCARREGADO — recurso
`painel_gerencial`, próprio, de propósito: COORDENADOR_TRAFEGO tem
`relatorios` e NÃO pode ver o painel.

Regras que atravessam o arquivo:
  · Toda rota com exige("painel_gerencial"). Nenhuma escrita, nenhum router
    da Portaria/Pátio alterado — o painel só lê o que já está gravado.
  · Dia = dia do RELÓGIO em São Paulo (00:00–23:59), filtrado pelo INSTANTE
    do evento (`momento`). ⛔ Nunca get_data_servico() (depois das 20h vira
    amanhã), nunca data_referencia, nunca date.today().
  · SQL por text() com bind — os trechos opcionais do WHERE são fixos
    (escolhidos por código), o que vem do usuário só entra como parâmetro.
  · A chave de um evento é (tipo, id): o id da view é o da linha de origem e
    se repete entre tipos (ver cabeçalho da 046).
"""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Callable, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.core.database import get_db
from app.core.deps import exige
from app.models.cadastro import Funcionario
from app.routers.portaria import _dentro_e_sem_saida, _enriquecer_dentro

router = APIRouter(prefix="/painel-gerencial", tags=["painel gerencial"])

Leitura = Annotated[Funcionario, Depends(exige("painel_gerencial"))]
DbSession = Annotated[Session, Depends(get_db)]

MAX_DIAS = 400
LIMITE_CSV = 100_000
# Mesmo limite da tela da Portaria (routers/portaria.py::dentro_agora).
HORAS_DENTRO = 36

Modulo = Literal["PORTARIA", "PATIO"]
Tipo = Literal[
    "ENTRADA", "SAIDA", "RECOLHIDA", "RECOLHIDA_AVALIADA", "RECOLHIDA_ENCERRADA",
    "AVARIA", "AVARIA_REVISTA", "AVARIA_CONTESTADA", "AVARIA_ENCERRADA", "VEICULO_SITUACAO",
    "ALOCACAO", "MOVIMENTACAO", "RETIRADA",
]

# Grupos = os chips da tela (26/09). UM lugar só: o filtro (`grupo` em
# /eventos e /exportar.csv) e os contadores do /resumo leem daqui, para que
# o número do chip seja sempre a quantidade de linhas que ele filtra (regra
# de ouro). VEICULO_SITUACAO não tem chip — aparece só sem filtro.
GRUPOS: dict[str, tuple[str, ...]] = {
    "ENTRADAS": ("ENTRADA",),
    "SAIDAS": ("SAIDA",),
    "RA": ("RECOLHIDA", "RECOLHIDA_AVALIADA", "RECOLHIDA_ENCERRADA"),
    "AVARIAS": ("AVARIA", "AVARIA_REVISTA", "AVARIA_CONTESTADA", "AVARIA_ENCERRADA"),
    "ALOCACOES": ("ALOCACAO",),
    "MOVIMENTACOES": ("MOVIMENTACAO",),
    "RETIRADAS": ("RETIRADA",),
}
MODULO_DO_GRUPO = {
    "ENTRADAS": "PORTARIA", "SAIDAS": "PORTARIA", "RA": "PORTARIA", "AVARIAS": "PORTARIA",
    "ALOCACOES": "PATIO", "MOVIMENTACOES": "PATIO", "RETIRADAS": "PATIO",
}
# Só estes aceitam sub-chip de categoria (D1).
GRUPOS_COM_CATEGORIA = ("ENTRADAS", "SAIDAS")

Grupo = Literal["ENTRADAS", "SAIDAS", "RA", "AVARIAS", "ALOCACOES", "MOVIMENTACOES", "RETIRADAS"]
# Categorias do movimento da Portaria, as da view 046, na ordem de
# precedência do CASE: RESERVADO → FROTA_APOIO → TERCEIRO → FUNCIONARIO.
Categoria = Literal["FUNCIONARIO", "FROTA_APOIO", "TERCEIRO", "RESERVADO"]
CATEGORIAS: tuple[str, ...] = ("FUNCIONARIO", "FROTA_APOIO", "TERCEIRO", "RESERVADO")

_COLUNAS = (
    "id", "momento", "modulo", "tipo", "categoria", "identificacao", "detalhe",
    "pessoa_re", "pessoa_nome", "autor_re", "autor_nome",
)


# ============================================================================
# Período — funções puras (testadas sem banco)
# ============================================================================

def hoje_sp(agora: Optional[datetime] = None) -> date:
    """Hoje no relógio da garagem. `agora` só para teste."""
    return (agora or datetime.now(timezone.utc)).astimezone(FUSO_OPERACAO).date()


def intervalo_utc(de: date, ate: date) -> tuple[datetime, datetime]:
    """[de 00:00, ate+1 00:00) em São Paulo, devolvido em UTC.
    Filtro: momento >= inicio AND momento < fim."""
    inicio = datetime.combine(de, datetime.min.time(), tzinfo=FUSO_OPERACAO)
    fim = datetime.combine(ate + timedelta(days=1), datetime.min.time(), tzinfo=FUSO_OPERACAO)
    return inicio.astimezone(timezone.utc), fim.astimezone(timezone.utc)


def _parse_dia(valor: str, campo: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise HTTPException(422, f"'{campo}' inválido: use AAAA-MM-DD")


def resolver_periodo(
    de: Optional[str], ate: Optional[str], hoje: date,
    primeiro_dia: Callable[[], Optional[date]],
) -> tuple[date, date]:
    """Padrão: de = ate = hoje. `ate` no futuro trava em hoje. de > ate → 422.
    Mais de MAX_DIAS → 422. de = "inicio" → primeiro dia com registro
    ("desde sempre" — não passa pelo teto de MAX_DIAS: é o botão da tela,
    não um intervalo digitado)."""
    dia_ate = _parse_dia(ate, "ate") if ate else hoje
    if dia_ate > hoje:
        dia_ate = hoje

    if de == "inicio":
        dia_de = min(primeiro_dia() or dia_ate, dia_ate)
        return dia_de, dia_ate

    dia_de = _parse_dia(de, "de") if de else dia_ate
    if dia_de > dia_ate:
        raise HTTPException(422, "'de' não pode ser depois de 'ate'")
    if (dia_ate - dia_de).days + 1 > MAX_DIAS:
        raise HTTPException(422, f"Período maior que {MAX_DIAS} dias")
    return dia_de, dia_ate


def formatar_momento_csv(momento: datetime) -> str:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(FUSO_OPERACAO).strftime("%d/%m/%Y %H:%M")


# ============================================================================
# Consultas — isoladas para os testes trocarem (sem PostgreSQL no CI)
# ============================================================================

def _primeiro_registro(db: Session) -> dict:
    """Menor instante gravado por módulo — direto nas tabelas de origem
    (índices por momento), sem varrer a view."""
    row = db.execute(text("""
        SELECT LEAST(
                 (SELECT min(momento)   FROM portaria.movimento),
                 (SELECT min(momento)   FROM portaria.recolhida_anormal),
                 (SELECT min(criado_em) FROM portaria.avaria_saida)
               ) AS portaria,
               (SELECT min(alocado_em) FROM public.alocacao_patio) AS patio
    """)).mappings().one()
    return {"portaria": row["portaria"], "patio": row["patio"]}


def _primeiro_dia(db: Session) -> Optional[date]:
    reg = _primeiro_registro(db)
    momentos = [m for m in (reg["portaria"], reg["patio"]) if m is not None]
    if not momentos:
        return None
    return min(momentos).astimezone(FUSO_OPERACAO).date()


def validar_filtro(
    modulo: Optional[str], tipo: Optional[str], grupo: Optional[str], categoria: Optional[str],
) -> None:
    """422 para combinação sem sentido — melhor recusar do que devolver uma
    lista que não bate com chip nenhum."""
    if grupo and tipo:
        raise HTTPException(422, "Use 'grupo' ou 'tipo', não os dois")
    if grupo and modulo and MODULO_DO_GRUPO[grupo] != modulo:
        raise HTTPException(422, f"O grupo {grupo} não é do módulo {modulo}")
    if categoria and grupo not in GRUPOS_COM_CATEGORIA:
        raise HTTPException(422, "'categoria' só vale com grupo ENTRADAS ou SAIDAS")


def _in_bind(coluna: str, prefixo: str, valores: tuple[str, ...]) -> tuple[str, dict]:
    """`coluna IN (:p0, :p1, ...)` — os NOMES dos parâmetros são escolhidos
    aqui; os valores só entram como bind."""
    nomes = [f"{prefixo}{i}" for i in range(len(valores))]
    return f"{coluna} IN ({', '.join(':' + n for n in nomes)})", dict(zip(nomes, valores))


def _where_eventos(
    modulo: Optional[str], tipo: Optional[str], busca: Optional[str],
    antes: Optional[datetime], antes_chave: Optional[str], desde: Optional[datetime],
    grupo: Optional[str] = None, categoria: Optional[str] = None,
) -> tuple[str, dict]:
    clausulas = ["momento >= :ini", "momento < :fim"]
    params: dict = {}
    if modulo:
        clausulas.append("modulo = :modulo")
        params["modulo"] = modulo
    if tipo:
        clausulas.append("tipo = :tipo")
        params["tipo"] = tipo
    if grupo:
        clausula, binds = _in_bind("tipo", "grupo_tipo", GRUPOS[grupo])
        clausulas.append(clausula)
        params.update(binds)
    if categoria:
        clausulas.append("categoria = :categoria")
        params["categoria"] = categoria
    if busca and busca.strip():
        termo = busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["busca"] = f"%{termo}%"
        clausulas.append(
            "(identificacao ILIKE :busca OR pessoa_re ILIKE :busca OR pessoa_nome ILIKE :busca"
            " OR autor_re ILIKE :busca OR autor_nome ILIKE :busca)"
        )
    if antes is not None:
        # Cursor composto: "limpar tudo" do Pátio grava centenas de RETIRADA
        # no MESMO instante — só momento < antes pularia o resto delas.
        if antes_chave and ":" in antes_chave:
            antes_tipo, antes_id = antes_chave.split(":", 1)
            try:
                antes_id = UUID(antes_id)
            except ValueError:
                raise HTTPException(422, "'antes_chave' inválido: use TIPO:id")
            clausulas.append("(momento, tipo, id) < (:antes, :antes_tipo, :antes_id)")
            params.update(antes_tipo=antes_tipo, antes_id=antes_id)
        else:
            clausulas.append("momento < :antes")
        params["antes"] = antes
    if desde is not None:
        clausulas.append("momento > :desde")
        params["desde"] = desde
    return " AND ".join(clausulas), params


def _consultar_eventos(db: Session, ini: datetime, fim: datetime, where: str, params: dict, limit: int) -> list[dict]:
    sql = text(
        f"SELECT {', '.join(_COLUNAS)} FROM public.vw_painel_evento WHERE {where} "
        "ORDER BY momento DESC, tipo DESC, id DESC LIMIT :limit"
    )
    linhas = db.execute(sql, {**params, "ini": ini, "fim": fim, "limit": limit}).mappings().all()
    return [dict(l) for l in linhas]


# Contadores da faixa do topo: só o número do período, por GRUPO (os
# chips). Nada de comparação, ranking ou agregação analítica — o painel é
# visualização (decisão do Alisson, 25/09); a análise fica com a gerência.
_CONTADORES = {
    "entradas": "ENTRADAS",
    "saidas": "SAIDAS",
    "ra": "RA",
    "avarias": "AVARIAS",
    "alocacoes": "ALOCACOES",
    "movimentacoes": "MOVIMENTACOES",
    "retiradas": "RETIRADAS",
}
# Contador que tem sub-chip de categoria → o tipo que ele conta.
_CATEGORIAS_DO_CONTADOR = {"entradas": "ENTRADA", "saidas": "SAIDA"}


def _consultar_contagens(db: Session, ini: datetime, fim: datetime) -> list[dict]:
    """[{tipo, categoria, n}] no período. A categoria só é separada em
    ENTRADA/SAIDA (os que têm sub-chip); nos outros tipos vem NULL."""
    todos = tuple(t for tipos in GRUPOS.values() for t in tipos)
    clausula, binds = _in_bind("tipo", "t", todos)
    sql = text(f"""
        SELECT tipo,
               CASE WHEN tipo IN ('ENTRADA', 'SAIDA') THEN categoria END AS categoria,
               count(*) AS n
          FROM public.vw_painel_evento
         WHERE momento >= :ini AND momento < :fim AND {clausula}
         GROUP BY 1, 2
    """)
    return [dict(r) for r in db.execute(sql, {**binds, "ini": ini, "fim": fim}).mappings()]


def montar_contadores(contagens: list[dict]) -> dict:
    """Soma os tipos de cada grupo — o MESMO GRUPOS do filtro."""
    por_tipo: dict[str, int] = {}
    for c in contagens:
        por_tipo[c["tipo"]] = por_tipo.get(c["tipo"], 0) + int(c["n"])
    return {nome: sum(por_tipo.get(t, 0) for t in GRUPOS[grupo]) for nome, grupo in _CONTADORES.items()}


def montar_categorias(contagens: list[dict]) -> dict:
    """Sempre as 4 chaves, zero quando não houver (D1)."""
    saida = {nome: dict.fromkeys(CATEGORIAS, 0) for nome in _CATEGORIAS_DO_CONTADOR}
    for nome, tipo in _CATEGORIAS_DO_CONTADOR.items():
        for c in contagens:
            if c["tipo"] == tipo and c["categoria"] in saida[nome]:
                saida[nome][c["categoria"]] += int(c["n"])
    return saida


# ─── Dentro agora (D2) ───────────────────────────────────────────────────────
# Regra D18 da Portaria — reusada, nunca duplicada aqui. A MESMA lista dá o
# número do chip e as linhas da tabela (regra de ouro).

def _dentro(db: Session) -> list:
    dentro, _sem_saida = _dentro_e_sem_saida(db, HORAS_DENTRO)
    return dentro


def _contar_dentro_agora(db: Session) -> int:
    return len(_dentro(db))


def categoria_movimento(
    prefixo: Optional[str], propriedade: Optional[str],
    terceiro_nome: Optional[str], terceiro_empresa: Optional[str],
) -> str:
    """O mesmo CASE da view 046: RESERVADO → FROTA_APOIO → TERCEIRO → FUNCIONARIO.
    (RESERVADO e FROTA_APOIO não chegam aqui hoje — a D18 os tira do
    "dentro" — mas o critério fica igual ao da view de propósito.)"""
    if prefixo:
        return "RESERVADO"
    if propriedade == "EMPRESA":
        return "FROTA_APOIO"
    if propriedade == "TERCEIRO" or terceiro_nome or terceiro_empresa:
        return "TERCEIRO"
    return "FUNCIONARIO"


def _listar_dentro_agora(db: Session) -> list[dict]:
    """Uma linha por veículo dentro, mais recente primeiro (como a Portaria).
    _enriquecer_dentro faz 1 query de veículo + 1 de setor, nunca N+1."""
    return [
        {
            "id": m.id,
            "entrou_em": m.momento,
            "identificacao": m.prefixo or m.placa_registrada,
            "categoria": categoria_movimento(m.prefixo, m.veiculo_propriedade, m.terceiro_nome, m.terceiro_empresa),
            "pessoa_re": m.re_registrado,
            "pessoa_nome": m.nome_registrado or m.terceiro_nome,
            "detalhe": " · ".join(
                x for x in (m.marca_modelo, m.terceiro_empresa, m.terceiro_destino, m.setor_nome) if x
            ) or None,
        }
        for m in _enriquecer_dentro(db, _dentro(db))
    ]


def filtrar_dentro(linhas: list[dict], busca: Optional[str]) -> list[dict]:
    """Mesma busca da lista de registros: placa/prefixo, RE ou nome."""
    termo = (busca or "").strip().casefold()
    if not termo:
        return linhas
    return [
        l for l in linhas
        if any(termo in (l[c] or "").casefold() for c in ("identificacao", "pessoa_re", "pessoa_nome"))
    ]


# ============================================================================
# Rotas
# ============================================================================

def _periodo(db: Session, de: Optional[str], ate: Optional[str]) -> dict:
    hoje = hoje_sp()
    dia_de, dia_ate = resolver_periodo(de, ate, hoje, lambda: _primeiro_dia(db))
    ini, fim = intervalo_utc(dia_de, dia_ate)
    return {"de": dia_de, "ate": dia_ate, "inicio": ini, "fim": fim, "inclui_hoje": dia_ate == hoje}


@router.get("/eventos", summary="Linha do tempo unificada (Portaria + Pátio), mais novo primeiro")
def eventos(
    _usuario: Leitura,
    db: DbSession,
    de: Optional[str] = Query(None, description="AAAA-MM-DD ou 'inicio'"),
    ate: Optional[str] = Query(None, description="AAAA-MM-DD"),
    modulo: Optional[Modulo] = None,
    tipo: Optional[Tipo] = Query(None, description="Legado — a tela usa 'grupo'"),
    grupo: Optional[Grupo] = Query(None, description="Chip da tela: vira tipo IN (...)"),
    categoria: Optional[Categoria] = Query(None, description="Sub-chip: só com grupo ENTRADAS ou SAIDAS"),
    busca: Optional[str] = Query(None, max_length=60),
    antes: Optional[datetime] = Query(None, description="Cursor 'carregar mais': momento < antes"),
    antes_chave: Optional[str] = Query(None, max_length=60, description="TIPO:id do último evento visto"),
    desde: Optional[datetime] = Query(None, description="Polling: momento > desde"),
    limit: int = Query(100, ge=1, le=500),
):
    validar_filtro(modulo, tipo, grupo, categoria)
    p = _periodo(db, de, ate)
    where, params = _where_eventos(modulo, tipo, busca, antes, antes_chave, desde, grupo, categoria)
    linhas = _consultar_eventos(db, p["inicio"], p["fim"], where, params, limit + 1)
    return {"periodo": p, "eventos": linhas[:limit], "tem_mais": len(linhas) > limit}


@router.get("/resumo", summary="Contadores do período para a faixa do topo")
def resumo(
    _usuario: Leitura,
    db: DbSession,
    de: Optional[str] = Query(None, description="AAAA-MM-DD ou 'inicio'"),
    ate: Optional[str] = Query(None, description="AAAA-MM-DD"),
):
    p = _periodo(db, de, ate)
    contagens = _consultar_contagens(db, p["inicio"], p["fim"])
    return {
        "periodo": p,
        "contadores": montar_contadores(contagens),
        "categorias": montar_categorias(contagens),
        "dentro_agora": _contar_dentro_agora(db) if p["inclui_hoje"] else None,
        "primeiro_registro": _primeiro_registro(db),
    }


_CABECALHO_CSV = (
    "Data e hora", "Módulo", "Tipo", "Categoria", "Placa/Prefixo", "Detalhe",
    "RE envolvido", "Nome envolvido", "RE de quem registrou", "Quem registrou",
)


def gerar_csv(linhas: list[dict]):
    """UTF-8 com BOM e ';' — o Excel brasileiro abre com acento e colunas certas."""
    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";", lineterminator="\r\n")

    def _descarrega() -> str:
        valor = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return valor

    escritor.writerow(_CABECALHO_CSV)
    yield "﻿" + _descarrega()
    for i, l in enumerate(linhas, start=1):
        escritor.writerow((
            formatar_momento_csv(l["momento"]), l["modulo"], l["tipo"], l["categoria"] or "",
            l["identificacao"] or "", l["detalhe"] or "", l["pessoa_re"] or "", l["pessoa_nome"] or "",
            l["autor_re"] or "",
            l["autor_nome"] or ("Limpeza geral do pátio" if l["tipo"] == "RETIRADA" else ""),
        ))
        if i % 1000 == 0:
            yield _descarrega()
    yield _descarrega()


@router.get("/exportar.csv", summary="Exporta a linha do tempo filtrada em CSV (Excel)")
def exportar_csv(
    _usuario: Leitura,
    db: DbSession,
    de: Optional[str] = Query(None, description="AAAA-MM-DD ou 'inicio'"),
    ate: Optional[str] = Query(None, description="AAAA-MM-DD"),
    modulo: Optional[Modulo] = None,
    tipo: Optional[Tipo] = Query(None, description="Legado — a tela usa 'grupo'"),
    grupo: Optional[Grupo] = None,
    categoria: Optional[Categoria] = None,
    busca: Optional[str] = Query(None, max_length=60),
):
    validar_filtro(modulo, tipo, grupo, categoria)
    p = _periodo(db, de, ate)
    where, params = _where_eventos(modulo, tipo, busca, None, None, None, grupo, categoria)
    # Lê tudo ANTES de responder: a sessão do get_db não pode ficar presa ao
    # streaming (o gerador roda depois que a dependência já saiu).
    linhas = _consultar_eventos(db, p["inicio"], p["fim"], where, params, LIMITE_CSV)
    nome = f"painel-gerencial_{p['de'].isoformat()}_{p['ate'].isoformat()}.csv"
    return StreamingResponse(
        gerar_csv(linhas),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ─── Dentro agora (D2) ───────────────────────────────────────────────────────
# O CSV fica em rota própria (/dentro-agora.csv), no mesmo estilo de
# /exportar.csv. Não depende do período: é o retrato de agora (a tela só
# mostra o chip quando o período inclui hoje, como o contador).

@router.get("/dentro-agora", summary="Veículos dentro da garagem agora (mesma regra da Portaria, D18)")
def dentro_agora(
    _usuario: Leitura,
    db: DbSession,
    busca: Optional[str] = Query(None, max_length=60),
):
    linhas = filtrar_dentro(_listar_dentro_agora(db), busca)
    return {"dentro": linhas, "total": len(linhas), "horas": HORAS_DENTRO}


def formatar_permanencia(entrou_em: datetime, agora: datetime) -> str:
    if entrou_em.tzinfo is None:
        entrou_em = entrou_em.replace(tzinfo=timezone.utc)
    minutos = max(0, int((agora - entrou_em).total_seconds() // 60))
    horas, resto = divmod(minutos, 60)
    return f"{horas} h {resto:02d} min" if horas else f"{resto} min"


def gerar_csv_dentro(linhas: list[dict], agora: datetime):
    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    escritor.writerow(("Entrou em", "Placa/Prefixo", "Categoria", "RE", "Nome", "Há quanto tempo", "Detalhe"))
    for l in linhas:
        escritor.writerow((
            formatar_momento_csv(l["entrou_em"]), l["identificacao"] or "", l["categoria"],
            l["pessoa_re"] or "", l["pessoa_nome"] or "", formatar_permanencia(l["entrou_em"], agora),
            l["detalhe"] or "",
        ))
    yield "﻿" + buf.getvalue()


@router.get("/dentro-agora.csv", summary="Exporta a lista de dentro agora em CSV (Excel)")
def dentro_agora_csv(
    _usuario: Leitura,
    db: DbSession,
    busca: Optional[str] = Query(None, max_length=60),
):
    agora = datetime.now(timezone.utc)
    linhas = filtrar_dentro(_listar_dentro_agora(db), busca)
    nome = f"painel-dentro-agora_{hoje_sp(agora).isoformat()}.csv"
    return StreamingResponse(
        gerar_csv_dentro(linhas, agora),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
