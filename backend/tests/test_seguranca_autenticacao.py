"""Evidência da Fase 1 da auditoria de segurança (2026-08-11).

Ver _handoff-claude/RELATORIO-SEGURANCA-2026-08-10.md para o achado completo,
severidade e recomendação de cada item. Este arquivo só prova o comportamento
com código real — não corrige nada (auditoria em modo somente leitura).

Padrão do projeto: teste o comportamento ERRADO, não só o certo. Achado
confirmado mas ainda não corrigido é marcado com @pytest.mark.xfail(strict=True)
para a suíte continuar verde sem esconder o problema — se alguém corrigir o
código, o teste passa a passar e o xfail estoura (strict=True), forçando quem
corrigiu a vir tirar a marca.
"""
import base64
import json
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.security import JWTError, decode_access_token
from app.main import app
from app.routers import auth as auth_router_mod


# ─── 1.2 — Rate limit por IP, sem bloqueio por conta ──────────────────────────


class _FakeDBSemUsuario:
    """DB que nunca encontra ninguém — toda tentativa de login vira 401,
    igual a uma senha errada de verdade. É o que dá pra simular sem banco:
    o que importa aqui é *quantas* tentativas passam antes do 429, não o
    resultado da autenticação em si.
    """

    def execute(self, *args, **kwargs):
        class _R:
            def scalar_one_or_none(self):
                return None
        return _R()


@pytest.fixture(autouse=True)
def _limpa_rate_limit():
    """_LOGIN_TENTATIVAS é dict de módulo — sem limpar, um teste vaza pro
    outro (mesmo "unknown" de TestClient em request.client.host)."""
    auth_router_mod._LOGIN_TENTATIVAS.clear()
    yield
    auth_router_mod._LOGIN_TENTATIVAS.clear()


@pytest.fixture
def cliente_auth():
    from app.core.database import get_db
    app.dependency_overrides[get_db] = lambda: _FakeDBSemUsuario()
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_rate_limit_bloqueia_na_11a_tentativa_do_mesmo_ip(cliente_auth):
    """Confirma A2: 10/min por IP. As 10 primeiras devem cair em 401
    (RE/senha incorretos); a 11ª tem que ser 429, não mais uma tentativa
    de autenticação de verdade."""
    respostas = [
        cliente_auth.post("/auth/login", json={"re": "10001", "senha": "errada"})
        for _ in range(10)
    ]
    assert all(r.status_code == 401 for r in respostas), [r.status_code for r in respostas]

    bloqueada = cliente_auth.post("/auth/login", json={"re": "10001", "senha": "errada"})
    assert bloqueada.status_code == 429


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SEV — não existe bloqueio por CONTA, só por IP (auth.py:24-38, "
        "_LOGIN_TENTATIVAS chaveado por request.client.host). Um atacante que "
        "rotacione IP (ou, pior, se o nginx não repassar X-Forwarded-For e "
        "--proxy-headers não estiver ligado no uvicorn, TODO MUNDO aparenta vir "
        "de 127.0.0.1 e o rate limit vira uma trava global de 10 logins/min pra "
        "empresa inteira) tenta o mesmo RE indefinidamente. Ver Fase 6 do "
        "relatório para a confirmação que só o Alisson pode fazer no servidor."
    ),
)
def test_rate_limit_nao_protege_a_conta_entre_ips_diferentes(cliente_auth):
    """20 tentativas contra o MESMO RE, cada uma "vinda" de um IP diferente
    (via ASGITransport com client= por request) — nenhuma deveria passar de
    um número pequeno e travar a conta. Hoje, nenhuma trava nunca: rate
    limit é só por IP, então rotacionar IP dá tentativas infinitas contra
    um RE específico."""
    import httpx

    RE_ALVO = "10001"
    codigos = []
    for i in range(20):
        ip_forjado = f"203.0.113.{i}"  # TEST-NET-3 (RFC 5737) — não é IP real
        transporte = httpx.ASGITransport(app=app, client=(ip_forjado, 12345))
        with httpx.Client(transport=transporte, base_url="http://testserver") as c:
            r = c.post("/auth/login", json={"re": RE_ALVO, "senha": f"tentativa-{i}"})
            codigos.append(r.status_code)

    # Comportamento desejado: a conta trava bem antes da 20ª tentativa vinda
    # de IPs diferentes (ex.: um limite por RE, não só por IP).
    assert any(c == 429 for c in codigos), (
        "Nenhuma das 20 tentativas contra o mesmo RE, vindas de IPs diferentes, "
        f"foi bloqueada: {codigos}"
    )


