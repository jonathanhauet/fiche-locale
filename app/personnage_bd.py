"""
Personnage BD : ta tete (ou celle d'un client) en version bande dessinee, utilisee comme personnage principal des videos de motion design.
Gemini dessine la tete d'apres les photos de reference de la fiche (voir models.PhotoReferenceClient) en trois expressions de base identiques a une
pres : bouche fermee, bouche ouverte (il parle) et yeux fermes (il cligne). On peut ensuite lui ajouter des emotions (content, surpris, il reflechit,
inquiet, clin d'oeil, confiant), chacune en deux images (bouche fermee et bouche ouverte) pour qu'il puisse parler avec cette emotion. Le fond vert est
retire (voir detourage.py), toutes les images sont alignees sur la premiere, puis personnage.dessiner_geo() pose la tete sur le corps geometrique et
choisit l'image selon l'emotion de la scene et la parole. Une image coute quelques centimes ; tout se fait une seule fois.
"""

import io

from PIL import Image, ImageOps

from . import detourage

EXPRESSIONS = ("ferme", "ouverte", "yeux")                       # expressions de base (toujours presentes)
LIBELLES_EXPRESSIONS = {"ferme": "Bouche fermée", "ouverte": "Il parle", "yeux": "Il cligne"}
# emotion -> (description bouche fermee, description bouche ouverte) ; chaque emotion donne deux images : « <emotion> » et « <emotion>_ouverte »
EMOTIONS = {
    "joie": ("a big joyful smile with the mouth closed, cheeks raised, eyes bright and happy, eyebrows raised",
             "a big joyful open-mouth smile showing teeth, eyes bright and happy, eyebrows raised"),
    "surpris": ("eyes wide open, eyebrows raised high, the mouth closed in a small round 'o' of surprise",
                "eyes wide open, eyebrows raised high, the mouth wide open in surprise"),
    "reflechit": ("a thoughtful look: one eyebrow raised, eyes looking up and to the side, the mouth closed and slightly crooked",
                  "a thoughtful look: one eyebrow raised, eyes looking up and to the side, the mouth slightly open as if explaining"),
    "inquiet": ("a worried look: eyebrows tilted up in the middle, eyes slightly wide, a small tense closed mouth",
                "a worried look: eyebrows tilted up in the middle, eyes slightly wide, the mouth open as if saying 'careful'"),
    "clin": ("a playful wink: one eye closed, a confident smirk with the mouth closed",
             "a playful wink: one eye closed, the mouth open in a cheerful smile"),
    "confiant": ("a confident, reassuring look: eyebrows slightly raised, a smirk on one side, the mouth closed, chin slightly up",
                 "a confident, reassuring look, the mouth open as if explaining something with certainty"),
}
LIBELLES_EMOTIONS = {"joie": "Content", "surpris": "Surpris", "reflechit": "Il réfléchit", "inquiet": "Inquiet", "clin": "Clin d'œil", "confiant": "Confiant"}
EMOTIONS_PAR_DEFAUT = ("joie", "surpris", "reflechit", "inquiet")
EMOTION_PAR_ACTION = {"salut": "joie", "pointe": "confiant", "idee": "surpris", "celebre": "joie", "reflechit": "reflechit"}      # emotion choisie quand la scene n'en precise pas
DELAI_EMOTION = 0.9                                                # le personnage arrive neutre, puis reagit
STYLES = {
    "plat": "modern flat vector illustration, like the friendly corporate illustrations of big tech companies: simple rounded shapes, clean outlines of uniform thickness, flat colors with only a few soft shading shapes, big expressive eyes, minimal details",
    "bd": "franco-belgian comic book style (ligne claire): clean uniform dark outlines, flat colors, a simple expressive face, slightly caricatured but flattering",
}
LIBELLES_STYLES = {"plat": "Illustration plate moderne (comme les illustrations Google)", "bd": "Bande dessinée (ligne claire)"}
STYLE_DEFAUT = "plat"
COTE_MAX = 640                                    # taille des images gardees (la tete est reduite a l'ecran)

CADRAGE = (
    "HEAD AND NECK ONLY, front view, facing the camera, perfectly centered, short neck, no shoulders and no clothes visible, the whole head and hair fully inside the frame with a margin around it. "
    "Background: ONE single flat pure green color (#00FF00) filling the entire image, no shadow, no gradient, no floor, no other object, no text anywhere. "
)
EXPRESSION_BASE = "a calm, friendly smile with the mouth closed, eyes open and looking at the camera."
EXPRESSIONS_PROMPT = {
    "ouverte": "the mouth OPEN as if speaking cheerfully (lips apart, a hint of teeth), eyes open and looking at the camera.",
    "yeux": "eyes CLOSED as in a blink (simple curved lines), mouth closed in a calm smile.",
}


