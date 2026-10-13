"""
Editeur de montage (page « Editeur ») : toutes les modifications se font sur l'etat d'un montage deja produit (segments gardes,
effets, transcription, illustrations) en temps de la video d'origine, puis un seul rendu final les applique (voir
montage_parole._rendre_sortie). L'apercu dans le navigateur utilise une copie legere de la video (apercu.mp4) et rejoue les coupes
et les effets cote page, sans rendu.

Rien n'est conserve durablement : l'etat vit en memoire avec le montage (voir montage_parole.MONTAGES).
"""

import copy
import os
import re
import subprocess
import threading
import uuid
from io import BytesIO

from PIL import Image

from . import montage_parole as mp

EXTENSIONS_IMAGE = {"png", "jpg", "jpeg", "webp"}
TAILLE_MAX_IMAGE = 15 * 1024 * 1024
NB_ILLUSTRATIONS_MAX = 40
NB_ANNULATIONS_MAX = 30
REGLAGES_ACTIFS = ("zooms", "mots", "accroche", "barre")
MODES_ILLUSTRATION = ("incrust", "plein")
SNAP_SILENCE = 0.4          # un bord de coupe a moins de 0,4 s d'un silence est cale dedans
SNAP_MARGE = 0.1


class ErreurEditeur(RuntimeError):
    pass


# --------------------------------------------------------------------------- lecture de l'etat

def mots_de_phrase(phrase: dict) -> list:
    """Mots d'une phrase avec leur instant estime (duree repartie selon la longueur des mots, comme pour les sous-titres)."""
    mots = phrase["texte"].split()
    if not mots:
        return []
    poids = [len(m) + 1 for m in mots]
    total, duree = sum(poids), max(0.05, phrase["fin"] - phrase["debut"])
    resultat, cumul = [], 0.0
    for mot, poids_mot in zip(mots, poids):
        debut = phrase["debut"] + duree * cumul / total
        cumul += poids_mot
        resultat.append({"texte": mot, "debut": round(debut, 3), "fin": round(phrase["debut"] + duree * cumul / total, 3)})
    return resultat


def _vers_source(zooms_montage: list, segments: list) -> list:
    """Fenetres de zoom du montage -> fenetres en temps de la video d'origine (pour l'apercu)."""
    resultat = []
    for fenetre in zooms_montage:
        a, b, amplitude = fenetre[:3]
        resultat.append({"debut": round(mp.montage_vers_source(a, segments), 3), "fin": round(mp.montage_vers_source(b, segments, True), 3),
                         "amplitude": amplitude, "mode": fenetre[3] if len(fenetre) > 3 else "doux"})
    return resultat


def etat_editeur(identifiant: str, montage: dict, sortie: dict) -> dict:
    """Tout ce dont la page a besoin : transcription par mots, parties gardees, effets et illustrations, en temps de la video d'origine."""
    segments, effets = sortie["segments"], sortie["effets"]
    duree = mp.duree_montee(segments)
    phrases_montage = mp.phrases_sur_montage(sortie["phrases"], segments)
    zooms = _vers_source(mp.zooms_du_rendu(sortie, phrases_montage, duree), segments)
    for zoom in zooms:      # un zoom ajoute a la main : meme amplitude et fenetre qui recouvre la sienne (ses bords peuvent bouger avec les coupes)
        zoom["manuel"] = zoom["mode"] == "doux" and any(abs(zoom["amplitude"] - amp) < 1e-6 and zoom["debut"] < f + 0.01 and zoom["fin"] > d - 0.01
                                                         for d, f, amp in effets["zoom_manuel"])
    return {
        "id": identifiant, "numero": sortie["numero"], "titre": sortie["titre"], "version": sortie["version"], "format": sortie["format"],
        "visage_x": sortie.get("visage_x"),
        "duree_source": round(montage["infos"]["duree"], 3), "duree_montee": round(duree, 3),
        "segments": [[round(a, 3), round(b, 3)] for a, b in segments],
        "silences": [[round(a, 3), round(b, 3)] for a, b in (montage.get("silences") or [])],
        "phrases": [{"indice": i, "debut": p["debut"], "fin": p["fin"], "texte": p["texte"], "mots": mots_de_phrase(p)} for i, p in enumerate(sortie["phrases"])],
        "zooms": zooms,
        "titres": [{"indice": i, "debut": d, "fin": f, "texte": t} for i, (d, f, t) in enumerate(effets["titres"])],
        "illustrations": [{"id": ill["id"], "debut": ill["debut"], "fin": ill["fin"], "mode": ill["mode"], "origine": ill.get("origine", ""),
                           "prompt": ill.get("prompt", "")} for ill in effets.get("illustrations", [])],
        "mots_gros": sorted(set(effets["mots_gros"]) | {mp._normaliser(m) for m in effets["mots_cles"]}),
        "reglages": {**effets["actif"], "alterne": bool(effets.get("alterne")), "sous_titres": bool(sortie["sous_titres"]), "style": effets["style"]},
        "accroche": effets["titre"], "peut_annuler": bool(sortie.get("annulations")), "peut_revenir": bool(sortie["historique"]),
        "apercu": montage.get("apercu", {"etat": "absent"}) | {"progression": montage.get("apercu", {}).get("progression", 0.0)},
    }


