"""SEV-12, SEV-13, SEV-14 (fechamento da auditoria, 11/08/2026).

Três achados do mesmo módulo novo (app/core/uploads.py):
- SEV-12: tamanho só era checado depois de ler o arquivo inteiro.
- SEV-13: tipo validado só pelo Content-Type que o cliente manda.
- SEV-14: caminho de disco relativo, sem checagem contra a raiz de upload.

Padrão do projeto: testar o comportamento ERRADO, não só o certo.

⚠️ Sem pytest-asyncio no projeto (confirmado antes de escrever este
arquivo — não é dependência nova que valha a pena introduzir só por
isto). `ler_upload_limitado` é `async def`; os testes que a chamam usam
`asyncio.run(...)` dentro de uma função de teste síncrona comum, em vez
de `async def test_...` com `@pytest.mark.asyncio`.
"""
import asyncio
import typing

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.uploads import (
    ASSINATURAS_ARQUIVO,
    ler_upload_limitado,
    resolver_caminho_seguro,
    validar_assinatura,
)
from app.main import app
from app.routers import ocorrencias as router_mod

from tests.test_seguranca_entrada import _AUTOR, _DBAnexoUpload, _nova_ocorrencia, _novo_tipo


def _dependency_de(annotated_type):
    """Mesmo helper de test_seguranca_entrada.py — acha o Depends()
    escondido dentro de um Annotated[...] pra poder sobrescrevê-lo em
    app.dependency_overrides."""
    for arg in typing.get_args(annotated_type)[1:]:
        dependency = getattr(arg, "dependency", None)
        if dependency is not None:
            return dependency
    raise RuntimeError("Depends não encontrado no tipo anotado")


# ─── SEV-12 — leitura limitada, de verdade (não só o 413 no fim) ──────────────


class _FonteInfinita:
    """Simula um upload malicioso: `.read(n)` sempre devolve `n` bytes,
    pra sempre — nunca acaba sozinho. Se `ler_upload_limitado` não
    abortasse cedo, este teste nunca terminaria (ou estouraria a memória
    de verdade). O teste passar rápido, com poucas chamadas, JÁ é a prova
    de que a leitura é limitada — não precisa (nem seria seguro) simular
    um upload de gigabytes de verdade."""

    def __init__(self):
        self.chamadas = 0
        self.bytes_pedidos = 0

    async def read(self, n: int) -> bytes:
        self.chamadas += 1
        self.bytes_pedidos += n
        return b"\x00" * n


def test_ler_upload_limitado_aborta_sem_drenar_a_fonte_inteira():
    fonte = _FonteInfinita()
    limite = 10 * 1024 * 1024  # 10 MB, mesmo limite do anexo de ocorrência

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ler_upload_limitado(fonte, limite))

    assert exc.value.status_code == 413
    # A fonte é "infinita" — se o helper tivesse lido tudo antes de
    # checar, isto nunca teria retornado. Poucas chamadas de 1 MiB cada
    # (chunk fixo do módulo) provam que abortou cedo, não que "coube".
    assert fonte.chamadas <= 12, fonte.chamadas
    assert fonte.bytes_pedidos <= limite + (2 * 1024 * 1024), fonte.bytes_pedidos


def test_ler_upload_limitado_aceita_arquivo_dentro_do_limite():
    class _FonteFinita:
        def __init__(self, conteudo: bytes):
            self._restante = conteudo

        async def read(self, n: int) -> bytes:
            pedaco, self._restante = self._restante[:n], self._restante[n:]
            return pedaco

    conteudo = b"x" * 1000
    resultado = asyncio.run(ler_upload_limitado(_FonteFinita(conteudo), limite_bytes=2000))
    assert resultado == conteudo


class _RequestContentLength:
    def __init__(self, valor: str):
        self.headers = {"content-length": valor}


def test_ler_upload_limitado_rejeita_pelo_content_length_sem_ler():
    """Rejeição rápida: Content-Length já denuncia que não cabe, então
    nem uma chamada de `.read()` deveria acontecer."""
    fonte = _FonteInfinita()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ler_upload_limitado(fonte, limite_bytes=1000, request=_RequestContentLength("999999999")))

    assert exc.value.status_code == 413
    assert fonte.chamadas == 0


def test_ler_upload_limitado_content_length_nao_e_autoridade_unica():
    """⚠️ O erro caro deste item: confiar só no Content-Length. Um cliente
    pode mandar o cabeçalho baixo (ou nem mandar) e ainda assim tentar
    empurrar mais bytes pelo corpo — a checagem por bloco tem que pegar
    isso mesmo quando o cabeçalho "parecia" ok."""
    fonte = _FonteInfinita()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ler_upload_limitado(fonte, limite_bytes=1000, request=_RequestContentLength("10")))

    assert exc.value.status_code == 413


