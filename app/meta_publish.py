"""
Publication sur une Page Facebook (et son compte Instagram lie) via l'API
Graph - voir meta_oauth.py pour la connexion et la recuperation des tokens
de Page. Le token utilise ici est celui de la Page (Client.token_page_meta),
distinct du token systeme du compte Meta connecte.
"""

import requests

from .meta_oauth import URL_GRAPH


def publier_post_page(token_page: str, page_id: str, message: str, image_url: str = None) -> dict:
    """
    Publie sur le fil de la Page - avec image (POST /photos, la legende tient
    lieu de texte du post) ou en texte seul (POST /feed).
    """
    if image_url:
        url = f"{URL_GRAPH}/{page_id}/photos"
        donnees = {"url": image_url, "caption": message, "access_token": token_page}
    else:
        url = f"{URL_GRAPH}/{page_id}/feed"
        donnees = {"message": message, "access_token": token_page}

    reponse = requests.post(url, data=donnees, timeout=30)
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la publication sur la Page (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def publier_photo_instagram(token_page: str, instagram_id: str, image_url: str, caption: str = "") -> dict:
    """
    Publication Instagram : en deux temps (contrairement a une Page Facebook)
    - 1) creer un conteneur media (l'image doit etre accessible publiquement
    via image_url, pas d'upload direct de fichier), 2) le publier une fois
    pret. Le token utilise est le meme token de Page (une Page Facebook et
    son compte Instagram professionnel lie partagent le meme token d'acces).
    """
    reponse_conteneur = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media",
        data={"image_url": image_url, "caption": caption, "access_token": token_page},
        timeout=30,
    )
    if reponse_conteneur.status_code != 200:
        raise RuntimeError(
            f"Echec de la creation du conteneur media Instagram (code {reponse_conteneur.status_code}) : {reponse_conteneur.text}"
        )
    conteneur_id = reponse_conteneur.json()["id"]

    reponse_publication = requests.post(
        f"{URL_GRAPH}/{instagram_id}/media_publish",
        data={"creation_id": conteneur_id, "access_token": token_page},
        timeout=30,
    )
    if reponse_publication.status_code != 200:
        raise RuntimeError(
            f"Echec de la publication du conteneur Instagram (code {reponse_publication.status_code}) : {reponse_publication.text}"
        )
    return reponse_publication.json()