# --------------------------------------------------------------------------- operations

def _fusionner(intervalles: list) -> list:
    return mp._fusionner(intervalles)


def affiner_borne(instant: float, silences: list, debut_de_coupe: bool) -> float:
    """
    Cale un bord de coupe dans le silence voisin (a moins de SNAP_SILENCE) pour ne pas couper un mot en deux : le debut d'une plage
    retiree va au debut du silence qui suit la derniere parole gardee, la fin au bout du silence qui precede la parole suivante.
    """
    meilleur, ecart = instant, None
    for s_debut, s_fin in silences:
        if s_fin - s_debut < 0.25:
            continue
        if debut_de_coupe and s_debut - SNAP_SILENCE <= instant <= s_fin:
            cible = min(max(instant, s_debut + SNAP_MARGE), s_fin - SNAP_MARGE)
        elif not debut_de_coupe and s_debut <= instant <= s_fin + SNAP_SILENCE:
            cible = max(min(instant, s_fin - SNAP_MARGE), s_debut + SNAP_MARGE)
        else:
            continue
        if ecart is None or abs(cible - instant) < ecart:
            meilleur, ecart = cible, abs(cible - instant)
    return meilleur


def dans_le_montage(instant: float, segments: list) -> float:
    """Un effet pose dans une partie coupee serait invisible dans la video : il est ramene au debut de la partie gardee suivante."""
    if any(a - 0.02 <= instant < b for a, b in segments):
        return instant
    suivant = next((a for a, _ in segments if a > instant), None)
    return suivant if suivant is not None else max(0.0, segments[-1][1] - 1.0)


def _instantane(sortie: dict) -> dict:
    return {"segments": list(sortie["segments"]), "effets": copy.deepcopy(sortie["effets"]), "phrases": copy.deepcopy(sortie["phrases"]),
            "sous_titres": sortie["sous_titres"]}


def annuler(sortie: dict) -> bool:
    """Annule la derniere serie de modifications de l'editeur (pas encore rendues) ; False s'il n'y en a pas."""
    pile = sortie.get("annulations") or []
    if not pile:
        return False
    sortie.update(pile.pop())
    return True


def _normaliser_mots(texte: str) -> list:
    return [m for m in (mp._normaliser(x) for x in str(texte or "").split()) if m]


