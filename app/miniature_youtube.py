"""
Miniatures YouTube (1280x720) : Gemini fournit la scene (la personne des photos de reference, expressive, a droite,
sans aucun texte) et la plateforme ajoute elle-meme le titre en gros caracteres a gauche - jamais de texte genere
par l'IA dans l'image, qui y met des fautes (surtout en francais).
"""

import io

from PIL import Image, ImageDraw, ImageFilter

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
    # Pastille : premiere couleur secondaire de la marque si elle existe, sinon un jaune vif (une miniature doit
    # claquer : l'eclaircie de la couleur principale donnait un bleu-gris terne).
    accent = tp._couleur_accent(marque, secondaires) if secondaires else (255, 214, 10)
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

    # Poppins tres gras (epaissi par un contour de sa propre couleur), ombre douce plutot qu'un gros contour noir,
    # et la derniere ligne sur une pastille de la couleur d'accent de la marque : rendu actuel, lisible en petit.
    taille, lignes, police = 170, [], None
    while taille >= 60:
        police = tp._police("sobre", taille)
        lignes = _lignes(d, mots, police, ZONE_TEXTE_LARGEUR - 30)
        if len(lignes) <= 4 and len(lignes) * taille * 1.08 <= HAUTEUR - 140:
            break
        taille -= 6
    pas = int(taille * 1.08)
    epaisseur = max(2, taille // 48)
    y0 = (HAUTEUR - len(lignes) * pas) / 2

    ombre = Image.new("RGBA", (LARGEUR, HAUTEUR), (0, 0, 0, 0))
    calque = Image.new("RGBA", (LARGEUR, HAUTEUR), (0, 0, 0, 0))
    do, dc = ImageDraw.Draw(ombre), ImageDraw.Draw(calque)
    for i, ligne in enumerate(lignes):
        y = y0 + i * pas
        if i == len(lignes) - 1 and len(lignes) > 1:
            largeur_ligne = d.textlength(ligne, font=police)
            dc.rounded_rectangle(
                (ZONE_TEXTE_X - 12, y + taille * 0.05, ZONE_TEXTE_X + largeur_ligne + 20, y + pas),
                radius=int(taille * 0.22), fill=accent + (255,),
            )
            couleur_texte = tp._texte_sur(accent)
            dc.text((ZONE_TEXTE_X, y), ligne, font=police, fill=couleur_texte, stroke_width=epaisseur, stroke_fill=couleur_texte)
        else:
            do.text((ZONE_TEXTE_X + 6, y + 8), ligne, font=police, fill=(0, 0, 0, 200), stroke_width=epaisseur)
            dc.text((ZONE_TEXTE_X, y), ligne, font=police, fill=(255, 255, 255), stroke_width=epaisseur, stroke_fill=(255, 255, 255))
    ombre = ombre.filter(ImageFilter.GaussianBlur(8))
    image = Image.alpha_composite(Image.alpha_composite(image.convert("RGBA"), ombre), calque).convert("RGB")
    return _jpeg(image)


def _jpeg(image: Image.Image) -> bytes:
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=88)
    return tampon.getvalue()
