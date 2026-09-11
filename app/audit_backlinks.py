"""
Autorite du site (backlinks) via l'API DataForSEO Backlinks Summary Live -
meme compte DataForSEO que rank_tracking.py/citations.py (aucun nouvel
abonnement necessaire), complement a audit_site_technique.py (Google
PageSpeed) pour la section "Autorite du site" de l'audit prospect PDF.
Un seul appel resume par audit (~0.02$), pas de liste detaillee des
backlinks individuels : hors de propos pour un audit de decouverte.
"""

import requests

from .rank_tracking import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD, identifiants_configures

URL_BACKLINKS_SUMMARY = "https://api.dataforseo.com/v3/backlinks/summary/live"


def analyser_autorite(url: str) -> dict:
    """
    Renvoie {"rang": int, "backlinks": int, "domaines_referents": int}.
    rang : score DataForSEO Rank sur 100 (rank_scale=one_hundred), comparable
    au Domain Rating d'Ahrefs ou au DA de Moz - pas le meme referentiel exact,
    mais un ordre de grandeur equivalent pour situer l'autorite d'un domaine.
    """
    if not identifiants_configures():
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env.")
    if not url:
        raise RuntimeError("Aucune URL de site renseignee.")

    corps = [{"target": url.strip(), "rank_scale": "one_hundred"}]

    try:
        reponse = requests.post(
            URL_BACKLINKS_SUMMARY, auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=corps, timeout=30,
        )
    except requests.RequestException as erreur:
        raise RuntimeError(f"Erreur reseau vers DataForSEO : {erreur}") from erreur

    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'appel DataForSEO (code {reponse.status_code}) : {reponse.text}")

    donnees = reponse.json()
    taches = donnees.get("tasks") or []
    if not taches or taches[0].get("status_code") != 20000:
        message = taches[0].get("status_message") if taches else "reponse vide"
        raise RuntimeError(f"Erreur DataForSEO : {message}")

    # Contrairement aux autres endpoints DataForSEO utilises dans l'app (Maps
    # Live, SERP...), "result" contient ici directement l'objet resume - pas
    # de sous-cle "items" imbriquee (verifie contre un appel reel).
    resultats = taches[0].get("result") or []
    if not resultats:
        raise RuntimeError("Aucune donnee de backlinks disponible pour ce domaine.")

    item = resultats[0]
    return {
        "rang": round(item.get("rank") or 0),
        "backlinks": item.get("backlinks") or 0,
        "domaines_referents": item.get("referring_main_domains") or item.get("referring_domains") or 0,
    }
