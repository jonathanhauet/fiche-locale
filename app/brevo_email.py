"""Envoi d'emails transactionnels via l'API Brevo (https://api.brevo.com)."""

import os

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
# Chaque module qui a besoin de .env le charge lui-meme : on ne peut pas
# compter sur l'ordre des imports pour garantir que main.py l'a deja fait.
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_EMAIL_EXPEDITEUR = os.getenv("BREVO_EMAIL_EXPEDITEUR")
BREVO_NOM_EXPEDITEUR = os.getenv("BREVO_NOM_EXPEDITEUR", "Fiche Locale")

URL_ENVOI = "https://api.brevo.com/v3/smtp/email"


def identifiants_configures() -> bool:
    return bool(BREVO_API_KEY and BREVO_EMAIL_EXPEDITEUR)


def envoyer_email(destinataire_email: str, destinataire_nom: str, sujet: str, contenu_html: str) -> None:
    if not identifiants_configures():
        raise RuntimeError("BREVO_API_KEY / BREVO_EMAIL_EXPEDITEUR manquants dans plateforme_web/.env.")

    reponse = requests.post(
        URL_ENVOI,
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"name": BREVO_NOM_EXPEDITEUR, "email": BREVO_EMAIL_EXPEDITEUR},
            "to": [{"email": destinataire_email, "name": destinataire_nom}],
            "subject": sujet,
            "htmlContent": contenu_html,
        },
        timeout=15,
    )
    if reponse.status_code >= 300:
        raise RuntimeError(f"Echec de l'envoi Brevo (code {reponse.status_code}) : {reponse.text}")
