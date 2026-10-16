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

from . import cartes_montage, charte_montage
from . import cartes_montage as cm
from . import montage_parole as mp
from . import montage_publication as pub
from . import styles_montage

EXTENSIONS_IMAGE = {"png", "jpg", "jpeg", "webp"}
TAILLE_MAX_IMAGE = 15 * 1024 * 1024
NB_ILLUSTRATIONS_MAX = 40
NB_ANNULATIONS_MAX = 30
REGLAGES_ACTIFS = ("zooms", "mots", "accroche", "barre")
MODES_ILLUSTRATION = ("incrust", "plein", "carte", "partage")
SNAP_SILENCE = 0.4          # un bord de coupe a moins de 0,4 s d'un silence est cale dedans
SNAP_MARGE = 0.1


class ErreurEditeur(RuntimeError):
    pass


# --------------------------------------------------------------------------- lecture de l'etat

def mots_de_phrase(phrase: dict) -> list:
    """Mots d'une phrase avec leur instant estime (duree repartie selon la longueur des mots, comme pour les sous-titres)."""
    return [{"texte": m["texte"], "debut": round(m["debut"], 3), "fin": round(m["fin"], 3)} for m in mp.mots_chronometres(phrase)]


def _vers_source(zooms_montage: list, segments: list) -> list:
    """Fenetres de zoom du montage -> fenetres en temps de la video d'origine (pour l'apercu)."""
    resultat = []
    for fenetre in zooms_montage:
        a, b, amplitude = fenetre[:3]
        resultat.append({"debut": round(mp.montage_vers_source(a, segments), 3), "fin": round(mp.montage_vers_source(b, segments, True), 3),
                         "amplitude": amplitude, "mode": fenetre[3] if len(fenetre) > 3 else "doux"})
    return resultat


def _source(instant: float, segments: list) -> float:
    return round(mp.montage_vers_source(instant, segments), 3)


