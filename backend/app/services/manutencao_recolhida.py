"""Abre ficha_manutencao a partir de uma recolhida anormal (Bloco F).

🔴 ÚNICA ESCRITA CROSS-MÓDULO DE TODO O SISTEMA. Portaria é módulo separado
do Pátio (regra de fronteira — ver cabeçalho de app/models/portaria.py), mas
a ligação recolhida↔ficha é o PRODUTO desta feature: quando uma recolhida
anormal é registrada, a manutenção precisa saber sem que alguém replique o
registro à mão. A escrita aqui é sempre um INSERT de linha NOVA em
public.ficha_manutencao — ⛔ nunca um UPDATE ou DELETE em dado do Pátio, e
nenhuma outra tabela do Pátio é tocada por este módulo.

Roda dentro de uma SAVEPOINT (Session.begin_nested) pra isolar qualquer
falha daqui do INSERT da própria recolhida — regra número um: a ficha não
poder nascer nunca impede o registro da recolhida.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalogos import TipoDefeito
from app.models.enums import StatusFichaEnum
from app.models.operacoes import FichaManutencao


def abrir_ficha_de_recolhida(
    db: Session,
    *,
    onibus_id: Optional[UUID],
    motorista_id: Optional[UUID],
    tipo_defeito_codigo: str,
    relato: Optional[str],
) -> tuple[Optional[UUID], Optional[str]]:
    """Devolve (ficha_id, motivo_falha). Nunca propaga exceção — falhou por
    qualquer motivo, devolve (None, motivo) e quem chamou segue salvando a
    recolhida do mesmo jeito."""
    if onibus_id is None:
        return None, "Prefixo não resolveu para um ônibus cadastrado — ficha não pôde nascer."

    try:
        with db.begin_nested():
            tipo_defeito = db.execute(
                select(TipoDefeito).where(
                    TipoDefeito.codigo == tipo_defeito_codigo, TipoDefeito.ativo.is_(True)
                )
            ).scalar_one_or_none()
            if tipo_defeito is None:
                raise ValueError(f"Tipo de defeito '{tipo_defeito_codigo}' não encontrado no catálogo.")

            descricao = "[Recolhida anormal]" + (f" {relato}" if relato else "")
            ficha = FichaManutencao(
                onibus_id=onibus_id,
                motorista_id=motorista_id,
                tipo_defeito_id=tipo_defeito.id,
                descricao=descricao,
                status=StatusFichaEnum.ABERTA,
            )
            db.add(ficha)
            db.flush()
        return ficha.id, None
    except Exception as exc:  # noqa: BLE001 — regra número um: nunca propaga
        return None, f"Não foi possível abrir a ficha automaticamente: {exc}"
