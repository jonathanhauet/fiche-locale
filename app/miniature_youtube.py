"""
Miniatures YouTube (1280x720) : Gemini fournit la scene (la personne des photos de reference, expressive, a droite,
sans aucun texte) et la plateforme ajoute elle-meme le titre en gros caracteres a gauche - jamais de texte genere
par l'IA dans l'image, qui y met des fautes (surtout en francais).
"""

import io

from PIL import Image, ImageDraw

from app import carrousel_visuel as cv
from app import titre_photo as tp

LARGEUR, HAUTEUR = 1280, 720
ZONE_TEXTE_X, ZONE_TEXTE_LARGEUR = 50, 700


def _lignes(d, mots, police, largeur_max):
    lignes, courante = [], ""
    for mot in mots:
        essai = f"{courante} {mot}".strip()
        if courante and d.textlength(essai, font=police) > largeur_max:
            lignes.append(courante)
            courante = mot
        else:
            courante = essai
    if courante:
        lignes.append(courante)
    return lignes


def composer(image_octets: bytes, texte: str, couleur: str = "#1f4e8c", secondaires: list = None) -> bytes:
    """Miniature finale (JPEG) : scene recadree en 16:9, degrade sombre a gauche, titre en majuscules."""
    marque = cv._rgb(couleur)
    accent = tp._couleur_accent(marque, secondaires or [])
    image = cv._recadrer(Image.open(io.BytesIO(image_octets)), LARGEUR, HAUTEUR).convert("RGB")

    degrade = Image.linear_gradient("L").rotate(-90, expand=True).resize((int(LARGEUR * 0.72), HAUTEUR))
    masque = Image.new("L", (LARGEUR, HAUTEUR), 0)
    masque.paste(degrade.point(lambda v: int(v * 0.78)), (0, 0))
    teinte = Image.new("RGB", (LARGEUR, HAUTEUR), cv._melange((6, 6, 10), marque, 0.25))
    image = Image.composite(teinte, image, masque)

    d = ImageDraw.Draw(image)
    mots = " ".join((texte or "").split()).upper().split()
    if not mots:
        return _jpeg(image)

    taille, lignes, police = 220, [], None
    while taille >= 70:
        police = tp._police("condensee", taille)
        lignes = _lignes(d, mots, police, ZONE_TEXTE_LARGEUR)
        pas = int(taille * 1.0)
        if len(lignes) <= 4 and len(lignes) * pas <= HAUTEUR - 120 and all(d.textlength(l, font=police) <= ZONE_TEXTE_LARGEUR for l in lignes):
            break
        taille -= 8
    pas = int(taille * 1.0)
    y = (HAUTEUR - len(lignes) * pas) / 2 - taille * 0.08
    for i, ligne in enumerate(lignes):
        couleur_ligne = accent if (i == len(lignes) - 1 and len(lignes) > 1) else (255, 255, 255)
        d.text((ZONE_TEXTE_X, y), ligne, font=police, fill=couleur_ligne, stroke_width=max(6, taille // 22), stroke_fill=(0, 0, 0))
        y += pas
    return _jpeg(image)


def _jpeg(image: Image.Image) -> bytes:
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=88)
    return tampon.getvalue()
