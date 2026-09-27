"""Cadastro único de linhas (migration 048) — normalização e interconexão.

Número + código formam linhas DIFERENTES (`271A-10` ≠ `271A-51`,
`2023-41` ≠ `2023-42`). Uma função só (`app/core/linha.py`) decide o formato
para Pátio, Escala de Fiscais, Fiscalização e cadastro.
"""
import pytest

from app.core.linha import codigo_linha, formatar_como_escala, normalizar_linha


# ─── R2: normalizar_linha ────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("271A", ("271A", "10")),
    ("271A51", ("271A", "51")),
    ("2023.41", ("2023", "41")),
    ("202341", ("2023", "41")),
    ("1726/10", ("1726", "10")),
    ("1726-10", ("1726", "10")),
    ("177H-10", ("177H", "10")),
    (" 119c ", ("119C", "10")),
    ("falta", None),
    ("MAN-E2", None),
    ("1726-1A", None),
])
def test_normalizar_linha_exemplos_da_r2(entrada, esperado):
    assert normalizar_linha(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    (2023, ("2023", "10")),          # célula numérica da planilha
    (202341, ("2023", "41")),
    (202341.0, ("2023", "41")),      # openpyxl pode devolver float inteiro
    ("271A 51", ("271A", "51")),
    ("", None),
    (None, None),
    ("271", None),                   # número com 3 caracteres: não adivinha
    ("271A-", None),
    ("271A-5100", None),             # sufixo com mais de 3 dígitos
    ("lixo", None),                  # 4 letras sem dígito não é número de linha
])
def test_normalizar_linha_bordas(entrada, esperado):
    assert normalizar_linha(entrada) == esperado


def test_codigo_canonico_e_formato_da_escala():
    assert codigo_linha("271A51") == "271A-51"
    assert codigo_linha("271A") == "271A-10"
    assert codigo_linha("falta") is None
    assert formatar_como_escala("1156-10") == "1156/10"
    assert formatar_como_escala("MAN-E2") == "MAN-E2"
