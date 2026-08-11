"""Evidência da Fase 2 da auditoria de segurança (2026-08-11) — RBAC.

Ver _handoff-claude/RELATORIO-SEGURANCA-2026-08-10.md para severidade e
recomendação de cada achado. Somente leitura: nada aqui corrige o código.
"""
import typing
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcao, Funcionario, FuncionarioFuncao
from app.routers import funcionarios as func_router_mod


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


# ─── 2.1 — Escalada de privilégio: COORDENADOR_TRAFEGO → ADMIN (SEV-01) ───────
#
# CORRIGIDO em 11/08/2026, migration 020 + funcionarios.py::atribuir_funcao().
#
# Duas camadas, testadas separadamente:
#   1. RBAC: COORDENADOR_TRAFEGO não tem mais pode_escrever=TRUE em
#      "usuarios" (migration 020 + seed 08 atualizados) — não reproduzível
#      aqui sem Postgres com vw_acesso_efetivo, fica confirmado por leitura
#      da migration/seed, não por teste.
#   2. Defesa em profundidade DENTRO do endpoint: mesmo que alguém que não
#      seja ADMIN volte a ter "usuarios:escrever" um dia (por engano de
#      seed, override individual, etc.), atribuir_funcao() agora recusa
#      conceder especificamente a função ADMIN a quem não é ADMIN. É essa
#      camada que os testes abaixo provam de ponta a ponta via TestClient,
#      com override só na dependency de RBAC (mesmo padrão de
#      test_ocorrencias_autoria.py) — simulando "alguém que passou pelo
#      gate de recurso", pra isolar exatamente a garantia durável do item
#      1.4 do prompt de execução.


class _FakeResultLista:
    def __init__(self, valor):
        self._valor = valor

    def scalar_one_or_none(self):
        return self._valor

    def scalar_one(self):
        return self._valor

    def unique(self):
        return self

    def first(self):
        return self._valor


class FakeSessionAtribuirFuncao:
    """Sessão mínima para POST /funcionarios/{id}/funcoes — só o que
    atribuir_funcao() usa. `chamador_e_admin` controla a resposta da query
    de _eh_admin() (text() sobre funcionario_funcao/funcao) — é a checagem
    nova que decide se quem chama pode conceder a função ADMIN."""

    def __init__(self, alvo: Funcionario, funcao_alvo: Funcao, chamador_e_admin: bool):
        self._alvo = alvo
        self._funcao_alvo = funcao_alvo
        self._chamador_e_admin = chamador_e_admin
        self._vinculo_criado = None
        self._chamadas_select = 0

    def get(self, model, id_):
        if model is Funcionario and id_ == self._alvo.id:
            return self._alvo
        if model is Funcao and id_ == self._funcao_alvo.id:
            return self._funcao_alvo
        return None

    def add(self, obj):
        # Simula os defaults que o Postgres aplicaria no INSERT (id, ativo)
        # — objeto Python puro não tem isso até um flush contra engine real.
        obj.id = uuid4()
        if getattr(obj, "ativo", None) is None:
            obj.ativo = True
        obj.funcao = self._funcao_alvo
        self._vinculo_criado = obj

    def flush(self):
        pass

    def commit(self):
        pass

    def execute(self, stmt, params=None, *args, **kwargs):
        if hasattr(stmt, "text"):
            texto = stmt.text
            if "funcionario_funcao" in texto:  # _eh_admin()
                return _FakeResultLista((1,) if self._chamador_e_admin else None)
            return _FakeResultLista(None)  # fn_ajustar_funcao_principal — resultado ignorado
        self._chamadas_select += 1
        if self._chamadas_select == 1:
            return _FakeResultLista(None)  # nenhum vínculo ativo existente
        return _FakeResultLista(self._vinculo_criado)  # busca final c/ joinedload


@pytest.fixture
def cliente_funcionarios():
    gerencia_dep = _dependency_de(func_router_mod.GerenciaUsuarios)
    yield app, gerencia_dep, TestClient(app)
    app.dependency_overrides.pop(gerencia_dep, None)
    app.dependency_overrides.pop(get_db, None)


def test_coordenador_trafego_nao_consegue_se_autopromover_a_admin(cliente_funcionarios):
    """SEV-01, corrigido: mesmo simulando alguém que passou pelo gate de
    recurso (dependency override — o cenário que a migration 020 já torna
    impossível para COORDENADOR_TRAFEGO, mas testado aqui isolado, como
    defesa em profundidade), atribuir_funcao() recusa conceder ADMIN a
    quem não é ADMIN."""
    app_, gerencia_dep, http = cliente_funcionarios
    coordenador = Funcionario(id=uuid4(), re="20001", nome="Coordenador de Tráfego")
    funcao_admin = Funcao(
        id=uuid4(), codigo="ADMIN", nome="Administrador", categoria="ADMINISTRATIVO",
        nivel=1, ativo=True,
    )
    app_.dependency_overrides[gerencia_dep] = lambda: coordenador
    app_.dependency_overrides[get_db] = lambda: FakeSessionAtribuirFuncao(
        coordenador, funcao_admin, chamador_e_admin=False
    )

    resp = http.post(
        f"/funcionarios/{coordenador.id}/funcoes",
        json={"funcao_id": str(funcao_admin.id), "principal": True},
    )

    assert resp.status_code == 403, resp.text
    assert "ADMIN" in resp.json()["erro"]


