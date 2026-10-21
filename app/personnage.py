"""
Personnages animes en aplats (style illustration d'entreprise) pour les videos de motion design : un petit personnage dessine par le code, qui salue, explique,
montre du doigt, a une idee, reflechit ou fete quelque chose, avec des objets (cle, maison, voiture...) qui apparaissent a cote de lui.
Tout est dessine avec Pillow, image par image, a partir de la charte du client : aucun fichier image n'est necessaire.
"""

import math

from PIL import Image, ImageDraw

SURECHANTILLON = 2                      # le personnage est dessine 2 fois plus grand puis reduit : bords lisses
LARGEUR_REF, HAUTEUR_REF = 1100, 1250   # espace de dessin (les bras tendus et le saut tiennent dedans) : pieds en (550, 1210), sommet de la tete vers y = 272
PIEDS = (550, 1210)
HAUTEUR_VISIBLE = 940                    # hauteur du personnage debout, en unites de dessin (pour le placer a l'ecran)

ACTIONS = ("explique", "salut", "pointe", "idee", "celebre", "reflechit")
ACCESSOIRES = ("aucun", "ampoule", "cle", "maison", "voiture", "coeur", "etoile", "telephone", "goutte", "calendrier", "bouclier")
LOOKS = (
    {"peau": (244, 200, 168), "cheveux": (62, 42, 30), "style": "court"},
    {"peau": (198, 138, 98), "cheveux": (28, 22, 20), "style": "chignon"},
    {"peau": (140, 92, 62), "cheveux": (24, 20, 20), "style": "casquette"},
    {"peau": (250, 214, 184), "cheveux": (196, 132, 62), "style": "long"},
)
NOMBRE_LOOKS = len(LOOKS)

LONGUEUR_BRAS, LONGUEUR_AVANT_BRAS = 190, 170


def _sombre(rgb: tuple, part: float = 0.25) -> tuple:
    return tuple(int(c * (1 - part)) for c in rgb)


def _clair(rgb: tuple, part: float = 0.3) -> tuple:
    return tuple(int(c + (255 - c) * part) for c in rgb)


def _borne(x: float) -> float:
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------------------- poses : angle de chaque bras et mouvement du corps en fonction du temps

def pose(action: str, t: float, parle: bool = True, vers: int = 1) -> dict:
    """
    Pose a l'instant t (secondes depuis le debut de la scene). Angles en degres, 0 = bras le long du corps, 90 = bras tendu a l'horizontale, 180 = bras leve.
    Un bras est un couple (epaule, coude) : angle du haut du bras et angle absolu de l'avant-bras.
    vers : +1 si le texte est a droite du personnage, -1 s'il est a gauche (le personnage le montre et le regarde).
    """
    respiration = math.sin(t * 2.4) * 5
    repos = (14, 10)
    gauche, droit = repos, repos
    saut = 0.0
    penche = 0.0
    if action == "salut":
        droit = (150, 150 + 24 * math.sin(t * 9))
        gauche = (18 + 4 * math.sin(t * 2), 12)
        penche = -8 * vers
    elif action == "pointe":
        bras = (96 + 4 * math.sin(t * 4), 100 + 8 * math.sin(t * 4 + 1))
        gauche, droit = (bras, repos) if vers < 0 else (repos, bras)
        penche = 14 * vers
    elif action == "idee":
        droit = (155 + 5 * math.sin(t * 6), 172 + 8 * math.sin(t * 6))
        gauche = (30, 70 + 10 * math.sin(t * 3))
        saut = max(0.0, math.sin(t * 5)) * 18
    elif action == "celebre":
        gauche = (158 + 10 * math.sin(t * 8), 176 + 6 * math.sin(t * 8 + 1))
        droit = (158 + 10 * math.sin(t * 8 + 2), 176 + 6 * math.sin(t * 8 + 3))
        saut = abs(math.sin(t * 4.2)) * 70
    elif action == "reflechit":
        droit = (28, 176 + 3 * math.sin(t * 2))
        gauche = (30, 95)
        penche = 10 * vers
    else:                                  # explique : les deux mains accompagnent la parole, a tour de role
        rythme = 2.3 if parle else 1.0
        gauche = (38 + 14 * math.sin(t * rythme), 92 + 22 * math.sin(t * rythme + 1.0))
        droit = (44 + 14 * math.sin(t * rythme + 2.4), 104 + 22 * math.sin(t * rythme + 3.4))
        penche = 5 * vers * math.sin(t * 1.3)
    clignement = 1.0 if (t % 3.4) < 0.13 else 0.0
    bouche = (0.5 + 0.5 * math.sin(t * 15) * math.sin(t * 4.1 + 0.7)) if parle and action != "reflechit" else 0.0
    if action == "celebre":
        bouche = 1.0
    return {"gauche": gauche, "droit": droit, "respiration": respiration, "saut": saut, "penche": penche, "clignement": clignement, "bouche": _borne(bouche), "regard": vers,
            "sourire": 0.0 if action == "reflechit" else 1.0, "sourcils": -6 if action == "reflechit" else (8 if action in ("idee", "celebre") else 0)}


