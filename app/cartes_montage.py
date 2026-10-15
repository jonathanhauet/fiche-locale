"""
Cartes graphiques du montage video (etape numerotee, information, chiffre cle, citation, titre) dessinees avec Pillow aux couleurs, a la
police et aux arrondis de la charte (voir charte_montage.py), puis incrustees et animees par ffmpeg (voir montage_parole.rendre). Une carte est
une image PNG transparente, avec ombre portee.
"""

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import charte_montage

DOSSIER_POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "polices")
TYPES = {"etape": "Étape numérotée", "info": "Information", "chiffre": "Chiffre clé", "citation": "Citation", "titre": "Titre"}
LIMITES = {"numero": 2, "titre": 60, "sous_titre": 90, "valeur": 12, "libelle": 60, "texte": 140, "auteur": 40}
LARGEUR_CARTE = {"vertical": 0.84, "horizontal": 0.40}          # part de la largeur de l'image video
POSITIONS = {"haut": "En haut", "centre": "Au centre", "bas": "En bas", "partage": "Écran coupé (vous en bas)"}


def nettoyer_textes(textes) -> dict:
    textes = textes if isinstance(textes, dict) else {}
    propres = {}
    for cle, limite in LIMITES.items():
        valeur = " ".join(str(textes.get(cle) or "").split())[:limite]
        propres[cle] = valeur
    return propres


def _police(charte: dict, taille: int, gras: bool = True):
    nom = charte_montage.POLICES[charte["police"]] if gras else "Poppins-Regular.ttf"
    return ImageFont.truetype(os.path.join(DOSSIER_POLICES, nom), max(8, int(taille)))


def _lignes(dessin, texte: str, police, largeur_max: int) -> list:
    """Retour a la ligne par mots pour tenir dans largeur_max pixels."""
    lignes, courante = [], ""
    for mot in texte.split():
        essai = (courante + " " + mot).strip()
        if courante and dessin.textlength(essai, font=police) > largeur_max:
            lignes.append(courante)
            courante = mot
        else:
            courante = essai
    if courante:
        lignes.append(courante)
    return lignes


def _bloc_texte(dessin, texte: str, police, largeur_max: int, couleur, x: int, y: int, interligne: float = 1.22, centre: bool = False) -> int:
    """Ecrit un paragraphe et renvoie sa hauteur."""
    hauteur_ligne = int(police.size * interligne)
    lignes = _lignes(dessin, texte, police, largeur_max)
    for i, ligne in enumerate(lignes):
        largeur = dessin.textlength(ligne, font=police)
        dessin.text((x + ((largeur_max - largeur) / 2 if centre else 0), y + i * hauteur_ligne), ligne, font=police, fill=couleur)
    return len(lignes) * hauteur_ligne


def _hauteur_texte(dessin, texte: str, police, largeur_max: int, interligne: float = 1.22) -> int:
    return len(_lignes(dessin, texte, police, largeur_max)) * int(police.size * interligne)


