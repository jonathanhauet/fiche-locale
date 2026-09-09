"""
Lecture de l'engagement (posts + commentaires) et des statistiques d'une
Page Facebook via l'API Graph - voir meta_oauth.py.

Important sur les statistiques (Page Insights) : Meta a deprecie fin 2025 la
plupart des metriques historiques ("impressions", "page fans"...), avec un
remplacement encore partiel/mouvant au moment ou ce module a ete ecrit -
voir https://developers.facebook.com/blog/post/2025/08/15/page-insights-api-updates/.
On se limite volontairement a "views" (remplacant confirme d'"impressions"),
plutot que de deviner d'autres noms de metriques qui pourraient etre
invalides ou eux-memes deja depreciees.
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
        params={"metric": "views", "period": "day", "access_token": token_page},
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
