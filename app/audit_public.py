"""
Score gratuit de la page publique d'audit (/audit-gratuit, voir main.py) -
outil de capture de leads pour le site vitrine de l'agence, distinct de
l'audit prospect interne (audit_prospect.py) qui teste une grille de
positions payante. Ici, un seul appel DataForSEO (recherche Maps publique,
reutilise audit_prospect._rechercher_maps) suffit, quel que soit le nombre
de candidats presentes a l'utilisateur pour confirmation - le score est
calcule a partir des donnees deja recuperees, sans second appel.
"""

from .audit_prospect import _item_vers_fiche, _rechercher_maps
from .rank_tracking import _score_correspondance, _mots

SEUIL_CORRESPONDANCE_PUBLIC = 0.3
MAX_CANDIDATS_PUBLIC = 5


def rechercher_candidats_public(nom_entreprise: str, ville: str) -> list[dict]:
    """
    Renvoie jusqu'a MAX_CANDIDATS_PUBLIC candidats, avec tous les champs
    necessaires a la fois pour l'affichage ET le calcul du score (voir
    calculer_score_depuis_candidat) - un seul appel DataForSEO, meme si
    l'utilisateur doit ensuite choisir parmi plusieurs fiches.
    """
    resultats = _rechercher_maps(nom_entreprise, ville)
    mots_mot_cle = _mots(f"{nom_entreprise} {ville}")

    resultats_notes = [
        (item, _score_correspondance(item.get("title", ""), nom_entreprise, mots_mot_cle))
        for item in resultats
    ]
    resultats_notes.sort(key=lambda paire: paire[1], reverse=True)

    candidats = []
    for item, score in resultats_notes[:MAX_CANDIDATS_PUBLIC]:
        if score <= 0:
            continue
        fiche = _item_vers_fiche(item)
        candidats.append({
            **fiche,
            "total_photos": item.get("total_photos") or 0,
            "a_horaires": bool(item.get("work_hours")),
            "a_categorie_secondaire": bool(item.get("additional_categories")),
        })
    return candidats


def calculer_score_depuis_candidat(candidat: dict) -> dict:
    """
    Calcule le score de completude a partir d'un candidat deja recupere (voir
    rechercher_candidats_public) - aucun appel reseau ici. Renvoie
    {"fiche": {...}, "score": int, "facteurs": [...]} ou "facteurs" est une
    liste triee (du plus prioritaire au moins prioritaire a corriger) de
    {"libelle", "detail", "niveau", "points", "points_max"}.
    """
    total_photos = candidat.get("total_photos") or 0
    nb_avis = candidat.get("nombre_avis") or 0
    note = candidat.get("note") or 0
    a_site_web = bool(candidat.get("site_web"))
    a_telephone = bool(candidat.get("telephone"))
    a_horaires = bool(candidat.get("a_horaires"))
    a_categorie_secondaire = bool(candidat.get("a_categorie_secondaire"))

    facteurs = []

    if total_photos >= 20:
        pts, niveau, detail = 20, "bon", f"{total_photos} photos sur la fiche"
    elif total_photos >= 5:
        pts, niveau, detail = 10, "moyenne", f"Seulement {total_photos} photos"
    else:
        pts, niveau, detail = 0, "haute", f"{total_photos} photo(s) seulement"
    facteurs.append({"libelle": "Photos", "detail": detail, "niveau": niveau, "points": pts, "points_max": 20})

    if nb_avis >= 50:
        pts, niveau, detail = 20, "bon", f"{nb_avis} avis"
    elif nb_avis >= 10:
        pts, niveau, detail = 12, "moyenne", f"{nb_avis} avis, peut progresser"
    elif nb_avis >= 1:
        pts, niveau, detail = 5, "haute", f"Seulement {nb_avis} avis"
    else:
        pts, niveau, detail = 0, "haute", "Aucun avis"
    facteurs.append({"libelle": "Nombre d'avis", "detail": detail, "niveau": niveau, "points": pts, "points_max": 20})

    if note >= 4.5:
        pts, niveau, detail = 15, "bon", f"Note de {note}/5"
    elif note >= 4.0:
        pts, niveau, detail = 10, "moyenne", f"Note de {note}/5"
    elif note > 0:
        pts, niveau, detail = 5, "haute", f"Note de {note}/5, a ameliorer"
    else:
        pts, niveau, detail = 0, "haute", "Pas de note"
    facteurs.append({"libelle": "Note moyenne", "detail": detail, "niveau": niveau, "points": pts, "points_max": 15})

    facteurs.append({
        "libelle": "Site web", "points_max": 15, "points": 15 if a_site_web else 0,
        "niveau": "bon" if a_site_web else "haute",
        "detail": "Site web renseigne" if a_site_web else "Aucun site web renseigne",
    })
    facteurs.append({
        "libelle": "Telephone", "points_max": 10, "points": 10 if a_telephone else 0,
        "niveau": "bon" if a_telephone else "haute",
        "detail": "Telephone renseigne" if a_telephone else "Aucun telephone renseigne",
    })
    facteurs.append({
        "libelle": "Horaires", "points_max": 10, "points": 10 if a_horaires else 0,
        "niveau": "bon" if a_horaires else "moyenne",
        "detail": "Horaires renseignes" if a_horaires else "Horaires non renseignes",
    })
    facteurs.append({
        "libelle": "Categorie secondaire", "points_max": 10, "points": 10 if a_categorie_secondaire else 0,
        "niveau": "bon" if a_categorie_secondaire else "moyenne",
        "detail": "Categorie(s) secondaire(s) presente(s)" if a_categorie_secondaire else "Aucune categorie secondaire",
    })

    score = sum(f["points"] for f in facteurs)
    ordre_niveau = {"haute": 0, "moyenne": 1, "bon": 2}
    facteurs.sort(key=lambda f: ordre_niveau[f["niveau"]])

    return {"fiche": candidat, "score": score, "facteurs": facteurs}
