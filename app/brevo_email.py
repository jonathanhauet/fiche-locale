"""Envoi d'emails transactionnels via l'API Brevo (https://api.brevo.com)."""

import base64
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


def envoyer_email(
    destinataire_email: str, destinataire_nom: str, sujet: str, contenu_html: str,
    pieces_jointes: list[tuple[str, bytes]] = None, nom_expediteur: str = None, reponse_a: str = None,
    email_expediteur: str = None, cle_api: str = None,
) -> None:
    """pieces_jointes : [(nom_fichier, octets), ...], optionnel (ex. PDF d'audit joint).
    nom_expediteur : remplace le nom affiche ; email_expediteur : remplace l'adresse d'envoi (elle doit etre validee
    dans le compte Brevo utilise) ; cle_api : envoie avec un autre compte Brevo que celui de la plateforme ;
    reponse_a : adresse qui recoit les reponses du destinataire."""
    cle = cle_api or BREVO_API_KEY
    expediteur = email_expediteur or BREVO_EMAIL_EXPEDITEUR
    if not (cle and expediteur):
        raise RuntimeError("BREVO_API_KEY / BREVO_EMAIL_EXPEDITEUR manquants dans plateforme_web/.env.")

    corps = {
        "sender": {"name": nom_expediteur or BREVO_NOM_EXPEDITEUR, "email": expediteur},
        "to": [{"email": destinataire_email, "name": destinataire_nom}],
        "subject": sujet,
        "htmlContent": contenu_html,
    }
    if reponse_a:
        corps["replyTo"] = {"email": reponse_a, "name": nom_expediteur or BREVO_NOM_EXPEDITEUR}
    if pieces_jointes:
        corps["attachment"] = [
            {"name": nom_fichier, "content": base64.b64encode(octets).decode()}
            for nom_fichier, octets in pieces_jointes
        ]

    reponse = requests.post(
        URL_ENVOI,
        headers={"api-key": cle, "Content-Type": "application/json", "Accept": "application/json"},
        json=corps,
        timeout=30,
    )
    if reponse.status_code >= 300:
        raise RuntimeError(f"Echec de l'envoi Brevo (code {reponse.status_code}) : {reponse.text}")