def noms_emotion(emotion: str) -> tuple:
    """Noms des deux images d'une emotion : (bouche fermee, bouche ouverte)."""
    return emotion, f"{emotion}_ouverte"


def tous_les_noms() -> list:
    """Tous les noms d'images possibles : expressions de base puis, pour chaque emotion, bouche fermee et bouche ouverte."""
    return list(EXPRESSIONS) + [n for e in EMOTIONS for n in noms_emotion(e)]


def libelle_image(nom: str) -> str:
    if nom in LIBELLES_EXPRESSIONS:
        return LIBELLES_EXPRESSIONS[nom]
    base = nom[:-len("_ouverte")] if nom.endswith("_ouverte") else nom
    return LIBELLES_EMOTIONS.get(base, base) + (" (il parle)" if nom.endswith("_ouverte") else "")


def emotions_valides(emotions) -> list:
    """Emotions demandees, dans l'ordre, sans doublon ni valeur inconnue."""
    vues = []
    for e in emotions or []:
        e = str(e).strip().lower()
        if e in EMOTIONS and e not in vues:
            vues.append(e)
    return vues


def instruction_depuis_photos(style: str) -> str:
    """Consigne placee avant le prompt quand des photos de reference sont jointes."""
    return (
        "The attached photos show a real person. Create a hand-drawn illustration of THIS SAME PERSON as a friendly cartoon character, recognizable and flattering: hair style and color, "
        "beard or no beard, glasses if any, face shape, skin tone, apparent age. Style: " + STYLES.get(style, STYLES[STYLE_DEFAUT]) + ". " + CADRAGE + "Expression: "
    )


def instruction_variante() -> str:
    return (
        "The FIRST attached image is the finished illustration of a cartoon character. Redraw it IDENTICAL in every detail: same drawing style, same outline thickness, same colors, same hair, "
        "same face proportions, same head size and exact same position in the frame, same flat green background. Change ONLY the expression: "
    )


def _jpeg(octets: bytes, cote: int = 1280) -> bytes:
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(octets)))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        fond = Image.new("RGB", image.size, (0, 255, 0))
        fond.paste(image, mask=image.split()[-1])
        image = fond
    image = image.convert("RGB")
    image.thumbnail((cote, cote))
    sortie = io.BytesIO()
    image.save(sortie, "JPEG", quality=90)
    return sortie.getvalue()


def detourer(octets: bytes) -> Image.Image:
    """Image Gemini (fond vert) -> RGBA de la meme taille, fond transparent."""
    return detourage.detourer_fond_vert(Image.open(io.BytesIO(octets)), recadrer=False)


def _boite(image: Image.Image) -> tuple:
    return image.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()


def _png(image: Image.Image) -> bytes:
    tampon = io.BytesIO()
    image.save(tampon, "PNG")
    return tampon.getvalue()


