"""
Cartes « maquette d'interface » du montage video : fenetre de chat, liste a cocher, notification, evolution d'un chiffre, appel a l'action
(« commente MOT »), badge compteur et sticker de titre. Dessinees avec Pillow aux couleurs et a la police de la charte (voir
charte_montage.py), comme les autres cartes (voir cartes_montage.py), puis incrustees et animees par ffmpeg.
"""

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from . import cartes_montage as cm
from . import charte_montage

TYPES = {genre: cm.TYPES[genre] for genre in cm.TYPES_INTERFACE}
NB_ELEMENTS_MAX = 4


def elements(textes: dict) -> list:
    """Lignes de la liste a cocher : « element 1 | element 2 | ... », 4 au plus."""
    return [e.strip() for e in str(textes.get("elements") or "").split("|") if e.strip()][:NB_ELEMENTS_MAX]


def _etoile(d, cx, cy, rayon, couleur, branches: int = 8):
    for i in range(branches):
        angle = math.pi * 2 * i / branches
        d.line([(cx + math.cos(angle) * rayon * 0.28, cy + math.sin(angle) * rayon * 0.28), (cx + math.cos(angle) * rayon, cy + math.sin(angle) * rayon)],
               fill=couleur, width=max(3, int(rayon * 0.2)), joint="curve")


def _coche(d, cx, cy, r, couleur, largeur):
    d.line([(cx - r, cy + r * 0.05), (cx - r * 0.3, cy + r * 0.75), (cx + r, cy - r * 0.7)], fill=couleur, width=largeur, joint="curve")


def _fleche_haut(d, cx, cy, r, couleur, largeur):
    d.line([(cx, cy + r), (cx, cy - r)], fill=couleur, width=largeur)
    d.line([(cx - r * 0.8, cy - r * 0.2), (cx, cy - r), (cx + r * 0.8, cy - r * 0.2)], fill=couleur, width=largeur, joint="curve")


