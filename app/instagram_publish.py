"""
Publication sur un compte Instagram professionnel via l'API Graph
(graph.instagram.com, pas graph.facebook.com) - voir instagram_oauth.py pour
la connexion. Le token utilise est celui du compte Instagram lui-meme
(Client.token_instagram), distinct du token de Page Facebook.
"""

import time

import requests

from .instagram_oauth import URL_GRAPH

ATTENTE_CONTENEUR_SECONDES = 2
TENTATIVES_MAX_CONTENEUR = 15
# Une video (Reel) est traitee bien plus lentement qu'une image par Instagram
# (encodage) : jusqu'a plusieurs minutes contre 2-4 secondes pour une image.
ATTENTE_CONTENEUR_VIDEO_SECONDES = 5
TENTATIVES_MAX_CONTENEUR_VIDEO = 72  # jusqu'a 6 minutes
# Meme quand le conteneur annonce FINISHED, media_publish peut repondre « media pas pret » (code 9007, sous-code 2207027),
# surtout pour un carrousel : on reessaie quelques secondes plus tard plutot que d'echouer.
TENTATIVES_PUBLICATION = 8
ATTENTE_PUBLICATION_SECONDES = 5
# Instagram telecharge lui-meme l'image ou la video depuis notre hebergement : il echoue parfois ponctuellement (« Media download has failed »,
# code 2207052, ou « Timeout », 2207003), alors que le fichier est bien en ligne. On reessaie apres ces pauses (en secondes) avant d'abandonner.
ATTENTES_TELECHARGEMENT = (10, 30, 60)


def _telechargement_rate(reponse) -> bool:
    """Instagram n'a pas reussi a telecharger le media (erreur passagere, a reessayer), par opposition a un refus definitif (token, format...)."""
    if reponse.status_code not in (400, 500, 502, 503, 504):
        return False
    texte = reponse.text.replace(" ", "").lower()
    return any(m in texte for m in ("2207052", "2207003", "mediadownloadhasfailed", '"message":"timeout"', '"is_transient":true'))


def _creer_conteneur(instagram_id: str, donnees: dict, libelle: str, attentes: tuple = None) -> str:
    """Cree un conteneur media et renvoie son id ; reessaie si Instagram n'a pas pu telecharger le media."""
    attentes = ATTENTES_TELECHARGEMENT if attentes is None else attentes
    for essai in range(len(attentes) + 1):
        reponse = requests.post(f"{URL_GRAPH}/{instagram_id}/media", data=donnees, timeout=30)
        if reponse.status_code == 200:
            return reponse.json()["id"]
        if essai < len(attentes) and _telechargement_rate(reponse):
            time.sleep(attentes[essai])
            continue
        raise RuntimeError(f"Echec de la creation {libelle} (code {reponse.status_code}) : {reponse.text}")
    raise RuntimeError(f"Echec de la creation {libelle}.")


def _attendre_conteneur_pret(
    instagram_id: str, conteneur_id: str, token_instagram: str,
    tentatives: int = TENTATIVES_MAX_CONTENEUR, attente_secondes: float = ATTENTE_CONTENEUR_SECONDES,
) -> None:
    """
    Instagram traite le media de facon asynchrone apres la creation du
    conteneur - le publier trop tot echoue avec "The media is not ready for
    publishing" (constate en reel). On interroge son status_code jusqu'a
    FINISHED (ou une erreur/un delai trop long). tentatives/attente_secondes
    plus genereuses pour une video (voir publier_reel) que pour une image.
    """
    for _ in range(tentatives):
        reponse = requests.get(
            f"{URL_GRAPH}/{conteneur_id}", params={"fields": "status_code", "access_token": token_instagram}, timeout=15,
        )
        if reponse.status_code == 200:
            statut = reponse.json().get("status_code")
            if statut == "FINISHED":
                return
            if statut == "ERROR":
                raise RuntimeError("Echec du traitement du conteneur media Instagram (statut ERROR).")
            if statut == "EXPIRED":
                raise RuntimeError("Le conteneur media Instagram a expire avant d'etre publie.")
        time.sleep(attente_secondes)
    raise RuntimeError("Le conteneur media Instagram n'est pas devenu pret a temps.")


def _media_pas_pret(reponse) -> bool:
    texte = reponse.text.replace(" ", "").lower()
    return reponse.status_code == 400 and ("2207027" in texte or '"code":9007' in texte or "notreadyforpublishing" in texte)


