"""Lógica de parser e importação de planilha Excel de escala.

Suporta dois formatos automaticamente:

  1. Formato Sambaíba (V2) — múltiplas abas com grupos de colunas:
       Aba E2:      3 grupos (carro|hora|linha) em cols 0,1,2 / 6,7,8 / 12,13,14
                    Cabeçalho em 3 linhas, dados a partir da linha 4
       Aba AR2:     mesma estrutura da E2
       Aba MANOBRA: 6 grupos (carro|hora) em cols 0,1 / 3,4 / 6,7 / 9,10 / 12,13 / 15,16
                    Cabeçalho em 1 linha, dados a partir da linha 2

  2. Formato simples — uma aba, uma linha por ônibus:
       Col A: numero_frota | B: linha_codigo | C: horario_saida | D: re_motorista | E: tipo
       Cabeçalho na linha 1, dados a partir da linha 2

Auto-detecta o formato pelo nome/cabeçalho das abas.
Auto-cria Ônibus e Linha caso não existam no banco.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date as date_type, datetime, time, timezone
from typing import IO

from openpyxl import load_workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    Escala,
    ImportacaoEscala,
    Linha,
    Motorista,
    Onibus,
    OrigemEscalaEnum,
    StatusImportacaoEnum,
    TipoEscalaEnum,
)
from app.models.enums import SetorEnum


# ─── Dataclass de linha parseada ─────────────────────────────────────────────

@dataclass
class LinhaParseada:
    linha_planilha: int
    numero_frota: int | None = None
    linha_codigo: str | None = None
    horario_saida: time | None = None
    re_motorista: str | None = None
    tipo: TipoEscalaEnum | None = None
    erro: str | None = None


# ─── Helpers de parsing ───────────────────────────────────────────────────────

def _parse_horario(valor) -> time | None:
    if valor is None:
        return None
    if isinstance(valor, time):
        return valor
    if isinstance(valor, datetime):
        return valor.time()
    if isinstance(valor, str):
        s = valor.strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
    return None


def _parse_tipo(valor) -> TipoEscalaEnum | None:
    if not valor:
        return None
    try:
        return TipoEscalaEnum(str(valor).strip().upper())
    except ValueError:
        return None


def _val_carro(valor) -> int | None:
    """Valida número de frota (3-4 dígitos). Retorna None se inválido."""
    if valor is None:
        return None
    try:
        s = str(int(round(float(str(valor))))).strip()
    except (ValueError, TypeError):
        return None
    return int(s) if re.match(r'^\d{3,4}$', s) else None


def _detectar_tipo_aba(sheet_name: str, header_text: str) -> str | None:
    """Detecta tipo da aba: 'e2', 'ar2', 'manobra', 'configuracao' ou None."""
    n = sheet_name.upper()
    h = header_text.upper()
    if 'E2' in n or 'E2' in h:
        return 'e2'
    if 'AR2' in n or 'AR2' in h:
        return 'ar2'
    if any(k in n or k in h for k in ('MANOBRA', 'MANOBRISTA', 'PRESO')):
        return 'manobra'
    if 'CONFIGURA' in n or 'CONFIGURA' in h:
        return 'configuracao'
    return None


def _setor_por_frota(numero_frota: int) -> SetorEnum:
    """Infere setor pelo número de frota: 1000-1999 = E2; 2000-2999 = AR2."""
    if 2000 <= numero_frota <= 2999:
        return SetorEnum.AR2
    return SetorEnum.E2  # default e para 1xxx


# ─── Parser formato Sambaíba (V2) ────────────────────────────────────────────

def _parsear_formato_sambaiba(wb) -> list[LinhaParseada]:
    """
    Parseia o formato real das planilhas de escala da Sambaíba.

    Abas E2/AR2:  3 grupos (carro, hora, linha) por linha de dados
                  Índices: (0,1,2), (6,7,8), (12,13,14)
                  Dados a partir da linha 4 (3 linhas de cabeçalho)

    Aba MANOBRA:  6 grupos (carro, hora) por linha de dados
                  Índices: (0,1), (3,4), (6,7), (9,10), (12,13), (15,16)
                  Dados a partir da linha 2 (1 linha de cabeçalho)
    """
    resultado: list[LinhaParseada] = []
    idx = 0

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # Header = primeiras 3 linhas concatenadas
        header_text = ' '.join(str(c or '') for row in rows[:3] for c in row)
        tipo_aba = _detectar_tipo_aba(sheet_name, header_text)

        if not tipo_aba or tipo_aba == 'configuracao':
            continue

        if tipo_aba == 'e2':
            tipo_enum = TipoEscalaEnum.PLANTAO_E2
            grupos = [(0, 1, 2), (6, 7, 8), (12, 13, 14)]
            data_rows = rows[3:]  # pula 3 linhas de cabeçalho
        elif tipo_aba == 'ar2':
            tipo_enum = TipoEscalaEnum.PLANTAO_AR2
            grupos = [(0, 1, 2), (6, 7, 8), (12, 13, 14)]
            data_rows = rows[3:]
        else:  # manobra
            tipo_enum = TipoEscalaEnum.MANOBRA
            grupos = [(0, 1), (3, 4), (6, 7), (9, 10), (12, 13), (15, 16)]
            data_rows = rows[1:]  # pula 1 linha de cabeçalho

        for row in data_rows:
            if not row or all(c is None for c in row):
                continue
            # Garante largura mínima para evitar IndexError
            row_list = list(row) + [None] * 20

            for grupo in grupos:
                ci = grupo[0]
                hi = grupo[1]
                li = grupo[2] if len(grupo) > 2 else None

                frota = _val_carro(row_list[ci])
                if frota is None:
                    continue

                hora_raw = row_list[hi]
                hora_str = str(hora_raw or '').strip().upper()
                # Marcadores especiais não são escalas de serviço
                if hora_str in ('PRESO', 'AMOSTRAL', 'EVENTO', 'TABELAS', ''):
                    continue

                hora = _parse_horario(hora_raw)
                if hora is None:
                    continue  # hora não reconhecida — pula silenciosamente

                linha_codigo: str | None = None
                if li is not None and row_list[li] is not None:
                    linha_codigo = str(row_list[li]).strip() or None

                idx += 1
                l = LinhaParseada(
                    linha_planilha=idx,
                    numero_frota=frota,
                    linha_codigo=linha_codigo,
                    horario_saida=hora,
                    tipo=tipo_enum,
                )

                # Plantão SEM linha = erro (manobra não exige linha)
                if tipo_enum != TipoEscalaEnum.MANOBRA and not linha_codigo:
                    l.erro = "linha_codigo ausente na aba de plantão"

                resultado.append(l)

    return resultado


# ─── Parser formato simples ───────────────────────────────────────────────────

def _parsear_formato_simples(wb) -> list[LinhaParseada]:
    """Formato simples: uma aba, col A=frota, B=linha, C=hora, D=re, E=tipo."""
    ws = wb.active
    linhas: list[LinhaParseada] = []
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(cell is None for cell in row):
     