# ─── SEV-13 — assinatura de arquivo (magic bytes) ─────────────────────────────


def test_validar_assinatura_aceita_jpeg_png_pdf_genuinos():
    assert validar_assinatura(b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg") is True
    assert validar_assinatura(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png") is True
    assert validar_assinatura(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", "application/pdf") is True


def test_validar_assinatura_recusa_exe_disfarcado_de_pdf():
    """O cenário do item: um .exe renomeado para .pdf com
    Content-Type: application/pdf forjado no form. `MZ` é a assinatura de
    executável do Windows — nunca vira `%PDF-` só por trocar o nome."""
    conteudo_exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
    assert validar_assinatura(conteudo_exe, "application/pdf") is False


def test_validar_assinatura_recusa_content_type_desconhecido():
    assert validar_assinatura(b"qualquer coisa", "application/octet-stream") is False


def test_validar_assinatura_cobre_os_tres_tipos_permitidos_no_anexo():
    """Trava simples: se algum dia ANEXO_MIME_PERMITIDOS ganhar um tipo
    novo sem ensinar validar_assinatura, o upload passaria a aceitar
    qualquer conteúdo pra esse tipo sem barreira nenhuma. Ver
    ocorrencias.py:ANEXO_MIME_PERMITIDOS."""
    assert router_mod.ANEXO_MIME_PERMITIDOS <= set(ASSINATURAS_ARQUIVO.keys())


# ─── SEV-14 — caminho de disco não escapa da raiz de upload ───────────────────


def test_resolver_caminho_seguro_aceita_caminho_normal(tmp_path):
    (tmp_path / "ocorrencias").mkdir()
    arquivo = tmp_path / "ocorrencias" / "foto.jpg"
    arquivo.write_bytes(b"conteudo")

    resolvido = resolver_caminho_seguro(tmp_path, "ocorrencias/foto.jpg")
    assert resolvido == arquivo.resolve()
    assert resolvido.is_file()


def test_resolver_caminho_seguro_recusa_escape_com_dotdot(tmp_path):
    """A prova do achado: `Path("uploads") / "../../../etc/passwd"` (o
    código antigo) resolveria pra fora da pasta sem reclamar — só
    funcionava por acidente, porque `caminho` sempre vinha do próprio
    servidor. Este teste finge que não veio."""
    with pytest.raises(HTTPException) as exc:
        resolver_caminho_seguro(tmp_path, "../../../etc/passwd")
    assert exc.value.status_code == 404


def test_resolver_caminho_seguro_recusa_absoluto_que_escapa(tmp_path):
    """Um caminho absoluto gravado por engano no banco também precisa ser
    recusado — `Path(base) / absoluto` no Python IGNORA `base` e vira só
    o absoluto, então isso teria escapado do jeito antigo sem nem
    precisar de `..`."""
    alvo = "C:\\Windows\\win.ini" if str(tmp_path)[1:2] == ":" else "/etc/passwd"
    with pytest.raises(HTTPException):
        resolver_caminho_seguro(tmp_path, alvo)


# ─── Integração — upload de verdade recusa conteúdo que não bate ──────────────


def test_upload_anexo_recusa_conteudo_que_nao_bate_com_o_tipo_declarado(tmp_path, monkeypatch):
    """Fim a fim: POST /ocorrencias/{id}/anexos com Content-Type
    application/pdf mas conteúdo de executável → 415, não 201. Prova que
    a checagem de assinatura está de fato ligada no endpoint, não só
    testada isolada."""
    leitura_dep = _dependency_de(router_mod.LeituraOcorrencia)
    escrita_dep = _dependency_de(router_mod.EscritaOcorrencia)

    oc = _nova_ocorrencia(_novo_tipo(), registrado_por=_AUTOR.id)
    app.dependency_overrides[leitura_dep] = lambda: _AUTOR
    app.dependency_overrides[escrita_dep] = lambda: _AUTOR
    app.dependency_overrides[get_db] = lambda: _DBAnexoUpload(oc)
    monkeypatch.setattr(router_mod, "UPLOAD_ROOT", tmp_path / "uploads" / "ocorrencias")

    try:
        resp = TestClient(app).post(
            f"/ocorrencias/{oc.id}/anexos",
            files={"arquivo": ("relatorio.pdf", b"MZ\x90\x00\x03\x00\x00\x00", "application/pdf")},
            data={"tipo": "BO_PDF"},
        )
    finally:
        app.dependency_overrides.pop(leitura_dep, None)
        app.dependency_overrides.pop(escrita_dep, None)
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 415, resp.text