# --------------------------------------------------------------------------- dessin

def _membre(d: ImageDraw.ImageDraw, a: tuple, b: tuple, largeur: float, couleur: tuple) -> None:
    d.line([a, b], fill=couleur + (255,), width=int(largeur))
    r = largeur / 2
    for x, y in (a, b):
        d.ellipse((x - r, y - r, x + r, y + r), fill=couleur + (255,))


def _bras(d, k, epaule: tuple, angles: tuple, cote: int, manche: tuple, peau: tuple) -> tuple:
    """Dessine un bras (manche puis avant-bras nu puis main) ; renvoie la position de la main en unites de dessin."""
    a1, a2 = math.radians(angles[0]), math.radians(angles[1])
    coude = (epaule[0] + cote * LONGUEUR_BRAS * math.sin(a1), epaule[1] + LONGUEUR_BRAS * math.cos(a1))
    main = (coude[0] + cote * LONGUEUR_AVANT_BRAS * math.sin(a2), coude[1] + LONGUEUR_AVANT_BRAS * math.cos(a2))
    P = lambda p: (p[0] * k, p[1] * k)
    _membre(d, P(coude), P(main), 66 * k, peau)
    _membre(d, P(epaule), P(coude), 86 * k, manche)
    d.ellipse((P(main)[0] - 42 * k, P(main)[1] - 42 * k, P(main)[0] + 42 * k, P(main)[1] + 42 * k), fill=peau + (255,))
    return main


def _cheveux(d, k, centre: tuple, rayon: float, look: dict, accent: tuple, avant: bool) -> None:
    cx, cy = centre
    couleur = look["cheveux"] + (255,)
    style = look["style"]
    boite = ((cx - rayon * 1.04) * k, (cy - rayon * 1.04) * k, (cx + rayon * 1.04) * k, (cy + rayon * 1.04) * k)
    if not avant:                          # derriere la tete
        if style == "long":
            d.rounded_rectangle(((cx - rayon * 1.1) * k, (cy - rayon * 0.2) * k, (cx + rayon * 1.1) * k, (cy + rayon * 1.65) * k), radius=int(rayon * 0.6 * k), fill=couleur)
        if style == "chignon":
            r = rayon * 0.42
            d.ellipse(((cx - r) * k, (cy - rayon * 1.32 - r) * k, (cx + r) * k, (cy - rayon * 1.32 + r) * k), fill=couleur)
        return
    d.pieslice(boite, 188, 352, fill=couleur)           # calotte sur le haut du crane
    if style in ("court", "long"):
        d.polygon([((cx - rayon * 1.0) * k, (cy - rayon * 0.28) * k), ((cx - rayon * 0.2) * k, (cy - rayon * 0.62) * k), ((cx + rayon * 0.9) * k, (cy - rayon * 0.5) * k),
                   ((cx + rayon * 0.99) * k, (cy - rayon * 0.24) * k), ((cx + rayon * 0.5) * k, (cy - rayon * 0.5) * k), ((cx - rayon * 0.3) * k, (cy - rayon * 0.38) * k)], fill=couleur)
    if style == "casquette":
        d.pieslice(boite, 180, 360, fill=accent + (255,))
        d.rounded_rectangle(((cx - rayon * 0.95) * k, (cy - rayon * 0.42) * k, (cx + rayon * 1.45) * k, (cy - rayon * 0.22) * k), radius=int(rayon * 0.1 * k), fill=_sombre(accent, 0.2) + (255,))


