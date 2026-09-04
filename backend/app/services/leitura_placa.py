"""Reconhecimento de placa por foto — motor fast-alpr (plugado em 04/09/2026).

Ver _handoff-claude/PROMPT-leitura-placa-engine.md. Medido em produção ANTES
de plugar (Bloco 0 do PROMPT-leitura-placa.md, com o servidor de verdade):
111 MB de RAM ao carregar, 1,6s pra carregar o modelo, 0,02s por foto.
Modelos globais (não específicos de placa brasileira) — a taxa de acerto que
importa é a medição de P13 nos dias em produção, não a documentação da lib.

🔴 PONTO ÚNICO DE TROCA — `reconhecer_placa` é a única função que
`routers/portaria.py` chama. Trocar a engine de novo é mexer só neste
arquivo — nunca no router, nos schemas, ou nos testes de RBAC/upload/P4,
que só conhecem esta assinatura.

R1 — instância única, preguiçosa. `ALPR(...)` carrega o modelo em ~1,6s;
construir isso a cada requisição custaria 1,6s em CADA foto (80x mais lento
que os 0,02s medidos). `_alpr_lock` protege a construção porque o endpoint
chama esta função via `asyncio.to_thread` — duas fotos podem chegar juntas.

R2 — `warmup()` é chamado no lifespan do FastAPI (app/main.py), só quando
`leitura_placa_ativa` é true, e NUNCA derruba o app se falhar: loga e
segue — a leitura cai em "não achou" até alguém corrigir, a digitação
continua funcionando (P7). Sem warmup, a primeira foto do plantão pagaria
o custo de carregar o modelo (e, numa máquina/cache novos, de baixá-lo).

R3 — uma thread só pro ONNX Runtime. O servidor tem 2 vCPU divididas com
FastAPI, Postgres e nginx; sem limite, o runtime abriria threads e uma
leitura de placa engasgaria coisas sem nada a ver com a Portaria (a
alocação do Pátio, por exemplo). Com 0,02s por foto, sobra folga de sobra
— ela se gasta melhor protegendo o resto do sistema.

Testes NUNCA devem carregar a engine de verdade nem baixar modelo —
sobrescrevem o atributo do módulo:

    monkeypatch.setattr(leitura_placa, "reconhecer_placa", fake_fn)

— nunca a função importada por nome em outro módulo (mesmo cuidado que
test_pre_ocorrencia.py já documenta pra `httpx` dentro de services/n8n.py).
Testes do módulo em si (não do endpoint) sobrescrevem `_get_alpr`/`_alpr`,
nunca deixando `_construir_alpr()` rodar de verdade.
"""
import logging
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
from fast_alpr import ALPR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeituraPlacaResultado:
    placa_lida: Optional[str]
    # 0.0–1.0. "Não achou" sempre devolve 0.0 junto com placa_lida=None —
    # confianca só tem sentido quando há placa_lida.
    confianca: float


# R1 — modelos medidos no servidor em 04/09/2026 (ver cabeçalho). Nomes
# explícitos mesmo sendo o default da lib hoje: documentar o que foi
# medido é mais seguro que confiar num default que pode mudar numa
# atualização futura do fast-alpr.
_DETECTOR_MODEL = "yolo-v9-t-384-license-plate-end2end"
_OCR_MODEL = "cct-xs-v2-global-model"

_alpr: Optional[ALPR] = None
_alpr_lock = threading.Lock()


def _sess_options_uma_thread() -> ort.SessionOptions:
    """R3 — trava o ONNX Runtime numa thread só."""
    opcoes = ort.SessionOptions()
    opcoes.intra_op_num_threads = 1
    opcoes.inter_op_num_threads = 1
    return opcoes


def _construir_alpr() -> ALPR:
    return ALPR(
        detector_model=_DETECTOR_MODEL,
        detector_sess_options=_sess_options_uma_thread(),
        ocr_model=_OCR_MODEL,
        ocr_sess_options=_sess_options_uma_thread(),
    )


def _get_alpr() -> ALPR:
    """R1 — instância única, construída na primeira chamada (ou no
    warmup() do lifespan). Dupla checagem: o lock só entra em jogo na
    construção; chamadas depois que `_alpr` já existe nem tocam nele."""
    global _alpr
    if _alpr is None:
        with _alpr_lock:
            if _alpr is None:
                _alpr = _construir_alpr()
    return _alpr


def warmup() -> None:
    """R2 — chamado no lifespan do FastAPI, só quando leitura_placa_ativa
    é true. Nunca deixa o app cair se a engine não carregar."""
    inicio = time.monotonic()
    try:
        _get_alpr()
    except Exception:
        logger.exception(
            "Falha ao carregar a engine de leitura de placa no warm-up — "
            "a leitura vai responder 'não achou' até alguém corrigir."
        )
        return
    logger.info("Engine de leitura de placa carregada em %.2fs", time.monotonic() - inicio)


def _confianca_ocr(confidence) -> float:
    """OcrResult.confidence vem como float único ou uma lista (um valor
    por caractere) — normaliza pra um número só."""
    if isinstance(confidence, list):
        return statistics.fmean(confidence) if confidence else 0.0
    return float(confidence)


def reconhecer_placa(imagem: bytes) -> LeituraPlacaResultado:
    """R4 — bytes -> imagem -> placa. Nunca grava em disco (P4): decodifica
    direto de um buffer numpy em memória. Qualquer falha (imagem
    corrompida, engine fora do ar, exceção interna da lib) vira "não
    achou", nunca uma exceção que propague pro endpoint — P7 manda cair
    na digitação, não numa tela de erro."""
    try:
        buffer = np.frombuffer(imagem, dtype=np.uint8)
        quadro = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if quadro is None:
            return LeituraPlacaResultado(placa_lida=None, confianca=0.0)

        resultados = _get_alpr().predict(quadro)
    except Exception:
        logger.exception("Falha na leitura de placa — devolvendo 'não achou'.")
        return LeituraPlacaResultado(placa_lida=None, confianca=0.0)

    # Mais de uma placa na foto (carro atrás na fila) -> fica com a de
    # maior confiança de OCR — é essa confiança que o card usa e a
    # medição de P13 registra, então é o critério certo de desempate.
    melhor: Optional[LeituraPlacaResultado] = None
    for resultado in resultados:
        ocr = resultado.ocr
        if ocr is None or not ocr.text:
            continue
        confianca = _confianca_ocr(ocr.confidence)
        if melhor is None or confianca > melhor.confianca:
            melhor = LeituraPlacaResultado(placa_lida=ocr.text, confianca=confianca)

    return melhor or LeituraPlacaResultado(placa_lida=None, confianca=0.0)