def rendre_carte(genre: str, textes: dict, charte: dict, format_video: str = "vertical") -> bytes:
    """Image PNG (transparente) d'une carte. genre : etape, info, chiffre, citation ou titre ; textes : voir LIMITES."""
    if genre not in TYPES:
        raise ValueError("Type de carte inconnu.")
    charte = charte_montage.nettoyer(charte)
    textes = nettoyer_textes(textes)
    largeur_video = 1080 if format_video == "vertical" else 1920
    largeur = int(largeur_video * LARGEUR_CARTE.get(format_video, 0.84))
    echelle = largeur / 907
    rayon = int(largeur * charte_montage.ARRONDIS[charte["arrondi"]])
    marge = int(46 * echelle)
    principale, secondaire = charte_montage.rgb(charte["principale"]), charte_montage.rgb(charte["secondaire"])
    couleur_texte, fond = charte_montage.rgb(charte["texte"]), charte_montage.rgb(charte["fond"])
    gris = tuple(int((a * 2 + b) / 3) for a, b in zip(couleur_texte, fond))
    mesure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    interieur = largeur - 2 * marge
    elements = []                    # (hauteur, fonction de dessin(dessin, y))
    couleur_fond = fond

    if genre == "etape":
        badge = int(118 * echelle)
        police_titre, police_sous = _police(charte, 54 * echelle), _police(charte, 34 * echelle, False)
        largeur_texte = interieur - badge - int(34 * echelle)
        titre = textes["titre"] or "Étape"
        h_titre = _hauteur_texte(mesure, titre, police_titre, largeur_texte)
        h_sous = _hauteur_texte(mesure, textes["sous_titre"], police_sous, largeur_texte) if textes["sous_titre"] else 0
        hauteur_contenu = max(badge, h_titre + (h_sous + int(8 * echelle) if h_sous else 0))

        def dessiner_etape(d, y):
            d.rounded_rectangle((marge, y, marge + badge, y + badge), radius=int(badge * 0.28), fill=principale)
            police_numero = _police(charte, 70 * echelle)
            numero = textes["numero"] or "1"
            d.text((marge + badge / 2, y + badge / 2), numero, font=police_numero, fill=(255, 255, 255), anchor="mm")
            x = marge + badge + int(34 * echelle)
            haut = y + (hauteur_contenu - (h_titre + (h_sous + int(8 * echelle) if h_sous else 0))) // 2
            _bloc_texte(d, titre, police_titre, largeur_texte, couleur_texte, x, haut)
            if h_sous:
                _bloc_texte(d, textes["sous_titre"], police_sous, largeur_texte, gris, x, haut + h_titre + int(8 * echelle))
        elements.append((hauteur_contenu, dessiner_etape))

    elif genre == "info":
        pastille = int(96 * echelle)
        police_titre, police_sous = _police(charte, 50 * echelle), _police(charte, 33 * echelle, False)
        largeur_texte = interieur - pastille - int(30 * echelle)
        titre = textes["titre"] or "À retenir"
        h_titre = _hauteur_texte(mesure, titre, police_titre, largeur_texte)
        h_sous = _hauteur_texte(mesure, textes["sous_titre"], police_sous, largeur_texte) if textes["sous_titre"] else 0
        hauteur_contenu = max(pastille, h_titre + (h_sous + int(8 * echelle) if h_sous else 0))

        def dessiner_info(d, y):
            cx, cy = marge + pastille / 2, y + pastille / 2
            d.ellipse((marge, y, marge + pastille, y + pastille), fill=principale)
            r = pastille * 0.26                                       # coche tracee a la main : pas besoin de police d'icones
            d.line([(cx - r, cy + r * 0.05), (cx - r * 0.3, cy + r * 0.75), (cx + r, cy - r * 0.7)], fill=(255, 255, 255), width=max(6, int(pastille * 0.1)), joint="curve")
            x = marge + pastille + int(30 * echelle)
            haut = y + (hauteur_contenu - (h_titre + (h_sous + int(8 * echelle) if h_sous else 0))) // 2
            _bloc_texte(d, titre, police_titre, largeur_texte, couleur_texte, x, haut)
            if h_sous:
                _bloc_texte(d, textes["sous_titre"], police_sous, largeur_texte, gris, x, haut + h_titre + int(8 * echelle))
        elements.append((hauteur_contenu, dessiner_info))

    elif genre == "chiffre":
        police_valeur, police_libelle = _police(charte, 170 * echelle), _police(charte, 44 * echelle, False)
        valeur = textes["valeur"] or "100 %"
        h_valeur = int(police_valeur.size * 1.05)
        h_libelle = _hauteur_texte(mesure, textes["libelle"], police_libelle, interieur) if textes["libelle"] else 0
        hauteur_contenu = h_valeur + (h_libelle + int(10 * echelle) if h_libelle else 0)

        def dessiner_chiffre(d, y):
            d.text((largeur / 2, y + h_valeur / 2), valeur, font=police_valeur, fill=principale, anchor="mm")
            if h_libelle:
                _bloc_texte(d, textes["libelle"], police_libelle, interieur, couleur_texte, marge, y + h_valeur + int(10 * echelle), centre=True)
        elements.append((hauteur_contenu, dessiner_chiffre))

    elif genre == "citation":
        police_texte, police_auteur = _police(charte, 46 * echelle), _police(charte, 32 * echelle, False)
        texte = textes["texte"] or "…"
        guillemet = int(110 * echelle)
        h_texte = _hauteur_texte(mesure, texte, police_texte, interieur, 1.3)
        h_auteur = int(police_auteur.size * 1.3) if textes["auteur"] else 0
        hauteur_contenu = guillemet + h_texte + (h_auteur + int(18 * echelle) if h_auteur else 0)

        def dessiner_citation(d, y):
            d.text((marge, y - int(24 * echelle)), "“", font=_police(charte, 190 * echelle), fill=secondaire)
            _bloc_texte(d, texte, police_texte, interieur, couleur_texte, marge, y + guillemet, 1.3)
            if h_auteur:
                d.text((marge, y + guillemet + h_texte + int(18 * echelle)), "— " + textes["auteur"], font=police_auteur, fill=gris)
        elements.append((hauteur_contenu, dessiner_citation))

    else:  # titre : bandeau plein, texte blanc
        couleur_fond = principale
        police_titre = _police(charte, 72 * echelle)
        titre = (textes["titre"] or "Titre").upper() if charte["police"] == "Bebas Neue" else (textes["titre"] or "Titre")
        hauteur_contenu = _hauteur_texte(mesure, titre, police_titre, interieur, 1.15)

        def dessiner_titre(d, y):
            _bloc_texte(d, titre, police_titre, interieur, (255, 255, 255), marge, y, 1.15, centre=True)
        elements.append((hauteur_contenu, dessiner_titre))

    hauteur = sum(h for h, _ in elements) + 2 * marge
    ombre_marge = int(40 * echelle)
    image = Image.new("RGBA", (largeur + 2 * ombre_marge, hauteur + 2 * ombre_marge), (0, 0, 0, 0))
    ombre = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(ombre).rounded_rectangle((ombre_marge, ombre_marge + int(12 * echelle), ombre_marge + largeur, ombre_marge + hauteur + int(12 * echelle)), radius=rayon, fill=(0, 0, 0, 90))
    image = Image.alpha_composite(image, ombre.filter(ImageFilter.GaussianBlur(int(18 * echelle))))
    carte = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(carte)
    dessin.rounded_rectangle((0, 0, largeur - 1, hauteur - 1), radius=rayon, fill=couleur_fond + (255,))
    y = marge
    for h, dessiner in elements:
        dessiner(dessin, y)
        y += h
    image.alpha_composite(carte, (ombre_marge, ombre_marge))
    sortie = BytesIO()
    image.save(sortie, format="PNG")
    return sortie.getvalue()
