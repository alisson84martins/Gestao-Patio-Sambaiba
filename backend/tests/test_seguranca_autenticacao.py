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
    """_LOGIN_TENTATIVAS e _TENTATIVAS_POR_CONTA são dicts de módulo —
    sem limpar, um teste vaza pro outro (mesmo "unknown" de TestClient em
    request.client.host, e REs repetidos como "10001" entre testes)."""
    auth_router_mod._LOGIN_TENTATIVAS.clear()
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()
    yield
    auth_router_mod._LOGIN_TENTATIVAS.clear()
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()


@pytest.fixture
def cliente_auth():
    from app.core.database import get_db
    app.dependency_overrides[get_db] = lambda: _FakeDBSemUsuario()
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_rate_limit_bloqueia_na_11a_tentativa_do_mesmo_ip(cliente_auth):
    """Confirma A2: 10/min por IP. As 10 primeiras devem cair em 401
    (RE/senha incorretos); a 11ª tem que ser 429, não mais uma tentativa
    de autenticação de verdade.

    RE diferente a cada tentativa de propósito — isola o limite por IP do
    limite por conta (SEV-08, 5 falhas/RE): usar o mesmo RE dez vezes
    bateria no limite de conta primeiro, e o teste pararia de medir o que
    diz medir."""
    respostas = [
        cliente_auth.post("/auth/login", json={"re": f"1000{i}", "senha": "errada"})
        for i in range(10)
    ]
    assert all(r.status_code == 401 for r in respostas), [r.status_code for r in respostas]

    bloqueada = cliente_auth.post("/auth/login", json={"re": "10099", "senha": "errada"})
    assert bloqueada.status_code == 429


def test_bloqueio_por_conta_protege_independente_do_ip(cliente_auth):
    """SEV-08, corrigido: 20 tentativas contra o MESMO RE, cada uma
    "vinda" de um IP diferente (TestClient(client=(ip, porta)) injeta
    request.client.host direto no escopo ASGI, sem depender de cabeçalho)
    — trava na 6ª tentativa (limite de 5 falhas), não importa quantos IPs
    diferentes o atacante rotacionar."""
    RE_ALVO = "10001"
    codigos = []
    for i in range(20):
        ip_forjado = f"203.0.113.{i}"  # TEST-NET-3 (RFC 5737) — não é IP real
        c = TestClient(app, client=(ip_forjado, 12345))
        r = c.post("/auth/login", json={"re": RE_ALVO, "senha": f"tentativa-{i}"})
        codigos.append(r.status_code)

    assert codigos[:5] == [401] * 5, codigos
    assert all(c == 429 for c in codigos[5:]), codigos


def test_falhas_por_conta_zeram_no_sucesso():
    """Conta só falha; sucesso zera o contador daquele RE — sem isso, um
    usuário legítimo que erra a senha algumas vezes e depois acerta
    ficaria a um erro de distância de travar sem motivo."""
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()
    re = "10098"
    for _ in range(4):
        auth_router_mod._registrar_falha_conta(re)
    auth_router_mod._checar_bloqueio_conta(re)  # 4 < 5 — ainda não trava

    auth_router_mod._zerar_falhas_conta(re)
    assert re not in auth_router_mod._TENTATIVAS_POR_CONTA

    for _ in range(4):
        auth_router_mod._registrar_falha_conta(re)
    auth_router_mod._checar_bloqueio_conta(re)  # zerado — 4 de novo, não trava


def test_bloqueio_por_conta_poda_entrada_quando_a_janela_expira(monkeypatch):
    """Diferente de _LOGIN_TENTATIVAS (que nunca poda chave vazia — ver
    teste seguinte), _checar_bloqueio_conta remove a entrada do RE quando
    todas as tentativas já saíram da janela de 15 min, em vez de deixar
    uma lista vazia ocupando a chave pra sempre."""
    auth_router_mod._TENTATIVAS_POR_CONTA.clear()
    re = "10097"

    agora = [1_000_000.0]
    monkeypatch.setattr(auth_router_mod.time, "time", lambda: agora[0])

    auth_router_mod._registrar_falha_conta(re)
    assert re in auth_router_mod._TENTATIVAS_POR_CONTA

    agora[0] += auth_router_mod._CONTA_JANELA + 1
    auth_router_mod._checar_bloqueio_conta(re)
    assert re not in auth_router_mod._TENTATIVAS_POR_CONTA


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


# ─── 1.1 / SEV-03 — troca de senha do fluxo novo (corrigido) ──────────────────
#
# ⚠️ Nota de execução: este teste NÃO estava marcado @pytest.mark.xfail no
# relatório original (era uma confirmação de AUSÊNCIA, não uma marca de
# "buraco esperando correção") — diferente do que o prompt de execução de
# 11/08 presumiu ao listar os "6 xfail". Reescrito de qualquer forma: a
# versão antiga só checava rotas terminadas exatamente em
# ".../funcionarios/{funcionario_id}/login" — a rota nova
# (".../login/senha") tem outro path e passaria batido pela checagem
# antiga, criando falso-positivo de "ainda não existe". Ver
# EXECUCAO-2026-08-11.md.


class _UsuarioLoginComSenha:
    def __init__(self, senha_hash, politica_senha="CPF"):
        self.senha_hash = senha_hash
        self.politica_senha = politica_senha
        self.ativo = True
        self.atualizado_em = None


