"""
Rendu de carrousels (images 1080x1350, format portrait Instagram/LinkedIn) avec Pillow :
l'IA fournit les textes des slides, ce module les dessine aux couleurs du client.
Trois mises en page : "plein" (fond couleur de marque), "clair" (fond clair, accent de marque),
"sombre" (fond sombre, accent de marque).
"""
import io
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

LARGEUR, HAUTEUR = 1080, 1350
MARGE = 90
DOSSIER_POLICES = os.path.join(os.path.dirname(__file__), "static", "polices")
# Polices : celles embarquees dans static/polices si presentes, sinon polices systeme courantes.
POLICES = {
    "gras": ["Poppins-Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
    "normal": ["Poppins-Regular.ttf", "segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"],
}
LAYOUTS = ("plein", "clair", "sombre")


def _police(style: str, taille: int) -> ImageFont.FreeTypeFont:
    for nom in POLICES[style]:
        for chemin in (os.path.join(DOSSIER_POLICES, nom), nom):
            try:
                return ImageFont.truetype(chemin, taille)
            except OSError:
                continue
    return ImageFont.load_default(taille)


def _rgb(couleur: str) -> tuple:
    couleur = (couleur or "#1f4e8c").lstrip("#")
    return tuple(int(couleur[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def _melange(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _lignes(dessin, texte: str, police, largeur_max: int) -> list[str]:
    lignes, courante = [], ""
    mots = []
    for mot in texte.split():
        # Typographie francaise : ":", "?", "!", ";" et "»" restent colles au mot precedent, "«" au suivant.
        if mots and (mot in (":", "?", "!", ";", "»") or mots[-1] == "«"):
            mots[-1] = f"{mots[-1]} {mot}"
        else:
            mots.append(mot)
    for mot in mots:
        essai = f"{courante} {mot}".strip()
        if dessin.textlength(essai, font=police) <= largeur_max:
            courante = essai
        else:
            if courante:
                lignes.append(courante)
            courante = mot
    if courante:
        lignes.append(courante)
    return lignes


def _texte_ajuste(dessin, texte, style, taille_max, taille_min, largeur, hauteur_max, interligne=1.22):
    """Plus grande taille de police pour laquelle le texte tient dans la boite. Renvoie (police, lignes, pas)."""
    for taille in range(taille_max, taille_min - 1, -2):
        police = _police(style, taille)
        lignes = _lignes(dessin, texte, police, largeur)
        pas = int(taille * interligne)
        if len(lignes) * pas <= hauteur_max:
            return police, lignes, pas
    police = _police(style, taille_min)
    pas = int(taille_min * interligne)
    lignes = _lignes(dessin, texte, police, largeur)[: max(1, hauteur_max // pas)]
    return police, lignes, pas


def _palette(layout: str, marque: tuple) -> dict:
    sombre_marque = _luminance(marque) < 0.55
    if layout == "plein":
        fond = marque
        texte = (255, 255, 255) if sombre_marque else (25, 25, 25)
        return {"fond": fond, "texte": texte, "doux": _melange(fond, texte, 0.75), "accent": texte,
                "pastille": _melange(fond, texte, 0.18), "sur_accent": fond}
    if layout == "clair":
        fond = (250, 247, 242)
        return {"fond": fond, "texte": (30, 32, 38), "doux": (110, 112, 120), "accent": marque,
                "pastille": marque, "sur_accent": (255, 255, 255) if sombre_marque else (25, 25, 25)}
    fond = _melange((18, 20, 26), marque, 0.12)
    accent = marque if _luminance(marque) > 0.32 else _melange(marque, (255, 255, 255), 0.45)
    return {"fond": fond, "texte": (245, 245, 247), "doux": (170, 172, 182), "accent": accent,
            "pastille": accent, "sur_accent": (20, 20, 20) if _luminance(accent) > 0.55 else (255, 255, 255)}


def _avec_secondaire(p: dict, secondaires: list = None) -> dict:
    """
    Ajoute a la palette la couleur d'accent secondaire (accent2 / sur_accent2) : la premiere couleur secondaire du
    client assez contrastee avec le fond, sinon l'accent d'origine. Sans couleur secondaire, rien ne change.
    En mise en page "plein", les cercles decoratifs prennent une teinte de la couleur secondaire.
    """
    p = dict(p)
    p["accent2"], p["sur_accent2"] = p["accent"], p["sur_accent"]
    valides = []
    for c in secondaires or []:
        try:
            valides.append(_rgb(c))
        except (ValueError, IndexError):
            continue
    for s in valides:
        if abs(_luminance(s) - _luminance(p["fond"])) >= 0.22:
            p["accent2"] = s
            p["sur_accent2"] = (25, 25, 25) if _luminance(s) > 0.6 else (255, 255, 255)
            break
    if valides and p["fond"] != (12, 12, 16):
        teinte = valides[1] if len(valides) > 1 else valides[0]
        if p["accent"] == (255, 255, 255) or p["accent"] == (25, 25, 25):  # mise en page "plein"
            p["pastille"] = _melange(p["fond"], teinte, 0.4)
    return p


STYLES_DECOR = ("cercles", "points", "diagonales", "vagues", "blocs", "cadre", "aucun")
# "auto" : un style different selon le client (stable pour un client donne), jamais "aucun".
STYLES_DECOR_AUTO = ("points", "diagonales", "vagues", "blocs", "cadre", "cercles")
LIBELLES_DECOR = {
    "auto": "Automatique (varie selon le client)", "cercles": "Cercles", "points": "Grille de points",
    "diagonales": "Bandes diagonales", "vagues": "Vagues", "blocs": "Blocs géométriques", "cadre": "Cadre fin",
    "aucun": "Aucun (épuré)",
}


def style_decor_effectif(decor: str, graine: int = 0) -> str:
    if decor in STYLES_DECOR:
        return decor
    return STYLES_DECOR_AUTO[int(graine or 0) % len(STYLES_DECOR_AUTO)]


def _teinte_decor(layout: str, p: dict, marque: tuple) -> tuple:
    """Couleur douce des formes decoratives, adaptee au fond de la mise en page."""
    if layout == "plein":
        return p["pastille"]
    if layout == "clair":
        return _melange(p["fond"], marque, 0.10)
    return _melange(p["fond"], p["accent"], 0.20)


def _decor(dessin, layout, p, marque, decor="auto", graine=0):
    style = style_decor_effectif(decor, graine)
    rng = random.Random((int(graine or 0) + 1) * 7919)
    tint = _teinte_decor(layout, p, marque)
    trait = p.get("accent2", p["accent"])
    miroir_x, miroir_y = rng.random() < 0.5, rng.random() < 0.5

    def pt(x, y):
        return (LARGEUR - x if miroir_x else x, HAUTEUR - y if miroir_y else y)

    # Reperes propres a la mise en page (discrets), sauf avec le cadre qui les remplace.
    if style != "cadre":
        if layout == "clair":
            dessin.rectangle((0, 0, LARGEUR, 26), fill=p["accent"])
            dessin.rectangle((0, HAUTEUR - 26, LARGEUR, HAUTEUR), fill=trait)
        elif layout == "sombre":
            dessin.rectangle((0, 0, 16, HAUTEUR), fill=p["accent"])

    if style == "cercles":
        if layout == "sombre":
            x, y = pt(LARGEUR - 50, HAUTEUR - 50)
            dessin.ellipse((x - 250, y - 250, x + 250, y + 250), outline=trait, width=6)
        else:
            x1, y1 = pt(LARGEUR - 60, 60)
            dessin.ellipse((x1 - 320, y1 - 320, x1 + 320, y1 + 320), fill=tint)
            x2, y2 = pt(30, HAUTEUR - 30)
            dessin.ellipse((x2 - 230, y2 - 230, x2 + 230, y2 + 230), fill=tint)
    elif style == "points":
        x0, y0 = pt(LARGEUR - 330, 40) if not miroir_x else pt(LARGEUR - 330, 40)
        for i in range(8):
            for j in range(8):
                x, y = pt(LARGEUR - 340 + i * 42, 50 + j * 42)
                dessin.ellipse((x - 6, y - 6, x + 6, y + 6), fill=tint if layout != "plein" else _melange(p["fond"], p["texte"], 0.28))
    elif style == "diagonales":
        for k in range(4):
            a, w = 110 + k * 95, 36
            dessin.polygon([pt(LARGEUR - a - w, 0), pt(LARGEUR - a, 0), pt(LARGEUR, a), pt(LARGEUR, a + w)], fill=tint)
    elif style == "vagues":
        for j in range(3):
            base = HAUTEUR - 70 - j * 52
            longueur = 520 + j * 140
            dephasage = rng.random() * math.pi * 2
            points = [pt(x, base + 26 * math.sin(x / longueur * 2 * math.pi + dephasage)) for x in range(0, LARGEUR + 20, 20)]
            points += [pt(LARGEUR, HAUTEUR), pt(0, HAUTEUR)]
            dessin.polygon(points, fill=_melange(p["fond"], tint, 1.0 - 0.28 * j))
    elif style == "blocs":
        for cx, cy, taille, poids in ((LARGEUR - 40, 70, 250, 1.0), (LARGEUR - 250, -10, 140, 0.6), (60, HAUTEUR - 60, 190, 0.7)):
            x, y = pt(cx, cy)
            dessin.polygon([(x, y - taille), (x + taille, y), (x, y + taille), (x - taille, y)], fill=_melange(p["fond"], tint, poids))
    elif style == "cadre":
        dessin.rounded_rectangle((34, 34, LARGEUR - 34, HAUTEUR - 34), radius=28, outline=_melange(p["fond"], trait, 0.55), width=4)
    # "aucun" : rien


def _pied(dessin, p, nom_client, numero, total):
    petite = _police("normal", 30)
    dessin.text((MARGE, HAUTEUR - 110), nom_client, font=petite, fill=p["doux"])
    if total:
        t = f"{numero}/{total}"
        dessin.text((LARGEUR - MARGE - dessin.textlength(t, font=petite), HAUTEUR - 110), t, font=petite, fill=p["doux"])


def _recadrer(photo: Image.Image, largeur: int = LARGEUR, hauteur: int = HAUTEUR) -> Image.Image:
    """Recadre une photo au format voulu (remplit tout le cadre, centre)."""
    photo = photo.convert("RGB")
    ratio = max(largeur / photo.width, hauteur / photo.height)
    photo = photo.resize((max(largeur, round(photo.width * ratio)), max(hauteur, round(photo.height * ratio))), Image.LANCZOS)
    gauche, haut = (photo.width - largeur) // 2, (photo.height - hauteur) // 2
    return photo.crop((gauche, haut, gauche + largeur, haut + hauteur))


def _photo_assombrie(photo: Image.Image, marque: tuple, voile_pourcent: int = None) -> Image.Image:
    """
    Photo de fond : voile sombre teinte de la couleur de marque. Par defaut plus dense en bas (couverture de carrousel) ;
    avec voile_pourcent (40 a 90), opacite quasi uniforme reglee par l'utilisateur (avis, texte a lire partout).
    """
    base = _recadrer(photo)
    voile = Image.new("RGB", (LARGEUR, HAUTEUR), _melange((10, 10, 14), marque, 0.25))
    if voile_pourcent is None:
        masque = Image.linear_gradient("L").resize((LARGEUR, HAUTEUR))  # noir en haut -> blanc en bas
        masque = masque.point(lambda v: 130 + int(v * 0.45))
    else:
        opacite = max(40, min(90, int(voile_pourcent))) / 100
        masque = Image.linear_gradient("L").resize((LARGEUR, HAUTEUR)).point(lambda v: int(255 * min(1.0, opacite - 0.06 + 0.12 * v / 255)))
    return Image.composite(voile, base, masque)


def _coller_logo(image: Image.Image, logo: Image.Image, haut: int) -> None:
    """Logo sur une pastille blanche arrondie, en haut a droite (lisible sur tous les fonds)."""
    logo = logo.convert("RGBA")
    ratio = min(220 / logo.width, 96 / logo.height)
    logo = logo.resize((max(1, round(logo.width * ratio)), max(1, round(logo.height * ratio))), Image.LANCZOS)
    marge = 18
    largeur, hauteur = logo.width + 2 * marge, logo.height + 2 * marge
    x = LARGEUR - MARGE - largeur
    pastille = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
    ImageDraw.Draw(pastille).rounded_rectangle((0, 0, largeur - 1, hauteur - 1), radius=24, fill=(255, 255, 255, 255))
    image.paste(pastille, (x, haut), pastille)
    image.paste(logo, (x + marge, haut + marge), logo)


def dessiner_slide(
    kind: str, contenu: dict, layout: str, couleur: str, nom_client: str, numero: int, total: int,
    logo: Image.Image = None, photo: Image.Image = None, secondaires: list = None, decor: str = "auto", graine: int = 0,
) -> Image.Image:
    """
    kind : "couverture" {titre, sous_titre}, "point" {numero, titre, texte}, "cta" {titre, texte, bouton}.
    logo : affiche sur la couverture et la derniere slide. photo : fond de la couverture (voile sombre, texte clair).
    """
    marque = _rgb(couleur)
    p = _avec_secondaire(_palette(layout, marque), secondaires)
    avec_photo = kind == "couverture" and photo is not None
    if avec_photo:
        p = {"fond": (12, 12, 16), "texte": (255, 255, 255), "doux": (225, 225, 230),
             "accent": _melange(marque, (255, 255, 255), 0.35 if _luminance(marque) > 0.5 else 0.6),
             "pastille": marque, "sur_accent": (255, 255, 255)}
        p = _avec_secondaire(p, secondaires)
        image = _photo_assombrie(photo, marque)
        d = ImageDraw.Draw(image)
    else:
        image = Image.new("RGB", (LARGEUR, HAUTEUR), p["fond"])
        d = ImageDraw.Draw(image)
        _decor(d, layout, p, marque, decor, graine)
    if logo is not None and kind in ("couverture", "cta"):
        _coller_logo(image, logo, 90)
    zone = LARGEUR - 2 * MARGE

    if kind == "couverture":
        d.text((MARGE, 190), nom_client.upper(), font=_police("gras", 34), fill=p["accent"] if layout != "plein" else p["doux"])
        police, lignes, pas = _texte_ajuste(d, contenu["titre"], "gras", 112, 60, zone, 640, 1.15)
        y = 330
        for ligne in lignes:
            d.text((MARGE, y), ligne, font=police, fill=p["texte"])
            y += pas
        d.rectangle((MARGE, y + 20, MARGE + 140, y + 32), fill=p["accent2"])
        if contenu.get("sous_titre"):
            police2, lignes2, pas2 = _texte_ajuste(d, contenu["sous_titre"], "normal", 44, 30, zone, 200, 1.3)
            y2 = y + 70
            for ligne in lignes2:
                d.text((MARGE, y2), ligne, font=police2, fill=p["doux"])
                y2 += pas2
        police_glisser = _police("gras", 36)
        d.text((MARGE, HAUTEUR - 200), "Faites glisser", font=police_glisser, fill=p["accent2"])
        x_fleche = MARGE + int(d.textlength("Faites glisser", font=police_glisser)) + 24
        y_fleche = HAUTEUR - 200 + 30
        d.line((x_fleche, y_fleche, x_fleche + 46, y_fleche), fill=p["accent2"], width=5)
        d.polygon([(x_fleche + 46, y_fleche - 13), (x_fleche + 62, y_fleche), (x_fleche + 46, y_fleche + 13)], fill=p["accent2"])
    elif kind == "point":
        rayon = 62
        d.ellipse((MARGE, 200, MARGE + 2 * rayon, 200 + 2 * rayon), fill=p["pastille"])
        num = str(contenu["numero"])
        pn = _police("gras", 70)
        d.text((MARGE + rayon - d.textlength(num, font=pn) / 2, 200 + rayon - 44), num, font=pn, fill=p["sur_accent"])
        police, lignes, pas = _texte_ajuste(d, contenu["titre"], "gras", 82, 50, zone, 330, 1.15)
        y = 380
        for ligne in lignes:
            d.text((MARGE, y), ligne, font=police, fill=p["texte"])
            y += pas
        police2, lignes2, pas2 = _texte_ajuste(d, contenu["texte"], "normal", 48, 32, zone, HAUTEUR - y - 260, 1.35)
        y += 30
        for ligne in lignes2:
            d.text((MARGE, y), ligne, font=police2, fill=p["doux"] if layout != "clair" else (70, 72, 80))
            y += pas2
    else:  # cta
        police, lignes, pas = _texte_ajuste(d, contenu["titre"], "gras", 96, 54, zone, 480, 1.15)
        y = 300
        for ligne in lignes:
            d.text((MARGE, y), ligne, font=police, fill=p["texte"])
            y += pas
        police2, lignes2, pas2 = _texte_ajuste(d, contenu.get("texte", ""), "normal", 46, 32, zone, 240, 1.35)
        y += 20
        for ligne in lignes2:
            d.text((MARGE, y), ligne, font=police2, fill=p["doux"])
            y += pas2
        pb = _police("gras", 44)
        bouton = contenu.get("bouton", "Prendre contact")
        largeur_b = int(d.textlength(bouton, font=pb)) + 100
        y_b = max(y + 60, 900)
        d.rounded_rectangle((MARGE, y_b, MARGE + largeur_b, y_b + 120), radius=60, fill=p["accent2"])
        d.text((MARGE + 50, y_b + 30), bouton, font=pb, fill=p["sur_accent2"])
    _pied(d, p, nom_client, numero, total)
    return image


def construire_carrousel(
    donnees: dict, layout: str, couleur: str, nom_client: str, logo: Image.Image = None, photo: Image.Image = None,
    secondaires: list = None, decor: str = "auto", graine: int = 0,
) -> list[Image.Image]:
    """donnees : {"couverture": {...}, "points": [{titre, texte}, ...], "cta": {...}}."""
    layout = layout if layout in LAYOUTS else "plein"
    points = donnees["points"]
    total = len(points) + 2
    slides = [dessiner_slide("couverture", donnees["couverture"], layout, couleur, nom_client, 1, total, logo, photo, secondaires, decor, graine)]
    for i, pt in enumerate(points, start=1):
        slides.append(dessiner_slide("point", {**pt, "numero": i}, layout, couleur, nom_client, i + 1, total, secondaires=secondaires, decor=decor, graine=graine))
    slides.append(dessiner_slide("cta", donnees["cta"], layout, couleur, nom_client, total, total, logo, secondaires=secondaires, decor=decor, graine=graine))
    return slides


def en_png(image: Image.Image) -> bytes:
    tampon = io.BytesIO()
    image.save(tampon, format="PNG", optimize=True)
    return tampon.getvalue()


def en_pdf(images: list[Image.Image]) -> bytes:
    """Assemble les slides en un PDF (une page par slide) : format "document" des carrousels LinkedIn."""
    pages = [i.convert("RGB") for i in images]
    tampon = io.BytesIO()
    pages[0].save(tampon, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return tampon.getvalue()


OR_ETOILES = (250, 190, 20)


def _etoile(dessin, centre_x: int, centre_y: int, rayon: int, couleur) -> None:
    import math
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = rayon if i % 2 == 0 else rayon * 0.42
        points.append((centre_x + r * math.cos(angle), centre_y + r * math.sin(angle)))
    dessin.polygon(points, fill=couleur)


def dessiner_avis(
    texte: str, auteur: str, note: int, layout: str, couleur: str, nom_client: str, logo: Image.Image = None,
    secondaires: list = None, photo: Image.Image = None, voile: int = 68, decor: str = "auto", graine: int = 0,
) -> Image.Image:
    """
    Visuel de mise en avant d'un avis client (1080x1350) : guillemet, etoiles, citation, auteur, nom du client.
    photo : fond photo assombri (voile en %, texte clair), sans decor.
    """
    marque = _rgb(couleur)
    layout = layout if layout in LAYOUTS else "plein"
    p = _avec_secondaire(_palette(layout, marque), secondaires)
    if photo is not None:
        p = _avec_secondaire({
            "fond": (12, 12, 16), "texte": (255, 255, 255), "doux": (225, 225, 230),
            "accent": _melange(marque, (255, 255, 255), 0.35 if _luminance(marque) > 0.5 else 0.6),
            "pastille": marque, "sur_accent": (255, 255, 255),
        }, secondaires)
        image = _photo_assombrie(photo, marque, voile)
        d = ImageDraw.Draw(image)
    else:
        image = Image.new("RGB", (LARGEUR, HAUTEUR), p["fond"])
        d = ImageDraw.Draw(image)
        _decor(d, layout, p, marque, decor, graine)
    if logo is not None:
        _coller_logo(image, logo, 90)
    zone = LARGEUR - 2 * MARGE

    d.text((MARGE, 130), "\u201c", font=_police("gras", 260), fill=p["accent2"])
    note = max(1, min(5, int(note or 5)))
    for i in range(5):
        _etoile(d, MARGE + 34 + i * 76, 470, 34, OR_ETOILES if i < note else _melange(p["fond"], p["texte"], 0.25))

    citation = " ".join((texte or "").split())
    police, lignes, pas = _texte_ajuste(d, citation, "normal", 68, 34, zone, 500, 1.32)
    if len(_lignes(d, citation, police, zone)) > len(lignes):
        lignes[-1] = lignes[-1].rstrip(" ,;:.") + "\u2026"
    y = 540
    for ligne in lignes:
        d.text((MARGE, y), ligne, font=police, fill=p["texte"])
        y += pas
    y_auteur = min(max(y + 40, 1130), HAUTEUR - 230)
    d.rectangle((MARGE, y_auteur - 26, MARGE + 110, y_auteur - 16), fill=p["accent2"])
    d.text((MARGE, y_auteur), auteur or "Un client", font=_police("gras", 48), fill=p["texte"])
    d.text((MARGE, y_auteur + 64), "Avis Google", font=_police("normal", 34), fill=p["doux"])
    d.text((MARGE, HAUTEUR - 110), nom_client, font=_police("normal", 30), fill=p["doux"])
    return image
