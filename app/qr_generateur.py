"""Generateur de QR codes personnalisables (page publique /qr-code) : contenu par type, couleurs, forme des modules, logo central, export PNG / SVG.

Les QR codes sont statiques (le contenu est ecrit dans le dessin) : rien n'est enregistre, ils ne peuvent jamais expirer.
"""

import base64
import re
from io import BytesIO
from urllib.parse import quote

import qrcode
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

LONGUEUR_MAX = 900
TAILLE_MIN, TAILLE_MAX, TAILLE_DEFAUT = 300, 2400, 1200
TAILLE_MAX_LOGO = 2 * 1024 * 1024
MARGE_MODULES = 4
PART_LOGO = 0.22  # largeur du logo / largeur du code : reste sous la capacite de correction d'erreur H
FORMES = ("carre", "arrondi", "point")
SURECHANTILLONNAGE = 4


class ErreurQR(ValueError):
    """Message destine a etre affiche tel quel a l'utilisateur."""


TYPES = {
    "lien": {"libelle": "Lien de site web", "champs": [("url", "Adresse du site", "https://www.mon-entreprise.fr", True)]},
    "avis": {"libelle": "Avis Google", "champs": [("place_id", "Identifiant de lieu Google (Place ID)", "ChIJ...", True)]},
    "itineraire": {"libelle": "Adresse sur Google Maps", "champs": [("adresse", "Adresse complète", "12 rue de la Paix, 75002 Paris", True)]},
    "tel": {"libelle": "Téléphone", "champs": [("telephone", "Numéro de téléphone", "+33 1 23 45 67 89", True)]},
    "whatsapp": {"libelle": "WhatsApp", "champs": [("telephone", "Numéro WhatsApp (avec l'indicatif pays)", "+33 6 12 34 56 78", True),
                                                   ("message", "Message prérempli (facultatif)", "Bonjour, je vous contacte depuis votre QR code", False)]},
    "email": {"libelle": "E-mail", "champs": [("email", "Adresse e-mail", "contact@mon-entreprise.fr", True),
                                              ("objet", "Objet (facultatif)", "", False), ("message", "Message (facultatif)", "", False)]},
    "sms": {"libelle": "SMS", "champs": [("telephone", "Numéro de téléphone", "+33 6 12 34 56 78", True), ("message", "Message prérempli (facultatif)", "", False)]},
    "wifi": {"libelle": "Wi-Fi", "champs": [("ssid", "Nom du réseau Wi-Fi", "", True), ("mot_de_passe", "Mot de passe", "", False),
                                          ("securite", "Sécurité (WPA, WEP ou aucune)", "WPA", False)]},
    "contact": {"libelle": "Carte de visite (contact)", "champs": [
        ("prenom", "Prénom", "", False), ("nom", "Nom", "", False), ("organisation", "Entreprise", "", False), ("telephone", "Téléphone", "", False),
        ("email", "E-mail", "", False), ("site", "Site web", "", False), ("adresse", "Adresse", "", False)]},
    "texte": {"libelle": "Texte libre", "champs": [("texte", "Texte", "", True)]},
}


def _champ(champs: dict, nom: str) -> str:
    return re.sub(r"\s+", " ", str(champs.get(nom) or "")).strip()


def _url(brut: str) -> str:
    adresse = brut.strip()
    if not adresse:
        raise ErreurQR("Indiquez l'adresse du site.")
    if re.match(r"^[a-z][a-z0-9+.-]*:", adresse, re.I) and not re.match(r"^https?://", adresse, re.I):
        raise ErreurQR("Seules les adresses commençant par http:// ou https:// sont acceptées.")
    if not re.match(r"^https?://", adresse, re.I):
        adresse = "https://" + adresse
    if " " in adresse or "." not in adresse.split("//", 1)[1].split("/", 1)[0]:
        raise ErreurQR("Cette adresse de site ne semble pas valide.")
    return adresse


def _telephone(brut: str, chiffres_seuls: bool = False) -> str:
    numero = re.sub(r"[^\d+]", "", brut)
    if numero.startswith("00"):
        numero = "+" + numero[2:]
    if len(re.sub(r"\D", "", numero)) < 6:
        raise ErreurQR("Indiquez un numéro de téléphone valide.")
    return re.sub(r"\D", "", numero) if chiffres_seuls else numero