def appliquer_operations(montage: dict, sortie: dict, operations: list) -> tuple:
    """
    Applique une serie d'operations (dicts, temps en secondes de la video d'origine) a l'etat de la sortie. Renvoie (resume, refus).
    Si rien n'a pu etre applique, l'etat reste intact. Operations : couper, restaurer, zoom, retirer_zoom, titre, retirer_titre,
    mot_en_gros, retirer_mot_en_gros, corriger_phrase, accroche, reglage, retirer_illustration, modifier_illustration.
    """
    avant = _instantane(sortie)
    duree_source = montage["infos"]["duree"]
    silences = montage.get("silences") or []
    effets = sortie["effets"]
    resume, refus = [], []

    def temps(valeur, defaut=0.0):
        try:
            return max(0.0, min(float(valeur), duree_source))
        except (TypeError, ValueError):
            return defaut

    for operation in operations:
        genre = operation.get("type")
        debut, fin = temps(operation.get("debut")), temps(operation.get("fin"))
        if genre == "couper":
            debut, fin = affiner_borne(debut, silences, True), affiner_borne(fin, silences, False)
            if fin - debut < 0.15:
                refus.append("Plage à couper trop courte.")
                continue
            restants = mp.soustraire_intervalles(sortie["segments"], [(debut, fin)])
            if not restants:
                refus.append("Cette coupe retirerait toute la vidéo.")
                continue
            sortie["segments"] = restants
            resume.append(f"Coupé de {mp._mmss(debut)} à {mp._mmss(fin)}")
        elif genre == "restaurer":
            if fin - debut < 0.15:
                refus.append("Plage à restaurer trop courte.")
                continue
            sortie["segments"] = _fusionner(list(sortie["segments"]) + [(debut, fin)])
            resume.append(f"Rétabli de {mp._mmss(debut)} à {mp._mmss(fin)}")
        elif genre == "zoom":
            debut_demande = debut
            debut = dans_le_montage(debut, sortie["segments"])
            fin = fin + (debut - debut_demande)
            if fin - debut < 0.5:
                fin = min(debut + 2.5, duree_source)
            effets["zoom_manuel"].append((debut, min(fin, debut + 8.0), mp.FORCE_ZOOM.get(operation.get("force"), mp.FORCE_ZOOM["leger"])))
            resume.append(f"Zoom ajouté à {mp._mmss(debut)}")
        elif genre == "retirer_zoom":
            effets["zoom_manuel"] = [z for z in effets["zoom_manuel"] if not z[0] - 0.05 <= debut <= z[1] + 0.05]
            effets["sans_zoom"].append((max(0.0, debut - 1.2), min(duree_source, debut + 1.2)))
            resume.append(f"Zoom retiré vers {mp._mmss(debut)}")
        elif genre == "titre":
            texte = " ".join(str(operation.get("texte") or "").split())[:80]
            if not texte:
                refus.append("Titre sans texte.")
                continue
            duree_titre = fin - debut if fin - debut >= 1.0 else 3.0
            debut = dans_le_montage(debut, sortie["segments"])
            effets["titres"].append((debut, debut + duree_titre, texte))
            resume.append(f"Titre ajouté à {mp._mmss(debut)}")
        elif genre == "retirer_titre":
            indice = operation.get("indice")
            if isinstance(indice, int) and 0 <= indice < len(effets["titres"]):
                effets["titres"].pop(indice)
                resume.append("Titre retiré")
        elif genre == "mot_en_gros":
            mots = _normaliser_mots(operation.get("mots") or operation.get("texte"))
            if not mots:
                refus.append("Aucun mot à mettre en valeur.")
                continue
            effets["mots_gros"] = sorted(set(effets["mots_gros"]) | set(mots))
            resume.append("Mis en valeur : " + " ".join(mots))
        elif genre == "retirer_mot_en_gros":
            mots = set(_normaliser_mots(operation.get("mots") or operation.get("texte")))
            effets["mots_gros"] = [m for m in effets["mots_gros"] if m not in mots]
            effets["mots_cles"] = [m for m in effets["mots_cles"] if mp._normaliser(m) not in mots]
            resume.append("Mise en valeur retirée : " + " ".join(sorted(mots)))
        elif genre == "corriger_phrase":
            indice, texte = operation.get("indice"), " ".join(str(operation.get("texte") or "").split())
            if not isinstance(indice, int) or not 0 <= indice < len(sortie["phrases"]) or not texte:
                refus.append("Correction de sous-titre invalide.")
                continue
            sortie["phrases"][indice]["texte"] = texte[:300]
            resume.append("Sous-titre corrigé")
        elif genre == "accroche":
            texte = " ".join(str(operation.get("texte") or "").split())[:90]
            effets["titre"] = texte
            effets["actif"]["accroche"] = bool(texte)
            resume.append("Accroche modifiée" if texte else "Accroche retirée")
        elif genre == "reglage":
            cle, valeur = operation.get("cle"), bool(operation.get("valeur"))
            if cle in REGLAGES_ACTIFS:
                effets["actif"][cle] = valeur
            elif cle == "alterne":
                effets["alterne"] = valeur
            elif cle == "sous_titres":
                sortie["sous_titres"] = valeur
            else:
                refus.append("Réglage inconnu.")
                continue
            resume.append(f"Réglage « {cle} » {'activé' if valeur else 'désactivé'}")
        elif genre == "retirer_illustration":
            avant_nb = len(effets.get("illustrations", []))
            effets["illustrations"] = [i for i in effets.get("illustrations", []) if i["id"] != operation.get("id")]
            if len(effets["illustrations"]) < avant_nb:
                resume.append("Illustration retirée")
        elif genre == "modifier_illustration":
            cible = next((i for i in effets.get("illustrations", []) if i["id"] == operation.get("id")), None)
            if not cible:
                refus.append("Illustration introuvable.")
                continue
            if operation.get("debut") is not None:
                cible["debut"] = debut
            if operation.get("fin") is not None:
                cible["fin"] = max(fin, cible["debut"] + 0.5)
            if operation.get("mode") in MODES_ILLUSTRATION:
                cible["mode"] = operation["mode"]
            resume.append("Illustration modifiée")
        else:
            refus.append("Opération non reconnue.")
    if resume:
        sortie["annulations"] = (sortie.get("annulations") or []) + [avant]
        sortie["annulations"] = sortie["annulations"][-NB_ANNULATIONS_MAX:]
    return resume, refus


# --------------------------------------------------------------------------- illustrations

