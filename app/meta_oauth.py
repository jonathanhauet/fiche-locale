"""
Connexion Meta (Facebook/Instagram) pour la plateforme web : flux "Facebook
Login for Business" avec un config_id (voir la Configuration creee dans le
tableau de bord developpeur Meta), variante "Token d'acces utilisateur
systeme" - contrairement au refresh token Google, ce jeton n'expire jamais
(choix fait a la creation de la configuration) et ne necessite donc aucun
rafraichissement : on le stocke tel quel et on le reutilise directement.
"""

import os
from types import SimpleNamespace

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from . import models

VERSION_API = "v23.0"
URL_DIALOGUE = "https://www.facebook.com/{version}/dialog/oauth"
URL_ECHANGE_TOKEN = "https://graph.facebook.com/{version}/oauth/access_token"
URL_GRAPH = f"https://graph.facebook.com/{VERSION_API}"

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

APP_ID = os.getenv("META_APP_ID")
APP_SECRET = os.getenv("META_APP_SECRET")
CONFIG_ID = os.getenv("META_CONFIG_ID")


def construire_url_autorisation(redirect_uri: str, state: str) -> str:
    """
    response_type=code et override_default_response_type=true sont
    obligatoires pour obtenir un token d'acces utilisateur systeme (SUAT) -
    voir la doc "Facebook Login for Business". config_id remplace scope :
    les permissions demandees sont celles definies dans la Configuration
    elle-meme (tableau de bord developpeur Meta), pas ici.
    """
    parametres = {
        "client_id": APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "config_id": CONFIG_ID,
        "response_type": "code",
        "override_default_response_type": "true",
    }
    requete = "&".join(f"{cle}={valeur}" for cle, valeur in parametres.items())
    return f"{URL_DIALOGUE.format(version=VERSION_API)}?{requete}"


def echanger_code(code: str, redirect_uri: str) -> str:
    """Echange le code recu sur le callback contre le token d'acces utilisateur systeme (n'expire jamais)."""
    reponse = requests.get(
        URL_ECHANGE_TOKEN.format(version=VERSION_API),
        params={"client_id": APP_ID, "client_secret": APP_SECRET, "redirect_uri": redirect_uri, "code": code},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'echange du code Meta (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()["access_token"]


def _recuperer_libelle(access_token: str) -> str:
    try:
        reponse = requests.get(f"{URL_GRAPH}/me", params={"fields": "name", "access_token": access_token}, timeout=15)
        if reponse.status_code == 200:
            return reponse.json().get("name") or "(compte Meta sans nom)"
    except Exception:
        pass
    return "(compte Meta sans nom)"


def enregistrer_compte(db: Session, access_token: str) -> "models.CompteMeta":
    """Ajoute un nouveau compte Meta connecte (flux additif, comme pour Google : n'ecrase jamais un compte existant)."""
    libelle = _recuperer_libelle(access_token)
    compte = models.CompteMeta(libelle=libelle, access_token=access_token)
    db.add(compte)
    db.commit()
    db.refresh(compte)
    return compte


def meta_est_connecte(db: Session) -> bool:
    return db.query(models.CompteMeta).first() is not None


def lister_comptes(db: Session):
    return db.query(models.CompteMeta).order_by(models.CompteMeta.cree_le).all()


def obtenir_identifiants(db: Session, compte_meta_id: int = None):
    """
    Renvoie un objet expose .token (comme google_oauth.obtenir_identifiants)
    pour rester compatible avec le meme style d'appel - mais sans logique de
    rafraichissement, le token systeme n'expirant jamais.
    """
    if compte_meta_id is not None:
        compte = db.get(models.CompteMeta, compte_meta_id)
    else:
        compte = db.query(models.CompteMeta).first()

    if not compte or not compte.access_token:
        return None
    return SimpleNamespace(token=compte.access_token)


def lister_pages(access_token: str) -> list[dict]:
    """
    Pages Facebook accessibles par ce token systeme, avec leur propre token
    de Page (necessaire pour publier/lire sur la Page elle-meme - distinct du
    token systeme utilise pour cet appel). Inclut le compte Instagram lie
    s'il existe, pour eviter un second appel par Page.
    """
    reponse = requests.get(
        f"{URL_GRAPH}/me/accounts",
        params={"fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": access_token},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des Pages Meta (code {reponse.status_code}) : {reponse.text}")

    pages = []
    for page in reponse.json().get("data", []):
        instagram = page.get("instagram_business_account") or {}
        pages.append({
            "id": page.get("id", ""),
            "nom": page.get("name", ""),
            "token_page": page.get("access_token", ""),
            "instagram_id": instagram.get("id", ""),
            "instagram_nom": instagram.get("username", ""),
        })
    return pages