def _media_publish(token_instagram: str, instagram_id: str, conteneur_id: str, libelle: str) -> dict:
    """Publie un conteneur deja traite, en reessayant si Instagram repond que le media n'est pas encore pret."""
    for tentative in range(1, TENTATIVES_PUBLICATION + 1):
        reponse = requests.post(
            f"{URL_GRAPH}/{instagram_id}/media_publish",
            data={"creation_id": conteneur_id, "access_token": token_instagram},
            timeout=30,
        )
        if reponse.status_code == 200:
            return reponse.json()
        if tentative < TENTATIVES_PUBLICATION and _media_pas_pret(reponse):
            time.sleep(ATTENTE_PUBLICATION_SECONDES)
            continue
        raise RuntimeError(f"Echec de la publication {libelle} (code {reponse.status_code}) : {reponse.text}")
    raise RuntimeError(f"Echec de la publication {libelle} : media jamais pret.")


def _publier_conteneur(token_instagram: str, instagram_id: str, conteneur_id: str) -> dict:
    _attendre_conteneur_pret(instagram_id, conteneur_id, token_instagram)
    return _media_publish(token_instagram, instagram_id, conteneur_id, "du conteneur Instagram")


def publier_carrousel(token_instagram: str, instagram_id: str, image_urls: list[str], caption: str = "") -> dict:
    """
    Carrousel (2 a 10 images) : 1) un conteneur "element de carrousel" par
    image, chacun attendu jusqu'a FINISHED, 2) un conteneur CAROUSEL qui les
    reference et porte la legende, 3) publication de celui-ci. Toutes les
    images sont recadrees au format de la premiere par Instagram.
    """
    enfants = []
    for indice, url_image in enumerate(image_urls, start=1):
        enfants.append(_creer_conteneur(
            instagram_id, {"image_url": url_image, "is_carousel_item": "true", "access_token": token_instagram}, f"de l'image {indice} du carrousel Instagram",
        ))
    for enfant in enfants:
        _attendre_conteneur_pret(instagram_id, enfant, token_instagram)

    carrousel_id = _creer_conteneur(
        instagram_id, {"media_type": "CAROUSEL", "children": ",".join(enfants), "caption": caption, "access_token": token_instagram}, "du carrousel Instagram",
    )
    return _publier_conteneur(token_instagram, instagram_id, carrousel_id)


def publier_medias(token_instagram: str, instagram_id: str, image_urls: list[str], caption: str = "") -> dict:
    """Une image : publication classique. Plusieurs : carrousel."""
    if len(image_urls) > 1:
        return publier_carrousel(token_instagram, instagram_id, image_urls, caption)
    return publier_photo(token_instagram, instagram_id, image_urls[0], caption)


def publier_reel(token_instagram: str, instagram_id: str, video_url: str, caption: str = "", couverture_url: str = None) -> dict:
    """
    Publie une video en Reel (media_type=REELS - toute nouvelle video postee
    via l'API rejoint desormais les Reels, il n'y a plus de "video de feed"
    distincte cote Instagram). video_url doit etre accessible publiquement
    (comme image_url pour une photo, voir publier_photo) : Instagram la
    televerse lui-meme.
    couverture_url : image de couverture du Reel (fil et grille du profil, voir
    couverture_video.py) ; sans elle Instagram prend la 1re image de la video.
    Si Instagram la refuse, le Reel repart sans elle plutot que d'echouer.
    """
    donnees = {"media_type": "REELS", "video_url": video_url, "caption": caption, "access_token": token_instagram}
    conteneur_id = None
    if couverture_url:
        reponse_conteneur = requests.post(f"{URL_GRAPH}/{instagram_id}/media", data={**donnees, "cover_url": couverture_url}, timeout=30)
        if reponse_conteneur.status_code == 200:
            conteneur_id = reponse_conteneur.json()["id"]
    if conteneur_id is None:
        conteneur_id = _creer_conteneur(instagram_id, donnees, "du conteneur Reel Instagram")

    _attendre_conteneur_pret(
        instagram_id, conteneur_id, token_instagram,
        tentatives=TENTATIVES_MAX_CONTENEUR_VIDEO, attente_secondes=ATTENTE_CONTENEUR_VIDEO_SECONDES,
    )
    # Deja attendu ci-dessus (avec le delai video) : publie directement plutot
    # que de passer par _publier_conteneur, qui rattendrait inutilement avec
    # le delai court prevu pour une image.
    return _media_publish(token_instagram, instagram_id, conteneur_id, "du Reel Instagram")


def publier_photo(token_instagram: str, instagram_id: str, image_url: str, caption: str = "") -> dict:
    """
    Publication en trois temps : 1) creer un conteneur media (l'image doit
    etre accessible publiquement via image_url, pas d'upload direct de
    fichier), 2) attendre qu'il soit pret (traitement asynchrone cote
    Instagram), 3) le publier.
    """
    conteneur_id = _creer_conteneur(instagram_id, {"image_url": image_url, "caption": caption, "access_token": token_instagram}, "du conteneur media Instagram")

    _attendre_conteneur_pret(instagram_id, conteneur_id, token_instagram)

    return _media_publish(token_instagram, instagram_id, conteneur_id, "du conteneur Instagram")
