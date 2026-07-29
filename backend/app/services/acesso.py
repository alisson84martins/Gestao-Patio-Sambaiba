"""Serviço de acesso RBAC: consulta as views de acesso efetivo e módulos."""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.cadastro import ModuloDisponivel


def acesso_efetivo(db: Session, funcionario_id: UUID) -> dict[str, dict[str, bool]]:
    """Retorna o acesso efetivo da pessoa por recurso, consultando vw_acesso_efetivo.

    A view já aplica a regra de união de funções + override individual da tabela permissao.
    Retorna: {"alocacao": {"ler": True, "escrever": False}, ...}
    """
    rows = db.execute(
        text(
            "SELECT recurso, pode_ler, pode_escrever "
            "FROM vw_acesso_efetivo "
            "WHERE funcionario_id = :fid"
        ),
        {"fid": funcionario_id},
    ).fetchall()
    return {
        row.recurso: {"ler": bool(row.pode_ler), "escrever": bool(row.pode_escrever)}
        for row in rows
    }


def modulos_disponiveis(db: Session, funcionario_id: UUID) -> list[ModuloDisponivel]:
    """Retorna os módulos que a pessoa pode abrir, consultando vw_modulos_usuario.

    Um módulo aparece se a pessoa tiver leitura em ao menos um recurso dele.
    """
    rows = db.execute(
        text(
            "SELECT modulo, modulo_nome, modulo_descricao, pode_escrever, recursos_liberados "
            "FROM vw_modulos_usuario "
            "WHERE funcionario_id = :fid "
            "ORDER BY modulo_ordem"
        ),
        {"fid": funcionario_id},
    ).fetchall()
    return [
        ModuloDisponivel(
            modulo=row.modulo,
            modulo_nome=row.modulo_nome,
            modulo_descricao=row.modulo_descricao,
            pode_escrever=bool(row.pode_escrever),
            recursos_liberados=row.recursos_liberados,
        )
        for row in rows
    ]
