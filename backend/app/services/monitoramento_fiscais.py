"""Fiscal identificado pelo NIMER (v4.0 fase 3, bloco A).

O robô lê a Escala de Fiscais do NIMER (/Departure, Função = Fiscal) e manda
cada cartão: pegada, largada, Local, tabela, RE e as batidas de início/fim
(a de início é COM FOTO). Aqui:

- `gravar_escala`: upsert em monitoramento.nimer_fiscal_escala por
  (data de serviço, RE, tabela).
- ⚠️ FUSO: pegada/largada/início/fim são HORA DE PAREDE DE SÃO PAULO que o
  NIMER mostra → `FUSO_OPERACAO` → instante (TIMESTAMPTZ). Início e fim só
  trazem a hora: a data é a da pegada; se a hora for menor que a da pegada
  menos 6 h, é o dia seguinte (pegada 23:00, início 00:10 → dia seguinte).
  ⛔ Nunca o relógio do processo (o servidor roda em UTC).
- `pontos_do_local`: Local do NIMER → linhas + terminal (monitoramento.
  ponto_nimer). O Local é normalizado (minúsculo, sem acento, espaços
  únicos) e casa pelo COMEÇO do texto — vale o cadastro mais longo que casar
  ("metro santana 178t/10 - 1726/10 …" casa com "metro santana").
- `escopo_pelo_nimer` (chamado por monitoramento_acesso.escopo para quem não
  tem turno aberto):
    * batida de início e sem batida de fim → ABRE O TURNO SOZINHO
      (origem NIMER) com as linhas do ponto;
    * sem batida de início e faltando ≤ 30 min para a pegada (ou a pegada
      já passou, até a largada) → escopo `previsto` (só leitura);
    * batida de fim, ou o turno daquele período já foi fechado → não abre
      de novo no mesmo dia;
    * nada do NIMER → None (o fluxo manual de "Abrir turno" continua).
  RE do NIMER ↔ funcionario.re comparados como TEXTO (pode ter letra).
"""
import logging
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import FUSO_OPERACAO
from app.models.cadastro import Funcionario
from app.models.fiscalizacao import Turno, TurnoLinha
from app.models.monitoramento import NimerFiscalEscala, PontoNimer

logger = logging.getLogger(__name__)

LIMITE_FISCAIS = 500
VIRADA_HORA_INICIO = timedelta(hours=6)   # hora < pegada − 6 h = dia seguinte
ANTECEDENCIA_PREVISTO = timedelta(minutes=30)
CORTE_PERIODO = time(12, 0)               # pegada antes do meio-dia = 1º período (ver RESULTADO: dúvida)
ORIGEM_NIMER = "NIMER"


# ─── Texto ───────────────────────────────────────────────────────────────────

def normalizar_local(texto: Optional[str]) -> str:
    """Minúsculo, sem acento, espaços únicos (mesmo espírito de normalizar_motivo)."""
    from app.services.fiscalizacao_tempo_real import _sem_acento
    return re.sub(r"\s+", " ", _sem_acento(texto or "").lower()).strip()


def _texto(valor: Any, n: int) -> Optional[str]:
    if valor is None:
        return None
    t = str(valor).strip()
    return t[:n] or None


# ─── Fuso ────────────────────────────────────────────────────────────────────

_RE_DATA_HORA = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*$")
_RE_HORA = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$")


def parede_para_instante(texto: Optional[str], ano_padrao: int) -> Optional[datetime]:
    """"11/11/2026 14:00" (ou "11/11 14:00") de São Paulo → instante UTC. None se não der."""
    m = _RE_DATA_HORA.match(texto or "")
    if not m:
        return None
    dia, mes, ano, hh, mm = m.groups()
    ano = int(ano) if ano else ano_padrao
    if ano < 100:
        ano += 2000
    try:
        local = datetime(ano, int(mes), int(dia), int(hh), int(mm), tzinfo=FUSO_OPERACAO)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def hora_apos_pegada(texto: Optional[str], pegada: Optional[datetime]) -> Optional[datetime]:
    """"14:13" → instante, na data (SP) da pegada; antes de pegada − 6 h = dia seguinte."""
    m = _RE_HORA.match(texto or "")
    if not m or pegada is None:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    pegada_local = pegada.astimezone(FUSO_OPERACAO)
    local = datetime.combine(pegada_local.date(), time(hh, mm), tzinfo=FUSO_OPERACAO)
    if local < pegada_local - VIRADA_HORA_INICIO:
        local = datetime.combine(pegada_local.date() + timedelta(days=1), time(hh, mm), tzinfo=FUSO_OPERACAO)
    return local.astimezone(timezone.utc)


