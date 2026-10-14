"""
Copier le style d'une video de reference : on mesure son rythme (nombre de coupes, duree moyenne d'un plan) avec ffmpeg, on en extrait quelques images
que Claude decrit (sous-titres, pastilles, cartes, barre, palette...), puis le tout est traduit en reglages du montage et en couleurs de charte.
"""

import os
import re
import tempfile

from . import charte_montage
from . import montage_parole as mp

DUREE_MAX = 180
TAILLE_MAX = 150 * 1024 * 1024
EXTENSIONS = {"mp4", "mov", "m4v", "webm", "mkv"}
SEUIL_SCENE = 0.30
NB_IMAGES = 6


class ErreurStyle(RuntimeError):
    pass


def detecter_coupes(chemin: str) -> list:
    """Instants (secondes) des changements de plan, detectes par ffmpeg (difference entre images successives)."""
    sortie = mp._lancer([mp.FFMPEG, "-hide_banner", "-i", chemin, "-an", "-vf", f"select='gt(scene,{SEUIL_SCENE})',showinfo", "-f", "null", "-"], 600).stderr
    return sorted({round(float(t), 2) for t in re.findall(r"pts_time:(\d+(?:\.\d+)?)", sortie)})


def extraire_images(chemin: str, duree: float, dossier: str) -> list:
    """Quelques images JPEG (octets) reparties sur toute la video, pour que Claude en decrive le style."""
    images = []
    for rang in range(NB_IMAGES):
        fichier = os.path.join(dossier, f"ref-{rang}.jpg")
        instant = duree * (0.08 + 0.84 * rang / (NB_IMAGES - 1))
        resultat = mp._lancer([mp.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{instant:.2f}", "-i", chemin, "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", fichier], 120)
        if resultat.returncode == 0 and os.path.exists(fichier):
            with open(fichier, "rb") as f:
                images.append(f.read())
    return images


def reglages_depuis_rythme(plan_moyen: float) -> dict:
    """Plus les plans sont courts, plus le montage est nerveux : coupes alternees, transitions et bruitages."""
    if plan_moyen < 1.5:
        return {"zooms": False, "alterne": True, "transition": "melange", "bruitages": True}
    if plan_moyen < 3.0:
        return {"zooms": True, "alterne": False, "transition": "flash", "bruitages": True}
    if plan_moyen < 6.0:
        return {"zooms": True, "alterne": False, "transition": "aucune", "bruitages": False}
    return {"zooms": False, "alterne": False, "transition": "aucune", "bruitages": False}


def analyser(chemin: str, decrire) -> dict:
    """
    Style d'une video de reference. decrire(images, rythme) -> description de Claude (voir claude_generation.decrire_style_reference).
    Renvoie {"rythme", "reglages", "charte", "resume"} ; reglages et charte s'appliquent tels quels (styles_montage, charte_montage).
    """
    infos = mp.sonde(chemin)
    if infos["duree"] > DUREE_MAX:
        raise ErreurStyle(f"Vidéo trop longue pour servir de référence ({DUREE_MAX // 60} minutes au maximum).")
    if infos["duree"] < 3:
        raise ErreurStyle("Vidéo trop courte pour servir de référence.")
    coupes = detecter_coupes(chemin)
    rythme = {"nb_coupes": len(coupes), "duree_moyenne_plan": round(infos["duree"] / (len(coupes) + 1), 2), "duree": round(infos["duree"], 1)}
    with tempfile.TemporaryDirectory() as dossier:
        images = extraire_images(chemin, infos["duree"], dossier)
    analyse = decrire(images, rythme)
    reglages = reglages_depuis_rythme(rythme["duree_moyenne_plan"])
    animes = analyse.get("sous_titres") == "animes"
    reglages.update({
        "mots": animes, "pastille": bool(analyse.get("pastille")) and animes, "barre": bool(analyse.get("barre_progression")), "accroche": bool(analyse.get("titre_accroche")),
        "visage": True, "style": "tendance" if (animes or analyse.get("cartes") or rythme["duree_moyenne_plan"] < 6) else "sobre",
    })
    if analyse.get("bruitages_probables"):
        reglages["bruitages"] = True
    charte = charte_montage.nettoyer({"principale": analyse.get("couleur_principale"), "secondaire": analyse.get("couleur_accent"), "police": analyse.get("police")})
    return {"rythme": rythme, "reglages": reglages, "charte": {k: charte[k] for k in ("principale", "secondaire", "police")}, "resume": str(analyse.get("resume") or "")[:400],
            "cartes": bool(analyse.get("cartes"))}
