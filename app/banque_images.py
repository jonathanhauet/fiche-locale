"""
Banque d'images libres de droits (Pixabay) pour les videos de motion design : des photos d'ambiance (accroche, marque, appel a l'action) qui
completent les photos du client. Recherche par mot-cle, puis telechargement cote serveur : la photo choisie est ensuite traitee comme une image
importee (voir motion_video.enregistrer_import), donc le rendu n'a rien de particulier.

Licence Pixabay (Content License) : usage commercial gratuit, sans credit obligatoire, modification permise ; interdit : presenter le contenu de
facon trompeuse, l'utiliser tel quel comme produit, ou l'associer a une marque reconnaissable. La cle d'API est gratuite (compte Pixabay) :
variable PIXABAY_API_KEY. Sans cle, la fonction est simplement masquee. Regles de l'API : 100 requetes par minute, reponses gardees 24 heures,
jamais de lien permanent vers leurs images (on telecharge), provenance affichee dans le selecteur. Sans « acces complet », la meilleure taille
est de 1280 px.
"""

import os
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLE_PIXABAY = os.getenv("PIXABAY_API_KEY")
URL_API = "https://pixabay.com/api/"
HOTES_IMAGES = ("pixabay.com", "cdn.pixabay.com")
ORIENTATIONS = {"portrait": "vertical", "landscape": "horizontal"}           # un format carre n'a pas de filtre : toutes les orientations
TAILLE_MAX = 8 * 1024 * 1024
PAR_PAGE = 24
DUREE_CACHE = 24 * 3600                 # exigence de Pixabay : une meme recherche n'est pas redemandee avant 24 heures
MAX_CACHE = 300
_CACHE = {}


class ErreurBanque(Exception):
    pass


def configuree() -> bool:
    return bool(CLE_PIXABAY)


def _appeler(params: dict) -> dict:
    if not CLE_PIXABAY:
        raise ErreurBanque("Banque d'images non configurée (PIXABAY_API_KEY manquante).")
    try:
        reponse = requests.get(URL_API, params={**params, "key": CLE_PIXABAY}, timeout=15)
    except requests.RequestException as erreur:
        raise ErreurBanque("Banque d'images injoignable pour le moment.") from erreur
    if reponse.status_code == 429:
        raise ErreurBanque("Trop de recherches pour l'instant : réessayez dans une minute.")
    if reponse.status_code in (400, 401, 403):
        raise ErreurBanque("Clé Pixabay refusée : vérifiez PIXABAY_API_KEY.")
    if reponse.status_code != 200:
        raise ErreurBanque(f"Banque d'images indisponible (code {reponse.status_code}).")
    try:
        return reponse.json()
    except ValueError as erreur:
        raise ErreurBanque("Réponse illisible de la banque d'images.") from erreur


def _resume(photo: dict) -> dict:
    return {
        "id": photo["id"], "miniature": photo["previewURL"], "description": (photo.get("tags") or "")[:140],
        "photographe": photo.get("user", ""), "lien": photo.get("pageURL", ""),
    }


def rechercher(requete: str, page: int = 1, orientation: str = "") -> dict:
    """{"photos": [{id, miniature, description, photographe, lien}], "suivante": bool}. Les mots-cles francais sont compris (lang=fr)."""
    requete = " ".join((requete or "").split())[:100]
    if not requete:
        raise ErreurBanque("Indiquez ce que vous cherchez (ex. : plombier, toit, cuisine moderne).")
    page = max(1, min(int(page or 1), 20))
    params = {"q": requete, "lang": "fr", "image_type": "photo", "safesearch": "true", "per_page": PAR_PAGE, "page": page}
    if orientation in ORIENTATIONS:
        params["orientation"] = ORIENTATIONS[orientation]
    cle = (requete.lower(), page, orientation)
    maintenant = time.time()
    for ancienne in [c for c, (_, t) in _CACHE.items() if maintenant - t > DUREE_CACHE]:
        _CACHE.pop(ancienne, None)
    if cle not in _CACHE:
        donnees = _appeler(params)
        photos = [_resume(p) for p in donnees.get("hits", []) if p.get("id") and p.get("previewURL")]
        if len(_CACHE) >= MAX_CACHE:
            _CACHE.pop(min(_CACHE, key=lambda c: _CACHE[c][1]), None)
        _CACHE[cle] = ({"photos": photos, "suivante": page * PAR_PAGE < min(int(donnees.get("totalHits") or 0), 500)}, maintenant)
    return _CACHE[cle][0]


def telecharger(photo_id) -> tuple:
    """
    (octets de la photo en 1280 px, {"photographe", "lien"}). L'adresse du fichier vient toujours de Pixabay (jamais du navigateur) et doit etre sur
    ses serveurs, y compris apres une redirection : on ne telecharge rien d'autre.
    """
    try:
        photo_id = int(photo_id)
    except (TypeError, ValueError) as erreur:
        raise ErreurBanque("Photo inconnue.") from erreur
    resultats = _appeler({"id": photo_id}).get("hits") or []
    if not resultats:
        raise ErreurBanque("Photo introuvable sur Pixabay.")
    url = resultats[0].get("largeImageURL") or ""
    adresse = urlparse(url)
    if adresse.scheme != "https" or adresse.hostname not in HOTES_IMAGES:
        raise ErreurBanque("Photo indisponible.")
    try:
        reponse = requests.get(url, timeout=30, headers={"User-Agent": "FicheLocale/1.0"})
    except requests.RequestException as erreur:
        raise ErreurBanque("Téléchargement de la photo impossible pour le moment.") from erreur
    if urlparse(str(getattr(reponse, "url", "") or url)).hostname not in HOTES_IMAGES:
        raise ErreurBanque("Photo indisponible.")
    if reponse.status_code != 200 or not reponse.content:
        raise ErreurBanque("Téléchargement de la photo impossible pour le moment.")
    if len(reponse.content) > TAILLE_MAX:
        raise ErreurBanque("Photo trop lourde (8 Mo au plus).")
    return reponse.content, {"photographe": resultats[0].get("user", ""), "lien": resultats[0].get("pageURL", "")}
