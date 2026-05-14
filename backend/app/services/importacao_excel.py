"""Lógica de parser e importação de planilha Excel de escala.

Formato esperado da planilha:
- Coluna A: numero_frota (4 dígitos)
- Coluna B: linha_codigo (ex: '8500-10')
- Coluna C: horario_saida (HH:MM ou tempo Excel)
- Coluna D: re_motorista (opcional)
- Coluna E: tipo (opcional: MANOBRA, PLANTAO_E2, PLANTAO_AR2)

A primeira linha é ignorada (cabeçalho).
"""
from __future__ import annotations

import hashlib
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


@dataclass
class LinhaParseada:
    linha_planilha: int
    numero_frota: int | None = None
    linha_codigo: str | None = None
    horario_saida: time | None = None
    re_motorista: str | None = None
    tipo: TipoEscalaEnum | None = None
    erro: str | None = None


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


def parsear_planilha(stream: IO[bytes]) -> list[LinhaParseada]:
    """Lê o Excel e retorna lista de LinhaParseada (com erro preenchido se inválida)."""
    wb = load_workbook(stream, data_only=True, read_only=True)
    ws = wb.active
    linhas: list[LinhaParseada] = []
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or all(cell is None for cell in row):
            continue
        l = LinhaParseada(linha_planilha=idx)
        try:
            cells = list(row) + [None] * 5
            l.numero_frota = int(cells[0]) if cells[0] is not None else None
            l.linha_codigo = str(cells[1]).strip() if cells[1] is not None else None
            l.horario_saida = _parse_horario(cells[2])
            l.re_motorista = str(cells[3]).strip() if cells[3] is not None else None
            l.tipo = _parse_tipo(cells[4])
            if l.numero_frota is None:
                l.erro = "numero_frota vazio"
            elif l.linha_codigo is None:
                l.erro = "linha_codigo vazio"
            elif l.horario_saida is None:
                l.erro = "horario_saida inválido"
        except Exception as exc:  # noqa: BLE001
            l.erro = f"erro ao parsear: {exc}"
        linhas.append(l)
    return linhas


def hash_arquivo(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def importar_escala(
    db: Session,
    arquivo_nome: str,
    conteudo: bytes,
    data_escala: date_type,
    tipo_default: TipoEscalaEnum,
    importado_por_id,
    substituir_existentes: bool = True,
) -> tuple[ImportacaoEscala, list[dict], int]:
    """Processa o Excel e insere escalas. Retorna (importacao, erros, substituidas)."""
    import io
    linhas_lidas = parsear_planilha(io.BytesIO(conteudo))

    # Substitui escalas existentes da mesma data (soft delete) se solicitado
    substituidas = 0
    if substituir_existentes:
        stmt = (
            update(Escala)
            .where(Escala.data == data_escala, Escala.deletado_em.is_(None))
            .values(deletado_em=datetime.now(timezone.utc))
        )
        result = db.execute(stmt)
        substituidas = result.rowcount or 0

    # Cria importacao_escala primeiro com status PARCIAL (atualiza no fim)
    imp = ImportacaoEscala(
        arquivo_nome=arquivo_nome,
        arquivo_hash=hash_arquivo(conteudo),
        data_escala=data_escala,
        total_registros=len(linhas_lidas),
        status=StatusImportacaoEnum.PARCIAL,
        importado_por=importado_por_id,
    )
    db.add(imp)
    db.flush()  # obtém o ID sem commitar

    # Cache de catálogos
    onibus_por_frota: dict[int, Onibus] = {}
    linha_por_codigo: dict[str, Linha] = {}
    motorista_por_re: dict[str, Motorista] = {}

    erros: list[dict] = []
    sucesso = 0

    for l in linhas_lidas:
        if l.erro:
            erros.append({"linha": l.linha_planilha, "motivo": l.erro, "valor_recebido": None})
            continue

        # Resolve ônibus
        onibus = onibus_por_frota.get(l.numero_frota)
        if onibus is None:
            onibus = db.execute(
                select(Onibus).where(Onibus.numero_frota == l.numero_frota)
            ).scalar_one_or_none()
            if onibus is None:
                erros.append({"linha": l.linha_planilha,
                              "motivo": f"Ônibus {l.numero_frota} não cadastrado",
                              "valor_recebido": str(l.numero_frota)})
                continue
            onibus_por_frota[l.numero_frota] = onibus

        # Resolve linha
        linha = linha_por_codigo.get(l.linha_codigo)
        if linha is None:
            linha = db.execute(
                select(Linha).where(Linha.codigo == l.linha_codigo)
            ).scalar_one_or_none()
            if linha is None:
                erros.append({"linha": l.linha_planilha,
                              "motivo": f"Linha {l.linha_codigo} não cadastrada",
                              "valor_recebido": l.linha_codigo})
                continue
            linha_por_codigo[l.linha_codigo] = linha

        # Resolve motorista (opcional)
        motorista_id = None
        if l.re_motorista:
            motorista = motorista_por_re.get(l.re_motorista)
            if motorista is None:
                motorista = db.execute(
                    select(Motorista).where(Motorista.re == l.re_motorista)
                ).scalar_one_or_none()
                if motorista:
                    motorista_por_re[l.re_motorista] = motorista
            if motorista:
                motorista_id = motorista.id

        # Valida cruzamento de setor
        if onibus.setor and linha.setor and onibus.setor != linha.setor:
            erros.append({"linha": l.linha_planilha,
                          "motivo": f"Setor incompatível: ônibus {onibus.setor.value} em linha {linha.setor.value}",
                          "valor_recebido": f"{l.numero_frota} -> {l.linha_codigo}"})
            continue

        # Cria escala
        escala = Escala(
            data=data_escala,
            onibus_id=onibus.id,
            motorista_id=motorista_id,
            linha_id=linha.id,
            horario_saida=l.horario_saida,
            tipo=l.tipo or tipo_default,
            origem=OrigemEscalaEnum.IMPORTACAO_EXCEL,
            importacao_id=imp.id,
            criado_por=importado_por_id,
        )
        db.add(escala)
        sucesso += 1

    imp.registros_sucesso = sucesso
    imp.registros_erro = len(erros)
    if erros and sucesso == 0:
        imp.status = StatusImportacaoEnum.ERRO
    elif erros:
        imp.status = StatusImportacaoEnum.PARCIAL
    else:
        imp.status = StatusImportacaoEnum.SUCESSO
    if erros:
        # Resumo dos primeiros 10 erros
        imp.erro_detalhe = "\n".join(
            f"Linha {e['linha']}: {e['motivo']}" for e in erros[:10]
        )
    db.commit()
    db.refresh(imp)
    return imp, erros, substituidas
