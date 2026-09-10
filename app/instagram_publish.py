"""
Publication sur un compte Instagram professionnel via l'API Graph
(graph.instagram.com, pas graph.facebook.com) - voir instagram_oauth.py pour
la connexion. Le token utilise est celui du compte Instagram lui-meme
(Client.token_instagram), distinct du token de Page Facebook.
"""

import requests

from .instagram_oauth import URL_GRAPH


def publier_photo(token_instagram: str, instagram_id: str, image_url: str, caption: str = "") -> dict:
    """
    Publication en deux temps : 1) creer un conteneur media (l'image doit
    etre accessible publiquement via image_url, pas d'upload direct de
    fichier), 2) le publier une fois pret.
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
