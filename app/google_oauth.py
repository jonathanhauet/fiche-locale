"""
Connexion Google pour la plateforme web : flux OAuth via redirection
navigateur (au lieu du serveur local utilise par les scripts en ligne de
commande), et gestion du refresh token stocke en base de donnees.
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
    "https://www.googleapis.com/auth/business.manage",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Libelle place sur le compte cree automatiquement lors de la migration depuis
# l'ancien modele mono-compte (voir main.py, _migrer_vers_multi_comptes). Son
# refresh token a ete emis avant l'ajout des scopes openid/userinfo.email et ne
# peut donc plus etre rafraichi : on le "guerit" en le remplacant en place des
# la premiere reconnexion, pour ne pas casser le rattachement des clients
# existants avec une ligne fantome supplementaire.
LIBELLE_COMPTE_MIGRE = "(compte principal)"

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
# Chaque module qui a besoin de .env le charge lui-meme : on ne peut pas
# compter sur l'ordre des imports pour garantir que main.py l'a deja fait.
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")


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
    # En local (http://localhost), Google/oauthlib refuse par defaut un
    # echange de jeton sur une connexion non chiffree. On desactive cette
    # verification uniquement pour localhost (jamais en production, ou
    # l'adresse sera en https).
    if redirect_uri.startswith("http://localhost") or redirect_uri.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    # code_verifier (PKCE) : genere lors de l'etape /google/connecter, doit
    # etre reutilise tel quel a l'etape /google/callback (deux requetes HTTP
    # distinctes = deux objets Flow distincts, d'ou le besoin de le faire
    # transiter par la session).
    return Flow.from_client_config(
        _configuration_client(), scopes=SCOPES, redirect_uri=redirect_uri, code_verifier=code_verifier
    )


def _recuperer_email(access_token: str) -> str:
    """Recupere l'adresse e-mail du compte Google connecte, pour servir de libelle."""
    try:
        reponse = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        reponse.raise_for_status()
        return reponse.json().get("email", "")
    except requests.RequestException:
        return ""


def enregistrer_refresh_token(db: Session, refresh_token: str) -> "models.CompteGoogle":
    """
    Ajoute un nouveau compte Google connecte (flux additif : n'ecrase jamais
    un compte deja connecte, meme si c'est le meme compte Google reconnecte).
    """
    identifiants = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    identifiants.refresh(Request())
    libelle = _recuperer_email(identifiants.token) or "(compte sans adresse e-mail)"

    placeholder_migre = db.query(models.CompteGoogle).filter_by(libelle=LIBELLE_COMPTE_MIGRE).first()
    if placeholder_migre and db.query(models.CompteGoogle).count() == 1:
        placeholder_migre.libelle = libelle
        placeholder_migre.refresh_token = refresh_token
        db.commit()
        db.refresh(placeholder_migre)
        return placeholder_migre

    compte = models.CompteGoogle(libelle=libelle, refresh_token=refresh_token)
    db.add(compte)
    db.commit()
    db.refresh(compte)
    return compte


def google_est_connecte(db: Session) -> bool:
    return db.query(models.CompteGoogle).first() is not None


def lister_comptes(db: Session):
    return db.query(models.CompteGoogle).order_by(models.CompteGoogle.cree_le).all()


def obtenir_identifiants(db: Session, compte_google_id: int = None):
    """Renvoie des identifiants Google valides pour le compte donne, ou None."""
    if compte_google_id is not None:
        compte = db.get(models.CompteGoogle, compte_google_id)
    else:
        compte = db.query(models.CompteGoogle).first()

    if not compte or not compte.refresh_token:
        return None

    identifiants = Credentials(
        token=None,
        refresh_token=compte.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    try:
        identifiants.refresh(Request())
    except RefreshError:
        # Jeton revoque ou emis avec des scopes desormais incompatibles (ex :
        # compte migre depuis l'ancien modele mono-compte, pas encore
        # reconnecte) : on traite comme "non connecte" plutot que de planter.
        return None
    return identifiants
