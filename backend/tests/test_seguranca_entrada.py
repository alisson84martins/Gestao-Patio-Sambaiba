"""Evidência das Fases 2.4, 4 e 5 da auditoria de segurança (2026-08-11):
IDOR em anexos/finalização de ocorrência, mass assignment e upload.

Ver _handoff-claude/RELATORIO-SEGURANCA-2026-08-10.md para severidade e
recomendação. Somente leitura: nada aqui corrige o código.
"""
import typing
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.cadastro import Funcionario
from app.models.ocorrencia import Ocorrencia, OcorrenciaAnexo, TipoOcorrencia
from app.routers import ocorrencias as router_mod


def _dependency_de(annotated_type):
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


_AUTOR = Funcionario(id=uuid4(), re="40001", nome="Coordenador Autor")
_OUTRO = Funcionario(id=uuid4(), re="40002", nome="Coordenador Outro")

# JPEG mínimo válido (SEV-13, fechamento 11/08/2026): upload_anexo() agora
# confere os primeiros bytes contra a assinatura real do formato, não só
# o Content-Type declarado — "fake-jpeg-bytes" (usado até 10/08/2026)
# deixou de passar. Só o cabeçalho importa pra validar_assinatura().
_JPEG_MINIMO = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 16


def _novo_tipo():
    return TipoOcorrencia(
        id=uuid4(), codigo="TESTE", nome="Teste",
        exige_vitima=False, exige_terceiro=False, exige_analise=False,
        ordem=1, ativo=True,
    )


def _nova_ocorrencia(tipo, registrado_por):
    oc = Ocorrencia(
        id=uuid4(), numero=1, tipo_ocorrencia_id=tipo.id, status="RASCUNHO",
        data_ocorrencia="2026-07-31", hora_ocorrencia="14:30", prefixo="1234",
        cidade="São Paulo", via_urbana=False, via_rodoviaria=False,
        area_interna=False, corredor=False, tem_fotos=False, monitoramento=False,
        ocorrencia_policial=False, houve_policia_tecnica=False,
        registrado_por=registrado_por, criado_em=datetime.now(timezone.utc),
    )
    oc.tipo_ocorrencia = tipo
    return oc


@pytest.fixture
def cliente():
    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)
    yield app, leitura_dep, escrita_dep, TestClient(app)
    app.dependency_overrides.pop(leitura_dep, None)
    app.dependency_overrides.pop(escrita_dep, None)
    app.dependency_overrides.pop(get_db, None)


def _autenticar_como(app_, leitura_dep, escrita_dep, usuario):
    app_.dependency_overrides[leitura_dep] = lambda: usuario
    app_.dependency_overrides[escrita_dep] = lambda: usuario


class _FakeResultAdmin:
    """Resultado de db.execute(text(...)) — só o que _eh_admin() usa
    (.first()). Mesmo padrão de test_ocorrencias_autoria.py."""

    def __init__(self, e_admin: bool):
        self._linha = (1,) if e_admin else None

    def first(self):
        return self._linha


# ─── 2.4 — IDOR: finalizar() não checa autoria (SEV-06, corrigido) ────────────


class _DBFinalizar:
    def __init__(self, oc, admins: frozenset = frozenset()):
        self._oc = oc
        self._admins = admins

    def get(self, model, id_):
        if model is Ocorrencia and id_ == self._oc.id:
            return self._oc
        return None

    def execute(self, stmt, params=None, *args, **kwargs):
        # _eh_admin() — só é chamada quando quem pede não é o autor.
        fid = (params or {}).get("fid")
        return _FakeResultAdmin(fid in self._admins)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_outro_coordenador_nao_finaliza_ocorrencia_alheia(cliente):
    """SEV-06, corrigido: finalizar() agora chama _exige_autoria() antes
    de mudar o status — mesma trava de atualizar()/deletar()."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    app_.dependency_overrides[get_db] = lambda: _DBFinalizar(oc)

    resp = http.post(f"/ocorrencias/{oc.id}/finalizar")

    assert resp.status_code == 403, resp.text


def test_admin_finaliza_ocorrencia_de_outro(cliente):
    """Controle positivo — ADMIN continua podendo finalizar qualquer
    ocorrência, igual já vale para editar/excluir."""
    app_, leitura_dep, escrita_dep, http = cliente
    admin = Funcionario(id=uuid4(), re="5598", nome="Admin Teste")
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, admin)
    app_.dependency_overrides[get_db] = lambda: _DBFinalizar(oc, admins=frozenset({admin.id}))

    resp = http.post(f"/ocorrencias/{oc.id}/finalizar")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "FINALIZADA"


def test_autor_finaliza_a_propria_ocorrencia(cliente):
    """Controle positivo — o caminho comum não pode ter sido travado
    junto com o buraco."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _AUTOR)
    app_.dependency_overrides[get_db] = lambda: _DBFinalizar(oc)

    resp = http.post(f"/ocorrencias/{oc.id}/finalizar")

    assert resp.status_code == 200, resp.text


