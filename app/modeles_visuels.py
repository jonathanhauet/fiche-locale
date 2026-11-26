"""
Modeles de visuels pour les reseaux sociaux (carrousels et posts), dessines avec Pillow aux couleurs, au logo et au decor du client (memes
reglages d'identite que les carrousels, voir carrousel_visuel.py) : mythe ou realite, avant / apres, pas a pas, chiffre cle, conseil local, temoignage
et post typographique. Un modele peut aussi etre exporte en petite video animee (les slides defilent avec un fondu et un leger zoom).

Contenu d'un modele : {"couverture": {"titre", "sous_titre"}, "elements": [{...champs du modele...}], "cta": {"titre", "texte", "bouton"}}
(le post typographique n'a ni couverture ni slide finale).
"""

import io
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw

from . import carrousel_visuel as cv

LARGEUR, HAUTEUR, MARGE = cv.LARGEUR, cv.HAUTEUR, cv.MARGE
FPS = 25

MODELES = {
    "mythe_realite": {"libelle": "Mythe ou réalité", "description": "Une idée reçue, puis la vérité : très partageable.", "champs": ("mythe", "realite"), "nb": (1, 5), "couverture": True},
    "avant_apres": {"libelle": "Avant / après", "description": "Une situation avant, puis après votre intervention.", "champs": ("avant", "apres"), "nb": (1, 4), "couverture": True},
    "pas_a_pas": {"libelle": "Pas à pas", "description": "Une méthode en étapes numérotées.", "champs": ("titre", "texte"), "nb": (3, 7), "couverture": True},
    "conseil_local": {"libelle": "Conseils locaux", "description": "Des conseils concrets pour les habitants de votre zone.", "champs": ("titre", "texte"), "nb": (3, 6), "couverture": True},
    "chiffre_cle": {"libelle": "Chiffres clés", "description": "Un grand chiffre par slide, avec ce qu'il veut dire.", "champs": ("valeur", "libelle"), "nb": (1, 5), "couverture": True},
    "temoignage": {"libelle": "Témoignages", "description": "Des avis clients mis en scène (étoiles, citation).", "champs": ("texte", "auteur"), "nb": (1, 4), "couverture": True},
    "post_typo": {"libelle": "Post typographique", "description": "Une phrase forte en grand, sur votre couleur : un seul visuel.", "champs": ("texte", "soustexte"), "nb": (1, 1), "couverture": False},
    "annonce": {"libelle": "Annonce", "description": "Un visuel façon publicité : surtitre, titre en deux couleurs, texte, atouts en pastilles, bouton et site. "
                "Avec la mise en page claire et le décor « Bande dégradée », il reprend les codes d'une charte de marque.",
                "champs": ("surtitre", "titre", "titre_accent", "texte", "pastilles", "bouton", "site"), "nb": (1, 1), "couverture": False},
}
LIMITES = {"mythe": 150, "realite": 190, "avant": 150, "apres": 190, "titre": 80, "texte": 260, "valeur": 12, "libelle": 110, "auteur": 40, "soustexte": 120,
           "surtitre": 60, "titre_accent": 80, "pastilles": 120, "bouton": 30, "site": 40}


# --------------------------------------------------------------------------- validation

def _texte(valeur, maxi: int) -> str:
    return " ".join(str(valeur or "").split())[:maxi]


def valider_contenu(modele: str, brut) -> dict:
    """Contenu nettoye (longueurs bornees, nombre d'elements dans la fourchette du modele) ; ValueError si inutilisable."""
    if modele not in MODELES:
        raise ValueError("Modèle inconnu.")
    definition = MODELES[modele]
    if not isinstance(brut, dict) or not isinstance(brut.get("elements"), list):
        raise ValueError("Aucun contenu à dessiner.")
    elements = []
    for element in brut["elements"][: definition["nb"][1]]:
        if not isinstance(element, dict):
            continue
        propre = {champ: _texte(element.get(champ), LIMITES[champ]) for champ in definition["champs"]}
        if any(propre[champ] for champ in definition["champs"][:1]) or any(propre.values()):
            elements.append(propre)
    if len(elements) < definition["nb"][0]:
        raise ValueError(f"Ce modèle demande au moins {definition['nb'][0]} élément(s) rempli(s).")
    resultat = {"elements": elements}
    if definition["couverture"]:
        couverture, cta = brut.get("couverture") or {}, brut.get("cta") or {}
        resultat["couverture"] = {"titre": _texte(couverture.get("titre"), 110) or definition["libelle"], "sous_titre": _texte(couverture.get("sous_titre"), 140)}
        resultat["cta"] = {"titre": _texte(cta.get("titre"), 110), "texte": _texte(cta.get("texte"), 180), "bouton": _texte(cta.get("bouton"), 30) or "Prendre contact"}
    return resultat


