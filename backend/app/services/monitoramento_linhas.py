"""Cadastro das linhas monitoradas ao vivo (SIM SPTrans + NIMER → sistema).

Fase 2 (migration 054): QUAIS linhas vêm de monitoramento.linha_monitorada
(ativo) e os códigos linha-sentido do SIM de monitoramento.sim_codigo_linha
(aprendidos pelo recebimento). O MAPA de cada linha (traçado por sentido +
centros de terminal) vem de app/data/tracados/<codigo>.json, gerado do GTFS
por backend/scripts/gerar_tracados_gtfs.py.

Incluir linha = INSERT em linha_monitorada + gerar o traçado. ⛔ Nada de
código de linha fixo aqui.

Linha monitorada SEM traçado: tudo que não depende de mapa funciona (ICV,
perdas, tabela sem motorista, sem transmissão); desvio/virada/retorno/
parado ficam desligados (`tem_mapa` False) e a tela avisa.

Formato do JSON do traçado:
  {"linha", "nome", "fonte",
   "sentidos": {"1": {"shape", "destino": "TS", "nome", "pontos": [[lat, lon], ...]}, "2": {...}},
   "terminais": {"TP": {"nome", "centros": [...pontas do traçado...],
                        "centros_calibrados": [...onde o SIM troca o sentido...]}, "TS": {...}}}
  Os centros calibrados vêm PRIMEIRO e podem ser escritos à mão (sobrescrever
  o centro do terminal) ou por scripts/calibrar_terminais.py.

Fatos medidos em 10/11/2026 (não "melhorar" sem falar com a coordenação):
- 1726-10: o ponto oficial do SIM para o TP (-23.434086, -46.565939) fica
  445 m da volta real — NÃO é centro de terminal (por isso a ponta final do
  sentido 2 nunca entra nos centros). Centros reais (146 viagens): TP
  (-23.438087, -46.566383) e TS (-23.502205, -46.62377).
- G3 em (-23.4902, -46.5610), ~5 km do itinerário.
"""
import json
import statistics
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core.linha import codigo_linha
from app.services.monitoramento_cadastro import linhas_ativas, mapa_sim

Ponto = tuple[float, float]

GARAGEM_G3: Ponto = (-23.4902, -46.5610)  # Av. João Simão de Castro (pelo rastro do SIM)
NOME_GARAGEM = "G3"

DIR_TRACADOS = Path(__file__).resolve().parent.parent / "data" / "tracados"


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
    nimer_line_id: Optional[int] = None

    @property
    def tem_mapa(self) -> bool:
        return bool(self.tracados) and all(self.terminais.get(t) for t in set(self.destino.values()))

    def sentido_do_sim(self, li) -> Optional[int]:
        try:
            return self.codigos_sim.get(int(li))
        except (TypeError, ValueError):
            return None


def _ponto(p) -> Ponto:
    return (float(p[0]), float(p[1]))


def linha_do_json(codigo: str, dados: Optional[dict]) -> LinhaMonitorada:
    """Monta a linha a partir do JSON do traçado (None = linha sem traçado)."""
    if not dados:
        return LinhaMonitorada(
            codigo=codigo, nome=codigo, codigos_sim={}, destino={1: "TS", 2: "TP"},
            nomes_terminais={"TP": "TP", "TS": "TS"}, terminais={}, tracados={},
        )
    sentidos = dados.get("sentidos") or {}
    tracados = {int(s): [_ponto(p) for p in v["pontos"]] for s, v in sentidos.items() if v.get("pontos")}
    destino = {int(s): v.get("destino") or ("TS" if int(s) == 1 else "TP") for s, v in sentidos.items()}
    terminais_json = dados.get("terminais") or {}
    terminais = {
        t: [_ponto(p) for p in (v.get("centros_calibrados") or [])] + [_ponto(p) for p in (v.get("centros") or [])]
        for t, v in terminais_json.items()
    }
    nomes = {t: v.get("nome") or t for t, v in terminais_json.items()}
    for t in set(destino.values()):
        nomes.setdefault(t, t)
    return LinhaMonitorada(
        codigo=codigo,
        nome=dados.get("nome") or codigo,
        codigos_sim={},
        destino=destino or {1: "TS", 2: "TP"},
        nomes_terminais=nomes,
        terminais={t: c for t, c in terminais.items() if c},
        tracados=tracados,
        shapes={int(s): str(v["shape"]) for s, v in sentidos.items() if v.get("shape")},
    )


