"""
Modeles de style du montage video : un jeu de reglages (sous-titres animes, zooms, bruitages, transitions...) applique en un clic. Quatre
modeles prets a l'emploi, plus les modeles enregistres par l'utilisateur (models.StyleMontage).
"""

import json

from . import models

TRANSITIONS_VALIDES = ("aucune", "melange", "flash", "zoom", "glitch", "secousse")
CLES_BOOLEENNES = ("zooms", "mots", "accroche", "barre", "visage", "bruitages", "alterne", "pastille")

STYLES_PREDEFINIS = [
    {"id": "dynamique", "nom": "Dynamique", "description": "Zooms, sous-titres animés, bruitages et transitions variées : le style réseaux sociaux complet.",
     "reglages": {"style": "tendance", "zooms": True, "mots": True, "accroche": True, "barre": True, "visage": True, "bruitages": True, "transition": "melange", "alterne": False, "pastille": True}},
    {"id": "punchy", "nom": "Punchy", "description": "Cadrage alterné à chaque coupe, flash sur les jonctions et bruitages : très rythmé.",
     "reglages": {"style": "tendance", "zooms": False, "mots": True, "accroche": True, "barre": True, "visage": True, "bruitages": True, "transition": "flash", "alterne": True, "pastille": True}},
    {"id": "storytelling", "nom": "Storytelling", "description": "Zooms doux et sous-titres animés, sans bruitage ni transition : posé, pour raconter.",
     "reglages": {"style": "tendance", "zooms": True, "mots": True, "accroche": True, "barre": False, "visage": True, "bruitages": False, "transition": "aucune", "alterne": False, "pastille": True}},
    {"id": "sobre_pro", "nom": "Sobre pro", "description": "Sous-titres classiques, aucun effet : pour une vidéo institutionnelle.",
     "reglages": {"style": "sobre", "zooms": False, "mots": False, "accroche": False, "barre": False, "visage": True, "bruitages": False, "transition": "aucune", "alterne": False, "pastille": False}},
]


def nettoyer_reglages(brut) -> dict:
    """Ne garde que les reglages connus, avec le bon type (jamais de valeur libre venue du navigateur)."""
    brut = brut if isinstance(brut, dict) else {}
    propre = {cle: bool(brut[cle]) for cle in CLES_BOOLEENNES if cle in brut}
    if brut.get("style") in ("tendance", "sobre"):
        propre["style"] = brut["style"]
    if brut.get("transition") in TRANSITIONS_VALIDES:
        propre["transition"] = brut["transition"]
    return propre


def lister(db) -> list:
    """Modeles prets a l'emploi puis modeles enregistres : [{"id", "nom", "description", "reglages", "perso"}]."""
    liste = [{**style, "perso": False} for style in STYLES_PREDEFINIS]
    for ligne in db.query(models.StyleMontage).order_by(models.StyleMontage.nom).all():
        try:
            reglages = nettoyer_reglages(json.loads(ligne.reglages_json or "{}"))
        except ValueError:
            reglages = {}
        liste.append({"id": f"perso-{ligne.id}", "nom": ligne.nom, "description": "Votre style enregistré.", "reglages": reglages, "perso": True})
    return liste


def enregistrer(db, nom: str, reglages: dict):
    """Cree ou met a jour (meme nom) un modele personnel."""
    nom = " ".join(str(nom or "").split())[:60]
    if not nom:
        raise ValueError("Donnez un nom à ce style.")
    if nom.lower() in {s["nom"].lower() for s in STYLES_PREDEFINIS}:
        raise ValueError("Ce nom est déjà celui d'un modèle prêt à l'emploi : choisissez-en un autre.")
    ligne = db.query(models.StyleMontage).filter(models.StyleMontage.nom == nom).first() or models.StyleMontage(nom=nom)
    ligne.reglages_json = json.dumps(nettoyer_reglages(reglages))
    db.add(ligne)
    db.commit()
    return ligne


def supprimer(db, identifiant: str) -> bool:
    if not identifiant.startswith("perso-") or not identifiant[6:].isdigit():
        return False
    ligne = db.get(models.StyleMontage, int(identifiant[6:]))
    if not ligne:
        return False
    db.delete(ligne)
    db.commit()
    return True
