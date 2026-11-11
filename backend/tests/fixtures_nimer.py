"""Montagem de respostas do NIMER (Relatório de Linha → timeline) para os testes.

Formato igual ao mapeado em 10/11/2026 (ver app/services/monitoramento_nimer.py).
⛔ Sem dado pessoal real: REs fictícios (9xxxx), carros e tabelas de teste.
"""
from datetime import datetime
from itertools import count
from typing import Optional

_seq = count(900000)


def ms(dt: Optional[datetime]) -> Optional[int]:
    return None if dt is None else int(dt.timestamp() * 1000)


def viagem(idx: int, sentido: int, status: str, prog: datetime, *, real: Optional[datetime] = None,
           duracao_min: int = 50, carro: Optional[str] = None, motivo: Optional[str] = None,
           departure: Optional[str] = "auto", fonte: str = "GPS") -> dict:
    from datetime import timedelta
    dep = str(next(_seq)) if departure == "auto" else departure
    return {
        "idx": idx,
        "tpToTs": sentido == 1,
        "status": status,
        "progStartMs": ms(prog),
        "progEndMs": ms(prog + timedelta(minutes=duracao_min)),
        "realStartMs": ms(real),
        "realEndMs": ms(real + timedelta(minutes=duracao_min)) if real and status == "done" else None,
        "startSource": fonte if real else None,
        "endSource": fonte if real and status == "done" else None,
        "delayStart": None,
        "delayEnd": None,
        "lostReason": motivo,
        "vehicle": carro,
        "departureId": dep,
        "startTripInputId": None,
    }


def tabela(nome: str, trips: list[dict], *, carro: str = "2412", motorista: str = "90001",
           cobrador: Optional[str] = "90002", table_id: Optional[str] = None) -> dict:
    for t in trips:
        if t.get("vehicle") is None:
            t["vehicle"] = carro
    return {
        "tableId": table_id or f"tb{nome}",
        "name": nome,
        "vehicle": carro,
        "driver": motorista,
        "charger": cobrador,
        "trips": trips,
    }


def timeline(tabelas: list[dict]) -> dict:
    trips = [t for tb in tabelas for t in tb["trips"]]
    return {
        "plannedTrips": len(trips),
        "realizedTrips": sum(t["status"] == "done" for t in trips),
        "lostTrips": sum(t["status"] == "lost" for t in trips),
        "missingTrips": sum(t["status"] == "missing" for t in trips),
        "onTimePercent": 87.5,
        "tables": tabelas,
    }