def _echapper_wifi(valeur: str) -> str:
    return re.sub(r'([\\;,:"])', r"\\\1", valeur)


def _echapper_vcard(valeur: str) -> str:
    return valeur.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def construire_contenu(type_qr: str, champs: dict) -> str:
    """Transforme les champs saisis en texte a encoder dans le QR code (les erreurs sont des ErreurQR lisibles)."""
    if type_qr not in TYPES:
        raise ErreurQR("Type de QR code inconnu.")
    if type_qr == "lien":
        contenu = _url(_champ(champs, "url"))
    elif type_qr == "avis":
        saisie = _champ(champs, "place_id")
        if re.match(r"^https?://", saisie, re.I):
            contenu = _url(saisie)
        elif re.fullmatch(r"[A-Za-z0-9_-]{12,}", saisie):
            contenu = f"https://search.google.com/local/writereview?placeid={saisie}"
        else:
            raise ErreurQR("L'identifiant de lieu Google semble invalide (il commence généralement par « ChIJ »).")
    elif type_qr == "itineraire":
        adresse = _champ(champs, "adresse")
        if len(adresse) < 5:
            raise ErreurQR("Indiquez l'adresse complète.")
        contenu = "https://www.google.com/maps/search/?api=1&query=" + quote(adresse)
    elif type_qr == "tel":
        contenu = "tel:" + _telephone(_champ(champs, "telephone"))
    elif type_qr == "whatsapp":
        contenu = "https://wa.me/" + _telephone(_champ(champs, "telephone"), chiffres_seuls=True)
        if _champ(champs, "message"):
            contenu += "?text=" + quote(_champ(champs, "message"))
    elif type_qr == "email":
        adresse = _champ(champs, "email")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", adresse):
            raise ErreurQR("Indiquez une adresse e-mail valide.")
        options = [f"{cle}={quote(_champ(champs, nom))}" for cle, nom in (("subject", "objet"), ("body", "message")) if _champ(champs, nom)]
        contenu = "mailto:" + adresse + ("?" + "&".join(options) if options else "")
    elif type_qr == "sms":
        contenu = f"SMSTO:{_telephone(_champ(champs, 'telephone'))}:{_champ(champs, 'message')}"
    elif type_qr == "wifi":
        ssid = _champ(champs, "ssid")
        if not ssid:
            raise ErreurQR("Indiquez le nom du réseau Wi-Fi.")
        securite = (_champ(champs, "securite") or "WPA").upper()
        securite = "nopass" if securite in ("AUCUNE", "AUCUN", "NOPASS", "") else ("WEP" if securite == "WEP" else "WPA")
        mot_de_passe = str(champs.get("mot_de_passe") or "")
        if securite != "nopass" and not mot_de_passe:
            raise ErreurQR("Indiquez le mot de passe, ou choisissez « aucune » comme sécurité.")
        contenu = f"WIFI:T:{securite};S:{_echapper_wifi(ssid)};" + (f"P:{_echapper_wifi(mot_de_passe)};" if securite != "nopass" else "") + ";"
    elif type_qr == "contact":
        prenom, nom = _champ(champs, "prenom"), _champ(champs, "nom")
        if not (prenom or nom or _champ(champs, "organisation")):
            raise ErreurQR("Indiquez au moins un nom, un prénom ou une entreprise.")
        lignes = ["BEGIN:VCARD", "VERSION:3.0", f"N:{_echapper_vcard(nom)};{_echapper_vcard(prenom)};;;", f"FN:{_echapper_vcard((prenom + ' ' + nom).strip() or _champ(champs, 'organisation'))}"]
        if _champ(champs, "organisation"):
            lignes.append("ORG:" + _echapper_vcard(_champ(champs, "organisation")))
        if _champ(champs, "telephone"):
            lignes.append("TEL;TYPE=WORK,VOICE:" + _telephone(_champ(champs, "telephone")))
        if _champ(champs, "email"):
            lignes.append("EMAIL:" + _champ(champs, "email"))
        if _champ(champs, "site"):
            lignes.append("URL:" + _url(_champ(champs, "site")))
        if _champ(champs, "adresse"):
            lignes.append("ADR;TYPE=WORK:;;" + _echapper_vcard(_champ(champs, "adresse")) + ";;;;")
        lignes.append("END:VCARD")
        contenu = "\n".join(lignes)
    else:  # texte
        contenu = str(champs.get("texte") or "").strip()
        if not contenu:
            raise ErreurQR("Saisissez le texte à encoder.")
    if len(contenu) > LONGUEUR_MAX:
        raise ErreurQR(f"Le contenu est trop long ({len(contenu)} caractères, {LONGUEUR_MAX} maximum) : un QR trop dense se scanne mal.")
    return contenu


