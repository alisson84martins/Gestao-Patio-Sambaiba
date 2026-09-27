"""Normalização única de linha — número + código (cadastro único, migration 048).

Na operação, número + código formam linhas DIFERENTES: `271A-10` sai da
Penha e `271A-51` do Cangaíba; `2023-41` ≠ `2023-42`. Cada módulo escrevia
a linha de um jeito (planilha do Pátio `271A51`/`2023.41`, Escala de Fiscais
`1726/10`, Fiscalização `1726`) e nenhum enxergava o outro.

⛔ Não duplicar esta regra em nenhum outro lugar do backend: Pátio
(importação da escala), Escala de Fiscais, Fiscalização e o cadastro de
Linhas importam daqui. A migration 048 tem uma função SQL equivalente,
temporária, só para o backfill.

Regra (R2):
  - upper + trim; os 4 primeiros caracteres `[0-9A-Z]{4}` são o NÚMERO;
  - o número tem ao menos um dígito (`LIXO` não é linha; `N101` seria);
  - o resto, depois de um separador opcional (`-`, `/`, `.`, espaço), tem
    que ser só dígitos (1–3) → SUFIXO (o "código" da linha na operação);
  - resto vazio → sufixo `10` (padrão);
  - qualquer outra coisa → None (reportar, nunca adivinhar).
"""
import re
from typing import Optional

SUFIXO_PADRAO = "10"

_LINHA_RE = re.compile(r"^([0-9A-Z]{4})(?:[-/. ]?([0-9]{1,3}))?$")


def normalizar_linha(txt) -> Optional[tuple[str, str]]:
    """'271A51' -> ('271A', '51'); '1726/10' -> ('1726', '10'); '119c' -> ('119C', '10').

    Célula numérica da planilha (openpyxl devolve int/float) é aceita:
    2023 -> ('2023', '10'); 202341.0 -> ('2023', '41'). `falta`, `MAN-E2`,
    `1726-1A` -> None.
    """
    if txt is None or isinstance(txt, bool):
        return None
    if isinstance(txt, float) and txt.is_integer():
        txt = int(txt)
    s = str(txt).strip().upper()
    m = _LINHA_RE.match(s)
    if not m:
        return None
    numero, sufixo = m.group(1), m.group(2)
    if not any(c.isdigit() for c in numero):
        return None
    return numero, (sufixo or SUFIXO_PADRAO)


def codigo_canonico(numero: str, sufixo: str) -> str:
    """Formato canônico gravado em `public.linha.codigo`: '271A-51'."""
    return f"{numero}-{sufixo}"


def codigo_linha(txt) -> Optional[str]:
    """Atalho: texto em qualquer formato -> código canônico, ou None."""
    par = normalizar_linha(txt)
    return codigo_canonico(*par) if par else None


def formatar_como_escala(codigo: Optional[str]) -> Optional[str]:
    """Código canônico -> formato da planilha da Escala de Fiscais ('1156/10').

    Só formatação de saída (impressão fiel ao Excel) — o banco guarda o
    canônico. Texto que não normaliza sai como veio.
    """
    par = normalizar_linha(codigo)
    return f"{par[0]}/{par[1]}" if par else codigo
