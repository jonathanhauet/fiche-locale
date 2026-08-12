"""
Recherche de lieux (villes, adresses) pour remplir automatiquement le geotag
d'une photo. Utilise Nominatim (OpenStreetMap), gratuit et sans cle API,
adapte a un usage ponctuel comme celui-ci (voir leur politique d'usage :
https://operations.osmfoundation.org/policies/nominatim/ - 1 requete/seconde,
User-Agent identifiable).
"""

import requests

URL_RECHERCHE = "https://nominatim.openstreetmap.org/search"
EN_TETE = {"User-Agent": "FicheLocale-PlateformeWeb/1.0 (jonathan.hauet@gmail.com)"}


def rechercher_lieu(recherche: str) -> list[dict]:
    """Renvoie jusqu'a 5 lieux correspondants : [{"nom": str, "latitude": float, "longitude": float}, ...]"""
    if not recherche.strip():
        return []

    reponse = requests.get(
        URL_RECHERCHE,
        params={"q": recherche, "format": "json", "limit": 5, "accept-language": "fr"},
        headers=EN_TETE,
        timeout=10,
    )
    if reponse.status_code != 200:
        return []

    return [
        {"nom": item["display_name"], "latitude": float(item["lat"]), "longitude": float(item["lon"])}
        for item in reponse.json()
    ]
