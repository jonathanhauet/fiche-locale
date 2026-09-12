"""
Publication sur un profil LinkedIn personnel via l'API Posts (REST, scope
w_member_social - voir linkedin_oauth.py). Texte uniquement pour l'instant :
l'ajout d'image necessiterait un flux d'upload en deux etapes (Images API),
pas construit dans cette premiere version.
"""

import requests

URL_POSTS = "https://api.linkedin.com/rest/posts"
VERSION_API = "202409"


def publier_texte(access_token: str, identifiant_membre: str, texte: str) -> str:
    """Publie un post texte sur le profil du membre. Renvoie l'URN du post cree."""
    corps = {
        "author": f"urn:li:person:{identifiant_membre}",
        "commentary": texte,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    reponse = requests.post(
        URL_POSTS,
        json=corps,
        headers={
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": VERSION_API,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de la publication LinkedIn (code {reponse.status_code}) : {reponse.text}")

    return reponse.headers.get("x-restli-id", "")
