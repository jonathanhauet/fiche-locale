"""
Appels a l'API Google Business Profile utilises par la plateforme web.
Reprend la meme logique que les scripts en ligne de commande
(connexion_google.py, publication_google.py), sous forme de fonctions pures.
"""

import time
from datetime import datetime

import requests

from . import image_geotag, ovh_upload


def _lister_tous_les_comptes(identifiants):
    """Suit la pagination pour renvoyer TOUS les comptes accessibles."""
    comptes = []
    page_token = None

    while True:
        params = {"pageSize": 20}
        if page_token:
            params["pageToken"] = page_token

        reponse = requests.get(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            headers={"Authorization": f"Bearer {identifiants.token}"},
            params=params,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
        comptes.extend(donnees.get("accounts", []))

        page_token = donnees.get("nextPageToken")
        if not page_token:
            break

    return comptes


def _lister_toutes_les_fiches(identifiants, nom_compte_complet: str):
    """Suit la pagination pour renvoyer TOUTES les fiches d'un compte."""
    fiches = []
    page_token = None

    while True:
        params = {"readMask": "name,title", "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token

        reponse = requests.get(
            f"https://mybusinessbusinessinformation.googleapis.com/v1/{nom_compte_complet}/locations",
            headers={"Authorization": f"Bearer {identifiants.token}"},
            params=params,
        )
        if reponse.status_code != 200:
            break
        donnees = reponse.json()
        fiches.extend(donnees.get("locations", []))

        page_token = donnees.get("nextPageToken")
        if not page_token:
            break

    return fiches


def lister_comptes_et_fiches(identifiants):
    """
    Renvoie une liste plate de fiches disponibles pour le compte Google connecte :
    [{"account_id": str, "location_id": str, "nom_compte": str, "nom_fiche": str}, ...]
    Suit la pagination des deux APIs (comptes et fiches) pour ne rien manquer.
    """
    resultats = []

    for compte in _lister_tous_les_comptes(identifiants):
        nom_compte_complet = compte["name"]  # format "accounts/123456789"
        account_id = nom_compte_complet.split("/")[-1]
        nom_compte_affiche = compte.get("accountName", "(compte sans nom)")

        for fiche in _lister_toutes_les_fiches(identifiants, nom_compte_complet):
            location_id = fiche["name"].split("/")[-1]
            resultats.append({
                "account_id": account_id,
                "location_id": location_id,
                "nom_compte": nom_compte_affiche,
                "nom_fiche": fiche.get("title", "(sans titre)"),
            })

    return resultats


def lister_fiches_multi_comptes(comptes_avec_identifiants) -> list[dict]:
    """
    comptes_avec_identifiants : liste de tuples (compte_google_id, libelle, identifiants).
    Agrege les fiches de tous les comptes Google connectes, chacune taguee avec le
    compte dont elle provient (necessaire pour savoir avec quel compte publier ensuite).
    """
    resultats = []
    for compte_google_id, libelle, identifiants in comptes_avec_identifiants:
        for fiche in lister_comptes_et_fiches(identifiants):
            fiche["compte_google_id"] = compte_google_id
            fiche["compte_libelle"] = libelle
            resultats.append(fiche)
    return resultats


def lister_photos(identifiants, account_id: str, location_id: str):
    """Renvoie les photos deja presentes sur une fiche : [{"url": str, "categorie": str}, ...]"""
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/media"
    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"})
    if reponse.status_code != 200:
        return []

    resultats = []
    for item in reponse.json().get("mediaItems", []):
        url_photo = item.get("googleUrl")
        if url_photo:
            resultats.append({
                "url": url_photo,
                # Une photo tres recemment ajoutee peut ne pas encore etre disponible a
                # l'URL pleine resolution (delai de traitement cote Google) ; la miniature
                # est generalement disponible plus vite et sert de repli a l'affichage.
                "miniature": item.get("thumbnailUrl", ""),
                "categorie": item.get("locationAssociation", {}).get("category", ""),
            })
    return resultats


def lister_posts(identifiants, account_id: str, location_id: str):
    """Renvoie les posts actuellement presents sur la fiche (Google les fait expirer au bout de 7 jours)."""
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/localPosts"
    resultats = []
    page_token = None

    while True:
        params = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token

        reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, params=params)
        if reponse.status_code != 200:
            break
        donnees = reponse.json()

        for item in donnees.get("localPosts", []):
            media = item.get("media") or []
            cree_le = item.get("createTime", "")
            try:
                date_affichee = datetime.fromisoformat(cree_le.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
            except ValueError:
                date_affichee = cree_le
            resultats.append({
                "texte": item.get("summary", ""),
                "etat": item.get("state", ""),
                "date_creation": date_affichee,
                "date_creation_brute": cree_le,
                "id_post_google": item.get("name", ""),
                "url_image": media[0].get("googleUrl") if media else "",
                "url_recherche": item.get("searchUrl", ""),
            })

        page_token = donnees.get("nextPageToken")
        if not page_token:
            break

    return resultats


CATEGORIES_PHOTO = [
    "ADDITIONAL", "COVER", "PROFILE", "LOGO", "EXTERIOR", "INTERIOR",
    "PRODUCT", "AT_WORK", "FOOD_AND_DRINK", "MENU", "TEAMS",
]

# Categories envoyees telles quelles a l'API Google (valeurs figees cote Google) ;
# seul le libelle affiche a l'utilisateur est traduit.
LIBELLES_CATEGORIE_PHOTO = {
    "ADDITIONAL": "Autre",
    "COVER": "Couverture",
    "PROFILE": "Photo de profil",
    "LOGO": "Logo",
    "EXTERIOR": "Extérieur",
    "INTERIOR": "Intérieur",
    "PRODUCT": "Produit",
    "AT_WORK": "Au travail",
    "FOOD_AND_DRINK": "Nourriture et boissons",
    "MENU": "Menu",
    "TEAMS": "Équipe",
}


def ajouter_photo(
    identifiants, account_id: str, location_id: str, url_source: str,
    categorie: str = "ADDITIONAL", legende: str = "",
):
    """
    Publie une photo deja hebergee publiquement (url_source) sur la fiche Google.
    Categorie parmi CATEGORIES_PHOTO (Google l'utilise pour classer la photo sur la fiche).
    La legende (description) n'est modifiable qu'a la creation cote Google, jamais
    apres, et est ignoree pour la categorie COVER.
    """
    corps = {
        "mediaFormat": "PHOTO",
        "locationAssociation": {"category": categorie},
        "sourceUrl": url_source,
    }
    if legende.strip():
        corps["description"] = legende.strip()

    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/media"
    reponse = requests.post(url, headers={"Authorization": f"Bearer {identifiants.token}"}, json=corps)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'ajout de la photo (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def publier_photo_fiche(db, identifiants, photo) -> str:
    """
    Envoie une photo en attente (models.PhotoFiche) sur la fiche Google, et met a
    jour son statut (PUBLIE_LIVE ou ECHEC_PUBLICATION). Meme logique que
    google_publish.publier_et_verifier pour les posts.
    """
    try:
        if photo.latitude is not None and photo.longitude is not None:
            octets_originaux = requests.get(photo.url_image, timeout=30).content
            octets_geotagges = image_geotag.ajouter_geotag(octets_originaux, photo.latitude, photo.longitude)
            nom_fichier = f"geotag_{photo.id}_{int(time.time())}.jpg"
            photo.url_image = ovh_upload.envoyer_octets(octets_geotagges, nom_fichier)
            db.commit()

        reponse = ajouter_photo(
            identifiants, photo.client.account_id, photo.client.location_id,
            photo.url_image, photo.categorie, photo.legende,
        )
    except Exception:
        photo.statut = "ECHEC_PUBLICATION"
        db.commit()
        raise

    photo.statut = "PUBLIE_LIVE"
    photo.id_media_google = reponse.get("name", "")
    db.commit()
    return photo.statut
