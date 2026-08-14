"""
Double authentification (2FA) par code a usage unique (TOTP), compatible avec
Google Authenticator / Authy / etc. Voir app/main.py pour le flux de connexion
en deux etapes qui utilise ces fonctions.
"""

import base64
from io import BytesIO

import pyotp
import qrcode

NOM_EMETTEUR = "Fiche Locale"


def generer_secret() -> str:
    return pyotp.random_base32()


def uri_provisionnement(secret: str, identifiant: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=identifiant, issuer_name=NOM_EMETTEUR)


def qr_code_data_uri(uri: str) -> str:
    image = qrcode.make(uri)
    tampon = BytesIO()
    image.save(tampon, format="PNG")
    encode = base64.b64encode(tampon.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encode}"


def code_valide(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
