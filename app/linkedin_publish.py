"""
Publication sur un profil LinkedIn personnel via l'API Posts (REST, scope
w_member_social - voir linkedin_oauth.py). Image ou video optionnelle : flux
d'upload en plusieurs etapes (Images/Videos API) - LinkedIn fournit une ou
plusieurs URL de televersement pre-signees, pas besoin d'heberger le fichier
soi-meme au prealable (contrairement a Meta/Instagram).
"""

import time

import requests

URL_POSTS = "https://api.linkedin.com/rest/posts"
URL_IMAGES = "https://api.linkedin.com/rest/images"
URL_VIDEOS = "https://api.linkedin.com/rest/videos"
VERSION_API = "202606"  # LinkedIn ne garde une version active qu'environ 1 an ; a avancer periodiquement (voir "LinkedIn-Version" dans la doc developer LinkedIn)
ATTENTE_VIDEO_SECONDES = 3
TENTATIVES_MAX_VIDEO = 60  # jusqu'a 3 minutes : traitement LinkedIn plus lent qu'une image


def _entetes(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": VERSION_API,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _initialiser_upload_image(access_token: str, identifiant_membre: str) -> tuple[str, str]:
    """Renvoie (url_televersement, urn_image)."""
    reponse = requests.post(
        f"{URL_IMAGES}?action=initializeUpload",
        json={"initializeUploadRequest": {"owner": f"urn:li:person:{identifiant_membre}"}},
        headers=_entetes(access_token),
        timeout=30,
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'initialisation de l'upload d'image LinkedIn (code {reponse.status_code}) : {reponse.text}")
    valeur = reponse.json()["value"]
    return valeur["uploadUrl"], valeur["image"]


def _televerser_image(url_televersement: str, octets_image: bytes):
    reponse = requests.put(url_televersement, data=octets_image, timeout=60)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec du televersement de l'image LinkedIn (code {reponse.status_code})")


def _initialiser_upload_video(access_token: str, identifiant_membre: str, taille_octets: int) -> tuple[list[dict], str, str]:
    """Renvoie (instructions_televersement, urn_video, jeton_televersement). Une video (contrairement a une image) peut etre decoupee en plusieurs tranches par LinkedIn : une seule pour un fichier de taille modeste comme les notres."""
    reponse = requests.post(
        f"{URL_VIDEOS}?action=initializeUpload",
        json={"initializeUploadRequest": {
            "owner": f"urn:li:person:{identifiant_membre}", "fileSizeBytes": taille_octets,
            "uploadCaptions": False, "uploadThumbnail": False,
        }},
        headers=_entetes(access_token), timeout=30,
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de l'initialisation de l'upload video LinkedIn (code {reponse.status_code}) : {reponse.text}")
    valeur = reponse.json()["value"]
    return valeur["uploadInstructions"], valeur["video"], valeur["uploadToken"]


def _televerser_video(instructions: list[dict], octets_video: bytes) -> list[str]:
    """Televerse chaque tranche indiquee par LinkedIn et renvoie les ETag (a fournir tels quels a finalizeUpload, dans le meme ordre)."""
    etags = []
    for instruction in instructions:
        premier_octet, dernier_octet = instruction["firstByte"], instruction["lastByte"]
        reponse = requests.put(instruction["uploadUrl"], data=octets_video[premier_octet:dernier_octet + 1], timeout=120)
        if reponse.status_code not in (200, 201):
            raise RuntimeError(f"Echec du televersement de la video LinkedIn (code {reponse.status_code})")
        etags.append(reponse.headers.get("ETag", "").strip('"'))
    return etags


def _finaliser_upload_video(access_token: str, urn_video: str, jeton_televersement: str, etags: list[str]):
    reponse = requests.post(
        f"{URL_VIDEOS}?action=finalizeUpload",
        json={"finalizeUploadRequest": {"video": urn_video, "uploadToken": jeton_televersement, "uploadedPartIds": etags}},
        headers=_entetes(access_token), timeout=30,
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de la finalisation de l'upload video LinkedIn (code {reponse.status_code}) : {reponse.text}")


def _attendre_video_prete(access_token: str, urn_video: str) -> None:
    """LinkedIn traite la video apres finalizeUpload (encodage) : on interroge son statut jusqu'a AVAILABLE avant de creer le post, sous peine d'une video invisible/cassee cote LinkedIn."""
    url = f"{URL_VIDEOS}/{urn_video.replace(':', '%3A')}"
    for _ in range(TENTATIVES_MAX_VIDEO):
        reponse = requests.get(url, headers=_entetes(access_token), timeout=15)
        if reponse.status_code == 200:
            statut = reponse.json().get("status")
            if statut == "AVAILABLE":
                return
            if statut == "PROCESSING_FAILED":
                raise RuntimeError("Echec du traitement de la video LinkedIn (statut PROCESSING_FAILED).")
        time.sleep(ATTENTE_VIDEO_SECONDES)
    raise RuntimeError("La video LinkedIn n'est pas devenue disponible a temps.")


def publier_post(
    access_token: str, identifiant_membre: str, texte: str, octets_image: bytes = None, octets_video: bytes = None,
) -> str:
    """Publie un post (texte, avec image ou video optionnelle - mutuellement exclusives) sur le profil du membre. Renvoie l'URN du post cree."""
    corps = {
        "author": f"urn:li:person:{identifiant_membre}",
        "commentary": texte,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if octets_video:
        instructions, urn_video, jeton_televersement = _initialiser_upload_video(access_token, identifiant_membre, len(octets_video))
        etags = _televerser_video(instructions, octets_video)
        _finaliser_upload_video(access_token, urn_video, jeton_televersement, etags)
        _attendre_video_prete(access_token, urn_video)
        corps["content"] = {"media": {"id": urn_video}}
    elif octets_image:
        url_televersement, urn_image = _initialiser_upload_image(access_token, identifiant_membre)
        _televerser_image(url_televersement, octets_image)
        corps["content"] = {"media": {"id": urn_image}}

    reponse = requests.post(URL_POSTS, json=corps, headers=_entetes(access_token), timeout=30)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Echec de la publication LinkedIn (code {reponse.status_code}) : {reponse.text}")

    return reponse.headers.get("x-restli-id", "")
