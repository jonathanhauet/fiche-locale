"""
Titre sur photo (1080x1350 portrait ou 1080x1080 carre) dessine avec Pillow : le texte est net et sans faute
(contrairement a du texte genere par une IA d'images), aux couleurs, au logo et au nom du client.

Cinq styles (italique + etiquette, bandeau plein, verre depoli, sobre, encadre), cinq polices, mots mis en couleur,
et de petits ajouts facultatifs : pastille (« 1/5 », « Nouveau »...), pictogramme, fleche « Glissez », bouton, signature,
cadre. Tous les reglages sont facultatifs : sans rien toucher, on obtient le style italique avec etiquette.
"""
import math
import random

from PIL import Image, ImageDraw, ImageFilter

from . import carrousel_visuel as cv

LARGEUR = 1080
HAUTEUR_PORTRAIT = 1350
HAUTEUR_CARRE = 1080
MARGE = 70

STYLES = {
    "italique": "Italique + étiquette", "bandeau": "Bandeau plein", "verre": "Verre dépoli", "sobre": "Sobre",
    "encadre": "Encadré",
}
POLICES = {
    "italique": "Italique épais", "sobre": "Sobre", "condensee": "Condensée", "elegante": "Élégante", "manuscrite": "Manuscrite",
}
FICHIERS_POLICES = {
    "italique": ["Poppins-ExtraBoldItalic.ttf", "Poppins-Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
    "sobre": ["Poppins-Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
    "condensee": ["BebasNeue-Regular.ttf", "Poppins-Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "elegante": ["DMSerifDisplay-Regular.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "manuscrite": ["Kalam-Bold.ttf", "segoepr.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
}
FACTEUR_TAILLE = {"italique": 1.0, "sobre": 0.92, "condensee": 1.45, "elegante": 1.0, "manuscrite": 1.0}
INTERLIGNE = {"italique": 1.06, "sobre": 1.12, "condensee": 0.98, "elegante": 1.08, "manuscrite": 1.12}
POLICE_PAR_STYLE = {"italique": "italique", "bandeau": "sobre", "verre": "elegante", "sobre": "sobre", "encadre": "condensee"}
MAJUSCULES_PAR_POLICE = {"italique": True, "sobre": False, "condensee": True, "elegante": False, "manuscrite": False}
PICTOS = {
    "aucun": "Aucun", "etoile": "Étoile", "coche": "Coche", "coeur": "Cœur", "eclair": "Éclair", "fleche": "Flèche",
}
CADRES = {"aucun": "Aucun", "fin": "Fin", "epais": "Épais"}

DEFAUTS = {
    "style": "italique", "position": "bas", "format": "portrait", "voile": 55, "police": "auto", "taille": 100,
    "majuscules": "auto", "mots": [], "surlignage": "couleur", "cadre": "aucun", "pastille": "", "bouton": "",
    "picto": "aucun", "fleche": False, "signature": False, "numeroter": False,
}


def normaliser_reglages(brut: dict) -> dict:
    """Reglages valides (valeur inconnue ou hors limites -> valeur par defaut) : ne fait jamais planter le rendu."""
    brut = brut if isinstance(brut, dict) else {}
    r = dict(DEFAUTS)

    def choix(cle, autorises):
        if brut.get(cle) in autorises:
            r[cle] = brut[cle]

    choix("style", STYLES)
    choix("position", ("bas", "haut", "centre"))
    choix("format", ("portrait", "carre"))
    choix("police", ("auto", *POLICES))
    choix("majuscules", ("auto", "oui", "non"))
    choix("surlignage", ("couleur", "marqueur"))
    choix("cadre", CADRES)
    choix("picto", PICTOS)
    for cle, mini, maxi in (("voile", 20, 90), ("taille", 70, 130)):
        try:
            r[cle] = max(mini, min(maxi, int(brut.get(cle, r[cle]))))
        except (TypeError, ValueError):
            pass
    for cle, longueur in (("pastille", 14), ("bouton", 28)):
        r[cle] = " ".join(str(brut.get(cle) or "").split())[:longueur]
    for cle in ("fleche", "signature", "numeroter"):
        r[cle] = bool(brut.get(cle))
    mots = brut.get("mots")
    if isinstance(mots, list):
        r["mots"] = sorted({int(m) for m in mots if isinstance(m, (int, float)) and 0 <= int(m) < 40})[:8]
    return r


def _police(cle: str, taille: int):
    import os
    for nom in FICHIERS_POLICES[cle]:
        for chemin in (os.path.join(cv.DOSSIER_POLICES, nom), nom):
            try:
                return cv.ImageFont.truetype(chemin, taille)
            except OSError:
                continue
    return cv.ImageFont.load_default(taille)


def decouper_mots(texte: str) -> list[str]:
    """Mots du titre ; « : », « ? », « ! », « ; » et « » » restent colles au mot precedent (typographie francaise).
    Meme regle cote navigateur, pour que les indices de mots mis en couleur correspondent."""
    mots = []
    for mot in (texte or "").split():
        if mots and mot in (":", "?", "!", ";", "»"):
            mots[-1] += " " + mot
        else:
            mots.append(mot)
    return mots


def _eclaircir(couleur: tuple, luminance_min: float = 0.55) -> tuple:
    """Eclaircit une couleur jusqu'a une luminance suffisante pour rester lisible sur une photo sombre."""
    for t in (0, 0.2, 0.35, 0.5, 0.65, 0.8):
        c = cv._melange(couleur, (255, 255, 255), t)
        if cv._luminance(c) >= luminance_min:
            return c
    return c


def _couleur_accent(marque: tuple, secondaires: list) -> tuple:
    for s in secondaires or []:
        try:
            return _rgb_sur_sombre(cv._rgb(s))
        except (ValueError, IndexError):
            continue
    return _eclaircir(marque)


def _rgb_sur_sombre(c: tuple) -> tuple:
    return c if cv._luminance(c) >= 0.35 else _eclaircir(c, 0.4)


def _texte_sur(fond: tuple) -> tuple:
    return (20, 20, 24) if cv._luminance(fond) > 0.62 else (255, 255, 255)


def _lignes_mots(d, mots, police, largeur_max):
    espace = d.textlength(" ", font=police)
    lignes, courante, largeur = [], [], 0.0
    for i, mot in enumerate(mots):
        w = d.textlength(mot, font=police)
        if courante and largeur + espace + w > largeur_max:
            lignes.append(courante)
            courante, largeur = [], 0.0
        largeur += (espace if courante else 0) + w
        courante.append((i, mot, w))
    if courante:
        lignes.append(courante)
    return lignes, espace


def _ajuster(d, mots, cle_police, taille_max, largeur_max, hauteur_max, max_lignes):
    """Plus grande taille pour laquelle le titre tient (largeur, hauteur, nombre de lignes). Renvoie (police, lignes, espace, pas)."""
    interligne = INTERLIGNE[cle_police]
    for taille in range(max(taille_max, 52), 51, -4):
        police = _police(cle_police, taille)
        lignes, espace = _lignes_mots(d, mots, police, largeur_max)
        pas = int(taille * interligne)
        if len(lignes) <= max_lignes and len(lignes) * pas <= hauteur_max:
            return police, lignes, espace, pas
    police = _police(cle_police, 52)
    lignes, espace = _lignes_mots(d, mots, police, largeur_max)
    return police, lignes[:max_lignes], espace, int(52 * interligne)


def _dessiner_lignes(d, lignes, x, y, police, espace, pas, couleur, surlignes, mode, accent, largeur_zone=None,
                     centre=False, ombre=True):
    """Dessine le titre mot a mot : les mots choisis prennent la couleur d'accent (ou un fond « marqueur »)."""
    for ligne in lignes:
        total = sum(w for _, _, w in ligne) + espace * (len(ligne) - 1)
        cx = x + ((largeur_zone - total) / 2 if centre and largeur_zone else 0)
        for i, mot, w in ligne:
            en_avant = i in surlignes
            couleur_mot = couleur
            if en_avant and mode == "marqueur":
                d.rectangle((cx - 12, y + pas * 0.10, cx + w + 12, y + pas * 0.96), fill=accent + (255,))
                couleur_mot = _texte_sur(accent)
            elif en_avant:
                couleur_mot = accent
            if ombre and not (en_avant and mode == "marqueur"):
                d.text((cx + 3, y + 4), mot, font=police, fill=(0, 0, 0, 110))
            d.text((cx, y), mot, font=police, fill=couleur_mot + (255,))
            cx += w + espace
        y += pas
    return y


# --------------------------------------------------------------------------- pictogrammes et ajouts

def _picto(d, cle, cx, cy, r, couleur):
    if cle == "etoile":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.42
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        d.polygon(pts, fill=couleur)
    elif cle == "coche":
        d.line([(cx - r * 0.8, cy), (cx - r * 0.2, cy + r * 0.6), (cx + r * 0.85, cy - r * 0.6)], fill=couleur, width=int(r * 0.32), joint="curve")
    elif cle == "coeur":
        rr = r * 0.52
        d.ellipse((cx - r, cy - r * 0.85, cx, cy + rr * 0.15), fill=couleur)
        d.ellipse((cx, cy - r * 0.85, cx + r, cy + rr * 0.15), fill=couleur)
        d.polygon([(cx - r * 0.97, cy - r * 0.15), (cx + r * 0.97, cy - r * 0.15), (cx, cy + r * 0.95)], fill=couleur)
    elif cle == "eclair":
        d.polygon([(cx + r * 0.2, cy - r), (cx - r * 0.65, cy + r * 0.1), (cx - r * 0.05, cy + r * 0.1),
                   (cx - r * 0.25, cy + r), (cx + r * 0.7, cy - r * 0.2), (cx + r * 0.1, cy - r * 0.2)], fill=couleur)
    elif cle == "fleche":
        d.line((cx - r, cy, cx + r * 0.6, cy), fill=couleur, width=int(r * 0.3))
        d.polygon([(cx + r * 0.3, cy - r * 0.55), (cx + r, cy), (cx + r * 0.3, cy + r * 0.55)], fill=couleur)


def _pastille(largeur, hauteur, texte, fond, accent_texte):
    """Tampon rond legerement incline : texte court sur fond colore."""
    diam = 210
    tampon = Image.new("RGBA", (diam + 20, diam + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(tampon)
    d.ellipse((10, 10, diam + 10, diam + 10), fill=fond + (255,))
    d.ellipse((24, 24, diam - 4, diam - 4), outline=accent_texte + (150,), width=4)
    cle = "italique"
    mots = texte.upper().split()
    for taille in range(70, 27, -3):
        police = _police(cle, taille)
        lignes = [" ".join(mots)] if len(mots) == 1 or d.textlength(" ".join(mots), font=police) <= diam - 60 else [" ".join(mots[: len(mots) // 2 or 1]), " ".join(mots[len(mots) // 2 or 1:])]
        if all(d.textlength(l, font=police) <= diam - 56 for l in lignes) and len(lignes) * taille * 1.1 <= diam - 60:
            break
    pas = int(taille * 1.1)
    y = 10 + (diam - len(lignes) * pas) / 2 - taille * 0.06
    for l in lignes:
        d.text((10 + (diam - d.textlength(l, font=police)) / 2, y), l, font=police, fill=accent_texte + (255,))
        y += pas
    return tampon.rotate(9, resample=Image.BICUBIC, expand=True)


def dessiner(
    photo: Image.Image, titre: str, sous_titre: str, couleur: str, logo: Image.Image = None, secondaires: list = None,
    reglages: dict = None, nom_client: str = "", numero: int = None, total: int = 1,
) -> Image.Image:
    r = normaliser_reglages(reglages)
    largeur = LARGEUR
    hauteur = HAUTEUR_PORTRAIT if r["format"] == "portrait" else HAUTEUR_CARRE
    marque = cv._rgb(couleur)
    accent = _couleur_accent(marque, secondaires)
    style, position = r["style"], r["position"]
    opacite = r["voile"] / 100

    cle_police = POLICE_PAR_STYLE[style] if r["police"] == "auto" else r["police"]
    majuscules = MAJUSCULES_PAR_POLICE[cle_police] if r["majuscules"] == "auto" else r["majuscules"] == "oui"
    texte_titre = (titre or "").strip()
    mots = decouper_mots(texte_titre.upper() if majuscules else texte_titre)
    sous = (sous_titre or "").strip()
    sous = sous.upper() if style in ("italique", "bandeau", "encadre") else sous
    surlignes = set(r["mots"])

    texte_pastille = r["pastille"]
    if r["numeroter"] and total > 1 and numero:
        texte_pastille = f"{numero}/{total}"
    a_pastille, a_picto = bool(texte_pastille), r["picto"] != "aucun"
    reserve_bas = (150 if (r["bouton"] or r["fleche"]) else 0) + (58 if r["signature"] else 0)
    haut_min = 170 + (250 if (a_pastille or a_picto) else 0)
    bas_max = hauteur - 110 - reserve_bas

    image = cv._recadrer(photo, largeur, hauteur)

    def voile(masque_opacite_haut, masque_opacite_bas):
        """Degrade du cote du texte (bas ou haut), ou uniforme au centre."""
        nonlocal image
        teinte = Image.new("RGB", (largeur, hauteur), cv._melange((8, 8, 12), marque, 0.30))
        if position == "centre":
            masque = Image.new("L", (largeur, hauteur), int(255 * opacite * 0.75))
        else:
            degrade = Image.linear_gradient("L").resize((largeur, hauteur))
            if position == "haut":
                degrade = degrade.transpose(Image.FLIP_TOP_BOTTOM)
            masque = degrade.point(lambda v: int(255 * opacite * max(0.0, (v / 255 - 0.25) / 0.75) ** 0.8))
        image = Image.composite(teinte, image, masque)

    def voile_uniforme(facteur):
        nonlocal image
        teinte = Image.new("RGB", (largeur, hauteur), cv._melange((8, 8, 12), marque, 0.30))
        image = Image.composite(teinte, image, Image.new("L", (largeur, hauteur), int(255 * opacite * facteur)))

    def bloc_y(hauteur_bloc):
        if position == "haut":
            return haut_min
        if position == "centre":
            return max(haut_min, (haut_min + bas_max - hauteur_bloc) // 2)
        return max(haut_min, bas_max - hauteur_bloc)

    calque = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    taille_max = int(132 * r["taille"] / 100 * FACTEUR_TAILLE[cle_police])
    blanc = (255, 255, 255)

    if style == "italique":
        voile(0, 1)
        zone = largeur - 2 * MARGE
        police, lignes, espace, pas = _ajuster(d, mots, cle_police, taille_max, zone, 560, 5)
        h_eti = 92 if sous else 0
        y = bloc_y(len(lignes) * pas + (h_eti + 18 if sous else 0))
        y = _dessiner_lignes(d, lignes, MARGE, y, police, espace, pas, blanc, surlignes, r["surlignage"], accent)
        if sous:
            police_s = _police("italique", 58)
            fond_eti = accent if secondaires else marque
            fond_eti = fond_eti if abs(cv._luminance(fond_eti) - cv._luminance(marque)) > 0 else marque
            y += 18
            largeur_eti = min(zone, int(d.textlength(sous, font=police_s)) + 64)
            d.rectangle((MARGE, y, MARGE + largeur_eti, y + h_eti), fill=fond_eti + (255,))
            d.text((MARGE + 32, y + 12), sous, font=police_s, fill=_texte_sur(fond_eti) + (255,))

    elif style == "sobre":
        voile(0, 1)
        zone = largeur - 2 * MARGE
        police, lignes, espace, pas = _ajuster(d, mots, cle_police, taille_max, zone, 560, 5)
        h_sous = 84 if sous else 0
        y = bloc_y(len(lignes) * pas + h_sous + 34)
        d.rectangle((MARGE, y, MARGE + 130, y + 9), fill=accent + (255,))
        y = _dessiner_lignes(d, lignes, MARGE, y + 30, police, espace, pas, blanc, surlignes, r["surlignage"], accent)
        if sous:
            d.text((MARGE, y + 8), sous, font=_police("sobre", 52), fill=accent + (255,))

    elif style == "bandeau":
        voile_uniforme(0.45)
        zone = largeur - 2 * MARGE - 20
        police, lignes, espace, pas = _ajuster(d, mots, cle_police, int(taille_max * 0.92), zone, 480, 4)
        h_bande = len(lignes) * pas + 90
        y0 = bloc_y(h_bande + (0 if not sous else 46))
        y_bande = y0 + (46 if sous else 0)
        couleur_texte = _texte_sur(marque)
        d.rectangle((0, y_bande, largeur, y_bande + h_bande), fill=marque + (238,))
        if sous:
            police_s = _police("sobre", 40)
            largeur_eti = int(d.textlength(sous, font=police_s)) + 56
            fond_eti = accent
            d.rectangle((MARGE, y_bande - 58, MARGE + largeur_eti, y_bande), fill=fond_eti + (255,))
            d.text((MARGE + 28, y_bande - 51), sous, font=police_s, fill=_texte_sur(fond_eti) + (255,))
        accent_bande = accent if abs(cv._luminance(accent) - cv._luminance(marque)) > 0.3 else _texte_sur(marque)
        _dessiner_lignes(d, lignes, MARGE + 10, y_bande + 45, police, espace, pas, couleur_texte, surlignes,
                         "marqueur" if r["surlignage"] == "marqueur" else "couleur", accent_bande, ombre=False)

    elif style == "verre":
        voile_uniforme(0.35)
        zone = largeur - 2 * (MARGE + 60)
        police, lignes, espace, pas = _ajuster(d, mots, cle_police, int(taille_max * 0.95), zone, 470, 4)
        h_sous = 76 if sous else 0
        h_panneau = len(lignes) * pas + 100 + h_sous
        y = bloc_y(h_panneau)
        boite = (MARGE - 10, y, largeur - MARGE + 10, y + h_panneau)
        flou = image.crop(boite).filter(ImageFilter.GaussianBlur(26))
        teinte = Image.new("RGB", flou.size, (12, 12, 18))
        flou = Image.composite(teinte, flou, Image.new("L", flou.size, 110))
        masque = Image.new("L", flou.size, 0)
        ImageDraw.Draw(masque).rounded_rectangle((0, 0, flou.width - 1, flou.height - 1), radius=44, fill=255)
        image.paste(flou, (boite[0], boite[1]), masque)
        ImageDraw.Draw(calque).rounded_rectangle(boite, radius=44, outline=(255, 255, 255, 70), width=3)
        yy = y + 42
        if sous:
            police_s = _police("sobre", 38)
            largeur_eti = int(d.textlength(sous, font=police_s)) + 48
            d.rounded_rectangle((MARGE + 40, yy, MARGE + 40 + largeur_eti, yy + 56), radius=28, fill=accent + (255,))
            d.text((MARGE + 64, yy + 8), sous, font=police_s, fill=_texte_sur(accent) + (255,))
            yy += 76
        _dessiner_lignes(d, lignes, MARGE + 40, yy, police, espace, pas, blanc, surlignes, r["surlignage"], accent, ombre=False)

    else:  # encadre
        voile_uniforme(0.6)
        zone = largeur - 2 * 190
        police, lignes, espace, pas = _ajuster(d, mots, cle_police, int(taille_max * 0.95), zone, 440, 4)
        h_boite = len(lignes) * pas + 110
        y = bloc_y(h_boite + (30 if sous else 0))
        boite = (110, y, largeur - 110, y + h_boite)
        ImageDraw.Draw(calque).rectangle(boite, fill=(8, 8, 12, 105))
        d = ImageDraw.Draw(calque)
        d.rectangle(boite, outline=blanc + (255,), width=7)
        _dessiner_lignes(d, lignes, 190, y + 55, police, espace, pas, blanc, surlignes, r["surlignage"], accent,
                         largeur_zone=zone, centre=True, ombre=False)
        if sous:
            police_s = _police("sobre", 40)
            largeur_eti = int(d.textlength(sous, font=police_s)) + 56
            x0 = (largeur - largeur_eti) // 2
            d.rectangle((x0, y + h_boite - 30, x0 + largeur_eti, y + h_boite + 34), fill=marque + (255,))
            d.text((x0 + 28, y + h_boite - 22), sous, font=police_s, fill=_texte_sur(marque) + (255,))

    # ---------------------------------------------------------------- ajouts
    d = ImageDraw.Draw(calque)
    if r["cadre"] != "aucun":
        if r["cadre"] == "fin":
            d.rectangle((26, 26, largeur - 26, hauteur - 26), outline=(255, 255, 255, 200), width=4)
        else:
            d.rectangle((0, 0, largeur - 1, hauteur - 1), outline=accent + (255,), width=22)
            d.rectangle((26, 26, largeur - 27, hauteur - 27), outline=(255, 255, 255, 190), width=3)

    x_haut = MARGE
    if a_pastille:
        tampon = _pastille(largeur, hauteur, texte_pastille, marque, _texte_sur(marque))
        calque.alpha_composite(tampon, (x_haut - 10, 60))
        x_haut += tampon.width + 10
    if a_picto:
        d.ellipse((x_haut, 88, x_haut + 130, 218), fill=accent + (255,))
        _picto(d, r["picto"], x_haut + 65, 153, 40, _texte_sur(accent) + (255,))

    y_bas = hauteur - 110
    if r["signature"]:
        police_sig = _police("sobre", 30)
        signature = (nom_client or "").strip()
        if signature:
            d.text((MARGE, hauteur - 78), signature, font=police_sig, fill=(255, 255, 255, 215))
            y_bas = hauteur - 122
    if r["bouton"]:
        police_b = _police("sobre", 42)
        texte_b = r["bouton"]
        largeur_b = int(d.textlength(texte_b, font=police_b)) + 90
        fond_b = accent
        d.rounded_rectangle((MARGE, y_bas - 96, MARGE + largeur_b, y_bas), radius=48, fill=fond_b + (255,))
        d.text((MARGE + 45, y_bas - 72), texte_b, font=police_b, fill=_texte_sur(fond_b) + (255,))
    if r["fleche"]:
        police_f = _police("sobre", 40)
        largeur_t = int(d.textlength("Glissez", font=police_f))
        x_f = largeur - MARGE - largeur_t - 90
        if position == "haut" and logo is not None:  # le logo occupe le coin bas droit : la fleche se place apres le bouton
            x_f = MARGE + (int(d.textlength(r["bouton"], font=_police("sobre", 42))) + 90 + 50 if r["bouton"] else 0)
        d.text((x_f, y_bas - 66), "Glissez", font=police_f, fill=(255, 255, 255, 255))
        y_a = y_bas - 42
        d.line((x_f + largeur_t + 16, y_a, x_f + largeur_t + 62, y_a), fill=accent + (255,), width=6)
        d.polygon([(x_f + largeur_t + 62, y_a - 14), (x_f + largeur_t + 80, y_a), (x_f + largeur_t + 62, y_a + 14)], fill=accent + (255,))

    image = Image.alpha_composite(image.convert("RGBA"), calque).convert("RGB")
    if logo is not None:
        cv._coller_logo(image, logo, 70 if position != "haut" else hauteur - 210)
    return image


def apercus_styles(photo, titre, sous_titre, couleur, logo, secondaires, reglages, nom_client="", largeur_apercu=360):
    """Un petit apercu par style (avec les autres reglages en cours) : pour choisir le style d'un coup d'oeil."""
    sorties = []
    for cle, libelle in STYLES.items():
        image = dessiner(photo, titre, sous_titre, couleur, logo, secondaires, {**(reglages or {}), "style": cle}, nom_client)
        sorties.append((cle, libelle, image.resize((largeur_apercu, round(largeur_apercu * image.height / image.width)), Image.LANCZOS)))
    return sorties
