"""Catálogos de veículo (app/core/veiculo.py) — cores DENATRAN e UFs.

O espelho do frontend (frontend-v3/assets/js/catalogos-veiculo.js) é
conferido aqui também: lê o arquivo e compara as listas, para a duplicata
não divergir em silêncio.
"""
import re
from pathlib import Path

import pytest

from app.core.veiculo import CORES_DENATRAN, UFS, normalizar_cor

_JS = Path(__file__).resolve().parents[2] / "frontend-v3" / "assets" / "js" / "catalogos-veiculo.js"


def test_cores_na_ordem_oficial():
    assert CORES_DENATRAN == (
        "AMARELA", "AZUL", "BEGE", "BRANCA", "CINZA", "DOURADA", "GRENÁ", "LARANJA",
        "MARROM", "PRATA", "PRETA", "ROSA", "ROXA", "VERDE", "VERMELHA", "FANTASIA",
    )


def test_27_ufs_sem_repeticao():
    assert len(UFS) == 27
    assert len(set(UFS)) == 27


@pytest.mark.parametrize("entrada, esperado", [
    ("Prata", "PRATA"),
    ("prata", "PRATA"),
    ("  PRATA  ", "PRATA"),
    ("Grena", "GRENÁ"),
    ("GRENÁ", "GRENÁ"),
    ("grená", "GRENÁ"),
    ("Branco", "BRANCA"),
    ("PRETO", "PRETA"),
    ("Amarelo", "AMARELA"),
    ("vermelho", "VERMELHA"),
    ("Roxo", "ROXA"),
    ("Dourado", "DOURADA"),
    ("Fantasia", "FANTASIA"),
])
def test_normalizar_cor_reconhece(entrada, esperado):
    assert normalizar_cor(entrada) == esperado


@pytest.mark.parametrize("entrada", [
    None, "", "   ", "Prata metálico", "AZUL CLARO", "Grafite", "Vinho", "Azulado",
])
def test_normalizar_cor_desconhecida_da_none(entrada):
    assert normalizar_cor(entrada) is None


def _lista_js(nome: str) -> tuple[str, ...]:
    texto = _JS.read_text(encoding="utf-8")
    bloco = re.search(rf"export const {nome} = Object\.freeze\(\[(.*?)\]\)", texto, re.S)
    assert bloco, f"{nome} não encontrado em catalogos-veiculo.js"
    return tuple(re.findall(r"'([^']+)'", bloco.group(1)))


def test_espelho_frontend_igual():
    assert _lista_js("CORES_VEICULO") == CORES_DENATRAN
    assert _lista_js("UFS") == UFS
