"""Utilitários compartilhados de upload de arquivo.

SEV-12, SEV-13, SEV-14 (fechamento da auditoria, 11/08/2026) — três
achados relacionados, um módulo só porque os dois endpoints de upload do
projeto (`ocorrencias.upload_anexo`, `importacao.importar`) precisavam da
mesma correção e escrevê-la duas vezes é como ela diverge em silêncio
(mesmo precedente de `mascaras.js`/`escape.js` no frontend).
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, UploadFile, status

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB por leitura


async def ler_upload_limitado(
    arquivo: UploadFile,
    limite_bytes: int,
    request: Optional[Request] = None,
) -> bytes:
    """SEV-12: lê o upload em blocos, abortando assim que a soma passar do
    limite — nunca materializa o arquivo inteiro em memória antes de saber
    se ele cabe. Antes desta correção, os dois endpoints faziam
    `await arquivo.read()` (lê tudo) e só DEPOIS comparavam o tamanho —
    um POST de alguns GB derrubava o processo antes de qualquer checagem
    rodar.

    `Content-Length`, quando o cliente manda, é usado como rejeição
    rápida (nem começa a ler) — mas não é autoridade: pode vir ausente
    (chunked transfer-encoding) ou forjado baixo pra passar da barreira.
    Por isso a soma real, conferida a cada bloco lido, é o que decide de
    verdade.
    """
    if request is not None:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limite_bytes:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"Arquivo muito grande (máx {limite_bytes // (1024 * 1024)} MB)",
                    )
            except ValueError:
                pass  # cabeçalho malformado — a checagem por bloco abaixo continua valendo

    total = 0
    blocos: list[bytes] = []
    while True:
        bloco = await arquivo.read(_CHUNK_SIZE)
        if not bloco:
            break
        total += len(bloco)
        if total > limite_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Arquivo muito grande (máx {limite_bytes // (1024 * 1024)} MB)",
            )
        blocos.append(bloco)
    return b"".join(blocos)


# SEV-13: Content-Type é o que o CLIENTE afirma que está mandando — trivial
# de forjar num form multipart. A assinatura (magic bytes) é o próprio
# arquivo se identificando; os primeiros bytes não mudam com o nome nem
# com o cabeçalho declarado.
ASSINATURAS_ARQUIVO: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}


def validar_assinatura(conteudo: bytes, content_type: str) -> bool:
    """True se os primeiros bytes do arquivo baterem com a assinatura
    esperada para o Content-Type declarado. `content_type` desconhecido
    (fora do dicionário) sempre falha — quem chama já filtrou por
    ANEXO_MIME_PERMITIDOS antes, então chegar aqui com um tipo de fora é
    bug de chamada, não caso a tolerar."""
    assinaturas = ASSINATURAS_ARQUIVO.get(content_type)
    if not assinaturas:
        return False
    return any(conteudo.startswith(assinatura) for assinatura in assinaturas)


def resolver_caminho_seguro(base: Path, caminho_relativo: str) -> Path:
    """SEV-14: resolve um caminho relativo (gravado no banco) para um
    caminho absoluto dentro de `base`, e recusa qualquer coisa que escape
    dela.

    Defesa em profundidade — hoje `OcorrenciaAnexo.caminho` é gerado só
    pelo próprio servidor (UUID + extensão do upload), então não há
    exploração conhecida hoje. Mas ler/apagar arquivo a partir de um
    caminho relativo (`Path("uploads") / anexo.caminho`, sem checagem)
    depende inteiramente de nenhum código futuro nem nenhuma linha do
    banco jamais conter `../`. `resolve()` + checagem elimina essa
    dependência, em vez de confiar nela.
    """
    raiz = base.resolve()
    candidato = (base / caminho_relativo).resolve()
    if not candidato.is_relative_to(raiz):
        logger.warning("Caminho de anexo fora da raiz de upload recusado: %s", caminho_relativo)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado no servidor")
    return candidato
