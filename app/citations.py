"""
Verification de la presence d'une fiche sur quelques annuaires/plateformes
tiers (citations locales), via l'API DataForSEO deja utilisee pour le suivi
de positions (voir rank_tracking.py). Verification a la demande (bouton),
jamais automatique : chaque annuaire verifie consomme une requete DataForSEO
facturee a l'usage.

Deux methodes selon l'annuaire :
- "google_site" : recherche Google restreinte au domaine de l'annuaire
  (site:domaine "nom" ville) - fonctionne pour les annuaires dont les fiches
  individuelles sont indexees par Google (Pages Jaunes, Apple Plans, Yelp).
- "bing_local_pack" : recherche directe sur Bing (le nom n'a pas de fiche
  publique indexable par Google pour ce cas - Bing Places n'existe que dans
  les propres resultats de recherche locale de Bing).
"""

import requests

from .rank_tracking import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD, _mots, _score_correspondance, identifiants_configures

URL_GOOGLE_ORGANIC = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
URL_BING_ORGANIC = "https://api.dataforseo.com/v3/serp/bing/organic/live/advanced"

SEUIL_CORRESPONDANCE_BING = 0.5

ANNUAIRES = [
    {"id": "pages_jaunes", "nom": "Pages Jaunes", "domaine": "pagesjaunes.fr", "methode": "google_site"},
    {"id": "apple_plans", "nom": "Apple Plans", "domaine": "maps.apple.com", "methode": "google_site"},
    {"id": "yelp", "nom": "Yelp", "domaine": "yelp.fr", "methode": "google_site"},
    {"id": "bing_places", "nom": "Bing Places", "domaine": None, "methode": "bing_local_pack"},
]

ANNUAIRES_PAR_ID = {a["id"]: a for a in ANNUAIRES}

# DataForSEO attend un nom de lieu present dans sa propre base (location_name)
# - une simple ville (ex. "Ath") echoue si DataForSEO ne la reconnait pas telle
# quelle. On cible donc toujours le PAYS (valeur sure, toujours reconnue) pour
# ce parametre, et on laisse la ville faire son travail directement dans le
# texte de la requete (voir _verifier_google_site/_verifier_bing_local_pack).
PAYS_DATAFORSEO = {
    "FR": "France", "BE": "Belgium", "CH": "Switzerland", "LU": "Luxembourg", "CA": "Canada",
}


def nom_pays_dataforseo(code_pays: str) -> str:
    return PAYS_DATAFORSEO.get((code_pays or "").upper(), "France")


def _appeler_dataforseo(url: str, corps: list[dict]) -> list[dict]:
    try:
        reponse = requests.post(url, auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=corps, timeout=30)
    except requests.RequestException as erreur:
        raise RuntimeError(f"Erreur reseau vers DataForSEO : {erreur}") from erreur

    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'appel DataForSEO (code {reponse.status_code}) : {reponse.text}")

    donnees = reponse.json()
    taches = donnees.get("tasks") or []
    if not taches or taches[0].get("status_code") != 20000:
        message = taches[0].get("status_message") if taches else "reponse vide"
        raise RuntimeError(f"Erreur DataForSEO : {message}")

    return (taches[0].get("result") or [{}])[0].get("items") or []


def _verifier_google_site(domaine: str, nom_entreprise: str, ville: str, pays: str) -> dict:
    requete = f'site:{domaine} "{nom_entreprise}"' + (f" {ville}" if ville else "")
    corps = [{
        "keyword": requete,
        "location_name": pays,
        "language_code": "fr",
        "device": "desktop",
    }]
    items = _appeler_dataforseo(URL_GOOGLE_ORGANIC, corps)
    # Google ignore parfois silencieusement le "site:" quand il ne trouve rien
    # sur ce domaine precis, et renvoie a la place des resultats generiques
    # hors-sujet - sans cette verification du domaine, ces resultats generiques
    # seraient a tort comptes comme une presence confirmee sur l'annuaire.
    resultat_organique = next(
        (i for i in items if i.get("type") == "organic" and domaine in (i.get("domain") or "")),
        None,
    )
    return {
        "trouve": resultat_organique is not None,
        "url": resultat_organique.get("url") if resultat_organique else None,
    }


def _verifier_bing_local_pack(nom_entreprise: str, ville: str, pays: str) -> dict:
    requete = f"{nom_entreprise} {ville}".strip()
    corps = [{
        "keyword": requete,
        "location_name": pays,
        "language_code": "fr",
        "device": "desktop",
    }]
    items = _appeler_dataforseo(URL_BING_ORGANIC, corps)
    mots_requete = _mots(requete)

    meilleur, meilleur_score = None, 0.0
    for item in items:
        if item.get("type") != "local_pack":
            continue
        score = _score_correspondance(item.get("title", ""), nom_entreprise, mots_requete)
        if score > meilleur_score:
            meilleur, meilleur_score = item, score

    if meilleur is not None and meilleur_score >= SEUIL_CORRESPONDANCE_BING:
        return {"trouve": True, "url": None}
    return {"trouve": False, "url": None}


def verifier_citations(nom_entreprise: str, ville: str, code_pays: str, annuaires_ids: list[str]) -> list[dict]:
    """
    Renvoie [{"id", "nom", "trouve": bool, "url": str|None, "erreur": str|None}, ...]
    pour chaque annuaire demande. Une erreur sur un annuaire n'empeche pas de
    verifier les autres. code_pays : code ISO a 2 lettres (storefrontAddress.regionCode).
    """
    if not identifiants_configures():
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env.")

    pays = nom_pays_dataforseo(code_pays)
    resultats = []
    for annuaire_id in annuaires_ids:
        annuaire = ANNUAIRES_PAR_ID.get(annuaire_id)
        if not annuaire:
            continue
        try:
            if annuaire["methode"] == "google_site":
                verif = _verifier_google_site(annuaire["domaine"], nom_entreprise, ville, pays)
            else:
                verif = _verifier_bing_local_pack(nom_entreprise, ville, pays)
            resultats.append({"id": annuaire["id"], "nom": annuaire["nom"], "erreur": None, **verif})
        except Exception as erreur:
            resultats.append({
                "id": annuaire["id"], "nom": annuaire["nom"],
                "trouve": None, "url": None, "erreur": str(erreur),
            })
    return resultats
