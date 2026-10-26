"""
Pastilles « Avant » / « Apres » dessinees sur deux photos (Pillow) : texte net, aux couleurs de la charte du client.
La pastille « Apres » prend la couleur principale, la pastille « Avant » la premiere couleur secondaire (sinon la principale assombrie),
pour que les deux se distinguent tout en restant dans la charte. Le texte est noir ou blanc selon le contraste.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import carrousel_visuel as cv
from . import titre_photo

POSITIONS = {
    "haut_gauche": "En haut à gauche", "haut_droite": "En haut à droite", "bas_gauche": "En bas à gauche", "bas_droite": "En bas à droite",
}
POSITION_DEFAUT = "haut_gauche"
LONGUEUR_MAX_TEXTE = 14
TEXTES_DEFAUT = ("Avant", "Après")


def couleurs_pastilles(principale: str, secondaires: list) -> tuple:
    """(fond « avant », fond « apres ») en RGB, a partir de la charte."""
    apres = cv._rgb(principale)
    avant = None
    for s in secondaires or []:
        try:
            avant = cv._rgb(s)
            break
        except (ValueError, IndexError):
            continue
    if avant is None or avant == apres:
        avant = cv._melange(apres, (0, 0, 0), 0.55)
    return avant, apres


def texte_pastille(brut, defaut: str) -> str:
    return " ".join(str(brut or "").split())[:LONGUEUR_MAX_TEXTE] or defaut


def _pastille(texte: str, fond: tuple, cote: int) -> Image.Image:
    """Pastille arrondie (texte en majuscules) avec liseré clair et ombre douce, taille proportionnelle au plus petit cote de la photo."""
    hauteur = max(34, round(cote * 0.075))
    police = titre_photo._police("client", round(hauteur * 0.52))
    mesure = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    texte = texte.upper()
    largeur_texte = mesure.textlength(texte, font=police)
    largeur = round(largeur_texte + hauteur * 1.1)
    marge = max(8, hauteur // 4)
    tampon = Image.new("RGBA", (largeur + 2 * marge, hauteur + 2 * marge), (0, 0, 0, 0))
    ombre = Image.new("RGBA", tampon.size, (0, 0, 0, 0))
    ImageDraw.Draw(ombre).rounded_rectangle((marge, marge + hauteur // 14, marge + largeur, marge + hauteur + hauteur // 14), radius=hauteur // 2, fill=(0, 0, 0, 110))
    tampon.alpha_composite(ombre.filter(ImageFilter.GaussianBlur(max(2, hauteur // 14))))
    d = ImageDraw.Draw(tampon)
    d.rounded_rectangle((marge, marge, marge + largeur, marge + hauteur), radius=hauteur // 2, fill=fond + (255,), outline=(255, 255, 255, 235), width=max(2, hauteur // 20))
    boite = d.textbbox((0, 0), texte, font=police)
    d.text((marge + (largeur - (boite[2] - boite[0])) / 2 - boite[0], marge + (hauteur - (boite[3] - boite[1])) / 2 - boite[1]), texte, font=police, fill=titre_photo._texte_sur(fond) + (255,))
    return tampon


def poser(image: Image.Image, texte: str, fond: tuple, position: str = POSITION_DEFAUT) -> Image.Image:
    """Photo avec la pastille posee dans le coin choisi (RGB, orientation EXIF appliquee)."""
    photo = ImageOps.exif_transpose(image)
    if photo.mode in ("RGBA", "LA", "P"):
        photo = photo.convert("RGBA")
        blanc = Image.new("RGB", photo.size, "white")
        blanc.paste(photo, mask=photo.split()[-1])
        photo = blanc
    else:
        photo = photo.convert("RGB")
    largeur, hauteur = photo.size
    pastille = _pastille(texte, fond, min(largeur, hauteur))
    ecart = round(min(largeur, hauteur) * 0.035)
    x = ecart if position.endswith("gauche") else largeur - pastille.width - ecart
    y = ecart if position.startswith("haut") else hauteur - pastille.height - ecart
    sortie = photo.convert("RGBA")
    sortie.alpha_composite(pastille, (max(0, x), max(0, y)))
    return sortie.convert("RGB")


def avant_apres(images: list, textes: tuple, principale: str, secondaires: list, position: str = POSITION_DEFAUT) -> list:
    """Deux photos -> deux photos portant la pastille « avant » puis « apres »."""
    if len(images) != 2:
        raise ValueError("Il faut exactement 2 photos : la 1re est l'avant, la 2e l'après.")
    if position not in POSITIONS:
        position = POSITION_DEFAUT
    fonds = couleurs_pastilles(principale, secondaires)
    return [poser(image, texte_pastille(texte, defaut), fond, position) for image, texte, defaut, fond in zip(images, textes, TEXTES_DEFAUT, fonds)]