# ─── Recebimento ─────────────────────────────────────────────────────────────

def cartao_da_escala(bruto: Any, data_servico: date) -> Optional[dict]:
    """Cartão do robô → colunas de nimer_fiscal_escala (None = descarta)."""
    if not isinstance(bruto, dict):
        return None
    re_ = _texto(bruto.get("re"), 20)
    if not re_:
        return None
    pegada = parede_para_instante(_texto(bruto.get("pegada"), 40), data_servico.year)
    largada = parede_para_instante(_texto(bruto.get("largada"), 40), data_servico.year)
    return {
        "data_servico": data_servico,
        "re": re_,
        "tabela": _texto(bruto.get("tabela"), 20) or "",
        "pegada_em": pegada,
        "largada_em": largada,
        "local_texto": _texto(bruto.get("local"), 200),
        "inicio_em": hora_apos_pegada(_texto(bruto.get("inicio"), 10), pegada),
        "fim_em": hora_apos_pegada(_texto(bruto.get("fim"), 10), pegada),
    }


def gravar_escala(db: Session, data_servico: date, fiscais: list[Any], coletado_em: Optional[datetime],
                  agora: datetime) -> dict:
    """Upsert por (data de serviço, RE, tabela). Não faz commit."""
    gravados = 0
    vistos: set[tuple] = set()
    for bruto in fiscais:
        cartao = cartao_da_escala(bruto, data_servico)
        if cartao is None:
            continue
        chave = (cartao["data_servico"], cartao["re"], cartao["tabela"])
        if chave in vistos:
            continue
        vistos.add(chave)
        existente = db.execute(select(NimerFiscalEscala).where(
            NimerFiscalEscala.data_servico == chave[0], NimerFiscalEscala.re == chave[1],
            NimerFiscalEscala.tabela == chave[2])).scalar_one_or_none()
        if existente is None:
            existente = NimerFiscalEscala(**cartao)
            db.add(existente)
        else:
            for campo, valor in cartao.items():
                setattr(existente, campo, valor)
        existente.coletado_em = coletado_em
        existente.atualizado_em = agora
        gravados += 1
    return {"data_servico": data_servico.isoformat(), "recebidos": len(fiscais), "gravados": gravados}


# ─── Local → linhas ──────────────────────────────────────────────────────────

def pontos_do_local(db: Session, local_texto: Optional[str]) -> list[tuple[str, str]]:
    """[(linha, terminal)] do cadastro mais longo cujo texto é o começo do Local."""
    local = normalizar_local(local_texto)
    if not local:
        return []
    cadastros = db.execute(select(PontoNimer).where(PontoNimer.ativo.is_(True))).scalars().all()
    casados = [p for p in cadastros if local.startswith(normalizar_local(p.local_texto))]
    if not casados:
        return []
    maior = max(len(normalizar_local(p.local_texto)) for p in casados)
    return sorted({(p.linha_codigo, p.terminal) for p in casados if len(normalizar_local(p.local_texto)) == maior})


def nome_do_local(local_texto: Optional[str]) -> str:
    """"metro santana 178t/10 - 1726/10" → "metro santana" (antes da 1ª linha)."""
    texto = (local_texto or "").strip()
    m = re.search(r"\s\d{3,4}[a-zA-Z]?/\d{2}", texto)
    return (texto[:m.start()] if m else texto).strip()


# ─── Turno automático ────────────────────────────────────────────────────────

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def periodo_da_pegada(pegada: Optional[datetime]) -> str:
    if pegada is None:
        return "1"
    return "1" if pegada.astimezone(FUSO_OPERACAO).time() < CORTE_PERIODO else "2"


def _terminal(pontos: list[tuple[str, str]]) -> str:
    contagem = Counter(t for _, t in pontos)
    return sorted(contagem.items(), key=lambda x: (-x[1], x[0]))[0][0]


