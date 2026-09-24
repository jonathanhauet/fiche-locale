"""
Google Search Console : ce que Google dit du SITE WEB d'un client (clics,
impressions, recherches qui l'amenent). Un seul compte Google pour toute
l'agence (voir models.ParametreSearchConsole, comme Google Ads), ajoute comme
utilisateur dans la Search Console de chaque site ; chaque client est ensuite
rattache a une "propriete" (Client.search_console_site).

Les donnees ont environ 2 a 3 jours de retard cote Google : une periode ne doit
pas se terminer trop pres d'aujourd'hui.
"""

from datetime import date, timedelta
from urllib.parse import quote, urlparse

import requests

URL_BASE = "https://searchconsole.googleapis.com/webmasters/v3"
DELAI_SECONDES = 30
RETARD_DONNEES_JOURS = 3


def _appeler(identifiants, methode: str, chemin: str, **kwargs) -> dict:
    reponse = requests.request(
        methode, f"{URL_BASE}{chemin}", headers={"Authorization": f"Bearer {identifiants.token}"},
        timeout=DELAI_SECONDES, **kwargs,
    )
    if reponse.status_code == 403:
        raise RuntimeError(
            "Accès refusé par la Search Console : soit l'API « Google Search Console API » n'est pas activée dans le "
            "projet Google Cloud, soit le compte connecté n'est pas utilisateur de ce site."
        )
    if reponse.status_code != 200:
        raise RuntimeError(f"Échec de la Search Console (code {reponse.status_code}) : {reponse.text[:300]}")
    return reponse.json()


def lister_sites(identifiants) -> list[dict]:
    """[{"site": "sc-domain:exemple.fr", "droits": "siteOwner"}, ...] : les proprietes accessibles au compte connecte."""
    donnees = _appeler(identifiants, "GET", "/sites")
    return [
        {"site": entree.get("siteUrl", ""), "droits": entree.get("permissionLevel", "")}
        for entree in donnees.get("siteEntry", [])
        if entree.get("permissionLevel") != "siteUnverifiedUser"
    ]


def _hote(url: str) -> str:
    url = (url or "").strip().lower()
    if not url:
        return ""
    hote = urlparse(url if "://" in url else "https://" + url).netloc
    return hote[4:] if hote.startswith("www.") else hote


def suggerer_site(sites: list[dict], url_site: str) -> str:
    """Propriete correspondant a l'adresse du site du client (domaine entier de preference), ou ''."""
    hote = _hote(url_site)
    if not hote:
        return ""
    for entree in sites:
        if entree["site"] == f"sc-domain:{hote}":
            return entree["site"]
    for entree in sites:
        if not entree["site"].startswith("sc-domain:") and _hote(entree["site"]) == hote:
            return entree["site"]
    return ""


def _requete(identifiants, site: str, debut: date, fin: date, dimensions: list[str] = None, limite: int = 10) -> list[dict]:
    corps = {"startDate": debut.isoformat(), "endDate": fin.isoformat(), "rowLimit": limite}
    if dimensions:
        corps["dimensions"] = dimensions
    donnees = _appeler(identifiants, "POST", f"/sites/{quote(site, safe='')}/searchAnalytics/query", json=corps)
    return donnees.get("rows", [])


def resume_periode(identifiants, site: str, debut: date, fin: date, nb_top: int = 5) -> dict:
    """{"clics", "impressions", "top_requetes": [{"requete", "clics"}], "top_pages": [{"page", "clics"}]} pour la periode."""
    totaux = _requete(identifiants, site, debut, fin)
    ligne = totaux[0] if totaux else {}
    requetes = _requete(identifiants, site, debut, fin, ["query"], limite=25)
    pages = _requete(identifiants, site, debut, fin, ["page"], limite=25)

    def classer(lignes, cle):
        avec_clics = [l for l in lignes if l.get("clicks", 0) > 0]
        avec_clics.sort(key=lambda l: l["clicks"], reverse=True)
        return [{cle: l["keys"][0], "clics": int(l["clicks"])} for l in avec_clics[:nb_top]]

    return {
        "clics": int(ligne.get("clicks", 0)),
        "impressions": int(ligne.get("impressions", 0)),
        "top_requetes": classer(requetes, "requete"),
        "top_pages": classer(pages, "page"),
    }


def derniers_jours(identifiants, site: str, nb_jours: int = 28) -> dict:
    """Resume des nb_jours derniers jours disponibles + periode precedente equivalente (pour l'evolution)."""
    fin = date.today() - timedelta(days=RETARD_DONNEES_JOURS)
    debut = fin - timedelta(days=nb_jours - 1)
    fin_prec = debut - timedelta(days=1)
    debut_prec = fin_prec - timedelta(days=nb_jours - 1)
    actuel = resume_periode(identifiants, site, debut, fin)
    precedent = _requete(identifiants, site, debut_prec, fin_prec)
    clics_prec = int(precedent[0].get("clicks", 0)) if precedent else 0
    actuel["clics_precedents"] = clics_prec
    actuel["periode"] = {"debut": debut.isoformat(), "fin": fin.isoformat()}
    return actuel


def resume_mensuel(identifiants, site: str, debut: date, fin: date) -> dict:
    """Resume du mois du recap + clics du mois precedent (evolution affichee seulement si positive)."""
    duree = (fin - debut).days + 1
    fin_prec = debut - timedelta(days=1)
    debut_prec = fin_prec - timedelta(days=duree - 1)
    actuel = resume_periode(identifiants, site, debut, fin, nb_top=3)
    precedent = _requete(identifiants, site, debut_prec, fin_prec)
    actuel["clics_precedents"] = int(precedent[0].get("clicks", 0)) if precedent else 0
    return actuel