def rendre(genre: str, textes: dict, charte: dict, format_video: str = "vertical") -> bytes:
    """Image PNG (transparente, avec ombre) d'une carte « maquette ». textes : voir cartes_montage.LIMITES."""
    if genre not in TYPES:
        raise ValueError("Type de carte inconnu.")
    charte = charte_montage.nettoyer(charte)
    textes = cm.nettoyer_textes(textes)
    largeur_video = 1080 if format_video == "vertical" else 1920
    largeur = int(largeur_video * cm.LARGEUR_CARTE.get(format_video, 0.84))
    e = largeur / 907
    rayon = int(largeur * charte_montage.ARRONDIS[charte["arrondi"]])
    marge = int(40 * e)
    principale, secondaire = charte_montage.rgb(charte["principale"]), charte_montage.rgb(charte["secondaire"])
    couleur_texte, fond = charte_montage.rgb(charte["texte"]), charte_montage.rgb(charte["fond"])
    gris = tuple(int((a * 2 + b) / 3) for a, b in zip(couleur_texte, fond))
    gris_clair = tuple(int(a * 0.12 + b * 0.88) for a, b in zip(couleur_texte, fond))
    mesure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    interieur = largeur - 2 * marge
    entete = 0

    def ligne_entete(d, titre):
        _etoile(d, marge + 22 * e, 52 * e, 22 * e, secondaire)
        d.text((marge + 62 * e, 52 * e), titre, font=cm._police(charte, 36 * e), fill=couleur_texte, anchor="lm")
        d.line([(0, 104 * e), (largeur, 104 * e)], fill=gris_clair, width=max(2, int(2 * e)))

    corps = []          # (hauteur, fonction de dessin(d, y))
    couleur_carte = fond
    angle = 0

    if genre == "chat":
        entete = int(104 * e)
        titre_fenetre = textes["titre"] or "Claude"
        police_msg = cm._police(charte, 38 * e, False)
        largeur_bulle = int(interieur * 0.82)
        message = textes["texte"] or textes["sous_titre"] or "Mon rush brut, avec les ratés"
        h_msg = cm._hauteur_texte(mesure, message, police_msg, largeur_bulle - int(56 * e), 1.3)
        h_bulle = h_msg + int(52 * e)
        h_saisie = int(96 * e)
        hauteur_corps = int(30 * e) + h_bulle + int(60 * e) + h_saisie

        def dessiner_chat(d, y):
            x_bulle = marge + interieur - largeur_bulle
            d.rounded_rectangle((x_bulle, y + 30 * e, x_bulle + largeur_bulle, y + 30 * e + h_bulle), radius=int(30 * e), fill=couleur_texte)
            cm._bloc_texte(d, message, police_msg, largeur_bulle - int(56 * e), fond, x_bulle + int(28 * e), int(y + 30 * e + 26 * e), 1.3)
            y_saisie = y + hauteur_corps - h_saisie
            d.rounded_rectangle((marge, y_saisie, marge + interieur, y_saisie + h_saisie), radius=int(h_saisie / 2), fill=fond, outline=gris_clair, width=max(2, int(3 * e)))
            d.text((marge + 70 * e, y_saisie + h_saisie / 2), "Écris à " + titre_fenetre + "…", font=cm._police(charte, 32 * e, False), fill=gris, anchor="lm")
            d.text((marge + 36 * e, y_saisie + h_saisie / 2), "+", font=cm._police(charte, 40 * e, False), fill=gris, anchor="mm")
            cx = marge + interieur - 48 * e
            d.ellipse((cx - 30 * e, y_saisie + h_saisie / 2 - 30 * e, cx + 30 * e, y_saisie + h_saisie / 2 + 30 * e), fill=couleur_texte)
            _fleche_haut(d, cx, y_saisie + h_saisie / 2, 14 * e, fond, max(3, int(5 * e)))
        corps.append((hauteur_corps, dessiner_chat))

    elif genre == "checklist":
        entete = int(104 * e)
        titre_fenetre = textes["titre"] or "Montage du reel"
        lignes = elements(textes) or ["Blancs coupés", "Prises ratées retirées", "Sous-titres posés"]
        police_item = cm._police(charte, 36 * e, False)
        h_ligne = int(78 * e)
        hauteur_corps = int(24 * e) + (int(60 * e) if textes["sous_titre"] else 0) + len(lignes) * h_ligne + int(10 * e)

        def dessiner_liste(d, y):
            courant = y + 24 * e
            if textes["sous_titre"]:
                d.text((marge, courant + 20 * e), textes["sous_titre"], font=cm._police(charte, 36 * e), fill=couleur_texte, anchor="lm")
                courant += 60 * e
            for ligne in lignes:
                cy = courant + h_ligne / 2
                d.ellipse((marge, cy - 24 * e, marge + 48 * e, cy + 24 * e), fill=principale)
                _coche(d, marge + 24 * e, cy, 11 * e, (255, 255, 255), max(3, int(5 * e)))
                d.text((marge + 74 * e, cy), ligne, font=police_item, fill=couleur_texte, anchor="lm")
                courant += h_ligne
        corps.append((hauteur_corps, dessiner_liste))

    elif genre == "notification":
        auteur = textes["auteur"] or "camille.m"
        message = textes["texte"] or "studio"
        police_msg = cm._police(charte, 56 * e)
        h_msg = cm._hauteur_texte(mesure, message, police_msg, interieur - int(150 * e), 1.15)
        hauteur_corps = max(int(120 * e), int(44 * e) + h_msg + int(10 * e))

        def dessiner_notif(d, y):
            cx, cy = marge + 48 * e, y + 52 * e
            d.ellipse((cx - 48 * e, cy - 48 * e, cx + 48 * e, cy + 48 * e), fill=principale)
            d.text((cx, cy), (auteur[:1] or "C").upper(), font=cm._police(charte, 50 * e), fill=(255, 255, 255), anchor="mm")
            x = marge + 118 * e
            d.text((x, y + 20 * e), auteur, font=cm._police(charte, 32 * e), fill=gris, anchor="lm")
            d.text((x + d.textlength(auteur, font=cm._police(charte, 32 * e)) + 12 * e, y + 20 * e), "· à l'instant", font=cm._police(charte, 30 * e, False), fill=gris, anchor="lm")
            cm._bloc_texte(d, message, police_msg, interieur - int(150 * e), couleur_texte, x, int(y + 44 * e), 1.15)
            ix, iy = marge + interieur - 26 * e, y + 20 * e                                             # logo Instagram simplifie
            d.rounded_rectangle((ix - 26 * e, iy - 26 * e, ix + 26 * e, iy + 26 * e), radius=int(14 * e), outline=couleur_texte, width=max(3, int(4 * e)))
            d.ellipse((ix - 11 * e, iy - 11 * e, ix + 11 * e, iy + 11 * e), outline=couleur_texte, width=max(3, int(4 * e)))
            d.ellipse((ix + 12 * e, iy - 18 * e, ix + 18 * e, iy - 12 * e), fill=couleur_texte)
        corps.append((hauteur_corps, dessiner_notif))

    elif genre == "evolution":
        entete = int(104 * e)
        titre_fenetre = textes["titre"] or "Résultat"
        police_libelle, police_avant, police_apres = cm._police(charte, 34 * e, False), cm._police(charte, 70 * e), cm._police(charte, 130 * e)
        hauteur_corps = int(40 * e) + int(150 * e) + int(30 * e)

        def dessiner_evolution(d, y):
            cy = y + 40 * e + 75 * e
            x = marge
            d.text((x, cy), textes["libelle"] or "Durée", font=police_libelle, fill=gris, anchor="lm")
            x += d.textlength(textes["libelle"] or "Durée", font=police_libelle) + 24 * e
            avant = textes["avant"] or "1:42"
            d.text((x, cy), avant, font=police_avant, fill=gris, anchor="lm")
            largeur_avant = d.textlength(avant, font=police_avant)
            d.line([(x - 4 * e, cy + 4 * e), (x + largeur_avant + 4 * e, cy + 4 * e)], fill=gris, width=max(3, int(6 * e)))          # valeur barree
            x += largeur_avant + 30 * e
            d.line([(x, cy), (x + 56 * e, cy)], fill=couleur_texte, width=max(4, int(8 * e)))                                          # fleche
            d.line([(x + 34 * e, cy - 22 * e), (x + 58 * e, cy), (x + 34 * e, cy + 22 * e)], fill=couleur_texte, width=max(4, int(8 * e)), joint="curve")
            d.text((x + 84 * e, cy), textes["valeur"] or "0:48", font=police_apres, fill=couleur_texte, anchor="lm")
        corps.append((hauteur_corps, dessiner_evolution))

    elif genre == "appel_action":
        mot = (textes["mot"] or "studio")
        verbe = textes["texte"] or "commente"
        police_saisie, police_bouton = cm._police(charte, 56 * e), cm._police(charte, 76 * e)
        h_saisie, h_bouton = int(120 * e), int(150 * e)
        hauteur_corps = h_saisie + int(60 * e) + h_bouton

        def dessiner_appel(d, y):
            d.rounded_rectangle((marge, y, marge + interieur, y + h_saisie), radius=int(18 * e), fill=fond, outline=gris_clair, width=max(2, int(3 * e)))
            d.text((marge + 36 * e, y + h_saisie / 2), mot, font=police_saisie, fill=couleur_texte, anchor="lm")
            x_curseur = marge + 36 * e + d.textlength(mot, font=police_saisie) + 8 * e
            d.line([(x_curseur, y + h_saisie * 0.25), (x_curseur, y + h_saisie * 0.75)], fill=couleur_texte, width=max(3, int(5 * e)))
            yb = y + h_saisie + 60 * e
            d.rounded_rectangle((marge, yb, marge + interieur, yb + h_bouton), radius=int(26 * e), fill=principale)
            texte = f"{verbe} {mot.upper()}"
            d.text((marge + interieur / 2, yb + h_bouton / 2), texte, font=police_bouton, fill=(255, 255, 255), anchor="mm")
        corps.append((hauteur_corps, dessiner_appel))

    elif genre == "badge":
        couleur_carte = couleur_texte
        valeur = textes["valeur"] or "0 s"
        police_valeur = cm._police(charte, 90 * e)
        marge, rayon = int(30 * e), int(34 * e)
        largeur = int(mesure.textlength(valeur, font=police_valeur)) + 2 * marge + int(48 * e) + int(24 * e)          # badge compact : plus etroit que les autres cartes
        interieur = largeur - 2 * marge
        hauteur_corps = int(100 * e)

        def dessiner_badge(d, y):
            cy = y + hauteur_corps / 2
            d.ellipse((marge, cy - 12 * e, marge + 24 * e, cy + 12 * e), fill=secondaire)
            d.text((marge + 48 * e, cy), valeur, font=police_valeur, fill=secondaire, anchor="lm")
        corps.append((hauteur_corps, dessiner_badge))

    else:  # sticker : un bloc blanc arrondi par ligne, legerement incline
        couleur_carte = None
        police_titre = cm._police(charte, 70 * e)
        lignes = cm._lignes(mesure, textes["titre"] or "Titre", police_titre, interieur)[:3]
        h_ligne = int(124 * e)
        hauteur_corps = h_ligne * len(lignes)
        angle = -3

        def dessiner_sticker(d, y):
            for i, ligne in enumerate(lignes):
                l = d.textlength(ligne, font=police_titre) + 70 * e
                x0 = marge + (interieur - l) / 2
                y0 = y + i * h_ligne + 8 * e
                d.rounded_rectangle((x0, y0, x0 + l, y0 + h_ligne - 16 * e), radius=int(30 * e), fill=(255, 255, 255))
                d.text((x0 + l / 2, y0 + (h_ligne - 16 * e) / 2), ligne, font=police_titre, fill=(20, 20, 20), anchor="mm")
        corps.append((hauteur_corps, dessiner_sticker))
        rayon = 0

    haut = entete if entete else (0 if genre == "sticker" else marge)
    hauteur = haut + sum(h for h, _ in corps) + (0 if genre == "sticker" else marge)
    ombre_marge = int(60 * e)
    carte = Image.new("RGBA", (largeur, max(hauteur, 10)), (0, 0, 0, 0))
    d = ImageDraw.Draw(carte)
    if couleur_carte is not None:
        d.rounded_rectangle((0, 0, largeur - 1, hauteur - 1), radius=rayon, fill=couleur_carte + (255,))
    if entete:
        ligne_entete(d, titre_fenetre)
    y = haut
    for h, dessiner in corps:
        dessiner(d, y)
        y += h
    image = Image.new("RGBA", (largeur + 2 * ombre_marge, hauteur + 2 * ombre_marge), (0, 0, 0, 0))
    if couleur_carte is not None:
        ombre = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(ombre).rounded_rectangle((ombre_marge, ombre_marge + int(12 * e), ombre_marge + largeur, ombre_marge + hauteur + int(12 * e)), radius=rayon, fill=(0, 0, 0, 90))
        image = Image.alpha_composite(image, ombre.filter(ImageFilter.GaussianBlur(int(18 * e))))
    image.alpha_composite(carte, (ombre_marge, ombre_marge))
    if angle:
        image = image.rotate(angle, resample=Image.BICUBIC, expand=True)
    sortie = BytesIO()
    image.save(sortie, format="PNG")
    return sortie.getvalue()
