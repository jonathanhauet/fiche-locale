"""
Polices du client pour les visuels (carrousels, avis, titres sur photo, modeles) : fichiers TTF de Google Fonts, telecharges a la demande et gardes sur le disque temporaire
du serveur (re-telecharges apres un redemarrage). Seules les polices de Google Fonts sont possibles ; sinon les visuels gardent la police par defaut de la plateforme.
"""

import os
import re
import tempfile
import threading
import time
from urllib.parse import urlparse

import requests

DOSSIER = os.path.join(tempfile.gettempdir(), "fiche_locale_polices")
URL_CSS = "https://fonts.googleapis.com/css2"
DELAI = 10
TAILLE_MAX = 3_000_000
DELAI_ECHEC = 300                       # apres un echec, on ne reessaie pas avant 5 minutes (un rendu ne doit jamais attendre Google a chaque image)
SIGNATURES = (b"\x00\x01\x00\x00", b"true", b"OTTO")
_echecs = {}
_verrou = threading.Lock()


def nom_valide(famille: str) -> str:
    """Nom de famille Google Fonts nettoye (lettres, chiffres, espaces), ou '' s'il est impossible."""
    famille = " ".join(str(famille or "").split())[:60]
    return famille if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ]*", famille) else ""


def _fichier(famille: str, graisse: int) -> str:
    return os.path.join(DOSSIER, f"{re.sub(r'[^a-z0-9]+', '-', famille.lower()).strip('-')}-{graisse}.ttf")


def _telecharger(famille: str, graisse: int, chemin: str) -> bool:
    for params in ({"family": f"{famille}:wght@{graisse}"}, {"family": famille}):             # la graisse demandee n'existe pas : on prend celle par defaut
        try:
            css = requests.get(URL_CSS, params=params, timeout=DELAI)
            if css.status_code != 200:
                continue
            m = re.search(r"url\(([^)]+)\)\s*format\('truetype'\)", css.text)
            if not m:
                continue
            adresse = m.group(1).strip("'\"")
            if not (urlparse(adresse).hostname or "").endswith("gstatic.com"):
                continue
            police = requests.get(adresse, timeout=DELAI + 10)
            if police.status_code != 200 or not 1000 < len(police.content) <= TAILLE_MAX or police.content[:4] not in SIGNATURES:
                continue
            os.makedirs(DOSSIER, exist_ok=True)
            temporaire = f"{chemin}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(temporaire, "wb") as f:
                f.write(police.content)
            os.replace(temporaire, chemin)
            return True
        except (requests.RequestException, OSError):
            continue
    return False


def chemin(famille: str, graisse: int = 400):
    """Chemin du fichier TTF de la famille Google Fonts (graisse 400 ou 700), telecharge si besoin ; None si indisponible."""
    famille = nom_valide(famille)
    if not famille:
        return None
    fichier = _fichier(famille, graisse)
    if os.path.isfile(fichier):
        return fichier
    cle = (famille.lower(), graisse)
    with _verrou:
        if time.monotonic() - _echecs.get(cle, -DELAI_ECHEC) < DELAI_ECHEC:
            return None
    if _telecharger(famille, graisse, fichier):
        return fichier
    with _verrou:
        _echecs[cle] = time.monotonic()
    return None


def verifier(famille: str) -> dict:
    """{"ok": bool, "famille": nom propre, "message": ...} : la famille est-elle disponible sur Google Fonts (normal et gras) ?"""
    nom = nom_valide(famille)
    if not famille or not str(famille).strip():
        return {"ok": False, "famille": "", "message": ""}
    if not nom:
        return {"ok": False, "famille": "", "message": "Nom de police invalide (lettres, chiffres et espaces uniquement)."}
    with _verrou:
        for graisse in (400, 700):
            _echecs.pop((nom.lower(), graisse), None)                       # une verification explicite relance toujours le telechargement
    normal, gras = chemin(nom, 400), chemin(nom, 700)
    if normal or gras:
        return {"ok": True, "famille": nom, "message": f"« {nom} » est disponible : les visuels l'utiliseront."}
    return {"ok": False, "famille": nom, "message": f"« {nom} » est introuvable sur Google Fonts (police payante ou propre au site ?) : les visuels garderont la police par défaut."}


def preparer(*familles) -> None:
    """Telecharge d'avance les polices (appel en arriere-plan apres l'enregistrement de la charte)."""
    for famille in familles:
        if famille and str(famille).strip():
            verifier(famille)
