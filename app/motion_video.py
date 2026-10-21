"""
Videos de motion design creees entierement par code, sans aucun tournage : un scenario (liste de scenes) devient une video MP4 de 15 a 25 s,
aux couleurs et aux polices du client, avec transitions, bruitages et barre de progression.

Chaque scene est dessinee image par image (Pillow) puis envoyee a ffmpeg : accroche en typographie animee, chiffre qui defile, liste qui
glisse, etapes, citation, marque, appel a l'action. Aucun logiciel de montage, aucune dependance en plus.

Scenario : {"scenes": [{"type": "hook", "texte": "Votre *fiche* est _invisible_", "sous_texte": "..."}, ...]} ; dans un texte,
*mot* est surligne sur une pastille de couleur et _mot_ passe en serif italique.
"""

import functools
import io
import math
import os
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import sons
from .montage_video import FFMPEG

DOSSIER_POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "polices")
FPS = 30
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080), "horizontal": (1920, 1080)}
DUREE_TRANSITION = 0.45
TYPES_SCENES = ("hook", "chiffre", "liste", "etape", "citation", "marque", "cta", "photo", "mosaique", "avant_apres", "realisation")
HOTES_IMAGES = (".googleusercontent.com", ".ggpht.com", ".google.com")
TAILLE_MAX_IMAGE = 8 * 1024 * 1024
GRAS, NORMAL, SERIF = "Poppins-Bold.ttf", "Poppins-Regular.ttf", "DMSerifDisplay-Regular.ttf"
CREME = (250, 244, 235)


class ErreurMotion(Exception):
    pass


# --------------------------------------------------------------------------- images du client (photos de la fiche, logo, imports)

def ouvrir_image(octets: bytes, transparente: bool = False) -> Image.Image:
    """Image lue depuis des octets (JPEG, PNG, WebP...), reduite a 1800 px au plus ; transparente=True garde la transparence (logo)."""
    try:
        image = Image.open(io.BytesIO(octets))
        image = image.convert("RGBA" if transparente else "RGB")
    except Exception as erreur:
        raise ErreurMotion("Image illisible.") from erreur
    image.thumbnail((1800, 1800))
    return image


def telecharger_image(url: str, transparente: bool = False, confiance: bool = False) -> Image.Image:
    """
    Photo d'une fiche Google : seules les adresses Google sont acceptees (pas de requete vers un site quelconque), sauf `confiance`
    (adresse lue en base par le serveur lui-meme, comme le logo enregistre d'un client).
    """
    analyse = urlparse(url or "")
    hote = (analyse.hostname or "").lower()
    if analyse.scheme != "https" or not (confiance or any(hote.endswith(suffixe) for suffixe in HOTES_IMAGES)):
        raise ErreurMotion("Adresse d'image non autorisee.")
    adresse = (re.sub(r"=[swh][0-9a-z-]*$", "", url) + "=s1600") if any(hote.endswith(suffixe) for suffixe in HOTES_IMAGES) else url
    try:
        reponse = requests.get(adresse, timeout=25, stream=True)
        reponse.raise_for_status()
        octets = b""
        for morceau in reponse.iter_content(65536):
            octets += morceau
            if len(octets) > TAILLE_MAX_IMAGE:
                break
    except requests.RequestException as erreur:
        raise ErreurMotion("Photo introuvable.") from erreur
    if len(octets) > TAILLE_MAX_IMAGE:
        raise ErreurMotion("Photo trop lourde.")
    return ouvrir_image(octets, transparente)


def recadrer(image: Image.Image, l: int, h: int, zoom: float = 1.0, cx: float = 0.5, cy: float = 0.5) -> Image.Image:
    """Remplit un cadre l x h avec l'image (recadrage « cover »), avec un zoom et un point de focale (fractions de 0 a 1)."""
    iw, ih = image.size
    echelle = max(l / iw, h / ih) * max(1.0, zoom)
    fw, fh = l / echelle, h / echelle
    fw, fh = min(fw, iw), min(fh, ih)                       # jamais plus grand que l'image (arrondis de calcul)
    x0, y0 = max(0.0, (iw - fw) * borne(cx)), max(0.0, (ih - fh) * borne(cy))
    return image.resize((l, h), Image.BILINEAR, box=(x0, y0, min(iw, x0 + fw), min(ih, y0 + fh)))


def carte_photo(image: Image.Image, l: int, h: int, rayon: int, zoom: float = 1.0) -> Image.Image:
    """Photo recadree dans un cadre aux coins arrondis, avec un fin liseré clair (RGBA)."""
    carte = recadrer(image, l, h, zoom).convert("RGBA")
    masque = Image.new("L", (l, h), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, l - 1, h - 1), radius=rayon, fill=255)
    carte.putalpha(masque)
    ImageDraw.Draw(carte).rounded_rectangle((0, 0, l - 1, h - 1), radius=rayon, outline=(255, 255, 255, 110), width=2)
    return carte


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
        "accent_texte": principale if luminance(principale) > 0.55 else melanger(principale, (255, 255, 255), 0.45),
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


def sans_balises(texte: str) -> str:
    """Texte sans les marques *surlignage* et _serif_ (pour les champs qui ne les gerent pas)."""
    return re.sub(r"[*_]", "", texte or "")


def bloc_texte(texte: str, nom: str, taille: int, largeur_max: float, hauteur_max: float, couleur: tuple, italique: bool = False, centre: bool = False,
               interligne: float = 1.15) -> Image.Image:
    """
    Texte qui tient TOUJOURS dans une boite : passage a la ligne et, au besoin, taille reduite (jusqu'a 55 %). Renvoie un calque RGBA de la largeur de
    la boite (+ marge de 20 px). Les textes ecrits par l'IA ne sont jamais coupes ni ne debordent de l'ecran.
    """
    texte = sans_balises(texte)
    courante_taille = taille
    while True:
        lignes, ligne = [], ""
        for mot in texte.split():
            essai = (ligne + " " + mot).strip()
            if ligne and largeur_texte(essai, nom, courante_taille) > largeur_max:
                lignes.append(ligne)
                ligne = mot
            else:
                ligne = essai
        if ligne:
            lignes.append(ligne)
        hauteur_ligne = int(courante_taille * interligne * 1.18)
        tient = len(lignes) * hauteur_ligne <= hauteur_max and all(largeur_texte(l, nom, courante_taille) <= largeur_max for l in lignes)
        if tient or courante_taille <= taille * 0.55:
            break
        courante_taille = int(courante_taille * 0.92)
    calque = Image.new("RGBA", (int(largeur_max) + 20, max(1, len(lignes)) * hauteur_ligne + 20), (0, 0, 0, 0))
    for i, texte_ligne in enumerate(lignes):
        img = image_texte(texte_ligne, nom, courante_taille, couleur, italique)
        x = int((calque.size[0] - img.size[0]) / 2) if centre else 0
        calque.paste(img, (x, i * hauteur_ligne), img)
    return calque