def _visage(d, k, centre: tuple, rayon: float, p: dict, peau: tuple) -> None:
    cx, cy = centre
    sombre = (38, 30, 34, 255)
    regard = p["regard"] * 5
    for cote in (-1, 1):
        ex, ey = cx + cote * rayon * 0.38, cy - rayon * 0.02
        if p["clignement"]:
            d.line([((ex - 15) * k, ey * k), ((ex + 15) * k, ey * k)], fill=sombre, width=int(6 * k))
        else:
            d.ellipse(((ex - 14) * k, (ey - 18) * k, (ex + 14) * k, (ey + 18) * k), fill=(255, 255, 255, 255))
            d.ellipse(((ex - 8 + regard) * k, (ey - 11) * k, (ex + 8 + regard) * k, (ey + 11) * k), fill=sombre)
        d.line([((ex - 20) * k, (ey - 34 - p["sourcils"] * (0.4 if cote < 0 else 1)) * k), ((ex + 20) * k, (ey - 38 - p["sourcils"]) * k)], fill=sombre, width=int(7 * k))
        d.ellipse(((cx + cote * rayon * 0.62 - 17) * k, (cy + rayon * 0.28 - 11) * k, (cx + cote * rayon * 0.62 + 17) * k, (cy + rayon * 0.28 + 11) * k), fill=(240, 130, 130, 110))
    my = cy + rayon * 0.42
    if p["bouche"] > 0.12:
        h = 10 + 28 * p["bouche"]
        d.pieslice(((cx - 28) * k, (my - 14) * k, (cx + 28) * k, (my - 14 + 2 * h) * k), 0, 180, fill=(120, 36, 52, 255))
        d.pieslice(((cx - 18) * k, (my - 14 + h * 0.9) * k, (cx + 18) * k, (my - 14 + 2 * h - 2) * k), 0, 180, fill=(226, 110, 120, 255))
    elif p["sourire"]:
        d.arc(((cx - 30) * k, (my - 24) * k, (cx + 30) * k, (my + 14) * k), 15, 165, fill=sombre, width=int(7 * k))
    else:
        d.line([((cx - 16) * k, (my + 4) * k), ((cx + 16) * k, (my - 2) * k)], fill=sombre, width=int(7 * k))


