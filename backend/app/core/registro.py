"""Normalizador único de RE — trim, maiúsculas e o cuidado de nunca virar
número (RE pode ter zero à esquerda: "01904").

Espelha o mesmo arranjo de app/core/placa.py — ⛔ não duplicar em nenhum
outro lugar do backend; todo schema que recebe RE importa daqui.

RE não é só numérico: existe RE alfanumérico (letra), mas SÓ de gerente
geral pra cima — diretoria e secretaria da presidência, pouquíssimas
pessoas (ex.: "A4011"). Motorista, cobrador, encarregado, coordenador de
tráfego, fiscal, mecânico, apontador, operador de pátio e plantonista têm
RE só numérico, sempre. A distinção de quem pode digitar letra é feita na
UI (frontend-v3/assets/js/mascaras.js — tipos 're' × 're-numerico'); aqui
só normaliza o que chegou, não valida quem pode ter letra.
"""
from typing import Annotated, Optional

from pydantic import BeforeValidator


def normalizar_re(valor: Optional[str]) -> Optional[str]:
    """Trim + maiúsculas, mesmo cuidado da placa. String vazia vira None —
    campo em branco é 'não informado', não um RE literal ''."""
    if valor is None:
        return valor
    limpo = valor.strip().upper()
    return limpo or None


def normalizar_re_obrigatorio(valor: Optional[str]) -> str:
    """Mesma normalização de normalizar_re, mas devolve '' em vez de None
    quando o valor vem vazio — em campo obrigatório, a recusa deve vir do
    `min_length=1` do Field (422 de string curta), não de um erro de tipo
    ('campo ausente') que confundiria mais do que ajudaria."""
    return normalizar_re(valor) or ""


# Annotated prontos pra Field() nos schemas — mesmo padrão de core/placa.py.
ReNormalizado = Annotated[Optional[str], BeforeValidator(normalizar_re)]
ReNormalizadoObrigatorio = Annotated[str, BeforeValidator(normalizar_re_obrigatorio)]
