"""
Rendu d'une image de carte reelle (fond OpenStreetMap) pour la "carte de
positionnement" de l'audit prospect PDF - remplace une grille abstraite de
carres colores par une vraie carte, pour que le lecteur comprenne concretement
quelle zone geographique a ete testee. Gratuit (tuiles OpenStreetMap
publiques, usage raisonnable), aucune cle API a configurer.

Chaque point de la grille est un marqueur compose (dessine avec Pillow) :
un gros disque colore avec le numero de position au centre (quand connue et
<=10), et un petit emoji accroche en haut a droite (visages Twemoji,
CC-BY 4.0, voir app/assets/) qui redouble l'info de couleur par une
expression - lisible d'un coup d'oeil meme sans distinguer les couleurs,
plus parlant qu'un symbole abstrait.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont
from staticmap import IconMarker, StaticMap

COULEUR_BON = "#16a34a"
COULEUR_ATTENTION = "#d98a06"
COULEUR_DANGER = "#dc2626"
COULEUR_ABSENT = "#9aa5b5"

COULEUR_PAR_NIVEAU = {
    "top3": COULEUR_BON, "milieu": COULEUR_ATTENTION, "loin": COULEUR_DANGER, "absent": COULEUR_ABSENT,
}

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_ASSETS = os.path.join(DOSSIER_APP, "assets")

TAILLE_PX = 760
DIAMETRE_MARQUEUR = 50
TAILLE_EMOJI = 26
_RAYON_PRINCIPAL = DIAMETRE_MARQUEUR / 2
_MARGE = TAILLE_EMOJI // 2 + 4
_TAILLE_ICONE = DIAMETRE_MARQUEUR + _MARGE * 2
_CENTRE_ICONE = _MARGE + _RAYON_PRINCIPAL

# Charges une seule fois au chargement du module - reutilises pour chaque
# marqueur genere plutot que relus depuis le disque a chaque appel.
_EMOJIS = {
    "top3": Image.open(os.path.join(DOSSIER_ASSETS, "emoji_bon.png")).convert("RGBA").resize(
        (TAILLE_EMOJI, TAILLE_EMOJI), Image.LANCZOS
    ),
    "milieu": Image.open(os.path.join(DOSSIER_ASSETS, "emoji_moyen.png")).convert("RGBA").resize(
        (TAILLE_EMOJI, TAILLE_EMOJI), Image.LANCZOS
    ),
    "loin": Image.open(os.path.join(DOSSIER_ASSETS, "emoji_mauvais.png")).convert("RGBA").resize(
        (TAILLE_EMOJI, TAILLE_EMOJI), Image.LANCZOS
    ),
}


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


def _coller_emoji_tendance(image: Image.Image, draw: ImageDraw.ImageDraw, cx: float, cy: float, niveau: str):
    if niveau not in _EMOJIS:
        return  # "absent" (fiche non trouvee a ce point) : rien a exprimer comme tendance

    rayon_fond = TAILLE_EMOJI / 2 + 2
    draw.ellipse((cx - rayon_fond, cy - rayon_fond, cx + rayon_fond, cy + rayon_fond), fill="white")
    emoji = _EMOJIS[niveau]
    image.paste(emoji, (int(cx - TAILLE_EMOJI / 2), int(cy - TAILLE_EMOJI / 2)), emoji)


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
    _coller_emoji_tendance(image, draw, cx_badge, cy_badge, niveau)

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