# ─── 2.4 — IDOR: upload e remoção de anexo (SEV-06, corrigido) ────────────────


class _DBAnexoUpload:
    def __init__(self, oc, admins: frozenset = frozenset()):
        self._oc = oc
        self._admins = admins

    def get(self, model, id_):
        if model is Ocorrencia and id_ == self._oc.id:
            return self._oc
        return None

    def execute(self, stmt, params=None, *args, **kwargs):
        fid = (params or {}).get("fid")
        return _FakeResultAdmin(fid in self._admins)

    def add(self, obj):
        obj.id = uuid4()
        obj.ordem = 1
        obj.criado_em = datetime.now(timezone.utc)
        self._anexo = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_outro_coordenador_nao_anexa_arquivo_em_ocorrencia_alheia(
    cliente, tmp_path, monkeypatch
):
    """SEV-06, corrigido: upload_anexo() agora chama _exige_autoria()."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    app_.dependency_overrides[get_db] = lambda: _DBAnexoUpload(oc)
    # Isola o teste do disco real do projeto — não queremos litter em
    # backend/uploads/ (que já é gitignored, mas ainda assim).
    monkeypatch.setattr(router_mod, "UPLOAD_ROOT", tmp_path / "uploads" / "ocorrencias")

    resp = http.post(
        f"/ocorrencias/{oc.id}/anexos",
        files={"arquivo": ("foto.jpg", b"fake-jpeg-bytes", "image/jpeg")},
        data={"tipo": "FOTO_ACIDENTE"},
    )

    assert resp.status_code == 403, resp.text


def test_autor_anexa_arquivo_na_propria_ocorrencia(cliente, tmp_path, monkeypatch):
    """Controle positivo — upload continua funcionando pra quem tem
    direito."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _AUTOR)
    app_.dependency_overrides[get_db] = lambda: _DBAnexoUpload(oc)
    monkeypatch.setattr(router_mod, "UPLOAD_ROOT", tmp_path / "uploads" / "ocorrencias")

    resp = http.post(
        f"/ocorrencias/{oc.id}/anexos",
        files={"arquivo": ("foto.jpg", _JPEG_MINIMO, "image/jpeg")},
        data={"tipo": "FOTO_ACIDENTE"},
    )

    assert resp.status_code == 201, resp.text


class _DBAnexoDelete:
    def __init__(self, oc, anexo, admins: frozenset = frozenset()):
        self._oc = oc
        self._anexo = anexo
        self._admins = admins
        self.deletado = False

    def get(self, model, id_):
        if model is Ocorrencia and id_ == self._oc.id:
            return self._oc
        return None

    def execute(self, stmt, params=None, *args, **kwargs):
        if hasattr(stmt, "text"):  # _eh_admin()
            fid = (params or {}).get("fid")
            return _FakeResultAdmin(fid in self._admins)

        class _R:
            def __init__(self, v):
                self._v = v
            def scalar_one_or_none(self):
                return self._v
        return _R(self._anexo)

    def delete(self, obj):
        self.deletado = True

    def commit(self):
        pass


