"""
Lecture des publications/commentaires et des statistiques d'un compte
Instagram professionnel via l'API Graph (graph.instagram.com) - voir
instagram_oauth.py.

Metrique verifiee en reel via Graph API Explorer/curl le 10/09/2026 :
"views" fonctionne pour cet endpoint (contrairement a la Page Facebook, ou
c'est "page_views_total" qu'il faut utiliser - deux API distinctes, deux
noms de metriques distincts, ne pas les confondre).
"""

import requests

from .instagram_oauth import URL_GRAPH


def _lister_commentaires(token_instagram: str, media_id: str) -> list[dict]:
    """
    Appel separe plutot qu'un champ imbrique sur /media (comments.limit(20){...})
    - constate en reel le 10/09/2026 : la version imbriquee ne renvoie aucun
    commentaire sur graph.instagram.com, alors que l'appel direct /comments
    fonctionne correctement.
    """
    reponse = requests.get(
        f"{URL_GRAPH}/{media_id}/comments",
        params={"fields": "text,username,timestamp", "access_token": token_instagram},
        timeout=15,
    )
    if reponse.status_code != 200:
        return []
    return reponse.json().get("data", [])


def lister_medias_avec_commentaires(token_instagram: str, instagram_id: str, limite: int = 10) -> list[dict]:
    reponse = requests.get(
        f"{URL_GRAPH}/{instagram_id}/media",
        params={"fields": "caption,timestamp,permalink", "limit": limite, "access_token": token_instagram},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des publications Instagram (code {reponse.status_code}) : {reponse.text}")

    medias = []
    for media in reponse.json().get("data", []):
        commentaires = _lister_commentaires(token_instagram, media.get("id", ""))
        medias.append({
            "id": media.get("id", ""),
            "legende": media.get("caption", ""),
            "cree_le": media.get("timestamp", ""),
            "url": media.get("permalink", ""),
            "commentaires": [
                {"auteur": c.get("username", "Utilisateur Instagram"), "message": c.get("text", ""), "cree_le": c.get("timestamp", "")}
                for c in commentaires
            ],
        })
    return medias


def obtenir_insights(token_instagram: str, instagram_id: str) -> list[dict]:
    reponse = requests.get(
        f"{URL_GRAPH}/{instagram_id}/insights",
        params={"metric": "views", "period": "day", "metric_type": "total_value", "access_token": token_instagram},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des statistiques Instagram (code {reponse.status_code}) : {reponse.text}")

    resultats = []
    for metrique in reponse.json().get("data", []):
        valeur_totale = (metrique.get("total_value") or {}).get("value")
        resultats.append({"nom": metrique.get("name", ""), "titre": metrique.get("title", ""), "derniere_valeur": valeur_totale})
    return resultats
