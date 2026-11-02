"""Linha espelho em `usuario` para quem ganha login pelo fluxo novo.

Mora em services/ (não em routers/funcionarios.py, onde nasceu) para poder
ser usada também por app/services/acesso_motorista.py sem import circular:
routers/funcionarios.py importa routers/ocorrencias.py, que importa o
serviço de acesso do motorista.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.models.enums import PerfilUsuarioEnum
from app.models.pessoas import Usuario

# Mapa de função (nova) → perfil legado (usuario.perfil, NOT NULL, 5 valores).
# Usado só para satisfazer a constraint da linha espelho em `usuario` — ver
# criar_ou_atualizar_espelho_usuario(). Campo deprecado, sem efeito no RBAC.
_PERFIL_LEGADO_POR_FUNCAO = {
    "ADMIN": PerfilUsuarioEnum.ADMIN,
    "COORDENADOR_TRAFEGO": PerfilUsuarioEnum.COORDENADOR,
    "OPERADOR_PATIO": PerfilUsuarioEnum.OPERADOR_PATIO,
    "MECANICO": PerfilUsuarioEnum.MECANICO,
}


def perfil_legado_de(funcao_codigo: Optional[str]) -> PerfilUsuarioEnum:
    """Deriva o usuario.perfil (legado) a partir do código da função principal."""
    return _PERFIL_LEGADO_POR_FUNCAO.get(funcao_codigo, PerfilUsuarioEnum.MOTORISTA)


def criar_ou_atualizar_espelho_usuario(db: Session, func: Funcionario, senha_hash: str) -> None:
    """Mantém uma linha espelho em `usuario` para quem ganha login pelo fluxo novo.

    ⚠️ SHIM TRANSITÓRIO — existe porque get_current_user (app/core/deps.py)
    ainda cai no JWT novo → Funcionario → busca Usuario pelo RE; sem essa
    linha, todo endpoint que usa CurrentUser (os 14 routers do Pátio) nega
    401 pra quem foi cadastrado só em funcionario + usuario_login. Ver
    database/migrations/014-espelho-usuario-transicao.sql para o backfill
    de quem já tinha login antes deste fix.
    Sai quando os 14 routers legados passarem a usar `exige()`/Funcionario
    diretamente e a tabela `usuario` for aposentada.
    """
    funcao_principal = db.execute(
        select(Funcao.codigo)
        .join(FuncionarioFuncao, FuncionarioFuncao.funcao_id == Funcao.id)
        .where(FuncionarioFuncao.funcionario_id == func.id, FuncionarioFuncao.principal.is_(True))
    ).scalar_one_or_none()
    perfil_legado = perfil_legado_de(funcao_principal)

    espelho = db.execute(select(Usuario).where(Usuario.re == func.re)).scalar_one_or_none()
    if espelho is None:
        db.add(Usuario(
            re=func.re,
            nome=func.nome,
            senha_hash=senha_hash,
            perfil=perfil_legado,
            ativo=True,
            cpf=func.cpf,
        ))
    else:
        espelho.senha_hash = senha_hash
        espelho.ativo = True
        espelho.nome = func.nome