def test_dict_de_tentativas_cresce_sem_limite_por_ip_novo():
    """A2: _LOGIN_TENTATIVAS é um defaultdict(list) de módulo, uma chave por
    IP que já tentou logar, nunca podado a não ser pela janela de 60s de
    CADA chave. Um atacante que bata em /auth/login com IP de origem
    diferente a cada request (trivial de forjar num X-Forwarded-For não
    validado, ou distribuído) faz o dict crescer para sempre — vetor de
    exaustão de memória do processo. Aqui só provamos o crescimento; não
    dá pra provar exaustão real sem derrubar o processo de teste."""
    auth_router_mod._LOGIN_TENTATIVAS.clear()

    class _RequestFake:
        def __init__(self, ip):
            self.client = type("C", (), {"host": ip})()

    tamanho_antes = len(auth_router_mod._LOGIN_TENTATIVAS)
    for i in range(500):
        auth_router_mod._checar_rate_limit(_RequestFake(f"198.51.100.{i % 255}.{i}"))
    tamanho_depois = len(auth_router_mod._LOGIN_TENTATIVAS)

    assert tamanho_depois - tamanho_antes == 500, (
        "Esperado: uma chave nova por IP forjado, sem teto — "
        f"antes={tamanho_antes} depois={tamanho_depois}"
    )


# ─── 1.3 — Ciclo de vida do token ──────────────────────────────────────────────


