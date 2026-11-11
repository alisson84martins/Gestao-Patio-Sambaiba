"""Teste manual local: manda ao backend um Relatório de Linha do NIMER FICTÍCIO.

⛔ Sem dado pessoal real: REs 9xxxx inventados, carros e tabelas de teste.
Reproduz os casos de 10/11/2026 que a fase 2 tem que pegar:
  - tabela 032116 com "Sem Motorista" (perde as viagens; alerta antes da 1ª);
  - carro 2293 perdido por "4- Recolhe Anormal / SOS" e de volta à tarde na
    032207 (vira S.O.S);
  - "3 - Trânsito" (vai para o OBS do fechamento, fora dos contadores);
  - viagem registrada duas vezes para o mesmo carro (duplicado não conta 2×).

Uso (backend rodando, MONITOR_INGEST_TOKEN no .env):
    python scripts/enviar_nimer_ficticio.py --url http://127.0.0.1:8000 --token SEU_TOKEN
    python scripts/enviar_nimer_ficticio.py --salvar nimer_ficticio.json      # só grava o JSON
Depois: abra ao-vivo.html?linha=1726-10 (aba Partidas) e fiscalizacao-central.html.
"""
import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import FUSO_OPERACAO  # noqa: E402
from app.services.monitoramento_cadastro import data_servico_de  # noqa: E402

LINHA = "1726-10"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def montar_timeline(dia: date, agora: datetime) -> dict:
    """9 tabelas, partidas a cada ~25 min por sentido das 05:00 às 21:00 (SP)."""
    inicio = datetime(dia.year, dia.month, dia.day, 5, 0, tzinfo=FUSO_OPERACAO)
    tabelas = []
    dep = 700000
    nomes = ["032104", "032105", "032106", "032107", "032108", "032109", "032110", "032116", "032120"]
    for n, nome in enumerate(nomes):
        carro = str(2400 + n)
        motorista, cobrador = f"9{1000 + n}", f"9{2000 + n}"
        sem_motorista = nome == "032116"
        if nome == "032107":
            carro = "2293"
        trips = []
        for idx in range(14):
            prog = inicio + timedelta(minutes=n * 25 + idx * 70)
            sentido_1 = idx % 2 == 0
            dep += 1
            passado = prog + timedelta(minutes=3) < agora
            status, real, motivo = "future", None, None
            if passado:
                status, real = "done", prog + timedelta(minutes=2)
                if sem_motorista:
                    status, real, motivo = "lost", None, "1 - Falta de Motorista"
                elif carro == "2293" and idx == 6:
                    status, real, motivo = "lost", None, "4- Recolhe Anormal / SOS"
                elif nome == "032110" and idx == 4:
                    status, real, motivo = "lost", None, "3 - Trânsito"
            trips.append({
                "idx": idx + 1, "tpToTs": sentido_1, "status": status,
                "progStartMs": _ms(prog), "progEndMs": _ms(prog + timedelta(minutes=55)),
                "realStartMs": _ms(real) if real else None,
                "realEndMs": _ms(real + timedelta(minutes=55)) if real else None,
                "startSource": "GPS" if real else None, "endSource": "GPS" if real else None,
                "delayStart": 2 if real else None, "delayEnd": None, "lostReason": motivo,
                "vehicle": carro, "departureId": dep, "startTripInputId": None,
            })
        tabelas.append({"tableId": 9000 + n, "name": nome, "vehicle": carro,
                        "driver": "Sem Motorista" if sem_motorista else motorista,
                        "charger": cobrador, "trips": trips})
    # 2293 volta à tarde numa tabela de reforço (032207) → S.O.S
    volta = inicio + timedelta(hours=12)
    tabelas.append({"tableId": 9100, "name": "032207", "vehicle": "2293", "driver": "91099", "charger": "92099",
                    "trips": [{"idx": 1, "tpToTs": False, "status": "done" if volta < agora else "future",
                               "progStartMs": _ms(volta), "progEndMs": _ms(volta + timedelta(minutes=55)),
                               "realStartMs": _ms(volta + timedelta(minutes=1)) if volta < agora else None,
                               "realEndMs": None, "startSource": "GPS", "endSource": None, "delayStart": 1,
                               "delayEnd": None, "lostReason": None, "vehicle": "2293", "departureId": 799001,
                               "startTripInputId": None},
                              # o mesmo carro registrado duas vezes para a mesma saída (duplicado)
                              {"idx": 2, "tpToTs": False, "status": "done" if volta < agora else "future",
                               "progStartMs": _ms(volta + timedelta(minutes=5)),
                               "progEndMs": _ms(volta + timedelta(minutes=60)),
                               "realStartMs": _ms(volta + timedelta(minutes=4)) if volta < agora else None,
                               "realEndMs": None, "startSource": "Fiscal", "endSource": None, "delayStart": None,
                               "delayEnd": None, "lostReason": None, "vehicle": "2293", "departureId": 799002,
                               "startTripInputId": None}]})
    trips = [t for tb in tabelas for t in tb["trips"]]
    return {
        "plannedTrips": len(trips),
        "realizedTrips": sum(t["status"] == "done" for t in trips),
        "lostTrips": sum(t["status"] == "lost" for t in trips),
        "missingTrips": 0,
        "onTimePercent": 84.0,
        "tables": tabelas,
    }


def payload(agora: datetime) -> dict:
    dia = data_servico_de(agora)
    return {"linha_codigo": LINHA, "data_servico": dia.isoformat(), "coletado_em": _ms(agora),
            "origem": "teste-manual-ficticio", "status": "ok", "mensagem": "fictício", "timeline": montar_timeline(dia, agora)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", help="MONITOR_INGEST_TOKEN do .env")
    ap.add_argument("--salvar", type=Path, help="grava o JSON em vez de enviar")
    args = ap.parse_args(argv)
    corpo = payload(datetime.now(FUSO_OPERACAO))
    if args.salvar:
        args.salvar.write_text(json.dumps(corpo, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Gravado em", args.salvar)
        return 0
    if not args.token:
        print("Passe --token (o MONITOR_INGEST_TOKEN do .env) ou --salvar.")
        return 2
    req = urllib.request.Request(args.url.rstrip("/") + "/monitoramento/nimer", data=json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json", "X-Monitor-Token": args.token})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.status, r.read().decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