# --------------------------------------------------------------------------- contexte de rendu

class Contexte:
    def __init__(self, format_video: str, charte: dict, marque: str = "", ville: str = "", images: list = None, logo: Image.Image = None):
        self.l, self.h = FORMATS[format_video]
        self.format = format_video
        self.u = min(self.l, self.h) / 1080            # echelle : les tailles ci-dessous sont donnees pour 1080 px
        self.t = theme(charte)
        self.marque, self.ville = marque, ville
        self.images, self.logo = list(images or []), logo
        self._fonds = {}
        self._cartes = {}
        self._degrade = None
        self._glows = {}

    def px(self, valeur: float) -> int:
        return int(round(valeur * self.u))

    def photo(self, numero) -> Image.Image:
        """Photo n (a partir de 1), ou None si elle n'existe pas."""
        try:
            return self.images[int(numero) - 1]
        except (TypeError, ValueError, IndexError):
            return None

    def carte(self, numero, l: int, h: int, rayon: int) -> Image.Image:
        cle = (int(numero), l, h, rayon)
        if cle not in self._cartes:
            self._cartes[cle] = carte_photo(self.photo(numero), l, h, rayon)
        return self._cartes[cle]

    def degrade_bas(self) -> Image.Image:
        """Voile sombre qui monte du bas de l'image (pour lire le texte sur une photo)."""
        if self._degrade is None:
            masque = Image.new("L", (1, self.h))
            masque.putdata([int(235 * borne((y / self.h - 0.28) / 0.6) ** 1.4) for y in range(self.h)])
            self._degrade = masque.resize((self.l, self.h))
        return self._degrade

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

def decouper_segments(texte: str) -> list:
    """
    « Votre *fiche Google* est d'_Agen_, la ville » -> [(mot, style, colle)] ; style : "n", "pastille" (*...*) ou "serif" (_..._) ; colle=True quand
    le segment suit le precedent SANS espace (d'*Ath*, *Ath*,) : il reste attache a lui, sur la meme ligne. Un marqueur isole est ignore.
    """
    segments, precedent_fini_par_espace = [], True
    for morceau in re.split(r"(\*[^*\n]+\*|_[^_\n]+_)", texte or ""):
        if not morceau:
            continue
        if len(morceau) > 2 and morceau[0] == "*" and morceau[-1] == "*":
            style, mots = "pastille", [morceau[1:-1].strip()]
        elif len(morceau) > 2 and morceau[0] == "_" and morceau[-1] == "_":
            style, mots = "serif", morceau[1:-1].split()
        else:
            style, mots = "n", sans_balises(morceau).split()
        for i, mot in enumerate(mots):
            if mot:
                segments.append((mot, style, i == 0 and bool(segments) and not morceau[0].isspace() and not precedent_fini_par_espace))
        precedent_fini_par_espace = morceau[-1].isspace() if mots else True
    return segments


def decouper_texte(texte: str) -> list:
    """« Votre *fiche Google* est _invisible_ » -> [("Votre", "n"), ("fiche Google", "pastille"), ("est", "n"), ("invisible", "serif")]."""
    return [(mot, style) for mot, style, _ in decouper_segments(texte)]


def composer(ctx: Contexte, texte: str, taille: int, largeur_max: float, nom: str = GRAS, interligne: float = 1.12) -> dict:
    """Place les mots sur des lignes : {"lignes": [[(mot, style, x, largeur)]], "hauteur_ligne", "hauteur"}. Les mots surlignes forment un seul bloc."""
    groupes = []
    for mot, style, colle in decouper_segments(texte):
        if colle and groupes:
            groupes[-1].append((mot, style))
        else:
            groupes.append([(mot, style)])
    espace = largeur_texte(" ", nom, taille) * 1.05
    lignes, courante, x = [], [], 0.0
    for groupe in groupes:
        largeurs = []
        for mot, style in groupe:
            police_mot = SERIF if style == "serif" else nom
            taille_mot = int(taille * 1.12) if style == "serif" else taille
            largeurs.append(largeur_texte(mot, police_mot, taille_mot) + (0.5 * taille if style == "pastille" else 0))
        if courante and x + sum(largeurs) > largeur_max:
            lignes.append(courante)
            courante, x = [], 0.0
        for (mot, style), w in zip(groupe, largeurs):
            courante.append((mot, style, x, w))
            x += w
        x += espace
    if courante:
        lignes.append(courante)
    hauteur_ligne = int(taille * interligne * 1.18)
    return {"lignes": lignes, "hauteur_ligne": hauteur_ligne, "hauteur": hauteur_ligne * len(lignes), "taille": taille, "nom": nom}


def dessiner_bloc(ctx: Contexte, image: Image.Image, bloc: dict, x0: int, y0: int, t: float, debut: float = 0.0, pas: float = 0.09, alignement: str = "gauche",
                  largeur_zone: float = 0.0, couleur: tuple = None, couleur_accent: tuple = None, couleur_serif: tuple = None) -> int:
    """Dessine un bloc compose, mot par mot (montee derriere un masque, pastille qui rebondit). Renvoie l'indice du dernier mot."""
    taille, nom, hauteur_ligne = bloc["taille"], bloc["nom"], bloc["hauteur_ligne"]
    couleur = couleur or ctx.t["texte"]
    couleur_accent = couleur_accent or ctx.t["accent"]
    couleur_serif = couleur_serif or ctx.t["accent_texte"]
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
                    calque = image_texte(mot, SERIF, int(taille * 1.12), couleur_serif, True)
                else:
                    calque = image_texte(mot, nom, taille, couleur)
                calque_revele(image, calque, xm - 10, y, hauteur_ligne, sortie_expo(avance))
    return rang


def echelle_pour(ctx: Contexte, mesure, zone: float = None, minimum: float = 0.5) -> float:
    """Plus grande echelle (<= 1) pour laquelle le contenu (hauteur donnee par mesure(e)) tient dans la zone utile de l'ecran."""
    zone = zone or ctx.h * 0.73
    e = 1.0
    while e > minimum:
        if mesure(e) <= zone:
            return e
        e *= 0.92
    return minimum


def etiquette(ctx: Contexte, image: Image.Image, texte: str, x: int, y: int, couleur: tuple, taille: int = 26, opacite: float = 1.0) -> None:
    """Petite etiquette en majuscules espacees (style « kicker »), reduite si elle depasserait de l'ecran."""
    texte = sans_balises(texte).upper()
    taille_px = ctx.px(taille)
    largeur_dispo = ctx.l - x - ctx.px(76)
    while taille_px > 12 and sum(police(NORMAL, taille_px).getlength(c) + 0.16 * taille_px for c in texte) > largeur_dispo:
        taille_px -= 1
    coller(image, image_texte(texte, NORMAL, taille_px, couleur, False, 0.16), x - 10, y - 10, opacite)


