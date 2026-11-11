"""Cenários sintéticos para os testes da fase 2 (blocos D–I).

Leituras do SIM ao longo do traçado real da 1726-10 e viagens do NIMER no
formato de app/services/fiscalizacao_tempo_real.py::viagem_como_dict.
⛔ Sem dado pessoal real: REs fictícios (9xxxx).
"""
from datetime import datetime, timedelta, timezone
from itertools import count

from app.services.monitoramento_linhas import GARAGEM_G3, obter_linha

LINHA = obter_linha("1726-10")
DIA = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # 09:00 em SP
_ids = count(1)


def em(minutos: float) -> datetime:
    return DIA + timedelta(minutes=minutos)


def viagem(tabela, sentido, prog_min, status, *, carro="2412", real_min=None, motivo=None,
           motorista="90011", cobrador="90012", sem_motorista=False, duracao=50, id=None):
    return {
        "id": id or next(_ids), "tabela": tabela, "sentido": sentido, "status": status,
        "prog_inicio": None if prog_min is None else em(prog_min),
        "prog_fim": None if prog_min is None else em(prog_min + duracao),
        "real_inicio": None if real_min is None else em(real_min), "real_fim": None,
        "fonte_inicio": "GPS" if real_min is not None else None, "atraso_inicio_min": None,
        "motivo": motivo, "carro": carro, "motorista_re": None if sem_motorista else motorista,
        "cobrador_re": cobrador, "sem_motorista": sem_motorista,
    }


def parado(prefixo, sentido, ponto, de_min, ate_min, passo=1, linha=None):
    """Leituras paradas num ponto, de `de_min` até `ate_min` (inclusive)."""
    out, m = [], de_min
    while m <= ate_min:
        out.append({"prefixo": prefixo, "linha": linha or LINHA.codigo, "em": em(m), "s": sentido,
                    "la": ponto[0], "lo": ponto[1], "vel": 0})
        m += passo
    return out


def rodando(prefixo, sentido, de_min, minutos=40, passo_pontos=12, linha=None, tracado=None):
    """Sai do terminal de origem de `sentido` em `de_min` e anda pelo traçado 1 leitura/min."""
    pts = (tracado or LINHA.tracados)[sentido]
    out = []
    for k in range(minutos):
        i = min(k * passo_pontos, len(pts) - 1)
        out.append({"prefixo": prefixo, "linha": linha or LINHA.codigo, "em": em(de_min + k), "s": sentido,
                    "la": pts[i][0], "lo": pts[i][1], "vel": 30})
    return out


def viagem_sim(prefixo, sentido, saida_min, espera=5, minutos=40, linha=None):
    """Espera `espera` min no terminal de origem (centro real) e sai em `saida_min`."""
    origem = "TP" if LINHA.destino[sentido] == "TS" else "TS"
    centro = LINHA.terminais[origem][0]
    return parado(prefixo, sentido, centro, saida_min - espera, saida_min - 1, linha=linha) \
        + rodando(prefixo, sentido, saida_min, minutos, linha=linha)


def na_garagem(prefixo, de_min, ate_min, sentido=1):
    return parado(prefixo, sentido, GARAGEM_G3, de_min, ate_min)


def calcular(viagens, leituras, agora_min=600, correcoes=None, tem_sim=True, linha=None):
    from app.services.fiscalizacao_tempo_real import calcular_linha
    linha = linha or LINHA
    return calcular_linha(
        viagens_nimer=viagens, correcoes=correcoes or {}, leituras=leituras,
        terminais=linha.terminais if linha.tem_mapa else {}, destino=linha.destino, garagem=GARAGEM_G3,
        agora=em(agora_min), tem_sim=tem_sim,
    )
