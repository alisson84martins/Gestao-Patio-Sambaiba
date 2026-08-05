"""Testes da trava de autoria em PATCH/DELETE /ocorrencias/{id}.

Regra de Alisson (01/08/2026): cada coordenador só mexe nas ocorrências
que ele mesmo registrou. Só o ADMIN pode editar ou excluir a de outra
pessoa. Ocorrência antiga com registrado_por nulo é tratada como não
editável por ninguém além do ADMIN — falhar fechado.

Usa TestClient de ponta a ponta (mesmo padrão de
test_ocorrencias_smoke.py) — é o despacho real do Starlette que executa
o corpo do endpoint, incluindo _exige_autoria().
"""
import typing
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.ocorrencia import Ocorrencia, TipoOcorrencia
from app.routers import ocorrencias as router_mod


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


_AUTOR = Funcionario(id=uuid4(), re="10001", nome="Coordenador Autor")
_OUTRO = Funcionario(id=uuid4(), re="10002", nome="Coordenador Outro")
_ADMIN = Funcionario(id=uuid4(), re="10003", nome="Admin Teste")


class _FakeResultOcorrencia:
    """Resultado de db.execute(select(Ocorrencia)...) — só o que
    _carregar_completa() usa (.unique().scalar_one_or_none())."""

    def __init__(self, valor):
        self._valor = valor

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._valor


class _FakeResultAdmin:
    """Resultado de db.execute(text(...)) — só o que _eh_admin() usa
    (.first())."""

    def __init__(self, e_admin: bool):
        self._linha = (1,) if e_admin else None

    def first(self):
        return self._linha


class FakeSession:
    """Sessão mínima: não confere regra de negócio de banco nenhuma —
    só deixa o corpo do endpoint (incluindo _exige_autoria) rodar até o
    fim com dados fictícios, pra provar a trava de autoria pela API real.
    """

    def __init__(self, ocorrencia: Ocorrencia, admins: set):
        self._ocorrencia = ocorrencia
        self._admins = admins

    def get(self, model, id_):
        if model is Ocorrencia and id_ == self._ocorrencia.id:
            return self._ocorrencia
        if model is TipoOcorrencia:
            return self._ocorrencia.tipo_ocorrencia
        return None

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def execute(self, stmt, params=None, *args, **kwargs):
        # _eh_admin() usa text(...) com parâmetro nomeado "fid".
        if hasattr(stmt, "text"):
            fid = (params or {}).get("fid")
            return _FakeResultAdmin(fid in self._admins)
        return _FakeResultOcorrencia(self._ocorrencia)


@pytest.fixture
def cliente():
    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    yield app, leitura_dep, escrita_dep, TestClient(app)
    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)


def _novo_tipo():
    return TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )


def _nova_ocorrencia(tipo, registrado_por):
    oc = Ocorrencia(
        id=uuid4(),
        numero=1,
        tipo_ocorrencia_id=tipo.id,
        status="RASCUNHO",
        data_ocorrencia="2026-07-31",
        hora_ocorrencia="14:30",
        prefixo="1234",
        cidade="São Paulo",
        via_urbana=False,
        via_rodoviaria=False,
        area_interna=False,
        corredor=False,
        tem_fotos=False,
        monitoramento=False,
        ocorrencia_policial=False,
        houve_policia_tecnica=False,
        registrado_por=registrado_por,
        criado_em=datetime.now(timezone.utc),
    )
    oc.tipo_ocorrencia = tipo
    oc.analise = None
    oc.veiculos_terceiro = []
    oc.avarias = []
    oc.vitimas = []
    oc.testemunhas = []
    oc.autoridades = []
    oc.anexos = []
    return oc


def _autenticar_como(app_, leitura_dep, escrita_dep, usuario):
    app_.dependency_overrides[leitura_dep] = lambda: usuario
    app_.dependency_overrides[escrita_dep] = lambda: usuario


def test_autor_exclui_a_propria_ocorrencia(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _AUTOR)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins=set())

    resp = http.delete(f"/ocorrencias/{oc.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["excluida_em"] is not None


def test_outro_coordenador_nao_exclui(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins=set())

    resp = http.delete(f"/ocorrencias/{oc.id}")

    assert resp.status_code == 403, resp.text
    # Backend padroniza erro em {"erro": "...", "status_code": N}, não no
    # "detail" cru do FastAPI (ver core/exception_handlers.py).
    assert "outro coordenador" in resp.json()["erro"].lower()


def test_outro_coordenador_nao_edita(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins=set())

    resp = http.patch(f"/ocorrencias/{oc.id}", json={"bairro": "Santana"})

    assert resp.status_code == 403, resp.text
    # Backend padroniza erro em {"erro": "...", "status_code": N}, não no
    # "detail" cru do FastAPI (ver core/exception_handlers.py).
    assert "outro coordenador" in resp.json()["erro"].lower()


def test_admin_exclui_ocorrencia_de_outro(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _ADMIN)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins={_ADMIN.id})

    resp = http.delete(f"/ocorrencias/{oc.id}")

    assert resp.status_code == 200, resp.text


def test_registrado_por_nulo_coordenador_comum_nao_edita(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=None)
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins=set())

    resp = http.patch(f"/ocorrencias/{oc.id}", json={"bairro": "Santana"})

    assert resp.status_code == 403, resp.text


def test_registrado_por_nulo_admin_edita(cliente):
    app_, leitura_dep, escrita_dep, http = cliente
    tipo = _novo_tipo()
    oc = _nova_ocorrencia(tipo, registrado_por=None)
    _autenticar_como(app_, leitura_dep, escrita_dep, _ADMIN)
    app_.dependency_overrides[get_db] = lambda: FakeSession(oc, admins={_ADMIN.id})

    resp = http.patch(f"/ocorrencias/{oc.id}", json={"bairro": "Santana"})

    assert resp.status_code == 200, resp.text