def dessiner(hauteur: int, p: dict, look: dict, haut: tuple, bas: tuple, chaussures: tuple, accent: tuple) -> Image.Image:
    """Personnage de la hauteur voulue (en pixels, canvas compris) ; les pieds sont au centre du bord bas (a ~3 % du bord). Renvoie un calque RGBA."""
    k = hauteur / HAUTEUR_REF * SURECHANTILLON
    larg, haut_px = int(LARGEUR_REF * k), int(HAUTEUR_REF * k)
    calque = Image.new("RGBA", (larg, haut_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    P = lambda x, y: (x * k, y * k)
    peau = look["peau"]
    saut = p["saut"]
    pieds_x, pieds_y = PIEDS[0], PIEDS[1] - saut
    hanches = (pieds_x, pieds_y - 430)
    penche = p["penche"]
    epaules_y = pieds_y - 690 + p["respiration"] * 0.4
    centre_epaules = (pieds_x + penche, epaules_y)
    # jambes et chaussures
    for cote in (-1, 1):
        haut_jambe = (hanches[0] + cote * 52, hanches[1])
        bas_jambe = (pieds_x + cote * 66, pieds_y - 36)
        _membre(d, P(*haut_jambe), P(*bas_jambe), 92 * k, bas)
        d.rounded_rectangle(((bas_jambe[0] + cote * 14 - 62) * k, (pieds_y - 44) * k, (bas_jambe[0] + cote * 14 + 62) * k, (pieds_y + 2) * k), radius=int(22 * k), fill=chaussures + (255,))
    # bras arriere (celui qui est « derriere » le corps : le gauche de l'ecran), puis buste, tete, bras avant
    epaule_g = (centre_epaules[0] - 118, epaules_y + 20)
    epaule_d = (centre_epaules[0] + 118, epaules_y + 20)
    _bras(d, k, epaule_g, p["gauche"], -1, haut, peau)
    torse = [P(hanches[0] - 96, hanches[1] + 20), P(hanches[0] + 96, hanches[1] + 20), P(centre_epaules[0] + 128, epaules_y + 8), P(centre_epaules[0] - 128, epaules_y + 8)]
    d.polygon(torse, fill=haut + (255,))
    d.ellipse(P(centre_epaules[0] - 128, epaules_y - 22) + P(centre_epaules[0] + 128, epaules_y + 38), fill=haut + (255,))
    d.rounded_rectangle(P(hanches[0] - 100, hanches[1] - 10) + P(hanches[0] + 100, hanches[1] + 40), radius=int(12 * k), fill=bas + (255,))
    d.rounded_rectangle(P(centre_epaules[0] - 28, epaules_y - 70) + P(centre_epaules[0] + 28, epaules_y + 6), radius=int(14 * k), fill=_sombre(peau, 0.08) + (255,))   # cou
    rayon = 118
    tete = (centre_epaules[0] + penche * 0.4, epaules_y - 140 + p["respiration"] * 0.2)
    _cheveux(d, k, tete, rayon, look, accent, False)
    d.ellipse(P(tete[0] - rayon, tete[1] - rayon) + P(tete[0] + rayon, tete[1] + rayon), fill=peau + (255,))
    for cote in (-1, 1):
        d.ellipse(P(tete[0] + cote * rayon * 0.98 - 17, tete[1] + 6) + P(tete[0] + cote * rayon * 0.98 + 17, tete[1] + 46), fill=_sombre(peau, 0.06) + (255,))      # oreilles
    _cheveux(d, k, tete, rayon, look, accent, True)
    _visage(d, k, tete, rayon, p, peau)
    main = _bras(d, k, epaule_d, p["droit"], 1, haut, peau)
    final = calque.resize((int(LARGEUR_REF * k / SURECHANTILLON), int(HAUTEUR_REF * k / SURECHANTILLON)), Image.LANCZOS)
    final.info["tete"] = (tete[0] * k / SURECHANTILLON, tete[1] * k / SURECHANTILLON)
    final.info["main"] = (main[0] * k / SURECHANTILLON, main[1] * k / SURECHANTILLON)
    return final


# --------------------------------------------------------------------------- objets qui apparaissent a cote du personnage

def accessoire(nom: str, taille: int, principale: tuple, secondaire: tuple, clair: tuple, t: float = 0.0) -> Image.Image:
    """Objet en aplats dans un carre de `taille` px (fond transparent) ; t anime quelques details (eclat, goutte, pulsation)."""
    s = SURECHANTILLON
    k = taille * s / 100.0
    img = Image.new("RGBA", (taille * s, taille * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    P = lambda x, y: (x * k, y * k)
    ombre = _sombre(principale, 0.3)
    if nom == "ampoule":
        d.ellipse(P(20, 6) + P(80, 66), fill=(255, 214, 86, 255))
        d.polygon([P(36, 56), P(64, 56), P(60, 76), P(40, 76)], fill=(255, 214, 86, 255))
        d.rounded_rectangle(P(38, 76) + P(62, 84), radius=int(3 * k), fill=(190, 190, 200, 255))
        d.rounded_rectangle(P(42, 86) + P(58, 94), radius=int(4 * k), fill=(150, 150, 160, 255))
        d.arc(P(34, 22) + P(66, 54), 200, 330, fill=(255, 255, 255, 220), width=int(4 * k))
        for a in range(-60, 61, 30):                     # rayons
            r = math.radians(a - 90)
            d.line([P(50 + 46 * math.cos(r), 36 + 46 * math.sin(r)), P(50 + 56 * math.cos(r), 36 + 56 * math.sin(r))], fill=(255, 214, 86, 255), width=int(4 * k))
    elif nom == "cle":
        d.ellipse(P(10, 8) + P(52, 50), fill=principale + (255,))
        d.ellipse(P(22, 20) + P(40, 38), fill=ombre + (255,))
        d.polygon([P(40, 36), P(52, 48), P(88, 84), P(80, 94), P(46, 60)], fill=principale + (255,))
        d.rounded_rectangle(P(62, 66) + P(94, 80), radius=int(3 * k), fill=ombre + (255,))
    elif nom == "maison":
        d.polygon([P(8, 48), P(50, 10), P(92, 48)], fill=secondaire + (255,))
        d.rectangle(P(18, 46) + P(82, 90), fill=clair + (255,))
        d.rectangle(P(42, 62) + P(58, 90), fill=principale + (255,))
        d.rectangle(P(24, 54) + P(36, 66), fill=principale + (255,))
        d.rectangle(P(64, 54) + P(76, 66), fill=principale + (255,))
    elif nom == "voiture":
        d.rounded_rectangle(P(4, 46) + P(96, 76), radius=int(8 * k), fill=principale + (255,))
        d.polygon([P(24, 46), P(36, 24), P(68, 24), P(82, 46)], fill=principale + (255,))
        d.polygon([P(30, 46), P(39, 29), P(48, 29), P(48, 46)], fill=_clair((190, 220, 240), 0.2) + (255,))
        d.polygon([P(52, 46), P(52, 29), P(66, 29), P(76, 46)], fill=_clair((190, 220, 240), 0.2) + (255,))
        for x in (28, 74):
            d.ellipse(P(x - 11, 64) + P(x + 11, 86), fill=(30, 30, 34, 255))
            d.ellipse(P(x - 5, 70) + P(x + 5, 80), fill=(190, 190, 200, 255))
    elif nom == "coeur":
        pulse = 1 + 0.06 * math.sin(t * 7)
        c = lambda x, y: (50 * k + (x - 50) * k * pulse, 52 * k + (y - 52) * k * pulse)
        d.ellipse(c(10, 14) + c(52, 56), fill=(236, 72, 100, 255))
        d.ellipse(c(48, 14) + c(90, 56), fill=(236, 72, 100, 255))
        d.polygon([c(12, 44), c(88, 44), c(50, 92)], fill=(236, 72, 100, 255))
    elif nom == "etoile":
        pts = []
        for i in range(10):
            r = 46 if i % 2 == 0 else 20
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(P(50 + r * math.cos(a), 54 + r * math.sin(a)))
        d.polygon(pts, fill=(255, 200, 54, 255))
    elif nom == "telephone":
        d.rounded_rectangle(P(26, 4) + P(74, 96), radius=int(10 * k), fill=(40, 42, 54, 255))
        d.rounded_rectangle(P(31, 14) + P(69, 84), radius=int(5 * k), fill=principale + (255,))
        d.ellipse(P(44, 38) + P(56, 50), fill=clair + (255,))
        d.arc(P(36, 52) + P(64, 74), 200, 340, fill=clair + (255,), width=int(4 * k))
    elif nom == "goutte":
        d.polygon([P(50, 4), P(80, 56), P(20, 56)], fill=(70, 160, 240, 255))
        d.ellipse(P(20, 38) + P(80, 96), fill=(70, 160, 240, 255))
        d.arc(P(30, 52) + P(54, 84), 100, 190, fill=(255, 255, 255, 200), width=int(4 * k))
    elif nom == "calendrier":
        d.rounded_rectangle(P(10, 14) + P(90, 92), radius=int(8 * k), fill=clair + (255,))
        d.rounded_rectangle(P(10, 14) + P(90, 38), radius=int(8 * k), fill=principale + (255,))
        d.rectangle(P(10, 28) + P(90, 38), fill=principale + (255,))
        for x in (30, 70):
            d.rounded_rectangle(P(x - 3, 6) + P(x + 3, 22), radius=int(3 * k), fill=ombre + (255,))
        for i in range(9):
            cx, cy = 26 + (i % 3) * 24, 50 + (i // 3) * 14
            d.ellipse(P(cx - 4, cy - 4) + P(cx + 4, cy + 4), fill=secondaire + (255,) if i == 4 else (200, 200, 210, 255))
    elif nom == "bouclier":
        d.polygon([P(50, 4), P(90, 18), P(86, 58), P(50, 96), P(14, 58), P(10, 18)], fill=principale + (255,))
        d.line([P(30, 50), P(46, 66), P(72, 34)], fill=clair + (255,), width=int(9 * k), joint="curve")
    return img.resize((taille, taille), Image.LANCZOS)
