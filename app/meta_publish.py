"""
Publication sur une Page Facebook via l'API Graph - voir meta_oauth.py pour
la connexion et la recuperation des tokens de Page. Le token utilise ici est
celui de la Page (Client.token_page_meta), distinct du token systeme du
compte Meta connecte. La publication Instagram est un flux separe, voir
instagram_publish.py.
"""

import json

import requests

from .meta_oauth import URL_GRAPH


def urls_depuis_champ(champ: str) -> list[str]:
    """
    Les posts programmes (PostMetaProgramme, PostInstagramProgramme) gardent
    leurs images dans une seule colonne texte, une URL par ligne : evite une
    migration de schema pour passer d'une image a plusieurs.
    """
    return [url.strip() for url in (champ or "").split("\n") if url.strip()]


def champ_depuis_urls(urls: list[str]):
    return "\n".join(urls) if urls else None


def publier_post_page(token_page: str, page_id: str, message: str, image_url=None) -> dict:
    """
    Publie sur le fil de la Page. image_url : une URL, une liste d'URL ou None.
    Une image : POST /photos (la legende tient lieu de texte du post). Plusieurs
    : chaque photo est d'abord envoyee "non publiee" puis rattachee a un seul
    post via attached_media. Aucune image : texte seul (POST /feed).
    """
    urls = [image_url] if isinstance(image_url, str) else list(image_url or [])
    urls = [u for u in urls if u]

    if len(urls) == 1:
        url = f"{URL_GRAPH}/{page_id}/photos"
        donnees = {"url": urls[0], "caption": message, "access_token": token_page}
    else:
        url = f"{URL_GRAPH}/{page_id}/feed"
        donnees = {"message": message, "access_token": token_page}
        for indice, url_image in enumerate(urls):
            envoi = requests.post(
                f"{URL_GRAPH}/{page_id}/photos",
                data={"url": url_image, "published": "false", "access_token": token_page},
                timeout=60,
            )
            if envoi.status_code != 200:
                raise RuntimeError(
                    f"Echec de l'envoi de la photo {indice + 1} sur la Page ({url_image}) (code {envoi.status_code}) : {envoi.text}"
                )
            donnees[f"attached_media[{indice}]"] = json.dumps({"media_fbid": envoi.json()["id"]})

    reponse = requests.post(url, data=donnees, timeout=30)
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la publication sur la Page (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def publier_video_page(token_page: str, page_id: str, video_url: str, message: str) -> dict:
    """
    Publie une video sur le fil de la Page (POST /videos) : Facebook la
    televerse lui-meme depuis video_url, le traitement (encodage, generation
    de la vignette) se termine de facon asynchrone cote Meta apres la reponse
    - la video devient visible une fois pret, sans que la plateforme ait a
    interroger un statut (contrairement au conteneur Instagram, voir
    instagram_publish.publier_reel).
    """
    reponse = requests.post(
        f"{URL_GRAPH}/{page_id}/videos",
        data={"file_url": video_url, "description": message, "access_token": token_page},
        timeout=60,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la publication video sur la Page (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()
