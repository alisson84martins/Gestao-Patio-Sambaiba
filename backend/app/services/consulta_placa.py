"""Consulta de placa do veículo de terceiro — consultadeplaca.net.

Decisões do Alisson (04/11/2026, ver PROMPT-consulta-placa-localidades):
- D2: cada consulta custa dinheiro dele. Por isso, nesta ordem, ANTES de
  chamar a API: placa fora do padrão não vai; placa já encontrada nos
  últimos `cache_dias` volta do nosso banco; sem chave não chama; limite
  diário (dia civil de São Paulo) bloqueia.
- D3: consulta ajuda, nunca trava. Esta função NÃO lança para quem chama —
  qualquer falha vira uma situação + mensagem curta, e o coordenador
  digita à mão como sempre. Nenhum salvamento de ocorrência depende dela.
- D9: roubo/furto e FIPE ficam de fora — não gravados, não devolvidos.

Grava UMA linha em coordenadoria.consulta_placa por consulta PAGA
(found / not_found). 402, 429, 503, timeout e erro de chave não gravam.

⛔ Nunca logar a chave nem o corpo cru da resposta (traz dado do veículo).
Log só com situação, status HTTP, `code` e `request_id` do fornecedor.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO, get_settings
from app.core.placa import normalizar_placa, placa_valida
from app.core.veiculo import UFS, normalizar_cor
from app.models.consulta_placa import ConsultaPlaca

logger = logging.getLogger(__name__)

# Pedido do Alisson: 10 s (o coordenador está na rua, esperando na tela).
TIMEOUT_SEGUNDOS = 10.0

# Situações devolvidas ao frontend.
ENCONTRADA = "ENCONTRADA"
NAO_ENCONTRADA = "NAO_ENCONTRADA"
PLACA_INVALIDA = "PLACA_INVALIDA"
INDISPONIVEL_SEM_CHAVE = "INDISPONIVEL_SEM_CHAVE"
LIMITE_DIARIO = "LIMITE_DIARIO"
SEM_SALDO = "SEM_SALDO"
LIMITE_FORNECEDOR = "LIMITE_FORNECEDOR"
ERRO_CHAVE = "ERRO_CHAVE"
INDISPONIVEL = "INDISPONIVEL"

MENSAGENS = {
    ENCONTRADA: "Dados da consulta preenchidos. Confira com o documento.",
    NAO_ENCONTRADA: "Placa não encontrada na base. Preencha à mão.",
    PLACA_INVALIDA: "Placa fora do padrão (AAA0000 ou AAA0A00) — não dá para consultar. Preencha à mão.",
    INDISPONIVEL_SEM_CHAVE: "Consulta de placa não configurada. Preencha à mão.",
    LIMITE_DIARIO: "Limite de consultas de hoje atingido. Preencha à mão.",
    SEM_SALDO: "Saldo da consulta acabou — avise o Alisson. Preencha à mão.",
    LIMITE_FORNECEDOR: "Muitas consultas seguidas no fornecedor. Tente de novo em instantes ou preencha à mão.",
    ERRO_CHAVE: "Consulta de placa recusada pelo fornecedor (chave) — avise o Alisson. Preencha à mão.",
    INDISPONIVEL: "Consulta de placa indisponível agora. Preencha à mão.",
}
MENSAGEM_CACHE = "Dados de uma consulta anterior desta placa. Confira com o documento."

# Campos do veículo que guardamos (D1 menos roubo/furto e FIPE — D9).
_CAMPOS_GUARDADOS = (
    "brand", "model", "version", "manufacture_year", "model_year",
    "color", "fuel", "type", "city", "state",
)

# max_length de OcorrenciaVeiculoTerceiroIn (schemas/ocorrencia.py).
_MAX_MARCA = 40
_MAX_MODELO = 60
_MAX_COR = 30
_MAX_CIDADE = 80


@dataclass
class ResultadoConsulta:
    situacao: str
    mensagem: str
    origem: Optional[str] = None  # "api" | "cache" | None (não consultou)
    dados: Optional[dict[str, Any]] = field(default=None)


def _resultado(situacao: str, **kw) -> ResultadoConsulta:
    return ResultadoConsulta(situacao=situacao, mensagem=MENSAGENS[situacao], **kw)


def _texto(valor: Any, limite: int) -> Optional[str]:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:limite] if texto else None


def _modelo_com_versao(modelo: Optional[str], versao: Optional[str]) -> Optional[str]:
    """Versão junto do modelo quando couber em 60. A versão do fornecedor
    às vezes já começa pelo modelo ("UNO" + "Uno Mille Economy…") — aí não
    repete."""
    modelo = (modelo or "").strip()
    versao = (versao or "").strip()
    if not versao:
        return _texto(modelo, _MAX_MODELO)
    if not modelo:
        return _texto(versao, _MAX_MODELO)
    junto = versao if versao.upper().startswith(modelo.upper()) else f"{modelo} {versao}"
    return junto if len(junto) <= _MAX_MODELO else modelo[:_MAX_MODELO]


def _ano(veiculo: dict[str, Any]) -> Optional[str]:
    """A máscara `ano` de mascaras.js aceita só 4 dígitos (formatarAno corta
    em 4; anoValido exige ^\\d{4}$) — "2019/2020" seria marcado inválido e
    cortado na primeira tecla. Vai o ano-modelo (o fab. só se faltar o
    modelo); os dois seguem separados em `ano_fabricacao`/`ano_modelo`.
    Ver _handoff-claude/DUVIDAS-consulta-placa.md."""
    for chave in ("model_year", "manufacture_year"):
        ano = _ano_inteiro(veiculo.get(chave))
        if ano is not None:
            return str(ano)
    return None


def _ano_inteiro(valor: Any) -> Optional[int]:
    texto = str(valor).strip() if valor is not None else ""
    return int(texto) if texto.isdigit() and len(texto) == 4 else None


def dados_formulario(veiculo: dict[str, Any]) -> dict[str, Any]:
    """Converte o bloco `vehicle` (ou o que guardamos dele) nos campos do
    card "Veículos de terceiro", respeitando os max_length do schema."""
    cor_original = _texto(veiculo.get("color"), _MAX_COR)
    uf = (veiculo.get("state") or "").strip().upper()
    return {
        "marca": _texto(veiculo.get("brand"), _MAX_MARCA),
        "modelo": _modelo_com_versao(veiculo.get("model"), veiculo.get("version")),
        "ano": _ano(veiculo),
        "ano_fabricacao": _ano_inteiro(veiculo.get("manufacture_year")),
        "ano_modelo": _ano_inteiro(veiculo.get("model_year")),
        "cor": normalizar_cor(cor_original),
        "cor_original": cor_original,
        "cidade_placa": _texto(veiculo.get("city"), _MAX_CIDADE),
        "estado_placa": uf if uf in UFS else None,
    }


def _inicio_do_dia_sp_utc(agora_utc: datetime) -> datetime:
    """00:00 de hoje em São Paulo, expresso em UTC (o banco guarda UTC)."""
    hoje_sp = agora_utc.astimezone(FUSO_OPERACAO).date()
    return datetime.combine(hoje_sp, time(0, 0), tzinfo=FUSO_OPERACAO).astimezone(timezone.utc)


def _preco(corpo: dict[str, Any]) -> Optional[Decimal]:
    try:
        valor = (corpo.get("price") or {}).get("amount")
        return Decimal(str(valor)).quantize(Decimal("0.01")) if valor is not None else None
    except (InvalidOperation, AttributeError):
        return None


def _codigo_erro(resp: httpx.Response) -> tuple[Optional[str], Optional[str]]:
    """`code` e `request_id` do problem+json — nunca o corpo inteiro."""
    try:
        corpo = resp.json()
        return corpo.get("code"), corpo.get("request_id")
    except Exception:
        return None, None


def _chamar_api(placa: str, chave: str, url_base: str) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {chave}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Novo por consulta — reaproveitar só valeria para retentar a MESMA
        # consulta, e aqui não há retentativa automática.
        "Idempotency-Key": str(uuid4()),
    }
    with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
        return client.post(f"{url_base.rstrip('/')}/lookups", json={"plate": placa}, headers=headers)


def consultar(db: Session, placa: Optional[str], funcionario_id: Optional[UUID]) -> ResultadoConsulta:
    """Consulta a placa. Nunca lança (D3). Commita só quando grava a linha
    de uma consulta paga."""
    settings = get_settings()

    # 1. Placa fora do padrão nunca vai para a API (não paga por lixo).
    placa_norm = normalizar_placa(placa)
    if not placa_valida(placa_norm):
        return _resultado(PLACA_INVALIDA)

    agora = datetime.now(timezone.utc)

    # 2. Cache: placa encontrada há menos de cache_dias volta sem pagar.
    if settings.consulta_placa_cache_dias > 0:
        limite_cache = agora - timedelta(days=settings.consulta_placa_cache_dias)
        anterior = db.execute(
            select(ConsultaPlaca)
            .where(
                ConsultaPlaca.placa == placa_norm,
                ConsultaPlaca.resultado == ENCONTRADA,
                ConsultaPlaca.consultado_em >= limite_cache,
            )
            .order_by(ConsultaPlaca.consultado_em.desc())
            .limit(1)
        ).scalar_one_or_none()
        if anterior is not None and anterior.dados:
            return ResultadoConsulta(
                situacao=ENCONTRADA, mensagem=MENSAGEM_CACHE, origem="cache",
                dados=dados_formulario(anterior.dados),
            )

    # 3. Sem chave → não há o que chamar.
    chave = (settings.consulta_placa_api_key or "").strip()
    if not chave:
        return _resultado(INDISPONIVEL_SEM_CHAVE)

    # 4. Limite diário — conta as consultas pagas de hoje (dia de SP).
    feitas_hoje = db.execute(
        select(func.count()).select_from(ConsultaPlaca)
        .where(ConsultaPlaca.consultado_em >= _inicio_do_dia_sp_utc(agora))
    ).scalar_one()
    if feitas_hoje >= settings.consulta_placa_limite_diario:
        return _resultado(LIMITE_DIARIO)

    # 5. Chama o fornecedor.
    try:
        resp = _chamar_api(placa_norm, chave, settings.consulta_placa_api_url)
    except httpx.TimeoutException:
        logger.warning("Consulta de placa: timeout de %.0f s no fornecedor.", TIMEOUT_SEGUNDOS)
        return _resultado(INDISPONIVEL)
    except Exception as exc:  # rede, DNS, TLS…
        logger.warning("Consulta de placa: falha de rede (%s).", type(exc).__name__)
        return _resultado(INDISPONIVEL)

    # 6. Mapeia a resposta.
    if resp.status_code == 201:
        try:
            corpo = resp.json()
        except Exception:
            logger.warning("Consulta de placa: 201 sem JSON válido.")
            return _resultado(INDISPONIVEL)
        resultado = corpo.get("result")
        if resultado == "found" and isinstance(corpo.get("vehicle"), dict):
            guardado = {k: corpo["vehicle"].get(k) for k in _CAMPOS_GUARDADOS}
            situacao = ENCONTRADA
        elif resultado == "not_found":
            guardado = None
            situacao = NAO_ENCONTRADA
        else:
            logger.warning("Consulta de placa: 201 com result inesperado.")
            return _resultado(INDISPONIVEL)

        db.add(ConsultaPlaca(
            placa=placa_norm, resultado=situacao, dados=guardado, preco=_preco(corpo),
            consultado_em=agora, consultado_por=funcionario_id,
        ))
        try:
            db.commit()
        except Exception:
            # A consulta já foi paga e o dado chegou — devolve mesmo assim;
            # só o cache/contagem fica sem esta linha.
            db.rollback()
            logger.exception("Consulta de placa: falha ao gravar o registro da consulta.")
        logger.info("Consulta de placa: %s (request_id=%s).", situacao, corpo.get("id"))
        return _resultado(
            situacao, origem="api",
            dados=dados_formulario(guardado) if guardado else None,
        )

    codigo, request_id = _codigo_erro(resp)
    logger.warning(
        "Consulta de placa: fornecedor respondeu %s (code=%s, request_id=%s).",
        resp.status_code, codigo, request_id,
    )
    if resp.status_code == 402:
        # daily_limit_exceeded = teto de gasto diário configurado na chave
        # (no painel do fornecedor), não falta de saldo.
        return _resultado(LIMITE_FORNECEDOR if codigo == "daily_limit_exceeded" else SEM_SALDO)
    if resp.status_code == 429:
        return _resultado(LIMITE_FORNECEDOR)
    if resp.status_code in (401, 403):
        return _resultado(ERRO_CHAVE)
    if resp.status_code == 422:
        return _resultado(PLACA_INVALIDA)
    return _resultado(INDISPONIVEL)
