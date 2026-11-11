"""Calibra o centro dos terminais de uma linha pelas trocas de sentido do SIM.

Método de 10/11/2026 (146 viagens na 1726-10): o centro de cada terminal é a
mediana dos pontos onde o SIM troca o sentido do carro. Lê as leituras de
monitoramento.leitura_veiculo (retenção de 3 dias) e mostra o centro
calculado ao lado do atual; com --gravar, escreve em `centros_calibrados`
do JSON do traçado (os centros que vêm das pontas do GTFS continuam).

Uso (na pasta backend/):
    python scripts/calibrar_terminais.py 1726-10              # só mostra
    python scripts/calibrar_terminais.py 1726-10 --gravar     # grava no JSON
    python scripts/calibrar_terminais.py 1726-10 --dias 2 --minimo 30

Depois de gravar, reinicie o backend (o traçado é lido uma vez por processo).
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.linha import codigo_linha  # noqa: E402
from app.models.monitoramento import LeituraVeiculo  # noqa: E402
from app.services.monitoramento_linhas import (  # noqa: E402
    DIR_TRACADOS,
    calibrar_centros,
    obter_linha,
)
from app.services.monitoramento_regras import distancia  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("linha")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--minimo", type=int, default=20, help="trocas mínimas por terminal para gravar")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args(argv)

    codigo = codigo_linha(args.linha)
    linha = obter_linha(codigo) if codigo else None
    if linha is None:
        print("Linha inválida:", args.linha)
        return 2
    desde = datetime.now(timezone.utc) - timedelta(days=args.dias)
    with SessionLocal() as db:
        linhas = db.execute(
            select(LeituraVeiculo.prefixo, LeituraVeiculo.posicao_em, LeituraVeiculo.sentido,
                   LeituraVeiculo.lat, LeituraVeiculo.lon)
            .where(LeituraVeiculo.linha_codigo == codigo, LeituraVeiculo.posicao_em >= desde)
        ).all()
    leituras = [{"prefixo": p, "t": int(t.timestamp() * 1000), "s": s, "la": la, "lo": lo} for p, t, s, la, lo in linhas]
    print(f"{codigo}: {len(leituras)} leituras desde {desde:%d/%m %H:%M} UTC")
    resultado = calibrar_centros(leituras, linha.destino)
    if not resultado:
        print("Nenhuma troca de sentido aproveitável.")
        return 1
    arquivo = DIR_TRACADOS / f"{codigo}.json"
    dados = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None
    for t, r in sorted(resultado.items()):
        atual = linha.terminais.get(t, [None])[0]
        dif = f"{distancia(tuple(r['centro']), atual):.0f} m do atual" if atual else "sem centro atual"
        print(f"  {t}: {r['centro']} ({r['trocas']} trocas) — {dif}")
    if not args.gravar:
        print("(só mostrei; --gravar escreve em centros_calibrados)")
        return 0
    if dados is None or "terminais" not in dados:
        print("Sem JSON de traçado no formato novo — gere antes com scripts/gerar_tracados_gtfs.py")
        return 2
    for t, r in resultado.items():
        if r["trocas"] < args.minimo:
            print(f"  {t}: só {r['trocas']} trocas (< {args.minimo}) — não gravado")
            continue
        dados["terminais"].setdefault(t, {"nome": t, "centros": []})["centros_calibrados"] = [r["centro"]]
    trocas = ", ".join(f"{t}: {r['trocas']}" for t, r in sorted(resultado.items()))
    dados["calibracao"] = (f"Centros calibrados em {datetime.now():%d/%m/%Y} pela mediana das trocas de sentido "
                           f"do SIM ({trocas}).")
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Gravado em", arquivo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
