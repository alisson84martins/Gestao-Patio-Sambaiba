"""Quem vê e quem corrige o quê no tempo real (fase 2, blocos G e H).

Regra NO BACKEND (bloco H):
- Quem tem só `fiscalizacao` (o fiscal) vê APENAS as linhas do seu turno
  ABERTO (fiscalizacao.turno_linha). Sem turno aberto → 403 "Abra o turno
  para ver a linha ao vivo". Vários fiscais em pontos diferentes ao mesmo
  tempo: cada um com as linhas do turno dele.
- Quem tem `fiscalizacao_painel` (coordenação) vê as linhas que coordena
  (fiscalizacao.linha_coordenador); ADMIN vê todas.
- Corrigir viagem (bloco G): `fiscalizacao` com escrita E a linha da viagem
  no turno aberto dele, do mesmo dia de serviço.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.fiscalizacao import LinhaCoordenador, Turno, TurnoLinha

MSG_SEM_TURNO = "Abra o turno para ver a linha ao vivo"
MSG_FORA_DO_TURNO = "Esta linha não está no seu turno aberto"


def tem_acesso(db: Session, funcionario_id: UUID, recurso: str, escrever: bool = False) -> bool:
    """Mesma consulta de app/core/deps.py::exige, sem levantar 403."""
    row = db.execute(
        text("SELECT pode_ler, pode_escrever FROM vw_acesso_efetivo WHERE funcionario_id = :fid AND recurso = :rec"),
        {"fid": funcionario_id, "rec": recurso},
    ).fetchone()
    return row is not None and bool(row.pode_escrever if escrever else row.pode_ler)


def eh_admin(db: Session, funcionario_id: UUID) -> bool:
    """Mesmo helper de app/routers/fiscalizacao.py::_eh_admin (função ADMIN no RBAC)."""
    row = db.execute(
        text("SELECT 1 FROM funcionario_funcao ff JOIN funcao f ON f.id = ff.funcao_id "
             "WHERE ff.funcionario_id = :fid AND ff.ativo AND f.codigo = 'ADMIN'"),
        {"fid": funcionario_id},
    ).first()
    return row is not None


def turno_aberto(db: Session, funcionario_id: UUID) -> Optional[Turno]:
    return db.execute(
        select(Turno).where(Turno.funcionario_id == funcionario_id, Turno.status == "ABERTO")
        .order_by(Turno.aberto_em.desc())
    ).scalars().first()


def linhas_do_turno(db: Session, turno_id: UUID) -> list[str]:
    return list(db.execute(
        select(TurnoLinha.linha_codigo).where(TurnoLinha.turno_id == turno_id).order_by(TurnoLinha.linha_codigo)
    ).scalars().all())


def linhas_do_coordenador(db: Session, funcionario_id: UUID) -> list[str]:
    return sorted(set(db.execute(
        select(LinhaCoordenador.linha_codigo).where(
            LinhaCoordenador.funcionario_id == funcionario_id, LinhaCoordenador.ativo.is_(True))
    ).scalars().all()))


def escopo(db: Session, funcionario_id: UUID) -> dict:
    """{"tipo": "todas" | "coordenador" | "turno" | "sem_turno", "linhas": [...] | None, "turno": Turno|None}"""
    if eh_admin(db, funcionario_id):
        return {"tipo": "todas", "linhas": None, "turno": None}
    if tem_acesso(db, funcionario_id, "fiscalizacao_painel"):
        return {"tipo": "coordenador", "linhas": linhas_do_coordenador(db, funcionario_id), "turno": None}
    turno = turno_aberto(db, funcionario_id)
    if turno is None:
        return {"tipo": "sem_turno", "linhas": [], "turno": None}
    return {"tipo": "turno", "linhas": linhas_do_turno(db, turno.id), "turno": turno}
