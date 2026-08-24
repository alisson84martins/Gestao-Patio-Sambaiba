"""Dependencies de autenticação e controle de acesso para os endpoints."""
from typing import Annotated, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import JWTError, decode_access_token
from app.models import Usuario
from app.models.cadastro import Funcionario, UsuarioLogin
from app.services.auditoria import ip_do_request, registrar_log_acesso

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=True)


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Usuario:
    """Decodifica o JWT e retorna o Usuario.

    Compatível com os dois sistemas:
    - JWT novo (sub = funcionario.id): busca Funcionario → Usuario pelo RE.
    - JWT antigo (sub = usuario.id): busca Usuario diretamente.

    SEV-05 (fechamento): também recusa `Funcionario.status == 'DESLIGADO'`,
    igual a `get_current_funcionario`. Antes desta checagem, `DESLIGADO`
    sozinho (sem também desativar o login em `UsuarioLogin.ativo`) não
    bloqueava nada nos 12 routers legados — só nos 5 do RBAC novo. Busca o
    `Funcionario` pelo RE do `Usuario` quando ele não veio de graça no
    caminho (JWT antigo); no caminho novo o `Funcionario` já foi carregado
    antes, sem consulta extra.

    ⚠️ DEPENDE de uma linha espelho em `usuario` para todo funcionario com
    login — os 14 routers legados do Pátio (CurrentUser/AdminUser/etc.) só
    conhecem `usuario`, não `Funcionario`. Sem o espelho, quem foi criado só
    pelo fluxo novo (funcionario + usuario_login) cai no "func é not None
    mas não existe usuario com esse RE" e toma 401 aqui — foi exatamente o
    bug do Gerente Operacional cadastrado em 30/07 (v3.0-dev, fix(auth)
    "cria espelho em usuario para contas criadas pelo fluxo novo").
    O espelho é criado por POST /funcionarios/{id}/login (ver
    _criar_ou_atualizar_espelho_usuario em app/routers/funcionarios.py) e
    foi backfilled uma vez pela migration
    database/migrations/014-espelho-usuario-transicao.sql.
    Este fallback por RE sai quando os 14 routers legados migrarem para
    `exige()`/Funcionario e a tabela `usuario` for aposentada — não antes,
    porque não há "mágica" que resolva isso dentro da própria requisição
    sem essa linha existir no banco.
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise cred_exc
        sub_id = UUID(sub)
    except (JWTError, ValueError):
        raise cred_exc

    # Tenta encontrar como Usuario (JWT antigo)
    user = db.get(Usuario, sub_id)
    if user is not None:
        if not user.ativo:
            raise cred_exc
        func = db.execute(
            select(Funcionario).where(Funcionario.re == user.re)
        ).scalar_one_or_none()
        # Sem Funcionario correspondente não há status pra checar — conta
        # puramente legada, de antes do cadastro central existir.
        if func is not None:
            _recusar_se_desligado(func)
        return user

    # JWT novo: sub = funcionario.id → busca Usuario pelo RE (para compatibilidade)
    func = db.get(Funcionario, sub_id)
    if func is None:
        raise cred_exc
    _recusar_se_desligado(func)

    user = db.execute(select(Usuario).where(Usuario.re == func.re)).scalar_one_or_none()
    if user is None or not user.ativo:
        raise cred_exc
    return user


def get_current_funcionario(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Funcionario:
    """Retorna o Funcionario autenticado (sistema novo).

    Compatível com os dois JWTs:
    - JWT novo (sub = funcionario.id): busca Funcionario diretamente.
    - JWT antigo (sub = usuario.id): busca Usuario → Funcionario pelo RE.
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise cred_exc
        sub_id = UUID(sub)
    except (JWTError, ValueError):
        raise cred_exc

    # Tenta como Funcionario (JWT novo)
    func = db.get(Funcionario, sub_id)
    if func is not None:
        _recusar_se_desligado(func)
        ul = db.execute(
            select(UsuarioLogin).where(UsuarioLogin.funcionario_id == func.id)
        ).scalar_one_or_none()
        if ul is not None and not ul.ativo:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acesso desativado. Procure o administrador.",
            )
        return func

    # Fallback: JWT antigo (sub = usuario.id) → busca Funcionario pelo RE
    user = db.get(Usuario, sub_id)
    if user is None or not user.ativo:
        raise cred_exc

    func = db.execute(
        select(Funcionario).where(Funcionario.re == user.re)
    ).scalar_one_or_none()
    if func is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta conta não tem cadastro no sistema novo. Procure o administrador.",
        )
    _recusar_se_desligado(func)
    return func


def _recusar_se_desligado(func: Funcionario) -> None:
    """SEV-05: Funcionario.status nunca era consultado por nenhuma camada
    de autenticação — marcar alguém como DESLIGADO só mudava um rótulo na
    tela de cadastro, sem revogar nada.

    ⚠️ Recusa SÓ 'DESLIGADO'. AFASTADO e FÉRIAS são gente que volta — se
    esta função barrasse os dois, um funcionário ativo ficaria trancado
    fora do sistema no meio do turno, na segunda-feira em que voltasse.
    """
    if func.status == "DESLIGADO":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Funcionário desligado. Procure o administrador.",
        )


