"""
Charte graphique du montage video : couleurs, police et arrondis communs aux pastilles des sous-titres et aux cartes animees (voir
cartes_montage.py), reglees une fois pour toutes. Une seule ligne en base (models.CharteMontage) ; chaque montage en garde une copie.
"""

import json
import re

from . import models

DEFAUT = {"principale": "#2f5eff", "secondaire": "#ffd60a", "texte": "#1a1d23", "fond": "#ffffff", "police": "Poppins", "arrondi": "moyen"}
POLICES = {"Poppins": "Poppins-Bold.ttf", "Bebas Neue": "BebasNeue-Regular.ttf", "DM Serif Display": "DMSerifDisplay-Regular.ttf", "Kalam": "Kalam-Bold.ttf"}
ARRONDIS = {"petit": 0.025, "moyen": 0.05, "grand": 0.09}          # rayon des coins, en part de la largeur d'une carte
_COULEUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def nettoyer(brut) -> dict:
    """Charte complete et valide : les valeurs inconnues ou mal formees reprennent la valeur par defaut."""
    brut = brut if isinstance(brut, dict) else {}
    charte = dict(DEFAUT)
    for cle in ("principale", "secondaire", "texte", "fond"):
        if isinstance(brut.get(cle), str) and _COULEUR.match(brut[cle].strip()):
            charte[cle] = brut[cle].strip().lower()
    if brut.get("police") in POLICES:
        charte["police"] = brut["police"]
    if brut.get("arrondi") in ARRONDIS:
        charte["arrondi"] = brut["arrondi"]
    return charte


def rgb(couleur: str) -> tuple:
    return tuple(int(couleur[i:i + 2], 16) for i in (1, 3, 5))


def lire(db) -> dict:
    ligne = db.query(models.CharteMontage).first()
    try:
        return nettoyer(json.loads(ligne.valeurs_json)) if ligne else dict(DEFAUT)
    except ValueError:
        return dict(DEFAUT)


def enregistrer(db, brut) -> dict:
    charte = nettoyer(brut)
    ligne = db.query(models.CharteMontage).first() or models.CharteMontage()
    ligne.valeurs_json = json.dumps(charte)
    db.add(ligne)
    db.commit()
    return charte
