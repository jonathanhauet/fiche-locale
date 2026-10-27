"""
QR code « Laisser un avis Google » d'un client, a imprimer (facture, carte de visite, vitrine, flyer) : affiche PNG propre, avec un titre, le QR code
et le nom de l'entreprise. Le lien vient de la fiche Google du client (newReviewUri) ; seuls les liens https Google sont acceptes.
"""
import io
import re
import unicodedata
from urllib.parse import urlparse

import qrcode
from PIL import Image, ImageDraw

from . import titre_photo

HOTES_AUTORISES = ("google.com", "g.page", "goo.gl", "page.link")
LARGEUR = 1000
HAUTEUR = 1300


def lien_valide(lien: str) -> str:
    """Le lien nettoye s'il est en https et chez Google, sinon une chaine vide."""
    lien = (lien or "").strip()
    try:
        adresse = urlparse(lien)
    except ValueError:
        return ""
    hote = (adresse.hostname or "").lower()
    if adresse.scheme != "https" or not hote or not any(hote == h or hote.endswith("." + h) for h in HOTES_AUTORISES):
        return ""
    return lien[:600]


def nom_fichier(nom: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode()
    return "qr-avis-google-" + (re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:40] or "client") + ".png"


def _centre(d, texte, police, y, couleur):
    d.text(((LARGEUR - d.textlength(texte, font=police)) / 2, y), texte, font=police, fill=couleur)


def affiche_png(lien: str, nom: str = "") -> bytes:
    """Affiche 1000 x 1300 px : titre, QR code (800 px), consigne, nom de l'entreprise. ValueError si le lien n'est pas un lien Google https."""
    lien = lien_valide(lien)
    if not lien:
        raise ValueError("Lien d'avis Google invalide.")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(lien)
    qr.make(fit=True)
    qr.box_size = max(1, 800 // (qr.modules_count + 2 * qr.border))           # modules de taille entiere : QR code net, sans pixels de largeurs inegales
    code = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    toile = Image.new("RGB", (LARGEUR, HAUTEUR), "white")
    d = ImageDraw.Draw(toile)
    _centre(d, "Votre avis compte", titre_photo._police("sobre", 78), 70, (20, 20, 24))
    toile.paste(code, ((LARGEUR - code.width) // 2, 210 + (800 - code.height) // 2))
    _centre(d, "Scannez pour laisser un avis Google", titre_photo._police("sobre", 46), 1040, (20, 20, 24))
    nom = " ".join((nom or "").split())[:60]
    if nom:
        _centre(d, nom, titre_photo._police("sobre", 38), 1130, (110, 110, 120))
    tampon = io.BytesIO()
    toile.save(tampon, format="PNG")
    return tampon.getvalue()
