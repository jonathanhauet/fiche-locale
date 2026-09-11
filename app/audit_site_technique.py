"""
Audit technique d'un site web via l'API Google PageSpeed Insights (gratuite,
quota genereux avec une cle API) - complement a audit_prospect.py pour la
section "Votre site face a Google" de l'audit prospect PDF. Analyse la page
d'accueil uniquement (pas le site entier).
"""

import os

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLE_API = os.getenv("GOOGLE_PAGESPEED_API_KEY")
URL_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# (id de l'audit Lighthouse, libelle affiche) - limite aux audits SEO les
# plus parlants pour un client non technique, plutot que la liste complete.
AUDITS_SUIVIS = [
    ("document-title", "Titre de page renseigne"),
    ("meta-description", "Description meta renseignee"),
    ("viewport", "Adapte aux mobiles"),
    ("image-alt", "Texte alternatif sur les images"),
    ("is-crawlable", "Indexable par Google"),
    ("robots-txt", "Fichier robots.txt valide"),
    ("canonical", "URL canonique correcte"),
]


def identifiants_configures() -> bool:
    return bool(CLE_API)


def analyser_site(url: str) -> dict:
    """
    Renvoie {"url", "score_performance", "score_seo", "premier_affichage",
    "affichage_complet", "points_bloquants": [...], "points_positifs": [...]}
    - score_performance/score_seo en pourcentage (0-100) ou None si absent de
    la reponse. Les audits "non applicables" a ce site (score None cote
    Lighthouse) sont ignores plutot que comptes comme un defaut.
    """
    if not CLE_API:
        raise RuntimeError("GOOGLE_PAGESPEED_API_KEY manquant dans plateforme_web/.env.")
    if not url:
        raise RuntimeError("Aucune URL de site renseignee.")

    reponse = requests.get(
        URL_API,
        params={"url": url, "key": CLE_API, "strategy": "mobile", "category": ["performance", "seo"]},
        timeout=45,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'analyse PageSpeed (code {reponse.status_code}) : {reponse.text}")

    donnees = reponse.json()
    resultat = donnees.get("lighthouseResult") or {}
    categories = resultat.get("categories") or {}
    audits = resultat.get("audits") or {}

    def _score_pourcentage(categorie: str):
        score = (categories.get(categorie) or {}).get("score")
        return round(score * 100) if score is not None else None

    def _valeur_affichee(audit_id: str) -> str:
        return (audits.get(audit_id) or {}).get("displayValue", "")

    points_bloquants, points_positifs = [], []
    for audit_id, libelle in AUDITS_SUIVIS:
        audit = audits.get(audit_id) or {}
        score = audit.get("score")
        if score is None:
            continue  # audit non applicable a ce site (ex. hors du champ mesure)
        cible = points_bloquants if score < 1 else points_positifs
        cible.append({"libelle": libelle})

    return {
        "url": url,
        "score_performance": _score_pourcentage("performance"),
        "score_seo": _score_pourcentage("seo"),
        "premier_affichage": _valeur_affichee("first-contentful-paint"),
        "affichage_complet": _valeur_affichee("largest-contentful-paint"),
        "points_bloquants": points_bloquants,
        "points_positifs": points_positifs,
    }
