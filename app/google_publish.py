"""
Publication d'un post et verification de son etat sur l'API Google Business
Profile (meme logique que publication_google.py / traitement_post.py).
"""

import time

import requests

from . import models

ATTENTE_VERIFICATION_SECONDES = 15

# (valeur API, libelle affiche) - correspond aux boutons "appel a l'action" proposes par Google.
OPTIONS_APPEL_ACTION = [
    ("", "Aucun appel à l'action"),
    ("BOOK", "Réserver"),
    ("CALL", "Appeler maintenant"),
    ("LEARN_MORE", "En savoir plus"),
    ("ORDER", "Commander en ligne"),
    ("SHOP", "Acheter"),
    ("SIGN_UP", "S'inscrire"),
]

# (valeur API, libelle affiche) - les 3 formats de post proposes par Google.
TYPES_POST = [
    ("STANDARD", "Standard"),
    ("EVENT", "Événement"),
    ("OFFER", "Offre"),
]


def _heure_vers_dict(heure_str: str):
    if not heure_str:
        return None
    try:
        heure, minute = (int(x) for x in heure_str.split(":"))
        return {"hours": heure, "minutes": minute}
    except ValueError:
        return None


def _date_vers_dict(valeur_date):
    if not valeur_date:
        return None
    return {"year": valeur_date.year, "month": valeur_date.month, "day": valeur_date.day}


def publier_un_post(
    identifiants, account_id: str, location_id: str, texte: str, image_url: str = None,
    type_appel_action: str = "", url_appel_action: str = "",
    type_post: str = "STANDARD",
    evenement_titre: str = "", evenement_date_debut=None, evenement_heure_debut: str = "",
    evenement_date_fin=None, evenement_heure_fin: str = "",
    offre_code: str = "", offre_url: str = "", offre_conditions: str = "",
):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/localPosts"
    type_post = type_post or "STANDARD"
    corps = {"languageCode": "fr", "summary": texte, "topicType": type_post}
    if image_url:
        corps["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": image_url}]

    # EVENT et OFFER necessitent tous deux un planning (date de debut/fin) ;
    # l'heure est optionnelle (evenement sur la journee entiere si absente).
    if type_post in ("EVENT", "OFFER") and evenement_date_debut and evenement_date_fin:
        planning = {
            "startDate": _date_vers_dict(evenement_date_debut),
            "endDate": _date_vers_dict(evenement_date_fin),
        }
        heure_debut = _heure_vers_dict(evenement_heure_debut)
        heure_fin = _heure_vers_dict(evenement_heure_fin)
        if heure_debut:
            planning["startTime"] = heure_debut
        if heure_fin:
            planning["endTime"] = heure_fin
        corps["event"] = {"title": evenement_titre or "Événement", "schedule": planning}

    if type_post == "OFFER":
        # Le bouton d'appel a l'action classique est ignore par Google pour ce type de post :
        # c'est le lien de l'offre (redeemOnlineUrl) qui fait office de bouton.
        offre = {}
        if offre_code.strip():
            offre["couponCode"] = offre_code.strip()
        if offre_url.strip():
            offre["redeemOnlineUrl"] = offre_url.strip()
        if offre_conditions.strip():
            offre["termsConditions"] = offre_conditions.strip()
        if offre:
            corps["offer"] = offre
    elif type_appel_action:
        appel_action = {"actionType": type_appel_action}
        # CALL utilise automatiquement le telephone deja renseigne sur la fiche :
        # aucune URL a fournir (et la fiche doit avoir un numero pour que le bouton apparaisse).
        if type_appel_action != "CALL" and url_appel_action:
            appel_action["url"] = url_appel_action
        corps["callToAction"] = appel_action

    reponse = requests.post(url, headers={"Authorization": f"Bearer {identifiants.token}"}, json=corps)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de la publication (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def verifier_etat_post(identifiants, nom_post: str):
    url = f"https://mybusiness.googleapis.com/v4/{nom_post}"
    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"})
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la verification (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def publier_et_verifier(db, identifiants, post: "models.Post") -> str:
    """
    Publie un post, attend, verifie son etat, journalise (evenements_publication)
    et met a jour son statut. Renvoie l'etat final obtenu.
    """
    try:
        reponse_publication = publier_un_post(
            identifiants,
            post.client.account_id,
            post.client.location_id,
            post.texte,
            image_url=post.image_url or None,
            type_appel_action=post.type_appel_action or "",
            url_appel_action=post.url_appel_action or "",
            type_post=post.type_post or "STANDARD",
            evenement_titre=post.evenement_titre or post.titre,
            evenement_date_debut=post.evenement_date_debut,
            evenement_heure_debut=post.evenement_heure_debut or "",
            evenement_date_fin=post.evenement_date_fin,
            evenement_heure_fin=post.evenement_heure_fin or "",
            offre_code=post.offre_code or "",
            offre_url=post.offre_url or "",
            offre_conditions=post.offre_conditions or "",
        )
    except Exception:
        post.statut = "ECHEC_PUBLICATION"
        db.add(models.EvenementPublication(post_id=post.id, etat="ECHEC_PUBLICATION"))
        db.commit()
        raise

    nom_post = reponse_publication.get("name")
    post.id_post_google = nom_post
    db.commit()

    time.sleep(ATTENTE_VERIFICATION_SECONDES)

    try:
        verification = verifier_etat_post(identifiants, nom_post)
        etat = verification.get("state", "inconnu")
    except Exception:
        etat = "inconnu"

    post.statut = f"PUBLIE_{etat}"
    db.add(models.EvenementPublication(post_id=post.id, etat=etat))
    db.commit()

    return etat
