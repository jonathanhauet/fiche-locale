"""
Personnages animes en aplats (style illustration d'entreprise) pour les videos de motion design : un personnage dessine par le code, en tenue de travail aux couleurs
du client, qui salue, explique, montre du doigt, a une idee, reflechit ou fete quelque chose, avec des objets (cle, maison, voiture...) qu'il peut tenir en main.
Tout est dessine avec Pillow, image par image, a partir de la charte du client : aucun fichier image n'est necessaire.
"""

import math

from PIL import Image, ImageDraw

SURECHANTILLON = 2                      # le personnage est dessine 2 fois plus grand puis reduit : bords lisses
LARGEUR_REF, HAUTEUR_REF = 1300, 1250   # espace de dessin (les bras tendus et le saut tiennent dedans) : pieds en (650, 1210), sommet de la tete vers y = 255
PIEDS = (650, 1210)
HAUTEUR_VISIBLE = 1000                   # hauteur du personnage debout, en unites de dessin (pour le placer a l'ecran)

ACTIONS = ("explique", "salut", "pointe", "idee", "celebre", "reflechit")
ACCESSOIRES = ("aucun", "ampoule", "cle", "maison", "voiture", "coeur", "etoile", "telephone", "goutte", "calendrier", "bouclier", "epingle", "etoiles", "recherche", "fiche", "avis", "carte")
TENUS_EN_MAIN = ("cle", "telephone")     # ces objets sont portes par le personnage au lieu de flotter a cote de lui
LOOKS = (
    {"peau": (243, 200, 170), "cheveux": (66, 44, 32), "style": "court", "barbe": False, "lunettes": False, "cils": False},
    {"peau": (202, 142, 104), "cheveux": (30, 22, 22), "style": "chignon", "barbe": False, "lunettes": False, "cils": True},
    {"peau": (141, 94, 66), "cheveux": (26, 22, 22), "style": "casquette", "barbe": True, "lunettes": False, "cils": False},
    {"peau": (250, 213, 184), "cheveux": (192, 122, 58), "style": "long", "barbe": False, "lunettes": True, "cils": True},
)
NOMBRE_LOOKS = len(LOOKS)

# Palette Google (bleu, rouge, jaune, vert) : couleurs des vetements du personnage et des objets en « style Google »
GOOGLE_BLEU, GOOGLE_ROUGE, GOOGLE_JAUNE, GOOGLE_VERT = (66, 133, 244), (234, 67, 53), (251, 188, 4), (52, 168, 83)
GOOGLE_COULEURS = (GOOGLE_BLEU, GOOGLE_ROUGE, GOOGLE_JAUNE, GOOGLE_VERT)
GOOGLE_BORD = (218, 220, 224)

LONGUEUR_BRAS, LONGUEUR_AVANT_BRAS = 185, 170
EPAULE_DX = 128                          # ecart de chaque epaule par rapport a l'axe du corps


def _sombre(rgb: tuple, part: float = 0.25) -> tuple:
    return tuple(int(c * (1 - part)) for c in rgb)


def _clair(rgb: tuple, part: float = 0.3) -> tuple:
    return tuple(int(c + (255 - c) * part) for c in rgb)


