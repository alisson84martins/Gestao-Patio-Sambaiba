"""Importação da planilha semanal de ICV da gerência (D20, D25, D28, §5).

Layout real levantado das planilhas de 19, 20 e 21/08/2026 (§5 do prompt
_handoff-claude/PROMPT-fiscalizacao-bloco-E-icv.md, por leitura direta —
não temos o arquivo à disposição neste ambiente). ⚠️ O parser abaixo ainda
não foi validado contra um .xlsx real — só contra fixtures sintéticas nos
testes. Ver _handoff-claude/PROGRESSO-2026-08-23.md.

D25 — a planilha real repete célula-arrastada: vários dias seguidos com a
MESMA contagem bruta. Mas repetição sozinha não é suspeita — linha a 100%
repete todo dia e isso é o esperado. `suspeito` só liga quando os DOIS
contadores brutos batem com o dia anterior já importado E o ICV do dia não
é 100%.

🔴 O formato de maio (LINHA | BACIA | LOTE | 3 (% ICV) | Media (% ICV) |
Total (% ICV), sem nenhuma coluna de contagem) NÃO serve como fonte —
percentual sem denominador não pondera (D22). Arquivo sem a coluna
VIAGENS PROG. é recusado inteiro, nunca importado parcialmente.

Isolado numa função só de detecção de cabeçalho, com os rótulos em
constantes no topo — o layout já mudou uma vez entre maio e agosto (mesma
disciplina de importacao_escala_fiscal.py::_localizar_cabecalho).

⛔ Este importador NÃO cria nem altera fiscalizacao.linha_coordenador —
associação de quem coordena cada linha é cadastro, não efeito colateral
de import (revisado em PROMPT-fiscalizacao-refatora-coordenador.md). A
coluna de agrupamento da planilha é gravada como snapshot puro em
icv_apurado.bacia_texto, só para conferência humana depois.
"""
import re
from datetime import date as date_type
from io import BytesIO
from typing import Optional
from uuid import UUID, uuid4

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fiscalizacao import IcvApurado

# ─── Rótulos de cabeçalho — isolados aqui porque o layout já mudou uma vez
# entre maio e agosto (§5 do prompt), e vai mudar de novo. ──────────────────
_ROTULOS_LINHA = ("AQA", "LINHA")
_ROTULO_BACIA_TEXTO = "BACIA"  # coluna da planilha — grava snapshot puro, não vira entidade
_ROTULO_LOTE = "LOTE"
_ROTULO_PROGRAMADAS = "VIAGENS PROG."
_ROTULO_TP_TS = "VIAGENS REAL TP-TS"
_ROTULO_TS_TP = "VIAGENS REAL TS-TP"
_ROTULO_PERCENTUAL = "TOTAL (% ICV)"
_MARCADOR_LINHA_TOTAL = "TOTAL"

_LOTES_VALIDOS = {"E2", "AR2"}
_TOLERANCIA_PERCENTUAL = 0.01  # D28 — divergência de conferência contra a própria planilha

_PADRAO_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_LINHAS_CABECALHO_VARRIDAS = 5  # título e "DATA: ..." vêm nas 2 primeiras linhas (§5)


def _normalizar_rotulo(valor) -> str:
    return str(valor).strip().upper() if valor is not None else ""


def _extrair_data_referencia(rows: list[tuple]) -> Optional[date_type]:
    """A data do dado vem do cabeçalho ('DATA: 19/08/2026'), NUNCA do nome
    do arquivo (§5)."""
    for row in rows[:_LINHAS_CABECALHO_VARRIDAS]:
        for cell in row:
            if cell is None:
                continue
            texto = str(cell)
            if "DATA" not in texto.upper():
                continue
            m = _PADRAO_DATA.search(texto)
            if m:
                dia, mes, ano = m.groups()
                try:
                    return date_type(int(ano), int(mes), int(dia))
                except ValueError:
                    continue
    return None


def _localizar_cabecalho(rows: list[tuple]) -> tuple[Optional[int], Optional[dict[str, int]]]:
    """Acha a linha de cabeçalho da tabela e mapeia rótulo → índice de
    coluna. (None, None) quando a coluna VIAGENS PROG. não existe — é o
    formato de maio, sem denominador (D22 não pondera sem ele)."""
    for i, row in enumerate(rows):
        normalizados = [_normalizar_rotulo(c) for c in row]
        if _ROTULO_PROGRAMADAS not in normalizados:
            continue
        indices: dict[str, int] = {}
        for idx, rotulo in enumerate(normalizados):
            if rotulo in _ROTULOS_LINHA:
                indices["linha"] = idx
            elif rotulo == _ROTULO_BACIA_TEXTO:
                indices["bacia_texto"] = idx
            elif rotulo == _ROTULO_LOTE:
                indices["lote"] = idx
            elif rotulo == _ROTULO_PROGRAMADAS:
                indices["programadas"] = idx
            elif rotulo == _ROTULO_TP_TS:
                indices["tp_ts"] = idx
            elif rotulo == _ROTULO_TS_TP:
                indices["ts_tp"] = idx
            elif rotulo == _ROTULO_PERCENTUAL:
                indices["percentual"] = idx
        if "linha" in indices:
            return i, indices
    return None, None


