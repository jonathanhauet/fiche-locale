"""
Gestion des administrateurs (qui a acces a quelle fiche) d'une fiche Google
Business Profile - Account Management API, distincte de la gestion du
contenu (google_location.py, qui gere infos/horaires/categories). Utilise
pour l'export des acces actuels et le remplacement en masse (voir /acces
dans main.py) - typiquement pour migrer d'anciennes adresses personnelles
vers des adresses d'entreprise (ex. Google Workspace) sur de nombreuses
fiches a la fois.

Important : inviter un administrateur (inviter_administrateur) place
l'invitation en attente ("pendingInvitation") - Google exige que l'adresse
invitee se connecte elle-meme pour accepter, aucun moyen de forcer l'acces
par API pour des raisons de securite. Retirer un administrateur, en
revanche, est immediat et ne demande pas son accord.
"""

import requests

URL_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"

ROLES = ["OWNER", "MANAGER", "SITE_MANAGER"]
LIBELLES_ROLE = {
    "PRIMARY_OWNER": "Propriétaire principal",
    "OWNER": "Propriétaire",
    "MANAGER": "Gestionnaire",
    "SITE_MANAGER": "Gestionnaire de site",
    "ADMIN_ROLE_UNSPECIFIED": "Non précisé",
}


def lister_administrateurs(identifiants, location_id: str) -> list[dict]:
    """
    Renvoie [{"nom_ressource", "identifiant", "compte_id", "role", "en_attente"}, ...]
    pour une fiche.

    Important - verifie contre un appel reel : le champ "admin" ne contient
    l'email QUE pour une invitation encore en attente (avant acceptation).
    Une fois l'invitation acceptee, Google le remplace par le NOM AFFICHE du
    compte Google (ex. "Jonathan Hauet"), plus jamais l'email - aucun email
    n'est expose pour un administrateur deja actif, seulement son identifiant
    de compte opaque (compte_id). Un rapprochement par email exact ne
    fonctionnera donc que pour des invitations pas encore acceptees.
    """
    url = f"{URL_BASE}/locations/{location_id}/admins"
    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, timeout=30)
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture des administrateurs (code {reponse.status_code}) : {reponse.text}")

    admins = reponse.json().get("admins", [])
    return [
        {
            "nom_ressource": admin.get("name", ""),
            "identifiant": admin.get("admin", ""),
            "compte_id": admin.get("account", ""),
            "role": admin.get("role", ""),
            "en_attente": bool(admin.get("pendingInvitation", False)),
        }
        for admin in admins
    ]


def inviter_administrateur(identifiants, location_id: str, email: str, role: str = "MANAGER") -> dict:
    """
    Envoie une invitation a administrer la fiche - reste "en attente" tant
    que l'adresse invitee ne s'est pas connectee elle-meme pour l'accepter.
    """
    url = f"{URL_BASE}/locations/{location_id}/admins"
    reponse = requests.post(
        url, headers={"Authorization": f"Bearer {identifiants.token}"},
        json={"admin": email, "role": role}, timeout=30,
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'invitation de {email} (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def retirer_administrateur(identifiants, nom_ressource_admin: str) -> None:
    """nom_ressource_admin : le champ 'nom_ressource' renvoye par lister_administrateurs (locations/{id}/admins/{admin_id})."""
    url = f"{URL_BASE}/{nom_ressource_admin}"
    reponse = requests.delete(url, headers={"Authorization": f"Bearer {identifiants.token}"}, timeout=30)
    if reponse.status_code not in (200, 204):
        raise RuntimeError(f"Echec du retrait de l'administrateur (code {reponse.status_code}) : {reponse.text}")
