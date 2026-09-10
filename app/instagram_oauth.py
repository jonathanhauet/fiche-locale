"""
Connexion Instagram pour la plateforme web : "Business Login for Instagram",
un flux OAuth totalement distinct de la connexion Meta/Facebook (meta_oauth.py)
- identifiants d'app separes, l'utilisateur se connecte directement sur
Instagram (pas via Facebook), et les appels passent par graph.instagram.com
(pas graph.facebook.com). Les permissions Instagram (instagram_business_*)
ne sont pas accessibles via "Facebook Login for Business", meme en
partageant un compte Instagram comme "element" - constate en reel le
10/09/2026 apres plusieurs tentatives infructueuses.

Contrairement au token systeme Meta (n'expire jamais), le token Instagram
est cree "court" puis echange contre un token longue duree (60 jours), a
rafraichir avant expiration - voir planificateur.rafraichir_tokens_instagram.
"""

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from . import models

VERSION_API = "v23.0"
URL_DIALOGUE = "https://www.instagram.com/oauth/authorize"
URL_ECHANGE_COURT = "https://api.instagram.com/oauth/access_token"
URL_GRAPH = "https://graph.instagram.com"

SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_messages"

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

APP_ID = os.getenv("INSTAGRAM_APP_ID")
APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")


def construire_url_autorisation(redirect_uri: str, state: str) -> str:
    parametres = {
        "client_id": APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    requete = "&".join(f"{cle}={valeur}" for cle, valeur in parametres.items())
    return f"{URL_DIALOGUE}?{requete}"


def echanger_code(code: str, redirect_uri: str) -> tuple[str, str]:
    """Echange le code contre un token court, puis contre un token longue duree (60 jours). Renvoie (access_token, identifiant_instagram)."""
    reponse_courte = requests.post(
        URL_ECHANGE_COURT,
        data={
            "client_id": APP_ID, "client_secret": APP_SECRET, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "code": code,
        },
        timeout=30,
    )
    if reponse_courte.status_code != 200:
        raise RuntimeError(f"Echec de l'echange du code Instagram (code {reponse_courte.status_code}) : {reponse_courte.text}")
    donnees_courtes = reponse_courte.json()
    token_court = donnees_courtes["access_token"]
    identifiant_instagram = str(donnees_courtes["user_id"])

    reponse_longue = requests.get(
        f"{URL_GRAPH}/access_token",
        params={"grant_type": "ig_exchange_token", "client_secret": APP_SECRET, "access_token": token_court},
        timeout=30,
    )
    if reponse_longue.status_code != 200:
        raise RuntimeError(f"Echec de l'obtention du token longue duree Instagram (code {reponse_longue.status_code}) : {reponse_longue.text}")
    return reponse_longue.json()["access_token"], identifiant_instagram


def rafraichir_token(access_token: str) -> tuple[str, int]:
    """Renvoie (nouveau_token, duree_secondes) - le token doit avoir au moins 24h pour pouvoir etre rafraichi."""
    reponse = requests.get(
        f"{URL_GRAPH}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": access_token}, timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec du rafraichissement du token Instagram (code {reponse.status_code}) : {reponse.text}")
    donnees = reponse.json()
    return donnees["access_token"], donnees["expires_in"]


def _recuperer_libelle(access_token: str) -> str:
    try:
        reponse = requests.get(f"{URL_GRAPH}/me", params={"fields": "username", "access_token": access_token}, timeout=15)
        if reponse.status_code == 200:
            return reponse.json().get("username") or "(compte Instagram sans nom)"
    except Exception:
        pass
    return "(compte Instagram sans nom)"


def enregistrer_compte(db: Session, access_token: str, identifiant_instagram: str) -> "models.CompteInstagram":
    """Flux additif, comme pour Google/Meta : n'ecrase jamais un compte deja connecte."""
    libelle = _recuperer_libelle(access_token)
    compte = models.CompteInstagram(
        libelle=libelle, identifiant_instagram=identifiant_instagram, access_token=access_token,
        expire_le=datetime.utcnow() + timedelta(days=60),
    )
    db.add(compte)
    db.commit()
    db.refresh(compte)
    return compte


def lister_comptes(db: Session):
    return db.query(models.CompteInstagram).order_by(models.CompteInstagram.cree_le).all()
