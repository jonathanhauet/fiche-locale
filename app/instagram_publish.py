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


def _attendre_conteneur_pret(instagram_id: str, conteneur_id: str, token_instagram: str) -> None:
    """
    Instagram traite l'image de facon asynchrone apres la creation du
    conteneur - la publier trop tot echoue avec "The media is not ready for
    publishing" (constate en reel). On interroge son status_code jusqu'a
    FINISHED (ou une erreur/un delai trop long).
    """
    for _ in range(TENTATIVES_MAX_CONTENEUR):
        reponse = requests.get(
            f"{URL_GRAPH}/{conteneur_id}", params={"fields": "status_code", "access_token": token_instagram}, timeout=15,
        )
        if reponse.status_code == 200:
            statut = reponse.json().get("status_code")
            if statut == "FINISHED":
                return
            if statut == "ERROR":
                raise RuntimeError("Echec du traitement du conteneur media Instagram (statut ERROR).")
        time.sleep(ATTENTE_CONTENEUR_SECONDES)
    raise RuntimeError("Le conteneur media Instagram n'est pas devenu pret a temps.")


def _publier_conteneur(token_instagram: str, instagram_id: str, conteneur_id: str) -> dict:
    _attendre_conteneur_pret(instagram_id, conteneur_id, token_instagram)
    reponse = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media_publish",
        data={"creation_id": conteneur_id, "access_token": token_instagram},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la publication du conteneur Instagram (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def publier_carrousel(token_instagram: str, instagram_id: str, image_urls: list[str], caption: str = "") -> dict:
    """
    Carrousel (2 a 10 images) : 1) un conteneur "element de carrousel" par
    image, chacun attendu jusqu'a FINISHED, 2) un conteneur CAROUSEL qui les
    reference et porte la legende, 3) publication de celui-ci. Toutes les
    images sont recadrees au format de la premiere par Instagram.
    """
    enfants = []
    for indice, url_image in enumerate(image_urls, start=1):
        reponse = requests.post(
            f"{URL_GRAPH}/{instagram_id}/media",
            data={"image_url": url_image, "is_carousel_item": "true", "access_token": token_instagram},
            timeout=30,
        )
        if reponse.status_code != 200:
            raise RuntimeError(
                f"Echec de la creation de l'image {indice} du carrousel Instagram (code {reponse.status_code}) : {reponse.text}"
            )
        enfants.append(reponse.json()["id"])
    for enfant in enfants:
        _attendre_conteneur_pret(instagram_id, enfant, token_instagram)

    reponse_carrousel = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media",
        data={"media_type": "CAROUSEL", "children": ",".join(enfants), "caption": caption, "access_token": token_instagram},
        timeout=30,
    )
    if reponse_carrousel.status_code != 200:
        raise RuntimeError(
            f"Echec de la creation du carrousel Instagram (code {reponse_carrousel.status_code}) : {reponse_carrousel.text}"
        )
    return _publier_conteneur(token_instagram, instagram_id, reponse_carrousel.json()["id"])


def publier_medias(token_instagram: str, instagram_id: str, image_urls: list[str], caption: str = "") -> dict:
    """Une image : publication classique. Plusieurs : carrousel."""
    if len(image_urls) > 1:
        return publier_carrousel(token_instagram, instagram_id, image_urls, caption)
    return publier_photo(token_instagram, instagram_id, image_urls[0], caption)


def publier_photo(token_instagram: str, instagram_id: str, image_url: str, caption: str = "") -> dict:
    """
    Publication en trois temps : 1) creer un conteneur media (l'image doit
    etre accessible publiquement via image_url, pas d'upload direct de
    fichier), 2) attendre qu'il soit pret (traitement asynchrone cote
    Instagram), 3) le publier.
    """
    reponse_conteneur = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media",
        data={"image_url": image_url, "caption": caption, "access_token": token_instagram},
        timeout=30,
    )
    if reponse_conteneur.status_code != 200:
        raise RuntimeError(
            f"Echec de la creation du conteneur media Instagram (code {reponse_conteneur.status_code}) : {reponse_conteneur.text}"
        )
    conteneur_id = reponse_conteneur.json()["id"]

    _attendre_conteneur_pret(instagram_id, conteneur_id, token_instagram)

    reponse_publication = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media_publish",
        data={"creation_id": conteneur_id, "access_token": token_instagram},
        timeout=30,
    )
    if reponse_publication.status_code != 200:
        raise RuntimeError(
            f"Echec de la publication du conteneur Instagram (code {reponse_publication.status_code}) : {reponse_publication.text}"
        )
    return reponse_publication.json()
