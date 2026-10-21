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

from . import personnage, sons
from .montage_video import FFMPEG

DOSSIER_POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "polices")
FPS = 30
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080), "horizontal": (1920, 1080)}
DUREE_TRANSITION = 0.45
TYPES_SCENES = ("hook", "chiffre", "liste", "etape", "citation", "marque", "cta", "photo", "mosaique", "avant_apres", "realisation", "personnage", "recherche")
HOTES_IMAGES = (".googleusercontent.com", ".ggpht.com", ".google.com")
TAILLE_MAX_IMAGE = 8 * 1024 * 1024
GRAS, NORMAL, SERIF = "Poppins-Bold.ttf", "Poppins-Regular.ttf", "DMSerifDisplay-Regular.ttf"
CREME = (250, 244, 235)
GOOGLE_BORD_CLAIR = (218, 220, 224)
GOOGLE_BLEU, GOOGLE_ROUGE, GOOGLE_JAUNE, GOOGLE_VERT = personnage.GOOGLE_BLEU, personnage.GOOGLE_ROUGE, personnage.GOOGLE_JAUNE, personnage.GOOGLE_VERT
STYLES = ("", "google")                  # "" : couleurs du client sur fond sombre ; "google" : fond blanc et palette Google
_REMPLACE_SERIF = False                  # vrai pendant un rendu « google » (voir Contexte)


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


def theme(charte: dict, style: str = "") -> dict:
    """Palette de la video a partir de la charte du montage (couleur principale = accent, fond tres sombre teinte de cette couleur) ; style « google » : fond blanc, bleu, rouge, jaune, vert."""
    if style == "google":
        return {
            "fond": (255, 255, 255), "fond2": (248, 249, 250), "carte": (241, 243, 244), "texte": (32, 33, 36), "texte2": (95, 99, 104), "texte3": (154, 160, 166),
            "accent": GOOGLE_BLEU, "accent2": GOOGLE_JAUNE, "accent_texte": (26, 115, 232), "sur_accent": (255, 255, 255), "marque_hab": (26, 115, 232),
            "google": True, "points": (GOOGLE_BLEU, GOOGLE_ROUGE, GOOGLE_JAUNE, GOOGLE_VERT),
        }
    principale = tuple(int(charte["principale"][i:i + 2], 16) for i in (1, 3, 5))
    secondaire = tuple(int(charte["secondaire"][i:i + 2], 16) for i in (1, 3, 5))
    sombre = (9, 8, 9)
    return {
        "fond": melanger(principale, sombre, 0.92), "fond2": melanger(principale, sombre, 0.80), "carte": melanger(principale, sombre, 0.86),
        "texte": CREME, "accent": principale, "accent2": secondaire,
        "accent_texte": principale if luminance(principale) > 0.55 else melanger(principale, (255, 255, 255), 0.45),
        "sur_accent": (17, 17, 17) if luminance(principale) > 0.5 else (255, 255, 255),
        "texte2": (214, 204, 194), "texte3": (190, 180, 170), "marque_hab": melanger(principale, (255, 255, 255), 0.35), "google": False, "points": None,
    }


@functools.lru_cache(maxsize=96)
def _police(nom: str, taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(DOSSIER_POLICES, nom), max(8, int(taille)))


def police(nom: str, taille: int) -> ImageFont.FreeTypeFont:
    return _police(GRAS if (_REMPLACE_SERIF and nom == SERIF) else nom, taille)


def image_texte(texte: str, nom: str, taille: int, couleur: tuple, italique: bool = False, espacement: float = 0.0) -> Image.Image:
    if _REMPLACE_SERIF and nom == SERIF:
        nom, italique = GRAS, False
    return _image_texte(texte, nom, taille, couleur, italique, espacement)


