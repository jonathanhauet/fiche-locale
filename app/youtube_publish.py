"""
Publication de videos sur YouTube (Data API v3, televersement resumable) : pensee pour des Shorts (video
verticale ou carree, 60 secondes maximum), mais rien n'empeche d'y televerser une video plus longue.

YouTube determine automatiquement qu'une video est un Short a partir de son format (vertical/carre) et de sa
duree (<= 60s, ou moins selon les evolutions de YouTube) - il n'y a pas de parametre API explicite "c'est un
Short". Ajouter "#Shorts" au titre ou a la description est une pratique courante en plus de ce critere.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

URL_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
FUSEAU_LOCAL = ZoneInfo("Europe/Brussels")


def publier_video(
    access_token: str, octets_video: bytes, titre: str, description: str = "", publier_le: datetime = None,
    mots_cles: list = None,
) -> dict:
    """
    Televerse une video sur la chaine du compte authentifie. Sans publier_le : publique immediatement. Avec
    publier_le (heure locale de Bruxelles, naive, future) : video mise en "privee" avec une date de publication
    programmee - YouTube la rend publique lui-meme a cette heure (meme principe que le statut "future" de
    wordpress_publish.creer_article), pas besoin de planificateur local. Renvoie {"id", "lien"}.
    """
    statut = {"selfDeclaredMadeForKids": False}
    if publier_le:
        statut["privacyStatus"] = "private"
        statut["publishAt"] = publier_le.replace(tzinfo=FUSEAU_LOCAL).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        statut["privacyStatus"] = "public"

    corps = {
        "snippet": {
            "title": (titre or "").strip()[:100] or "Vidéo",
            "description": (description or "").strip()[:5000],
            "tags": [m.strip() for m in (mots_cles or []) if m.strip()][:15],
        },
        "status": statut,
    }
    entetes = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(len(octets_video)),
    }
    session = requests.post(
        f"{URL_UPLOAD}?uploadType=resumable&part=snippet,status", json=corps, headers=entetes, timeout=30,
    )
    if session.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'initialisation de l'envoi YouTube (code {session.status_code}) : {session.text[:300]}")
    url_session = session.headers.get("Location")
    if not url_session:
        raise RuntimeError("YouTube n'a renvoyé aucune URL d'envoi.")

    envoi = requests.put(url_session, data=octets_video, headers={"Content-Type": "video/mp4"}, timeout=180)
    if envoi.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'envoi de la vidéo à YouTube (code {envoi.status_code}) : {envoi.text[:300]}")

    donnees = envoi.json()
    video_id = donnees.get("id", "")
    return {"id": video_id, "lien": f"https://youtube.com/shorts/{video_id}" if video_id else ""}