def previsto_info(escala: NimerFiscalEscala, pontos: list[tuple[str, str]]) -> dict:
    pegada = _utc(escala.pegada_em)
    nome = nome_do_local(escala.local_texto) or "o ponto"
    hora = pegada.astimezone(FUSO_OPERACAO).strftime("%H:%M") if pegada else "—"
    return {
        "re": escala.re,
        "local": escala.local_texto,
        "local_nome": nome,
        "terminal": _terminal(pontos),
        "pegada_em": pegada.isoformat() if pegada else None,
        "pegada": hora,
        "texto": f"Previsto: {nome} às {hora} · aguardando foto de início no NIMER",
    }


def _abrir_turno(db: Session, funcionario: Funcionario, escala: NimerFiscalEscala, pontos, periodo: str,
                 data_servico: date) -> Optional[Turno]:
    from app.services.monitoramento_eta import tipo_dia
    turno = Turno(
        funcionario_id=funcionario.id, fiscal_re=funcionario.re, ponto_codigo=None,
        ponto_nome=(nome_do_local(escala.local_texto) or escala.local_texto or "")[:160] or None,
        terminal=_terminal(pontos), periodo=periodo, data_referencia=data_servico,
        tipo_dia=tipo_dia(data_servico), status="ABERTO", aberto_em=_utc(escala.inicio_em),
        origem=ORIGEM_NIMER,
    )
    try:
        db.add(turno)
        db.flush()
        for linha in sorted({linha for linha, _ in pontos}):
            db.add(TurnoLinha(turno_id=turno.id, linha_codigo=linha))
        db.commit()
    except IntegrityError:
        # Outra requisição abriu o mesmo turno ao mesmo tempo (índice de
        # turno aberto por pessoa/período/dia): vale o que já está lá.
        db.rollback()
        logger.info("Turno automático do NIMER já aberto por outra requisição (RE %s)", funcionario.re)
        return None
    return turno


def escopo_pelo_nimer(db: Session, funcionario_id: UUID, agora: Optional[datetime] = None) -> Optional[dict]:
    """Escopo vindo da escala de fiscais do NIMER, ou None (fluxo manual)."""
    from app.services.monitoramento_cadastro import data_servico_de

    agora = agora or datetime.now(timezone.utc)
    funcionario = db.get(Funcionario, funcionario_id)
    re_ = (getattr(funcionario, "re", None) or "").strip()
    if not re_:
        return None
    data_servico = data_servico_de(agora)
    escalas = db.execute(
        select(NimerFiscalEscala)
        .where(NimerFiscalEscala.re == re_, NimerFiscalEscala.data_servico == data_servico)
        .order_by(NimerFiscalEscala.pegada_em, NimerFiscalEscala.id)
    ).scalars().all()
    for escala in escalas:
        if escala.fim_em is not None:
            continue
        pontos = pontos_do_local(db, escala.local_texto)
        if not pontos:
            continue
        linhas = sorted({linha for linha, _ in pontos})
        pegada = _utc(escala.pegada_em)
        if escala.inicio_em is not None:
            periodo = periodo_da_pegada(pegada or _utc(escala.inicio_em))
            ja_teve = db.execute(select(Turno.id).where(
                Turno.funcionario_id == funcionario.id, Turno.data_referencia == data_servico,
                Turno.periodo == periodo)).first()
            if ja_teve is not None:
                continue  # turno do período já foi fechado: não abre de novo no mesmo dia
            turno = _abrir_turno(db, funcionario, escala, pontos, periodo, data_servico)
            if turno is None:
                from app.services.monitoramento_acesso import linhas_do_turno, turno_aberto
                turno = turno_aberto(db, funcionario.id)
                if turno is None:
                    continue
                linhas = linhas_do_turno(db, turno.id)
            return {"tipo": "turno", "linhas": linhas, "turno": turno}
        largada = _utc(escala.largada_em)
        if pegada is not None and agora >= pegada - ANTECEDENCIA_PREVISTO and (largada is None or agora < largada):
            return {"tipo": "previsto", "linhas": linhas, "turno": None, "previsto": previsto_info(escala, pontos)}
    return None


def escala_do_turno(db: Session, turno: Turno) -> Optional[NimerFiscalEscala]:
    """A batida do NIMER que abriu (ou cobre) o turno — para o selo "NIMER ✓ 14:13"."""
    return db.execute(
        select(NimerFiscalEscala)
        .where(NimerFiscalEscala.re == (turno.fiscal_re or "").strip(),
               NimerFiscalEscala.data_servico == turno.data_referencia,
               NimerFiscalEscala.inicio_em.is_not(None))
        .order_by(NimerFiscalEscala.inicio_em)
    ).scalars().first()