@functools.lru_cache(maxsize=1024)
def _image_texte(texte: str, nom: str, taille: int, couleur: tuple, italique: bool = False, espacement: float = 0.0) -> Image.Image:
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
        mots = []
        for mot in texte.split():
            if mots and mot in ("?", "!", ":", ";", "%", "»"):          # une ponctuation seule reste collee au mot qui precede (jamais seule en debut de ligne)
                mots[-1] += " " + mot
            else:
                mots.append(mot)
        for mot in mots:
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
    def __init__(self, format_video: str, charte: dict, marque: str = "", ville: str = "", images: list = None, logo: Image.Image = None, style: str = ""):
        global _REMPLACE_SERIF
        self.l, self.h = FORMATS[format_video]
        self.format = format_video
        self.u = min(self.l, self.h) / 1080            # echelle : les tailles ci-dessous sont donnees pour 1080 px
        self.google = style == "google"
        _REMPLACE_SERIF = self.google                  # un seul rendu a la fois : le reglage vaut pour toute la video
        self.t = theme(charte, style)
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
        clair = luminance(couleur) > 0.85                # fond blanc du style Google : degrade vers un gris tres doux, lueur bleutee a peine visible
        if clair and glow is not None:
            glow = melanger(glow, (255, 255, 255), 0.84)
        cle = (couleur, glow)
        if cle not in self._fonds:
            haut, bas = couleur, melanger(couleur, (206, 212, 222), 0.30) if clair else melanger(couleur, (0, 0, 0), 0.45)
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
            coller(image, image_texte(texte, NORMAL, ctx.px(44 * e), ctx.t["texte2"]), marge - 10, y0 + bloc["hauteur"] + ctx.px(36 * e) + i * petit["hauteur_ligne"], borne((t - suite) / 0.5))
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
        coller(image, image_texte(texte, NORMAL, ctx.px(46 * e), ctx.t["texte2"]), marge - 10, y_titre + titre["hauteur"] + ctx.px(34 * e) + i * petit["hauteur_ligne"], borne((t - debut - 0.12 * i) / 0.5))
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
        coller(image, bloc_texte(sc["auteur"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(110 * e), ctx.t["texte2"]), marge - 10, y_bas + ctx.px(100 * e), borne((t - 1.7) / 0.5))
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
        d.ellipse((px_ - pr, py_ - pr, px_ + pr, py_ + pr), fill=ctx.t["points"][i % 4] if ctx.google else (ctx.t["texte"] if i % 3 else ctx.t["accent2"]))
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
        coller(image, bloc_texte(ligne.strip(), NORMAL, ctx.px(48 * e), ctx.l - 2 * marge, ctx.px(72 * e), ctx.t["texte2"]), marge - 10, y_bouton + ctx.px(220 * e) + i * ctx.px(74 * e), borne((t - 1.3 - 0.15 * i) / 0.5))
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
    dessiner_bloc(ctx, image, bloc, marge, y0, t, 0.25, couleur=CREME, couleur_serif=(138, 180, 248) if ctx.google else None)
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
        legende = bloc_texte(sc["legende"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(110 * e), ctx.t["texte2"]) if sc.get("legende") else None
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
        description = bloc_texte(sc["texte"], NORMAL, ctx.px(40 * e), ctx.l - 2 * marge, ctx.px(190 * e), ctx.t["texte2"]) if sc.get("texte") else None
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
            _epingle(dessin, rayon_pin + 4, ctx.px(26 * e), rayon_pin, ctx.t["accent_texte"], ctx.t["fond"])
            texte_lieu = bloc_texte(lieu, GRAS, ctx.px(46 * e), ctx.l - 2 * marge - ctx.px(80 * e), ctx.px(60 * e), ctx.t["texte"])
            calque.paste(texte_lieu, (ctx.px(52 * e), 0), texte_lieu)
            coller(image, calque, marge - 4, y_bas + ctx.px(20 * e), avance)
    if description is not None:
        coller(image, description, marge - 10, y_bas + (ctx.px(76 * e) if lieu else 0) + ctx.px(14 * e), borne((t - 1.6) / 0.5))
    return image


def _bulle(ctx: Contexte, image: Image.Image, texte_img: Image.Image, x: int, y: int, pointe: tuple, p: float, t_texte: float) -> None:
    """Bulle de dialogue (fond creme, texte fonce) qui apparait en rebondissant, avec sa pointe tournee vers le personnage."""
    pad = ctx.px(42)
    l, h = texte_img.size[0] + 2 * pad, texte_img.size[1] + 2 * pad
    marge = ctx.px(60)
    calque = Image.new("RGBA", (l + 2 * marge, h + 2 * marge), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    ombre = Image.new("RGBA", calque.size, (0, 0, 0, 0))
    ImageDraw.Draw(ombre).rounded_rectangle((marge, marge + ctx.px(10), marge + l, marge + h + ctx.px(10)), radius=ctx.px(48), fill=(0, 0, 0, 90))
    calque = Image.alpha_composite(calque, ombre.filter(ImageFilter.GaussianBlur(ctx.px(14))))
    d = ImageDraw.Draw(calque)
    papier = (255, 255, 255) if ctx.google else CREME
    d.rounded_rectangle((marge, marge, marge + l, marge + h), radius=ctx.px(48), fill=papier + (255,), outline=GOOGLE_BORD_CLAIR + (255,) if ctx.google else None, width=max(2, ctx.px(4)))
    px_, py_ = pointe[0] - x + marge, pointe[1] - y + marge
    cx = max(marge + ctx.px(70), min(px_, marge + l - ctx.px(70)))
    if px_ < marge or px_ > marge + l:                                 # personnage a cote de la bulle : pointe sur le cote
        cy = max(marge + h * 0.3, min(py_, marge + h * 0.7))
        bord = marge + 4 if px_ < marge else marge + l - 4
        d.polygon([(bord, cy - ctx.px(30)), (bord, cy + ctx.px(30)), (px_, py_)], fill=papier + (255,))
    elif py_ > marge + h / 2:                                            # pointe vers le bas
        d.polygon([(cx - ctx.px(34), marge + h - 4), (cx + ctx.px(34), marge + h - 4), (px_, py_)], fill=papier + (255,))
    else:                                                              # pointe vers le haut
        d.polygon([(cx - ctx.px(34), marge + 4), (cx + ctx.px(34), marge + 4), (px_, py_)], fill=papier + (255,))
    calque.paste(texte_img, (marge + pad - 10, marge + pad - 10), texte_img)
    if p <= 0:
        return
    echelle = max(0.05, p)
    cible = (pointe[0] - x + marge, pointe[1] - y + marge)
    reduit = calque.resize((max(1, int(calque.size[0] * echelle)), max(1, int(calque.size[1] * echelle))), Image.BILINEAR)
    coller(image, reduit, x - marge + cible[0] * (1 - echelle), y - marge + cible[1] * (1 - echelle), borne(p * 2.5))


def _badge_personnage(ctx: Contexte) -> Image.Image:
    """Pastille posee sur la poitrine du personnage : le logo du client, ou l'initiale de son nom."""
    if getattr(ctx, "_badge", None) is None:
        n = 160
        img = Image.new("RGBA", (n * 2, n * 2), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((0, 0, n * 2 - 1, n * 2 - 1), fill=((255, 255, 255) if ctx.google else CREME) + (255,))
        if ctx.google:
            quatre = ImageDraw.Draw(img)
            for i, couleur in enumerate(ctx.t["points"]):
                cx, cy = n * (0.62 if i % 2 == 0 else 1.38), n * (0.62 if i < 2 else 1.38)
                quatre.ellipse((cx - n * 0.3, cy - n * 0.3, cx + n * 0.3, cy + n * 0.3), fill=couleur + (255,))
        elif ctx.logo is not None:
            logo = ctx.logo.copy()
            logo.thumbnail((int(n * 1.5), int(n * 1.5)))
            img.paste(logo, (int(n - logo.size[0] / 2), int(n - logo.size[1] / 2)), logo if logo.mode == "RGBA" else None)
        else:
            initiale = (sans_balises(ctx.marque).strip()[:1] or "*").upper()
            lettre = image_texte(initiale, GRAS, int(n * 1.15), ctx.t["accent"])
            img.paste(lettre, (int(n - lettre.size[0] / 2), int(n - lettre.size[1] / 2)), lettre)
        ctx._badge = img.resize((n, n), Image.LANCZOS)
    return ctx._badge


def _plante(d: ImageDraw.ImageDraw, ctx: Contexte, x: int, sol_y: int, unite: float, t: float) -> None:
    """Plante en pot posee sur le sol : les feuilles se balancent doucement."""
    vert, vert_clair = melanger((46, 120, 84), ctx.t["accent"], 0.12), melanger((84, 160, 108), ctx.t["accent"], 0.12)
    for i, angle in enumerate((-58, -30, -8, 16, 40, 62)):
        a = math.radians(angle + math.sin(t * 1.4 + i) * 3.5)
        longueur, largeur = unite * (190 + 40 * (i % 3)), unite * 62
        base = (x, sol_y - unite * 80)
        points = []
        for (u, v) in ((0, 0), (0.55, 0.28), (0.38, 0.72), (0, 1.0), (-0.38, 0.72), (-0.55, 0.28)):
            lx, ly = u * largeur, -v * longueur
            points.append((base[0] + lx * math.cos(a) - ly * math.sin(a), base[1] + lx * math.sin(a) + ly * math.cos(a)))
        d.polygon(points, fill=vert if i % 2 else vert_clair)
    d.polygon([(x - unite * 54, sol_y - unite * 84), (x + unite * 54, sol_y - unite * 84), (x + unite * 40, sol_y), (x - unite * 40, sol_y)], fill=melanger(ctx.t["accent2"], (30, 20, 20), 0.35))
    d.rectangle((x - unite * 60, sol_y - unite * 96, x + unite * 60, sol_y - unite * 78), fill=melanger(ctx.t["accent2"], (30, 20, 20), 0.2))


def _decor_personnage(ctx: Contexte, image: Image.Image, t: float, pieds: tuple, unite: float, cote: int, horizontal: bool) -> None:
    """Petite piece derriere le personnage : fenetre en arche (qui s'ouvre a son arrivee), sol, plante en pot."""
    d = ImageDraw.Draw(image)
    accent, fond = ctx.t["accent"], ctx.t["fond"]
    sol_y = pieds[1] - int(unite * 34)
    cx = pieds[0]
    pousse = sortie_expo(t / 0.8)
    largeur = int(unite * 760)
    haut = int(sol_y - unite * 1040 * pousse)
    d.rectangle((cx - largeur // 2, min(haut + largeur // 2, sol_y), cx + largeur // 2, sol_y), fill=melanger(fond, accent, 0.30))               # (au debut, l'arche est encore plus basse que sa courbe)
    d.ellipse((cx - largeur // 2, haut, cx + largeur // 2, haut + largeur), fill=melanger(fond, accent, 0.30))
    interieur = int(largeur * 0.84)
    haut_i = haut + int(largeur * 0.08)
    bas_i = sol_y - int(unite * 40)
    d.rectangle((cx - interieur // 2, min(haut_i + interieur // 2, bas_i), cx + interieur // 2, bas_i), fill=melanger(fond, accent, 0.48))
    d.ellipse((cx - interieur // 2, haut_i, cx + interieur // 2, haut_i + interieur), fill=melanger(fond, accent, 0.48))
    trait = melanger(fond, accent, 0.30)
    d.line((cx, haut_i, cx, sol_y - int(unite * 40)), fill=trait, width=max(3, int(unite * 12)))
    d.line((cx - interieur // 2, haut_i + interieur // 2 + int(unite * 120), cx + interieur // 2, haut_i + interieur // 2 + int(unite * 120)), fill=trait, width=max(3, int(unite * 12)))
    # sol
    d.rectangle((0, sol_y, ctx.l, ctx.h), fill=melanger(fond, (0, 0, 0), 0.34))
    d.rectangle((0, sol_y, ctx.l, sol_y + max(3, int(unite * 9))), fill=melanger(fond, accent, 0.40))
    plante_x = int(ctx.l * (0.5 + cote * 0.46)) if horizontal else int(ctx.l * (0.5 - cote * 0.42))
    _plante(d, ctx, plante_x, sol_y, unite, t)


def _decor_google(ctx: Contexte, image: Image.Image, t: float, pieds: tuple, unite: float, cote: int, horizontal: bool) -> None:
    """Fond « illustration Google » : grand disque bleu pale, formes pastel qui flottent, et quatre points (bleu, rouge, jaune, vert) qui tournent autour du personnage."""
    d = ImageDraw.Draw(image)
    cx, cy = pieds[0], pieds[1] - int(unite * 460)
    pousse = sortie_expo(t / 0.8)
    rayon = unite * 470
    d.ellipse((cx - rayon * pousse, cy - rayon * pousse, cx + rayon * pousse, cy + rayon * pousse), fill=(232, 240, 254))
    for i, (fx, fy, fr, couleur) in enumerate(((-0.95, -0.55, 0.20, (252, 232, 230)), (0.9, -0.7, 0.16, (254, 247, 224)), (-0.85, 0.5, 0.14, (230, 244, 234)), (0.95, 0.45, 0.22, (252, 232, 230)))):
        rr = rayon * fr * pousse
        x, y = cx + fx * rayon + math.sin(t * 0.8 + i) * ctx.px(10), cy + fy * rayon + math.cos(t * 0.7 + i) * ctx.px(10)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=couleur)
    for i, couleur in enumerate(ctx.t["points"]):
        angle = t * 0.6 + i * math.pi / 2
        x, y = cx + rayon * 1.1 * pousse * math.cos(angle), cy + rayon * 0.95 * pousse * math.sin(angle)
        pr = ctx.px(16)
        d.ellipse((x - pr, y - pr, x + pr, y + pr), fill=couleur)
    sol = pieds[1] - int(unite * 34)
    d.line((cx - unite * 380, sol + 8, cx + unite * 380, sol + 8), fill=GOOGLE_BORD_CLAIR, width=max(3, ctx.px(5)))


# --------------------------------------------------------------------------- elements d'interface Google : puces flottantes, scene de recherche

TEXTE_UI, BLANC = (32, 33, 36), (255, 255, 255)
GRIS_UI, GRIS_CLAIR_UI = (95, 99, 104), (241, 243, 244)


def _aa(largeur: int, hauteur: int, dessin, facteur: int = 3) -> Image.Image:
    """Forme aux bords lisses : dessin(draw, f) trace a l'echelle f (toutes les coordonnees multipliees par f), puis l'image est reduite."""
    img = Image.new("RGBA", (max(1, largeur) * facteur, max(1, hauteur) * facteur), (0, 0, 0, 0))
    dessin(ImageDraw.Draw(img), facteur)
    return img.resize((max(1, largeur), max(1, hauteur)), Image.LANCZOS)


def _etoile_aa(d: ImageDraw.ImageDraw, f: int, cx: float, cy: float, rayon: float, couleur: tuple) -> None:
    d.polygon([(f * p[0], f * p[1]) for p in _etoile((cx, cy), rayon)], fill=couleur + (255,))


def _carte_ui(ctx: Contexte, l: int, h: int, rayon: int, contour: tuple = GOOGLE_BORD_CLAIR, fond: tuple = BLANC, marge: int = None) -> tuple:
    """Carte blanche aux coins ronds, bord gris et ombre douce. Renvoie (calque RGBA avec marge, marge)."""
    m = marge if marge is not None else ctx.px(26)
    ombre = Image.new("RGBA", (l + 2 * m, h + 2 * m), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).rounded_rectangle((m, m + ctx.px(8), m + l, m + h + ctx.px(8)), radius=rayon, fill=(32, 33, 36, 60))
    ombre = ombre.filter(ImageFilter.GaussianBlur(ctx.px(11)))
    forme = _aa(l, h, lambda d, f: d.rounded_rectangle((0, 0, l * f - 1, h * f - 1), radius=rayon * f, fill=fond + (255,), outline=contour + (255,), width=max(f, int(ctx.px(2) * f))))
    ombre.alpha_composite(forme, (m, m))
    return ombre, m


def _type_icone(texte: str) -> str:
    t = (texte or "").lower()
    if "ouvert" in t:
        return "ouvert"
    if "appel" in t or "tél" in t or "tel" in t.split():
        return "appel"
    if "itin" in t or "route" in t or "y aller" in t:
        return "route"
    return "point"


def _icone_ui(d: ImageDraw.ImageDraw, f: int, type_icone: str, cx: float, cy: float, r: float) -> None:
    """Icone ronde de couleur Google avec un pictogramme blanc (points, appel, itineraire)."""
    couleur = {"ouvert": GOOGLE_VERT, "appel": GOOGLE_BLEU, "route": GOOGLE_BLEU, "point": GOOGLE_BLEU}[type_icone]
    d.ellipse((f * (cx - r), f * (cy - r), f * (cx + r), f * (cy + r)), fill=couleur + (255,))
    blanc = BLANC + (255,)
    if type_icone == "ouvert":
        d.line([(f * (cx - r * 0.42), f * (cy + r * 0.02)), (f * (cx - r * 0.1), f * (cy + r * 0.34)), (f * (cx + r * 0.46), f * (cy - r * 0.32))], fill=blanc, width=int(f * r * 0.24), joint="curve")
    elif type_icone == "appel":
        d.arc((f * (cx - r * 0.52), f * (cy - r * 0.52), f * (cx + r * 0.52), f * (cy + r * 0.52)), 20, 100, fill=blanc, width=int(f * r * 0.34))        # combine : arc epais, bouts arrondis
        for angle in (20, 100):
            a = math.radians(angle)
            d.ellipse((f * (cx + r * 0.52 * math.cos(a) - r * 0.26), f * (cy + r * 0.52 * math.sin(a) - r * 0.26), f * (cx + r * 0.52 * math.cos(a) + r * 0.26), f * (cy + r * 0.52 * math.sin(a) + r * 0.26)), fill=blanc)
    elif type_icone == "route":
        d.polygon([(f * (cx - r * 0.4), f * (cy + r * 0.38)), (f * (cx + r * 0.44), f * cy), (f * (cx - r * 0.4), f * (cy - r * 0.38)), (f * (cx - r * 0.18), f * cy)], fill=blanc)
    else:
        d.ellipse((f * (cx - r * 0.36), f * (cy - r * 0.36), f * (cx + r * 0.36), f * (cy + r * 0.36)), fill=blanc)


def _puce_ui(ctx: Contexte, texte: str, hauteur: int) -> Image.Image:
    """Petite carte d'interface flottante (« Ouvert maintenant », « 4,9 ★ (128 avis) », « Appeler ») : icone ronde, texte, etoile dessinee a la place de ★."""
    cle = (texte, hauteur)
    cache = ctx.__dict__.setdefault("_puces", {})
    if cle in cache:
        return cache[cle]
    taille_texte = int(hauteur * 0.42)
    elements = []                                                       # [("texte", image) ou ("etoile", largeur)]
    for morceau in re.split("(\u2605)", sans_balises(texte)):
        if morceau == "\u2605":
            elements.append(("etoile", int(taille_texte * 0.95)))
        elif morceau.strip():
            img = image_texte(morceau.strip(), GRAS, taille_texte, TEXTE_UI)
            elements.append(("texte", img))
    icone = None if "\u2605" in texte else _type_icone(texte)
    ecart = int(hauteur * 0.14)
    largeur_contenu = sum((e[1].size[0] - 20 if e[0] == "texte" else e[1]) + ecart for e in elements) - ecart
    pad = int(hauteur * 0.34)
    largeur = pad * 2 + largeur_contenu + (int(hauteur * 0.5) + ecart if icone else 0)
    carte, m = _carte_ui(ctx, largeur, hauteur, hauteur // 2, marge=ctx.px(22))
    x = m + pad
    if icone:
        r = hauteur * 0.25
        pastille = _aa(int(r * 2) + 2, int(r * 2) + 2, lambda d, f: _icone_ui(d, f, icone, r + 1, r + 1, r))
        carte.alpha_composite(pastille, (int(x), int(m + hauteur / 2 - r - 1)))
        x += int(hauteur * 0.5) + ecart
    for genre, valeur in elements:
        if genre == "etoile":
            rayon = valeur * 0.55
            etoile = _aa(valeur + 2, valeur + 2, lambda d, f: _etoile_aa(d, f, valeur / 2 + 1, valeur / 2 + 1.5, rayon, GOOGLE_JAUNE))
            carte.alpha_composite(etoile, (int(x), int(m + hauteur / 2 - valeur / 2 - 1)))
            x += valeur + ecart
        else:
            carte.alpha_composite(valeur, (int(x - 10), int(m + (hauteur - valeur.size[1]) / 2)))
            x += valeur.size[0] - 20 + ecart
    cache[cle] = carte
    return carte


def _placer_puces(ctx: Contexte, image: Image.Image, puces: list, t: float, pieds: tuple, unite: float, cote: int, horizontal: bool) -> None:
    """Les puces flottent a l'ecart du personnage et de son objet, apparaissent l'une apres l'autre en rebondissant."""
    hauteur = ctx.px(78 if not horizontal else 64)
    for i, texte in enumerate(puces[:2 if horizontal else 3]):
        p = sortie_rebond((t - 1.3 - 0.28 * i) / 0.5)
        if p <= 0:
            continue
        carte = _puce_ui(ctx, texte, hauteur)
        if horizontal:
            cx = ctx.l * (0.5 - cote * 0.24)
            cy = ctx.h * (0.80 + 0.09 * i)
        else:
            cx = ctx.l * (0.5 - cote * 0.27) + (-1) ** i * ctx.px(34)
            cy = pieds[1] - unite * (330 - 118 * i)
        cx = max(carte.size[0] / 2 + ctx.px(24), min(cx, ctx.l - carte.size[0] / 2 - ctx.px(24)))
        cy += math.sin(t * 1.8 + i * 1.7) * ctx.px(7)
        s = max(0.05, p)
        reduite = carte.resize((max(1, int(carte.size[0] * s)), max(1, int(carte.size[1] * s))), Image.BILINEAR) if abs(s - 1) > 0.002 else carte
        coller(image, reduite, cx - reduite.size[0] / 2, cy - reduite.size[1] / 2, borne(p * 2.5))


def _temps_recherche(sc: dict) -> dict:
    """Chronologie de la scene de recherche (secondes) : frappe, carte, resultats, badge, legende."""
    n = len(sans_balises(sc.get("texte", ""))[:40])
    frappe = min(2.4, max(0.8, n / 13))
    carte = 0.5 + frappe + 0.35
    return {"frappe": frappe, "carte": carte, "cartes": [carte + 0.55 + 0.28 * i for i in range(3)], "badge": carte + 2.0, "legende": carte + 2.7}


def _mise_en_page_recherche(ctx: Contexte) -> dict:
    m = ctx.px(60)
    if ctx.format == "horizontal":
        gauche, largeur_g = m, int(ctx.l * 0.46)
        droite = int(ctx.l * 0.54)
        h_carte = int(ctx.h * 0.215)
        return {"barre": (gauche, int(ctx.h * 0.14), largeur_g, int(ctx.h * 0.13)), "plan": (gauche, int(ctx.h * 0.31), largeur_g, int(ctx.h * 0.50)),
                "cartes": [(droite, int(ctx.h * 0.14) + i * (h_carte + int(ctx.h * 0.03)), ctx.l - droite - m, h_carte) for i in range(3)], "legende": None}
    if ctx.format == "carre":
        h_carte = int(ctx.h * 0.12)
        return {"barre": (m, int(ctx.h * 0.13), ctx.l - 2 * m, int(ctx.h * 0.095)), "plan": (m, int(ctx.h * 0.245), ctx.l - 2 * m, int(ctx.h * 0.20)),
                "cartes": [(m, int(ctx.h * 0.47) + i * (h_carte + int(ctx.h * 0.012)), ctx.l - 2 * m, h_carte) for i in range(3)], "legende": None}
    h_carte = int(ctx.h * 0.105)
    return {"barre": (m, int(ctx.h * 0.13), ctx.l - 2 * m, int(ctx.h * 0.075)), "plan": (m, int(ctx.h * 0.23), ctx.l - 2 * m, int(ctx.h * 0.21)),
            "cartes": [(m, int(ctx.h * 0.465) + i * (h_carte + int(ctx.h * 0.015)), ctx.l - 2 * m, h_carte) for i in range(3)], "legende": (m, int(ctx.h * 0.835), ctx.l - 2 * m)}


def _barre_recherche(ctx: Contexte, l: int, h: int, texte: str, curseur: bool) -> Image.Image:
    cle = ("barre", l, h)
    cache = ctx.__dict__.setdefault("_ui", {})
    if cle not in cache:
        carte, m = _carte_ui(ctx, l, h, h // 2)
        r = h * 0.19
        loupe = _aa(int(h * 0.6), int(h * 0.6), lambda d, f: (d.ellipse((f * h * 0.08, f * h * 0.08, f * (h * 0.08 + r * 2), f * (h * 0.08 + r * 2)), outline=GOOGLE_BLEU + (255,), width=int(f * h * 0.065)),
                                                                d.line([(f * (h * 0.08 + r * 1.75), f * (h * 0.08 + r * 1.75)), (f * h * 0.54, f * h * 0.54)], fill=GOOGLE_BLEU + (255,), width=int(f * h * 0.075))))
        carte.alpha_composite(loupe, (m + int(h * 0.42), m + int(h * 0.2)))
        cache[cle] = (carte, m)
    base, m = cache[cle]
    image = base.copy()
    taille = int(h * 0.4)
    x_texte = m + int(h * 1.2)
    maxi = l - int(h * 1.6)
    while taille > 12 and largeur_texte(texte or "M", NORMAL, taille) > maxi:
        taille = int(taille * 0.93)
    img = image_texte(texte, NORMAL, taille, TEXTE_UI) if texte else None
    x_fin = x_texte
    if img is not None:
        image.alpha_composite(img, (x_texte - 10, m + int((h - img.size[1]) / 2)))
        x_fin = x_texte - 10 + img.size[0] - 10
    if curseur:
        d = ImageDraw.Draw(image)
        d.rectangle((x_fin + 4, m + int(h * 0.24), x_fin + 4 + max(2, int(h * 0.04)), m + int(h * 0.76)), fill=GOOGLE_BLEU)
    return image


def _plan_google(ctx: Contexte, l: int, h: int) -> Image.Image:
    """Fond de plan (routes, parc, riviere) aux coins arrondis, sans les reperes."""
    cle = ("plan", l, h)
    cache = ctx.__dict__.setdefault("_ui", {})
    if cle not in cache:
        f = 2
        L, H = l * f, h * f
        contenu = Image.new("RGB", (L, H), (232, 234, 237))
        d = ImageDraw.Draw(contenu)
        d.polygon([(L * 0.04, H * 0.62), (L * 0.30, H * 0.56), (L * 0.36, H * 0.92), (L * 0.08, H * 0.96)], fill=(200, 230, 201))
        d.polygon([(L * 0.66, H * 0.08), (L * 0.92, H * 0.04), (L * 0.96, H * 0.36), (L * 0.70, H * 0.4)], fill=(200, 230, 201))
        d.line([(0, H * 0.18), (L * 0.28, H * 0.34), (L * 0.6, H * 0.22), (L, H * 0.3)], fill=(168, 218, 220), width=int(H * 0.09), joint="curve")
        for pts, largeur in (([(0, H * 0.5), (L * 0.4, H * 0.58), (L * 0.7, H * 0.46), (L, H * 0.52)], 0.07), ([(L * 0.46, 0), (L * 0.52, H * 0.5), (L * 0.6, H)], 0.07),
                             ([(L * 0.12, 0), (L * 0.2, H * 0.4), (L * 0.14, H)], 0.045), ([(L * 0.8, 0), (L * 0.84, H * 0.55), (L * 0.78, H)], 0.045)):
            d.line([(x, y) for x, y in pts], fill=(255, 255, 255), width=int(H * largeur), joint="curve")
        d.line([(0, H * 0.78), (L, H * 0.7)], fill=(255, 230, 160), width=int(H * 0.05))
        masque = Image.new("L", (L, H), 0)
        ImageDraw.Draw(masque).rounded_rectangle((0, 0, L - 1, H - 1), radius=ctx.px(30) * f, fill=255)
        rgba = contenu.convert("RGBA")
        rgba.putalpha(masque)
        reduite = rgba.resize((l, h), Image.LANCZOS)
        trait = _aa(l, h, lambda dd, ff: dd.rounded_rectangle((0, 0, l * ff - 1, h * ff - 1), radius=ctx.px(30) * ff, outline=GOOGLE_BORD_CLAIR + (255,), width=max(ff, ctx.px(2) * ff)))
        reduite.alpha_composite(trait)
        cache[cle] = reduite
    return cache[cle]


def _etoiles_note(d: ImageDraw.ImageDraw, f: int, x: float, y: float, rayon: float, note: float) -> None:
    for i in range(5):
        _etoile_aa(d, f, x + rayon + i * rayon * 2.15, y, rayon, GOOGLE_JAUNE if note >= i + 0.5 else (218, 220, 224))


def _carte_resultat(ctx: Contexte, l: int, h: int, vedette: bool, nom: str, note: str, avis: str, categorie: str) -> Image.Image:
    """Resultat de recherche locale : miniature, nom, note, categorie, boutons appel et itineraire. Les autres resultats sont de simples gabarits gris (jamais de faux noms)."""
    cle = ("resultat", l, h, vedette, nom, note, avis, categorie)
    cache = ctx.__dict__.setdefault("_ui", {})
    if cle in cache:
        return cache[cle]
    carte, m = _carte_ui(ctx, l, h, int(h * 0.2), contour=GOOGLE_BLEU if vedette else GOOGLE_BORD_CLAIR)
    pad = int(h * 0.14)
    cote_img = h - 2 * pad
    x_img, y_img = m + pad, m + pad
    if vedette and ctx.photo(1) is not None:
        miniature = ctx.carte(1, cote_img, cote_img, int(cote_img * 0.16))
    else:
        miniature = _aa(cote_img, cote_img, lambda d, f: d.rounded_rectangle((0, 0, cote_img * f - 1, cote_img * f - 1), radius=int(cote_img * 0.16 * f), fill=((232, 240, 254) if vedette else GRIS_CLAIR_UI) + (255,)))
        if vedette:
            maison = personnage.accessoire("maison", int(cote_img * 0.62), ctx.t["accent"], GOOGLE_JAUNE, BLANC)
            miniature.alpha_composite(maison, (int(cote_img * 0.19), int(cote_img * 0.2)))
    carte.alpha_composite(miniature, (x_img, y_img))
    x_texte = x_img + cote_img + pad
    x_boutons = m + l - pad - int(h * 0.4) * 2 - int(h * 0.1)
    largeur_texte_dispo = x_boutons - x_texte - pad
    if vedette:
        img_nom = bloc_texte(nom, GRAS, int(h * 0.22), largeur_texte_dispo, h * 0.3, TEXTE_UI)
        carte.alpha_composite(img_nom, (x_texte - 10, y_img - 6))
        y = y_img + int(h * 0.3)
        if note:
            try:
                valeur = float(note.replace(",", "."))
            except ValueError:
                valeur = 0.0
            e = min(1.0, largeur_texte_dispo / max(1.0, largeur_texte(note, NORMAL, int(h * 0.15)) + h * 0.06 + h * 0.062 * 2.15 * 5 + (largeur_texte(f"({avis})", NORMAL, int(h * 0.15)) + h * 0.04 if avis else 0)))
            img_note = image_texte(note, NORMAL, int(h * 0.15 * e), GRIS_UI)
            carte.alpha_composite(img_note, (x_texte - 10, y - 4))
            x_etoiles = x_texte + img_note.size[0] - 20 + int(h * 0.04 * e)
            rayon = h * 0.062 * e
            largeur_etoiles = int(rayon * 2.15 * 5 + 4)
            bande = _aa(largeur_etoiles, int(rayon * 2.4), lambda d, f: _etoiles_note(d, f, 1, rayon * 1.2, rayon, valeur))
            carte.alpha_composite(bande, (int(x_etoiles), int(y + h * 0.015)))
            if avis:
                img_avis = image_texte(f"({avis})", NORMAL, int(h * 0.15 * e), GRIS_UI)
                carte.alpha_composite(img_avis, (int(x_etoiles + largeur_etoiles + h * 0.02) - 10, y - 4))
            y += int(h * 0.23)
        if categorie:
            img_cat = image_texte(categorie + "  \u00b7", NORMAL, int(h * 0.15), GRIS_UI)
            carte.alpha_composite(img_cat, (x_texte - 10, y - 4))
            img_ouvert = image_texte("Ouvert", GRAS, int(h * 0.15), (24, 128, 56))
            carte.alpha_composite(img_ouvert, (x_texte - 10 + img_cat.size[0] - 14, y - 4))
    else:
        for i, (largeur_rel, hauteur_rel, dy) in enumerate(((0.62, 0.15, 0.04), (0.42, 0.1, 0.34), (0.5, 0.1, 0.56))):
            barre = _aa(int(largeur_texte_dispo * largeur_rel), int(h * hauteur_rel), lambda d, f, w=int(largeur_texte_dispo * largeur_rel), hh=int(h * hauteur_rel):
                        d.rounded_rectangle((0, 0, w * f - 1, hh * f - 1), radius=hh * f // 2, fill=(218, 220, 224, 255)))
            carte.alpha_composite(barre, (x_texte, y_img + int(h * dy)))
    for i, genre in enumerate(("appel", "route")):
        diametre = int(h * 0.4)
        bouton = _aa(diametre + 2, diametre + 2, lambda d, f, i=i, genre=genre: (
            d.ellipse((f, f, f * (diametre + 1), f * (diametre + 1)), outline=(GOOGLE_BLEU if vedette else (218, 220, 224)) + (255,), width=max(f, int(f * h * 0.025))),
            _icone_ui(d, f, genre, diametre / 2 + 1, diametre / 2 + 1, diametre * 0.28) if vedette else None))
        carte.alpha_composite(bouton, (x_boutons + i * (diametre + int(h * 0.1)), m + int((h - diametre) / 2)))
    cache[cle] = carte
    return carte


def scene_recherche(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Recherche Google locale : la requete se tape, le plan apparait avec ses reperes, puis les resultats ; le premier (le client) est mis en avant."""
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    mp = _mise_en_page_recherche(ctx)
    tt = _temps_recherche(sc)
    requete = sans_balises(sc.get("texte", ""))[:40] or "entreprise près de moi"
    nom = sans_balises(sc.get("nom") or ctx.marque or "Votre entreprise")
    # barre de recherche : glisse depuis le haut puis la requete se tape lettre par lettre
    x, y, l, h = mp["barre"]
    entree = sortie_expo(t / 0.5)
    frappe = borne((t - 0.5) / tt["frappe"])
    tape = requete[:int(round(len(requete) * frappe))]
    barre = _barre_recherche(ctx, l, h, tape, curseur=(frappe < 1.0) or int(t * 2.2) % 2 == 0)
    coller(image, barre, x - ctx.px(26), y - ctx.px(26) - int((1 - entree) * ctx.px(60)), borne(entree * 2))
    # plan et reperes qui tombent
    xp, yp, lp, hp = mp["plan"]
    pc = sortie_rebond((t - tt["carte"]) / 0.55)
    if pc > 0:
        plan = _plan_google(ctx, lp, hp)
        s = max(0.05, pc)
        reduit = plan.resize((max(1, int(lp * s)), max(1, int(hp * s))), Image.BILINEAR) if abs(s - 1) > 0.002 else plan
        coller(image, reduit, xp + (lp - reduit.size[0]) / 2, yp + (hp - reduit.size[1]) / 2, borne(pc * 2.5))
        for i, (fx, fy, grand) in enumerate(((0.62, 0.46, True), (0.27, 0.62, False), (0.82, 0.7, False))):
            depart = tt["carte"] + 0.25 + 0.22 * i
            chute = sortie_rebond((t - depart) / 0.45)
            if chute <= 0:
                continue
            taille = int(hp * (0.42 if grand else 0.3))
            repere = personnage.accessoire("epingle", taille, GOOGLE_ROUGE, GOOGLE_JAUNE, BLANC)
            px_, py_ = xp + lp * fx - taille / 2, yp + hp * fy - taille * 0.92 - (1 - min(chute, 1.0)) * hp * 0.5
            if grand and t > depart + 0.45:                             # onde autour du repere du client
                onde = ((t - depart - 0.45) * 0.9) % 1.0
                rayon = hp * (0.1 + 0.28 * onde)
                halo = Image.new("RGBA", (int(rayon * 2) + 4, int(rayon * 2) + 4), (0, 0, 0, 0))
                ImageDraw.Draw(halo).ellipse((2, 2, rayon * 2 + 2, rayon * 2 + 2), outline=GOOGLE_BLEU + (int(200 * (1 - onde)),), width=max(2, ctx.px(4)))
                coller(image, halo, xp + lp * fx - rayon - 2, yp + hp * fy - rayon - 2)
            coller(image, repere, px_, py_, borne(chute * 3))
    # resultats qui montent l'un apres l'autre ; le premier est celui du client
    for i, (xc, yc, lc, hc) in enumerate(mp["cartes"]):
        pr = sortie_cubique((t - tt["cartes"][i]) / 0.5)
        if pr <= 0:
            continue
        carte = _carte_resultat(ctx, lc, hc, i == 0, nom, sc.get("valeur", ""), sc.get("suffixe", ""), sc.get("libelle", ""))
        coller(image, carte, xc - ctx.px(26), yc - ctx.px(26) + int((1 - pr) * ctx.px(90)), pr)
    # pastille « Votre fiche » sur le premier resultat
    pb = sortie_rebond((t - tt["badge"]) / 0.45)
    if pb > 0:
        xc, yc, lc, hc = mp["cartes"][0]
        texte_badge = "Votre fiche"
        img = image_texte(texte_badge, GRAS, int(hc * 0.17), BLANC)
        lb, hb = img.size[0] - 20 + int(hc * 0.36), int(hc * 0.3)
        pastille = _aa(lb, hb, lambda d, f: d.rounded_rectangle((0, 0, lb * f - 1, hb * f - 1), radius=hb * f // 2, fill=GOOGLE_BLEU + (255,)))
        pastille.alpha_composite(img, (int(hc * 0.18) - 10, int((hb - img.size[1]) / 2)))
        s = max(0.05, pb)
        pastille = pastille.resize((max(1, int(lb * s)), max(1, int(hb * s))), Image.BILINEAR)
        coller(image, pastille, xc + lc - lb - ctx.px(22) + (lb - pastille.size[0]) / 2, yc - hb * 0.45 + (hb - pastille.size[1]) / 2, borne(pb * 2.5))
    if mp["legende"] and sc.get("legende"):
        xl, yl, ll = mp["legende"]
        img = bloc_texte(sc["legende"], NORMAL, ctx.px(40), ll, ctx.px(120), ctx.t["texte2"], False, True)
        coller(image, img, xl + (ll - img.size[0]) / 2, yl, borne((t - tt["legende"]) / 0.5))
    return image


def scene_personnage(ctx: Contexte, sc: dict, t: float) -> Image.Image:
    """Un personnage anime (voir personnage.py) arrive, dit la phrase dans une bulle et joue l'action choisie, avec un objet a ses cotes."""
    image = ctx.fond(ctx.t["fond"], t, ctx.t["accent"])
    d = ImageDraw.Draw(image)
    action = sc.get("action") if sc.get("action") in personnage.ACTIONS else "explique"
    objet = sc.get("accessoire") if sc.get("accessoire") in personnage.ACCESSOIRES else "aucun"
    if objet == "aucun" and action == "idee":
        objet = "ampoule"
    cote = -1 if sc.get("cote") == -1 else 1                          # cote de l'ecran ou se tient le personnage
    look = personnage.LOOKS[(int(sc.get("perso", 1)) - 1) % personnage.NOMBRE_LOOKS]
    horizontal = ctx.format == "horizontal"
    marge = ctx.px(76)
    duree = sc.get("duree", 3.0)
    if horizontal:
        hauteur_canevas = int(ctx.h * 0.70 * personnage.HAUTEUR_REF / personnage.HAUTEUR_VISIBLE)
        pieds = (int(ctx.l * (0.5 + cote * 0.24)), int(ctx.h * 0.89))
    else:
        visible = 0.50 if ctx.format == "vertical" else 0.56
        hauteur_canevas = int(ctx.h * visible * personnage.HAUTEUR_REF / personnage.HAUTEUR_VISIBLE)
        pieds = (int(ctx.l * (0.5 + cote * 0.15)), int(ctx.h * (0.905 if ctx.format == "vertical" else 0.93)))
    unite = hauteur_canevas / personnage.HAUTEUR_REF
    (_decor_google if ctx.google else _decor_personnage)(ctx, image, t, pieds, unite, cote, horizontal)
    # arrivee : le personnage saute depuis le bord de l'ecran, puis joue son action
    arrivee = sortie_rebond(t / 0.7)
    sortie_x = pieds[0] + cote * int(ctx.l * 0.55) * (1 - sortie_cubique(t / 0.6))
    parle = 0.7 < t < duree - 0.35
    t_action = max(0.0, t - 0.5)
    p = personnage.pose(action, t_action, parle, -cote)
    p["saut"] += max(0.0, math.sin(min(1.0, t / 0.7) * math.pi)) * 60 * (1 - borne((t - 0.7) / 0.1)) if t < 0.8 else 0
    if ctx.google:                                                     # t-shirt de l'une des quatre couleurs Google selon le personnage, pantalon gris
        haut, bas = personnage.GOOGLE_COULEURS[(int(sc.get("perso", 1)) - 1) % 4], (60, 64, 67)
    else:
        haut, bas = ctx.t["accent"], melanger(ctx.t["accent"], (14, 18, 36), 0.80)
    if sc.get("forme") == "geo":
        perso = personnage.dessiner_geo(hauteur_canevas, p, look, haut, bas if ctx.google else melanger(ctx.t["accent"], (14, 18, 36), 0.80), haut, _badge_personnage(ctx))
    else:
        perso = personnage.dessiner(hauteur_canevas, p, look, haut, bas, (26, 26, 32), haut, _badge_personnage(ctx), ctx.google)
    reception = t - 0.66                                               # ecrasement a la reception du saut d'arrivee, puis retour souple
    ecrase = max(0.0, math.exp(-reception * 7) * math.sin(reception * 16)) if 0 <= reception < 0.7 else 0.0
    sx, sy = 1 + 0.07 * ecrase, 1 - 0.09 * ecrase
    if ecrase > 0.01:
        perso_info = dict(perso.info)
        perso = perso.resize((max(1, int(perso.size[0] * sx)), max(1, int(perso.size[1] * sy))), Image.BILINEAR)
        perso.info["tete"], perso.info["main"] = (perso_info["tete"][0] * sx, perso_info["tete"][1] * sy), (perso_info["main"][0] * sx, perso_info["main"][1] * sy)
    ombre_rayon = int(unite * 190 * (1 - 0.35 * borne(p["saut"] / 80)))
    ombre = Image.new("RGBA", (ombre_rayon * 2, ombre_rayon // 2), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).ellipse((0, 0, ombre_rayon * 2 - 1, ombre_rayon // 2 - 1), fill=(0, 0, 0, 34 if ctx.google else 110))
    coller(image, ombre, int(sortie_x - ombre_rayon), pieds[1] - ombre_rayon // 5, borne(t / 0.3))
    origine = (sortie_x - personnage.PIEDS[0] * unite * sx, pieds[1] - personnage.PIEDS[1] * unite * sy)
    coller(image, perso, int(origine[0]), int(origine[1]), borne(t / 0.15))
    tete = (origine[0] + perso.info["tete"][0], origine[1] + perso.info["tete"][1])
    main = (origine[0] + perso.info["main"][0], origine[1] + perso.info["main"][1])
    tient = objet in personnage.TENUS_EN_MAIN and action != "celebre"
    if tient:                                                          # l'objet est dans la main du personnage
        pm = sortie_rebond((t - 0.9) / 0.4)
        if pm > 0:
            cote_main = int(unite * 300)
            sprite = personnage.accessoire(objet, cote_main, ctx.t["accent"], ctx.t["accent2"], (255, 255, 255) if ctx.google else CREME, t)
            sprite = sprite.rotate(-22 + 7 * math.sin(t * 3), resample=Image.BICUBIC, expand=True)
            s_ = max(0.05, pm)
            sprite = sprite.resize((max(1, int(sprite.size[0] * s_)), max(1, int(sprite.size[1] * s_))), Image.BILINEAR)
            coller(image, sprite, int(main[0] - sprite.size[0] / 2 + unite * 20 * s_), int(main[1] - sprite.size[1] * 0.62), borne(pm * 2))
    # objet pose a l'autre bout de l'ecran, qui rebondit a son apparition puis flotte
    if objet != "aucun" and not tient:
        taille = int(min(ctx.l, ctx.h) * (0.25 if not horizontal else 0.28))
        po = sortie_rebond((t - 1.0) / 0.55)
        if po > 0:
            sprite = personnage.accessoire(objet, taille, ctx.t["accent"], ctx.t["accent2"], (255, 255, 255) if ctx.google else CREME, t)
            if action == "idee":                                       # l'ampoule s'allume au-dessus de la tete
                ox, oy = int(tete[0] + cote * unite * 150), int(tete[1] - unite * 330)
            elif horizontal:
                ox, oy = int(ctx.l * (0.5 - cote * 0.12)) - taille // 2, int(ctx.h * 0.14)
            else:
                ox, oy = int(ctx.l * (0.5 - cote * 0.24)) - taille // 2, int(pieds[1] - unite * 640)
            oy += int(math.sin(t * 2.2) * ctx.px(10))
            s_ = max(0.05, po)
            sprite = sprite.resize((max(1, int(taille * s_)), max(1, int(taille * s_))), Image.BILINEAR)
            coller(image, sprite, ox + (taille - sprite.size[0]) // 2, oy + (taille - sprite.size[1]) // 2, borne(po * 2))
    if sc.get("puces"):
        _placer_puces(ctx, image, sc["puces"], t, pieds, unite, cote, horizontal)
    if action == "celebre":                                            # confettis
        for i in range(26):
            depart = 0.6 + (i % 9) * 0.05
            age = t - depart
            if age <= 0:
                continue
            x = tete[0] + math.sin(i * 12.9898) * unite * 240 + math.sin(age * 3 + i) * ctx.px(24)
            y = tete[1] - unite * 120 + age * ctx.px(380 + (i % 5) * 60) - ctx.px(420) * (1 - borne(age / 0.5)) ** 2
            if y > ctx.h * 0.86:
                continue
            couleur = ctx.t["points"][i % 4] if ctx.google else (ctx.t["accent"], ctx.t["accent2"], CREME)[i % 3]
            r = ctx.px(9 + (i % 3) * 3)
            d.rectangle((x - r, y - r / 2, x + r, y + r / 2), fill=couleur)
    if action == "reflechit":                                          # points d'interrogation
        for i in range(2):
            pq = sortie_rebond((t - 1.0 - 0.4 * i) / 0.5)
            if pq > 0:
                img = image_texte("?", SERIF, int(ctx.px(150 - 40 * i) * max(0.05, pq)), ctx.t["accent_texte"], True)
                coller(image, img, int(tete[0] + cote * -1 * unite * (130 + 120 * i)) - img.size[0] // 2, int(tete[1] - unite * (170 + 60 * i)), borne(pq * 2))
    # bulle de dialogue : au-dessus du personnage (vertical, carre) ou de l'autre cote (horizontal)
    texte = sc.get("texte", "")
    if texte:
        if horizontal:
            largeur_max = ctx.l * 0.40
            hauteur_max = ctx.h * 0.46
            img_texte = bloc_texte(texte, GRAS, ctx.px(64), largeur_max, hauteur_max, (28, 24, 30), False, True, 1.05)
            bx = int(ctx.l * (0.5 - cote * 0.24) - img_texte.size[0] / 2 - ctx.px(42))
            by = int(ctx.h * 0.52 - img_texte.size[1] / 2)
            pointe = (int(tete[0] - cote * unite * 120), int(tete[1] - unite * 30))
            bx = max(ctx.px(40), min(bx, ctx.l - ctx.px(40) - img_texte.size[0] - 2 * ctx.px(42)))
        else:
            largeur_max = ctx.l - 2 * marge - 2 * ctx.px(42)
            ecart = 290 if action in ("salut", "idee", "celebre") else 150              # bras leves : la bulle monte pour ne pas cacher les mains
            hauteur_max = tete[1] - unite * ecart - ctx.h * 0.13 - 2 * ctx.px(42)
            img_texte = bloc_texte(texte, GRAS, ctx.px(74), largeur_max, max(ctx.px(180), hauteur_max), (28, 24, 30), False, True, 1.05)
            bx = int((ctx.l - img_texte.size[0]) / 2 - ctx.px(42))
            by = int(max(ctx.h * 0.12, tete[1] - unite * ecart - img_texte.size[1] - 2 * ctx.px(42)))
            pointe = (int(tete[0]), int(tete[1] - unite * 138))
        _bulle(ctx, image, img_texte, bx, by, pointe, sortie_rebond((t - 0.45) / 0.5), 0.9)
    return image


SCENES = {"hook": scene_hook, "chiffre": scene_chiffre, "liste": scene_liste, "etape": scene_etape, "citation": scene_citation, "marque": scene_marque, "cta": scene_cta, "photo": scene_photo, "mosaique": scene_mosaique, "avant_apres": scene_avant_apres, "realisation": scene_realisation, "personnage": scene_personnage, "recherche": scene_recherche}


# --------------------------------------------------------------------------- habillage, transitions, assemblage

def habillage(ctx: Contexte, image: Image.Image, avancement: float, numero: int, total: int, sur_accent: bool = False) -> None:
    """Elements constants : nom en haut, barre de progression et compteur en bas (comme les slides)."""
    couleur = ctx.t["sur_accent"] if sur_accent else ctx.t["texte3"]
    marge = ctx.px(76)
    etiquette(ctx, image, f"× {ctx.marque} ×" if ctx.marque else "", marge, ctx.px(70), ctx.t["sur_accent"] if sur_accent else ctx.t["marque_hab"], 30)
    y = ctx.h - ctx.px(96)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((marge, y, ctx.l - marge, y + 4), radius=2, fill=(GOOGLE_BORD_CLAIR if ctx.google and not sur_accent else melanger(couleur, (0, 0, 0), 0.7)))
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
        for cle in ("texte", "sous_texte", "valeur", "suffixe", "libelle", "legende", "titre", "numero", "kicker", "auteur", "nom", "accroche", "bouton", "contact", "action", "accessoire", "forme"):
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
        if isinstance(brut.get("puces"), list):
            scene["puces"] = [sans_balises(str(x).replace("\u2014", "-")).strip()[:28] for x in brut["puces"] if str(x).strip()][:3]
        if isinstance(brut.get("items"), list):
            scene["items"] = [sans_balises(str(x).replace("—", "-")).strip()[:80] for x in brut["items"] if str(x).strip()][:5]
        scenes.append(scene)
    scenes = scenes[:12]
    perso_commun = next((int(b["perso"]) for b in (scenario or {}).get("scenes", []) if isinstance(b, dict) and b.get("type") == "personnage" and str(b.get("perso", "")).strip() in ("1", "2", "3", "4")), 1)
    rang_perso = 0
    for scene in scenes:
        if scene["type"] == "personnage":
            scene["action"] = scene.get("action", "").lower() if scene.get("action", "").lower() in personnage.ACTIONS else "explique"
            scene["accessoire"] = scene.get("accessoire", "").lower() if scene.get("accessoire", "").lower() in personnage.ACCESSOIRES else "aucun"
            scene["perso"], scene["cote"] = perso_commun, 1 if rang_perso % 2 == 0 else -1          # meme personnage dans toute la video, qui change de cote a chaque scene
            scene["forme"] = scene.get("forme", "").lower() if scene.get("forme", "").lower() in personnage.FORMES else "detaille"
            scene["puces"] = scene.get("puces", [])
            rang_perso += 1
        else:
            for cle in ("action", "accessoire", "forme", "puces"):
                scene.pop(cle, None)
        if scene["type"] == "recherche":
            scene["texte"] = sans_balises(scene.get("texte", ""))[:40]
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
        if scene["type"] == "recherche":
            tt = _temps_recherche(scene)
            evenements += [(debut + 0.35, "pop"), (debut + tt["carte"], "pop")] + [(debut + c, "pop") for c in tt["cartes"]] + [(debut + tt["badge"], "ding")]
        if scene["type"] == "personnage":
            evenements += [(debut + 0.5, "pop"), (debut + 1.05, "pop")]
            evenements += [(debut + 1.3 + 0.28 * k, "pop") for k in range(len(scene.get("puces", [])))]
            if scene.get("action") == "celebre":
                evenements.append((debut + 0.8, "ding"))
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
           images: list = None, logo: Image.Image = None, bpm: float = None, style: str = "") -> float:
    """Rend la video MP4 (image par image vers ffmpeg, bruitages inclus). Renvoie sa duree en secondes."""
    if format_video not in FORMATS:
        raise ErreurMotion("Format inconnu.")
    scenes = nettoyer_scenario(scenario)
    if not scenes:
        raise ErreurMotion("Le scenario ne contient aucune scene valide.")
    if bpm:
        scenes = caler_sur_tempo(scenes, bpm)
    ctx = Contexte(format_video, charte, marque, ville, images, logo, style if style in STYLES else "")
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
             url_musique: str = "", bpm: float = None, archiver=None, style: str = "") -> str:
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
            duree = rendre(scenario, charte, format_video, chemin, marque, ville, lambda p: etat.__setitem__("progression", round(p, 3)), musique, images, logo, bpm, style)
            if archiver:                       # archive automatique (hebergement + trace par client) : jamais bloquante pour la video elle-meme
                try:
                    etat["archive"] = archiver(chemin, round(duree, 1))
                except Exception as erreur_archive:
                    etat["archive_erreur"] = str(erreur_archive)[:200]
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
    archive = etat.get("archive")
    if archive and archive.get("url") and hebergement["etat"] in ("absent", "erreur"):
        hebergement.update(etat="pret", url=archive["url"], nom_distant=archive.get("nom_distant", ""), erreur="")      # deja hebergee par l'archive : pas de second envoi
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
