"""Cadastro das linhas monitoradas ao vivo (SIM SPTrans → sistema).

Porte do `linhas.js` da POC (C:\\dev\\poc-olhovivo\\extensao). Para incluir
outra linha: traçado GTFS em app/data/tracados/<codigo>.json + códigos SIM
de cada sentido + terminais. ⛔ Fase 1: só a 1726-10.

Fatos medidos em 10/11/2026 (não "melhorar" sem falar com a coordenação):
- 1726-10: TP = Cem. Pq. dos Pinheiros (Av. Cabuçu x Estr. Três Cruzes),
  TS = Terminal Santana.
- SIM: 1403 = sentido 1 (TP → TS); 34171 = sentido 2 (TS → TP). O campo
  `li` da leitura é esse código.
- O ponto oficial do SIM para o TP (-23.434086, -46.565939) fica 445 m da
  volta real — NÃO é centro de terminal. Centros reais (146 viagens):
  TP (-23.438087, -46.566383) e TS (-23.502205, -46.62377), mais as pontas
  do traçado GTFS.
- G3 em (-23.4902, -46.5610), ~5 km do itinerário.
"""
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.core.linha import codigo_linha

Ponto = tuple[float, float]

GARAGEM_G3: Ponto = (-23.4902, -46.5610)  # Av. João Simão de Castro (pelo rastro do SIM)
NOME_GARAGEM = "G3"

_DIR_TRACADOS = Path(__file__).resolve().parent.parent / "data" / "tracados"


@dataclass(frozen=True)
class LinhaMonitorada:
    codigo: str                                  # código canônico (cadastro único, 048)
    nome: str
    codigos_sim: dict[int, int]                  # li do SIM -> sentido
    destino: dict[int, str]                      # sentido -> terminal de destino
    nomes_terminais: dict[str, str]
    terminais: dict[str, list[Ponto]]            # vários centros por terminal
    tracados: dict[int, list[Ponto]]             # sentido -> pontos (lat, lon)
    shapes: dict[int, str] = field(default_factory=dict)  # sentido -> shape GTFS

    def sentido_do_sim(self, li) -> Optional[int]:
        try:
            return self.codigos_sim.get(int(li))
        except (TypeError, ValueError):
            return None


def _carregar_tracado(codigo: str) -> dict:
    with open(_DIR_TRACADOS / f"{codigo}.json", encoding="utf-8") as f:
        return json.load(f)


def _pontos(shape: dict) -> list[Ponto]:
    return [(float(la), float(lo)) for la, lo in shape["pontos"]]


def _linha_1726() -> LinhaMonitorada:
    t = _carregar_tracado("1726-10")
    ida = _pontos(t["86298"])    # sentido 1 → Metrô Santana
    volta = _pontos(t["86299"])  # sentido 2 → Cem. Pq. dos Pinheiros
    return LinhaMonitorada(
        codigo=codigo_linha("1726-10"),
        nome="1726-10 Cem. Pq. dos Pinheiros ⇄ Metrô Santana",
        codigos_sim={1403: 1, 34171: 2},
        destino={1: "TS", 2: "TP"},
        nomes_terminais={
            "TP": "TP Cem. Pq. dos Pinheiros (Três Cruzes)",
            "TS": "TS Terminal Santana",
        },
        terminais={
            # Centros onde o SIM troca o sentido (146 viagens) + pontas do
            # traçado. Igual ao linhas.js — a ponta final do 86299 é o ponto
            # oficial do SIM (445 m da volta real) e fica de fora de propósito.
            "TP": [(-23.438087, -46.566383), ida[0]],
            "TS": [(-23.502205, -46.62377), ida[-1], volta[0]],
        },
        tracados={1: ida, 2: volta},
        shapes={1: "86298", 2: "86299"},
    )


@lru_cache
def linhas_monitoradas() -> dict[str, LinhaMonitorada]:
    """Linhas monitoradas por código canônico (carregado uma vez por processo)."""
    linhas = [_linha_1726()]
    return {l.codigo: l for l in linhas}


def obter_linha(codigo) -> Optional[LinhaMonitorada]:
    """Linha monitorada pelo código em qualquer formato ('1726-10', '1726/10', '1726')."""
    canon = codigo_linha(codigo)
    return linhas_monitoradas().get(canon) if canon else None


@lru_cache
def _por_sim() -> dict[int, LinhaMonitorada]:
    return {li: l for l in linhas_monitoradas().values() for li in l.codigos_sim}


def linha_do_sim(li) -> Optional[LinhaMonitorada]:
    """Linha monitorada dona do código linha-sentido do SIM (`li`), ou None."""
    try:
        return _por_sim().get(int(li))
    except (TypeError, ValueError):
        return None


def prefixo_patio(prefixo_sim) -> str:
    """SIM usa 5 dígitos (22412); o pátio usa 4 (2412) = tira o 1º dígito."""
    p = str(prefixo_sim or "").strip()
    return p[1:] if len(p) == 5 and p.isdigit() else p
