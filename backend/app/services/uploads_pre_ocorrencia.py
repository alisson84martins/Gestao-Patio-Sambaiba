"""Gravação em disco do anexo de pré-ocorrência — mesmo padrão de
ocorrencias.py (UPLOAD_ROOT + UUID como nome de arquivo), em módulo
próprio porque o único chamador é um endpoint sem autenticação e não faz
sentido misturar com o router de ocorrência definitiva.
"""
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.pre_ocorrencia import PreOcorrenciaAnexo

# Mesma base de app/routers/ocorrencias.py (Path("uploads")), pasta
# própria — nunca serve por StaticFiles (mesma regra do anexo de
# ocorrência definitiva).
PRE_OC_UPLOAD_ROOT = Path("uploads") / "pre_ocorrencias"


def salvar_anexo_pre_ocorrencia(
    db: Session,
    pre_ocorrencia_id: UUID,
    tipo: str,
    nome_original: str | None,
    content_type: str | None,
    conteudo: bytes,
) -> PreOcorrenciaAnexo:
    pasta = PRE_OC_UPLOAD_ROOT / str(pre_ocorrencia_id)
    pasta.mkdir(parents=True, exist_ok=True)
    extensao = Path(nome_original or "").suffix
    nome_arquivo = f"{uuid4()}{extensao}"
    (pasta / nome_arquivo).write_bytes(conteudo)

    anexo = PreOcorrenciaAnexo(
        pre_ocorrencia_id=pre_ocorrencia_id,
        tipo=tipo,
        caminho=f"pre_ocorrencias/{pre_ocorrencia_id}/{nome_arquivo}",
        nome_original=nome_original,
        mime_type=content_type,
        tamanho_bytes=len(conteudo),
    )
    db.add(anexo)
    db.commit()
    db.refresh(anexo)
    return anexo
