"""QR do veículo (Bloco E) — geração de código, SVG e página de impressão.

Fica separado de services/portaria.py porque é uma preocupação bem
delimitada (criptografia/SVG/HTML), reaproveitada por dois endpoints de
routers/portaria_veiculos.py.

🔴 O código do QR é um token opaco (secrets.token_urlsafe) — nunca placa, RE,
nome ou URL. Ver comentário de portaria.credencial.codigo na migration 025.

🔄 Reversão de 2026-08-24 (decisão do Alisson): a etiqueta volta a trazer o
RE do dono, abaixo da placa, em corpo pequeno (~8pt). A regra original deste
módulo dizia "nunca RE, nome ou CPF impresso no adesivo" — motivo de
privacidade. Duas razões operacionais levaram à reversão: (1) identificar o
dono de um carro mal estacionado dentro do pátio sem precisar caminhar até a
guarita; (2) entregar 40 etiquetas de uma tacada sem trocar o adesivo de
dono na hora de distribuir. Nome e CPF continuam banidos do adesivo — RE é
identificador funcional interno, não dado pessoal sensível. Veículo de
EMPRESA/terceira não tem dono pessoa física; a etiqueta desse continua só
com a placa. ⛔ O QR em si não muda: o conteúdo codificado continua sendo o
token opaco — nunca o RE (QR com RE dentro seria crachá clonável por foto).
"""
import html
import io
import secrets

import qrcode
from qrcode.image.svg import SvgPathImage

from app.models.portaria import Credencial, VeiculoPortaria


def gerar_codigo() -> str:
    return secrets.token_urlsafe(16)


def _construir_svg(codigo: str, box_size: int, border: int) -> str:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # aguenta sol e sujeira no adesivo
        box_size=box_size,
        border=border,
        image_factory=SvgPathImage,
    )
    qr.add_data(codigo)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue().decode("utf-8")


def gerar_svg_documento(codigo: str) -> str:
    """Documento SVG completo (com prólogo XML) — para GET .../credencial.svg."""
    return _construir_svg(codigo, box_size=10, border=4)


def gerar_svg_inline(codigo: str) -> str:
    """Só a tag <svg>...</svg>, sem prólogo XML — para embutir na página de
    etiquetas (um documento não pode ter dois prólogos)."""
    doc = _construir_svg(codigo, box_size=10, border=2)
    inicio = doc.find("<svg")
    return doc[inicio:] if inicio != -1 else doc


def montar_html_etiquetas(itens: list[tuple[VeiculoPortaria, Credencial, str | None]]) -> str:
    """HTML autocontido (CSS embutido, sem dependência externa) para
    impressão em A4. Abaixo do QR, placa + RE do dono (reversão de
    2026-08-24, ver docstring do módulo) — ⛔ nunca nome ou CPF impresso no
    adesivo (§1.4 do prompt). `re_dono` vem `None` para veículo de
    EMPRESA/terceira (não tem dono pessoa física) — a etiqueta sai só com a
    placa, como antes da reversão."""
    etiquetas = "".join(
        f'<div class="etq"><div class="qr">{gerar_svg_inline(cred.codigo)}</div>'
        f'<div class="placa">{html.escape(veiculo.placa)}</div>'
        + (f'<div class="re">RE {html.escape(re_dono)}</div>' if re_dono else "")
        + "</div>"
        for veiculo, cred, re_dono in itens
    )
    if not etiquetas:
        etiquetas = '<p class="vazio">Nenhuma credencial ativa para os veículos selecionados.</p>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Etiquetas QR — Portaria Sambaíba</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, sans-serif; margin: 0; padding: 12mm; }}
  .grade {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8mm;
  }}
  .etq {{
    border: 1px dashed #999;
    padding: 4mm;
    text-align: center;
    page-break-inside: avoid;
  }}
  .qr {{ width: 35mm; height: 35mm; margin: 0 auto; }}
  .qr svg {{ width: 100%; height: 100%; }}
  .placa {{
    margin-top: 3mm;
    font-family: 'Courier New', monospace;
    font-weight: bold;
    font-size: 14pt;
    letter-spacing: 1px;
  }}
  .re {{
    margin-top: 1mm;
    font-family: Arial, sans-serif;
    font-size: 8pt;
    color: #444;
  }}
  .vazio {{ font-family: Arial, sans-serif; }}
  @media print {{
    body {{ padding: 0; }}
    .etq {{ border-color: #ccc; }}
  }}
</style>
</head>
<body>
<div class="grade">
{etiquetas}
</div>
</body>
</html>
"""