class _DBTrocarSenha:
    """execute() atende dois formatos: select(UsuarioLogin) e o text()
    de _eh_admin() — distingue por hasattr(stmt, "text"), mesmo padrão já
    usado nos outros arquivos desta suíte."""

    def __init__(self, login, admins: frozenset = frozenset()):
        self._login = login
        self._admins = admins

    def execute(self, stmt, params=None, *args, **kwargs):
        if hasattr(stmt, "text"):
            fid = (params or {}).get("fid")
            return _FakeResultAdmin(fid in self._admins)

        class _R:
            def __init__(self, v):
                self._v = v
            def scalar_one_or_none(self):
                return self._v
        return _R(self._login)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


class _FakeResultAdmin:
    def __init__(self, e_admin: bool):
        self._linha = (1,) if e_admin else None

    def first(self):
        return self._linha


def test_endpoint_de_troca_de_senha_existe_no_path_esperado():
    rota = [
        r for r in app.routes
        if getattr(r, "path", "") == "/funcionarios/{funcionario_id}/login/senha"
    ]
    assert len(rota) == 1
    assert "PATCH" in rota[0].methods


def test_trocar_senha_autoatendimento_com_senha_atual_correta_funciona():
    from app.core.security import hash_password, verify_password
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import TrocaSenhaRequest

    dono = _FuncionarioFake(uuid4(), "10001")
    login = _UsuarioLoginComSenha(hash_password("1234"))
    db = _DBTrocarSenha(login)

    resultado = func_router_mod.trocar_senha(
        funcionario_id=dono.id,
        dados=TrocaSenhaRequest(senha_atual="1234", senha_nova="minha-senha-nova"),
        usuario=dono,
        db=db,
    )

    assert resultado.politica_senha == "PROPRIA"
    assert verify_password("minha-senha-nova", login.senha_hash)


def test_trocar_senha_autoatendimento_sem_informar_senha_atual_falha():
    from app.core.security import hash_password
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import TrocaSenhaRequest

    dono = _FuncionarioFake(uuid4(), "10001")
    login = _UsuarioLoginComSenha(hash_password("1234"))
    db = _DBTrocarSenha(login)

    with pytest.raises(HTTPException) as exc:
        func_router_mod.trocar_senha(
            funcionario_id=dono.id,
            dados=TrocaSenhaRequest(senha_nova="minha-senha-nova"),
            usuario=dono,
            db=db,
        )
    assert exc.value.status_code == 403


def test_trocar_senha_autoatendimento_com_senha_atual_errada_falha():
    from app.core.security import hash_password
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import TrocaSenhaRequest

    dono = _FuncionarioFake(uuid4(), "10001")
    login = _UsuarioLoginComSenha(hash_password("1234"))
    db = _DBTrocarSenha(login)

    with pytest.raises(HTTPException) as exc:
        func_router_mod.trocar_senha(
            funcionario_id=dono.id,
            dados=TrocaSenhaRequest(senha_atual="senha-errada", senha_nova="minha-senha-nova"),
            usuario=dono,
            db=db,
        )
    assert exc.value.status_code == 403


def test_trocar_senha_terceiro_nao_admin_nao_mexe_na_de_outro():
    """⛔ Ninguém além do próprio dono e do ADMIN — nem coordenador, nem
    encarregado, nem gerência."""
    from app.core.security import hash_password
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import TrocaSenhaRequest

    dono = _FuncionarioFake(uuid4(), "10001")
    terceiro = _FuncionarioFake(uuid4(), "10002")
    login = _UsuarioLoginComSenha(hash_password("1234"))
    db = _DBTrocarSenha(login, admins=frozenset())  # terceiro não é admin

    with pytest.raises(HTTPException) as exc:
        func_router_mod.trocar_senha(
            funcionario_id=dono.id,
            dados=TrocaSenhaRequest(senha_atual="1234", senha_nova="minha-senha-nova"),
            usuario=terceiro,
            db=db,
        )
    assert exc.value.status_code == 403


def test_admin_reseta_senha_de_outro_sem_precisar_da_atual():
    from app.core.security import hash_password
    from app.routers import funcionarios as func_router_mod
    from app.schemas.cadastro import TrocaSenhaRequest

    dono = _FuncionarioFake(uuid4(), "10001")
    admin = _FuncionarioFake(uuid4(), "5598")
    login = _UsuarioLoginComSenha(hash_password("1234"))
    db = _DBTrocarSenha(login, admins=frozenset({admin.id}))

    resultado = func_router_mod.trocar_senha(
        funcionario_id=dono.id,
        dados=TrocaSenhaRequest(senha_nova="senha-resetada-pelo-admin"),
        usuario=admin,
        db=db,
    )

    assert resultado.politica_senha == "PROPRIA"


def test_trocar_senha_recusa_menos_de_6_caracteres():
    """Mínimo 6 caracteres, sem exigir complexidade barroca — quem usa
    digita no celular, em pé, na garagem."""
    from pydantic import ValidationError

    from app.schemas.cadastro import TrocaSenhaRequest

    with pytest.raises(ValidationError):
        TrocaSenhaRequest(senha_nova="12345")

    TrocaSenhaRequest(senha_nova="123456")  # 6 é o mínimo aceito, não levanta


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