# --- Couleurs -------------------------------------------------------------------------------------------------------

def couleur_valide(valeur: str, defaut: str) -> str:
    valeur = (valeur or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", valeur):
        return valeur.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", valeur):
        return "#" + "".join(c * 2 for c in valeur[1:]).lower()
    return defaut


def _luminance(couleur: str) -> float:
    composantes = []
    for i in (1, 3, 5):
        c = int(couleur[i:i + 2], 16) / 255
        composantes.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * composantes[0] + 0.7152 * composantes[1] + 0.0722 * composantes[2]


def contraste(c1: str, c2: str) -> float:
    l1, l2 = sorted((_luminance(c1), _luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def verifier_couleurs(couleur: str, fond: str) -> list[str]:
    """Renvoie des avertissements ; leve ErreurQR si le code serait illisible."""
    rapport = contraste(couleur, fond)
    if rapport < 2:
        raise ErreurQR("Le contraste entre le code et le fond est trop faible : le QR code ne pourrait pas être scanné. Choisissez un code plus foncé que le fond.")
    avertissements = []
    if rapport < 4:
        avertissements.append("Le contraste est un peu faible : testez le scan avec votre téléphone avant d'imprimer.")
    if _luminance(couleur) > _luminance(fond):
        avertissements.append("Le code est plus clair que le fond : certaines applications de scan ne le lisent pas. Préférez un code foncé sur fond clair.")
    return avertissements


# --- Geometrie ------------------------------------------------------------------------------------------------------

def _matrice(contenu: str, avec_logo: bool):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H if avec_logo else ERROR_CORRECT_M, border=0, box_size=1)
    qr.add_data(contenu)
    try:
        qr.make(fit=True)
    except Exception as erreur:  # donnees trop volumineuses pour la plus grande version
        raise ErreurQR("Le contenu est trop long pour un QR code.") from erreur
    return qr.get_matrix()


def _est_oeil(x: int, y: int, n: int) -> bool:
    return (x < 7 and y < 7) or (x >= n - 7 and y < 7) or (x < 7 and y >= n - 7)


def _formes(matrice, forme: str):
    """Liste de primitives en unites de module (origine = coin du code, hors marge) : ('rect'|'rond'|'cercle', x, y, taille, rayon, creux)."""
    n = len(matrice)
    primitives = []
    for oeil_x, oeil_y in ((0, 0), (n - 7, 0), (0, n - 7)):
        rayon = {"carre": 0, "arrondi": 1.6, "point": 3.5}[forme]
        primitives.append(("anneau", oeil_x, oeil_y, 7, rayon, 1))
        primitives.append(("plein", oeil_x + 2, oeil_y + 2, 3, {"carre": 0, "arrondi": 0.9, "point": 1.5}[forme], 0))
    for y, ligne in enumerate(matrice):
        for x, actif in enumerate(ligne):
            if actif and not _est_oeil(x, y, n):
                primitives.append(("module", x, y, 1, 0, 0))
    return primitives, n


def _logo_pret(octets_logo: bytes):
    if len(octets_logo) > TAILLE_MAX_LOGO:
        raise ErreurQR("Le logo est trop volumineux (2 Mo maximum).")
    try:
        image = Image.open(BytesIO(octets_logo))
        image.load()
    except Exception as erreur:
        raise ErreurQR("Le logo n'a pas pu être lu : utilisez une image PNG ou JPG.") from erreur
    return image.convert("RGBA")


def _zone_logo(n: int):
    cote = max(3, round(n * PART_LOGO))
    if (n - cote) % 2:
        cote += 1  # reste centre sur la grille
    debut = (n - cote) // 2
    return debut, cote


def _matrice_sans_logo(matrice, avec_logo: bool):
    if not avec_logo:
        return matrice
    n = len(matrice)
    debut, cote = _zone_logo(n)
    resultat = [ligne[:] for ligne in matrice]
    for y in range(debut, debut + cote):
        for x in range(debut, debut + cote):
            resultat[y][x] = False
    return resultat


def _taille_valide(taille) -> int:
    try:
        taille = int(taille)
    except (TypeError, ValueError):
        taille = TAILLE_DEFAUT
    return max(TAILLE_MIN, min(TAILLE_MAX, taille))


def _preparer(contenu, forme, couleur, fond, octets_logo):
    if forme not in FORMES:
        forme = "carre"
    couleur = couleur_valide(couleur, "#000000")
    fond = couleur_valide(fond, "#ffffff")
    avertissements = verifier_couleurs(couleur, fond)
    logo = _logo_pret(octets_logo) if octets_logo else None
    matrice = _matrice(contenu, logo is not None)
    return forme, couleur, fond, logo, matrice, avertissements


# --- Rendu PNG ------------------------------------------------------------------------------------------------------

def generer_png(contenu: str, forme="carre", couleur="#000000", fond="#ffffff", octets_logo: bytes | None = None, taille=TAILLE_DEFAUT, transparent=False):
    """-> (octets PNG, avertissements)."""
    taille = _taille_valide(taille)
    forme, couleur, fond, logo, matrice, avertissements = _preparer(contenu, forme, couleur, fond, octets_logo)
    n = len(matrice)
    cases = n + 2 * MARGE_MODULES
    module = max(1, round(taille * SURECHANTILLONNAGE / cases))
    cote = module * cases
    image = Image.new("RGBA", (cote, cote), (0, 0, 0, 0) if transparent else fond)
    dessin = ImageDraw.Draw(image)
    decalage = MARGE_MODULES * module
    primitives, _ = _formes(_matrice_sans_logo(matrice, logo is not None), forme)

    def boite(x, y, c):
        return (decalage + x * module, decalage + y * module, decalage + (x + c) * module - 1, decalage + (y + c) * module - 1)

    for genre, x, y, c, rayon, _creux in primitives:
        if genre == "module":
            if forme == "point":
                dessin.ellipse(boite(x, y, 1), fill=couleur)
            elif forme == "arrondi":
                dessin.rounded_rectangle(boite(x, y, 1), radius=module * 0.3, fill=couleur)
            else:
                dessin.rectangle(boite(x, y, 1), fill=couleur)
        elif genre == "anneau":
            dessin.rounded_rectangle(boite(x, y, c), radius=rayon * module, fill=couleur)
            interieur = boite(x + 1, y + 1, c - 2)
            dessin.rounded_rectangle(interieur, radius=max(0, rayon - 1) * module, fill=(0, 0, 0, 0) if transparent else fond)
        else:
            dessin.rounded_rectangle(boite(x, y, c), radius=rayon * module, fill=couleur)
    if transparent:
        # le trou des yeux a ete peint en transparent par-dessus un fond deja transparent : rien d'autre a faire
        pass
    if logo is not None:
        debut, cote_logo = _zone_logo(n)
        pastille = boite(debut, debut, cote_logo)
        dessin.rounded_rectangle(pastille, radius=module * 1.2, fill=(0, 0, 0, 0) if transparent else fond)
        marge_logo = module * 0.5
        zone = int(cote_logo * module - 2 * marge_logo)
        copie = logo.copy()
        copie.thumbnail((zone, zone), Image.LANCZOS)
        if transparent:  # sans fond de code : on garde une pastille blanche sous le logo pour qu'il reste lisible
            dessin.rounded_rectangle(pastille, radius=module * 1.2, fill="#ffffff")
        position = (int(pastille[0] + marge_logo + (zone - copie.width) / 2), int(pastille[1] + marge_logo + (zone - copie.height) / 2))
        image.alpha_composite(copie, position)
    final = image.resize((taille, taille), Image.LANCZOS)
    tampon = BytesIO()
    final.save(tampon, format="PNG")
    return tampon.getvalue(), avertissements


# --- Rendu SVG ------------------------------------------------------------------------------------------------------

def generer_svg(contenu: str, forme="carre", couleur="#000000", fond="#ffffff", octets_logo: bytes | None = None, transparent=False):
    """-> (texte SVG, avertissements). Vectoriel : imprimable a n'importe quelle taille sans perte."""
    forme, couleur, fond, logo, matrice, avertissements = _preparer(contenu, forme, couleur, fond, octets_logo)
    n = len(matrice)
    total = n + 2 * MARGE_MODULES
    primitives, _ = _formes(_matrice_sans_logo(matrice, logo is not None), forme)
    elements = []
    modules = []
    for genre, x, y, c, rayon, _creux in primitives:
        px, py = x + MARGE_MODULES, y + MARGE_MODULES
        if genre == "module":
            if forme == "point":
                modules.append(f'<circle cx="{px + 0.5}" cy="{py + 0.5}" r="0.5"/>')
            elif forme == "arrondi":
                modules.append(f'<rect x="{px}" y="{py}" width="1" height="1" rx="0.3"/>')
            else:
                modules.append(f'<rect x="{px}" y="{py}" width="1" height="1"/>')
        elif genre == "anneau":
            interieur = max(0, rayon - 1)
            elements.append(
                f'<path fill-rule="evenodd" d="{_chemin_arrondi(px, py, c, rayon)} {_chemin_arrondi(px + 1, py + 1, c - 2, interieur)}"/>')
        else:
            elements.append(f'<path d="{_chemin_arrondi(px, py, c, rayon)}"/>')
    corps = "".join(elements) + "".join(modules)
    fond_svg = "" if transparent else f'<rect width="{total}" height="{total}" fill="{fond}"/>'
    logo_svg = ""
    if logo is not None:
        debut, cote_logo = _zone_logo(n)
        px = debut + MARGE_MODULES
        marge = 0.5
        zone = cote_logo - 2 * marge
        copie = logo.copy()
        copie.thumbnail((int(zone * 100), int(zone * 100)), Image.LANCZOS)
        tampon = BytesIO()
        copie.save(tampon, format="PNG")
        donnees = base64.b64encode(tampon.getvalue()).decode("ascii")
        largeur, hauteur = copie.width / 100, copie.height / 100
        pastille = "#ffffff" if transparent else fond
        logo_svg = (
            f'<rect x="{px}" y="{px}" width="{cote_logo}" height="{cote_logo}" rx="1.2" fill="{pastille}"/>'
            f'<image x="{px + marge + (zone - largeur) / 2:.3f}" y="{px + marge + (zone - hauteur) / 2:.3f}" width="{largeur:.3f}" height="{hauteur:.3f}" '
            f'href="data:image/png;base64,{donnees}"/>')
    svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
           f'width="{total * 10}" height="{total * 10}" shape-rendering="geometricPrecision">{fond_svg}<g fill="{couleur}">{corps}</g>{logo_svg}</svg>')
    return svg, avertissements


def _chemin_arrondi(x: float, y: float, c: float, r: float) -> str:
    r = min(r, c / 2)
    if r <= 0:
        return f"M{x},{y}h{c}v{c}h-{c}z"
    return (f"M{x + r},{y}h{c - 2 * r}a{r},{r} 0 0 1 {r},{r}v{c - 2 * r}a{r},{r} 0 0 1 -{r},{r}"
            f"h-{c - 2 * r}a{r},{r} 0 0 1 -{r},-{r}v-{c - 2 * r}a{r},{r} 0 0 1 {r},-{r}z")


def apercu_data_uri(octets_png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(octets_png).decode("ascii")


def description_types() -> list[dict]:
    return [{"code": code, "libelle": t["libelle"], "champs": [{"nom": n, "libelle": l, "exemple": e, "obligatoire": o} for n, l, e, o in t["champs"]]}
            for code, t in TYPES.items()]