def _borne(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lisse(x: float) -> float:
    x = _borne(x)
    return x * x * (3 - 2 * x)


# --------------------------------------------------------------------------- poses : angle de chaque bras et mouvement du corps en fonction du temps

def pose(action: str, t: float, parle: bool = True, vers: int = 1) -> dict:
    """
    Pose a l'instant t (secondes depuis le debut de la scene). Angles en degres, 0 = bras le long du corps, 90 = bras tendu a l'horizontale, 180 = bras leve.
    Un bras est un couple (epaule, coude) : angle du haut du bras et angle absolu de l'avant-bras.
    vers : +1 si le texte est a droite du personnage, -1 s'il est a gauche (le personnage le montre et le regarde).
    """
    respiration = math.sin(t * 2.4) * 6
    repos = (12 + 2 * math.sin(t * 1.7), 8 + 3 * math.sin(t * 1.7 + 1))
    gauche, droit = repos, repos
    saut = 0.0
    penche = 3 * math.sin(t * 1.3)
    tete_inclinee = 0.0
    if action == "salut":
        droit = (148 + 4 * math.sin(t * 3), 150 + 26 * math.sin(t * 9))
        gauche = (18 + 4 * math.sin(t * 2), 12)
        penche = -10 * vers
        tete_inclinee = 5 * vers
    elif action == "pointe":
        presse = 0.5 + 0.5 * math.sin(t * 3.2)
        bras = (92 + 6 * presse, 96 + 10 * presse)
        gauche, droit = (bras, repos) if vers < 0 else (repos, bras)
        penche = 16 * vers
        tete_inclinee = 4 * vers
    elif action == "idee":
        droit = (152 + 6 * math.sin(t * 5), 170 + 8 * math.sin(t * 5))
        gauche = (28, 72 + 10 * math.sin(t * 3))
        saut = max(0.0, math.sin(t * 5)) * 22
        tete_inclinee = -6 * vers
    elif action == "celebre":
        gauche = (160 + 10 * math.sin(t * 8), 176 + 6 * math.sin(t * 8 + 1))
        droit = (160 + 10 * math.sin(t * 8 + 2), 176 + 6 * math.sin(t * 8 + 3))
        saut = abs(math.sin(t * 4.2)) * 70
    elif action == "reflechit":
        droit = (28, 176 + 3 * math.sin(t * 2))
        gauche = (30, 95)
        penche = 10 * vers
        tete_inclinee = 8 * vers
    else:                                  # explique : les deux mains accompagnent la parole, a tour de role
        rythme = 2.3 if parle else 1.0
        gauche = (38 + 14 * math.sin(t * rythme), 92 + 22 * math.sin(t * rythme + 1.0))
        droit = (44 + 14 * math.sin(t * rythme + 2.4), 104 + 22 * math.sin(t * rythme + 3.4))
        penche = 6 * vers * math.sin(t * 1.3)
        tete_inclinee = 3 * math.sin(t * 1.9)
    clignement = 1.0 if (t % 3.4) < 0.13 else 0.0
    bouche = (0.5 + 0.5 * math.sin(t * 15) * math.sin(t * 4.1 + 0.7)) if parle and action != "reflechit" else 0.0
    if action == "celebre":
        bouche = 1.0
    return {"gauche": gauche, "droit": droit, "respiration": respiration, "saut": saut, "penche": penche, "clignement": clignement, "bouche": _borne(bouche), "regard": vers,
            "sourire": 0.0 if action == "reflechit" else 1.0, "sourcils": -7 if action == "reflechit" else (9 if action in ("idee", "celebre") else 0), "tete": tete_inclinee}


# --------------------------------------------------------------------------- dessin : formes de base

def _cercle(d, k, centre: tuple, rayon: float, couleur: tuple) -> None:
    d.ellipse(((centre[0] - rayon) * k, (centre[1] - rayon) * k, (centre[0] + rayon) * k, (centre[1] + rayon) * k), fill=couleur + (255,))


def _conique(d, k, a: tuple, b: tuple, ra: float, rb: float, couleur: tuple) -> None:
    """Membre qui s'affine de a (rayon ra) vers b (rayon rb), bouts arrondis."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    longueur = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / longueur, dx / longueur
    d.polygon([((a[0] + nx * ra) * k, (a[1] + ny * ra) * k), ((b[0] + nx * rb) * k, (b[1] + ny * rb) * k),
               ((b[0] - nx * rb) * k, (b[1] - ny * rb) * k), ((a[0] - nx * ra) * k, (a[1] - ny * ra) * k)], fill=couleur + (255,))
    _cercle(d, k, a, ra, couleur)
    _cercle(d, k, b, rb, couleur)


def _main(d, k, poignet: tuple, direction: tuple, peau: tuple, cote: int) -> None:
    """Main arrondie avec le pouce du cote interieur, dans le prolongement de l'avant-bras."""
    ux, uy = direction
    nx, ny = -uy * cote, ux * cote
    centre = (poignet[0] + ux * 30, poignet[1] + uy * 30)
    _cercle(d, k, centre, 40, peau)
    _conique(d, k, (centre[0] + nx * 26 - ux * 4, centre[1] + ny * 26 - uy * 4), (centre[0] + nx * 40 + ux * 22, centre[1] + ny * 40 + uy * 22), 15, 12, _sombre(peau, 0.04))
    for i in (-1, 0, 1):                                              # doigts : trois petits bourrelets au bout de la main
        _cercle(d, k, (centre[0] + ux * 38 - nx * i * 17, centre[1] + uy * 38 - ny * i * 17), 17, peau)


def _bras(d, k, epaule: tuple, angles: tuple, cote: int, manche: tuple, peau: tuple) -> tuple:
    """Dessine un bras (avant-bras nu, manche, main) ; renvoie la position de la main en unites de dessin."""
    a1, a2 = math.radians(angles[0]), math.radians(angles[1])
    coude = (epaule[0] + cote * LONGUEUR_BRAS * math.sin(a1), epaule[1] + LONGUEUR_BRAS * math.cos(a1))
    poignet = (coude[0] + cote * (LONGUEUR_AVANT_BRAS - 30) * math.sin(a2), coude[1] + (LONGUEUR_AVANT_BRAS - 30) * math.cos(a2))
    direction = (cote * math.sin(a2), math.cos(a2))
    _conique(d, k, coude, poignet, 38, 31, peau)
    _main(d, k, poignet, direction, peau, -cote)
    _conique(d, k, epaule, coude, 52, 45, manche)
    _cercle(d, k, coude, 45, manche)
    return (poignet[0] + direction[0] * 30, poignet[1] + direction[1] * 30)


def _cheveux(d, k, centre: tuple, rayon: float, look: dict, accent: tuple, avant: bool) -> None:
    cx, cy = centre
    couleur = look["cheveux"]
    reflet = _clair(couleur, 0.18)
    style = look["style"]
    r = rayon
    boite = lambda f: ((cx - r * f) * k, (cy - r * f) * k, (cx + r * f) * k, (cy + r * f) * k)
    if not avant:                          # derriere la tete
        if style == "long":
            d.rounded_rectangle(((cx - r * 1.12) * k, (cy - r * 0.3) * k, (cx + r * 1.12) * k, (cy + r * 1.85) * k), radius=int(r * 0.62 * k), fill=couleur + (255,))
        if style == "chignon":
            _cercle(d, k, (cx, cy - r * 1.22), r * 0.44, couleur)
            _cercle(d, k, (cx - r * 0.1, cy - r * 1.3), r * 0.16, reflet)
        return
    if style == "casquette":
        d.pieslice(boite(1.07), 182, 358, fill=accent + (255,))
        d.pieslice(boite(1.07), 182, 215, fill=_sombre(accent, 0.2) + (255,))
        d.rounded_rectangle(((cx - r * 0.9) * k, (cy - r * 0.62) * k, (cx + r * 1.42) * k, (cy - r * 0.44) * k), radius=int(r * 0.1 * k), fill=_sombre(accent, 0.22) + (255,))
        _cercle(d, k, (cx, cy - r * 1.06), r * 0.07, _sombre(accent, 0.3))
        return
    d.pieslice(boite(1.07), 186, 354, fill=couleur + (255,))
    for cote in (-1, 1):                                                # meches sur les tempes
        d.polygon([((cx + cote * r * 0.98) * k, (cy - r * 0.55) * k), ((cx + cote * r * 1.04) * k, (cy + r * 0.1) * k), ((cx + cote * r * 0.82) * k, (cy - r * 0.2) * k)], fill=couleur + (255,))
    if style in ("court", "long"):
        d.polygon([((cx - r * 1.02) * k, (cy - r * 0.2) * k), ((cx - r * 0.45) * k, (cy - r * 0.72) * k), ((cx + r * 0.7) * k, (cy - r * 0.64) * k), ((cx + r * 1.02) * k, (cy - r * 0.3) * k),
                   ((cx + r * 0.36) * k, (cy - r * 0.56) * k), ((cx - r * 0.2) * k, (cy - r * 0.44) * k)], fill=couleur + (255,))
        d.arc(boite(0.98), 235, 290, fill=reflet + (255,), width=int(9 * k))
    elif style == "chignon":
        d.polygon([((cx - r * 1.0) * k, (cy - r * 0.12) * k), ((cx - r * 0.1) * k, (cy - r * 0.66) * k), ((cx + r * 1.0) * k, (cy - r * 0.12) * k), ((cx + r * 0.1) * k, (cy - r * 0.42) * k)], fill=couleur + (255,))


def _visage(d, k, centre: tuple, rayon: float, p: dict, look: dict) -> None:
    cx, cy = centre
    peau = look["peau"]
    sombre = (36, 28, 32, 255)
    regard = p["regard"] * 6
    d.ellipse(((cx - 8) * k, (cy + rayon * 0.05) * k, (cx + 8) * k, (cy + rayon * 0.34) * k), fill=_sombre(peau, 0.1) + (255,))      # nez
    for cote in (-1, 1):
        ex, ey = cx + cote * rayon * 0.4, cy - rayon * 0.06
        if p["clignement"]:
            d.arc(((ex - 17) * k, (ey - 12) * k, (ex + 17) * k, (ey + 12) * k), 0, 180, fill=sombre, width=int(6 * k))
        else:
            d.ellipse(((ex - 17) * k, (ey - 22) * k, (ex + 17) * k, (ey + 22) * k), fill=(255, 255, 255, 255))
            d.ellipse(((ex - 10 + regard) * k, (ey - 14) * k, (ex + 10 + regard) * k, (ey + 14) * k), fill=(52, 38, 34, 255))
            d.ellipse(((ex - 5 + regard) * k, (ey - 8) * k, (ex + 5 + regard) * k, (ey + 8) * k), fill=sombre)
            d.ellipse(((ex - 7 + regard) * k, (ey - 12) * k, (ex - 1 + regard) * k, (ey - 6) * k), fill=(255, 255, 255, 255))
        if look["cils"]:
            d.line([((ex + cote * 14) * k, (ey - 12) * k), ((ex + cote * 26) * k, (ey - 22) * k)], fill=sombre, width=int(5 * k))
        haut_sourcil = ey - 40 - p["sourcils"] * (0.5 if cote < 0 else 1)
        exterieur, interieur = (ex + cote * 22, haut_sourcil + 5), (ex - cote * 22, haut_sourcil - 2)                 # sourcils arques, plus hauts vers le nez : regard aimable
        d.line([(exterieur[0] * k, (exterieur[1] - 8) * k), (interieur[0] * k, (interieur[1] - 8) * k)], fill=_sombre(look["cheveux"], 0.1) + (255,), width=int(8 * k))
        d.ellipse(((cx + cote * rayon * 0.66 - 19) * k, (cy + rayon * 0.3 - 12) * k, (cx + cote * rayon * 0.66 + 19) * k, (cy + rayon * 0.3 + 12) * k), fill=_melange_peau(peau))
    if look["lunettes"]:
        for cote in (-1, 1):
            ex, ey = cx + cote * rayon * 0.4, cy - rayon * 0.06
            d.ellipse(((ex - 30) * k, (ey - 30) * k, (ex + 30) * k, (ey + 30) * k), outline=(40, 40, 52, 255), width=int(6 * k))
        d.line([((cx - rayon * 0.4 + 30) * k, (cy - rayon * 0.1) * k), ((cx + rayon * 0.4 - 30) * k, (cy - rayon * 0.1) * k)], fill=(40, 40, 52, 255), width=int(5 * k))
    my = cy + rayon * 0.46
    if look["barbe"]:                                                   # barbe courte autour de la bouche
        d.pieslice(((cx - rayon * 0.9) * k, (cy - rayon * 0.2) * k, (cx + rayon * 0.9) * k, (cy + rayon * 1.0) * k), 40, 140, fill=_clair(look["cheveux"], 0.12) + (255,))
        d.ellipse(((cx - 44) * k, (my - 32) * k, (cx + 44) * k, (my + 18) * k), fill=peau + (255,))
    if p["bouche"] > 0.12:
        h = 12 + 30 * p["bouche"]
        d.pieslice(((cx - 32) * k, (my - 16) * k, (cx + 32) * k, (my - 16 + 2 * h) * k), 0, 180, fill=(112, 32, 50, 255))
        if h > 24:
            d.rectangle(((cx - 27) * k, (my - 15) * k, (cx + 27) * k, (my - 6) * k), fill=(255, 255, 255, 255))
        d.pieslice(((cx - 20) * k, (my - 16 + h * 0.9) * k, (cx + 20) * k, (my - 16 + 2 * h - 2) * k), 0, 180, fill=(226, 110, 120, 255))
    elif p["sourire"]:
        d.arc(((cx - 34) * k, (my - 28) * k, (cx + 34) * k, (my + 16) * k), 15, 165, fill=sombre, width=int(7 * k))
    else:
        d.line([((cx - 18) * k, (my + 4) * k), ((cx + 18) * k, (my - 2) * k)], fill=sombre, width=int(7 * k))


def _melange_peau(peau: tuple) -> tuple:
    return (min(255, peau[0] + 6), int(peau[1] * 0.78), int(peau[2] * 0.78), 255)


def dessiner(hauteur: int, p: dict, look: dict, haut: tuple, bas: tuple, chaussures: tuple, accent: tuple, badge: Image.Image = None, google: bool = False) -> Image.Image:
    """
    Personnage de la hauteur voulue (en pixels, canvas compris) ; les pieds sont a (PIEDS / dimensions du canevas). Renvoie un calque RGBA dont `info` donne la position
    de la tete et de la main droite (« tete », « main »). badge : petit logo (RGBA) pose sur la poitrine. google : tenue simple en aplats (t-shirt colore, pantalon gris,
    baskets blanches), sans ceinture a outils ni col.
    """
    k = hauteur / HAUTEUR_REF * SURECHANTILLON
    calque = Image.new("RGBA", (int(LARGEUR_REF * k), int(HAUTEUR_REF * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    P = lambda x, y: (x * k, y * k)
    peau = look["peau"]
    pieds_x, pieds_y = PIEDS[0], PIEDS[1] - p["saut"]
    hanche_y = pieds_y - 410
    penche = p["penche"]
    epaules_y = pieds_y - 690 + p["respiration"] * 0.4
    axe = pieds_x + penche
    haut_sombre, bas_sombre = _sombre(haut, 0.2), _sombre(bas, 0.22)
    # jambes (pantalon, chaussures de securite)
    for cote in (-1, 1):
        a, b = (pieds_x + cote * 56, hanche_y), (pieds_x + cote * 74, pieds_y - 46)
        _conique(d, k, a, b, 64, 50, bas)
        d.polygon([P(a[0] - cote * 6, a[1] + 30), P(b[0] - cote * 44, b[1]), P(b[0] - cote * 14, b[1]), P(a[0] + cote * 10, a[1] + 40)] if cote > 0 else
                  [P(a[0] + 6, a[1] + 30), P(b[0] + 44, b[1]), P(b[0] + 14, b[1]), P(a[0] - 10, a[1] + 40)], fill=bas_sombre + (255,))       # pli interieur du pantalon
        bx = b[0] + cote * 20
        d.rounded_rectangle(P(bx - 66, pieds_y - 62) + P(bx + 66, pieds_y + 2), radius=int(28 * k), fill=(chaussures if not google else (246, 247, 248)) + (255,))
        d.rounded_rectangle(P(bx - 68, pieds_y - 16) + P(bx + 68, pieds_y + 4), radius=int(10 * k), fill=((236, 232, 226) if not google else accent) + (255,))                  # semelle
        d.rounded_rectangle(P(bx + cote * 8 - 18, pieds_y - 52) + P(bx + cote * 8 + 34, pieds_y - 40), radius=int(6 * k), fill=(_clair(chaussures, 0.22) if not google else GOOGLE_BORD) + (255,))
    # bras arriere (celui de gauche a l'ecran), puis buste, cou, tete, bras avant
    epaule_g, epaule_d = (axe - EPAULE_DX, epaules_y + 48), (axe + EPAULE_DX, epaules_y + 48)
    _bras(d, k, epaule_g, p["gauche"], -1, haut, peau)
    taille_y = hanche_y - 10
    torse = [P(axe - 104, epaules_y), P(axe + 104, epaules_y), P(axe + 150, epaules_y + 46), P(pieds_x + 118, taille_y), P(pieds_x - 118, taille_y), P(axe - 150, epaules_y + 46)]
    d.polygon(torse, fill=haut + (255,))
    for cote in (-1, 1):
        _cercle(d, k, (axe + cote * 104, epaules_y + 46), 46, haut)
    d.polygon([P(axe - 150, epaules_y + 46), P(axe - 84, epaules_y + 8), P(pieds_x - 52, taille_y), P(pieds_x - 118, taille_y)], fill=haut_sombre + (255,))   # cote ombre
    if not google:
        d.rounded_rectangle(P(pieds_x - 124, taille_y - 34) + P(pieds_x + 124, taille_y + 22), radius=int(10 * k), fill=(94, 66, 44, 255))              # ceinture a outils
        d.rounded_rectangle(P(pieds_x - 18, taille_y - 28) + P(pieds_x + 18, taille_y + 16), radius=int(6 * k), fill=(214, 184, 96, 255))
        d.rounded_rectangle(P(pieds_x + 62, taille_y) + P(pieds_x + 126, taille_y + 96), radius=int(14 * k), fill=(120, 86, 58, 255))               # poche
        d.rounded_rectangle(P(pieds_x + 70, taille_y + 8) + P(pieds_x + 118, taille_y + 24), radius=int(6 * k), fill=(94, 66, 44, 255))
        d.rounded_rectangle(P(pieds_x + 88, taille_y - 62) + P(pieds_x + 104, taille_y + 8), radius=int(6 * k), fill=(176, 176, 188, 255))            # tournevis dans la poche
        d.rounded_rectangle(P(pieds_x + 86, taille_y - 78) + P(pieds_x + 106, taille_y - 54), radius=int(8 * k), fill=accent + (255,))
    # cou, col de polo
    d.rounded_rectangle(P(axe - 32, epaules_y - 86) + P(axe + 32, epaules_y + 14), radius=int(16 * k), fill=_sombre(peau, 0.1) + (255,))
    if google:                                                         # encolure ronde de t-shirt
        d.pieslice(P(axe - 52, epaules_y - 34) + P(axe + 52, epaules_y + 58), 0, 180, fill=_sombre(peau, 0.06) + (255,))
    else:
        d.polygon([P(axe - 54, epaules_y - 4), P(axe + 54, epaules_y - 4), P(axe, epaules_y + 70)], fill=_sombre(peau, 0.06) + (255,))
        blanc = _clair(haut, 0.72)
        d.polygon([P(axe - 64, epaules_y - 10), P(axe - 8, epaules_y + 6), P(axe - 2, epaules_y + 78), P(axe - 40, epaules_y + 36)], fill=blanc + (255,))
        d.polygon([P(axe + 64, epaules_y - 10), P(axe + 8, epaules_y + 6), P(axe + 2, epaules_y + 78), P(axe + 40, epaules_y + 36)], fill=_sombre(blanc, 0.06) + (255,))
    if badge is not None:
        taille_badge = int(64 * k)
        petit = badge.resize((taille_badge, taille_badge), Image.LANCZOS)
        calque.paste(petit, (int((axe + 70) * k - taille_badge / 2), int((epaules_y + 120) * k - taille_badge / 2)), petit)
    # tete (legerement inclinee par decalage lateral)
    rayon = 136
    tete = (axe + penche * 0.35 + p["tete"] * 1.4, epaules_y - 168 + p["respiration"] * 0.2)
    _cheveux(d, k, tete, rayon, look, accent, False)
    for cote in (-1, 1):
        _cercle(d, k, (tete[0] + cote * rayon * 0.97, tete[1] + 26), 22, _sombre(peau, 0.05))
    d.ellipse(P(tete[0] - rayon, tete[1] - rayon) + P(tete[0] + rayon, tete[1] + rayon * 1.06), fill=peau + (255,))
    _cheveux(d, k, tete, rayon, look, accent, True)
    _visage(d, k, tete, rayon, p, look)
    main = _bras(d, k, epaule_d, p["droit"], 1, haut, peau)
    final = calque.resize((int(LARGEUR_REF * k / SURECHANTILLON), int(HAUTEUR_REF * k / SURECHANTILLON)), Image.LANCZOS)
    final.info["tete"] = (tete[0] * k / SURECHANTILLON, tete[1] * k / SURECHANTILLON)
    final.info["main"] = (main[0] * k / SURECHANTILLON, main[1] * k / SURECHANTILLON)
    return final


# --------------------------------------------------------------------------- personnage geometrique (formes simples, visage minimal)

FORMES = ("detaille", "geo", "moi")                # « moi » : tete BD de l'utilisateur (voir personnage_bd.py) sur le corps geometrique
LARGEUR_TETE_BD = 250                              # largeur de la tete BD, en unites de dessin


def _visage_geo(d, k, centre: tuple, rayon: float, p: dict, look: dict) -> None:
    """Visage minimal : deux points pour les yeux, joues, petit sourire (bouche ouverte quand il parle)."""
    cx, cy = centre
    sombre = (38, 32, 42, 255)
    regard = p["regard"] * 7
    for cote in (-1, 1):
        ex, ey = cx + cote * rayon * 0.36 + regard, cy - rayon * 0.02
        if p["clignement"]:
            d.line([((ex - 14) * k, ey * k), ((ex + 14) * k, ey * k)], fill=sombre, width=int(7 * k))
        else:
            _cercle(d, k, (ex, ey), 13, sombre[:3])
        d.ellipse(((cx + cote * rayon * 0.66 - 20) * k, (cy + rayon * 0.3 - 13) * k, (cx + cote * rayon * 0.66 + 20) * k, (cy + rayon * 0.3 + 13) * k), fill=_melange_peau(look["peau"]))
    my = cy + rayon * 0.4
    if p["bouche"] > 0.15:
        h = 10 + 26 * p["bouche"]
        d.pieslice(((cx - 26) * k, (my - 14) * k, (cx + 26) * k, (my - 14 + 2 * h) * k), 0, 180, fill=(112, 32, 50, 255))
    else:
        d.arc(((cx - 28) * k, (my - 22) * k, (cx + 28) * k, (my + 12) * k), 20, 160, fill=sombre, width=int(7 * k))


def _image_tete_bd(tete_bd: dict, p: dict) -> str:
    """Image de la tete BD a montrer : yeux fermes (clignement), sinon l'emotion de la scene (bouche ouverte quand il parle), sinon l'expression de base."""
    if p.get("clignement") and "yeux" in tete_bd:
        return "yeux"
    parle = p.get("bouche", 0) > 0.35
    emotion = p.get("emotion") or ""
    if emotion:
        if parle and f"{emotion}_ouverte" in tete_bd:
            return f"{emotion}_ouverte"
        if emotion in tete_bd and not parle:
            return emotion
        if emotion in tete_bd and f"{emotion}_ouverte" not in tete_bd:
            return emotion
    return "ouverte" if parle else "ferme"


def _preparer_tete_bd(tete_bd: dict, p: dict, k: float) -> tuple:
    """(image de la tete BD a la bonne taille, (largeur du bas du cou en unites de dessin, couleur du cou)) ; mis en cache : appele a chaque image de la video."""
    nom = _image_tete_bd(tete_bd, p)
    largeur = max(8, int(LARGEUR_TETE_BD * k))
    cache = tete_bd.setdefault("_cache", {})
    if (nom, largeur) not in cache:
        source = tete_bd[nom]
        img = source.resize((largeur, max(8, round(source.size[1] * largeur / source.size[0]))), Image.LANCZOS)
        w_img, h_img = img.size
        bande = img.crop((0, int(h_img * 0.86), w_img, int(h_img * 0.93)))                       # au-dessus du trait du bas du cou
        boite = bande.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
        centre = img.crop((int(w_img * 0.35), int(h_img * 0.86), int(w_img * 0.65), int(h_img * 0.93)))
        pixels = [px for px in centre.getdata() if px[3] > 200 and (px[0] * 0.3 + px[1] * 0.59 + px[2] * 0.11) > 110]      # peau du cou, sans le trait sombre
        if boite and pixels:
            n = len(pixels)
            cou = ((boite[2] - boite[0]) / k, tuple(int(sum(px[i] for px in pixels) / n) for i in range(3)))
        else:
            cou = (LARGEUR_TETE_BD * 0.45, tuple(tete_bd["peau"]))
        cache[(nom, largeur)] = (img, cou)
    return cache[(nom, largeur)]


def _poser_tete_bd(calque: Image.Image, k: float, img: Image.Image, p: dict, bas_centre: tuple) -> None:
    """Pose la tete BD, legerement inclinee autour du bas du cou, le bas du cou en bas_centre."""
    w, h = img.size
    angle = p["tete"] * 0.6
    ancre = (w / 2, h)
    if abs(angle) > 0.15:
        marge = int(w * 0.3)
        toile = Image.new("RGBA", (w + 2 * marge, h + marge), (0, 0, 0, 0))
        toile.paste(img, (marge, 0))
        img = toile.rotate(-angle, resample=Image.BICUBIC, center=(marge + w / 2, h))
        ancre = (marge + w / 2, h)
    calque.paste(img, (int(bas_centre[0] * k - ancre[0]), int(bas_centre[1] * k - ancre[1])), img)


def _bras_geo(d, k, epaule: tuple, angles: tuple, cote: int, couleur: tuple, peau: tuple) -> tuple:
    """Bras epais d'une seule couleur, termine par une main ronde ; renvoie la position de la main."""
    a1, a2 = math.radians(angles[0]), math.radians(angles[1])
    coude = (epaule[0] + cote * LONGUEUR_BRAS * math.sin(a1), epaule[1] + LONGUEUR_BRAS * math.cos(a1))
    poignet = (coude[0] + cote * (LONGUEUR_AVANT_BRAS - 10) * math.sin(a2), coude[1] + (LONGUEUR_AVANT_BRAS - 10) * math.cos(a2))
    _conique(d, k, epaule, coude, 44, 40, couleur)
    _conique(d, k, coude, poignet, 40, 37, couleur)
    _cercle(d, k, poignet, 44, peau)
    return poignet


def dessiner_geo(hauteur: int, p: dict, look: dict, haut: tuple, bas: tuple, accent: tuple, badge: Image.Image = None, tete_bd: dict = None) -> Image.Image:
    """
    Personnage « geometrique » : tete ronde, corps en gelule d'une couleur, jambes en gelules, bras epais, mains rondes. Meme calque et memes informations que
    dessiner() (info « tete » et « main »), donc interchangeable dans la scene.
    """
    k = hauteur / HAUTEUR_REF * SURECHANTILLON
    calque = Image.new("RGBA", (int(LARGEUR_REF * k), int(HAUTEUR_REF * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    P = lambda x, y: (x * k, y * k)
    peau = tete_bd["peau"] if tete_bd else look["peau"]
    pieds_x, pieds_y = PIEDS[0], PIEDS[1] - p["saut"]
    hanche_y = pieds_y - 330
    epaules_y = pieds_y - 690 + p["respiration"] * 0.4
    axe = pieds_x + p["penche"] * 0.6
    for cote in (-1, 1):                                               # jambes en gelules, baskets blanches a semelle de couleur
        x = pieds_x + cote * 64
        d.rounded_rectangle(P(x - 48, hanche_y - 20) + P(x + 48, pieds_y - 26), radius=int(48 * k), fill=bas + (255,))
        d.rounded_rectangle(P(x - 68 + cote * 8, pieds_y - 46) + P(x + 68 + cote * 8, pieds_y + 4), radius=int(24 * k), fill=(234, 236, 240, 255))
        d.rounded_rectangle(P(x - 68 + cote * 8, pieds_y - 12) + P(x + 68 + cote * 8, pieds_y + 4), radius=int(8 * k), fill=accent + (255,))
    epaule_g, epaule_d = (axe - 126, epaules_y + 52), (axe + 126, epaules_y + 52)
    _bras_geo(d, k, epaule_g, p["gauche"], -1, haut, peau)
    d.rounded_rectangle(P(axe - 142, epaules_y - 4) + P(pieds_x + 142, hanche_y + 50), radius=int(104 * k), fill=haut + (255,))               # corps en gelule
    d.rounded_rectangle(P(axe - 142, epaules_y - 4) + P(axe - 70, hanche_y + 50), radius=int(60 * k), fill=_sombre(haut, 0.14) + (255,))      # ombre du cote gauche
    d.rounded_rectangle(P(axe - 112, epaules_y - 4) + P(pieds_x + 142, hanche_y + 50), radius=int(104 * k), fill=haut + (255,))
    if tete_bd:                                                         # epaules plus plates : le cou repose sur un haut de buste large
        d.rounded_rectangle(P(axe - 142, epaules_y - 4) + P(pieds_x + 142, epaules_y + 140), radius=int(54 * k), fill=haut + (255,))
        d.rounded_rectangle(P(axe - 142, epaules_y - 4) + P(axe - 70, epaules_y + 140), radius=int(40 * k), fill=_sombre(haut, 0.14) + (255,))
        d.rounded_rectangle(P(axe - 112, epaules_y - 4) + P(pieds_x + 142, epaules_y + 140), radius=int(54 * k), fill=haut + (255,))
    if not tete_bd:
        d.rounded_rectangle(P(axe - 34, epaules_y - 92) + P(axe + 34, epaules_y + 8), radius=int(17 * k), fill=_sombre(peau, 0.1) + (255,))
    if badge is not None:
        taille_badge = int(70 * k)
        petit = badge.resize((taille_badge, taille_badge), Image.LANCZOS)
        calque.paste(petit, (int((axe + 62) * k - taille_badge / 2), int((epaules_y + 120) * k - taille_badge / 2)), petit)
    rayon = 132
    tete = (axe + p["penche"] * 0.3 + p["tete"] * 1.4, epaules_y - (250 if tete_bd else 168) + p["respiration"] * 0.2)
    if not tete_bd:
        _cheveux(d, k, tete, rayon, look, accent, False)
        _cercle(d, k, tete, rayon, peau)
        _cheveux(d, k, tete, rayon, look, accent, True)
        _visage_geo(d, k, tete, rayon, p, look)
    else:
        img_tete, (largeur_cou, couleur_cou) = _preparer_tete_bd(tete_bd, p, k)
        cx, cy = tete[0], epaules_y + 6
        rx, ry = largeur_cou * 0.82, largeur_cou * 0.30
        col = _sombre(haut, 0.2) + (255,)
        d.ellipse(P(cx - rx, cy - ry) + P(cx + rx, cy + ry), fill=col)                                           # arriere du col, derriere le cou
        _poser_tete_bd(calque, k, img_tete, p, (cx, cy))
        d.pieslice(P(cx - rx, cy - ry) + P(cx + rx, cy + ry), 0, 180, fill=col)                                   # levre avant du col
        irx, iry = largeur_cou * 0.58, largeur_cou * 0.2
        d.pieslice(P(cx - irx, cy - iry - 14) + P(cx + irx, cy + iry - 14), 0, 180, fill=couleur_cou + (255,))      # ouverture du col : la couleur du cou
    main = _bras_geo(d, k, epaule_d, p["droit"], 1, haut, peau)
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
    elif nom == "cle":                                  # cle a molette, manche en bas (se tient par le manche)
        d.rounded_rectangle(P(41, 40) + P(59, 98), radius=int(8 * k), fill=ombre + (255,))
        d.rounded_rectangle(P(41, 40) + P(59, 70), radius=int(8 * k), fill=principale + (255,))
        d.ellipse(P(26, 4) + P(74, 52), fill=principale + (255,))
        d.polygon([P(42, 12), P(58, 12), P(58, 30), P(42, 30)], fill=ombre + (255,))
        d.ellipse(P(40, 18) + P(60, 38), fill=ombre + (255,))
        d.rectangle(P(41, 22) + P(59, 34), fill=ombre + (255,))
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
    elif nom == "epingle":                              # repere de localisation rouge (comme sur la carte)
        d.ellipse(P(30, 86) + P(70, 96), fill=(0, 0, 0, 40))
        d.polygon([P(24, 52), P(76, 52), P(50, 94)], fill=GOOGLE_ROUGE + (255,))
        d.ellipse(P(20, 6) + P(80, 66), fill=GOOGLE_ROUGE + (255,))
        d.ellipse(P(38, 24) + P(62, 48), fill=(255, 255, 255, 255))
        d.pieslice(P(20, 6) + P(80, 66), 200, 250, fill=_sombre(GOOGLE_ROUGE, 0.15) + (255,))
    elif nom == "etoiles":                              # cinq etoiles d'avis
        d.rounded_rectangle(P(3, 34) + P(97, 72), radius=int(19 * k), fill=(0, 0, 0, 28))
        d.rounded_rectangle(P(3, 30) + P(97, 68), radius=int(19 * k), fill=(255, 255, 255, 255), outline=GOOGLE_BORD + (255,), width=int(2.2 * k))
        for i in range(5):
            cx, cy = 14 + i * 18, 49
            pts = []
            for j in range(10):
                r = 9.2 if j % 2 == 0 else 3.9
                a = -math.pi / 2 + j * math.pi / 5
                pts.append(P(cx + r * math.cos(a), cy + r * math.sin(a)))
            d.polygon(pts, fill=GOOGLE_JAUNE + (255,))
    elif nom == "recherche":                            # barre de recherche avec loupe
        d.rounded_rectangle(P(4, 36) + P(96, 70), radius=int(17 * k), fill=(0, 0, 0, 30))
        d.rounded_rectangle(P(4, 32) + P(96, 66), radius=int(17 * k), fill=(255, 255, 255, 255), outline=GOOGLE_BORD + (255,), width=int(2.2 * k))
        d.ellipse(P(14, 40) + P(30, 56), outline=GOOGLE_BLEU + (255,), width=int(3.4 * k))
        d.line([P(28, 54), P(35, 61)], fill=GOOGLE_BLEU + (255,), width=int(3.6 * k))
        d.rounded_rectangle(P(44, 44) + P(86, 50), radius=int(3 * k), fill=(95, 99, 104, 255))
        d.ellipse(P(80, 42) + P(88, 50), fill=GOOGLE_ROUGE + (255,))
    elif nom == "fiche":                                # fiche d'etablissement : photo, nom, etoiles, boutons
        d.rounded_rectangle(P(16, 10) + P(86, 96), radius=int(8 * k), fill=(0, 0, 0, 28))
        d.rounded_rectangle(P(14, 6) + P(84, 92), radius=int(8 * k), fill=(255, 255, 255, 255), outline=GOOGLE_BORD + (255,), width=int(2 * k))
        d.rounded_rectangle(P(14, 6) + P(84, 40), radius=int(8 * k), fill=(232, 240, 254, 255))
        d.rectangle(P(15, 30) + P(83, 40), fill=(232, 240, 254, 255))
        d.polygon([P(30, 34), P(49, 16), P(68, 34)], fill=GOOGLE_BLEU + (255,))
        d.rectangle(P(36, 34) + P(62, 40), fill=GOOGLE_BLEU + (255,))
        d.rounded_rectangle(P(22, 48) + P(62, 55), radius=int(3 * k), fill=(32, 33, 36, 255))
        for i in range(5):
            cx = 26 + i * 9
            pts = []
            for j in range(10):
                r = 4.2 if j % 2 == 0 else 1.8
                a = -math.pi / 2 + j * math.pi / 5
                pts.append(P(cx + r * math.cos(a), 65 + r * math.sin(a)))
            d.polygon(pts, fill=GOOGLE_JAUNE + (255,))
        d.rounded_rectangle(P(22, 76) + P(46, 86), radius=int(5 * k), fill=GOOGLE_BLEU + (255,))
        d.rounded_rectangle(P(52, 76) + P(76, 86), radius=int(5 * k), fill=(232, 240, 254, 255))
    elif nom == "avis":                                 # bulle d'avis client : avatar, etoiles, texte
        d.rounded_rectangle(P(8, 16) + P(94, 76), radius=int(10 * k), fill=(0, 0, 0, 28))
        d.rounded_rectangle(P(6, 12) + P(92, 72), radius=int(10 * k), fill=(255, 255, 255, 255), outline=GOOGLE_BORD + (255,), width=int(2 * k))
        d.polygon([P(24, 71), P(38, 71), P(26, 88)], fill=(255, 255, 255, 255))
        d.line([P(24, 72), P(26, 88), P(38, 71)], fill=GOOGLE_BORD + (255,), width=int(2 * k))
        d.polygon([P(25, 71), P(38, 71), P(26, 86)], fill=(255, 255, 255, 255))
        d.ellipse(P(14, 20) + P(34, 40), fill=GOOGLE_VERT + (255,))
        d.ellipse(P(20, 24) + P(28, 32), fill=(255, 255, 255, 255))
        d.pieslice(P(16, 32) + P(32, 48), 180, 360, fill=(255, 255, 255, 255))
        for i in range(5):
            cx = 44 + i * 9
            pts = []
            for j in range(10):
                r = 4.4 if j % 2 == 0 else 1.9
                a = -math.pi / 2 + j * math.pi / 5
                pts.append(P(cx + r * math.cos(a), 28 + r * math.sin(a)))
            d.polygon(pts, fill=GOOGLE_JAUNE + (255,))
        for y, fin in ((40, 84), (50, 78), (60, 66)):
            d.rounded_rectangle(P(14, y) + P(fin, y + 4.4), radius=int(2.2 * k), fill=(189, 193, 198, 255))
    elif nom == "carte":                                # morceau de plan avec un repere
        d.rounded_rectangle(P(8, 14) + P(92, 88), radius=int(10 * k), fill=(232, 234, 237, 255))
        d.rounded_rectangle(P(8, 14) + P(92, 88), radius=int(10 * k), outline=GOOGLE_BORD + (255,), width=int(2 * k))
        d.polygon([P(14, 60), P(40, 56), P(46, 80), P(20, 84)], fill=(200, 230, 201, 255))
        d.line([P(8, 40), P(40, 46), P(70, 30), P(92, 36)], fill=(255, 255, 255, 255), width=int(6 * k), joint="curve")
        d.line([P(50, 14), P(56, 50), P(66, 88)], fill=(255, 255, 255, 255), width=int(6 * k), joint="curve")
        d.line([P(8, 70), P(92, 64)], fill=(255, 230, 160, 255), width=int(5 * k))
        d.line([P(8, 22), P(30, 36), P(92, 16)], fill=(168, 218, 220, 255), width=int(7 * k), joint="curve")
        d.ellipse(P(60, 62) + P(80, 72), fill=(0, 0, 0, 36))
        d.polygon([P(62, 40), P(78, 40), P(70, 66)], fill=GOOGLE_ROUGE + (255,))
        d.ellipse(P(60, 26) + P(80, 46), fill=GOOGLE_ROUGE + (255,))
        d.ellipse(P(66, 32) + P(74, 40), fill=(255, 255, 255, 255))
    return img.resize((taille, taille), Image.LANCZOS)
