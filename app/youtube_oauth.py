"""
Connexion YouTube (Data API v3) pour la plateforme web : OAuth Google standard, mais avec son PROPRE projet Google
Cloud et son propre identifiant (YOUTUBE_CLIENT_ID/SECRET), separe du projet "Extraction-GBP" utilise par
google_oauth.py pour la fiche Business Profile. Ce n'est pas juste une precaution : Google bloque totalement (pas
seulement un avertissement contournable) tout compte qui tente d'autoriser un scope YouTube tant que l'appli n'est
pas verifiee - impossible donc de tester ou d'enregistrer la video de demonstration exigee par Google si ce scope
est ajoute au projet Extraction-GBP, deja en mode "Production" (passer ce projet en mode "Test" couperait le risque
de bloquer l'acces Business Profile en production pour les clients non listes comme testeurs). Le projet YouTube
dedie reste en mode "Test" (avec le compte de l'agence comme testeur) : l'ecran de blocage devient alors un simple
avertissement contournable, ce qui permet de tester et, plus tard, de faire la demande de validation aupres de
Google sans jamais toucher au projet Business Profile.

Prealables cote Google Cloud (dans ce projet YouTube dedie) :
- Activer "YouTube Data API v3".
- Ajouter l'URL de callback (https://.../youtube/callback) aux "URI de redirection autorisees" de l'identifiant OAuth.
- Ajouter le(s) compte(s) Google testeurs (Audience > Utilisateurs test) tant que l'appli n'est pas verifiee.
Attention au quota par defaut de l'API (10000 unites/jour, un televersement de video en coute 1600 - environ 6
televersements/jour possibles sans demande d'augmentation de quota auprès de Google).
"""

import os

import requests
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from . import models

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")


def _configuration_client():
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def construire_flow(redirect_uri: str, code_verifier: str = None) -> Flow:
    if redirect_uri.startswith("http://localhost") or redirect_uri.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    return Flow.from_client_config(
        _configuration_client(), scopes=SCOPES, redirect_uri=redirect_uri, code_verifier=code_verifier
    )


def _recuperer_email(access_token: str) -> str:
    try:
        reponse = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
        )
        reponse.raise_for_status()
        return reponse.json().get("email", "")
    except requests.RequestException:
        return ""


def _recuperer_chaine(access_token: str) -> dict:
    """{"id", "titre"} de la chaine YouTube du compte authentifie (mine=true). Leve si aucune chaine n'existe."""
    reponse = requests.get(
        "https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture de la chaine YouTube (code {reponse.status_code}) : {reponse.text[:300]}")
    items = reponse.json().get("items", [])
    if not items:
        raise RuntimeError(
            "Aucune chaîne YouTube trouvée sur ce compte Google. Créez une chaîne sur ce compte avant de le connecter."
        )
    return {"id": items[0]["id"], "titre": items[0].get("snippet", {}).get("title", "")}


def enregistrer_compte(db: Session, refresh_token: str) -> "models.CompteYouTube":
    """Flux additif (comme les autres connexions Google/Meta/LinkedIn) : n'ecrase jamais un compte deja connecte."""
    identifiants = Credentials(
        token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=SCOPES,
    )
    identifiants.refresh(Request())
    chaine = _recuperer_chaine(identifiants.token)
    email = _recuperer_email(identifiants.token)

    compte = models.CompteYouTube(
        libelle=email or chaine["titre"] or "(compte sans adresse e-mail)",
        channel_id=chaine["id"], channel_titre=chaine["titre"], refresh_token=refresh_token,
    )
    db.add(compte)
    db.commit()
    db.refresh(compte)
    return compte


def lister_comptes(db: Session):
    return db.query(models.CompteYouTube).order_by(models.CompteYouTube.cree_le).all()


def obtenir_identifiants(db: Session, compte_id: int):
    """Renvoie des identifiants Google valides pour ce compte YouTube, ou None (jamais connecte, ou acces revoque)."""
    compte = db.get(models.CompteYouTube, compte_id) if compte_id is not None else None
    if not compte or not compte.refresh_token:
        return None
    identifiants = Credentials(
        token=None, refresh_token=compte.refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=SCOPES,
    )
    try:
        identifiants.refresh(Request())
    except RefreshError:
        return None
    return identifiants