# --------------------------------------------------------------------------- scenes

def scene_hook(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    base = 122 if ctx.format != "horizontal" else 96

    def blocs(e):
        titre = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge)
        petit = composer(ctx, sc["sous_texte"], ctx.px(44 * e), ctx.l - 2 * marge, NORMAL, 1.15) if sc.get("sous_texte") else None
        return titre, petit

    def mesure(e):
        titre, petit = blocs(e)
        return titre["hauteur"] + (ctx.px(36 * e) + petit["hauteur"] if petit else 0)

    e = echelle_pour(ctx, mesure)
    bloc, petit = blocs(e)
    y0 = ctx.haut_centre(mesure(e), -0.01)
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.10)
    if petit:
        suite = 0.1 + 0.09 * sum(len(l) for l in bloc["lignes"]) + 0.5
        for i, ligne in enumerate(petit["lignes"]):
            texte = " ".join(m for m, _, _, _ in ligne)
            coller(image, image_texte(texte, NORMAL, ctx.px(44 * e), (210, 200, 190)), marge - 10, y0 + bloc["hauteur"] + ctx.px(36 * e) + i * petit["hauteur_ligne"], borne((t - suite) / 0.5))
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
    marge = ctx.px(76)
    cible_texte = _formater_nombre(cible, decimales)
    mots_libelle = re.sub(r"[*_]", "", sc.get("libelle", "")).split()

    def mesures(e):
        taille = min(ctx.px((430 if ctx.format != "horizontal" else 360) * e), int(ctx.h * 0.26))
        while taille > 60 and largeur_texte(cible_texte, GRAS, taille) + largeur_texte(sc.get("suffixe", ""), GRAS, int(taille * 0.38)) > ctx.l * 0.88:
            taille = int(taille * 0.9)
        libelle = composer(ctx, " ".join(f"_{m}_" for m in mots_libelle), ctx.px(78 * e), ctx.l - 2 * marge, NORMAL)
        return taille, libelle

    e = echelle_pour(ctx, lambda e: mesures(e)[0] * 1.3 + mesures(e)[1]["hauteur"])
    taille, bloc = mesures(e)
    haut = ctx.haut_centre(taille * 1.3 + bloc["hauteur"])
    nombre = _formater_nombre(cible * sortie_cubique(t / 1.5), decimales)
    img_nombre = image_texte(nombre, GRAS, taille, encre)
    suffixe = sc.get("suffixe", "")
    img_suffixe = image_texte(suffixe, GRAS, int(taille * 0.38), encre) if suffixe else None
    largeur_totale = image_texte(cible_texte, GRAS, taille, encre).size[0] + (img_suffixe.size[0] if img_suffixe else 0)
    x = int((ctx.l - largeur_totale) / 2)
    coller(image, img_nombre, x, haut, borne(t / 0.2))
    if img_suffixe:
        coller(image, img_suffixe, x + image_texte(cible_texte, GRAS, taille, encre).size[0] - 6, haut + int(taille * 0.12), borne((t - 0.3) / 0.3))
    dessiner_bloc(ctx, image, bloc, 0, int(haut + taille * 1.3), t, 0.9, 0.07, "centre", ctx.l, encre, encre, encre)
    if sc.get("legende"):
        etiquette(ctx, image, sc["legende"], marge, int(ctx.h * 0.88), encre, 26, borne((t - 1.4) / 0.4))
    return image