def test_outro_coordenador_nao_apaga_anexo_de_ocorrencia_alheia(cliente, tmp_path):
    """SEV-06, corrigido: deletar_anexo() agora carrega a OCORRÊNCIA (não
    o anexo) e chama _exige_autoria() antes de buscar/apagar o anexo."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    anexo = OcorrenciaAnexo(
        id=uuid4(), ocorrencia_id=oc.id, tipo="FOTO_ACIDENTE",
        caminho=f"ocorrencias/{oc.id}/inexistente.jpg", nome_original="foto.jpg",
        mime_type="image/jpeg", tamanho_bytes=10, ordem=1,
        criado_em=datetime.now(timezone.utc),
    )
    _autenticar_como(app_, leitura_dep, escrita_dep, _OUTRO)
    fake_db = _DBAnexoDelete(oc, anexo)
    app_.dependency_overrides[get_db] = lambda: fake_db

    resp = http.delete(f"/ocorrencias/{oc.id}/anexos/{anexo.id}")

    assert resp.status_code == 403, resp.text
    assert fake_db.deletado is False


def test_autor_apaga_o_proprio_anexo(cliente, tmp_path):
    """Controle positivo — o autor continua conseguindo apagar o próprio
    anexo (arquivo não existe em disco neste teste; unlink usa
    missing_ok=True, não é isso que está sendo verificado aqui)."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    anexo = OcorrenciaAnexo(
        id=uuid4(), ocorrencia_id=oc.id, tipo="FOTO_ACIDENTE",
        caminho=f"ocorrencias/{oc.id}/inexistente.jpg", nome_original="foto.jpg",
        mime_type="image/jpeg", tamanho_bytes=10, ordem=1,
        criado_em=datetime.now(timezone.utc),
    )
    _autenticar_como(app_, leitura_dep, escrita_dep, _AUTOR)
    app_.dependency_overrides[get_db] = lambda: _DBAnexoDelete(oc, anexo)

    resp = http.delete(f"/ocorrencias/{oc.id}/anexos/{anexo.id}")

    assert resp.status_code == 204, resp.text


# ─── 4.3 — mass assignment: FuncionarioUpdate ignora campos não declarados ────


def test_funcionario_update_ignora_campos_nao_declarados_no_schema():
    """Confirma que atualizar_funcionario() (funcionarios.py:346:
    setattr(func, campo, valor) para campo, valor in
    dados.model_dump(exclude_unset=True).items()) só pode setar o que o
    Pydantic aceitou — 'id', 'criado_por' e 'status_invalido' mandados no
    corpo não aparecem no dump porque FuncionarioUpdate não os declara
    (ou, no caso de 'status', valida contra um pattern fechado)."""
    from pydantic import ValidationError

    from app.schemas.cadastro import FuncionarioUpdate

    payload = {
        "nome": "Nome Legítimo",
        "id": "00000000-0000-0000-0000-000000000000",
        "criado_por": "00000000-0000-0000-0000-000000000000",
        "criado_em": "2000-01-01T00:00:00Z",
    }
    dados = FuncionarioUpdate(**payload)
    dump = dados.model_dump(exclude_unset=True)

    assert "id" not in dump
    assert "criado_por" not in dump
    assert "criado_em" not in dump
    assert dump == {"nome": "Nome Legítimo"}

    # status é STRING mas com pattern fechado — não dá pra injetar um valor
    # fora da lista (ex.: tentar algo tipo SQL/serialização) por esse campo.
    with pytest.raises(ValidationError):
        FuncionarioUpdate(status="SUPER_ADMIN_BACKDOOR")


# ─── 5 — upload: tamanho, assinatura e caminho (SEV-12/13/14, corrigidos) ─────


def test_upload_anexo_acima_do_limite_retorna_413(cliente, tmp_path, monkeypatch):
    """SEV-12, corrigido: até 10/08/2026 `conteudo = await arquivo.read()`
    lia o corpo inteiro ANTES de comparar com ANEXO_TAMANHO_MAXIMO — um
    upload multi-GB colocava o arquivo INTEIRO na memória antes de
    rejeitar. Agora `ler_upload_limitado()` (app/core/uploads.py) lê em
    blocos de 1 MiB e aborta assim que a soma passa do limite. Este teste
    prova que o 413 continua funcionando fim a fim; a prova de que a
    leitura é *limitada* (não lê o resto da fonte depois de estourar) está
    isolada em test_seguranca_upload.py, onde dá pra contar bytes
    efetivamente lidos sem alocar memória de verdade no processo de
    teste."""
    app_, leitura_dep, escrita_dep, http = cliente
    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    _autenticar_como(app_, leitura_dep, escrita_dep, _AUTOR)
    app_.dependency_overrides[get_db] = lambda: _DBAnexoUpload(oc)
    monkeypatch.setattr(router_mod, "UPLOAD_ROOT", tmp_path / "uploads" / "ocorrencias")

    conteudo_grande = b"x" * (11 * 1024 * 1024)  # 11 MB > limite de 10 MB
    resp = http.post(
        f"/ocorrencias/{oc.id}/anexos",
        files={"arquivo": ("grande.jpg", conteudo_grande, "image/jpeg")},
        data={"tipo": "FOTO_ACIDENTE"},
    )

    assert resp.status_code == 413, resp.text
