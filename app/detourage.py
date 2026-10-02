"""
Detourage d'une personne photographiee sur fond vert uni (Gemini ne produit pas de transparence : on lui demande un
fond vert pur, puis on le retire ici). Uniquement Pillow, sans dependance supplementaire.
"""

from PIL import Image, ImageChops, ImageFilter

SEUIL_FOND = 55        # a partir de cette « dominance du vert », un pixel est candidat fond
LARGEUR_BANDE = 9      # bande autour du fond ou les reflets verts sont aussi attenues (cheveux, contours)


def detourer_fond_vert(image: Image.Image) -> Image.Image:
    """RGBA recadre sur la personne : fond vert relie aux bords retire, contours adoucis, reflets verts attenues."""
    rgb = image.convert("RGB")
    r, g, b = rgb.split()
    dominance = ImageChops.subtract(g, ImageChops.lighter(r, b))  # > 0 la ou le vert domine
    candidats = dominance.point(lambda v: 255 if v > SEUIL_FOND else 0)

    # Seul le fond relie aux bords est retire : un vert present sur la personne (vetement...) est conserve.
    largeur, hauteur = rgb.size
    marque = candidats.copy()
    graines = [(0, 0), (largeur - 1, 0), (largeur // 2, 0), (0, hauteur // 2), (largeur - 1, hauteur // 2),
               (0, hauteur - 1), (largeur - 1, hauteur - 1)]
    from PIL import ImageDraw
    for graine in graines:
        if marque.getpixel(graine) == 255:
            ImageDraw.floodfill(marque, graine, 128, thresh=0)
    fond = marque.point(lambda v: 255 if v == 128 else 0)
    # Poches de fond enfermees (entre un bras et le torse...) : un vert aussi pur n'existe pas sur une personne.
    fond = ImageChops.lighter(fond, dominance.point(lambda v: 255 if v > 110 else 0))

    alpha = ImageChops.invert(fond).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.1))
    bande = fond.filter(ImageFilter.MaxFilter(LARGEUR_BANDE))
    # Dans la bande : tout ce qui reste franchement vert devient transparent, et les reflets verts sont neutralises.
    alpha_vert = dominance.point(lambda v: 255 - min(255, max(0, (v - 18) * 7)))
    alpha = ImageChops.darker(alpha, ImageChops.lighter(alpha_vert, ImageChops.invert(bande)))
    g_neutre = ImageChops.darker(g, ImageChops.lighter(r, b))
    g = Image.composite(g_neutre, g, bande)

    resultat = Image.merge("RGBA", (r, g, b, alpha))
    boite = alpha.point(lambda v: 255 if v > 20 else 0).getbbox()
    return resultat.crop(boite) if boite else resultat
