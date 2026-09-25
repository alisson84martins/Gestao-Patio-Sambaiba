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
from app.routers.portaria import _dentro_e_sem_saida

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


def _where_eventos(
    modulo: Optional[str], tipo: Optional[str], busca: Optional[str],
    antes: Optional[datetime], antes_chave: Optional[str], desde: Optional[datetime],
) -> tuple[str, dict]:
    clausulas = ["momento >= :ini", "momento < :fim"]
    params: dict = {}
    if modulo:
        clausulas.append("modulo = :modulo")
        params["modulo"] = modulo
    if tipo:
        clausulas.append("tipo = :tipo")
        params["tipo"] = tipo
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


# Contadores da faixa do topo: só o número do período, por tipo. Nada de
# comparação, ranking ou agregação analítica — o painel é visualização
# (decisão do Alisson, 25/09); a análise fica com a gerência.
_CONTADORES = {
    "entradas": "ENTRADA",
    "saidas": "SAIDA",
    "recolhidas": "RECOLHIDA",
    "avarias": "AVARIA",
    "alocacoes": "ALOCACAO",
    "movimentacoes": "MOVIMENTACAO",
    "retiradas": "RETIRADA",
}

_SQL_CONTADORES = text("""
    SELECT tipo, count(*) AS n
      FROM public.vw_painel_evento
     WHERE momento >= :ini AND momento < :fim
       AND tipo IN ('ENTRADA','SAIDA','RECOLHIDA','AVARIA','ALOCACAO','MOVIMENTACAO','RETIRADA')
     GROUP BY tipo
""")


def _consultar_contagens(db: Session, ini: datetime, fim: datetime) -> dict:
    """{tipo: quantidade} no período."""
    return {r["tipo"]: r["n"] for r in db.execute(_SQL_CONTADORES, {"ini": ini, "fim": fim}).mappings()}


def montar_contadores(contagens: dict) -> dict:
    return {nome: int(contagens.get(tipo, 0)) for nome, tipo in _CONTADORES.items()}


def _contar_dentro_agora(db: Session) -> int:
    # Regra D18 da Portaria — reusada, nunca duplicada aqui.
    dentro, _sem_saida = _dentro_e_sem_saida(db, HORAS_DENTRO)
    return len(dentro)


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
    tipo: Optional[Tipo] = None,
    busca: Optional[str] = Query(None, max_length=60),
    antes: Optional[datetime] = Query(None, description="Cursor 'carregar mais': momento < antes"),
    antes_chave: Optional[str] = Query(None, max_length=60, description="TIPO:id do último evento visto"),
    desde: Optional[datetime] = Query(None, description="Polling: momento > desde"),
    limit: int = Query(100, ge=1, le=500),
):
    p = _periodo(db, de, ate)
    where, params = _where_eventos(modulo, tipo, busca, antes, antes_chave, desde)
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
    return {
        "periodo": p,
        "contadores": montar_contadores(_consultar_contagens(db, p["inicio"], p["fim"])),
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
    tipo: Optional[Tipo] = None,
    busca: Optional[str] = Query(None, max_length=60),
):
    p = _periodo(db, de, ate)
    where, params = _where_eventos(modulo, tipo, busca, None, None, None)
    # Lê tudo ANTES de responder: a sessão do get_db não pode ficar presa ao
    # streaming (o gerador roda depois que a dependência já saiu).
    linhas = _consultar_eventos(db, p["inicio"], p["fim"], where, params, LIMITE_CSV)
    nome = f"painel-gerencial_{p['de'].isoformat()}_{p['ate'].isoformat()}.csv"
    return StreamingResponse(
        gerar_csv(linhas),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