def exige(recurso: str, escrever: bool = False) -> Callable:
    """Factory de dependency RBAC: 403 se a pessoa não tiver o acesso pedido.

    Retorna Funcionario autenticado com acesso verificado.
    Uso: Depends(exige("alocacao", escrever=True))
    """
    def checker(
        request: Request,
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Funcionario:
        func = get_current_funcionario(token, db)

        row = db.execute(
            text(
                "SELECT pode_ler, pode_escrever "
                "FROM vw_acesso_efetivo "
                "WHERE funcionario_id = :fid AND recurso = :rec"
            ),
            {"fid": func.id, "rec": recurso},
        ).fetchone()

        tem_acesso = row is not None and (row.pode_escrever if escrever else row.pode_ler)
        if not tem_acesso:
            # Trilha de auditoria mínima (migration 021) — negativa de
            # 403 via exige(), o gate RBAC usado pela maioria dos
            # routers novos (não cobre os 12 legados nem a trava de
            # autoria em ocorrências — ver cabeçalho da migration 021).
            registrar_log_acesso(
                db, "NEGADO_403",
                funcionario_id=func.id, recurso=recurso, ip=ip_do_request(request),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sem permissão para {'escrever em' if escrever else 'ler'} '{recurso}'",
            )
        return func

    return checker


def exige_qualquer(*recursos: str, escrever: bool = False) -> Callable:
    """Como exige(), mas passa se a pessoa tiver o acesso pedido em QUALQUER
    um dos recursos passados — 403 só quando nenhum bate.

    Uso: um endpoint compartilhado por duas ‘pontas’ do RBAC que antes eram
    um recurso só e foram separadas (ex.: migration 037 — Bloco I — separou
    `recolhida_anormal`/registrar de `recolhida_tratativa`/tratar; GET
    /portaria/recolhidas é lido pelas duas). Mesmo espírito do
    `qualquerAcesso` do frontend (nav.js), agora do lado do backend.

    Uso: Depends(exige_qualquer("recolhida_anormal", "recolhida_tratativa"))
    """
    def checker(
        request: Request,
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Funcionario:
        func = get_current_funcionario(token, db)

        rows = db.execute(
            text(
                "SELECT recurso, pode_ler, pode_escrever "
                "FROM vw_acesso_efetivo "
                "WHERE funcionario_id = :fid AND recurso = ANY(:recs)"
            ),
            {"fid": func.id, "recs": list(recursos)},
        ).fetchall()

        tem_acesso = any((row.pode_escrever if escrever else row.pode_ler) for row in rows)
        if not tem_acesso:
            # Loga só o primeiro recurso da lista — trilha mínima (migration
            # 021), mesmo padrão de exige(); não é o dado que decide a
            # negação (é a AUSÊNCIA de qualquer um deles).
            registrar_log_acesso(
                db, "NEGADO_403",
                funcionario_id=func.id, recurso=recursos[0], ip=ip_do_request(request),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sem permissão para {'escrever em' if escrever else 'ler'} "
                       + " ou ".join(recursos),
            )
        return func

    return checker


# ─── Aliases legados — mantidos para os 14 routers existentes não quebrarem ──
# Após validação completa do RBAC, serão atualizados um a um para usar exige().

CurrentUser = Annotated[Usuario, Depends(get_current_user)]


def require_admin(
    user: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    """Garante que o usuário tem perfil ADMIN (sistema antigo)."""
    from app.models.enums import PerfilUsuarioEnum
    if user.perfil != PerfilUsuarioEnum.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas administradores podem realizar esta operação",
        )
    return user


def require_perfis(*perfis) -> Callable:
    """Factory: exige um dos perfis informados (sistema antigo)."""
    def checker(user: Annotated[Usuario, Depends(get_current_user)]) -> Usuario:
        from app.models.enums import PerfilUsuarioEnum
        if user.perfil not in perfis:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Perfil sem permissão para esta operação",
            )
        return user
    return checker


AdminUser = Annotated[Usuario, Depends(require_admin)]


def _require_operador_ou_admin(
    user: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    from app.models.enums import PerfilUsuarioEnum
    permitidos = {
        PerfilUsuarioEnum.OPERADOR_PATIO,
        PerfilUsuarioEnum.COORDENADOR,
        PerfilUsuarioEnum.ADMIN,
    }
    if user.perfil not in permitidos:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Perfil sem permissão para esta operação",
        )
    return user


OperadorOuAdmin = Annotated[Usuario, Depends(_require_operador_ou_admin)]


def _require_mecanico_ou_superior(
    user: Annotated[Usuario, Depends(get_current_user)],
) -> Usuario:
    from app.models.enums import PerfilUsuarioEnum
    permitidos = {
        PerfilUsuarioEnum.MECANICO,
        PerfilUsuarioEnum.OPERADOR_PATIO,
        PerfilUsuarioEnum.COORDENADOR,
        PerfilUsuarioEnum.ADMIN,
    }
    if user.perfil not in permitidos:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Perfil sem permissão para esta operação",
        )
    return user


MecanicoOuSuperior = Annotated[Usuario, Depends(_require_mecanico_ou_superior)]
