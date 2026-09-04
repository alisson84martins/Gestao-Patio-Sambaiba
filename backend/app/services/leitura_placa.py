"""Reconhecimento de placa por foto — motor PLUGÁVEL, ainda não escolhido.

Ver _handoff-claude/PROMPT-leitura-placa.md, Bloco 0/1. A escolha da engine
de verdade (Tesseract local, EasyOCR, API paga, etc.) depende de medir
RAM/CPU/tempo por foto do servidor de produção — decisão que só o Alisson
toma, com o número na mão, nunca por chute. `qrcode` já derrubou o serviço
em 22/08 por consumir memória demais; a engine errada tem o mesmo risco.

🔴 PONTO ÚNICO DE TROCA — `reconhecer_placa` é a única função que
`routers/portaria.py` chama. Plugar a engine de verdade é reescrever
`_stub_sem_engine` (ou trocar a atribuição no fim do arquivo) por uma
implementação real — nunca mexer no router, nos schemas, ou nos testes de
RBAC/upload/P4, que só conhecem esta assinatura.

O stub abaixo nunca lê a imagem de verdade: devolve sempre "não achou"
(placa_lida=None, confianca=0.0), documentado por P6/P7 do prompt — placa
não encontrada e falha de leitura caem no mesmo caminho que já existe
(digitação manual), então mesmo sem engine nenhuma o endpoint responde
200 e a tela funciona. Testes que querem simular acerto sobrescrevem o
atributo do módulo:

    monkeypatch.setattr(leitura_placa, "reconhecer_placa", fake_fn)

— nunca a função importada por nome em outro módulo (o mesmo cuidado que
test_pre_ocorrencia.py já documenta pra `httpx` dentro de services/n8n.py).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LeituraPlacaResultado:
    placa_lida: Optional[str]
    # 0.0–1.0. O stub sempre devolve 0.0 junto com placa_lida=None —
    # confianca só tem sentido quando há placa_lida.
    confianca: float


def _stub_sem_engine(imagem: bytes) -> LeituraPlacaResultado:
    """Implementação placeholder. Nenhuma engine ligada ainda — sempre
    'não achou'. Não é decisão de produto (P6/P7 já cobrem esse caminho),
    é só o que existe antes de alguém plugar a engine de verdade aqui."""
    return LeituraPlacaResultado(placa_lida=None, confianca=0.0)


reconhecer_placa = _stub_sem_engine