def _linha_codigo_e_ilegivel(valor) -> tuple[str, bool]:
    """Códigos podem vir corrompidos pelo Excel como notação científica
    (ex.: '2,13E-08'). Não descarta nem converte de volta (impossível) —
    importa como texto e sinaliza para conferência humana."""
    if isinstance(valor, str):
        return valor.strip(), False
    if isinstance(valor, (int, float)):
        texto = f"{valor:.2E}".replace(".", ",")
        return texto, True
    return str(valor).strip() if valor is not None else "", True


def _para_inteiro(valor) -> Optional[int]:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise TypeError("valor booleano onde esperava número")
    if isinstance(valor, (int, float)):
        return int(round(valor))
    texto = str(valor).strip()
    if not texto:
        return None
    return int(round(float(texto.replace(",", "."))))


def _para_percentual(valor) -> Optional[float]:
    """Normaliza a coluna 'Total (% ICV)' — pode vir como fração Excel
    (0.9482) ou já como número (94.82). <=1.5 é tratado como fração (cobre
    inclusive >100%: 1.10 → 110.0, D30)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        texto = str(valor).strip().replace("%", "").replace(",", ".")
        if not texto:
            return None
        try:
            numero = float(texto)
        except ValueError:
            return None
    if numero <= 1.5:
        numero *= 100
    return round(numero, 2)


def _verificar_suspeita(
    db: Session, linha_codigo: str, data_referencia: date_type, tp_ts: int, ts_tp: int, icv_calculado: Optional[float]
) -> tuple[bool, Optional[str]]:
    """D25 — suspeito quando os DOIS contadores brutos batem com o dia
    anterior JÁ IMPORTADO (não necessariamente ontem no calendário — pega o
    último dia com dado, cobrindo buracos de planilha faltando) E o ICV do
    dia não é 100% (linha a 100% que repete é o esperado, não suspeita)."""
    anterior = db.execute(
        select(IcvApurado)
        .where(IcvApurado.linha_codigo == linha_codigo, IcvApurado.data_referencia < data_referencia)
        .order_by(IcvApurado.data_referencia.desc())
    ).scalars().first()
    if anterior is None:
        return False, None
    if anterior.realizadas_tp_ts != tp_ts or anterior.realizadas_ts_tp != ts_tp:
        return False, None
    if icv_calculado is not None and round(icv_calculado, 2) == 100.00:
        return False, None
    motivo = (
        f"Contadores idênticos ao dia anterior importado ({anterior.data_referencia.isoformat()}): "
        f"TP-TS={tp_ts}, TS-TP={ts_tp} — possível célula arrastada no Excel."
    )
    return True, motivo


def importar_icv(db: Session, conteudo: bytes, *, arquivo_nome: str, importado_por: Optional[UUID]) -> dict:
    """Recebe os bytes do .xlsx de ICV. Upsert por (linha_codigo,
    data_referencia) — reimportar o mesmo dia atualiza, não duplica.
    Levanta ValueError (400 no router) só quando o arquivo inteiro não
    serve como fonte (sem VIAGENS PROG. — formato de maio, D22); erro de
    UMA linha nunca derruba as outras, só entra na lista de erros."""
    wb = openpyxl.load_workbook(BytesIO(conteudo), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    data_referencia = _extrair_data_referencia(rows)
    if data_referencia is None:
        raise ValueError(
            "Não foi possível localizar 'DATA: DD/MM/AAAA' no cabeçalho da planilha "
            "(esperado nas duas primeiras linhas, ex.: 'DATA: 19/08/2026')."
        )

    header_idx, indices = _localizar_cabecalho(rows)
    if indices is None or "programadas" not in indices:
        raise ValueError(
            "Planilha sem a coluna 'VIAGENS PROG.' — formato antigo (só percentual, sem "
            "contagem) não pondera (D22) e não pode ser importado como origem de número. "
            "Peça o arquivo no formato atual (com VIAGENS PROG. / VIAGENS REAL TP-TS / TS-TP)."
        )

    linhas_lidas = linhas_gravadas = linhas_atualizadas = 0
    suspeitas: list[dict] = []
    divergentes_percentual: list[dict] = []
    codigos_ilegiveis: list[dict] = []
    erros: list[dict] = []

    for i, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        linha_bruta = row[indices["linha"]] if indices["linha"] < len(row) else None
        if linha_bruta is None or str(linha_bruta).strip() == "":
            continue

        linha_codigo, ilegivel = _linha_codigo_e_ilegivel(linha_bruta)
        if linha_codigo.strip().upper() == _MARCADOR_LINHA_TOTAL:
            break  # linha TOTAL — descartar, fim dos dados (§5)

        try:
            programadas = _para_inteiro(row[indices["programadas"]]) if "programadas" in indices else None
        except (TypeError, ValueError):
            erros.append({"linha_index": i, "mensagem": f"'{linha_codigo}': VIAGENS PROG. inválido."})
            continue
        if programadas is None:
            erros.append({"linha_index": i, "mensagem": f"'{linha_codigo}': VIAGENS PROG. vazio."})
            continue

        try:
            tp_ts = _para_inteiro(row[indices["tp_ts"]]) if "tp_ts" in indices else None
            ts_tp = _para_inteiro(row[indices["ts_tp"]]) if "ts_tp" in indices else None
        except (TypeError, ValueError):
            erros.append({"linha_index": i, "mensagem": f"'{linha_codigo}': contador de realizadas inválido."})
            continue
        # VIAGENS REAL TS-TP vazio é normal em linha circular (§5) → 0, não erro.
        tp_ts = tp_ts or 0
        ts_tp = ts_tp or 0

        lote: Optional[str] = None
        if "lote" in indices:
            bruto_lote = row[indices["lote"]]
            if bruto_lote is not None and str(bruto_lote).strip():
                candidato = str(bruto_lote).strip().upper()
                if candidato in _LOTES_VALIDOS:
                    lote = candidato
                else:
                    erros.append({
                        "linha_index": i,
                        "mensagem": f"'{linha_codigo}': lote '{candidato}' fora de E2/AR2 — gravado sem lote.",
                    })

        bacia_texto: Optional[str] = None
        if "bacia_texto" in indices:
            bruto_bacia = row[indices["bacia_texto"]]
            if bruto_bacia is not None and str(bruto_bacia).strip():
                bacia_texto = str(bruto_bacia).strip()

        icv_calculado = round((tp_ts + ts_tp) / programadas * 100, 2) if programadas > 0 else None

        if "percentual" in indices and icv_calculado is not None:
            percentual_planilha = _para_percentual(row[indices["percentual"]])
            if percentual_planilha is not None and abs(percentual_planilha - icv_calculado) > _TOLERANCIA_PERCENTUAL:
                divergentes_percentual.append({
                    "linha_codigo": linha_codigo,
                    "icv_calculado": icv_calculado,
                    "icv_planilha": percentual_planilha,
                })

        if ilegivel:
            codigos_ilegiveis.append({"linha_index": i, "valor_bruto": linha_codigo})

        suspeito, motivo = _verificar_suspeita(db, linha_codigo, data_referencia, tp_ts, ts_tp, icv_calculado)

        existente = db.execute(
            select(IcvApurado).where(
                IcvApurado.linha_codigo == linha_codigo, IcvApurado.data_referencia == data_referencia
            )
        ).scalar_one_or_none()
        if existente is None:
            db.add(IcvApurado(
                id=uuid4(), linha_codigo=linha_codigo, data_referencia=data_referencia,
                programadas=programadas, realizadas_tp_ts=tp_ts, realizadas_ts_tp=ts_tp,
                lote=lote, origem="PLANILHA", suspeito=suspeito, suspeito_motivo=motivo,
                arquivo_nome=arquivo_nome, importado_por=importado_por, bacia_texto=bacia_texto,
            ))
            linhas_gravadas += 1
        else:
            existente.programadas = programadas
            existente.realizadas_tp_ts = tp_ts
            existente.realizadas_ts_tp = ts_tp
            existente.lote = lote
            existente.suspeito = suspeito
            existente.suspeito_motivo = motivo
            existente.arquivo_nome = arquivo_nome
            existente.importado_por = importado_por
            existente.bacia_texto = bacia_texto
            linhas_atualizadas += 1

        linhas_lidas += 1

        if suspeito:
            suspeitas.append({"linha_codigo": linha_codigo, "motivo": motivo})

    return {
        "data_referencia": data_referencia.isoformat(),
        "linhas_lidas": linhas_lidas,
        "linhas_gravadas": linhas_gravadas,
        "linhas_atualizadas": linhas_atualizadas,
        "suspeitas": suspeitas,
        "divergentes_percentual": divergentes_percentual,
        "codigos_ilegiveis": codigos_ilegiveis,
        "erros": erros,
    }