@lru_cache(maxsize=256)
def _linha_do_arquivo(codigo: str) -> LinhaMonitorada:
    arquivo = DIR_TRACADOS / f"{codigo}.json"
    dados = None
    if arquivo.exists():
        with open(arquivo, encoding="utf-8") as f:
            dados = json.load(f)
    return linha_do_json(codigo, dados)


def recarregar_tracados() -> None:
    """Depois de gerar/calibrar um traçado com o processo no ar."""
    _linha_do_arquivo.cache_clear()


def _com_banco(base: LinhaMonitorada, db: Session, nimer_line_id: Optional[int]) -> LinhaMonitorada:
    codigos = {li: s for li, (linha, s) in mapa_sim(db).items() if linha == base.codigo and s in (1, 2)}
    return replace(base, codigos_sim=codigos, nimer_line_id=nimer_line_id)


def linhas_monitoradas(db: Session) -> dict[str, LinhaMonitorada]:
    """Linhas ATIVAS de linha_monitorada, por código canônico, com o mapa do JSON."""
    return {codigo: _com_banco(_linha_do_arquivo(codigo), db, nimer) for codigo, nimer in linhas_ativas(db).items()}


def obter_linha(codigo, db: Optional[Session] = None) -> Optional[LinhaMonitorada]:
    """Linha pelo código em qualquer formato ('1726-10', '1726/10', '1726').

    Com `db`: só se estiver ativa em linha_monitorada (None se não), com os
    códigos do SIM. Sem `db` (testes da cerca virtual, scripts): só o mapa."""
    canon = codigo_linha(codigo)
    if not canon:
        return None
    if db is None:
        return _linha_do_arquivo(canon)
    ativas = linhas_ativas(db)
    if canon not in ativas:
        return None
    return _com_banco(_linha_do_arquivo(canon), db, ativas[canon])


def prefixo_patio(prefixo_sim) -> str:
    """SIM usa 5 dígitos (22412); o pátio usa 4 (2412) = tira o 1º dígito."""
    p = str(prefixo_sim or "").strip()
    return p[1:] if len(p) == 5 and p.isdigit() else p


# ─── Calibração dos centros de terminal ──────────────────────────────────────

CALIBRA_MIN_LEITURAS_ANTES = 10   # ≥10 leituras no sentido que termina: viagem de verdade
CALIBRA_MIN_LEITURAS_DEPOIS = 3   # ≥3 no sentido novo: não é o SIM "piscando"
CALIBRA_LACUNA_MS = 3 * 60_000


def calibrar_centros(leituras: Iterable[dict], destino: dict[int, str]) -> dict[str, dict]:
    """Centro de cada terminal = mediana (lat e lon separadas) dos pontos onde
    o SIM troca o sentido — o método usado em 10/11/2026 com 146 viagens.

    `leituras`: dicts com prefixo, t (ms), s, la, lo. A troca de A → B conta
    para o terminal de destino de A, no meio das duas leituras, só quando A
    durou ≥ 10 leituras, B ≥ 3, e não há buraco > 3 min na troca (filtra o
    "pisca-pisca" do sentido com o carro parado na origem)."""
    por_carro: dict[str, list[dict]] = {}
    for r in leituras:
        por_carro.setdefault(str(r["prefixo"]), []).append(r)
    pontos: dict[str, list[Ponto]] = {}
    for lista in por_carro.values():
        lista.sort(key=lambda r: r["t"])
        trechos: list[list[dict]] = []
        for r in lista:
            if trechos and trechos[-1][-1]["s"] == r["s"]:
                trechos[-1].append(r)
            else:
                trechos.append([r])
        for antes, depois in zip(trechos, trechos[1:]):
            if len(antes) < CALIBRA_MIN_LEITURAS_ANTES or len(depois) < CALIBRA_MIN_LEITURAS_DEPOIS:
                continue
            a, b = antes[-1], depois[0]
            if b["t"] - a["t"] > CALIBRA_LACUNA_MS:
                continue
            terminal = destino.get(a["s"])
            if terminal:
                pontos.setdefault(terminal, []).append(((a["la"] + b["la"]) / 2, (a["lo"] + b["lo"]) / 2))
    return {
        t: {"centro": [round(statistics.median(p[0] for p in pts), 6), round(statistics.median(p[1] for p in pts), 6)],
            "trocas": len(pts)}
        for t, pts in pontos.items()
    }
