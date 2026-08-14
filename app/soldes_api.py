"""
Solde restant sur les plateformes payantes complementaires, pour affichage
dans la barre laterale (voir base.html). Seul DataForSEO expose une API de
solde utilisable simplement avec une cle API standard (endpoint gratuit,
identifiants deja configures pour rank_tracking.py) : Anthropic et Google
(Gemini) n'exposent aucune API de solde pour un compte standard a ce jour,
seule leur console web le montre - la barre laterale se contente donc de
liens directs vers ces deux-la (voir LIENS_PLATEFORMES_PAIEMENT).

Le solde DataForSEO est mis en cache en memoire et rafraichi periodiquement
par un job du planificateur (voir main.py) plutot qu'a chaque affichage de
page : la barre laterale apparait sur toutes les pages, un appel reseau a
chaque chargement ralentirait inutilement toute la plateforme.
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

from .rank_tracking import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD, identifiants_configures

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

SEUIL_ALERTE_SOLDE_DATAFORSEO_USD = float(os.getenv("DATAFORSEO_SEUIL_ALERTE_USD", "5"))

URL_USER_DATA = "https://api.dataforseo.com/v3/appendix/user_data"

LIENS_PLATEFORMES_PAIEMENT = [
    {"nom": "Claude (Anthropic)", "url": "https://platform.claude.com/settings/billing"},
    {"nom": "Gemini (Google AI)", "url": "https://aistudio.google.com/billing"},
    {"nom": "DataForSEO", "url": "https://app.dataforseo.com/"},
]

_cache_solde_dataforseo = {"valeur": None, "erreur": None, "verifie_le": None}


def rafraichir_solde_dataforseo() -> None:
    """Appele periodiquement par le planificateur (voir main.py) - met a jour le cache en memoire."""
    if not identifiants_configures():
        _cache_solde_dataforseo["valeur"] = None
        _cache_solde_dataforseo["erreur"] = "Identifiants DataForSEO non configures."
        _cache_solde_dataforseo["verifie_le"] = datetime.utcnow()
        return

    try:
        reponse = requests.get(
            URL_USER_DATA, auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), timeout=15
        )
        reponse.raise_for_status()
        resultat = reponse.json()["tasks"][0]["result"][0]
        _cache_solde_dataforseo["valeur"] = resultat["money"]["balance"]
        _cache_solde_dataforseo["erreur"] = None
    except Exception as erreur:
        _cache_solde_dataforseo["erreur"] = str(erreur)
    _cache_solde_dataforseo["verifie_le"] = datetime.utcnow()


def solde_dataforseo() -> dict:
    """
    Utilise directement dans les templates (Jinja) via templates.env.globals -
    ne fait jamais d'appel reseau, ne lit que le cache rempli par
    rafraichir_solde_dataforseo().
    """
    valeur = _cache_solde_dataforseo["valeur"]
    return {
        "valeur": valeur,
        "erreur": _cache_solde_dataforseo["erreur"],
        "verifie_le": _cache_solde_dataforseo["verifie_le"],
        "bas": valeur is not None and valeur < SEUIL_ALERTE_SOLDE_DATAFORSEO_USD,
    }
