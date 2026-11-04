"""Catálogos de veículo — cores DENATRAN e siglas de UF.

Espelho no frontend: frontend-v3/assets/js/catalogos-veiculo.js (mesmo
padrão de app/core/placa.py ↔ mascaras.js). Mexeu num, mexe no outro —
duplicata diverge em silêncio.

Cores (D6, 04/11/2026): tabela DENATRAN/RENAVAM, a mesma do CRLV e da NF-e
(campo `cCorDENATRAN`), 16 valores na ordem oficial. Serve para o select do
formulário e para padronizar o texto que vem da consulta de placa.

⛔ Nada aqui bloqueia registro: o banco continua aceitando texto livre em
`cor` (ocorrência antiga com "Prata metálico" segue válida). `normalizar_cor`
só TRADUZ quando reconhece; quando não reconhece devolve None e quem chama
decide (a consulta de placa mostra o texto cru ao lado).
"""
import unicodedata
from typing import Optional

CORES_DENATRAN: tuple[str, ...] = (
    "AMARELA", "AZUL", "BEGE", "BRANCA", "CINZA", "DOURADA", "GRENÁ", "LARANJA",
    "MARROM", "PRATA", "PRETA", "ROSA", "ROXA", "VERDE", "VERMELHA", "FANTASIA",
)

UFS: tuple[str, ...] = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
)


def _chave(texto: str) -> str:
    """Sem acento, maiúscula, espaços das pontas cortados."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).strip().upper()


# Chave sem acento → item oficial. Inclui a forma masculina que algumas
# fontes usam (AMARELO, BRANCO…) — "GRENA" sem acento já cai pela chave.
_SINONIMOS = {
    "AMARELO": "AMARELA",
    "BRANCO": "BRANCA",
    "PRETO": "PRETA",
    "VERMELHO": "VERMELHA",
    "ROXO": "ROXA",
    "DOURADO": "DOURADA",
}
_MAPA_CORES: dict[str, str] = {_chave(c): c for c in CORES_DENATRAN}
_MAPA_CORES.update({k: _MAPA_CORES[v] for k, v in _SINONIMOS.items()})


def normalizar_cor(texto: Optional[str]) -> Optional[str]:
    """'Prata' -> 'PRATA'; 'grena' -> 'GRENÁ'; 'Branco' -> 'BRANCA'.

    Compara o texto inteiro (sem acento/maiúscula) — "PRATA METALICO" não é
    reconhecido de propósito: adivinhar por pedaço trocaria cor de documento.
    Não reconhecido ou vazio → None.
    """
    if not texto:
        return None
    return _MAPA_CORES.get(_chave(texto))
