"""
Videos de motion design creees entierement par code, sans aucun tournage : un scenario (liste de scenes) devient une video MP4 de 15 a 25 s,
aux couleurs et aux polices du client, avec transitions, bruitages et barre de progression.

Chaque scene est dessinee image par image (Pillow) puis envoyee a ffmpeg : accroche en typographie animee, chiffre qui defile, liste qui
glisse, etapes, citation, marque, appel a l'action. Aucun logiciel de montage, aucune dependance en plus.

Scenario : {"scenes": [{"type": "hook", "texte": "Votre *fiche* est _invisible_", "sous_texte": "..."}, ...]} ; dans un texte,
*mot* est surligne sur une pastille de couleur et _mot_ passe en serif italique.
"""

import functools
import math
import os
import re
import subprocess
import threading
import time
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import sons
from .montage_video import FFMPEG

DOSSIER_POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "polices")
FPS = 30
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080), "horizontal": (1920, 1080)}
DUREE_TRANSITION = 0.45
TYPES_SCENES = ("hook", "chiffre", "liste", "etape", "citation", "marque", "cta")
GRAS, NORMAL, SERIF = "Poppins-Bold.ttf", "Poppins-Regular.ttf", "DMSerifDisplay-Regular.ttf"
CREME = (250, 244, 235)


class ErreurMotion(Exception):
    pass


# --------------------------------------------------------------------------- outils : courbes, couleurs, polices

def borne(x: float) -> float:
    return max(0.0, min(1.0, x))


def sortie_cubique(x: float) -> float:
    return 1 - (1 - borne(x)) ** 3