def etat_editeur(identifiant: str, montage: dict, sortie: dict) -> dict:
    """Tout ce dont la page a besoin : transcription par mots, parties gardees, effets et illustrations, en temps de la video d'origine."""
    segments, effets = sortie["segments"], sortie["effets"]
    plan = mp.plan_effets(sortie)
    duree = plan["duree"]
    zooms = _vers_source(plan["zooms"], segments)
    for zoom in zooms:      # un zoom ajoute a la main : meme amplitude et fenetre qui recouvre la sienne (ses bords peuvent bouger avec les coupes)
        zoom["manuel"] = zoom["mode"] == "doux" and any(abs(zoom["amplitude"] - amp) < 1e-6 and zoom["debut"] < f + 0.01 and zoom["fin"] > d - 0.01
                                                         for d, f, amp in effets["zoom_manuel"])
    partages = {i["id"] for i in plan["illustrations"] if i.get("partage")}
    transitions = [{"type": genre, "debut": _source(a, segments), "fin": _source(b, segments)}
                   for genre in ("flash", "glitch", "secousse") for a, b in plan["fenetres"][genre]]
    return {
        "id": identifiant, "numero": sortie["numero"], "titre": sortie["titre"], "version": sortie["version"], "format": sortie["format"],
        "visage_x": sortie.get("visage_x"),
        "duree_source": round(montage["infos"]["duree"], 3), "duree_montee": round(duree, 3),
        "segments": [[round(a, 3), round(b, 3)] for a, b in segments],
        "silences": [[round(a, 3), round(b, 3)] for a, b in (montage.get("silences") or [])],
        "phrases": [{"indice": i, "debut": p["debut"], "fin": p["fin"], "texte": p["texte"], "mots": mots_de_phrase(p)} for i, p in enumerate(sortie["phrases"])],
        "zooms": zooms, "transitions": transitions,
        "sons": [{"t": _source(e["t"], segments), "son": e["son"], "volume": e["volume"]} for e in plan["bruitages"]],
        "titres": [{"indice": i, "debut": d, "fin": f, "texte": t} for i, (d, f, t) in enumerate(effets["titres"])],
        "illustrations": [{"id": ill["id"], "debut": ill["debut"], "fin": ill["fin"], "mode": ill["mode"], "origine": ill.get("origine", ""),
                           "prompt": ill.get("prompt", ""), "kind": ill.get("kind", "image"), "duree_clip": ill.get("duree_clip"), "place": ill.get("place", "haut"),
                           "carte": ill.get("carte"), "partage": ill["id"] in partages, "anime": bool(ill.get("sequence"))} for ill in effets.get("illustrations", [])],
        "charte": charte_montage.nettoyer(effets.get("charte")),
        "plans": [{"id": p["id"], "prompt": p["prompt"], "etat": p["etat"], "erreur": p.get("erreur", ""), "qualite": p["qualite"]}
                  for p in sorted(montage.get("plans", {}).values(), key=lambda x: x["cree"])[-8:]],
        "mots_gros": sorted(set(effets["mots_gros"]) | {mp._normaliser(m) for m in effets["mots_cles"]}),
        "reglages": {**effets["actif"], "alterne": bool(effets.get("alterne")), "bruitages": bool(effets.get("bruitages")), "pastille": bool(effets.get("pastille")),
                     "editorial": bool(effets.get("editorial")), "partage": bool(effets.get("partage")), "classique": bool(effets.get("classique")),
                     "panneau": "#%02x%02x%02x" % plan["panneau"],
                     "transition": effets.get("transition", "aucune"), "sous_titres": bool(sortie["sous_titres"]), "style": effets["style"]},
        "accroche": effets["titre"], "peut_annuler": bool(sortie.get("annulations")), "peut_revenir": bool(sortie["historique"]),
        "non_rendu": pub.non_rendu(sortie),
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
    with montage["verrou"]:
        return _appliquer_operations(montage, sortie, operations)


def _appliquer_operations(montage: dict, sortie: dict, operations: list) -> tuple:
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
            mp.appliquer_texte(sortie["phrases"][indice], texte[:300])
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
            elif cle == "bruitages":
                effets["bruitages"] = valeur
            elif cle == "pastille":
                effets["pastille"] = valeur
            elif cle in ("editorial", "partage", "classique"):
                effets[cle] = valeur
            elif cle == "sous_titres":
                sortie["sous_titres"] = valeur
            elif cle == "transition":
                if operation.get("valeur") not in styles_montage.TRANSITIONS_VALIDES:
                    refus.append("Transition inconnue.")
                    continue
                effets["transition"] = operation["valeur"]
                resume.append(f"Transition : {mp.TRANSITIONS[effets['transition']]}")
                continue
            else:
                refus.append("Réglage inconnu.")
                continue
            resume.append(f"Réglage « {cle} » {'activé' if valeur else 'désactivé'}")
        elif genre == "appliquer_style":
            reglages = styles_montage.nettoyer_reglages(operation.get("reglages"))
            if not reglages:
                refus.append("Style vide.")
                continue
            for cle in REGLAGES_ACTIFS:
                if cle in reglages:
                    effets["actif"][cle] = reglages[cle]
            for cle in ("alterne", "bruitages", "pastille", "transition", "style", "editorial", "partage"):
                if cle in reglages:
                    effets[cle] = reglages[cle]
            resume.append("Style appliqué" + (f" : {operation['nom']}" if operation.get("nom") else ""))
        elif genre == "appliquer_charte":
            effets["charte"] = charte_montage.nettoyer(operation.get("charte"))
            resume.append("Charte graphique appliquée (les cartes déjà créées gardent leurs couleurs)")
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
            if operation.get("mode") in MODES_ILLUSTRATION and cible.get("kind") != "carte":
                cible["mode"] = operation["mode"]
            if operation.get("place") in cartes_montage.POSITIONS and cible.get("kind") == "carte":
                cible["place"] = operation["place"]
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
    with montage["verrou"]:
        sortie["annulations"] = ((sortie.get("annulations") or []) + [_instantane(sortie)])[-NB_ANNULATIONS_MAX:]
        sortie["effets"]["illustrations"].append({
            "id": identifiant, "debut": debut, "fin": fin, "mode": mode if mode in MODES_ILLUSTRATION else "incrust",
            "fichier": chemin, "origine": origine, "prompt": prompt[:300],
        })
    return identifiant


# --------------------------------------------------------------------------- plans video IA (Veo)

NB_PLANS_PAR_MONTAGE = 10
NB_PLANS_SIMULTANES = 3
DUREE_PLAN_MAX = 8.0
COUT_PLAN = {"lite": "environ 0,40 $", "fast": "environ 0,80 $"}


def lancer_plan_video(montage: dict, sortie: dict, prompt: str, debut: float, mode: str, qualite: str, generer) -> str:
    """
    Cree en tache de fond un court plan video par IA (generer(prompt, qualite, vertical) -> octets MP4 ; voir veo_video.generer_video, une a
    quelques minutes et un cout reel) puis l'ajoute comme illustration a `debut`. Renvoie l'identifiant du plan ; l'avancement se lit dans
    l'etat de l'editeur (cle « plans »).
    """
    prompt = " ".join(str(prompt or "").split())[:400]
    if not prompt:
        raise ErreurEditeur("Décrivez le plan à créer.")
    plans = montage.setdefault("plans", {})
    if len(plans) >= NB_PLANS_PAR_MONTAGE:
        raise ErreurEditeur(f"{NB_PLANS_PAR_MONTAGE} plans vidéo au maximum par montage (chacun a un coût).")
    if sum(1 for p in plans.values() if p["etat"] == "en_cours") >= NB_PLANS_SIMULTANES:
        raise ErreurEditeur(f"{NB_PLANS_SIMULTANES} plans en cours de création au maximum : attendez qu'un se termine.")
    identifiant = uuid.uuid4().hex[:10]
    plan = {"id": identifiant, "prompt": prompt, "etat": "en_cours", "erreur": "", "qualite": qualite if qualite in COUT_PLAN else "lite", "cree": __import__("time").time()}
    plans[identifiant] = plan
    mode = mode if mode in MODES_ILLUSTRATION else "plein"
    vertical = sortie["format"] == "vertical"

    def travail():
        try:
            octets = generer(prompt, plan["qualite"], vertical)
            dossier = os.path.join(montage["dossier"], "illustrations")
            os.makedirs(dossier, exist_ok=True)
            chemin_video = os.path.join(dossier, f"{identifiant}.mp4")
            with open(chemin_video, "wb") as fichier:
                fichier.write(octets)
            infos = mp.sonde(chemin_video)
            duree_clip = min(DUREE_PLAN_MAX, max(1.0, infos["duree"]))
            vignette = os.path.join(dossier, f"{identifiant}.jpg")
            resultat = mp._lancer([mp.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.3", "-i", chemin_video, "-frames:v", "1", "-vf", "scale=1080:-2", vignette], 120)
            if resultat.returncode != 0 or not os.path.exists(vignette):
                raise ErreurEditeur("Plan vidéo illisible.")
            with montage["verrou"]:
                sortie["annulations"] = ((sortie.get("annulations") or []) + [_instantane(sortie)])[-NB_ANNULATIONS_MAX:]
                debut_effectif = dans_le_montage(max(0.0, min(float(debut), montage["infos"]["duree"])), sortie["segments"])
                sortie["effets"].setdefault("illustrations", []).append({
                    "id": identifiant, "debut": debut_effectif, "fin": debut_effectif + duree_clip, "mode": mode, "fichier": vignette, "fichier_video": chemin_video,
                    "kind": "video", "duree_clip": round(duree_clip, 1), "origine": "ia-video", "prompt": prompt[:300],
                })
            plan["etat"] = "pret"
        except Exception as erreur:
            plan.update(etat="erreur", erreur=str(erreur)[:300] or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
    return identifiant


def fichier_video_illustration(sortie: dict, identifiant: str):
    cible = next((i for i in sortie["effets"].get("illustrations", []) if i["id"] == identifiant and i.get("kind") == "video"), None)
    return cible["fichier_video"] if cible and os.path.exists(cible["fichier_video"]) else None


def _ecrire_sequence(dossier: str, identifiant: str, genre: str, textes: dict, charte: dict, format_video: str):
    """Carte animee : une image par etat (voir cartes_montage.etats_animation) et une liste ffmpeg (concat) qui les enchaine ; None si rien a animer."""
    etats = cm.etats_animation(genre, textes)
    if not etats:
        return None
    repertoire = os.path.join(dossier, f"{identifiant}_seq")
    os.makedirs(repertoire, exist_ok=True)
    lignes, dernier = ["ffconcat version 1.0"], None
    for rang, (progres, duree_etat) in enumerate(etats):
        chemin = os.path.join(repertoire, f"e{rang:02d}.png")
        with open(chemin, "wb") as fichier:
            fichier.write(cm.rendre_carte(genre, textes, charte, format_video, progres=progres))
        dernier = chemin.replace(os.sep, "/")
        lignes += [f"file '{dernier}'", f"duration {duree_etat:.3f}"]
    lignes.append(f"file '{dernier}'")          # le dernier fichier doit etre repete pour que sa duree soit respectee
    liste = os.path.join(repertoire, "sequence.txt")
    with open(liste, "w", encoding="utf-8") as fichier:
        fichier.write("\n".join(lignes) + "\n")
    return liste


def ajouter_carte(montage: dict, sortie: dict, genre: str, textes: dict, debut: float, duree: float, place: str = "haut", anime: bool = True) -> str:
    """Dessine une carte graphique (voir cartes_montage.py) aux couleurs de la charte du montage et l'ajoute a `debut` pour `duree` secondes (animee si le type s'y prete)."""
    effets = sortie["effets"]
    if len(effets.setdefault("illustrations", [])) >= NB_ILLUSTRATIONS_MAX:
        raise ErreurEditeur(f"{NB_ILLUSTRATIONS_MAX} illustrations et cartes au maximum.")
    if genre not in cartes_montage.TYPES:
        raise ErreurEditeur("Type de carte inconnu.")
    textes = cartes_montage.nettoyer_textes(textes)
    if not any(textes[c] for c in ("titre", "valeur", "texte", "mot", "elements")):
        raise ErreurEditeur("Écrivez le texte de la carte.")
    png = cartes_montage.rendre_carte(genre, textes, effets.get("charte"), sortie["format"])
    duree_source = montage["infos"]["duree"]
    debut = dans_le_montage(max(0.0, min(float(debut), duree_source)), sortie["segments"])
    duree = max(1.5, min(float(duree), 8.0))
    identifiant = uuid.uuid4().hex[:10]
    dossier = os.path.join(montage["dossier"], "illustrations")
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, f"{identifiant}.png")
    with open(chemin, "wb") as fichier:
        fichier.write(png)
    sequence = _ecrire_sequence(dossier, identifiant, genre, textes, effets.get("charte"), sortie["format"]) if anime else None
    with montage["verrou"]:
        sortie["annulations"] = ((sortie.get("annulations") or []) + [_instantane(sortie)])[-NB_ANNULATIONS_MAX:]
        effets["illustrations"].append({
            "id": identifiant, "debut": debut, "fin": debut + duree, "mode": "carte", "kind": "carte", "fichier": chemin, "origine": "carte",
            "prompt": "", "place": place if place in cartes_montage.POSITIONS else "haut", "carte": {"type": genre, "textes": textes},
            **({"sequence": sequence} if sequence else {}),
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
