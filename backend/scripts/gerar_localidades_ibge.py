"""Gera frontend-v3/assets/data/localidades-ibge.json — UFs e municípios do IBGE.

FONTE: API pública de localidades do IBGE (grátis, sem chave)
       https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado
GERADO EM: 04/11/2026 (27 UFs).

O formulário de ocorrência (sugestão de cidade enquanto digita) só lê o JSON
gerado — ⛔ o front nunca chama o IBGE em tempo de uso.

Formato (compacto, uma chave por UF, nomes em ordem pt-BR):
    {"AC": ["Acrelândia", ...], ..., "TO": [...]}

COMO REGENERAR (município novo criado por lei, troca de nome):
    cd backend
    .venv\\Scripts\\python.exe scripts\\gerar_localidades_ibge.py
    → confira o total impresso (hoje ~5.570) e commite o JSON.

`view=nivelado` (e não a resposta aninhada padrão) porque a aninhada já veio
com `microrregiao: null` em município recém-criado — a nivelada traz
`UF-sigla` direto em todo item.
"""
import json
import sys
import unicodedata
from pathlib import Path

import httpx

URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
DESTINO = Path(__file__).resolve().parents[2] / "frontend-v3" / "assets" / "data" / "localidades-ibge.json"


def chave_ptbr(nome: str) -> tuple[str, str]:
    """Ordem pt-BR sem depender do locale do sistema (Windows/Linux dão
    resultados diferentes): compara sem acento e sem maiúscula; empate
    desfeito pelo texto original para a ordem ser estável."""
    sem_acento = unicodedata.normalize("NFKD", nome)
    base = "".join(c for c in sem_acento if not unicodedata.combining(c)).casefold()
    return base, nome


def main() -> int:
    resp = httpx.get(URL, timeout=60.0)
    resp.raise_for_status()

    por_uf: dict[str, set[str]] = {}
    for item in resp.json():
        uf = item["UF-sigla"]
        por_uf.setdefault(uf, set()).add(item["municipio-nome"])

    saida = {uf: sorted(nomes, key=chave_ptbr) for uf, nomes in sorted(por_uf.items())}
    total = sum(len(v) for v in saida.values())

    if len(saida) != 27 or not 5560 <= total <= 5580:
        print(f"ERRO: {len(saida)} UFs / {total} municípios — fora do esperado, JSON não gravado.")
        return 1

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps(saida, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"OK: {len(saida)} UFs, {total} municípios → {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