def test_decode_access_token_rejeita_alg_none_forjado():
    """Confirma que decode_access_token (security.py:47) fixa
    algorithms=[settings.jwt_algorithm] — um JWT forjado com header
    {"alg":"none"} (ataque clássico de confusão de algoritmo) tem que
    ser rejeitado, não aceito como token válido sem assinatura."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": str(uuid4()), "exp": int(time.time()) + 3600}).encode()
    ).rstrip(b"=")
    token_forjado = (header + b"." + payload + b".").decode()

    with pytest.raises(JWTError):
        decode_access_token(token_forjado)


def test_decode_access_token_rejeita_token_assinado_com_outra_chave():
    """Token tecnicamente bem formado (HS256, com assinatura) mas assinado
    com uma chave diferente da SECRET_KEY do servidor — tem que cair."""
    from jose import jwt as jose_jwt
    token_outra_chave = jose_jwt.encode(
        {"sub": str(uuid4()), "exp": int(time.time()) + 3600},
        "uma-chave-completamente-diferente-da-secret-key-do-servidor-aqui",
        algorithm="HS256",
    )
    with pytest.raises(JWTError):
        decode_access_token(token_outra_chave)


# ─── 1.1 / SEV — sem endpoint de troca de senha para o fluxo novo ─────────────


def test_nenhuma_rota_permite_trocar_senha_de_usuario_login():
    """SEV: UsuarioLogin.senha_hash é escrito uma única vez, em
    POST /funcionarios/{id}/login (funcionarios.py:461), como hash dos 4
    últimos dígitos do CPF. Não existe, em nenhum router, uma rota que
    volte a escrever nesse campo — nem self-service, nem por ADMIN.
    PATCH /funcionarios/{id}/login só aceita {"ativo": bool}
    (UsuarioLoginAtivoUpdate — ver schemas/cadastro.py). O único endpoint
    que aceita um campo "senha" é PATCH /usuarios/{id} (usuarios.py:80),
    que escreve em Usuario.senha_hash — a tabela LEGADA, que o login só
    consulta quando a pessoa não tem UsuarioLogin (auth.py:47-53).

    Este teste falha (te avisa) se algum dia alguém adicionar uma rota de
    troca de senha para o fluxo novo — o que é a correção esperada, não um
    bug a preservar.
    """
    from app.schemas.cadastro import UsuarioLoginAtivoUpdate

    assert set(UsuarioLoginAtivoUpdate.model_fields.keys()) == {"ativo"}, (
        "UsuarioLoginAtivoUpdate ganhou um campo novo — se for 'senha', "
        "o achado SEV de troca de senha impossível pode estar corrigido; "
        "atualize o relatório."
    )

    rotas_login = [
        r for r in app.routes
        if getattr(r, "path", "").endswith("/funcionarios/{funcionario_id}/login")
    ]
    metodos_com_corpo_de_senha = []
    for r in rotas_login:
        campos = getattr(getattr(r, "body_field", None), "type_", None)
        if campos is not None and "senha" in getattr(campos, "model_fields", {}):
            metodos_com_corpo_de_senha.append(r.methods)

    assert metodos_com_corpo_de_senha == [], (
        "Existe uma rota /funcionarios/{id}/login que aceita 'senha' no corpo "
        f"— achado pode estar corrigido: {metodos_com_corpo_de_senha}"
    )


# ─── 2.3 / 3.1 / 3.2 — Desligamento (SEV-05, corrigido) ────────────────────────


class _FuncionarioFake:
    def __init__(self, id_, re, status="ATIVO"):
        self.id = id_
        self.re = re
        self.status = status


class _UsuarioLoginFake:
    def __init__(self, ativo):
        self.ativo = ativo


class _UsuarioMirrorFake:
    """Linha espelho em `usuario` (sistema legado) — o que os 12 routers
    do Pátio (CurrentUser/AdminUser/OperadorOuAdmin/MecanicoOuSuperior)
    realmente consultam."""
    def __init__(self, id_, re, ativo):
        self.id = id_
        self.re = re
        self.ativo = ativo


class _DBDesligamento:
    """Simula: Funcionario existe, UsuarioLogin.ativo=False (acesso
    desativado pelo fluxo novo via PATCH /funcionarios/{id}/login), mas o
    espelho em `usuario` permanece ativo=True porque nada no código o
    desativa (_criar_ou_atualizar_espelho_usuario só liga ativo=True, nunca
    desliga — ver funcionarios.py:56-88)."""

    def __init__(self, func, login_novo, mirror_legado):
        self._func = func
        self._login_novo = login_novo
        self._mirror = mirror_legado

    def get(self, model, id_):
        from app.models import Usuario
        from app.models.cadastro import Funcionario
        if model is Funcionario and id_ == self._func.id:
            return self._func
        if model is Usuario and id_ == self._func.id:
            return None  # sub do JWT novo não bate com PK de Usuario
        return None

    def execute(self, stmt, *args, **kwargs):
        class _R:
            def __init__(self, v):
                self._v = v
            def scalar_one_or_none(self):
                return self._v
        # Heurística simples: os dois selects deste fluxo são por
        # UsuarioLogin (fluxo novo) e por Usuario (espelho legado). Como o
        # teste só faz uma chamada por vez a cada dependency, alternar por
        # tipo de retorno esperado é suficiente e explícito.
        return _R(self._resultado_atual)


def _fabrica_db_desligamento(func, login_novo, mirror_legado, alvo: str):
    """Cria uma DB fake que responde só à consulta relevante ao teste —
    evita ter que replicar o dialeto inteiro do SQLAlchemy só pra decidir
    qual SELECT é qual."""
    db = _DBDesligamento(func, login_novo, mirror_legado)
    db._resultado_atual = login_novo if alvo == "login_novo" else mirror_legado
    return db


def test_desativar_login_novo_bloqueia_get_current_funcionario():
    """Controle positivo: get_current_funcionario (deps.py:106) RECONSULTA
    UsuarioLogin.ativo a cada request — desativar pelo fluxo novo bloqueia
    corretamente quem passa por exige()/Funcionario (ocorrencias, permissoes,
    funcionarios)."""
    from app.core.deps import get_current_funcionario
    from app.core.security import create_access_token

    func = _FuncionarioFake(uuid4(), "10001")
    token = create_access_token(subject=func.id)
    db = _fabrica_db_desligamento(func, _UsuarioLoginFake(ativo=False), None, "login_novo")

    with pytest.raises(HTTPException) as exc:
        get_current_funcionario(token, db)
    assert exc.value.status_code == 403


class _DBAtualizarLogin:
    """Sessão fake para funcionarios.atualizar_login() — UsuarioLogin,
    Funcionario e o espelho em Usuario (mesmo RE) já existem, caso comum
    de quem ganhou acesso via criar_login() (que sempre monta os dois
    juntos). Duas chamadas de select em sequência fixa: primeiro
    UsuarioLogin, depois Usuario — contador simples resolve, mesmo padrão
    de FakeSessionAtribuirFuncao em test_seguranca_autorizacao.py."""

    def __init__(self, func, login, mirror):
        self._func = func
        self._login = login
        self._mirror = mirror
        self._chamadas = 0

    def get(self, model, id_):
        from app.models.cadastro import Funcionario as F
        if model is F and id_ == self._func.id:
            return self._func
        return None

    def execute(self, stmt, params=None, *args, **kwargs):
        class _R:
            def __init__(self, v):
                self._v = v
            def scalar_one_or_none(self):
                return self._v
        self._chamadas += 1
        return _R(self._login if self._chamadas == 1 else self._mirror)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_atualizar_login_desativa_tambem_o_espelho_legado():
    """SEV-05, corrigido: PATCH /funcionarios/{id}/login {"ativo": false}
    agora propaga pro espelho legado. Sem isso, get_current_user (usado
    pelos 12 routers legados do Pátio) continuava deixando entrar, porque
    só consulta Usuario.ativo, nunca UsuarioLogin."""
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import UsuarioLoginAtivoUpdate

    func = _FuncionarioFake(uuid4(), "10001")
    login = _UsuarioLoginFake(ativo=True)
    mirror = _UsuarioMirrorFake(uuid4(), func.re, ativo=True)
    db = _DBAtualizarLogin(func, login, mirror)

    resultado = func_router_mod.atualizar_login(
        funcionario_id=func.id,
        dados=UsuarioLoginAtivoUpdate(ativo=False),
        _=None,
        db=db,
    )

    assert resultado.ativo is False
    assert mirror.ativo is False, "espelho legado deveria ter sido desativado junto"


def test_atualizar_login_reativa_tambem_o_espelho_legado():
    """Controle positivo — reativar (ex.: funcionário voltou de férias e
    teve o acesso restaurado) também precisa propagar, não só desativar."""
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import UsuarioLoginAtivoUpdate

    func = _FuncionarioFake(uuid4(), "10001")
    login = _UsuarioLoginFake(ativo=False)
    mirror = _UsuarioMirrorFake(uuid4(), func.re, ativo=False)
    db = _DBAtualizarLogin(func, login, mirror)

    resultado = func_router_mod.atualizar_login(
        funcionario_id=func.id,
        dados=UsuarioLoginAtivoUpdate(ativo=True),
        _=None,
        db=db,
    )

    assert resultado.ativo is True
    assert mirror.ativo is True


def test_funcionario_desligado_e_barrado_por_get_current_funcionario():
    """SEV-05, corrigido: Funcionario.status == 'DESLIGADO' agora é
    consultado por get_current_funcionario e barra com 403 — mesmo que o
    login (UsuarioLogin.ativo) nunca tenha sido desativado à parte."""
    from app.core.deps import get_current_funcionario
    from app.core.security import create_access_token

    func = _FuncionarioFake(uuid4(), "10001", status="DESLIGADO")
    token = create_access_token(subject=func.id)
    db = _fabrica_db_desligamento(func, _UsuarioLoginFake(ativo=True), None, "login_novo")

    with pytest.raises(HTTPException) as exc:
        get_current_funcionario(token, db)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("status_atual", ["ATIVO", "AFASTADO", "FERIAS"])
def test_funcionario_afastado_ou_ferias_continua_entrando(status_atual):
    """⚠️ O erro caro deste item: recusar só DESLIGADO. AFASTADO e FÉRIAS
    são gente que volta — bloquear os dois trancaria funcionário ativo
    fora do sistema na segunda-feira, no meio do turno."""
    from app.core.deps import get_current_funcionario
    from app.core.security import create_access_token

    func = _FuncionarioFake(uuid4(), "10001", status=status_atual)
    token = create_access_token(subject=func.id)
    db = _fabrica_db_desligamento(func, _UsuarioLoginFake(ativo=True), None, "login_novo")

    resultado = get_current_funcionario(token, db)
    assert resultado is func
