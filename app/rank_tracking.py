"""
Carte de positions par mots-cles (grille geographique), equivalent d'un outil
comme Localo. Google ne propose aucune API officielle pour ca : on passe par
DataForSEO (API tierce payante, compte a creer separement sur dataforseo.com).

Le format exact des reponses DataForSEO n'a pas pu etre verifie contre un
compte reel au moment de l'ecriture (pas d'identifiants disponibles) : le
parsing est fait de facon defensive (.get() partout) et a valider des que
des identifiants DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD sont configures.
"""

import difflib
import math
import os
import re

import requests
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD")

URL_MAPS_LIVE = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"

KM_PAR_DEGRE_LAT = 111.32


def identifiants_configures() -> bool:
    return bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)


def generer_points_grille(lat_centre: float, lng_centre: float, taille: int = 5, rayon_km: float = 2.0):
    """Renvoie une liste de (latitude, longitude) formant une grille carree taille x taille centree sur le point donne."""
    km_par_degre_lng = KM_PAR_DEGRE_LAT * math.cos(math.radians(lat_centre)) or 1
    demi = taille // 2
    points = []
    for i in range(-demi, demi + 1):
        for j in range(-demi, demi + 1):
            lat = lat_centre + (i * rayon_km) / KM_PAR_DEGRE_LAT
            lng = lng_centre + (j * rayon_km) / km_par_degre_lng
            points.append((lat, lng))
    return points


SEUIL_CORRESPONDANCE = 0.5


def _mots(texte: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (texte or "").lower()))


def _score_correspondance(nom_resultat: str, nom_entreprise: str, mots_mot_cle: set) -> float:
    """
    Score de correspondance entre le nom d'un resultat DataForSEO et le nom de
    la fiche du client (0 a 1). Google Business Profile et DataForSEO n'utilisent
    pas le meme systeme d'identifiant : impossible de rapprocher a 100% de facon fiable.

    La comparaison brute caractere-par-caractere (SequenceMatcher) se fait avoir
    quand deux noms de concurrents differents partagent un long suffixe generique
    issu du mot-cle recherche (ex. "Top Serrurerie - Ath" vs "Nico Serrurerie -
    Ath" : les deux se ressemblent beaucoup caractere par caractere alors que ce
    sont deux entreprises distinctes). On compare donc d'abord les MOTS
    significatifs des deux noms, en ignorant ceux qui viennent du mot-cle
    lui-meme (partages par presque tous les concurrents, donc non discriminants).
    """
    a = (nom_resultat or "").strip().lower()
    b = (nom_entreprise or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95

    mots_resultat = _mots(nom_resultat) - mots_mot_cle
    mots_entreprise = _mots(nom_entreprise) - mots_mot_cle
    if mots_resultat and mots_entreprise:
        recouvrement = len(mots_resultat & mots_entreprise) / len(mots_entreprise)
        if recouvrement > 0:
            return 0.5 + 0.4 * recouvrement

    # Repli : similarite brute de caracteres, peu fiable seule, donc plafonnee
    # pour ne jamais l'emporter sur une correspondance par mots.
    return difflib.SequenceMatcher(None, a, b).ratio() * 0.5


def verifier_position(mot_cle: str, latitude: float, longitude: float, nom_entreprise: str):
    """
    Interroge DataForSEO pour un point donne. Renvoie (position, nom_correspondance, classement) :
    - position est None si l'entreprise n'apparait pas dans les resultats renvoyes
      (donc hors classement visible a cet endroit) ;
    - classement est le top 10 des resultats a ce point (independant du fait que
      l'entreprise y figure ou non), sous la forme [{"position": int, "nom": str}, ...].
    """
    if not identifiants_configures():
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env.")

    corps = [{
        "keyword": mot_cle,
        "location_coordinate": f"{latitude},{longitude},14z",
        "language_code": "fr",
        "device": "desktop",
    }]

    try:
        reponse = requests.post(
            URL_MAPS_LIVE, auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=corps, timeout=30,
        )
    except requests.RequestException as erreur:
        raise RuntimeError(f"Erreur reseau vers DataForSEO : {erreur}") from erreur

    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de l'appel DataForSEO (code {reponse.status_code}) : {reponse.text}")

    donnees = reponse.json()
    taches = donnees.get("tasks") or []
    if not taches or taches[0].get("status_code") != 20000:
        message = taches[0].get("status_message") if taches else "reponse vide"
        raise RuntimeError(f"Erreur DataForSEO : {message}")

    resultats = (taches[0].get("result") or [{}])[0].get("items") or []
    resultats_classes = sorted(
        resultats, key=lambda item: item.get("rank_absolute") or item.get("rank_group") or 9999
    )

    classement = [
        {"position": item.get("rank_absolute") or item.get("rank_group"), "nom": item.get("title", "")}
        for item in resultats_classes[:10]
    ]

    mots_mot_cle = _mots(mot_cle)
    meilleur_item = None
    meilleur_score = 0.0
    for item in resultats_classes:
        score = _score_correspondance(item.get("title", ""), nom_entreprise, mots_mot_cle)
        if score > meilleur_score:
            meilleur_score = score
            meilleur_item = item

    if meilleur_item is not None and meilleur_score >= SEUIL_CORRESPONDANCE:
        position = meilleur_item.get("rank_absolute") or meilleur_item.get("rank_group")
        return position, meilleur_item.get("title", ""), classement

    return None, "", classement


def resumer_releve(points: list) -> dict:
    """Petit resume statistique d'un releve termine, pour l'affichage."""
    verifies = [p for p in points if p.verifie]
    trouves = [p for p in verifies if p.position is not None]
    return {
        "total": len(points),
        "verifies": len(verifies),
        "top3": len([p for p in trouves if p.position <= 3]),
        "top10": len([p for p in trouves if p.position <= 10]),
        "non_trouves": len(verifies) - len(trouves),
    }