def ajouter_illustration(montage: dict, sortie: dict, octets: bytes, debut: float, fin: float, mode: str, origine: str, prompt: str = "") -> str:
    """Enregistre une image (importee ou generee) comme illustration a afficher de debut a fin (temps de la video d'origine)."""
    if len(octets) > TAILLE_MAX_IMAGE:
        raise ErreurEditeur("Image trop lourde (15 Mo maximum).")
    if len(sortie["effets"].setdefault("illustrations", [])) >= NB_ILLUSTRATIONS_MAX:
        raise ErreurEditeur(f"{NB_ILLUSTRATIONS_MAX} illustrations au maximum.")
    try:
        image = Image.open(BytesIO(octets))
        image.load()
    except Exception as erreur:
        raise ErreurEditeur("Ce fichier n'est pas une image lisible (PNG, JPG ou WEBP).") from erreur
    image = image.convert("RGB")
    image.thumbnail((1920, 1920))
    duree_source = montage["infos"]["duree"]
    debut = max(0.0, min(float(debut), duree_source))
    duree_ill = max(0.5, float(fin) - debut)
    debut = dans_le_montage(debut, sortie["segments"])
    fin = debut + duree_ill
    identifiant = uuid.uuid4().hex[:10]
    dossier = os.path.join(montage["dossier"], "illustrations")
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, f"{identifiant}.jpg")
    image.save(chemin, format="JPEG", quality=92)
    sortie["annulations"] = ((sortie.get("annulations") or []) + [_instantane(sortie)])[-NB_ANNULATIONS_MAX:]
    sortie["effets"]["illustrations"].append({
        "id": identifiant, "debut": debut, "fin": fin, "mode": mode if mode in MODES_ILLUSTRATION else "incrust",
        "fichier": chemin, "origine": origine, "prompt": prompt[:300],
    })
    return identifiant


def fichier_illustration(sortie: dict, identifiant: str):
    cible = next((i for i in sortie["effets"].get("illustrations", []) if i["id"] == identifiant), None)
    return cible["fichier"] if cible and os.path.exists(cible["fichier"]) else None


# --------------------------------------------------------------------------- apercu leger

def preparer_apercu(montage: dict) -> None:
    """
    Copie legere de la video d'origine (540 p, tres compressee, images cles frequentes pour pouvoir se deplacer dedans) lue par
    l'editeur. Preparee une seule fois par video, en tache de fond.
    """
    etat = montage.setdefault("apercu", {"etat": "absent", "progression": 0.0})
    if etat["etat"] in ("en_cours", "pret"):
        return
    etat.update(etat="en_cours", progression=0.0, erreur="")
    sortie = os.path.join(montage["dossier"], "apercu.mp4")
    duree = max(1.0, montage["infos"]["duree"])

    def travail():
        try:
            commande = [mp.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-i", montage["source"],
                        "-vf", "scale=-2:540,fps=25", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-g", "12", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-movflags", "+faststart", sortie]
            processus = subprocess.Popen(commande, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            erreurs = []
            lecteur = threading.Thread(target=lambda: erreurs.append(processus.stderr.read()), daemon=True)
            lecteur.start()
            for ligne in processus.stdout:
                if ligne.startswith("out_time_us="):
                    try:
                        etat["progression"] = min(0.99, int(ligne.split("=")[1]) / 1_000_000 / duree)
                    except ValueError:
                        pass
            processus.wait()
            lecteur.join(timeout=5)
            if processus.returncode != 0 or not os.path.exists(sortie):
                raise mp.ErreurMontage((erreurs[0] if erreurs else "")[-300:] or "Aperçu impossible.")
            etat.update(etat="pret", progression=1.0)
        except Exception as erreur:
            etat.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()


def fichier_apercu(montage: dict):
    chemin = os.path.join(montage["dossier"], "apercu.mp4")
    return chemin if montage.get("apercu", {}).get("etat") == "pret" and os.path.exists(chemin) else None


# --------------------------------------------------------------------------- rendu final depuis l'editeur

def lancer_rendu(identifiant: str, numero: int) -> None:
    """Rend la sortie avec l'etat actuel de l'editeur : une nouvelle version, la precedente reste disponible (retour en arriere)."""
    montage = mp.MONTAGES[identifiant]
    montage.update(etat="en_cours", etape="Rendu de la vidéo", progression=0.02, erreur="", avertissement="")

    def travail():
        try:
            sortie = montage["sorties"][numero]
            suivi = lambda p: montage.__setitem__("progression", 0.02 + 0.98 * p)
            chemin, duree = mp._rendre_sortie(montage, sortie, suivi)
            sortie["historique"] = (sortie["historique"] + [mp._version_rendue(sortie)])[-5:]
            sortie.update(chemin=chemin, duree=round(duree, 1), version=sortie["version"] + 1, resume=["Rendu depuis l'éditeur"], annulations=[])
            mp._figer_etat_rendu(sortie)
            montage.update(etat="termine", etape="Terminé", progression=1.0)
        except Exception as erreur:
            montage.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
