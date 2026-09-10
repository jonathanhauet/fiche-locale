"""
Publication sur une Page Facebook via l'API Graph - voir meta_oauth.py pour
la connexion et la recuperation des tokens de Page. Le token utilise ici est
celui de la Page (Client.token_page_meta), distinct du token systeme du
compte Meta connecte. La publication Instagram est un flux separe, voir
instagram_publish.py.
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