def aligner(images: dict) -> dict:
    """
    Les expressions de base sur un meme cadre : chacune est recadree sur sa tete, ramenee a la largeur de la premiere, puis posee centree en haut du cadre commun
    (la tete ne « saute » pas quand l'expression change). Renvoie {nom: image RGBA de meme taille}.
    """
    boites = {nom: _boite(img) for nom, img in images.items()}
    if any(b is None for b in boites.values()):
        raise ValueError("Une des images ne contient aucun personnage.")
    reference = boites[EXPRESSIONS[0]]
    largeur_ref = reference[2] - reference[0]
    recadrees = {}
    for nom, img in images.items():
        b = boites[nom]
        morceau = img.crop(b)
        facteur = largeur_ref / max(1, b[2] - b[0])
        if abs(facteur - 1) > 0.01:
            morceau = morceau.resize((max(1, round(morceau.size[0] * facteur)), max(1, round(morceau.size[1] * facteur))), Image.LANCZOS)
        recadrees[nom] = morceau
    l = max(m.size[0] for m in recadrees.values())
    h = max(m.size[1] for m in recadrees.values())
    sortie = {}
    for nom, morceau in recadrees.items():
        toile = Image.new("RGBA", (l, h), (0, 0, 0, 0))
        toile.paste(morceau, ((l - morceau.size[0]) // 2, 0), morceau)
        reduit = toile
        if max(l, h) > COTE_MAX:
            echelle = COTE_MAX / max(l, h)
            reduit = toile.resize((max(1, round(l * echelle)), max(1, round(h * echelle))), Image.LANCZOS)
        sortie[nom] = reduit
    return sortie


def aligner_sur(reference: Image.Image, image: Image.Image) -> Image.Image:
    """Une image ajoutee plus tard (emotion) : meme cadre que la reference deja enregistree, meme largeur de tete, meme centre et meme haut."""
    b_ref, b = _boite(reference), _boite(image)
    if b_ref is None or b is None:
        raise ValueError("Une des images ne contient aucun personnage.")
    morceau = image.crop(b)
    facteur = (b_ref[2] - b_ref[0]) / max(1, b[2] - b[0])
    if abs(facteur - 1) > 0.01:
        morceau = morceau.resize((max(1, round(morceau.size[0] * facteur)), max(1, round(morceau.size[1] * facteur))), Image.LANCZOS)
    toile = Image.new("RGBA", reference.size, (0, 0, 0, 0))
    toile.paste(morceau, (round((b_ref[0] + b_ref[2]) / 2 - morceau.size[0] / 2), b_ref[1]), morceau)
    return toile


def generer_emotions(ferme_png: bytes, emotions: list, generer, progression=None) -> dict:
    """
    Images des emotions demandees (deux par emotion) a partir de l'image « bouche fermee » deja enregistree, qui sert de reference pour que le visage reste identique.
    Renvoie {nom: PNG alignes sur ferme_png}. progression(i, n) apres chaque image.
    """
    emotions = emotions_valides(emotions)
    reference = charger(ferme_png)
    jpeg = _jpeg(ferme_png)
    total = 2 * len(emotions)
    sortie, fait = {}, 0
    for emotion in emotions:
        for nom, description in zip(noms_emotion(emotion), EMOTIONS[emotion]):
            octets = generer(description[0].upper() + description[1:] + ".", "1:1", [jpeg], False, instruction_personne=instruction_variante())
            sortie[nom] = _png(aligner_sur(reference, detourer(octets)))
            fait += 1
            if progression:
                progression(fait, total)
    return sortie


def generer_tetes(photos_reference: list, generer, style: str = STYLE_DEFAUT, description: str = "", progression=None, emotions=()) -> dict:
    """
    PNG (octets) alignes : {"ferme", "ouverte", "yeux"} plus, pour chaque emotion demandee, « <emotion> » et « <emotion>_ouverte ».
    photos_reference : octets de photos (la fiche de la personne) ; description : texte de repli si aucune photo.
    generer(prompt, aspect_ratio, images_reference, texte_autorise, instruction_personne=...) : gemini_images.generer_image (injecte pour les tests).
    progression(i, n) : i-eme image terminee sur n.
    """
    style = style if style in STYLES else STYLE_DEFAUT
    emotions = emotions_valides(emotions)
    photos = [_jpeg(p) for p in (photos_reference or []) if p][:6]
    if not photos and not (description or "").strip():
        raise ValueError("Aucune photo de référence : ajoute des photos de visage à la fiche, ou décris le personnage.")
    total = 3 + 2 * len(emotions)
    brutes = {}
    if photos:
        octets = generer(EXPRESSION_BASE, "1:1", photos, False, instruction_personne=instruction_depuis_photos(style))
    else:
        octets = generer(f"{description.strip()[:300]}. Style: {STYLES[style]}. {CADRAGE}Expression: {EXPRESSION_BASE}", "1:1", None, False)
    brutes["ferme"] = octets
    if progression:
        progression(1, total)
    premiere = _jpeg(octets)
    for rang, nom in enumerate(("ouverte", "yeux"), 2):
        brutes[nom] = generer(EXPRESSIONS_PROMPT[nom], "1:1", [premiere], False, instruction_personne=instruction_variante())
        if progression:
            progression(rang, total)
    alignees = aligner({nom: detourer(brutes[nom]) for nom in EXPRESSIONS})
    sortie = {nom: _png(img) for nom, img in alignees.items()}
    if emotions:
        decalage = 3
        sortie.update(generer_emotions(sortie["ferme"], emotions, generer, (lambda i, n: progression(decalage + i, total)) if progression else None))
    return sortie


def charger(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA")


def couleur_peau(image: Image.Image) -> tuple:
    """Teinte de la peau (pour les mains du corps) : moyenne des pixels opaques du centre-bas du visage."""
    l, h = image.size
    zone = image.crop((int(l * 0.42), int(h * 0.42), int(l * 0.58), int(h * 0.58)))
    pixels = [p for p in zone.getdata() if p[3] > 200]
    if not pixels:
        return (240, 200, 170)
    n = len(pixels)
    return tuple(int(sum(p[i] for p in pixels) / n) for i in range(3))


def preparer_pour_rendu(png_par_nom: dict) -> dict:
    """{nom: octets PNG} -> {nom: image RGBA, ..., "peau": teinte des mains} pour personnage.dessiner_geo. Les trois expressions de base sont obligatoires."""
    images = {nom: charger(png) for nom, png in png_par_nom.items() if nom in tous_les_noms()}
    if not all(nom in images for nom in EXPRESSIONS):
        raise ValueError("Le personnage BD est incomplet.")
    return {**images, "peau": couleur_peau(images["ferme"])}
