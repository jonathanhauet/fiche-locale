"""
Rendu d'une image de carte reelle (fond OpenStreetMap) pour la "carte de
positionnement" de l'audit prospect PDF - remplace une grille abstraite de
carres colores par une vraie carte, pour que le lecteur comprenne concretement
quelle zone geographique a ete testee. Gratuit (tuiles OpenStreetMap
publiques, usage raisonnable), aucune cle API a configurer.
"""

import io

from staticmap import CircleMarker, StaticMap

# Memes couleurs que audit_prospect_pdf.py (COULEUR_BON/ATTENTION/DANGER/
# GRILLE_ABSENT), dupliquees ici en hexadecimal plutot qu'importees pour
# eviter tout couplage avec le module de mise en page PDF.
COULEUR_BON = "#16a34a"
COULEUR_ATTENTION = "#d98a06"
COULEUR_DANGER = "#dc2626"
COULEUR_ABSENT = "#cbd2dd"

TAILLE_PX = 760
RAYON_MARQUEUR_PX = 16


def _couleur_point(position) -> str:
    if position is None:
        return COULEUR_ABSENT
    if position <= 3:
        return COULEUR_BON
    if position <= 10:
        return COULEUR_ATTENTION
    return COULEUR_DANGER


def generer_image_carte(points: list) -> bytes:
    """
    points : voir audit_prospect.grille_positions_prospect()["points"] -
    [{"latitude", "longitude", "position"}, ...]. Renvoie les octets PNG
    d'une carte OpenStreetMap centree et cadree automatiquement sur les
    points, avec un marqueur colore par point (vert = top 3, orange = top
    4-10, rouge = au-dela/non classee, gris = fiche non trouvee a cet
    endroit). Leve une exception si le rendu echoue (ex. tuiles injoignables) -
    a la charge de l'appelant de retomber sur un affichage de secours plutot
    que de bloquer la generation du PDF.
    """
    carte = StaticMap(TAILLE_PX, TAILLE_PX, url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

    for point in points:
        if point.get("latitude") is None or point.get("longitude") is None:
            continue
        carte.add_marker(CircleMarker(
            (point["longitude"], point["latitude"]), _couleur_point(point.get("position")), RAYON_MARQUEUR_PX,
        ))

    image = carte.render()
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return tampon.getvalue()
