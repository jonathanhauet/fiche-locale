"""
Double authentification (2FA) par code a usage unique (TOTP), compatible avec
Google Authenticator / Authy / etc. Voir app/main.py pour le flux de connexion
en deux etapes qui utilise ces fonctions.
"""

import base64
import secrets
import string
from io import BytesIO

import pyotp
import qrcode

NOM_EMETTEUR = "Fiche Locale"

# Alphabet des codes de recuperation : lettres majuscules + chiffres, sans les
# caracteres visuellement ambigus (0/O, 1/I) pour limiter les erreurs de saisie.
ALPHABET_CODES_RECUPERATION = "".join(
    c for c in string.ascii_uppercase + string.digits if c not in "O0I1"
)


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


def generer_codes_recuperation(nombre: int = 8) -> list[str]:
    """Genere des codes de secours a usage unique, au format 'ABCDE-FGHJK'."""
    codes = []
    for _ in range(nombre):
        brut = "".join(secrets.choice(ALPHABET_CODES_RECUPERATION) for _ in range(10))
        codes.append(f"{brut[:5]}-{brut[5:]}")
    return codes


def normaliser_code_recuperation(code: str) -> str:
    """Reconstruit le format 'ABCDE-FGHJK' a partir d'une saisie utilisateur
    tolerante (espaces, minuscules, tiret omis ou mal place)."""
    brut = "".join(caractere for caractere in code.strip().upper() if caractere.isalnum())
    if len(brut) != 10:
        return code.strip().upper()
    return f"{brut[:5]}-{brut[5:]}"
