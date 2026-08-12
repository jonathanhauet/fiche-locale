"""
Statistiques de performance d'une fiche Google Business Profile
(vues, appels, clics site, demandes d'itineraire...).
API : businessprofileperformance.googleapis.com/v1
"""

from datetime import date

import requests

METRIQUES = [
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_DIRECTION_REQUESTS",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
]

LIBELLES = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "Vues sur Maps (ordinateur)",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "Vues sur Recherche Google (ordinateur)",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "Vues sur Maps (mobile)",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "Vues sur Recherche Google (mobile)",
    "BUSINESS_CONVERSATIONS": "Messages reçus",
    "BUSINESS_DIRECTION_REQUESTS": "Demandes d'itinéraire",
    "CALL_CLICKS": "Clics sur \"Appeler\"",
    "WEBSITE_CLICKS": "Clics vers le site web",
}


def _params_date(prefixe: str, jour: date) -> dict:
    return {
        f"{prefixe}.year": jour.year,
        f"{prefixe}.month": jour.month,
        f"{prefixe}.day": jour.day,
    }


def recuperer_statistiques(identifiants, location_id: str, date_debut: date, date_fin: date) -> dict:
    """
    Renvoie un dictionnaire {libelle_lisible: total_sur_periode} pour une fiche,
    entre date_debut et date_fin (inclus).
    """
    url = f"https://businessprofileperformance.googleapis.com/v1/locations/{location_id}:fetchMultiDailyMetricsTimeSeries"

    params = [("dailyMetrics", metrique) for metrique in METRIQUES]
    params += list(_params_date("dailyRange.start_date", date_debut).items())
    params += list(_params_date("dailyRange.end_date", date_fin).items())

    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, params=params)
    if reponse.status_code != 200:
        raise RuntimeError(
            f"Echec de la recuperation des statistiques (code {reponse.status_code}) : {reponse.text}"
        )

    totaux = {metrique: 0 for metrique in METRIQUES}

    for groupe in reponse.json().get("multiDailyMetricTimeSeries", []):
        for serie in groupe.get("dailyMetricTimeSeries", []):
            metrique = serie.get("dailyMetric")
            if metrique not in totaux:
                continue
            for point in serie.get("timeSeries", {}).get("datedValues", []):
                totaux[metrique] += int(point.get("value", 0))

    return {LIBELLES[m]: totaux[m] for m in METRIQUES}


def recuperer_mots_cles_recherche(identifiants, location_id: str, date_debut: date, date_fin: date, limite: int = 25) -> list[dict]:
    """
    Renvoie les mots-cles de recherche reels ayant amene des vues sur la fiche
    (endpoint searchkeywords de la Performance API), tries par impressions
    decroissantes. Granularite mensuelle uniquement cote Google : date_debut/
    date_fin sont ramenes a leur mois.

    Chaque entree : {"mot_cle": str, "impressions": int, "est_seuil": bool}.
    "est_seuil" = True signifie que Google n'a pas donne le chiffre exact
    (trop faible pour etre precis) mais un seuil maximum a la place.
    """
    parent = f"locations/{location_id}"
    url = f"https://businessprofileperformance.googleapis.com/v1/{parent}/searchkeywords/impressions/monthly"

    params = {
        "monthlyRange.startMonth.year": date_debut.year,
        "monthlyRange.startMonth.month": date_debut.month,
        "monthlyRange.endMonth.year": date_fin.year,
        "monthlyRange.endMonth.month": date_fin.month,
        "pageSize": 100,
    }

    resultats = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, params=params)
        if reponse.status_code != 200:
            raise RuntimeError(
                f"Echec de la recuperation des mots-cles de recherche (code {reponse.status_code}) : {reponse.text}"
            )
        donnees = reponse.json()
        for item in donnees.get("searchKeywordsCounts", []):
            valeur = item.get("insightsValue", {}) or {}
            est_seuil = "threshold" in valeur
            impressions = int(valeur.get("value") or valeur.get("threshold") or 0)
            resultats.append({
                "mot_cle": item.get("searchKeyword", ""),
                "impressions": impressions,
                "est_seuil": est_seuil,
            })
        page_token = donnees.get("nextPageToken")
        if not page_token:
            break

    resultats.sort(key=lambda r: r["impressions"], reverse=True)
    return resultats[:limite] if limite else resultats
