"""
Notification push sur le telephone de Jonathan via ntfy.sh (gratuit, pas de
compte requis : https://ntfy.sh) - utilise pour prevenir en temps reel qu'une
publication est prete a etre relue/validee pour un client, sans dependre de
WhatsApp (canal desormais partage entre plusieurs clients, voir
planificateur.envoyer_questions_whatsapp_si_prevu).
"""

import os

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

TOPIC_NTFY = os.getenv("NTFY_TOPIC")


def notifier(titre: str, message: str, url: str = None) -> None:
    """
    Envoie une notification push. Echoue silencieusement (pas de
    NTFY_TOPIC configure, ntfy.sh injoignable...) : une notif ratee ne doit
    jamais faire echouer le traitement principal (generation de post,
    webhook WhatsApp...).
    """
    if not TOPIC_NTFY:
        return
    entetes = {"Title": titre}
    if url:
        entetes["Click"] = url
    try:
        requests.post(f"https://ntfy.sh/{TOPIC_NTFY}", data=message.encode("utf-8"), headers=entetes, timeout=10)
    except Exception:
        pass