def test_admin_atribuindo_admin_continua_funcionando(cliente_funcionarios):
    """Controle positivo — senão a correção do SEV-01 trancaria a porta com
    a chave dentro: ADMIN precisa continuar conseguindo promover alguém a
    ADMIN (ex.: suceder o próprio Alisson um dia)."""
    app_, gerencia_dep, http = cliente_funcionarios
    admin = Funcionario(id=uuid4(), re="5598", nome="Alisson Martins")
    funcao_admin = Funcao(
        id=uuid4(), codigo="ADMIN", nome="Administrador", categoria="ADMINISTRATIVO",
        nivel=1, ativo=True,
    )
    app_.dependency_overrides[gerencia_dep] = lambda: admin
    app_.dependency_overrides[get_db] = lambda: FakeSessionAtribuirFuncao(
        admin, funcao_admin, chamador_e_admin=True
    )

    resp = http.post(
        f"/funcionarios/{admin.id}/funcoes",
        json={"funcao_id": str(funcao_admin.id), "principal": True},
    )

    assert resp.status_code == 201, resp.text


def test_admin_atribuindo_funcao_nao_admin_continua_funcionando(cliente_funcionarios):
    """Controle positivo complementar: a trava é só para o código ADMIN —
    atribuir qualquer outra função continua liberado sem checar _eh_admin."""
    app_, gerencia_dep, http = cliente_funcionarios
    admin = Funcionario(id=uuid4(), re="5598", nome="Alisson Martins")
    funcao_motorista = Funcao(
        id=uuid4(), codigo="MOTORISTA", nome="Motorista", categoria="OPERACIONAL",
        nivel=5, ativo=True,
    )
    app_.dependency_overrides[gerencia_dep] = lambda: admin
    app_.dependency_overrides[get_db] = lambda: FakeSessionAtribuirFuncao(
        admin, funcao_motorista, chamador_e_admin=False
    )

    resp = http.post(
        f"/funcionarios/{admin.id}/funcoes",
        json={"funcao_id": str(funcao_motorista.id), "principal": True},
    )

    assert resp.status_code == 201, resp.text


# ─── 2.1 — exige() nega corretamente quem não tem o recurso ───────────────────
# Controle positivo + negativo direto na dependency, sem passar pelo router
# inteiro — exige() (deps.py:129) é genérica pra qualquer recurso, então
# testar uma vez cobre "usuarios", "ocorrencia" etc. igualmente.


def test_exige_nega_403_sem_linha_de_permissao():
    """Funcionário existe (get_current_funcionario resolve OK) mas
    vw_acesso_efetivo não devolve linha nenhuma para o recurso pedido —
    tem que ser 403, não 200 nem 500."""
    from app.core.deps import exige
    from app.core.security import create_access_token
    from app.models.cadastro import Funcionario as F, UsuarioLogin

    func = F(id=uuid4(), re="30001", nome="Motorista")
    token = create_access_token(subject=func.id)

    class _DB:
        def get(self, model, id_):
            if model is F and id_ == func.id:
                return func
            return None

        def execute(self, stmt, params=None, *args, **kwargs):
            class _R:
                def scalar_one_or_none(self):
                    return None  # sem UsuarioLogin — não bloqueia get_current_funcionario

                def fetchone(self):
                    return None  # vw_acesso_efetivo: sem linha pro recurso
            return _R()

    # request=None: exige() agora tenta logar a negativa em log_acesso
    # (migration 021) — registrar_log_acesso() nunca deixa escapar erro
    # de sessão fake sem .add()/.commit(), então None passa por aqui sem
    # quebrar o teste, só sem IP no log (irrelevante pro que este teste prova).
    checker = exige("usuarios", escrever=True)
    with pytest.raises(HTTPException) as exc:
        checker(None, token, _DB())
    assert exc.value.status_code == 403


def test_exige_permite_quando_view_confirma_acesso():
    """Controle positivo do mesmo mecanismo: linha com pode_escrever=True
    → passa e devolve o Funcionario."""
    from app.core.deps import exige
    from app.core.security import create_access_token
    from app.models.cadastro import Funcionario as F

    func = F(id=uuid4(), re="30002", nome="Admin Teste")
    token = create_access_token(subject=func.id)

    class _LinhaPermissao:
        pode_ler = True
        pode_escrever = True

    class _DB:
        def get(self, model, id_):
            if model is F and id_ == func.id:
                return func
            return None

        def execute(self, stmt, params=None, *args, **kwargs):
            class _R:
                def scalar_one_or_none(self):
                    return None

                def fetchone(self):
                    return _LinhaPermissao()
            return _R()

    checker = exige("usuarios", escrever=True)
    resultado = checker(None, token, _DB())
    assert resultado is func


# ─── 2.4 — IDOR: anexo e finalização não checam autoria ───────────────────────
# Ver test_seguranca_entrada.py — os testes de anexo/finalizar ficaram lá
# junto dos outros testes de escrita da Fase 4, pra não duplicar o setup de
# Ocorrencia usado nos dois.