def scene_liste(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    elements = (sc.get("items") or [])[:5]
    base_carte = 150 if ctx.format != "horizontal" else 120

    def titre_pour(e):
        return composer(ctx, sc.get("titre", ""), ctx.px((92 if ctx.format != "horizontal" else 72) * e), ctx.l - 2 * marge)

    def mesure(e):
        return titre_pour(e)["hauteur"] + ctx.px(60 * e) + len(elements) * (ctx.px(base_carte * e) + ctx.px(28 * e))

    e = echelle_pour(ctx, mesure)
    titre = titre_pour(e)
    hauteur_carte, ecart = ctx.px(base_carte * e), ctx.px(28 * e)
    haut = ctx.haut_centre(mesure(e))
    dessiner_bloc(ctx, image, titre, marge, haut, t, 0.05)
    y0 = haut + titre["hauteur"] + ctx.px(60 * e)
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
        d.rounded_rectangle((4, 4, largeur + 4, hauteur_carte + 4), radius=ctx.px(28 * e), fill=ctx.t["carte"] + (255,), outline=melanger(ctx.t["accent"], ctx.t["carte"], 0.45) + (255,), width=2)
        rond = ctx.px(78 * e)
        cx, cy = ctx.px(48 * e) + rond // 2, 4 + hauteur_carte // 2
        d.ellipse((cx - rond // 2, cy - rond // 2, cx + rond // 2, cy + rond // 2), fill=ctx.t["accent"] + (255,))
        trace = borne((t - debut - 0.25) / 0.3)
        points = [(cx - rond * 0.22, cy + rond * 0.02), (cx - rond * 0.06, cy + rond * 0.18), (cx + rond * 0.24, cy - rond * 0.16)]
        if trace > 0:
            if trace < 0.5:
                fin = (points[0][0] + (points[1][0] - points[0][0]) * trace * 2, points[0][1] + (points[1][1] - points[0][1]) * trace * 2)
                d.line([points[0], fin], fill=ctx.t["sur_accent"] + (255,), width=max(2, ctx.px(9 * e)), joint="curve")
            else:
                reste = (trace - 0.5) * 2
                fin = (points[1][0] + (points[2][0] - points[1][0]) * reste, points[1][1] + (points[2][1] - points[1][1]) * reste)
                d.line([points[0], points[1], fin], fill=ctx.t["sur_accent"] + (255,), width=max(2, ctx.px(9 * e)), joint="curve")
        x_texte = ctx.px(48 * e) + rond + ctx.px(34 * e)
        texte_img = bloc_texte(texte, GRAS, ctx.px(50 * e), largeur - x_texte - ctx.px(40), hauteur_carte - ctx.px(30 * e), ctx.t["texte"])
        calque.paste(texte_img, (x_texte, 4 + int((hauteur_carte - texte_img.size[1]) / 2)), texte_img)
        coller(image, calque, x - 4, y - 4, borne(avance * 4))
    return image


def scene_etape(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    numero = str(sc.get("numero", "01"))

    def elements(e):
        img_numero = image_texte(numero, SERIF, ctx.px(330 * e), ctx.t["accent_texte"], True)
        titre = composer(ctx, sc.get("titre", ""), ctx.px((96 if ctx.format != "horizontal" else 76) * e), ctx.l - 2 * marge)
        petit = composer(ctx, sc.get("texte", ""), ctx.px(46 * e), ctx.l - 2 * marge, NORMAL, 1.2)
        return img_numero, titre, petit

    def mesure(e):
        img_numero, titre, petit = elements(e)
        return img_numero.size[1] + titre["hauteur"] + ctx.px(44 * e) + petit["hauteur"]

    e = echelle_pour(ctx, mesure)
    img_numero, titre, petit = elements(e)
    y = ctx.haut_centre(mesure(e))
    calque_revele(image, img_numero, marge - 10, y, img_numero.size[1] - ctx.px(40 * e), sortie_expo(t / 0.7), 0)
    if sc.get("kicker"):
        etiquette(ctx, image, sc["kicker"], marge + img_numero.size[0] - ctx.px(10), y + int(img_numero.size[1] * 0.72), ctx.t["texte"], 28, borne((t - 0.4) / 0.4))
    y_titre = y + img_numero.size[1] + ctx.px(10 * e)
    dessiner_bloc(ctx, image, titre, marge, y_titre, t, 0.35)
    debut = 0.35 + 0.09 * sum(len(l) for l in titre["lignes"]) + 0.4
    for i, ligne in enumerate(petit["lignes"]):
        texte = " ".join(m for m, _, _, _ in ligne)
        coller(image, image_texte(texte, NORMAL, ctx.px(46 * e), (214, 204, 194)), marge - 10, y_titre + titre["hauteur"] + ctx.px(34 * e) + i * petit["hauteur_ligne"], borne((t - debut - 0.12 * i) / 0.5))
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
    base = 76 if ctx.format != "horizontal" else 60

    def mesure(e):
        bloc = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge, SERIF, 1.15)
        return ctx.px(300 * e) + bloc["hauteur"] + ctx.px(210 * e)

    e = echelle_pour(ctx, mesure)
    guillemet = image_texte(chr(0x201C), SERIF, ctx.px(420 * e), ctx.t["accent_texte"])
    bloc = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge, SERIF, 1.15)
    haut = ctx.haut_centre(mesure(e))
    calque_revele(image, guillemet, marge - 10, haut, guillemet.size[1] - ctx.px(120 * e), sortie_expo(t / 0.6), 0)
    y0 = haut + ctx.px(300 * e)
    lignes_texte = [" ".join(m for m, _, _, _ in l) for l in bloc["lignes"]]
    for i, texte in enumerate(lignes_texte):
        calque = image_texte(texte, SERIF, ctx.px(base * e), ctx.t["texte"])
        calque_revele(image, calque, marge - 10, y0 + i * bloc["hauteur_ligne"], bloc["hauteur_ligne"], sortie_expo((t - 0.35 - 0.16 * i) / 0.6))
    y_bas = y0 + bloc["hauteur"] + ctx.px(50 * e)
    for i in range(5):
        p = sortie_rebond((t - 1.0 - 0.12 * i) / 0.4)
        if p > 0:
            rayon = ctx.px(34 * e) * max(0.01, p)
            calque = Image.new("RGBA", (ctx.px(90 * e), ctx.px(90 * e)), (0, 0, 0, 0))
            ImageDraw.Draw(calque).polygon(_etoile((ctx.px(45 * e), ctx.px(45 * e)), rayon), fill=ctx.t["accent2"] + (255,))
            coller(image, calque, marge - 10 + i * ctx.px(78 * e), y_bas, borne(p * 3))
    if sc.get("auteur"):
        coller(image, bloc_texte(sc["auteur"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(110 * e), (214, 204, 194)), marge - 10, y_bas + ctx.px(100 * e), borne((t - 1.7) / 0.5))
    return image


def scene_marque(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    accent = ctx.t["accent"]
    image = ctx.fond(ctx.t["fond"], t, accent)
    d = ImageDraw.Draw(image)
    horizontal = ctx.format != "vertical"          # carre et horizontal : mise en page compacte
    cx, cy = ctx.l // 2, int(ctx.h * (0.30 if horizontal else 0.34))
    rayon = ctx.px(120 if horizontal else 190) * sortie_expo(t / 0.9)
    # forme qui passe du cercle au carre arrondi, entouree de points en orbite
    arrondi = rayon * (1 - 0.7 * entree_sortie((t - 0.8) / 1.0))
    d.rounded_rectangle((cx - rayon, cy - rayon, cx + rayon, cy + rayon), radius=max(2, arrondi), fill=(255, 255, 255) if ctx.logo is not None else accent)
    if ctx.logo is not None and rayon > 10:
        interieur = int(rayon * 1.5)
        logo = ctx.logo.copy()
        logo.thumbnail((interieur, interieur))
        image.paste(logo.convert("RGB"), (int(cx - logo.size[0] / 2), int(cy - logo.size[1] / 2)), logo.getchannel("A") if logo.mode == "RGBA" else None)
    for i in range(12):
        angle = t * 0.9 + i * math.pi / 6
        rr = ctx.px(190 if horizontal else 300) * sortie_expo((t - 0.2) / 0.9)
        px_, py_ = cx + rr * math.cos(angle), cy + rr * math.sin(angle)
        pr = ctx.px(11 if i % 3 else 17)
        d.ellipse((px_ - pr, py_ - pr, px_ + pr, py_ + pr), fill=ctx.t["texte"] if i % 3 else ctx.t["accent2"])
    marge = ctx.px(60)
    nom = sans_balises(sc.get("nom") or ctx.marque)
    y_nom = int(ctx.h * (0.55 if horizontal else 0.60))
    image_nom = bloc_texte(nom, GRAS, ctx.px(110 if horizontal else 150), ctx.l - 2 * marge, ctx.px(250 if horizontal else 340), ctx.t["texte"], False, True, 1.0)
    calque_revele(image, image_nom, int((ctx.l - image_nom.size[0]) / 2), y_nom, image_nom.size[1], sortie_expo((t - 0.6) / 0.7), 0)
    if sc.get("accroche"):
        img = bloc_texte(sc["accroche"], SERIF, ctx.px(64), ctx.l - 2 * marge, ctx.px(230), ctx.t["accent_texte"], True, True)
        calque_revele(image, img, int((ctx.l - img.size[0]) / 2), y_nom + image_nom.size[1] + ctx.px(10), img.size[1], sortie_expo((t - 0.95) / 0.7), 0)
    return image


def scene_cta(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    accent = ctx.t["accent"]
    image = ctx.fond(ctx.t["fond"], t, accent)
    marge = ctx.px(76)
    contacts = [l for l in (sc.get("contact") or "").split(chr(10)) if l.strip()][:3]
    base = 118 if ctx.format != "horizontal" else 92

    def mesure(e):
        bloc = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge)
        return bloc["hauteur"] + ctx.px(70 * e) + ctx.px(220 * e) + len(contacts) * ctx.px(74 * e)

    e = echelle_pour(ctx, mesure)
    bloc = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge)
    y0 = ctx.haut_centre(mesure(e))
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.05)
    y_bouton = y0 + bloc["hauteur"] + ctx.px(70 * e)
    p = sortie_rebond((t - 0.9) / 0.5)
    if p > 0:
        texte = sc.get("bouton", "Contactez-nous")
        img_texte = bloc_texte(texte, GRAS, ctx.px(58 * e), ctx.l - 2 * marge - ctx.px(130), ctx.px(130 * e), ctx.t["sur_accent"], False, True)
        largeur, hauteur = img_texte.size[0] + ctx.px(90 * e), max(ctx.px(150 * e), img_texte.size[1] + ctx.px(50 * e))
        pulse = 1 + 0.025 * math.sin(t * 5)
        calque = Image.new("RGBA", (largeur + 80, hauteur + 80), (0, 0, 0, 0))
        halo = Image.new("RGBA", calque.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle((40, 40, largeur + 40, hauteur + 40), radius=hauteur // 2, fill=accent + (130,))
        calque = Image.alpha_composite(calque, halo.filter(ImageFilter.GaussianBlur(ctx.px(22))))
        ImageDraw.Draw(calque).rounded_rectangle((40, 40, largeur + 40, hauteur + 40), radius=hauteur // 2, fill=accent + (255,))
        calque.paste(img_texte, (40 + int((largeur - img_texte.size[0]) / 2), 40 + int((hauteur - img_texte.size[1]) / 2)), img_texte)
        s_ = max(0.05, p) * pulse
        calque = calque.resize((max(1, int(calque.size[0] * s_)), max(1, int(calque.size[1] * s_))), Image.BILINEAR)
        coller(image, calque, marge - 40 + (largeur + 80 - calque.size[0]) / 2, y_bouton - 40 + (hauteur + 80 - calque.size[1]) / 2, borne(p * 2))
    for i, ligne in enumerate(contacts):
        coller(image, bloc_texte(ligne.strip(), NORMAL, ctx.px(48 * e), ctx.l - 2 * marge, ctx.px(72 * e), (220, 210, 200)), marge - 10, y_bouton + ctx.px(220 * e) + i * ctx.px(74 * e), borne((t - 1.3 - 0.15 * i) / 0.5))
    return image


def scene_photo(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Photo reelle du client en plein cadre, avec un lent mouvement de camera et un texte qui monte par-dessus."""
    photo = ctx.photo(sc.get("photo"))
    if photo is None:
        return scene_hook(ctx, {**sc, "texte": sc.get("texte") or sc.get("sous_texte", "")}, t)
    avance = borne(t / max(0.1, sc.get("duree", 3.0)))
    image = recadrer(photo, ctx.l, ctx.h, 1.0 + 0.14 * sortie_cubique(avance), 0.42 + 0.16 * avance, 0.5)
    image.paste(Image.new("RGB", (ctx.l, ctx.h), (6, 5, 6)), (0, 0), ctx.degrade_bas())
    marge = ctx.px(76)
    base = 112 if ctx.format != "horizontal" else 88

    def blocs(e):
        titre = composer(ctx, sc.get("texte", ""), ctx.px(base * e), ctx.l - 2 * marge)
        petit = bloc_texte(sc["sous_texte"], NORMAL, ctx.px(44 * e), ctx.l - 2 * marge, ctx.px(150 * e), (235, 228, 220)) if sc.get("sous_texte") else None
        return titre, petit

    def mesure(e):
        titre, petit = blocs(e)
        return titre["hauteur"] + (ctx.px(20 * e) + petit.size[1] if petit else 0)

    e = echelle_pour(ctx, mesure, ctx.h * 0.5)
    bloc, petit = blocs(e)
    y0 = int(ctx.h * 0.80 - mesure(e))
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.25)
    if petit is not None:
        suite = 0.25 + 0.09 * sum(len(l) for l in bloc["lignes"]) + 0.4
        coller(image, petit, marge - 10, y0 + bloc["hauteur"] + ctx.px(20 * e), borne((t - suite) / 0.5))
    return image


def scene_mosaique(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Trois photos du client en cartes aux coins arrondis qui rebondissent a l'arrivee."""
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    numeros = [n for n in (sc.get("photos") or []) if ctx.photo(n) is not None][:3]
    marge = ctx.px(76)
    titre = composer(ctx, sc.get("titre", ""), ctx.px(88 if ctx.format != "horizontal" else 70), ctx.l - 2 * marge)
    haut = ctx.haut_centre(titre["hauteur"] + ctx.px(50) + int(ctx.h * 0.46), -0.02)
    dessiner_bloc(ctx, image, titre, marge, haut, t, 0.05)
    y_cartes = haut + titre["hauteur"] + ctx.px(50)
    ecart = ctx.px(24)
    rayon = ctx.px(34)
    if ctx.format == "horizontal":
        zone_h = int(ctx.h * 0.52)
        largeur = (ctx.l - 2 * marge - 2 * ecart) // 3
        cases = [(marge + i * (largeur + ecart), y_cartes, largeur, zone_h) for i in range(3)]
    else:
        grand_h, petit_h = int(ctx.h * 0.26), int(ctx.h * 0.20)
        petit_l = (ctx.l - 2 * marge - ecart) // 2
        cases = [(marge, y_cartes, ctx.l - 2 * marge, grand_h), (marge, y_cartes + grand_h + ecart, petit_l, petit_h),
                 (marge + petit_l + ecart, y_cartes + grand_h + ecart, petit_l, petit_h)]
    for i, numero in enumerate(numeros):
        x, y, l, h = cases[i]
        p = sortie_rebond((t - 0.35 - 0.28 * i) / 0.55)
        if p <= 0:
            continue
        carte = ctx.carte(numero, l, h, rayon)
        s = max(0.05, p)
        reduite = carte.resize((max(1, int(l * s)), max(1, int(h * s))), Image.BILINEAR) if abs(s - 1) > 0.002 else carte
        flotte = math.sin(t * 1.1 + i * 1.3) * ctx.px(6)
        coller(image, reduite, x + (l - reduite.size[0]) / 2, y + (h - reduite.size[1]) / 2 + flotte, borne(p * 3))
    return image


def _pastille_texte(ctx: Contexte, d: ImageDraw.ImageDraw, image: Image.Image, texte: str, x: int, y: int, fond: tuple, encre: tuple, opacite: float = 1.0) -> None:
    """Petite etiquette arrondie (« AVANT », « APRES ») posee sur une photo."""
    if opacite <= 0:
        return
    img = image_texte(texte.upper(), GRAS, ctx.px(34), encre, False, 0.1)
    l, h = img.size[0] + ctx.px(20), img.size[1] + ctx.px(6)
    calque = Image.new("RGBA", (l, h), (0, 0, 0, 0))
    ImageDraw.Draw(calque).rounded_rectangle((0, 0, l - 1, h - 1), radius=h // 2, fill=fond + (int(235 * opacite),))
    calque.paste(img, (ctx.px(10) - 10, (h - img.size[1]) // 2), img)
    image.paste(calque.convert("RGB"), (x, y), calque.getchannel("A"))


def _epingle(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, couleur: tuple, trou: tuple) -> None:
    """Icone de localisation (goutte avec un point au centre)."""
    d.polygon([(cx - r * 0.82, cy + r * 0.45), (cx + r * 0.82, cy + r * 0.45), (cx, cy + r * 2.0)], fill=couleur)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=couleur)
    d.ellipse((cx - r * 0.4, cy - r * 0.4, cx + r * 0.4, cy + r * 0.4), fill=trou)


def scene_avant_apres(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Avant / apres : la photo « avant » puis un curseur la balaie pour reveler la photo « apres »."""
    numeros = [n for n in (sc.get("photos") or []) if ctx.photo(n) is not None][:2]
    if len(numeros) < 2:                                   # une seule photo disponible : repli sur une scene photo
        return scene_photo(ctx, {**sc, "photo": numeros[0] if numeros else None, "texte": sc.get("titre", "")}, t)
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    boite_h = int(ctx.h * (0.46 if ctx.format == "vertical" else 0.50))
    boite_l = ctx.l - 2 * marge if ctx.format != "horizontal" else min(ctx.l - 2 * marge, int(boite_h * 1.6))

    def blocs(e):
        titre = composer(ctx, sc.get("titre", ""), ctx.px((80 if ctx.format != "horizontal" else 64) * e), ctx.l - 2 * marge)
        legende = bloc_texte(sc["legende"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(110 * e), (214, 204, 194)) if sc.get("legende") else None
        return titre, legende

    def mesure(e):
        titre, legende = blocs(e)
        return titre["hauteur"] + ctx.px(36 * e) + boite_h + (ctx.px(28 * e) + legende.size[1] if legende else 0)

    e = echelle_pour(ctx, mesure)
    titre, legende = blocs(e)
    haut = ctx.haut_centre(mesure(e))
    dessiner_bloc(ctx, image, titre, marge, haut, t, 0.0)
    boite_x, boite_y = (ctx.l - boite_l) // 2, haut + titre["hauteur"] + ctx.px(36 * e)
    duree = max(2.0, sc.get("duree", 4.5))
    zoom = 1.0 + 0.05 * borne(t / duree)
    avant = recadrer(ctx.photo(numeros[0]), boite_l, boite_h, zoom)
    apres = recadrer(ctx.photo(numeros[1]), boite_l, boite_h, zoom)
    balayage = entree_sortie((t - 1.1) / 1.4)
    x_curseur = int(boite_l * balayage)
    composition = avant.copy()
    if x_curseur > 0:
        composition.paste(apres.crop((0, 0, x_curseur, boite_h)), (0, 0))
    d = ImageDraw.Draw(composition)
    if 0.9 < t and 0 < x_curseur < boite_l:                # poignee du curseur : trait blanc et rond d'accent avec deux fleches
        d.line((x_curseur, 0, x_curseur, boite_h), fill=(255, 255, 255), width=ctx.px(6))
        rayon = ctx.px(36)
        d.ellipse((x_curseur - rayon, boite_h // 2 - rayon, x_curseur + rayon, boite_h // 2 + rayon), fill=ctx.t["accent"], outline=(255, 255, 255), width=ctx.px(5))
        f = rayon * 0.42
        d.polygon([(x_curseur - f * 1.5, boite_h // 2), (x_curseur - f * 0.4, boite_h // 2 - f), (x_curseur - f * 0.4, boite_h // 2 + f)], fill=ctx.t["sur_accent"])
        d.polygon([(x_curseur + f * 1.5, boite_h // 2), (x_curseur + f * 0.4, boite_h // 2 - f), (x_curseur + f * 0.4, boite_h // 2 + f)], fill=ctx.t["sur_accent"])
    sombre, clair = (15, 15, 15), (255, 255, 255)
    _pastille_texte(ctx, d, composition, "Avant", boite_l - ctx.px(190), ctx.px(24), sombre, clair, borne((0.82 - balayage) / 0.15) * borne(t / 0.4))
    _pastille_texte(ctx, d, composition, "Après", ctx.px(24), ctx.px(24), ctx.t["accent"], ctx.t["sur_accent"], borne((balayage - 0.18) / 0.15))
    carte = composition.convert("RGBA")
    masque = Image.new("L", (boite_l, boite_h), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, boite_l - 1, boite_h - 1), radius=ctx.px(36), fill=255)
    carte.putalpha(masque)
    coller(image, carte, boite_x, boite_y + (1 - sortie_cubique(t / 0.5)) * ctx.px(40), borne(t / 0.35))
    if legende is not None:
        coller(image, legende, marge - 10, boite_y + boite_h + ctx.px(28 * e), borne((t - 2.4) / 0.5))
    return image


def scene_realisation(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Realisation de la semaine : titre, 1 a 3 photos du chantier, commune et description."""
    numeros = [n for n in (sc.get("photos") or []) if ctx.photo(n) is not None][:3]
    if not numeros:
        return scene_hook(ctx, {**sc, "texte": sc.get("titre") or sc.get("texte", ""), "sous_texte": sc.get("sous_texte", "")}, t)
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    marge = ctx.px(76)
    zone_h = int(ctx.h * {"vertical": 0.33, "carre": 0.28, "horizontal": 0.36}[ctx.format])
    largeur_zone = ctx.l - 2 * marge if ctx.format != "horizontal" else min(ctx.l - 2 * marge, int(zone_h * 1.9))
    lieu = sans_balises(sc.get("sous_texte", "")).strip()

    def elements(e):
        titre = composer(ctx, sc.get("titre", ""), ctx.px((76 if ctx.format != "horizontal" else 60) * e), ctx.l - 2 * marge)
        description = bloc_texte(sc["texte"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(190 * e), (214, 204, 194)) if sc.get("texte") else None
        return titre, description

    def mesure(e):
        titre, description = elements(e)
        return ctx.px(50 * e) + titre["hauteur"] + ctx.px(28 * e) + zone_h + (ctx.px(76 * e) if lieu else 0) + (ctx.px(14 * e) + description.size[1] if description else 0)

    e = echelle_pour(ctx, mesure)
    titre, description = elements(e)
    haut = ctx.haut_centre(mesure(e))
    etiquette(ctx, image, sc.get("kicker") or "Réalisation de la semaine", marge, haut + ctx.px(20), ctx.t["accent_texte"], 28, borne(t / 0.4))
    y_titre = haut + ctx.px(50 * e)
    dessiner_bloc(ctx, image, titre, marge, y_titre, t, 0.1)
    y_photos = y_titre + titre["hauteur"] + ctx.px(28 * e)
    x0, ecart, rayon = (ctx.l - largeur_zone) // 2, ctx.px(18), ctx.px(30)
    if len(numeros) == 1:
        cases = [(x0, y_photos, largeur_zone, zone_h)]
    elif len(numeros) == 2:
        grand = int(largeur_zone * 0.6)
        cases = [(x0, y_photos, grand, zone_h), (x0 + grand + ecart, y_photos, largeur_zone - grand - ecart, zone_h)]
    else:
        grand = int(largeur_zone * 0.56)
        moitie = (zone_h - ecart) // 2
        cases = [(x0, y_photos, grand, zone_h), (x0 + grand + ecart, y_photos, largeur_zone - grand - ecart, moitie),
                 (x0 + grand + ecart, y_photos + moitie + ecart, largeur_zone - grand - ecart, zone_h - moitie - ecart)]
    for i, numero in enumerate(numeros):
        x, y, l, h = cases[i]
        p = sortie_rebond((t - 0.45 - 0.25 * i) / 0.55)
        if p <= 0:
            continue
        carte = ctx.carte(numero, l, h, rayon)
        facteur = max(0.05, p)
        reduite = carte.resize((max(1, int(l * facteur)), max(1, int(h * facteur))), Image.BILINEAR) if abs(facteur - 1) > 0.002 else carte
        coller(image, reduite, x + (l - reduite.size[0]) / 2, y + (h - reduite.size[1]) / 2, borne(p * 3))
    y_bas = y_photos + zone_h
    if lieu:
        avance = borne((t - 1.2) / 0.5)
        if avance > 0:
            rayon_pin = ctx.px(17 * e)
            calque = Image.new("RGBA", (ctx.l - 2 * marge, ctx.px(76 * e)), (0, 0, 0, 0))
            dessin = ImageDraw.Draw(calque)
            _epingle(dessin, rayon_pin + 4, ctx.px(26 * e), rayon_pin, ctx.t["accent_texte"], (20, 20, 20))
            texte_lieu = bloc_texte(lieu, GRAS, ctx.px(46 * e), ctx.l - 2 * marge - ctx.px(80 * e), ctx.px(60 * e), ctx.t["texte"])
            calque.paste(texte_lieu, (ctx.px(52 * e), 0), texte_lieu)
            coller(image, calque, marge - 4, y_bas + ctx.px(20 * e), avance)
    if description is not None:
        coller(image, description, marge - 10, y_bas + (ctx.px(76 * e) if lieu else 0) + ctx.px(14 * e), borne((t - 1.6) / 0.5))
    return image


SCENES = {"hook": scene_hook, "chiffre": scene_chiffre, "liste": scene_liste, "etape": scene_etape, "citation": scene_citation, "marque": scene_marque, "cta": scene_cta, "photo": scene_photo, "mosaique": scene_mosaique, "avant_apres": scene_avant_apres, "realisation": scene_realisation}


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
                valeur = str(brut[cle]).replace("—", "-").strip()[:180]
                scene[cle] = valeur if cle in ("texte", "titre", "sous_texte") else sans_balises(valeur)          # *surlignage* et _serif_ : seulement ou ils sont geres
        if brut.get("photo") not in (None, "", 0, "0"):
            try:
                scene["photo"] = max(1, min(int(brut["photo"]), 12))
            except (TypeError, ValueError):
                pass
        if isinstance(brut.get("photos"), list):
            scene["photos"] = [n for n in (int(x) if str(x).strip().isdigit() else 0 for x in brut["photos"]) if 1 <= n <= 12][:3]
        if isinstance(brut.get("items"), list):
            scene["items"] = [sans_balises(str(x).replace("—", "-")).strip()[:80] for x in brut["items"] if str(x).strip()][:5]
        scenes.append(scene)
    scenes = scenes[:12]
    etapes = [sc for sc in scenes if sc["type"] == "etape"]
    if len(etapes) == 1:                  # une etape « 01 » sans « 02 » n'a pas de sens : elle devient une scene d'accroche
        unique = etapes[0]
        unique.update(type="hook", texte=unique.get("titre") or unique.get("texte", ""), sous_texte=unique.get("texte", "") if unique.get("titre") else "")
        for cle in ("titre", "numero", "kicker"):
            unique.pop(cle, None)
    else:
        for rang, etape in enumerate(etapes, 1):
            etape["numero"] = f"{rang:02d}"
    return scenes


def minutage(scenes: list) -> list:
    """[(debut, fin)] de chaque scene sur la chronologie de la video ; deux scenes se chevauchent pendant la transition."""
    positions, debut = [], 0.0
    for scene in scenes:
        positions.append((debut, debut + scene["duree"]))
        debut += scene["duree"] - DUREE_TRANSITION
    return positions


def caler_sur_tempo(scenes: list, bpm: float) -> list:
    """Allonge ou raccourcit chaque scene pour que son debut tombe sur un temps de la musique (par pas de deux temps)."""
    try:
        pas = 2 * 60.0 / float(bpm)
    except (TypeError, ValueError, ZeroDivisionError):
        return scenes
    if not 50 <= float(bpm) <= 200:
        return scenes
    for scene in scenes:
        scene["duree"] = round(max(1, round((scene["duree"] - DUREE_TRANSITION) / pas)) * pas + DUREE_TRANSITION, 3)
    return scenes


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
        if scene["type"] == "photo":
            evenements.append((debut + 0.3, "pop"))
        if scene["type"] == "mosaique":
            evenements += [(debut + 0.35 + 0.28 * k + 0.1, "pop") for k in range(len(scene.get("photos", [])))]
        if scene["type"] == "avant_apres":
            evenements += [(debut + 0.35, "pop"), (debut + 1.05, "whoosh"), (debut + 2.4, "ding")]
        if scene["type"] == "realisation":
            evenements += [(debut + 0.5 + 0.25 * k + 0.1, "pop") for k in range(min(3, len(scene.get("photos", []))))]
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


def rendre(scenario: dict, charte: dict, format_video: str, sortie: str, marque: str = "", ville: str = "", progression=None, musique: str = None,
           images: list = None, logo: Image.Image = None, bpm: float = None) -> float:
    """Rend la video MP4 (image par image vers ffmpeg, bruitages inclus). Renvoie sa duree en secondes."""
    if format_video not in FORMATS:
        raise ErreurMotion("Format inconnu.")
    scenes = nettoyer_scenario(scenario)
    if not scenes:
        raise ErreurMotion("Le scenario ne contient aucune scene valide.")
    if bpm:
        scenes = caler_sur_tempo(scenes, bpm)
    ctx = Contexte(format_video, charte, marque, ville, images, logo)
    positions = minutage(scenes)
    duree = positions[-1][1]
    total_images = int(duree * FPS)
    evenements = evenements_sons(scenes)
    commande = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ctx.l}x{ctx.h}", "-r", str(FPS), "-i", "-"]
    fichiers = sorted({sons.chemin_son(nom) for _, nom in evenements})
    for fichier in fichiers:
        commande += ["-i", fichier]
    indice_musique = 1 + len(fichiers)
    if musique:
        commande += ["-stream_loop", "-1", "-i", musique]
    parties, etiquettes = [], []
    for k, (t, nom) in enumerate(evenements):
        indice = 1 + fichiers.index(sons.chemin_son(nom))
        ms = int(max(0.0, t) * 1000)
        parties.append(f"[{indice}:a]aformat=channel_layouts=stereo,volume={VOLUMES[nom]:.2f},adelay={ms}|{ms}[s{k}]")
        etiquettes.append(f"[s{k}]")
    if musique:
        parties.append(f"[{indice_musique}:a]aformat=channel_layouts=stereo,volume=0.50,afade=t=in:st=0:d=0.8,afade=t=out:st={max(0.0, duree - 1.8):.2f}:d=1.8[mus]")
        etiquettes.append("[mus]")
    parties.append("".join(etiquettes) + f"amix=inputs={len(etiquettes)}:duration=longest:dropout_transition=0:normalize=0,atrim=0:{duree:.2f},apad=whole_dur={duree:.2f}[a]")
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


TELEVERSEES = {}            # images importees depuis la page : identifiant -> (octets, instant)
MAX_TELEVERSEES = 30


def enregistrer_import(octets: bytes) -> str:
    """Garde en memoire une image importee (verifiee lisible) jusqu'au rendu ; renvoie son identifiant."""
    if len(octets) > TAILLE_MAX_IMAGE:
        raise ErreurMotion("Image trop lourde (8 Mo au plus).")
    ouvrir_image(octets)
    maintenant = time.time()
    for ancien in [i for i, (_, t) in TELEVERSEES.items() if maintenant - t > DUREE_CONSERVATION]:
        TELEVERSEES.pop(ancien, None)
    if len(TELEVERSEES) >= MAX_TELEVERSEES:
        raise ErreurMotion("Trop d'images importees : generez une video pour liberer de la place.")
    identifiant = uuid.uuid4().hex[:12]
    TELEVERSEES[identifiant] = (octets, maintenant)
    return identifiant


def telecharger_musique(url: str, dossier: str) -> str:
    """Piste de la bibliotheque musicale (adresse enregistree par la plateforme) copiee dans le dossier du rendu."""
    extension = (url or "").rsplit(".", 1)[-1].lower()[:4]
    chemin = os.path.join(dossier, f"musique.{extension if extension.isalnum() else 'mp3'}")
    reponse = requests.get(url, timeout=60)
    reponse.raise_for_status()
    with open(chemin, "wb") as fichier:
        fichier.write(reponse.content)
    return chemin


def demarrer(scenario: dict, charte: dict, format_video: str, marque: str = "", ville: str = "", sources_images: list = None, source_logo: tuple = None,
             url_musique: str = "", bpm: float = None) -> str:
    """
    Lance le rendu en tache de fond (un seul a la fois : il occupe le processeur) et renvoie son identifiant. sources_images : [("google", url) ou
    ("import", identifiant)] dans l'ordre des numeros de photos ; source_logo : ("url", adresse) ou ("import", identifiant).
    """
    import tempfile
    with _VERROU:
        _purger()
        if any(e["etat"] == "en_cours" for e in ETATS.values()):
            raise ErreurMotion("Une video est deja en cours de generation : attendez qu'elle soit terminee.")
        identifiant = uuid.uuid4().hex[:12]
        etat = {"etat": "en_cours", "progression": 0.0, "erreur": "", "chemin": "", "duree": 0.0, "cree": time.time(), "format": format_video, "marque": marque}
        ETATS[identifiant] = etat
    dossier = tempfile.mkdtemp(prefix="fiche_locale_motion_")

    def travail():
        try:
            chemin = os.path.join(dossier, "video.mp4")
            images = []
            for numero, (genre, reference) in enumerate(sources_images or [], 1):
                try:
                    images.append(telecharger_image(reference) if genre == "google" else ouvrir_image(TELEVERSEES[reference][0]))
                except (ErreurMotion, KeyError) as erreur:
                    raise ErreurMotion(f"Photo {numero} indisponible : {erreur}") from erreur
            logo = None
            if source_logo:
                try:
                    genre, reference = source_logo
                    logo = telecharger_image(reference, True, True) if genre == "url" else ouvrir_image(TELEVERSEES[reference][0], True)
                except (ErreurMotion, KeyError):
                    logo = None                     # un logo introuvable ne bloque pas la video
            musique = None
            if url_musique:
                try:
                    musique = telecharger_musique(url_musique, dossier)
                except (requests.RequestException, OSError):
                    musique = None                  # idem pour la musique
            duree = rendre(scenario, charte, format_video, chemin, marque, ville, lambda p: etat.__setitem__("progression", round(p, 3)), musique, images, logo, bpm)
            etat.update(etat="termine", chemin=chemin, duree=round(duree, 1), progression=1.0)
        except Exception as erreur:
            etat.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
    return identifiant


def heberger(identifiant: str, heberger_fichier) -> dict:
    """
    Heberge la video terminee en arriere-plan (heberger_fichier(octets, nom) -> adresse publique) pour pouvoir la publier. Un hebergement deja fait
    n'est pas refait ; un hebergement en erreur est relance. Renvoie {"etat": absent / en_cours / pret / erreur, "url", "nom_distant", "erreur"}.
    """
    etat = ETATS.get(identifiant)
    if not etat or etat["etat"] != "termine" or not os.path.exists(etat["chemin"]):
        raise ErreurMotion("La video n'est pas prete (ou n'est plus disponible) : regenerez-la.")
    hebergement = etat.setdefault("hebergement", {"etat": "absent", "url": "", "nom_distant": "", "erreur": ""})
    if hebergement["etat"] in ("absent", "erreur"):
        hebergement.update(etat="en_cours", erreur="")
        chemin_video = etat["chemin"]

        def travail():
            try:
                with open(chemin_video, "rb") as fichier:
                    octets = fichier.read()
                nom = f"motion-{uuid.uuid4().hex[:12]}.mp4"
                hebergement.update(url=heberger_fichier(octets, nom), nom_distant=nom, etat="pret")
            except Exception as erreur:
                hebergement.update(etat="erreur", erreur=f"Hebergement de la video impossible : {str(erreur)[:250]}")

        threading.Thread(target=travail, daemon=True).start()
    return dict(hebergement)
