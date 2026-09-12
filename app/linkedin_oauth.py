"""
Connexion LinkedIn pour la plateforme web : OAuth 2.0 standard, produits
"Sign In with LinkedIn using OpenID Connect" (identification) + "Share on
LinkedIn" (publication au nom du membre authentifie, scope w_member_social).

Contrairement au token systeme Meta (n'expire jamais), le token LinkedIn
expire (~60 jours, voir expires_in renvoye par LinkedIn) et LinkedIn ne
fournit pas de refresh token sans le produit "Programmatic Refresh Tokens"
(non demande ici) : une fois expire, il faut reconnecter le compte
manuellement plutot que de le rafraichir automatiquement.

Gere uniquement des profils personnels pour l'instant. La gestion des pages
entreprise des clients necessite le produit "Community Management API",
demande separement et en attente de validation par LinkedIn au moment de
l'ecriture de ce module (voir app/assets - demande soumise le 11/09/2026).
"""

import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from . import models

URL_DIALOGUE = "https://www.linkedin.com/oauth/v2/authorization"
URL_ECHANGE = "https://www.linkedin.com/oauth/v2/accessToken"
URL_USERINFO = "https://api.linkedin.com/v2/userinfo"

SCOPES = "openid profile w_member_social"

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

APP_ID = os.getenv("LINKEDIN_APP_ID")
APP_SECRET = os.getenv("LINKEDIN_APP_SECRET")


def identifiants_configures() -> bool:
    return bool(APP_ID and APP_SECRET)


def construire_url_autorisation(redirect_uri: str, state: str) -> str:
    parametres = {
        "response_type": "code",
        "client_id": APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": SCOPES,
    }
    return f"{URL_DIALOGUE}?{urlencode(parametres)}"


def echanger_code(code: str, redirect_uri: str) -> dict:
    """Renvoie {"access_token", "expire_le" (datetime)}."""
    reponse = requests.post(
        URL_ECHANGE,
        data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": APP_ID, "client_secret": APP_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'echange du code LinkedIn (code {reponse.status_code}) : {reponse.text}")

    donnees = reponse.json()
    return {
        "access_token": donnees["access_token"],
        "expire_le": datetime.utcnow() + timedelta(seconds=donnees.get("expires_in", 60 * 24 * 3600)),
    }


def obtenir_userinfo(access_token: str) -> dict:
    """Renvoie {"sub", "name", ...} - "sub" est l'identifiant du membre, utilise pour publier (urn:li:person:{sub})."""
    reponse = requests.get(URL_USERINFO, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la recuperation du profil LinkedIn (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def enregistrer_compte(db: Session, access_token: str, expire_le, userinfo: dict) -> "models.CompteLinkedIn":
    """Flux additif, comme pour Google/Meta/Instagram : n'ecrase jamais un compte deja connecte."""
    compte = models.CompteLinkedIn(
        libelle=userinfo.get("name") or "(profil LinkedIn sans nom)",
        identifiant_membre=userinfo["sub"],
        access_token=access_token,
        expire_le=expire_le,
    )
    db.add(compte)
    db.commit()
    db.refresh(compte)
    return compte


def lister_comptes(db: Session):
    return db.query(models.CompteLinkedIn).order_by(models.CompteLinkedIn.cree_le).all()
