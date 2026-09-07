"""
Audit ponctuel d'une entreprise qui n'est PAS cliente (prospection a froid) -
inspire des rapports automatises que certains concurrents envoient en
demarchage. Contrairement au reste de la plateforme, aucune donnee ne vient
de l'API Google Business Profile authentifiee (impossible sans autorisation
du proprietaire) : tout est reconstitue a partir de donnees publiques via
DataForSEO (meme compte que rank_tracking.py/citations.py), deja utilise
pour le suivi de position.

Consequence : on ne peut pas reproduire les sections qui necessitent un
acces autorise a la fiche (photos, historique des posts, attributs
detailles...) - seules la fiche publique (note, avis, coordonnees) et la
grille de positions/concurrents sont couvertes dans cette premiere version.
"""

from . import rank_tracking
from .rank_tracking import (
    DATAFORSEO_LOGIN,
    DATAFORSEO_PASSWORD,
    URL_MAPS_LIVE,
    _score_correspondance,
    generer_points_grille,
    identifiants_configures,
    verifier_position,
)

import requests

TAILLE_GRILLE_DEFAUT = 3
RAYON_KM_DEFAUT = 2.0
MAX_CONCURRENTS_AFFICHES = 5


def rechercher_fiche_publique(nom_entreprise: str, ville: str) -> dict:
    """
    Retrouve la fiche publique d'une entreprise (nom + ville) via une
    recherche Google Maps grand public - aucune authentification necessaire,
    ca fonctionne donc pour une entreprise qui n'est pas cliente. Renvoie
    {"trouve": bool, "titre", "note", "nombre_avis", "adresse", "telephone",
    "categorie", "site_web", "latitude", "longitude"}.
    """
    if not identifiants_configures():
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env.")

    requete = f"{nom_entreprise} {ville}".strip()
    corps = [{"keyword": requete, "location_name": "France", "language_code": "fr", "device": "desktop"}]

    try:
        reponse = requests.post(URL_MAPS_LIVE, auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=corps, timeout=30)
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
    mots_mot_cle = rank_tracking._mots(requete)

    meilleur, meilleur_score = None, 0.0
    for item in resultats:
        score = _score_correspondance(item.get("title", ""), nom_entreprise, mots_mot_cle)
        if score > meilleur_score:
            meilleur, meilleur_score = item, score

    if meilleur is None or meilleur_score < rank_tracking.SEUIL_CORRESPONDANCE:
        return {"trouve": False}

    note = meilleur.get("rating") or {}
    coordonnees = meilleur.get("coordinates") or {}
    return {
        "trouve": True,
        "titre": meilleur.get("title", ""),
        "note": note.get("value"),
        "nombre_avis": note.get("votes_count"),
        "adresse": meilleur.get("address", ""),
        "telephone": meilleur.get("phone", ""),
        "categorie": meilleur.get("category", ""),
        "site_web": meilleur.get("url", "") or meilleur.get("domain", ""),
        "latitude": coordonnees.get("latitude"),
        "longitude": coordonnees.get("longitude"),
    }


def grille_positions_prospect(
    nom_entreprise: str, mot_cle: str, latitude: float, longitude: float,
    taille_grille: int = TAILLE_GRILLE_DEFAUT, rayon_km: float = RAYON_KM_DEFAUT,
) -> dict:
    """
    Releve de positions pour un mot-cle donne, sans passer par les tables
    ReleveDePosition/PointDeGrille (reservees aux clients) : tout se fait en
    memoire pour ce seul appel, le resultat n'est pas conserve en base.
    Renvoie {"points": [...], "resume": {...}, "concurrents": [...]}.
    """
    points_coords = generer_points_grille(latitude, longitude, taille_grille, rayon_km)
    points = []
    concurrents_par_nom = {}

    for lat, lng in points_coords:
        position, nom_correspondance, classement = verifier_position(mot_cle, lat, lng, nom_entreprise)
        points.append({"latitude": lat, "longitude": lng, "position": position})
        for item in classement:
            nom = item.get("nom", "")
            if not nom or _score_correspondance(nom, nom_entreprise, rank_tracking._mots(mot_cle)) >= rank_tracking.SEUIL_CORRESPONDANCE:
                continue  # c'est l'entreprise elle-meme, pas un concurrent
            entree = concurrents_par_nom.setdefault(nom, {"nom": nom, "positions": []})
            entree["positions"].append(item.get("position") or 9999)

    trouves = [p["position"] for p in points if p["position"] is not None]
    resume = {
        "total_points": len(points),
        "points_trouves": len(trouves),
        "pourcentage_couverture": round(len(trouves) / len(points) * 100) if points else 0,
        "position_moyenne": round(sum(trouves) / len(trouves), 1) if trouves else None,
    }

    concurrents = sorted(
        (
            {"nom": c["nom"], "position_moyenne": round(sum(c["positions"]) / len(c["positions"]), 1),
             "presence": round(len(c["positions"]) / len(points) * 100) if points else 0}
            for c in concurrents_par_nom.values()
        ),
        key=lambda c: c["position_moyenne"],
    )[:MAX_CONCURRENTS_AFFICHES]

    return {"mot_cle": mot_cle, "points": points, "resume": resume, "concurrents": concurrents}
