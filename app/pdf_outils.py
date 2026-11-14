"""
Outils PDF : ouvrir un PDF, le modifier proprement (texte existant, texte / image / surlignage / caviardage ajoutes, pages reordonnees, pivotees,
supprimees, fusionnees, numeros de page, filigrane, compression) puis le telecharger (PDF, PDF protege, pages choisies, images).

Rien n'est garde dans la base : chaque PDF ouvert vit dans un dossier temporaire sous un identifiant secret, avec l'historique de ses versions (annulation),
et disparait tout seul au bout de quelques heures (purger) ou a la fermeture. Bibliotheque : PyMuPDF (licence AGPL ; usage interne de la plateforme).
"""

import io
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime

import pymupdf
from PIL import Image

RACINE = os.path.join(tempfile.gettempdir(), "outils_pdf_fiche_locale")
DUREE_VIE_SECONDES = 4 * 3600                  # un PDF ouvert est efface 4 h apres sa derniere modification
TAILLE_MAX_OCTETS = 60 * 1024 * 1024
PAGES_MAX = 400
VERSIONS_MAX = 15                              # nombre d'annulations possibles
MAX_SESSIONS = 30
ZOOM_MAX = 4.0
_verrous = {}
_verrou_global = threading.Lock()


class ErreurPDF(Exception):
    """Erreur a montrer telle quelle a l'utilisateur."""


# ---------- sessions (dossier temporaire par PDF ouvert)

