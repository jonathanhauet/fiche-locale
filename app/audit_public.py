"""
Score gratuit de la page publique d'audit (/audit-gratuit, voir main.py) -
outil de capture de leads pour le site vitrine de l'agence, distinct de
l'audit prospect interne (audit_prospect.py) qui teste une grille de
positions payante. Ici, un seul appel DataForSEO (recherche Maps publique,
reutilise audit_prospect._rechercher_maps) suffit a construire un score de
completude de la fiche - regles simples, pas d'IA, pour rester gratuit a
chaque soumission du formulaire public.
"""

from .audit_prospect import _item_vers_fiche, _rechercher_maps
from .rank_tracking import _score_correspondance, _mots

SEUIL_CORRESPONDANCE_PUBLIC = 0.5


def _meilleur_item(nom_entreprise: str, ville: str) -> dict | None:
    resultats = _rechercher_maps(nom_entreprise, ville)
    mots_mot_cle = _mots(f"{nom_entreprise} {ville}")

    meilleur, meilleur_score = None, 0.0
    for item in resultats:
        score = _score_correspondance(item.get("title", ""), nom_entreprise, mots_mot_cle)
        if score > meilleur_score:
            meilleur, meilleur_score = item, score

    if meilleur is None or meilleur_score < SEUIL_CORRESPONDANCE_PUBLIC:
        return None
    return meilleur


def calculer_score_public(nom_entreprise: str, ville: str) -> dict:
    """
    Renvoie {"trouve": bool, "fiche": {...}, "score": int, "facteurs": [...]}
    ou "facteurs" est une liste ordonnee (du plus prioritaire au moins
    prioritaire a corriger) de {"libelle", "detail", "niveau", "points",
    "points_max"} - niveau parmi "haute", "moyenne", "bon".
    """
    item = _meilleur_item(nom_entreprise, ville)
    if item is None:
        return {"trouve": False, "fiche": None, "score": None, "facteurs": []}

    fiche = _item_vers_fiche(item)
    total_photos = item.get("total_photos") or 0
    nb_avis = fiche.get("nombre_avis") or 0
    note = fiche.get("note") or 0
    a_site_web = bool(fiche.get("site_web"))
    a_telephone = bool(fiche.get("telephone"))
    a_horaires = bool(item.get("work_hours"))
    a_categorie_secondaire = bool(item.get("additional_categories"))

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

    return {"trouve": True, "fiche": fiche, "score": score, "facteurs": facteurs}
