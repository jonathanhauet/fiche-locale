"""
Envoi et reception de messages via l'API WhatsApp Cloud (Meta), pour le
"mode rapide vocal" par WhatsApp : questions envoyees chaque mercredi,
reponse dictee (+ photo optionnelle) recuperee via webhook.
"""

import os

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

VERSION_API = "v25.0"
URL_GRAPH = f"https://graph.facebook.com/{VERSION_API}"

TOKEN_ACCES = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
NOM_TEMPLATE_QUESTIONS_HEBDO = os.getenv("WHATSAPP_TEMPLATE_QUESTIONS", "questions_hebdo")

CLE_OPENAI = os.getenv("OPENAI_API_KEY")


def identifiants_configures() -> bool:
    return bool(TOKEN_ACCES and PHONE_NUMBER_ID and VERIFY_TOKEN)


def _entetes() -> dict:
    return {"Authorization": f"Bearer {TOKEN_ACCES}", "Content-Type": "application/json"}


def envoyer_message_texte(numero_destinataire: str, texte: str) -> None:
    """
    Message texte libre - ne fonctionne que dans les 24h suivant le dernier
    message recu de ce destinataire (fenetre de session WhatsApp) : utilise
    pour les reponses/confirmations dans une conversation deja ouverte, pas
    pour relancer quelqu'un qui n'a pas ecrit depuis plus de 24h (voir
    envoyer_message_template pour ce cas).
    """
    reponse = requests.post(
        f"{URL_GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers=_entetes(),
        json={
            "messaging_product": "whatsapp",
            "to": numero_destinataire,
            "type": "text",
            "text": {"body": texte},
        },
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'envoi WhatsApp (code {reponse.status_code}) : {reponse.text}")


def envoyer_message_template(numero_destinataire: str, nom_template: str, langue: str, parametres_corps: list[str] = None) -> None:
    """
    Message a partir d'un modele approuve par Meta - seul type de message
    autorise pour initier une conversation (le destinataire n'a pas ecrit
    dans les 24h precedentes), ex : la relance hebdomadaire du mercredi.
    """
    composants = []
    if parametres_corps:
        composants.append({
            "type": "body",
            "parameters": [{"type": "text", "text": valeur} for valeur in parametres_corps],
        })

    reponse = requests.post(
        f"{URL_GRAPH}/{PHONE_NUMBER_ID}/messages",
        headers=_entetes(),
        json={
            "messaging_product": "whatsapp",
            "to": numero_destinataire,
            "type": "template",
            "template": {"name": nom_template, "language": {"code": langue}, "components": composants},
        },
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'envoi du modele WhatsApp (code {reponse.status_code}) : {reponse.text}")


def telecharger_media(media_id: str) -> tuple[bytes, str]:
    """Renvoie (octets, type_mime) d'un media recu (vocal, photo...) : deux appels, l'URL du media expire vite et n'est pas previsible a l'avance."""
    reponse_url = requests.get(f"{URL_GRAPH}/{media_id}", headers=_entetes(), timeout=30)
    if reponse_url.status_code != 200:
        raise RuntimeError(f"Echec de la recuperation du media WhatsApp (code {reponse_url.status_code}) : {reponse_url.text}")
    infos = reponse_url.json()

    reponse_fichier = requests.get(infos["url"], headers={"Authorization": f"Bearer {TOKEN_ACCES}"}, timeout=60)
    if reponse_fichier.status_code != 200:
        raise RuntimeError(f"Echec du telechargement du media WhatsApp (code {reponse_fichier.status_code})")

    return reponse_fichier.content, infos.get("mime_type", "application/octet-stream")


def marquer_lu(message_id: str) -> None:
    """Purement cosmetique (coche bleue chez l'expediteur) : echec sans consequence, jamais bloquant."""
    try:
        requests.post(
            f"{URL_GRAPH}/{PHONE_NUMBER_ID}/messages",
            headers=_entetes(),
            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
            timeout=15,
        )
    except Exception:
        pass


def transcrire_audio(octets: bytes, mime_type: str) -> str:
    """
    Transcrit un vocal en texte via l'API Whisper d'OpenAI (cle deja
    configuree sur la plateforme pour une autre fonctionnalite, voir
    ia_visibilite.py) - contrairement a la dictee dans le navigateur (API
    du navigateur, gratuite), un vocal recu par WhatsApp est un fichier
    audio qui doit etre transcrit cote serveur, seule vraie depense de
    cette integration (quelques centimes par minute).
    """
    if not CLE_OPENAI:
        raise RuntimeError("OPENAI_API_KEY manquant dans plateforme_web/.env.")

    extension = "ogg" if "ogg" in mime_type else "mp3"
    reponse = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {CLE_OPENAI}"},
        files={"file": (f"audio.{extension}", octets, mime_type)},
        data={"model": "whisper-1", "language": "fr"},
        timeout=60,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la transcription (code {reponse.status_code}) : {reponse.text}")

    return reponse.json()["text"]
