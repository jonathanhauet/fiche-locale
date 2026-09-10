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