# --------------------------------------------------------------------------- dessin

def _fond(layout: str, couleur: str, secondaires: list, decor: str, graine: int):
    marque = cv._rgb(couleur)
    p = cv._avec_secondaire(cv._palette(layout, marque), secondaires)
    image = Image.new("RGB", (LARGEUR, HAUTEUR), p["fond"])
    d = ImageDraw.Draw(image)
    cv._decor(d, layout, p, marque, decor, graine)
    return image, d, p


def _etiquette(d, x: int, y: int, texte: str, fond, couleur) -> int:
    """Petite pastille de texte (MYTHE, REALITE...) ; renvoie son bord droit."""
    police = cv._police("gras", 34)
    largeur = int(d.textlength(texte, font=police)) + 56
    d.rounded_rectangle((x, y, x + largeur, y + 62), radius=31, fill=fond)
    d.text((x + 28, y + 11), texte, font=police, fill=couleur)
    return x + largeur


def _paragraphe(d, texte: str, style: str, taille_max: int, taille_min: int, x: int, y: int, largeur: int, hauteur_max: int, couleur, interligne: float = 1.25, barre: bool = False) -> int:
    """Ecrit un texte ajuste a sa boite et renvoie la hauteur utilisee. barre : trait au milieu de chaque ligne (idee recue)."""
    if not texte:
        return 0
    police, lignes, pas = cv._texte_ajuste(d, texte, style, taille_max, taille_min, largeur, hauteur_max, interligne)
    for i, ligne in enumerate(lignes):
        d.text((x, y + i * pas), ligne, font=police, fill=couleur)
        if barre:
            milieu = y + i * pas + int(police.size * 0.62)
            d.line((x - 6, milieu, x + int(d.textlength(ligne, font=police)) + 6, milieu), fill=couleur, width=max(4, police.size // 14))
    return len(lignes) * pas


def _slide_mythe_realite(element, layout, couleur, nom, rang, total, secondaires, decor, graine):
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    zone = LARGEUR - 2 * MARGE
    _etiquette(d, MARGE, 160, "MYTHE", cv._melange(p["fond"], p["texte"], 0.16), p["doux"])
    _paragraphe(d, element["mythe"], "normal", 62, 36, MARGE, 250, zone, 330, p["doux"], 1.25, barre=True)
    d.line((MARGE, 640, LARGEUR - MARGE, 640), fill=cv._melange(p["fond"], p["texte"], 0.22), width=3)
    _etiquette(d, MARGE, 690, "RÉALITÉ", p["accent2"], p["sur_accent2"])
    _paragraphe(d, element["realite"], "gras", 74, 42, MARGE, 790, zone, 400, p["texte"], 1.2)
    cv._pied(d, p, nom, rang, total)
    return image


def _slide_avant_apres(element, layout, couleur, nom, rang, total, secondaires, decor, graine):
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    zone = LARGEUR - 2 * MARGE
    d.rounded_rectangle((MARGE, 150, LARGEUR - MARGE, 590), radius=44, fill=cv._melange(p["fond"], p["texte"], 0.10))
    d.text((MARGE + 44, 190), "AVANT", font=cv._police("gras", 34), fill=p["doux"])
    _paragraphe(d, element["avant"], "normal", 56, 34, MARGE + 44, 252, zone - 88, 290, p["texte"], 1.25)
    d.rounded_rectangle((MARGE, 650, LARGEUR - MARGE, 1170), radius=44, fill=p["accent2"])
    d.text((MARGE + 44, 690), "APRÈS", font=cv._police("gras", 34), fill=p["sur_accent2"])
    _paragraphe(d, element["apres"], "gras", 64, 38, MARGE + 44, 752, zone - 88, 370, p["sur_accent2"], 1.2)
    centre_x, centre_y = LARGEUR // 2, 620                                       # fleche vers le bas entre les deux cartes
    d.ellipse((centre_x - 40, centre_y - 40, centre_x + 40, centre_y + 40), fill=p["fond"], outline=p["accent2"], width=6)
    d.line((centre_x, centre_y - 18, centre_x, centre_y + 16), fill=p["accent2"], width=7)
    d.polygon([(centre_x - 16, centre_y + 6), (centre_x + 16, centre_y + 6), (centre_x, centre_y + 24)], fill=p["accent2"])
    cv._pied(d, p, nom, rang, total)
    return image


def _slide_chiffre(element, layout, couleur, nom, rang, total, secondaires, decor, graine):
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    zone = LARGEUR - 2 * MARGE
    taille = 330
    while taille > 110 and d.textlength(element["valeur"], font=cv._police("gras", taille)) > zone:
        taille -= 10
    police = cv._police("gras", taille)
    d.text((MARGE, 300), element["valeur"], font=police, fill=p["accent2"])
    y = 300 + int(taille * 1.25)
    d.rectangle((MARGE, y, MARGE + 140, y + 12), fill=p["accent2"])
    _paragraphe(d, element["libelle"], "gras", 62, 38, MARGE, y + 60, zone, 1130 - y - 60, p["texte"], 1.25)
    cv._pied(d, p, nom, rang, total)
    return image


def _slide_temoignage(element, layout, couleur, nom, rang, total, secondaires, decor, graine):
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    zone = LARGEUR - 2 * MARGE
    for i in range(5):
        cv._etoile(d, MARGE + 36 + i * 84, 215, 34, cv.OR_ETOILES)
    d.text((MARGE - 6, 250), "“", font=cv._police("gras", 260), fill=p["accent2"])
    hauteur = _paragraphe(d, element["texte"], "gras", 64, 36, MARGE, 470, zone, 500, p["texte"], 1.28)
    if element["auteur"]:
        d.text((MARGE, 470 + hauteur + 40), "— " + element["auteur"], font=cv._police("normal", 40), fill=p["doux"])
    cv._pied(d, p, nom, rang, total)
    return image


def _slide_typo(element, layout, couleur, nom, rang, total, secondaires, decor, graine, logo=None, logo_position="haut_droite", logo_style="pastille", logo_couleur_fond=""):
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    zone = LARGEUR - 2 * MARGE
    if logo is not None:
        cv._coller_logo(image, logo, 90, logo_position, logo_style, logo_couleur_fond)
    hauteur = _paragraphe(d, element["texte"], "gras", 132, 60, MARGE, 300, zone, 700, p["texte"], 1.12)
    y = 300 + hauteur + 24
    d.rectangle((MARGE, y, MARGE + 140, y + 12), fill=p["accent2"])
    _paragraphe(d, element["soustexte"], "normal", 46, 30, MARGE, y + 56, zone, 230, p["doux"], 1.3)
    cv._pied(d, p, nom, 0, 0)
    return image


def _contraste(a: tuple, b: tuple) -> float:
    """Rapport de contraste WCAG entre deux couleurs."""
    def lum(c):
        canaux = []
        for v in c[:3]:
            v = v / 255
            canaux.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]
    claire, sombre = sorted((lum(a), lum(b)), reverse=True)
    return (claire + 0.05) / (sombre + 0.05)


def _lisible(couleur: tuple, fond: tuple, minimum: float = 4.5) -> tuple:
    """La couleur, assombrie (fond clair) ou eclaircie (fond sombre) juste assez pour etre lisible en texte sur ce fond."""
    cible = (0, 0, 0) if cv._luminance(fond) > 0.5 else (255, 255, 255)
    t = 0.0
    while _contraste(couleur if t == 0 else cv._melange(couleur, cible, t), fond) < minimum and t < 1:
        t = round(t + 0.05, 2)
    return couleur if t == 0 else cv._melange(couleur, cible, t)


def _pastilles(texte: str) -> list:
    morceaux = [" ".join(m.split()) for m in texte.replace("|", "·").replace(";", "·").replace(",", "·").replace("\n", "·").split("·")]
    return [m for m in morceaux if m][:4]


def _slide_annonce(element, layout, couleur, nom, secondaires, decor, graine, logo=None, logo_position="haut_gauche", logo_style="pastille", logo_couleur_fond=""):
    """
    Visuel unique facon publicite (1080x1350) : surtitre en majuscules, titre en deux couleurs, texte, atouts en pastilles, bouton et site en bas.
    Le titre prend la couleur principale si elle est lisible sur le fond, sa suite la couleur secondaire (assombrie si besoin pour rester lisible).
    """
    image, d, p = _fond(layout, couleur, secondaires, decor, graine)
    marque, fond = cv._rgb(couleur), p["fond"]
    clair = cv._luminance(fond) > 0.5
    zone = LARGEUR - 2 * MARGE
    couleur_titre = marque if clair and _contraste(marque, fond) >= 7 else p["texte"]
    accent = _lisible(p["accent2"], fond)
    if _contraste(accent, couleur_titre) < 1.6:                 # pas de couleur secondaire distincte : titre neutre, suite a la couleur du client
        couleur_titre = p["texte"]
        accent = _lisible(marque if clair else p["accent"], fond)
    corps = _lisible(p["doux"], fond)

    y = 160
    if logo is not None:
        position = logo_position if str(logo_position).startswith("haut") else "haut_gauche"      # le bas est pris par le bouton
        cv._coller_logo(image, logo, 80, position, logo_style, logo_couleur_fond)
        y = 270
    if element.get("surtitre"):
        d.text((MARGE, y), element["surtitre"].upper(), font=cv._police("gras", 30), fill=accent)
        y += 62

    titre, suite = element.get("titre", ""), element.get("titre_accent", "")
    taille = 96
    while taille > 52:
        police = cv._police("gras", taille)
        lignes = (cv._lignes(d, titre, police, zone) if titre else []) + (cv._lignes(d, suite, police, zone) if suite else [])
        if len(lignes) * int(taille * 1.1) <= 440:
            break
        taille -= 4
    police, pas = cv._police("gras", taille), int(taille * 1.1)
    for texte, teinte in ((titre, couleur_titre), (suite, accent)):
        if not texte:
            continue
        for ligne in cv._lignes(d, texte, police, zone):
            d.text((MARGE, y), ligne, font=police, fill=teinte)
            y += pas
    y += 30

    haut_bouton = HAUTEUR - 200
    y += _paragraphe(d, element.get("texte", ""), "normal", 40, 28, MARGE, y, zone, min(240, max(0, haut_bouton - 150 - y)), corps, 1.3) + 34

    police_pastille = cv._police("gras", 28)
    fond_pastille = (255, 255, 255) if clair and fond != (255, 255, 255) else cv._melange(fond, p["texte"], 0.14)
    x = MARGE
    for etiquette in _pastilles(element.get("pastilles", "")):
        largeur = int(d.textlength(etiquette, font=police_pastille)) + 48
        if x + largeur > LARGEUR - MARGE:
            x, y = MARGE, y + 70
        if y + 58 > haut_bouton - 30:
            break
        d.rounded_rectangle((x, y, x + largeur, y + 58), radius=29, fill=fond_pastille)
        d.text((x + 24, y + 12), etiquette, font=police_pastille, fill=p["texte"])
        x += largeur + 16

    if element.get("bouton"):
        police_bouton = cv._police("gras", 34)
        libelle = element["bouton"] + "  →"
        largeur = int(d.textlength(libelle, font=police_bouton)) + 84
        fond_bouton = couleur_titre if clair else (255, 255, 255)
        d.rounded_rectangle((MARGE, haut_bouton, MARGE + largeur, haut_bouton + 92), radius=46, fill=fond_bouton)
        texte_bouton = (255, 255, 255) if cv._luminance(fond_bouton) < 0.5 else p["texte"] if clair else fond
        d.text((MARGE + 42, haut_bouton + 24), libelle, font=police_bouton, fill=texte_bouton)
    site = element.get("site", "")
    if site:
        police_site = cv._police("gras", 32)
        d.text((LARGEUR - MARGE - d.textlength(site, font=police_site), haut_bouton + 28), site, font=police_site, fill=couleur_titre if clair else p["texte"])
    elif not element.get("bouton"):
        cv._pied(d, p, nom, 0, 0)
    return image


_DESSINS = {"mythe_realite": _slide_mythe_realite, "avant_apres": _slide_avant_apres, "chiffre_cle": _slide_chiffre, "temoignage": _slide_temoignage}


def construire(modele: str, contenu: dict, layout: str, couleur: str, nom_client: str, logo=None, photo=None, secondaires: list = None, decor: str = "auto",
               graine: int = 0, logo_position: str = "haut_droite", logo_style: str = "pastille", logo_couleur_fond: str = "") -> list:
    """Slides (images 1080x1350) du modele ; contenu deja valide (valider_contenu)."""
    layout = layout if layout in cv.LAYOUTS else "plein"
    definition = MODELES[modele]
    if modele == "annonce":
        return [_slide_annonce(contenu["elements"][0], layout, couleur, nom_client, secondaires, decor, graine, logo, logo_position, logo_style, logo_couleur_fond)]
    if modele == "post_typo":
        return [_slide_typo(contenu["elements"][0], layout, couleur, nom_client, 1, 1, secondaires, decor, graine, logo, logo_position, logo_style, logo_couleur_fond)]
    elements = contenu["elements"]
    total = len(elements) + 2
    slides = [cv.dessiner_slide("couverture", contenu["couverture"], layout, couleur, nom_client, 1, total, logo, photo, secondaires, decor, graine,
                                logo_position, logo_style, logo_couleur_fond)]
    for i, element in enumerate(elements, start=1):
        if modele in _DESSINS:
            slides.append(_DESSINS[modele](element, layout, couleur, nom_client, i + 1, total, secondaires, decor, graine))
        else:                                           # pas a pas, conseils : slides numerotees du carrousel
            slides.append(cv.dessiner_slide("point", {**element, "numero": i}, layout, couleur, nom_client, i + 1, total, secondaires=secondaires, decor=decor, graine=graine))
    slides.append(cv.dessiner_slide("cta", contenu["cta"], layout, couleur, nom_client, total, total, logo, secondaires=secondaires, decor=decor, graine=graine,
                                    logo_position=logo_position, logo_style=logo_style, logo_couleur_fond=logo_couleur_fond))
    return slides


# --------------------------------------------------------------------------- video animee

def en_video(images: list, couleur_fond: tuple, duree_slide: float = 3.2, transition: float = 0.5) -> bytes:
    """
    Petite video verticale 1080x1920 (Reel, Short, TikTok) a partir des slides : chaque slide se rapproche tres doucement, avec un glissement
    d'une slide a l'autre ; le haut et le bas sont de la couleur du fond. Renvoie le MP4.
    """
    from . import montage_video

    if not images:
        raise ValueError("Aucune image.")
    images = images[:12]
    frames = int(duree_slide * FPS)
    fond = "#%02x%02x%02x" % tuple(couleur_fond)
    with tempfile.TemporaryDirectory() as dossier:
        commande = [montage_video.FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
        for i, image in enumerate(images):
            chemin = os.path.join(dossier, f"s{i}.jpg")
            image.convert("RGB").save(chemin, format="JPEG", quality=92)
            commande += ["-i", chemin]
        parties = [
            f"[{i}:v]scale=1080:1350,zoompan=z='1+0.045*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1350:fps={FPS},"
            f"pad=1080:1920:0:285:color={fond},format=yuv420p,setsar=1[v{i}]" for i in range(len(images))
        ]
        etiquette = "v0"
        for i in range(1, len(images)):
            parties.append(f"[{etiquette}][v{i}]xfade=transition=slideleft:duration={transition}:offset={i * (duree_slide - transition):.2f}[x{i}]")
            etiquette = f"x{i}"
        sortie = os.path.join(dossier, "sortie.mp4")
        commande += ["-filter_complex", ";".join(parties), "-map", f"[{etiquette}]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                     "-r", str(FPS), "-movflags", "+faststart", sortie]
        resultat = subprocess.run(commande, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if resultat.returncode != 0 or not os.path.exists(sortie):
            raise RuntimeError("Création de la vidéo impossible : " + (resultat.stderr or "")[-300:])
        with open(sortie, "rb") as fichier:
            return fichier.read()


def couleur_du_fond(layout: str, couleur: str, secondaires: list = None) -> tuple:
    return cv._palette(layout if layout in cv.LAYOUTS else "plein", cv._rgb(couleur))["fond"]


LIBELLES_CHAMPS = {
    "mythe": "L'idée reçue", "realite": "La réalité", "avant": "Avant", "apres": "Après", "titre": "Titre", "texte": "Texte", "valeur": "Chiffre (ex : 137 %)",
    "libelle": "Ce que mesure ce chiffre", "auteur": "Prénom et initiale (ex : Marie D.)", "soustexte": "Précision (facultatif)",
    "surtitre": "Surtitre (ex : Pour les associations employeuses)", "titre_accent": "Suite du titre, en couleur", "pastilles": "Atouts en pastilles, séparés par des virgules",
    "bouton": "Texte du bouton (ex : Réserver une démo)", "site": "Site affiché (ex : assorh.fr)",
}
LIBELLES_CHAMPS_PAR_MODELE = {"temoignage": {"texte": "Avis du client (un vrai avis)"}, "post_typo": {"texte": "La phrase en grand"},
                              "annonce": {"titre": "Début du titre", "texte": "Texte (1 à 2 phrases)"}}


def description_pour_page() -> dict:
    """Modeles et libelles de leurs champs, pour construire le formulaire du composeur."""
    resultat = {}
    for identifiant, definition in MODELES.items():
        libelles = {**LIBELLES_CHAMPS, **LIBELLES_CHAMPS_PAR_MODELE.get(identifiant, {})}
        resultat[identifiant] = {"libelle": definition["libelle"], "description": definition["description"], "couverture": definition["couverture"], "nb": list(definition["nb"]),
                                 "champs": [[champ, libelles[champ]] for champ in definition["champs"]]}
    return resultat
