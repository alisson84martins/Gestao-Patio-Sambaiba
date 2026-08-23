"""Validador único de placa — normalização e checagem de formato.

Espelha exatamente as duas regex de frontend-v3/assets/js/mascaras.js
(formatarPlaca/placaValida) — ⛔ não duplicar em nenhum outro lugar do
backend; todo schema que recebe placa importa daqui.

🔴 D10 (migration 024, portaria.veiculo.placa) — regra número um do
módulo Portaria, e a mesma vale para ocorrência/pré-ocorrência: nenhum
registro é recusado por causa do formato da placa. Placa provisória ou de
outro país existe e não bate nenhuma das duas regex abaixo. `placa_valida`
existe só para SINALIZAR (front: aviso visual em mascaras.js; back:
indicador `placa_atipica` no cadastro de veículo — ver
routers/portaria_veiculos.py) — nunca para bloquear com 422, aqui ou em
schemas/ocorrencia.py e schemas/pre_ocorrencia.py.
"""
import re
from typing import Annotated, Optional

from pydantic import BeforeValidator

_LIMPA_RE = re.compile(r"[^A-Z0-9]")
_PLACA_ANTIGA_RE = re.compile(r"^[A-Z]{3}\d{4}$")
_PLACA_MERCOSUL_RE = re.compile(r"^[A-Z]{3}\d[A-Z]\d{2}$")


def normalizar_placa(valor: Optional[str]) -> Optional[str]:
    """Maiúscula, sem hífen/espaço/ponto. 'abc-1d23' -> 'ABC1D23'.

    Não valida formato (D10) — só limpa o que veio, mesmo que o resultado
    não bata em nenhuma das duas regex de placa_valida.
    """
    if valor is None:
        return None
    return _LIMPA_RE.sub("", valor.strip().upper())


def placa_valida(valor: Optional[str]) -> bool:
    """Formato antigo (AAA0000) ou Mercosul (AAA0A00). ⛔ Nunca usado para
    recusar registro — só para sinalizar (ver docstring do módulo)."""
    limpa = normalizar_placa(valor)
    if not limpa:
        return False
    return bool(_PLACA_ANTIGA_RE.match(limpa) or _PLACA_MERCOSUL_RE.match(limpa))


# Annotated prontos pra Field() nos schemas — mesmo padrão que já existia
# em schemas/portaria.py, agora centralizado aqui pra não duplicar.
PlacaNormalizada = Annotated[str, BeforeValidator(normalizar_placa)]
PlacaNormalizadaOpcional = Annotated[Optional[str], BeforeValidator(normalizar_placa)]
