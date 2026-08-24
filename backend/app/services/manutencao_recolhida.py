"""Abre e fecha ficha_manutencao a partir de uma recolhida anormal (Blocos F
e migration 032).

🔴 ÚNICA ESCRITA CROSS-MÓDULO DE TODO O SISTEMA. Portaria é módulo separado
do Pátio (regra de fronteira — ver cabeçalho de app/models/portaria.py), mas
a ligação recolhida↔ficha é o PRODUTO desta feature: quando uma recolhida
anormal é registrada, a manutenção precisa saber sem que alguém replique o
registro à mão. Nenhuma outra tabela do Pátio é tocada por este módulo.

⚠️ EXCEÇÃO NOVA (migration 032) — `encerrar_ficha_de_recolhida` faz UPDATE,
não INSERT. Isso era proibido em letras maiúsculas no cabeçalho original
deste arquivo; a exceção é deliberada e ESTREITA: a ficha em questão nasceu
DESTA MESMA recolhida (é o `ficha_id` que ela mesma gravou em
abrir_ficha_de_recolhida) — fechar essa ficha é o outro lado do mesmo
produto, não uma escrita genérica em dado do Pátio. Isso NÃO abre precedente
pra este módulo fazer UPDATE em qualquer outra linha de ficha_manutencao,
onibus, alocacao_patio, fila, alerta ou escala — a regra de fronteira segue
valendo pra tudo o mais.

Ambas as funções rodam dentro de uma SAVEPOINT (Session.begin_nested) pra
isolar qualquer falha daqui do UPDATE/INSERT da própria recolhida — regra
número um: nada do lado da ficha pode impedir o registro ou o encerramento
da recolhida.
"""
from datetime import datetime, timezone
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


_DESFECHO_PARA_STATUS_FICHA = {
    "SEM_DEFEITO": StatusFichaEnum.CANCELADA,
    "SERVICO_EXECUTADO": StatusFichaEnum.CONCLUIDA,
}


def encerrar_ficha_de_recolhida(
    db: Session,
    *,
    ficha_id: Optional[UUID],
    desfecho: str,
) -> Optional[str]:
    """Espelha na ficha_manutencao o desfecho do encerramento da recolhida:
    SEM_DEFEITO -> CANCELADA, SERVICO_EXECUTADO -> CONCLUIDA (+ concluida_em).

    Devolve motivo_falha (None = ok, nada a reportar). Regra número um: se
    `ficha_id` for None — RA de motivo != DEFEITO, ou ficha que não pôde
    nascer (ver abrir_ficha_de_recolhida) — não há o que atualizar, e a RA
    encerra do mesmo jeito, sem erro nenhum."""
    if ficha_id is None:
        return None

    try:
        with db.begin_nested():
            ficha = db.get(FichaManutencao, ficha_id)
            if ficha is None:
                raise ValueError(f"ficha_manutencao {ficha_id} não encontrada.")
            ficha.status = _DESFECHO_PARA_STATUS_FICHA[desfecho]
            if ficha.status == StatusFichaEnum.CONCLUIDA:
                # Rede de segurança pro ambiente sem o trigger
                # fn_ficha_concluida_em (SQLite dos testes de pytest) — em
                # produção o trigger (migration 006) preenche de qualquer
                # forma, e só entra em ação quando concluida_em ainda é NULL,
                # então gravar aqui também não conflita com ele.
                ficha.concluida_em = datetime.now(timezone.utc)
            db.flush()
        return None
    except Exception as exc:  # noqa: BLE001 — regra número um: nunca propaga
        return f"Não foi possível atualizar a ficha de manutenção: {exc}"
