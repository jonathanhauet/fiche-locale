"""Generation de QR codes en data URI (PNG base64), sans fichier a servir."""

import base64
from io import BytesIO

import qrcode


def data_uri(contenu: str) -> str:
    image = qrcode.make(contenu)
    tampon = BytesIO()
    image.save(tampon, format="PNG")
    encode = base64.b64encode(tampon.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encode}"
