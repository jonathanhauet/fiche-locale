"""
Lecture de l'engagement (posts + commentaires) et des statistiques d'une
Page Facebook via l'API Graph - voir meta_oauth.py.

Important sur les statistiques (Page Insights) : Meta a deprecie fin 2025 la
plupart des metriques historiques ("impressions", "page fans"...). Verifie
en reel via Graph API Explorer (metric=page_views_total&period=day) le
10/09/2026 - fonctionne. On se limite volontairement a cette seule metrique
confirmee, plutot que de deviner d'autres noms qui pourraient etre invalides
ou eux-memes deja depreciees.
"""

import requests

from .meta_oauth import URL_GRAPH


def lister_posts_avec_commentaires(token_page: str, page_id: str, limite: int = 10) -> list[dict]:
    reponse = requests.get(
        f"{URL_GRAPH}/{page_id}/posts",
        params={
            "fields": f"message,created_time,permalink_url,comments.limit(20){{message,from,created_time}}",
            "limit": limite,
            "access_token": token_page,
        },
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des posts de la Page (code {reponse.status_code}) : {reponse.text}")

    posts = []
    for post in reponse.json().get("data", []):
        commentaires = (post.get("comments") or {}).get("data", [])
        posts.append({
            "id": post.get("id", ""),
            "message": post.get("message", ""),
            "cree_le": post.get("created_time", ""),
            "url": post.get("permalink_url", ""),
            "commentaires": [
                {"auteur": (c.get("from") or {}).get("name", "?"), "message": c.get("message", ""), "cree_le": c.get("created_time", "")}
                for c in commentaires
            ],
        })
    return posts


def obtenir_insights_page(token_page: str, page_id: str) -> list[dict]:
    reponse = requests.get(
        f"{URL_GRAPH}/{page_id}/insights",
        params={"metric": "page_views_total", "period": "day", "access_token": token_page},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des statistiques de la Page (code {reponse.status_code}) : {reponse.text}")

    resultats = []
    for metrique in reponse.json().get("data", []):
        valeurs = metrique.get("values") or []
        derniere_valeur = valeurs[-1]["value"] if valeurs else None
        resultats.append({"nom": metrique.get("name", ""), "titre": metrique.get("title", ""), "derniere_valeur": derniere_valeur})
    return resultats


def lister_medias_instagram_avec_commentaires(token_page: str, instagram_id: str, limite: int = 10) -> list[dict]:
    reponse = requests.get(
        f"{URL_GRAPH}/{instagram_id}/media",
        params={
            "fields": "caption,timestamp,permalink,comments.limit(20){text,username,timestamp}",
            "limit": limite,
            "access_token": token_page,
        },
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des publications Instagram (code {reponse.status_code}) : {reponse.text}")

    medias = []
    for media in reponse.json().get("data", []):
        commentaires = (media.get("comments") or {}).get("data", [])
        medias.append({
            "id": media.get("id", ""),
            "legende": media.get("caption", ""),
            "cree_le": media.get("timestamp", ""),
            "url": media.get("permalink", ""),
            "commentaires": [
                {"auteur": c.get("username", "?"), "message": c.get("text", ""), "cree_le": c.get("timestamp", "")}
                for c in commentaires
            ],
        })
    return medias


def obtenir_insights_instagram(token_page: str, instagram_id: str) -> list[dict]:
    """
    Metrique a verifier en reel avant usage (voir la note en tete de module) -
    Instagram a aussi deprecie "impressions" au profit de "views" pour les
    comptes professionnels, mais ce module ne l'a pas encore teste contre
    l'API reelle.
    """
    reponse = requests.get(
        f"{URL_GRAPH}/{instagram_id}/insights",
        params={"metric": "views", "period": "day", "metric_type": "total_value", "access_token": token_page},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des statistiques Instagram (code {reponse.status_code}) : {reponse.text}")

    resultats = []
    for metrique in reponse.json().get("data", []):
        valeur_totale = (metrique.get("total_value") or {}).get("value")
        resultats.append({"nom": metrique.get("name", ""), "titre": metrique.get("title", ""), "derniere_valeur": valeur_totale})
    return resultats
