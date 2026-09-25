"""
Rendu de carrousels (images 1080x1350, format portrait Instagram/LinkedIn) avec Pillow :
l'IA fournit les textes des slides, ce module les dessine aux couleurs du client.
Trois mises en page : "plein" (fond couleur de marque), "clair" (fond clair, accent de marque),
"sombre" (fond sombre, accent de marque).
"""
import io
import os

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


def _decor(dessin, layout, p, marque):
    if layout == "plein":
        dessin.ellipse((LARGEUR - 380, -260, LARGEUR + 260, 380), fill=p["pastille"])
        dessin.ellipse((-200, HAUTEUR - 260, 260, HAUTEUR + 200), fill=p["pastille"])
    elif layout == "clair":
        dessin.rectangle((0, 0, LARGEUR, 26), fill=p["accent"])
        dessin.rectangle((0, HAUTEUR - 26, LARGEUR, HAUTEUR), fill=p["accent"])
    else:
        dessin.rectangle((0, 0, 16, HAUTEUR), fill=p["accent"])
        dessin.ellipse((LARGEUR - 300, HAUTEUR - 300, LARGEUR + 200, HAUTEUR + 200), outline=p["accent"], width=6)


def _pied(dessin, p, nom_client, numero, total):
    petite = _police("normal", 30)
    dessin.text((MARGE, HAUTEUR - 110), nom_client, font=petite, fill=p["doux"])
    if total:
        t = f"{numero}/{total}"
        dessin.text((LARGEUR - MARGE - dessin.textlength(t, font=petite), HAUTEUR - 110), t, font=petite, fill=p["doux"])


def _recadrer(photo: Image.Image) -> Image.Image:
    """Recadre une photo au format d'une slide (remplit tout le cadre, centre)."""
    photo = photo.convert("RGB")
    ratio = max(LARGEUR / photo.width, HAUTEUR / photo.height)
    photo = photo.resize((max(LARGEUR, round(photo.width * ratio)), max(HAUTEUR, round(photo.height * ratio))), Image.LANCZOS)
    gauche, haut = (photo.width - LARGEUR) // 2, (photo.height - HAUTEUR) // 2
    return photo.crop((gauche, haut, gauche + LARGEUR, haut + HAUTEUR))


def _photo_assombrie(photo: Image.Image, marque: tuple) -> Image.Image:
    """Photo de fond de couverture : voile sombre teinte de la couleur de marque, plus dense en bas pour le texte."""
    base = _recadrer(photo)
    voile = Image.new("RGB", (LARGEUR, HAUTEUR), _melange((10, 10, 14), marque, 0.25))
    masque = Image.linear_gradient("L").resize((LARGEUR, HAUTEUR))  # noir en haut -> blanc en bas
    masque = masque.point(lambda v: 130 + int(v * 0.45))
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
    logo: Image.Image = None, photo: Image.Image = None,
) -> Image.Image:
    """
    kind : "couverture" {titre, sous_titre}, "point" {numero, titre, texte}, "cta" {titre, texte, bouton}.
    logo : affiche sur la couverture et la derniere slide. photo : fond de la couverture (voile sombre, texte clair).
    """
    marque = _rgb(couleur)
    p = _palette(layout, marque)
    avec_photo = kind == "couverture" and photo is not None
    if avec_photo:
        p = {"fond": (12, 12, 16), "texte": (255, 255, 255), "doux": (225, 225, 230),
             "accent": _melange(marque, (255, 255, 255), 0.35 if _luminance(marque) > 0.5 else 0.6),
             "pastille": marque, "sur_accent": (255, 255, 255)}
        image = _photo_assombrie(photo, marque)
        d = ImageDraw.Draw(image)
    else:
        image = Image.new("RGB", (LARGEUR, HAUTEUR), p["fond"])
        d = ImageDraw.Draw(image)
        _decor(d, layout, p, marque)
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
        d.rectangle((MARGE, y + 20, MARGE + 140, y + 32), fill=p["accent"])
        if contenu.get("sous_titre"):
            police2, lignes2, pas2 = _texte_ajuste(d, contenu["sous_titre"], "normal", 44, 30, zone, 200, 1.3)
            y2 = y + 70
            for ligne in lignes2:
                d.text((MARGE, y2), ligne, font=police2, fill=p["doux"])
                y2 += pas2
        police_glisser = _police("gras", 36)
        d.text((MARGE, HAUTEUR - 200), "Faites glisser", font=police_glisser, fill=p["accent"])
        x_fleche = MARGE + int(d.textlength("Faites glisser", font=police_glisser)) + 24
        y_fleche = HAUTEUR - 200 + 30
        d.line((x_fleche, y_fleche, x_fleche + 46, y_fleche), fill=p["accent"], width=5)
        d.polygon([(x_fleche + 46, y_fleche - 13), (x_fleche + 62, y_fleche), (x_fleche + 46, y_fleche + 13)], fill=p["accent"])
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
        d.rounded_rectangle((MARGE, y_b, MARGE + largeur_b, y_b + 120), radius=60, fill=p["accent"])
        d.text((MARGE + 50, y_b + 30), bouton, font=pb, fill=p["sur_accent"])
    _pied(d, p, nom_client, numero, total)
    return image


def construire_carrousel(
    donnees: dict, layout: str, couleur: str, nom_client: str, logo: Image.Image = None, photo: Image.Image = None,
) -> list[Image.Image]:
    """donnees : {"couverture": {...}, "points": [{titre, texte}, ...], "cta": {...}}."""
    layout = layout if layout in LAYOUTS else "plein"
    points = donnees["points"]
    total = len(points) + 2
    slides = [dessiner_slide("couverture", donnees["couverture"], layout, couleur, nom_client, 1, total, logo, photo)]
    for i, pt in enumerate(points, start=1):
        slides.append(dessiner_slide("point", {**pt, "numero": i}, layout, couleur, nom_client, i + 1, total))
    slides.append(dessiner_slide("cta", donnees["cta"], layout, couleur, nom_client, total, total, logo))
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
