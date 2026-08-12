"""
Liens d'action permanents sur une fiche Google Business Profile (reserver une
table, commander en livraison, prendre rendez-vous...). Different des boutons
d'appel a l'action sur les posts (google_publish.py) : ici c'est attache a la
fiche elle-meme, pas a une publication.

API : mybusinessplaceactions.googleapis.com/v1. Aucune donnee stockee en
local, Google reste la source de verite (meme logique que google_location.py).

L'eligibilite de chaque type de lien selon la categorie d'etablissement n'est
pas documentee publiquement par Google : impossible de filtrer les options a
l'avance cote plateforme. Les erreurs renvoyees par Google sont donc
remontees telles quelles a l'utilisateur.
"""

import requests

URL_BASE = "https://mybusinessplaceactions.googleapis.com/v1"

TYPES_ACTION = [
    ("APPOINTMENT", "Prendre rendez-vous"),
    ("ONLINE_APPOINTMENT", "Rendez-vous en ligne"),
    ("DINING_RESERVATION", "Réserver une table"),
    ("FOOD_ORDERING", "Commander"),
    ("FOOD_DELIVERY", "Commander en livraison"),
    ("FOOD_TAKEOUT", "Commander à emporter"),
    ("SHOP_ONLINE", "Acheter en ligne"),
]

LIBELLES_TYPE_ACTION = dict(TYPES_ACTION)


def lister_liens(identifiants, location_id: str) -> list[dict]:
    url = f"{URL_BASE}/locations/{location_id}/placeActionLinks"
    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"})
    if reponse.status_code != 200:
        raise RuntimeError(
            f"Echec de la recuperation des liens d'action (code {reponse.status_code}) : {reponse.text}"
        )
    return reponse.json().get("placeActionLinks", [])


def creer_lien(identifiants, location_id: str, type_action: str, uri: str, est_prefere: bool = False) -> dict:
    url = f"{URL_BASE}/locations/{location_id}/placeActionLinks"
    corps = {"placeActionType": type_action, "uri": uri, "isPreferred": est_prefere}
    reponse = requests.post(url, headers={"Authorization": f"Bearer {identifiants.token}"}, json=corps)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(
            f"Echec de la creation du lien d'action (code {reponse.status_code}) : {reponse.text}"
        )
    return reponse.json()


def supprimer_lien(identifiants, location_id: str, lien_id: str) -> None:
    url = f"{URL_BASE}/locations/{location_id}/placeActionLinks/{lien_id}"
    reponse = requests.delete(url, headers={"Authorization": f"Bearer {identifiants.token}"})
    if reponse.status_code not in (200, 204):
        raise RuntimeError(
            f"Echec de la suppression du lien d'action (code {reponse.status_code}) : {reponse.text}"
        )