def _verifier_id(ident: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", ident or ""):
        raise ErreurPDF("Document introuvable.")
    return ident


def _dossier(ident: str) -> str:
    return os.path.join(RACINE, _verifier_id(ident))


def _verrou(ident: str) -> threading.Lock:
    with _verrou_global:
        return _verrous.setdefault(ident, threading.Lock())


def _lire_meta(ident: str) -> dict:
    try:
        with open(os.path.join(_dossier(ident), "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise ErreurPDF("Document introuvable ou expiré : rouvrez le fichier.")


def _ecrire_meta(ident: str, meta: dict) -> None:
    meta["maj"] = time.time()
    with open(os.path.join(_dossier(ident), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)


def _chemin_version(ident: str, version: int) -> str:
    return os.path.join(_dossier(ident), f"v{int(version)}.pdf")


def ouvrir_octets(octets: bytes, mot_de_passe: str = "") -> pymupdf.Document:
    """Ouvre un PDF depuis des octets, avec controle de taille, de validite, de mot de passe et de nombre de pages."""
    if not octets:
        raise ErreurPDF("Fichier vide.")
    if len(octets) > TAILLE_MAX_OCTETS:
        raise ErreurPDF(f"Fichier trop lourd (maximum {TAILLE_MAX_OCTETS // 1048576} Mo).")
    try:
        doc = pymupdf.open(stream=octets, filetype="pdf")
    except Exception:
        raise ErreurPDF("Ce fichier n'est pas un PDF lisible.")
    if doc.needs_pass:
        if not mot_de_passe or not doc.authenticate(mot_de_passe):
            doc.close()
            raise ErreurPDF("Ce PDF est protégé par un mot de passe : saisissez-le pour l'ouvrir.")
    if doc.page_count == 0:
        doc.close()
        raise ErreurPDF("Ce PDF ne contient aucune page.")
    if doc.page_count > PAGES_MAX:
        doc.close()
        raise ErreurPDF(f"Trop de pages ({doc.page_count}) : maximum {PAGES_MAX}.")
    return doc


def _sauver(doc: pymupdf.Document, chemin: str) -> None:
    doc.save(chemin, garbage=3, deflate=True)


def creer_session(octets: bytes, nom: str, mot_de_passe: str = "") -> str:
    """Ouvre un PDF et lui cree une session ; renvoie l'identifiant secret."""
    doc = ouvrir_octets(octets, mot_de_passe)
    purger()
    with _verrou_global:
        existantes = os.listdir(RACINE) if os.path.isdir(RACINE) else []
    if len(existantes) >= MAX_SESSIONS:
        doc.close()
        raise ErreurPDF("Trop de documents ouverts en même temps : fermez-en un d'abord.")
    ident = secrets.token_hex(16)
    os.makedirs(_dossier(ident), exist_ok=True)
    try:
        _sauver(doc, _chemin_version(ident, 0))
    finally:
        doc.close()
    nom = re.sub(r"[^\w .()\-]", "_", os.path.basename(nom or "document.pdf"))[:120] or "document.pdf"
    _ecrire_meta(ident, {"nom": nom, "version": 0, "plus_ancienne": 0, "cree": time.time()})
    return ident


def session_depuis_images(images: list) -> str:
    """Cree un PDF dont chaque page est une image [(nom, octets)] (une page par image, a la taille de l'image)."""
    if not images:
        raise ErreurPDF("Aucune image.")
    doc = pymupdf.open()
    try:
        for nom, octets in images[:PAGES_MAX]:
            try:
                img = Image.open(io.BytesIO(octets))
                img = img.convert("RGB")
                tampon = io.BytesIO()
                img.save(tampon, "JPEG", quality=92)
            except Exception:
                raise ErreurPDF(f"« {nom} » n'est pas une image lisible (JPG ou PNG).")
            largeur, hauteur = img.size
            echelle = 595.0 / largeur if largeur >= hauteur * 0.7 else 842.0 / hauteur            # tient dans une page A4
            page = doc.new_page(width=largeur * echelle, height=hauteur * echelle)
            page.insert_image(page.rect, stream=tampon.getvalue())
        octets_pdf = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    return creer_session(octets_pdf, "images.pdf")


def etat(ident: str) -> dict:
    """Description du document courant : nom, version, taille et pages (largeur, hauteur, rotation)."""
    meta = _lire_meta(ident)
    chemin = _chemin_version(ident, meta["version"])
    doc = pymupdf.open(chemin)
    try:
        pages = [{"l": round(p.rect.width, 2), "h": round(p.rect.height, 2), "rot": p.rotation} for p in doc]
        texte_possible = any(len(p.get_text("text").strip()) > 0 for p in list(doc)[:5])
    finally:
        doc.close()
    return {"id": ident, "nom": meta["nom"], "version": meta["version"], "annulable": meta["version"] > meta["plus_ancienne"],
            "taille": os.path.getsize(chemin), "pages": pages, "texte_possible": texte_possible}


def fermer(ident: str) -> None:
    shutil.rmtree(_dossier(ident), ignore_errors=True)
    with _verrou_global:
        _verrous.pop(ident, None)


def purger(maintenant: float = None) -> int:
    """Efface les documents ouverts depuis trop longtemps (tache planifiee et a chaque ouverture)."""
    maintenant = maintenant or time.time()
    supprimes = 0
    if not os.path.isdir(RACINE):
        return 0
    for nom in os.listdir(RACINE):
        dossier = os.path.join(RACINE, nom)
        try:
            with open(os.path.join(dossier, "meta.json"), encoding="utf-8") as f:
                maj = json.load(f).get("maj", 0)
        except (OSError, ValueError):
            maj = os.path.getmtime(dossier) if os.path.exists(dossier) else 0
        if maintenant - maj > DUREE_VIE_SECONDES:
            shutil.rmtree(dossier, ignore_errors=True)
            supprimes += 1
    return supprimes


# ---------- lecture : pages en images, texte modifiable

def rendre_page(ident: str, numero: int, zoom: float = 1.5, version: int = None) -> bytes:
    """Page en JPEG (zoom 1 = 72 dpi)."""
    meta = _lire_meta(ident)
    doc = pymupdf.open(_chemin_version(ident, meta["version"] if version is None else version))
    try:
        if not 0 <= numero < doc.page_count:
            raise ErreurPDF("Page introuvable.")
        zoom = max(0.1, min(float(zoom), ZOOM_MAX))
        pix = doc[numero].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=88 if zoom >= 1 else 80)
    finally:
        doc.close()


POLICES = {"sans": ("helv", "hebo", "heit", "hebi"), "serif": ("tiro", "tibo", "tiit", "tibi"), "mono": ("cour", "cobo", "coit", "cobi")}
LIBELLES_POLICES = {"sans": "Helvetica (sans empattement)", "serif": "Times (avec empattement)", "mono": "Courier (chasse fixe)"}


def nom_police(famille: str, gras: bool, italique: bool) -> str:
    return POLICES.get(famille, POLICES["sans"])[(1 if gras else 0) + (2 if italique else 0)]


def _hex(couleur_entier: int) -> str:
    return "#{:06x}".format(couleur_entier & 0xFFFFFF)


def _rgb(couleur_hex: str) -> tuple:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", couleur_hex or "")
    if not m:
        return (0, 0, 0)
    v = m.group(1)
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _famille(nom_police_pdf: str, flags: int) -> str:
    n = (nom_police_pdf or "").lower()
    if flags & 8 or any(m in n for m in ("mono", "courier", "consolas", "typewriter")):
        return "mono"
    if flags & 4 or any(m in n for m in ("times", "serif", "georgia", "garamond", "palatino", "book", "minion", "cambria")) and "sans" not in n:
        return "serif"
    return "sans"


def lignes_texte(ident: str, numero: int) -> list:
    """Lignes de texte modifiables d'une page : [{"i", "b": [x0, y0, x1, y1], "t", "police", "gras", "italique", "taille", "couleur", "police_pdf"}]."""
    meta = _lire_meta(ident)
    doc = pymupdf.open(_chemin_version(ident, meta["version"]))
    try:
        if not 0 <= numero < doc.page_count:
            raise ErreurPDF("Page introuvable.")
        page = doc[numero]
        if page.rotation:
            return []
        lignes = []
        for bloc in page.get_text("dict").get("blocks", []):
            for ligne in bloc.get("lines", []) if bloc.get("type") == 0 else []:
                if abs(ligne.get("dir", (1, 0))[0] - 1) > 0.01:                       # texte penche ou vertical : non modifiable ici
                    continue
                spans = [s for s in ligne.get("spans", []) if s.get("text", "")]
                texte = "".join(s["text"] for s in spans)
                if not texte.strip():
                    continue
                principal = max(spans, key=lambda s: len(s["text"].strip()))
                nom_pdf = principal.get("font", "")
                lignes.append({
                    "i": len(lignes), "b": [round(v, 2) for v in ligne["bbox"]], "t": texte, "police": _famille(nom_pdf, principal.get("flags", 0)),
                    "gras": bool(principal.get("flags", 0) & 16) or any(m in nom_pdf.lower() for m in ("bold", "black", "heavy", "semibold")),
                    "italique": bool(principal.get("flags", 0) & 2) or any(m in nom_pdf.lower() for m in ("italic", "oblique")),
                    "taille": round(principal.get("size", 11), 1), "couleur": _hex(principal.get("color", 0)), "police_pdf": nom_pdf.split("+")[-1],
                    "origine": [round(v, 2) for v in principal.get("origin", (ligne["bbox"][0], ligne["bbox"][3]))],
                })
        return lignes
    finally:
        doc.close()


# ---------- operations (chacune modifie un Document ouvert et renvoie un message eventuel)

def _page(doc: pymupdf.Document, numero: int, modifiable: bool = True) -> pymupdf.Page:
    if not isinstance(numero, int) or not 0 <= numero < doc.page_count:
        raise ErreurPDF("Page introuvable.")
    page = doc[numero]
    if modifiable and page.rotation:
        raise ErreurPDF("Cette page est pivotée : remettez-la à l'endroit (bouton ↻ de la vignette) avant de la modifier.")
    return page


def _rect(valeur, page: pymupdf.Page, minimum: float = 2.0) -> pymupdf.Rect:
    try:
        x0, y0, x1, y1 = [float(v) for v in valeur]
    except (TypeError, ValueError):
        raise ErreurPDF("Zone invalide.")
    r = pymupdf.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) & page.rect
    if r.is_empty or r.width < minimum or r.height < minimum:
        raise ErreurPDF("Zone trop petite : dessinez un rectangle plus grand.")
    return r


def _couleur_fond(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple:
    """Couleur dominante juste autour d'une zone (pour effacer un texte sans laisser de trace sur un fond colore)."""
    marge = 2.0
    zone = pymupdf.Rect(rect.x0 - marge, rect.y0 - marge, rect.x1 + marge, rect.y1 + marge) & page.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=zone, alpha=False)
    larg, haut, n = pix.width, pix.height, pix.n
    if not larg or not haut:
        return (1, 1, 1)
    echantillons = pix.samples
    compte = {}
    def prendre(x, y):
        i = (y * larg + x) * n
        cle = tuple(int(echantillons[i + k]) // 8 for k in range(3))
        compte[cle] = compte.get(cle, 0) + 1
    for x in range(larg):
        prendre(x, 0); prendre(x, haut - 1)
    for y in range(haut):
        prendre(0, y); prendre(larg - 1, y)
    cle = max(compte, key=compte.get)
    return tuple(min(1.0, (v * 8 + 4) / 255) for v in cle)


def op_texte_modifier(doc, p: dict) -> str:
    """Remplace le texte d'une ligne par un autre, a la meme place (l'ancien texte est vraiment supprime du PDF)."""
    page = _page(doc, p.get("page"))
    nouveau = str(p.get("texte", ""))
    if len(nouveau) > 2000:
        raise ErreurPDF("Texte trop long.")
    police = nom_police(p.get("police", "sans"), bool(p.get("gras")), bool(p.get("italique")))
    taille = max(4.0, min(float(p.get("taille") or 11), 200.0))
    couleur = _rgb(p.get("couleur", "#000000"))
    zone = _rect(p.get("bbox"), page)
    origine = p.get("origine") or [zone.x0, zone.y1 - taille * 0.22]
    manquants = _caracteres_absents(nouveau, police)
    if manquants:
        raise ErreurPDF("Caractère(s) non pris en charge par cette police : " + " ".join(sorted(manquants)[:8]) + ". Retirez-les ou changez de police.")
    fond = _couleur_fond(page, zone)
    effacement = pymupdf.Rect(zone.x0 - 0.5, zone.y0 + zone.height * 0.08, zone.x1 + 0.5, zone.y1 - zone.height * 0.08)
    page.add_redact_annot(effacement, fill=fond)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    if not nouveau.strip():
        return "Texte supprimé."
    largeur_max = max(page.rect.width - zone.x0 - 18, zone.width)
    plus_large = _largeur(nouveau, police, taille)
    message = ""
    if plus_large > largeur_max:
        taille = max(taille * 0.6, taille * largeur_max / plus_large)
        plus_large = _largeur(nouveau, police, taille)
        message = "Texte réduit pour tenir dans la page."
    x = float(origine[0])
    centre_ancien = (zone.x0 + zone.x1) / 2
    if abs(centre_ancien - page.rect.width / 2) < 4 and zone.width < page.rect.width * 0.9:       # ligne centree : le nouveau texte reste centre
        x = page.rect.width / 2 - plus_large / 2
    _ecrire(page, x, float(origine[1]), nouveau, police, taille, couleur)
    return message


def _caracteres_absents(texte: str, nom: str) -> set:
    font = pymupdf.Font(nom)
    return {c for c in texte if c not in "\n\r\t" and not font.has_glyph(ord(c))}


def _largeur(texte: str, nom: str, taille: float) -> float:
    font = pymupdf.Font(nom)
    return max((font.text_length(ligne, fontsize=taille) for ligne in texte.split("\n")), default=0.0)


def _ecrire(page, x: float, y: float, texte: str, nom: str, taille: float, couleur: tuple, opacite: float = 1.0, morph=None, interligne: float = 1.15) -> None:
    """Ecrit un texte (plusieurs lignes possibles) dont (x, y) est le point de depart de la premiere ligne, avec des polices qui gerent les accents, l'euro, etc."""
    font = pymupdf.Font(nom)
    ecrivain = pymupdf.TextWriter(page.rect)
    for k, ligne in enumerate(texte.split("\n")):
        ecrivain.append((x, y + k * taille * interligne), ligne, font=font, fontsize=taille)
    ecrivain.write_text(page, color=couleur, opacity=opacite, morph=morph, overlay=True)


def op_texte_ajouter(doc, p: dict) -> str:
    page = _page(doc, p.get("page"))
    texte = str(p.get("texte", ""))
    if not texte.strip():
        raise ErreurPDF("Texte vide.")
    if len(texte) > 2000:
        raise ErreurPDF("Texte trop long.")
    police = nom_police(p.get("police", "sans"), bool(p.get("gras")), bool(p.get("italique")))
    taille = max(4.0, min(float(p.get("taille") or 14), 200.0))
    manquants = _caracteres_absents(texte, police)
    if manquants:
        raise ErreurPDF("Caractère(s) non pris en charge par cette police : " + " ".join(sorted(manquants)[:8]) + ".")
    try:
        x, y = float(p["x"]), float(p["y"])
    except (KeyError, TypeError, ValueError):
        raise ErreurPDF("Position invalide.")
    if not page.rect.contains(pymupdf.Point(x, y)):
        raise ErreurPDF("Cliquez à l'intérieur de la page.")
    _ecrire(page, x, y + taille, texte, police, taille, _rgb(p.get("couleur", "#000000")))
    return ""


def op_image(doc, p: dict, image: bytes = b"") -> str:
    page = _page(doc, p.get("page"))
    zone = _rect(p.get("bbox"), page, minimum=6)
    try:
        img = Image.open(io.BytesIO(image))
        img.load()
    except Exception:
        raise ErreurPDF("Image illisible (JPG ou PNG).")
    tampon = io.BytesIO()
    img.save(tampon, "PNG" if img.mode in ("RGBA", "LA", "P") else "JPEG", **({} if img.mode in ("RGBA", "LA", "P") else {"quality": 92}))
    page.insert_image(zone, stream=tampon.getvalue(), keep_proportion=True)
    return ""


def op_surligner(doc, p: dict) -> str:
    page = _page(doc, p.get("page"))
    zone = _rect(p.get("bbox"), page)
    page.draw_rect(zone, color=None, fill=_rgb(p.get("couleur", "#ffe600")), fill_opacity=0.4, overlay=True)
    return ""


def op_caviarder(doc, p: dict) -> str:
    """Efface vraiment le contenu d'une zone (texte et images dessous inclus) et la remplit d'une couleur."""
    page = _page(doc, p.get("page"))
    zone = _rect(p.get("bbox"), page)
    page.add_redact_annot(zone, fill=_rgb(p.get("couleur", "#000000")))
    page.apply_redactions()
    return "Zone caviardée : le contenu dessous est supprimé du fichier."


def op_pages_ordre(doc, p: dict) -> str:
    """Nouvel ordre des pages (liste de numeros, 0 = premiere) ; une page absente de la liste est supprimee, une page repetee est dupliquee."""
    ordre = p.get("ordre")
    if not isinstance(ordre, list) or not ordre or any(not isinstance(i, int) or not 0 <= i < doc.page_count for i in ordre):
        raise ErreurPDF("Ordre des pages invalide.")
    if len(ordre) > PAGES_MAX:
        raise ErreurPDF(f"Maximum {PAGES_MAX} pages.")
    doc.select(ordre)
    return ""


def op_pivoter(doc, p: dict) -> str:
    angle = p.get("angle", 90)
    if angle not in (90, 180, 270, -90):
        raise ErreurPDF("Angle invalide.")
    pages = p.get("pages")
    for n in (pages if isinstance(pages, list) else [p.get("page")]):
        page = _page(doc, n, modifiable=False)
        page.set_rotation((page.rotation + angle) % 360)
    return ""


def op_fusionner(doc, p: dict, autre: bytes = b"") -> str:
    """Ajoute les pages d'un autre PDF : a la fin, ou apres la page indiquee."""
    mot_de_passe = str(p.get("mot_de_passe_autre", ""))
    source = ouvrir_octets(autre, mot_de_passe)
    try:
        if doc.page_count + source.page_count > PAGES_MAX:
            raise ErreurPDF(f"Trop de pages au total (maximum {PAGES_MAX}).")
        apres = p.get("apres")
        position = doc.page_count if apres is None else max(0, min(int(apres) + 1, doc.page_count))
        doc.insert_pdf(source, start_at=position)
        return f"{source.page_count} page(s) ajoutée(s)."
    finally:
        source.close()


EMPLACEMENTS = {"bas-centre", "bas-droite", "bas-gauche", "haut-centre", "haut-droite", "haut-gauche"}


def op_numeroter(doc, p: dict) -> str:
    """Numeros de page : format avec {n} (numero) et {total}."""
    emplacement = p.get("emplacement", "bas-centre")
    if emplacement not in EMPLACEMENTS:
        raise ErreurPDF("Emplacement invalide.")
    modele = str(p.get("format") or "{n}")[:60]
    debut = int(p.get("debut") or 1)
    taille = max(6.0, min(float(p.get("taille") or 10), 40.0))
    couleur = _rgb(p.get("couleur", "#444444"))
    total = doc.page_count
    manquants = _caracteres_absents(modele, "helv")
    if manquants:
        raise ErreurPDF("Caractère(s) non pris en charge : " + " ".join(sorted(manquants)) + ".")
    for i, page in enumerate(doc):
        if page.rotation:
            continue                                   # une page pivotee n'est pas numerotee (le numero serait de travers)
        texte = modele.replace("{n}", str(debut + i)).replace("{total}", str(total + debut - 1))
        largeur = _largeur(texte, "helv", taille)
        marge = 28
        x = {"gauche": marge, "centre": (page.rect.width - largeur) / 2, "droite": page.rect.width - marge - largeur}[emplacement.split("-")[1]]
        y = page.rect.height - marge + taille * 0.3 if emplacement.startswith("bas") else marge + taille * 0.7
        _ecrire(page, x, y, texte, "helv", taille, couleur)
    ignorees = sum(1 for page in doc if page.rotation)
    return f"{ignorees} page(s) pivotée(s) non numérotée(s)." if ignorees else ""


def op_filigrane(doc, p: dict) -> str:
    texte = str(p.get("texte", "")).strip()[:60]
    if not texte:
        raise ErreurPDF("Texte du filigrane vide.")
    manquants = _caracteres_absents(texte, "hebo")
    if manquants:
        raise ErreurPDF("Caractère(s) non pris en charge : " + " ".join(sorted(manquants)) + ".")
    opacite = max(0.05, min(float(p.get("opacite") or 0.18), 1.0))
    angle = max(-90, min(int(p.get("angle", 45)), 90))
    couleur = _rgb(p.get("couleur", "#888888"))
    for page in doc:
        if page.rotation:
            continue
        r = page.rect
        taille = max(18.0, min(120.0, r.width * 0.9 / max(1.0, _largeur(texte, "hebo", 1)) / 1.15))
        largeur = _largeur(texte, "hebo", taille)
        centre = pymupdf.Point(r.width / 2, r.height / 2)
        depart = pymupdf.Point(centre.x - largeur / 2, centre.y + taille * 0.3)
        _ecrire(page, depart.x, depart.y, texte, "hebo", taille, couleur, opacite=opacite, morph=(centre, pymupdf.Matrix(-angle)))
    return ""


NIVEAUX_COMPRESSION = {"legere": (220, 150, 85), "forte": (150, 110, 70), "maximale": (110, 72, 55)}     # (seuil dpi, dpi cible, qualite JPEG)


def op_compresser(doc, p: dict) -> str:
    niveau = p.get("niveau", "forte")
    if niveau not in NIVEAUX_COMPRESSION:
        raise ErreurPDF("Niveau de compression invalide.")
    seuil, cible, qualite = NIVEAUX_COMPRESSION[niveau]
    try:
        doc.rewrite_images(dpi_threshold=seuil, dpi_target=cible, quality=qualite)
    except Exception:
        pass                                                   # PDF sans image ou image non reencodable : le nettoyage ci-dessous suffit
    return ""


OPERATIONS = {
    "texte_modifier": op_texte_modifier, "texte_ajouter": op_texte_ajouter, "surligner": op_surligner, "caviarder": op_caviarder, "ordre": op_pages_ordre,
    "pivoter": op_pivoter, "numeroter": op_numeroter, "filigrane": op_filigrane, "compresser": op_compresser,
}
OPERATIONS_AVEC_FICHIER = {"image": op_image, "fusionner": op_fusionner}


def appliquer(ident: str, operation: str, params: dict, fichier: bytes = b"") -> dict:
    """Applique une operation au document courant et enregistre le resultat comme nouvelle version (annulable). Renvoie {"message", "etat"}."""
    if operation not in OPERATIONS and operation not in OPERATIONS_AVEC_FICHIER:
        raise ErreurPDF("Opération inconnue.")
    with _verrou(ident):
        meta = _lire_meta(ident)
        avant = _chemin_version(ident, meta["version"])
        taille_avant = os.path.getsize(avant)
        doc = pymupdf.open(avant)
        try:
            if operation in OPERATIONS_AVEC_FICHIER:
                message = OPERATIONS_AVEC_FICHIER[operation](doc, params, fichier)
            else:
                message = OPERATIONS[operation](doc, params)
            nouvelle = meta["version"] + 1
            if operation == "compresser":
                doc.save(_chemin_version(ident, nouvelle), garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
            else:
                _sauver(doc, _chemin_version(ident, nouvelle))
        except ErreurPDF:
            raise
        except Exception as erreur:
            raise ErreurPDF(f"Modification impossible : {str(erreur)[:150]}")
        finally:
            doc.close()
        if operation == "compresser":
            apres = os.path.getsize(_chemin_version(ident, nouvelle))
            if apres >= taille_avant:
                os.remove(_chemin_version(ident, nouvelle))
                return {"message": "Ce PDF est déjà optimisé : la compression ne le réduirait pas, il est inchangé.", "etat": etat(ident), "inchange": True}
            message = f"Taille réduite de {_taille(taille_avant)} à {_taille(apres)} ({round((1 - apres / taille_avant) * 100)} % de moins)."
        meta["version"] = nouvelle
        while meta["version"] - meta["plus_ancienne"] > VERSIONS_MAX:
            try:
                os.remove(_chemin_version(ident, meta["plus_ancienne"]))
            except OSError:
                pass
            meta["plus_ancienne"] += 1
        _ecrire_meta(ident, meta)
        return {"message": message, "etat": etat(ident)}


def annuler(ident: str) -> dict:
    """Revient a la version precedente."""
    with _verrou(ident):
        meta = _lire_meta(ident)
        if meta["version"] <= meta["plus_ancienne"]:
            raise ErreurPDF("Rien à annuler.")
        try:
            os.remove(_chemin_version(ident, meta["version"]))
        except OSError:
            pass
        meta["version"] -= 1
        _ecrire_meta(ident, meta)
        return {"message": "Dernière modification annulée.", "etat": etat(ident)}


def _taille(octets: int) -> str:
    return f"{octets / 1048576:.1f} Mo".replace(".", ",") if octets >= 1048576 else f"{max(1, round(octets / 1024))} Ko"


# ---------- exports

def _nom_sortie(ident: str, suffixe: str, extension: str) -> str:
    base = os.path.splitext(_lire_meta(ident)["nom"])[0]
    return f"{base}{suffixe}.{extension}"


def exporter_pdf(ident: str, pages: list = None, mot_de_passe: str = "") -> tuple:
    """(octets, nom de fichier) : le PDF courant, ou seulement les pages demandees, avec ou sans mot de passe d'ouverture."""
    meta = _lire_meta(ident)
    doc = pymupdf.open(_chemin_version(ident, meta["version"]))
    try:
        suffixe = ""
        if pages:
            if any(not isinstance(i, int) or not 0 <= i < doc.page_count for i in pages):
                raise ErreurPDF("Pages invalides.")
            doc.select(pages)
            suffixe = "-extrait"
        options = {"garbage": 3, "deflate": True}
        if mot_de_passe:
            if len(mot_de_passe) < 4 or len(mot_de_passe) > 64:
                raise ErreurPDF("Le mot de passe doit faire de 4 à 64 caractères.")
            options.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=mot_de_passe, owner_pw=secrets.token_hex(12))
            suffixe += "-protege"
        return doc.tobytes(**options), _nom_sortie(ident, suffixe, "pdf")
    finally:
        doc.close()


def exporter_images(ident: str, pages: list = None, format_image: str = "png", dpi: int = 150) -> tuple:
    """(octets du ZIP, nom) : les pages (toutes ou choisies) en images numerotees."""
    if format_image not in ("png", "jpg"):
        raise ErreurPDF("Format d'image invalide.")
    dpi = max(50, min(int(dpi), 300))
    meta = _lire_meta(ident)
    doc = pymupdf.open(_chemin_version(ident, meta["version"]))
    try:
        numeros = pages or list(range(doc.page_count))
        if any(not isinstance(i, int) or not 0 <= i < doc.page_count for i in numeros):
            raise ErreurPDF("Pages invalides.")
        sortie = io.BytesIO()
        base = os.path.splitext(meta["nom"])[0]
        with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as z:
            for i in numeros:
                pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
                z.writestr(f"{base}-page-{i + 1:03d}.{format_image}", pix.tobytes("png" if format_image == "png" else "jpeg"))
        return sortie.getvalue(), _nom_sortie(ident, "-images", "zip")
    finally:
        doc.close()


def decouper(ident: str, plages: str) -> tuple:
    """(ZIP, nom) : un PDF par plage, ex. « 1-3, 4-6, 7 » (numeros affiches, la premiere page est la 1)."""
    meta = _lire_meta(ident)
    doc = pymupdf.open(_chemin_version(ident, meta["version"]))
    try:
        morceaux = []
        for partie in re.split(r"[;,]", plages or ""):
            partie = partie.strip()
            if not partie:
                continue
            m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", partie)
            if not m:
                raise ErreurPDF(f"Plage invalide : « {partie} ».")
            debut, fin = int(m.group(1)), int(m.group(2) or m.group(1))
            if not 1 <= debut <= fin <= doc.page_count:
                raise ErreurPDF(f"La plage « {partie} » sort du document ({doc.page_count} pages).")
            morceaux.append((debut, fin))
        if not morceaux:
            raise ErreurPDF("Indiquez au moins une plage, par exemple 1-3, 4-6.")
        if len(morceaux) > 100:
            raise ErreurPDF("Trop de plages (maximum 100).")
        sortie = io.BytesIO()
        base = os.path.splitext(meta["nom"])[0]
        with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as z:
            for debut, fin in morceaux:
                part = pymupdf.open()
                try:
                    part.insert_pdf(doc, from_page=debut - 1, to_page=fin - 1)
                    z.writestr(f"{base}-pages-{debut}" + (f"-{fin}" if fin != debut else "") + ".pdf", part.tobytes(garbage=3, deflate=True))
                finally:
                    part.close()
        return sortie.getvalue(), _nom_sortie(ident, "-decoupe", "zip")
    finally:
        doc.close()
