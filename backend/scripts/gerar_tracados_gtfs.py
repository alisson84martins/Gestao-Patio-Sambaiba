"""Gera app/data/tracados/<linha>.json a partir do GTFS da SPTrans.

Uso (na pasta backend/):
    python scripts/gerar_tracados_gtfs.py 1726-10 2011-10
    python scripts/gerar_tracados_gtfs.py --do-banco          # linhas ativas de linha_monitorada
    python scripts/gerar_tracados_gtfs.py --gtfs D:/gtfs 1726-10

Para cada linha: shape mais usado de cada direction_id (0 → sentido 1,
TP → TS; 1 → sentido 2, TS → TP — conferido na 1726-10: shape 86298 =
sentido 1 = destino Metrô Santana, igual ao SIM 1403), nomes pelo
trip_headsign e as pontas do traçado como centros iniciais de terminal:
  TP = início do sentido 1; TS = fim do sentido 1 e início do sentido 2.
⚠️ A ponta FINAL do sentido 2 não entra: na 1726 é o ponto oficial do SIM,
445 m da volta real. Para corrigir o centro, use `centros_calibrados`
(à mão ou scripts/calibrar_terminais.py).

Regerar uma linha que já tem JSON PRESERVA o que foi ajustado à mão: `nome`,
os nomes dos terminais e `centros_calibrados`.
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.linha import codigo_linha  # noqa: E402

GTFS_PADRAO = Path(r"C:\dev\poc-olhovivo\dados\gtfs")
DIR_TRACADOS = BACKEND / "app" / "data" / "tracados"


def _ler_csv(caminho: Path):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def escolher_shapes(gtfs: Path, linhas: list[str]) -> dict[str, dict]:
    """{linha: {"nome_rota", "sentidos": {1: {"shape", "headsign"}, 2: {...}}}}"""
    rotas = {r["route_id"]: r for r in _ler_csv(gtfs / "routes.txt")}
    contagem: dict[tuple[str, int], Counter] = defaultdict(Counter)
    headsign: dict[tuple[str, str], str] = {}
    for t in _ler_csv(gtfs / "trips.txt"):
        canon = codigo_linha(t["route_id"])
        if canon not in linhas or not t.get("shape_id"):
            continue
        sentido = 1 if str(t.get("direction_id", "0")) == "0" else 2
        contagem[(canon, sentido)][t["shape_id"]] += 1
        headsign[(canon, t["shape_id"])] = t.get("trip_headsign") or ""
    saida: dict[str, dict] = {}
    for canon in linhas:
        rota = next((r for rid, r in rotas.items() if codigo_linha(rid) == canon), None)
        sentidos = {}
        for s in (1, 2):
            if contagem.get((canon, s)):
                shape = contagem[(canon, s)].most_common(1)[0][0]
                sentidos[s] = {"shape": shape, "headsign": headsign.get((canon, shape), "")}
        if sentidos:
            saida[canon] = {"nome_rota": (rota or {}).get("route_long_name", ""), "sentidos": sentidos}
    return saida


def ler_pontos(gtfs: Path, shapes: set[str]) -> dict[str, list[list[float]]]:
    pontos: dict[str, list] = {s: [] for s in shapes}
    for r in _ler_csv(gtfs / "shapes.txt"):
        if r["shape_id"] in pontos:
            pontos[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    return {s: [[la, lo] for _, la, lo in sorted(lista)] for s, lista in pontos.items()}


def montar_json(codigo: str, escolha: dict, pontos: dict, anterior: dict | None) -> dict:
    anterior = anterior or {}
    sentidos = {}
    for s, v in sorted(escolha["sentidos"].items()):
        sentidos[str(s)] = {
            "shape": v["shape"],
            "destino": "TS" if s == 1 else "TP",
            "nome": f"Sentido {v['headsign']}".strip(),
            "pontos": pontos[v["shape"]],
        }
    ida = sentidos.get("1", {}).get("pontos") or []
    volta = sentidos.get("2", {}).get("pontos") or []
    pontas = {
        "TP": [ida[0]] if ida else [],
        "TS": ([ida[-1]] if ida else []) + ([volta[0]] if volta else []),
    }
    nomes_gtfs = {
        "TP": escolha["sentidos"].get(2, {}).get("headsign") or "TP",
        "TS": escolha["sentidos"].get(1, {}).get("headsign") or "TS",
    }
    term_ant = anterior.get("terminais") or {}
    terminais = {}
    for t in ("TP", "TS"):
        ant = term_ant.get(t) or {}
        terminais[t] = {
            "nome": ant.get("nome") or f"{t} {nomes_gtfs[t]}",
            "centros_calibrados": ant.get("centros_calibrados") or [],
            "centros": pontas[t],
        }
    shapes = ", ".join(f"{v['shape']} = sentido {s}" for s, v in sentidos.items())
    return {
        "linha": codigo,
        "nome": anterior.get("nome") or f"{codigo} {escolha['nome_rota']}".strip(),
        "fonte": f"GTFS SPTrans ({shapes}); gerado por scripts/gerar_tracados_gtfs.py em {date.today().isoformat()}",
        "sentidos": sentidos,
        "terminais": terminais,
    }


def _linhas_do_banco() -> list[str]:
    from app.core.database import SessionLocal
    from app.services.monitoramento_cadastro import linhas_ativas
    with SessionLocal() as db:
        return list(linhas_ativas(db))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("linhas", nargs="*", help="códigos de linha (qualquer formato: 1726-10, 1726/10, 1726)")
    ap.add_argument("--gtfs", type=Path, default=GTFS_PADRAO, help="pasta do GTFS descompactado")
    ap.add_argument("--do-banco", action="store_true", help="usa as linhas ativas de monitoramento.linha_monitorada")
    ap.add_argument("--saida", type=Path, default=DIR_TRACADOS)
    args = ap.parse_args(argv)

    linhas = [codigo_linha(x) for x in args.linhas]
    if None in linhas:
        print("Código de linha inválido:", [x for x, c in zip(args.linhas, linhas) if c is None])
        return 2
    if args.do_banco:
        linhas += _linhas_do_banco()
    linhas = sorted(set(linhas))
    if not linhas:
        print("Nenhuma linha. Passe os códigos ou --do-banco.")
        return 2

    escolhas = escolher_shapes(args.gtfs, linhas)
    faltando = [x for x in linhas if x not in escolhas]
    shapes = {v["shape"] for e in escolhas.values() for v in e["sentidos"].values()}
    pontos = ler_pontos(args.gtfs, shapes)
    args.saida.mkdir(parents=True, exist_ok=True)
    for codigo, escolha in escolhas.items():
        arquivo = args.saida / f"{codigo}.json"
        anterior = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None
        if anterior and "sentidos" not in anterior:
            anterior = None  # formato antigo (fase 1): só traçado, nada ajustado à mão
        dados = montar_json(codigo, escolha, pontos, anterior)
        arquivo.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tam = {s: len(v["pontos"]) for s, v in dados["sentidos"].items()}
        print(f"{codigo}: {arquivo.name} — pontos por sentido {tam}")
    if faltando:
        print("Sem traçado no GTFS (a linha funciona sem mapa):", ", ".join(faltando))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
