"""
Rendu d'une image de carte reelle (fond OpenStreetMap) pour la "carte de
positionnement" de l'audit prospect PDF - remplace une grille abstraite de
carres colores par une vraie carte, pour que le lecteur comprenne concretement
quelle zone geographique a ete testee. Gratuit (tuiles OpenStreetMap
publiques, usage raisonnable), aucune cle API a configurer.

Chaque point de la grille est un marqueur compose (dessine avec Pillow) :
un gros disque colore avec le numero de position au centre (quand connue et
<=10), et un petit badge accroche en haut a droite qui redouble l'info de
couleur par une forme simple (coche/tiret/croix) - lisible d'un coup d'oeil
meme sans distinguer les couleurs.
"""

import io

from PIL import Image, ImageDraw, ImageFont
from staticmap import IconMarker, StaticMap

COULEUR_BON = "#16a34a"
COULEUR_ATTENTION = "#d98a06"
COULEUR_DANGER = "#dc2626"
COULEUR_ABSENT = "#9aa5b5"

COULEUR_PAR_NIVEAU = {
    "top3": COULEUR_BON, "milieu": COULEUR_ATTENTION, "loin": COULEUR_DANGER, "absent": COULEUR_ABSENT,
}

TAILLE_PX = 760
DIAMETRE_MARQUEUR = 50
RAYON_BADGE = 10
_RAYON_PRINCIPAL = DIAMETRE_MARQUEUR / 2
_MARGE = RAYON_BADGE + 4
_TAILLE_ICONE = DIAMETRE_MARQUEUR + _MARGE * 2
_CENTRE_ICONE = _MARGE + _RAYON_PRINCIPAL


def _niveau(position) -> str:
    if position is None:
        return "absent"
    if position <= 3:
        return "top3"
    if position <= 10:
        return "milieu"
    return "loin"


class _MarqueurImage(IconMarker):
    """
    Variante de staticmap.IconMarker qui accepte une image Pillow deja en
    memoire plutot qu'un chemin de fichier sur disque (IconMarker.__init__
    appelle Image.open(file_path), ce qu'on veut eviter ici).
    """
    def __init__(self, coord, image: Image.Image, offset_x: float, offset_y: float):
        self.coord = coord
        self.img = image
        self.offset = (offset_x, offset_y)


def _dessiner_badge_tendance(draw: ImageDraw.ImageDraw, cx: float, cy: float, niveau: str):
    if niveau == "absent":
        return
    draw.ellipse(
        (cx - RAYON_BADGE, cy - RAYON_BADGE, cx + RAYON_BADGE, cy + RAYON_BADGE),
        fill=COULEUR_PAR_NIVEAU[niveau], outline="white", width=2,
    )
    d = RAYON_BADGE * 0.5
    if niveau == "top3":
        draw.line([(cx - d, cy), (cx - d * 0.2, cy + d * 0.8), (cx + d, cy - d * 0.7)], fill="white", width=2)
    elif niveau == "loin":
        draw.line([(cx - d, cy - d), (cx + d, cy + d)], fill="white", width=2)
        draw.line([(cx - d, cy + d), (cx + d, cy - d)], fill="white", width=2)
    else:  # milieu
        draw.line([(cx - d, cy - 2), (cx + d, cy - 2)], fill="white", width=2)
        draw.line([(cx - d, cy + 2), (cx + d, cy + 2)], fill="white", width=2)


def _generer_icone_marqueur(position) -> Image.Image:
    niveau = _niveau(position)
    image = Image.new("RGBA", (_TAILLE_ICONE, _TAILLE_ICONE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse(
        (
            _CENTRE_ICONE - _RAYON_PRINCIPAL, _CENTRE_ICONE - _RAYON_PRINCIPAL,
            _CENTRE_ICONE + _RAYON_PRINCIPAL, _CENTRE_ICONE + _RAYON_PRINCIPAL,
        ),
        fill=COULEUR_PAR_NIVEAU[niveau], outline="white", width=3,
    )

    if position is not None and position <= 10:
        police = ImageFont.load_default(size=int(DIAMETRE_MARQUEUR * 0.42))
        texte = str(position)
        boite = draw.textbbox((0, 0), texte, font=police)
        largeur_texte, hauteur_texte = boite[2] - boite[0], boite[3] - boite[1]
        draw.text(
            (_CENTRE_ICONE - largeur_texte / 2 - boite[0], _CENTRE_ICONE - hauteur_texte / 2 - boite[1]),
            texte, font=police, fill="white",
        )

    cx_badge = _CENTRE_ICONE + _RAYON_PRINCIPAL * 0.72
    cy_badge = _CENTRE_ICONE - _RAYON_PRINCIPAL * 0.72
    _dessiner_badge_tendance(draw, cx_badge, cy_badge, niveau)

    return image


def generer_image_carte(points: list) -> bytes:
    """
    points : voir audit_prospect.grille_positions_prospect()["points"] -
    [{"latitude", "longitude", "position"}, ...]. Renvoie les octets PNG
    d'une carte OpenStreetMap centree et cadree automatiquement sur les
    points, avec un marqueur compose par point (voir _generer_icone_marqueur).
    Leve une exception si le rendu echoue (ex. tuiles injoignables) - a la
    charge de l'appelant de retomber sur un affichage de secours plutot que
    de bloquer la generation du PDF.
    """
    carte = StaticMap(TAILLE_PX, TAILLE_PX, url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

    for point in points:
        if point.get("latitude") is None or point.get("longitude") is None:
            continue
        icone = _generer_icone_marqueur(point.get("position"))
        carte.add_marker(_MarqueurImage(
            (point["longitude"], point["latitude"]), icone, int(_CENTRE_ICONE), int(_CENTRE_ICONE),
        ))

    image = carte.render()
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return tampon.getvalue()
