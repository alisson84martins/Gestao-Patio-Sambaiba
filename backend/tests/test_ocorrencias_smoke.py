"""Testes de fumaça dos endpoints de escrita de /ocorrencias.

Regressão específica de produção (31/07/2026):
    TypeError: run_in_threadpool() got multiple values for argument 'func'

Os endpoints síncronos de escrita usavam `func` como nome do parâmetro
de dependência RBAC. FastAPI despacha endpoint síncrono via
run_in_threadpool(dependant.call, **values) — como `values` trazia a
chave "func", colidia com o primeiro parâmetro posicional de
run_in_threadpool(func, *args, **kwargs) e estourava ANTES do corpo do
endpoint rodar. O erro só aparece numa requisição de verdade passando
pelo despacho do Starlette — por isso estes testes usam TestClient de
ponta a ponta (nunca chamam a função do router diretamente em Python,
o que mascararia o bug).

Cobre os 4 endpoints síncronos de escrita (criar, atualizar, finalizar,
deletar). upload_anexo é `async def` — não passa por run_in_threadpool,
então não podia ter esse bug especificamente; não tem teste de fumaça
aqui de propósito (testar multipart só por cobertura, sem relação com
a regressão, ficou fora do escopo desta sessão).
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
    """Extrai o callable de Depends(...) de um tipo Annotated[X, Depends(...)]."""
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


_USUARIO_FAKE = Funcionario(id=uuid4(), re="99999", nome="Teste Fumaça")


class _FakeResult:
    """Simula o objeto Result de db.execute(select(...)) — só o suficiente
    pra _carregar_completa() (usa .unique().scalar_one_or_none()) não travar.
    A regressão testada aqui é de despacho de requisição, não de query."""

    def __init__(self, valor):
        self._valor = valor

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._valor


class FakeSession:
    """Sessão mínima pra deixar o corpo do endpoint rodar até o fim.

    Não é um mock de comportamento de banco — não confere regra de
    negócio. O único objetivo é provar que a requisição atravessa o
    despacho do Starlette (run_in_threadpool) sem o TypeError de colisão
    de parâmetro.
    """

    def __init__(self, tipo: TipoOcorrencia, ocorrencia: "Ocorrencia | None" = None):
        self._tipo = tipo
        self._ocorrencia = ocorrencia

    def get(self, model, id_):
        if model is TipoOcorrencia and id_ == self._tipo.id:
            return self._tipo
        if model is Ocorrencia and self._ocorrencia is not None and id_ == self._ocorrencia.id:
            return self._ocorrencia
        return None

    def add(self, obj):
        if isinstance(obj, Ocorrencia):
            self._ocorrencia = obj

    def commit(self):
        pass

    def flush(self):
        pass

    def refresh(self, obj):
        if isinstance(obj, Ocorrencia):
            if obj.id is None:
                obj.id = uuid4()
            if obj.numero is None:
                obj.numero = 1
            if obj.criado_em is None:
                obj.criado_em = datetime.now(timezone.utc)

    def execute(self, *_args, **_kwargs):
        return _FakeResult(self._ocorrencia)

    def delete(self, obj):
        pass


@pytest.fixture
def cliente():
    """TestClient com auth/DB trocados por dublês — mas passando pelo
    despacho real do Starlette (é isso que expõe o bug)."""
    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)

    app.dependency_overrides[leitura_dep] = lambda: _USUARIO_FAKE
    app.dependency_overrides[escrita_dep] = lambda: _USUARIO_FAKE

    yield TestClient(app)

    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)


def _payload_minimo(tipo_id):
    return {
        "tipo_ocorrencia_id": str(tipo_id),
        "data_ocorrencia": "2026-07-31",
        "hora_ocorrencia": "14:30",
        "prefixo": "1234",
    }


def _novo_tipo_teste():
    """Mesma história do comentário acima, agora pro TipoOcorrencia: os
    defaults de coluna só se aplicam num flush de verdade."""
    return TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )


def _nova_ocorrencia_teste(tipo, registrado_por=None):
    """Ocorrência com todos os NOT NULL preenchidos — construída direto em
    Python, sem passar pelo flush do SQLAlchemy, então os defaults das
    colunas booleanas (default=False no model) não se aplicam sozinhos.

    `registrado_por` default = o próprio usuário fake usado pelos testes
    de fumaça (leitura e escrita), pra _exige_autoria() (item 2) deixar
    passar sem precisar simular a checagem de ADMIN aqui — isso é testado
    à parte em test_ocorrencias_autoria.py."""
    return Ocorrencia(
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
        registrado_por=registrado_por if registrado_por is not None else _USUARIO_FAKE.id,
        criado_em=datetime.now(timezone.utc),
    )


def test_post_ocorrencias_cria_com_sucesso(cliente):
    """POST /ocorrencias com payload mínimo válido → 201.

    Exatamente o caso do prompt — este é o teste que teria pegado a
    regressão antes de chegar em produção.
    """
    tipo = _novo_tipo_teste()
    app.dependency_overrides[get_db] = lambda: FakeSession(tipo)

    resp = cliente.post("/ocorrencias", json=_payload_minimo(tipo.id))

    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["prefixo"] == "1234"
    assert corpo["status"] == "RASCUNHO"


def test_patch_ocorrencia_atualiza_com_sucesso(cliente):
    """PATCH /ocorrencias/{id} com payload mínimo → 200."""
    tipo = _novo_tipo_teste()
    oc = _nova_ocorrencia_teste(tipo)
    # Relações setadas direto em Python — objeto nunca passa por uma
    # sessão de verdade, então não há lazy-load a fazer (nem deveria).
    oc.tipo_ocorrencia = tipo
    oc.analise = None
    oc.veiculos_terceiro = []
    oc.avarias = []
    oc.vitimas = []
    oc.testemunhas = []
    oc.autoridades = []
    oc.anexos = []

    app.dependency_overrides[get_db] = lambda: FakeSession(tipo, oc)

    resp = cliente.patch(f"/ocorrencias/{oc.id}", json={"bairro": "Santana"})

    assert resp.status_code == 200, resp.text


def test_post_finalizar_com_sucesso(cliente):
    """POST /ocorrencias/{id}/finalizar → 200."""
    tipo = _novo_tipo_teste()
    oc = _nova_ocorrencia_teste(tipo)
    app.dependency_overrides[get_db] = lambda: FakeSession(tipo, oc)

    resp = cliente.post(f"/ocorrencias/{oc.id}/finalizar")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "FINALIZADA"


def test_delete_ocorrencia_com_sucesso(cliente):
    """DELETE /ocorrencias/{id} (soft delete) → 200."""
    tipo = _novo_tipo_teste()
    oc = _nova_ocorrencia_teste(tipo)
    app.dependency_overrides[get_db] = lambda: FakeSession(tipo, oc)

    resp = cliente.delete(f"/ocorrencias/{oc.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["excluida_em"] is not None
