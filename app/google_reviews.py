"""
Gestion des avis Google Business Profile : liste (tous clients confondus),
reponse, modification et suppression de reponse. Meme famille d'API que les
posts (mybusiness.googleapis.com/v4), aucune donnee stockee localement :
Google reste la source de verite, comme pour les photos.
"""

from datetime import datetime

import requests

NOTES = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


def _lister_avis_dune_fiche(identifiants, account_id: str, location_id: str, toutes_les_pages: bool = False):
    """
    Recupere les avis d'une fiche. Par defaut, une seule page (jusqu'a 50 avis,
    suffisant pour l'ecran de gestion des avis). Avec toutes_les_pages=True,
    suit la pagination pour obtenir un total exact (utilise pour les statistiques).
    """
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews"
    avis = []
    page_token = None

    while True:
        params = {"pageSize": 50}
        if page_token:
            params["pageToken"] = page_token

        reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, params=params)
        if reponse.status_code != 200:
            break

        donnees = reponse.json()
        avis.extend(donnees.get("reviews", []))

        page_token = donnees.get("nextPageToken")
        if not toutes_les_pages or not page_token:
            break

    return avis


def lister_avis_multi_clients(identifiants_par_client: dict, clients: list) -> list[dict]:
    """
    Recupere les avis de plusieurs clients (ceux ayant une fiche Google associee et
    des identifiants resolus) et les renvoie sous forme d'une liste plate, triee :
    sans reponse d'abord, puis par date decroissante.
    identifiants_par_client : {client_id: identifiants Google}.
    """
    resultats = []

    for client in clients:
        if not client.account_id or not client.location_id:
            continue
        identifiants = identifiants_par_client.get(client.id)
        if not identifiants:
            continue

        for avis in _lister_avis_dune_fiche(identifiants, client.account_id, client.location_id):
            nom_ressource = avis["name"]  # accounts/{a}/locations/{l}/reviews/{r}
            review_id = nom_ressource.split("/")[-1]
            reponse_existante = avis.get("reviewReply")

            resultats.append({
                "client_id": client.id,
                "client_nom": client.nom,
                "account_id": client.account_id,
                "location_id": client.location_id,
                "review_id": review_id,
                "auteur": avis.get("reviewer", {}).get("displayName", "Anonyme"),
                "note": NOTES.get(avis.get("starRating"), 0),
                "commentaire": avis.get("comment", ""),
                "date_avis": avis.get("createTime", ""),
                "reponse": reponse_existante.get("comment") if reponse_existante else None,
                "date_reponse": reponse_existante.get("updateTime") if reponse_existante else None,
                "texte_suggere": None,
            })

    # Tri stable en deux temps : d'abord par date (recent en premier), puis
    # les avis sans reponse remontent au-dessus (l'ordre par date est conserve
    # a l'interieur de chaque groupe grace a la stabilite du tri).
    resultats.sort(key=lambda a: a["date_avis"], reverse=True)
    resultats.sort(key=lambda a: a["reponse"] is not None)

    return resultats


def repondre_avis(identifiants, account_id: str, location_id: str, review_id: str, texte: str):
    """Cree ou remplace la reponse a un avis (PUT = upsert cote API Google)."""
    url = (
        f"https://mybusiness.googleapis.com/v4/accounts/{account_id}"
        f"/locations/{location_id}/reviews/{review_id}/reply"
    )
    reponse = requests.put(
        url,
        headers={"Authorization": f"Bearer {identifiants.token}"},
        json={"comment": texte},
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'envoi de la reponse (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def supprimer_reponse_avis(identifiants, account_id: str, location_id: str, review_id: str):
    url = (
        f"https://mybusiness.googleapis.com/v4/accounts/{account_id}"
        f"/locations/{location_id}/reviews/{review_id}/reply"
    )
    reponse = requests.delete(url, headers={"Authorization": f"Bearer {identifiants.token}"})
    if reponse.status_code not in (200, 204):
        raise RuntimeError(f"Echec de la suppression (code {reponse.status_code}) : {reponse.text}")


def resumer_avis(
    identifiants, account_id: str, location_id: str, date_debut, date_fin,
    date_debut_n1=None, date_fin_n1=None,
) -> dict:
    """
    Resume des avis d'une fiche : note moyenne et total sur toute la duree,
    plus le detail des avis recus dans la periode [date_debut, date_fin] (inclus).

    Si date_debut_n1/date_fin_n1 sont fournis (periode de comparaison, typiquement
    la meme periode l'annee precedente), ajoute nombre_avis_periode_n1 et
    note_moyenne_periode_n1 - calcules a partir du meme releve d'avis, sans
    second appel a l'API.
    """
    tous_les_avis = _lister_avis_dune_fiche(identifiants, account_id, location_id, toutes_les_pages=True)

    notes_globales = [NOTES.get(a.get("starRating"), 0) for a in tous_les_avis if a.get("starRating")]
    note_moyenne_globale = round(sum(notes_globales) / len(notes_globales), 1) if notes_globales else None

    def _avis_de_la_periode(debut, fin):
        avis_periode = []
        for avis in tous_les_avis:
            cree_le = avis.get("createTime", "")
            try:
                date_creation = datetime.fromisoformat(cree_le.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if debut <= date_creation <= fin:
                avis_periode.append(avis)
        return avis_periode

    def _stats_periode(debut, fin):
        avis_periode = _avis_de_la_periode(debut, fin)
        notes_periode = [NOTES.get(a.get("starRating"), 0) for a in avis_periode if a.get("starRating")]
        note_moyenne = round(sum(notes_periode) / len(notes_periode), 1) if notes_periode else None
        return len(avis_periode), note_moyenne

    nombre_avis_periode, note_moyenne_periode = _stats_periode(date_debut, date_fin)

    resultat = {
        "note_moyenne_globale": note_moyenne_globale,
        "total_avis_global": len(tous_les_avis),
        "nombre_avis_periode": nombre_avis_periode,
        "note_moyenne_periode": note_moyenne_periode,
    }

    if date_debut_n1 and date_fin_n1:
        nombre_avis_periode_n1, note_moyenne_periode_n1 = _stats_periode(date_debut_n1, date_fin_n1)
        resultat["nombre_avis_periode_n1"] = nombre_avis_periode_n1
        resultat["note_moyenne_periode_n1"] = note_moyenne_periode_n1

    return resultat