def sortie_expo(x: float) -> float:
    x = borne(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def sortie_rebond(x: float) -> float:
    """Depasse un peu la cible puis revient (overshoot)."""
    x = borne(x)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def entree_sortie(x: float) -> float:
    x = borne(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def melanger(a: tuple, b: tuple, part: float) -> tuple:
    return tuple(int(round(a[i] * (1 - part) + b[i] * part)) for i in range(3))


def luminance(rgb: tuple) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def theme(charte: dict) -> dict:
    """Palette de la video a partir de la charte du montage (couleur principale = accent, fond tres sombre teinte de cette couleur)."""
    principale = tuple(int(charte["principale"][i:i + 2], 16) for i in (1, 3, 5))
    secondaire = tuple(int(charte["secondaire"][i:i + 2], 16) for i in (1, 3, 5))
    sombre = (9, 8, 9)
    return {
        "fond": melanger(principale, sombre, 0.92), "fond2": melanger(principale, sombre, 0.80), "carte": melanger(principale, sombre, 0.86),
        "texte": CREME, "accent": principale, "accent2": secondaire,
        "sur_accent": (17, 17, 17) if luminance(principale) > 0.5 else (255, 255, 255),
    }


@functools.lru_cache(maxsize=96)
def police(nom: str, taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(DOSSIER_POLICES, nom), max(8, int(taille)))


@functools.lru_cache(maxsize=1024)
def image_texte(texte: str, nom: str, taille: int, couleur: tuple, italique: bool = False, espacement: float = 0.0) -> Image.Image:
    """Texte rendu sur fond transparent (marge de 10 px autour). italique : penche (la police serif n'a pas d'italique). espacement : lettres ecartees."""
    f = police(nom, taille)
    marge = 10
    if espacement:
        largeur = sum(f.getlength(c) + espacement * taille for c in texte)
    else:
        largeur = f.getlength(texte)
    ascent, descent = f.getmetrics()
    img = Image.new("RGBA", (int(largeur) + 2 * marge + (int(taille * 0.25) if italique else 0), ascent + descent + 2 * marge), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(img)
    if espacement:
        x = marge
        for c in texte:
            dessin.text((x, marge), c, font=f, fill=couleur + (255,))
            x += f.getlength(c) + espacement * taille
    else:
        dessin.text((marge, marge), texte, font=f, fill=couleur + (255,))
    if italique:
        img = img.transform(img.size, Image.AFFINE, (1, 0.22, -0.22 * img.size[1] * 0.6, 0, 1, 0), resample=Image.BICUBIC)
    return img


def largeur_texte(texte: str, nom: str, taille: int) -> float:
    return police(nom, taille).getlength(texte)


# --------------------------------------------------------------------------- contexte de rendu

class Contexte:
    def __init__(self, format_video: str, charte: dict, marque: str = "", ville: str = ""):
        self.l, self.h = FORMATS[format_video]
        self.format = format_video
        self.u = min(self.l, self.h) / 1080            # echelle : les tailles ci-dessous sont donnees pour 1080 px
        self.t = theme(charte)
        self.marque, self.ville = marque, ville
        self._fonds = {}
        self._glows = {}

    def px(self, valeur: float) -> int:
        return int(round(valeur * self.u))

    def haut_centre(self, hauteur_contenu: float, decalage: float = 0.0) -> int:
        """Ordonnee du haut d'un contenu de la hauteur donnee, centre dans la zone utile de l'ecran."""
        zone_haut, zone_bas = self.h * 0.11, self.h * 0.84
        return int(max(zone_haut, zone_haut + (zone_bas - zone_haut - hauteur_contenu) / 2 + decalage * self.h))

    def fond(self, couleur: tuple, t: float, glow: tuple = None) -> Image.Image:
        """Fond en degrade vertical (mis en cache) avec une lueur douce qui derive lentement."""
        cle = (couleur, glow)
        if cle not in self._fonds:
            haut, bas = couleur, melanger(couleur, (0, 0, 0), 0.45)
            ligne = Image.new("RGB", (1, self.h))
            ligne.putdata([melanger(haut, bas, y / max(1, self.h - 1)) for y in range(self.h)])
            self._fonds[cle] = ligne.resize((self.l, self.h))
        image = self._fonds[cle].copy()
        if glow is not None:
            if glow not in self._glows:
                taille = int(max(self.l, self.h) * 0.9)
                masque = Image.new("L", (taille, taille), 0)
                ImageDraw.Draw(masque).ellipse((taille * 0.18, taille * 0.18, taille * 0.82, taille * 0.82), fill=85)
                masque = masque.filter(ImageFilter.GaussianBlur(taille * 0.12))
                self._glows[glow] = (Image.new("RGB", (taille, taille), glow), masque)
            lueur, masque = self._glows[glow]
            x = int(self.l * 0.5 - masque.size[0] / 2 + math.sin(t * 0.7) * self.l * 0.06)
            y = int(self.h * 0.78 - masque.size[1] / 2 + math.cos(t * 0.5) * self.h * 0.03)
            image.paste(lueur, (x, y), masque)
        self._decor(image, couleur, t)
        return image

    def _decor(self, image: Image.Image, couleur: tuple, t: float) -> None:
        """Deux grands anneaux fins et quelques croix qui derivent lentement : de la vie derriere le texte, sans le gener."""
        trait = melanger(couleur, CREME if luminance(couleur) < 0.5 else (0, 0, 0), 0.10)
        d = ImageDraw.Draw(image)
        for k, (cx, cy, r, vitesse) in enumerate(((0.88, 0.16, 0.34, 0.35), (0.04, 0.80, 0.42, -0.25))):
            x = self.l * cx + math.sin(t * vitesse + k) * self.l * 0.03
            y = self.h * cy + math.cos(t * vitesse * 0.8 + k) * self.h * 0.02
            rayon = min(self.l, self.h) * r
            d.ellipse((x - rayon, y - rayon, x + rayon, y + rayon), outline=trait, width=max(2, self.px(2)))
        for k, (cx, cy) in enumerate(((0.12, 0.22), (0.86, 0.5), (0.2, 0.62), (0.78, 0.84), (0.5, 0.12))):
            x = self.l * cx + math.sin(t * 0.6 + k * 1.7) * self.px(14)
            y = self.h * cy + math.cos(t * 0.5 + k * 2.1) * self.px(14)
            demi = self.px(11)
            d.line((x - demi, y, x + demi, y), fill=trait, width=self.px(3))
            d.line((x, y - demi, x, y + demi), fill=trait, width=self.px(3))


def coller(image: Image.Image, calque: Image.Image, x: int, y: int, opacite: float = 1.0) -> None:
    """Colle un calque RGBA sur l'image RGB (opacite 0 a 1)."""
    if opacite <= 0:
        return
    alpha = calque.getchannel("A")
    if opacite < 1:
        alpha = alpha.point(lambda v: int(v * opacite))
    image.paste(calque.convert("RGB"), (int(x), int(y)), alpha)


def calque_revele(image: Image.Image, calque: Image.Image, x: int, y: int, hauteur: int, avance: float, marge: int = 10) -> None:
    """Revele un calque en le faisant monter derriere le bord inferieur d'un masque de la hauteur d'une ligne (mouvement de titre type motion design)."""
    if avance <= 0:
        return
    boite = Image.new("RGBA", (calque.size[0], hauteur), (0, 0, 0, 0))
    decalage = int((1 - avance) * hauteur * 1.1)
    boite.paste(calque, (0, decalage - marge // 2), calque)
    coller(image, boite, x, y)


# --------------------------------------------------------------------------- composition typographique

def decouper_texte(texte: str) -> list:
    """« Votre *fiche Google* est _invisible_ » -> [("Votre", "n"), ("fiche Google", "pastille"), ("est", "n"), ("invisible", "serif")]."""
    unites = []
    for morceau in re.findall(r"\*[^*]+\*|_[^_]+_|\S+", texte or ""):
        if morceau.startswith("*") and morceau.endswith("*") and len(morceau) > 2:
            unites.append((morceau[1:-1].strip(), "pastille"))
        elif morceau.startswith("_") and morceau.endswith("_") and len(morceau) > 2:
            unites += [(m, "serif") for m in morceau[1:-1].split()]
        else:
            unites.append((morceau.strip("*_"), "n"))
    return [u for u in unites if u[0]]


def composer(ctx: Contexte, texte: str, taille: int, largeur_max: float, nom: str = GRAS, interligne: float = 1.12) -> dict:
    """Place les mots sur des lignes : {"lignes": [[(mot, style, x, largeur)]], "hauteur_ligne", "hauteur"}. Les mots surlignes forment un seul bloc."""
    unites = decouper_texte(texte)
    espace = largeur_texte(" ", nom, taille) * 1.05
    lignes, courante, x = [], [], 0.0
    for mot, style in unites:
        police_mot = SERIF if style == "serif" else nom
        taille_mot = int(taille * 1.12) if style == "serif" else taille
        w = largeur_texte(mot, police_mot, taille_mot) + (0.5 * taille if style == "pastille" else 0)
        if courante and x + w > largeur_max:
            lignes.append(courante)
            courante, x = [], 0.0
        courante.append((mot, style, x, w))
        x += w + espace
    if courante:
        lignes.append(courante)
    hauteur_ligne = int(taille * interligne * 1.18)
    return {"lignes": lignes, "hauteur_ligne": hauteur_ligne, "hauteur": hauteur_ligne * len(lignes), "taille": taille, "nom": nom}


def dessiner_bloc(ctx: Contexte, image: Image.Image, bloc: dict, x0: int, y0: int, t: float, debut: float = 0.0, pas: float = 0.09, alignement: str = "gauche",
                  largeur_zone: float = 0.0, couleur: tuple = None, couleur_accent: tuple = None) -> int:
    """Dessine un bloc compose, mot par mot (montee derriere un masque, pastille qui rebondit). Renvoie l'indice du dernier mot."""
    taille, nom, hauteur_ligne = bloc["taille"], bloc["nom"], bloc["hauteur_ligne"]
    couleur = couleur or ctx.t["texte"]
    couleur_accent = couleur_accent or ctx.t["accent"]
    rang = 0
    for numero, ligne in enumerate(bloc["lignes"]):
        total = ligne[-1][2] + ligne[-1][3] - ligne[0][2]
        decalage = 0 if alignement == "gauche" else (largeur_zone - total) / 2
        y = y0 + numero * hauteur_ligne
        for mot, style, x, w in ligne:
            avance = (t - debut - pas * rang) / 0.55
            rang += 1
            if avance <= 0:
                continue
            xm = int(x0 + decalage + x)
            if style == "pastille":
                p = sortie_rebond(avance * 1.15)
                largeur, hauteur = int(w), int(hauteur_ligne * 0.86)
                calque = Image.new("RGBA", (largeur + 40, hauteur + 40), (0, 0, 0, 0))
                d = ImageDraw.Draw(calque)
                d.rounded_rectangle((20, 20, 20 + largeur, 20 + hauteur), radius=int(hauteur * 0.26), fill=couleur_accent + (255,))
                texte_img = image_texte(mot, nom, taille, ctx.t["sur_accent"])
                calque.paste(texte_img, (20 + int(0.25 * taille) - 10, 20 + int((hauteur - texte_img.size[1]) / 2) + 2), texte_img)
                if p != 1.0:
                    s = max(0.05, p)
                    calque = calque.resize((max(1, int(calque.size[0] * s)), max(1, int(calque.size[1] * s))), Image.BILINEAR)
                cx = xm - 20 + (largeur + 40) / 2
                cy = y + hauteur_ligne * 0.5
                coller(image, calque, cx - calque.size[0] / 2, cy - calque.size[1] / 2, borne(avance * 3))
            else:
                if style == "serif":
                    calque = image_texte(mot, SERIF, int(taille * 1.12), couleur_accent, True)
                else:
                    calque = image_texte(mot, nom, taille, couleur)
                calque_revele(image, calque, xm - 10, y, hauteur_ligne, sortie_expo(avance))
    return rang


def etiquette(ctx: Contexte, image: Image.Image, texte: str, x: int, y: int, couleur: tuple, taille: int = 26, opacite: float = 1.0) -> None:
    """Petite etiquette en majuscules espacees (style « kicker »)."""
    coller(image, image_texte(texte.upper(), NORMAL, ctx.px(taille), couleur, False, 0.16), x - 10, y - 10, opacite)


# --------------------------------------------------------------------------- scenes

def scene_hook(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    bloc = composer(ctx, sc.get("texte", ""), ctx.px(122 if ctx.format != "horizontal" else 96), ctx.l - 2 * marge)
    y0 = int((ctx.h - bloc["hauteur"]) / 2 - ctx.h * 0.04)
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.10)
    if sc.get("sous_texte"):
        suite = 0.1 + 0.09 * sum(len(l) for l in bloc["lignes"]) + 0.5
        petit = composer(ctx, sc["sous_texte"], ctx.px(44), ctx.l - 2 * marge, NORMAL, 1.15)
        for i, ligne in enumerate(petit["lignes"]):
            texte = " ".join(m for m, _, _, _ in ligne)
            coller(image, image_texte(texte, NORMAL, ctx.px(44), (210, 200, 190)), marge - 10, y0 + bloc["hauteur"] + ctx.px(36) + i * petit["hauteur_ligne"], borne((t - suite) / 0.5))
    return image


def _formater_nombre(valeur: float, decimales: int) -> str:
    return f"{valeur:.{decimales}f}".replace(".", ",") if decimales else f"{int(round(valeur))}"


def scene_chiffre(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    accent = ctx.t["accent"]
    image = ctx.fond(accent, t, melanger(accent, (255, 255, 255), 0.25))
    encre = ctx.t["sur_accent"]
    brut = str(sc.get("valeur", "0")).replace(",", ".").replace(" ", "")
    try:
        cible = float(brut)
    except ValueError:
        cible = 0.0
    decimales = len(brut.split(".")[1]) if "." in brut else 0
    courant = cible * sortie_cubique(t / 1.5)
    nombre = _formater_nombre(courant, decimales)
    taille = ctx.px(430 if ctx.format != "horizontal" else 360)
    img_nombre = image_texte(nombre, GRAS, taille, encre)
    suffixe = sc.get("suffixe", "")
    img_suffixe = image_texte(suffixe, GRAS, int(taille * 0.38), encre) if suffixe else None
    largeur_totale = img_nombre.size[0] + (img_suffixe.size[0] if img_suffixe else 0)
    x = int((ctx.l - largeur_totale) / 2)
    y = int(ctx.h * 0.30)
    coller(image, img_nombre, x, y, borne(t / 0.2))
    if img_suffixe:
        coller(image, img_suffixe, x + img_nombre.size[0] - 6, y + int(taille * 0.12), borne((t - 0.3) / 0.3))
    marge = ctx.px(76)
    mots_libelle = re.sub(r"[*_]", "", sc.get("libelle", "")).split()
    bloc = composer(ctx, " ".join(f"_{m}_" for m in mots_libelle), ctx.px(78), ctx.l - 2 * marge, NORMAL)
    dessiner_bloc(ctx, image, bloc, 0, int(ctx.h * 0.30 + taille * 1.3), t, 0.9, 0.07, "centre", ctx.l, encre, encre)
    if sc.get("legende"):
        etiquette(ctx, image, sc["legende"], marge, int(ctx.h * 0.86), encre, 26, borne((t - 1.4) / 0.4))
    return image


def scene_liste(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    titre = composer(ctx, sc.get("titre", ""), ctx.px(92 if ctx.format != "horizontal" else 72), ctx.l - 2 * marge)
    elements = (sc.get("items") or [])[:5]
    hauteur_carte = ctx.px(150 if ctx.format != "horizontal" else 120)
    ecart = ctx.px(28)
    haut = ctx.haut_centre(titre["hauteur"] + ctx.px(60) + len(elements) * (hauteur_carte + ecart))
    dessiner_bloc(ctx, image, titre, marge, haut, t, 0.05)
    y0 = haut + titre["hauteur"] + ctx.px(60)
    for i, texte in enumerate(elements):
        debut = 0.7 + 0.45 * i
        avance = (t - debut) / 0.6
        if avance <= 0:
            continue
        glisse = 1 - sortie_rebond(avance)
        x = marge + int(glisse * ctx.l * 0.9)
        y = y0 + i * (hauteur_carte + ecart)
        largeur = ctx.l - 2 * marge
        calque = Image.new("RGBA", (largeur + 8, hauteur_carte + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(calque)
        d.rounded_rectangle((4, 4, largeur + 4, hauteur_carte + 4), radius=ctx.px(28), fill=ctx.t["carte"] + (255,), outline=melanger(ctx.t["accent"], ctx.t["carte"], 0.45) + (255,), width=2)
        rond = ctx.px(78)
        cx, cy = ctx.px(48) + rond // 2, 4 + hauteur_carte // 2
        d.ellipse((cx - rond // 2, cy - rond // 2, cx + rond // 2, cy + rond // 2), fill=ctx.t["accent"] + (255,))
        trace = borne((t - debut - 0.25) / 0.3)
        points = [(cx - rond * 0.22, cy + rond * 0.02), (cx - rond * 0.06, cy + rond * 0.18), (cx + rond * 0.24, cy - rond * 0.16)]
        if trace > 0:
            if trace < 0.5:
                fin = (points[0][0] + (points[1][0] - points[0][0]) * trace * 2, points[0][1] + (points[1][1] - points[0][1]) * trace * 2)
                d.line([points[0], fin], fill=ctx.t["sur_accent"] + (255,), width=ctx.px(9), joint="curve")
            else:
                reste = (trace - 0.5) * 2
                fin = (points[1][0] + (points[2][0] - points[1][0]) * reste, points[1][1] + (points[2][1] - points[1][1]) * reste)
                d.line([points[0], points[1], fin], fill=ctx.t["sur_accent"] + (255,), width=ctx.px(9), joint="curve")
        texte_img = image_texte(texte, GRAS, ctx.px(50 if len(texte) < 28 else 42), ctx.t["texte"])
        calque.paste(texte_img, (ctx.px(48) + rond + ctx.px(34), 4 + int((hauteur_carte - texte_img.size[1]) / 2)), texte_img)
        coller(image, calque, x - 4, y - 4, borne(avance * 4))
    return image


def scene_etape(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    numero = str(sc.get("numero", "01"))
    img_numero = image_texte(numero, SERIF, ctx.px(330), ctx.t["accent"], True)
    titre = composer(ctx, sc.get("titre", ""), ctx.px(96 if ctx.format != "horizontal" else 76), ctx.l - 2 * marge)
    petit = composer(ctx, sc.get("texte", ""), ctx.px(46), ctx.l - 2 * marge, NORMAL, 1.2)
    y = ctx.haut_centre(img_numero.size[1] + titre["hauteur"] + ctx.px(44) + petit["hauteur"])
    calque_revele(image, img_numero, marge - 10, y, img_numero.size[1] - ctx.px(40), sortie_expo(t / 0.7), 0)
    if sc.get("kicker"):
        etiquette(ctx, image, sc["kicker"], marge + img_numero.size[0] - ctx.px(10), y + int(img_numero.size[1] * 0.72), ctx.t["texte"], 28, borne((t - 0.4) / 0.4))
    y_titre = y + img_numero.size[1] + ctx.px(10)
    dessiner_bloc(ctx, image, titre, marge, y_titre, t, 0.35)
    debut = 0.35 + 0.09 * sum(len(l) for l in titre["lignes"]) + 0.4
    for i, ligne in enumerate(petit["lignes"]):
        texte = " ".join(m for m, _, _, _ in ligne)
        coller(image, image_texte(texte, NORMAL, ctx.px(46), (214, 204, 194)), marge - 10, y_titre + titre["hauteur"] + ctx.px(34) + i * petit["hauteur_ligne"], borne((t - debut - 0.12 * i) / 0.5))
    return image


def _etoile(centre: tuple, rayon: float, rotation: float = -math.pi / 2) -> list:
    points = []
    for i in range(10):
        r = rayon if i % 2 == 0 else rayon * 0.42
        a = rotation + i * math.pi / 5
        points.append((centre[0] + r * math.cos(a), centre[1] + r * math.sin(a)))
    return points


def scene_citation(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    guillemet = image_texte("“", SERIF, ctx.px(420), ctx.t["accent"])
    bloc = composer(ctx, sc.get("texte", ""), ctx.px(76 if ctx.format != "horizontal" else 60), ctx.l - 2 * marge, SERIF, 1.15)
    haut = ctx.haut_centre(ctx.px(300) + bloc["hauteur"] + ctx.px(50) + ctx.px(100) + ctx.px(60))
    calque_revele(image, guillemet, marge - 10, haut, guillemet.size[1] - ctx.px(120), sortie_expo(t / 0.6), 0)
    y0 = haut + ctx.px(300)
    lignes_texte = [" ".join(m for m, _, _, _ in l) for l in bloc["lignes"]]
    for i, texte in enumerate(lignes_texte):
        calque = image_texte(texte, SERIF, ctx.px(76 if ctx.format != "horizontal" else 60), ctx.t["texte"])
        calque_revele(image, calque, marge - 10, y0 + i * bloc["hauteur_ligne"], bloc["hauteur_ligne"], sortie_expo((t - 0.35 - 0.16 * i) / 0.6))
    y_bas = y0 + bloc["hauteur"] + ctx.px(50)
    for i in range(5):
        p = sortie_rebond((t - 1.0 - 0.12 * i) / 0.4)
        if p > 0:
            rayon = ctx.px(34) * max(0.01, p)
            calque = Image.new("RGBA", (ctx.px(90), ctx.px(90)), (0, 0, 0, 0))
            ImageDraw.Draw(calque).polygon(_etoile((ctx.px(45), ctx.px(45)), rayon), fill=ctx.t["accent2"] + (255,))
            coller(image, calque, marge - 10 + i * ctx.px(78), y_bas, borne(p * 3))
    if sc.get("auteur"):
        coller(image, image_texte(sc["auteur"], NORMAL, ctx.px(40), (214, 204, 194)), marge - 10, y_bas + ctx.px(100), borne((t - 1.7) / 0.5))
    return image


def scene_marque(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    accent = ctx.t["accent"]
    image = ctx.fond(ctx.t["fond"], t, accent)
    d = ImageDraw.Draw(image)
    horizontal = ctx.format == "horizontal"
    cx, cy = ctx.l // 2, int(ctx.h * (0.30 if horizontal else 0.34))
    rayon = ctx.px(120 if horizontal else 190) * sortie_expo(t / 0.9)
    # forme qui passe du cercle au carre arrondi, entouree de points en orbite
    arrondi = rayon * (1 - 0.7 * entree_sortie((t - 0.8) / 1.0))
    d.rounded_rectangle((cx - rayon, cy - rayon, cx + rayon, cy + rayon), radius=max(2, arrondi), fill=accent)
    for i in range(12):
        angle = t * 0.9 + i * math.pi / 6
        rr = ctx.px(190 if horizontal else 300) * sortie_expo((t - 0.2) / 0.9)
        px_, py_ = cx + rr * math.cos(angle), cy + rr * math.sin(angle)
        pr = ctx.px(11 if i % 3 else 17)
        d.ellipse((px_ - pr, py_ - pr, px_ + pr, py_ + pr), fill=ctx.t["texte"] if i % 3 else ctx.t["accent2"])
    marge = ctx.px(60)
    nom = sc.get("nom") or ctx.marque
    taille = ctx.px(110 if horizontal else 150)
    y_nom = int(ctx.h * (0.55 if horizontal else 0.60))
    while largeur_texte(nom, GRAS, taille) > ctx.l - 2 * marge and taille > 40:
        taille = int(taille * 0.92)
    image_nom = image_texte(nom, GRAS, taille, ctx.t["texte"])
    calque_revele(image, image_nom, int((ctx.l - image_nom.size[0]) / 2), y_nom, image_nom.size[1], sortie_expo((t - 0.6) / 0.7), 0)
    if sc.get("accroche"):
        img = image_texte(sc["accroche"], SERIF, ctx.px(64), ctx.t["accent"], True)
        calque_revele(image, img, int((ctx.l - img.size[0]) / 2), y_nom + image_nom.size[1] + ctx.px(10), img.size[1], sortie_expo((t - 0.95) / 0.7), 0)
    return image


def scene_cta(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    accent = ctx.t["accent"]
    image = ctx.fond(ctx.t["fond"], t, accent)
    marge = ctx.px(76)
    bloc = composer(ctx, sc.get("texte", ""), ctx.px(118 if ctx.format != "horizontal" else 92), ctx.l - 2 * marge)
    nombre_contacts = len([l for l in (sc.get("contact") or "").split("\n") if l.strip()][:3])
    y0 = ctx.haut_centre(bloc["hauteur"] + ctx.px(70) + ctx.px(220) + nombre_contacts * ctx.px(74))
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.05)
    y_bouton = y0 + bloc["hauteur"] + ctx.px(70)
    p = sortie_rebond((t - 0.9) / 0.5)
    if p > 0:
        texte = sc.get("bouton", "Contactez-nous")
        img_texte = image_texte(texte, GRAS, ctx.px(58), ctx.t["sur_accent"])
        largeur, hauteur = img_texte.size[0] + ctx.px(110), ctx.px(150)
        pulse = 1 + 0.025 * math.sin(t * 5)
        calque = Image.new("RGBA", (largeur + 80, hauteur + 80), (0, 0, 0, 0))
        halo = Image.new("RGBA", calque.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle((40, 40, largeur + 40, hauteur + 40), radius=hauteur // 2, fill=accent + (130,))
        calque = Image.alpha_composite(calque, halo.filter(ImageFilter.GaussianBlur(ctx.px(22))))
        ImageDraw.Draw(calque).rounded_rectangle((40, 40, largeur + 40, hauteur + 40), radius=hauteur // 2, fill=accent + (255,))
        calque.paste(img_texte, (40 + int((largeur - img_texte.size[0]) / 2), 40 + int((hauteur - img_texte.size[1]) / 2)), img_texte)
        s = max(0.05, p) * pulse
        calque = calque.resize((max(1, int(calque.size[0] * s)), max(1, int(calque.size[1] * s))), Image.BILINEAR)
        coller(image, calque, marge - 40 + (largeur + 80 - calque.size[0]) / 2, y_bouton - 40 + (hauteur + 80 - calque.size[1]) / 2, borne(p * 2))
    for i, ligne in enumerate([l for l in (sc.get("contact") or "").split("\n") if l.strip()][:3]):
        coller(image, image_texte(ligne.strip(), NORMAL, ctx.px(48), (220, 210, 200)), marge - 10, y_bouton + ctx.px(220) + i * ctx.px(74), borne((t - 1.3 - 0.15 * i) / 0.5))
    return image


SCENES = {"hook": scene_hook, "chiffre": scene_chiffre, "liste": scene_liste, "etape": scene_etape, "citation": scene_citation, "marque": scene_marque, "cta": scene_cta}


# --------------------------------------------------------------------------- habillage, transitions, assemblage

def habillage(ctx: Contexte, image: Image.Image, avancement: float, numero: int, total: int, sur_accent: bool = False) -> None:
    """Elements constants : nom en haut, barre de progression et compteur en bas (comme les slides)."""
    couleur = ctx.t["sur_accent"] if sur_accent else (190, 180, 170)
    marge = ctx.px(76)
    etiquette(ctx, image, f"× {ctx.marque} ×" if ctx.marque else "", marge, ctx.px(70), ctx.t["sur_accent"] if sur_accent else melanger(ctx.t["accent"], (255, 255, 255), 0.35), 30)
    y = ctx.h - ctx.px(96)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((marge, y, ctx.l - marge, y + 4), radius=2, fill=melanger(couleur, (0, 0, 0), 0.7))
    d.rounded_rectangle((marge, y, marge + (ctx.l - 2 * marge) * borne(avancement), y + 4), radius=2, fill=couleur)
    compteur = image_texte(f"{numero} / {total}", NORMAL, ctx.px(24), couleur, False, 0.08)
    image.paste(compteur.convert("RGB"), (ctx.l - marge - compteur.size[0] + 10, y + ctx.px(16)), compteur.getchannel("A"))


def transition_balayage(ctx: Contexte, a: Image.Image, b: Image.Image, p: float) -> Image.Image:
    """b recouvre a par la gauche, derriere une bande de couleur oblique."""
    biais = int(ctx.l * 0.14)
    avant = -biais + (ctx.l + 2 * biais + int(ctx.l * 0.1)) * sortie_cubique(p) if p < 1 else ctx.l + 2 * biais
    bande = int(ctx.l * 0.09)
    masque = Image.new("L", (ctx.l, ctx.h), 0)
    ImageDraw.Draw(masque).polygon([(0, 0), (avant + biais, 0), (avant, ctx.h), (0, ctx.h)], fill=255)
    sortie = a.copy()
    sortie.paste(b, (0, 0), masque)
    ImageDraw.Draw(sortie).polygon([(avant + biais, 0), (avant + biais + bande, 0), (avant + bande, ctx.h), (avant, ctx.h)], fill=ctx.t["accent2"])
    ImageDraw.Draw(sortie).polygon([(avant + biais + bande, 0), (avant + biais + 2 * bande, 0), (avant + 2 * bande, ctx.h), (avant + bande, ctx.h)], fill=ctx.t["accent"])
    return sortie


def nettoyer_scenario(scenario: dict) -> list:
    """Scenes valides uniquement (type connu, textes courts) : jamais d'erreur sur un scenario ecrit par l'IA."""
    scenes = []
    for brut in (scenario or {}).get("scenes") or []:
        if not isinstance(brut, dict) or brut.get("type") not in TYPES_SCENES:
            continue
        scene = {"type": brut["type"], "duree": max(1.8, min(float(brut.get("duree") or 3.0), 6.0))}
        for cle in ("texte", "sous_texte", "valeur", "suffixe", "libelle", "legende", "titre", "numero", "kicker", "auteur", "nom", "accroche", "bouton", "contact"):
            if brut.get(cle) is not None:
                scene[cle] = str(brut[cle]).replace("—", "-").strip()[:180]
        if isinstance(brut.get("items"), list):
            scene["items"] = [str(x).replace("—", "-").strip()[:80] for x in brut["items"] if str(x).strip()][:5]
        scenes.append(scene)
    return scenes[:12]


def minutage(scenes: list) -> list:
    """[(debut, fin)] de chaque scene sur la chronologie de la video ; deux scenes se chevauchent pendant la transition."""
    positions, debut = [], 0.0
    for scene in scenes:
        positions.append((debut, debut + scene["duree"]))
        debut += scene["duree"] - DUREE_TRANSITION
    return positions


def duree_totale(scenes: list) -> float:
    return minutage(scenes)[-1][1] if scenes else 0.0


def evenements_sons(scenes: list) -> list:
    """[(instant, nom du son)] : impact a l'accroche, whoosh a chaque transition, pop sur les elements, ding sur les etoiles."""
    positions = minutage(scenes)
    evenements = []
    for i, (scene, (debut, _)) in enumerate(zip(scenes, positions)):
        evenements.append((max(0.0, debut + (0.05 if i == 0 else DUREE_TRANSITION * 0.4)), "impact" if i == 0 else "whoosh"))
        if scene["type"] == "liste":
            evenements += [(debut + 0.7 + 0.45 * k + 0.1, "pop") for k in range(len(scene.get("items", [])))]
        if scene["type"] in ("chiffre", "etape", "cta", "marque"):
            evenements.append((debut + 0.85, "pop"))
        if scene["type"] == "citation":
            evenements.append((debut + 1.05, "ding"))
    brut = sorted(evenements)
    gardes, dernier = [], -1.0
    for t, nom in brut:
        if t - dernier >= 0.2:
            gardes.append((round(t, 2), nom))
            dernier = t
    return gardes


VOLUMES = {"whoosh": 0.5, "pop": 0.7, "impact": 0.65, "ding": 0.45}


def scene_a(ctx: Contexte, scenes: list, positions: list, i: int, t: float) -> Image.Image:
    """Scene i a l'instant t de la video, avec un leger zoom de camera continu."""
    debut, fin = positions[i]
    image = SCENES[scenes[i]["type"]](ctx, scenes[i], t - debut)
    zoom = 1.0 + 0.045 * borne((t - debut) / (fin - debut))
    if zoom > 1.0005:
        l, h = int(ctx.l * zoom), int(ctx.h * zoom)
        image = image.resize((l, h), Image.BILINEAR).crop(((l - ctx.l) // 2, (h - ctx.h) // 2, (l - ctx.l) // 2 + ctx.l, (h - ctx.h) // 2 + ctx.h))
    return image


def image_a(ctx: Contexte, scenes: list, positions: list, t: float) -> Image.Image:
    """Image de la video a l'instant t."""
    n = len(scenes)
    courante = max(i for i, (debut, _) in enumerate(positions) if debut <= t + 1e-9) if t >= 0 else 0
    debut, fin = positions[courante]
    image = scene_a(ctx, scenes, positions, courante, t)
    if courante > 0:
        precedent_debut, precedent_fin = positions[courante - 1]
        if t < precedent_fin:
            avant = scene_a(ctx, scenes, positions, courante - 1, t)
            image = transition_balayage(ctx, avant, image, (t - debut) / DUREE_TRANSITION)
    habillage(ctx, image, t / max(0.1, positions[-1][1]), courante + 1, n, scenes[courante]["type"] == "chiffre" and (t - debut) > DUREE_TRANSITION)
    return image


def rendre(scenario: dict, charte: dict, format_video: str, sortie: str, marque: str = "", ville: str = "", progression=None, musique: str = None) -> float:
    """Rend la video MP4 (image par image vers ffmpeg, bruitages inclus). Renvoie sa duree en secondes."""
    if format_video not in FORMATS:
        raise ErreurMotion("Format inconnu.")
    scenes = nettoyer_scenario(scenario)
    if not scenes:
        raise ErreurMotion("Le scenario ne contient aucune scene valide.")
    ctx = Contexte(format_video, charte, marque, ville)
    positions = minutage(scenes)
    duree = positions[-1][1]
    total_images = int(duree * FPS)
    evenements = evenements_sons(scenes)
    commande = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ctx.l}x{ctx.h}", "-r", str(FPS), "-i", "-"]
    fichiers = sorted({sons.chemin_son(nom) for _, nom in evenements})
    for fichier in fichiers:
        commande += ["-i", fichier]
    parties, etiquettes = [], []
    for k, (t, nom) in enumerate(evenements):
        indice = 1 + fichiers.index(sons.chemin_son(nom))
        ms = int(max(0.0, t) * 1000)
        parties.append(f"[{indice}:a]aformat=channel_layouts=stereo,volume={VOLUMES[nom]:.2f},adelay={ms}|{ms}[s{k}]")
        etiquettes.append(f"[s{k}]")
    parties.append("".join(etiquettes) + f"amix=inputs={len(etiquettes)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={duree:.2f}[a]")
    commande += ["-filter_complex", ";".join(parties), "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k", "-t", f"{duree:.2f}", "-movflags", "+faststart", sortie]
    processus = subprocess.Popen(commande, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    erreurs = []
    lecteur = threading.Thread(target=lambda: erreurs.append(processus.stderr.read()), daemon=True)
    lecteur.start()
    try:
        for i in range(total_images):
            processus.stdin.write(image_a(ctx, scenes, positions, i / FPS).tobytes())
            if progression:
                progression((i + 1) / total_images)
        processus.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    processus.wait()
    lecteur.join(timeout=5)
    if processus.returncode != 0 or not os.path.exists(sortie):
        message = (erreurs[0].decode("utf-8", "replace") if erreurs else "")[-300:]
        raise ErreurMotion(message or "ffmpeg a echoue.")
    return duree


# --------------------------------------------------------------------------- rendus en tache de fond

ETATS = {}
_VERROU = threading.Lock()
DUREE_CONSERVATION = 3 * 3600


def _purger() -> None:
    maintenant = time.time()
    for identifiant in [i for i, e in ETATS.items() if e["etat"] != "en_cours" and maintenant - e["cree"] > DUREE_CONSERVATION]:
        etat = ETATS.pop(identifiant)
        if etat.get("chemin") and os.path.exists(etat["chemin"]):
            try:
                os.remove(etat["chemin"])
            except OSError:
                pass


def demarrer(scenario: dict, charte: dict, format_video: str, marque: str = "", ville: str = "") -> str:
    """Lance le rendu en tache de fond (un seul a la fois : il occupe le processeur) et renvoie son identifiant."""
    import tempfile
    with _VERROU:
        _purger()
        if any(e["etat"] == "en_cours" for e in ETATS.values()):
            raise ErreurMotion("Une video est deja en cours de generation : attendez qu'elle soit terminee.")
        identifiant = uuid.uuid4().hex[:12]
        etat = {"etat": "en_cours", "progression": 0.0, "erreur": "", "chemin": "", "duree": 0.0, "cree": time.time()}
        ETATS[identifiant] = etat
    dossier = tempfile.mkdtemp(prefix="fiche_locale_motion_")

    def travail():
        try:
            chemin = os.path.join(dossier, "video.mp4")
            duree = rendre(scenario, charte, format_video, chemin, marque, ville, lambda p: etat.__setitem__("progression", round(p, 3)))
            etat.update(etat="termine", chemin=chemin, duree=round(duree, 1), progression=1.0)
        except Exception as erreur:
            etat.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
    return identifiant
