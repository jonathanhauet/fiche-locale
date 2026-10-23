"""
Voix off des videos de motion design : synthese vocale OpenAI (modele gpt-4o-mini-tts, la meme cle OPENAI_API_KEY que la transcription Whisper).
Une phrase par scene : le champ « voix » du scenario, ou a defaut un texte tire de ce qui est affiche a l'ecran. Les fichiers audio sont places par
motion_video.rendre() au debut de chaque scene, avec la musique baissee pendant que la voix parle. Un texte deja synthetise n'est pas re-facture
(petit cache en memoire), ce qui permet de refaire un rendu apres une retouche sans payer deux fois.
"""

import array
import hashlib
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .montage_video import FFMPEG

MODELE = "gpt-4o-mini-tts"
ADRESSE = "https://api.openai.com/v1/audio/speech"
MAX_CARACTERES_SCENE = 320                 # une scene ne parle pas plus longtemps (environ 25 secondes)
MAX_CARACTERES_VIDEO = 2400                # plafond par video : borne le cout en cas de scenario demesure
PHRASE_EXTRAIT = "Bonjour, voici un extrait de la voix choisie pour votre vidéo. Elle vous convient-elle ?"
PHRASE_EXTRAIT_PERSONNAGE = "Salut, c'est moi ! Aujourd'hui, on regarde ensemble comment faire travailler ta fiche Google pour toi."

VOIX = {
    "nova": "Nova : claire et chaleureuse",
    "shimmer": "Shimmer : douce",
    "coral": "Coral : vive",
    "sage": "Sage : posée",
    "onyx": "Onyx : grave",
    "echo": "Echo : nette",
    "ash": "Ash : naturelle",
    "ballad": "Ballad : expressive",
}
VOIX_DEFAUT = "nova"                       # voix off (narration) : feminine
VOIX_PERSONNAGE_DEFAUT = "onyx"            # voix du personnage quand il parle (scenes « personnage ») : masculine
GENRES_VOIX = {"nova": "féminine", "shimmer": "féminine", "coral": "féminine", "sage": "féminine", "onyx": "masculine", "echo": "masculine", "ash": "masculine", "ballad": "masculine"}

BASE_INSTRUCTIONS = "Parle en français de France, sans accent étranger, avec une diction naturelle, comme un professionnel qui présente son métier à un client. "
INSTRUCTIONS_TON = {
    "elegant": "Voix posée et élégante, rythme calme, articulation soignée.",
    "dynamique": "Voix dynamique et affirmée, rythme soutenu, énergie positive, sans crier.",
    "chaleureux": "Voix chaleureuse et proche, souriante, rassurante.",
    "humour": "Voix légère et amusée, avec un petit sourire dans la voix, sans exagérer.",
    "premium": "Voix sobre et premium, calme, chaque mot compte, aucune emphase.",
}


class ErreurVoix(Exception):
    pass


_CACHE = {}
_MAX_CACHE = 60


def disponible() -> bool:
    """Vrai si la cle OpenAI est configuree (sans elle, la voix off est impossible)."""
    return bool(os.getenv("OPENAI_API_KEY"))


def voix_valide(voix, defaut: str = VOIX_DEFAUT) -> str:
    return voix if voix in VOIX else defaut


def nettoyer_texte(texte) -> str:
    """Texte lisible a voix haute : sans marques de mise en forme ni tirets cadratins, etoiles dites « étoiles », espaces propres, longueur bornee."""
    t = re.sub(r"[*_]", "", str(texte or ""))
    t = t.replace("★", " étoiles ").replace("—", ", ").replace("–", ", ")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_CARACTERES_SCENE]


def texte_a_dire(scene: dict) -> str:
    """Ce que dit la voix pendant la scene : le champ « voix » s'il existe, sinon un texte tire de l'ecran (vide si rien ne s'y prete)."""
    if scene.get("voix"):
        return nettoyer_texte(scene["voix"])
    genre = scene.get("type")
    items = ", ".join(scene.get("items") or [])
    morceaux = {
        "hook": [scene.get("texte"), scene.get("sous_texte")],
        "chiffre": [f"{scene.get('valeur', '')} {scene.get('suffixe', '')}".strip(), scene.get("libelle")],
        "liste": [scene.get("titre"), items],
        "etape": [scene.get("titre"), scene.get("texte")],
        "citation": [scene.get("texte"), scene.get("auteur")],
        "marque": [scene.get("nom"), scene.get("accroche")],
        "cta": [scene.get("texte")],
        "photo": [scene.get("texte"), scene.get("sous_texte")],
        "mosaique": [scene.get("titre")],
        "avant_apres": [scene.get("titre"), scene.get("legende")],
        "realisation": [scene.get("titre"), scene.get("sous_texte"), scene.get("texte")],
        "personnage": [scene.get("texte")],
        "recherche": [scene.get("legende")],
    }.get(genre, [])
    return nettoyer_texte(". ".join(str(m).strip().rstrip(".") for m in morceaux if m and str(m).strip()))


def _instructions(ton: str) -> str:
    return BASE_INSTRUCTIONS + INSTRUCTIONS_TON.get(ton, INSTRUCTIONS_TON["dynamique"])


def synthese(texte: str, voix: str = VOIX_DEFAUT, ton: str = "dynamique") -> bytes:
    """Fichier MP3 de la phrase dite par la voix choisie. Leve ErreurVoix avec un message clair (cle absente, quota, coupure...)."""
    texte = nettoyer_texte(texte)
    if not texte:
        raise ErreurVoix("Aucun texte à dire.")
    cle_api = os.getenv("OPENAI_API_KEY")
    if not cle_api:
        raise ErreurVoix("La voix off demande la clé OpenAI (OPENAI_API_KEY manquant dans plateforme_web/.env).")
    voix = voix_valide(voix)
    cle = hashlib.sha1(f"{MODELE}|{voix}|{ton}|{texte}".encode("utf-8")).hexdigest()
    if cle in _CACHE:
        return _CACHE[cle]
    derniere = None
    for tentative in range(3):
        try:
            reponse = requests.post(ADRESSE, headers={"Authorization": f"Bearer {cle_api}"}, timeout=120,
                                    json={"model": MODELE, "voice": voix, "input": texte, "instructions": _instructions(ton), "response_format": "mp3"})
        except requests.RequestException as erreur:           # coupure reseau : on retente
            derniere = f"connexion impossible ({erreur.__class__.__name__})"
            time.sleep(1.5 * (tentative + 1))
            continue
        if reponse.status_code == 200 and reponse.content:
            if len(_CACHE) >= _MAX_CACHE:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[cle] = reponse.content
            return reponse.content
        if reponse.status_code in (401, 403):
            raise ErreurVoix("OpenAI refuse la clé (vérifiez OPENAI_API_KEY et que votre compte a du crédit).")
        if reponse.status_code == 429:
            derniere = "limite OpenAI atteinte (crédit épuisé ou trop de demandes)"
            time.sleep(2 * (tentative + 1))
            continue
        derniere = f"code {reponse.status_code} : {reponse.text[:150]}"
        if reponse.status_code < 500:
            break
        time.sleep(1.5 * (tentative + 1))
    raise ErreurVoix(f"Synthèse vocale impossible : {derniere}")


def duree_audio(chemin: str) -> float:
    """Duree d'un fichier audio en secondes (lu avec ffmpeg, qui est deja necessaire pour le montage)."""
    reponse = subprocess.run([FFMPEG, "-hide_banner", "-i", chemin, "-f", "null", "-"], capture_output=True, timeout=60)
    trouves = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", reponse.stderr.decode("utf-8", "replace"))
    if not trouves:
        raise ErreurVoix("Fichier audio illisible.")
    h, m, s = trouves[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def enveloppe(chemin: str, fps: int = 30) -> list:
    """Niveau sonore image par image (0 a 1, normalise sur les passages forts) : sert a faire bouger la bouche du personnage au rythme de sa voix."""
    reponse = subprocess.run([FFMPEG, "-loglevel", "error", "-i", chemin, "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True, timeout=60)
    echantillons = array.array("f")
    echantillons.frombytes(reponse.stdout[: len(reponse.stdout) // 4 * 4])
    if not len(echantillons):
        return []
    niveaux = []
    for i in range(int(len(echantillons) * fps / 8000) + 1):
        morceau = echantillons[int(i * 8000 / fps):int((i + 1) * 8000 / fps)]
        if len(morceau):
            niveaux.append((sum(x * x for x in morceau) / len(morceau)) ** 0.5)
    ordonnes = sorted(niveaux)
    reference = ordonnes[int(len(ordonnes) * 0.9)] if ordonnes else 0.0
    if reference < 1e-4:
        return [0.0] * len(niveaux)
    return [min(1.0, n / reference) for n in niveaux]


def preparer(scenes: list, voix: str, ton: str, dossier: str, voix_personnage: str = "") -> list:
    """
    Une entree par scene : (chemin du fichier MP3, duree en secondes), ou None si la scene n'a rien a dire. Les phrases sont synthetisees en parallele.
    voix_personnage : voix des scenes « personnage » (le personnage lit sa bulle), differente de la voix off des autres scenes ; vide : meme voix partout.
    Leve ErreurVoix si le total depasse le plafond de caracteres ou si une synthese echoue.
    """
    voix_perso = voix_valide(voix_personnage, VOIX_PERSONNAGE_DEFAUT) if voix_personnage else ""
    voix_de = lambda sc: voix_perso if (voix_perso and sc.get("type") == "personnage") else voix
    textes = [texte_a_dire(sc) for sc in scenes]
    total = sum(len(t) for t in textes)
    if total > MAX_CARACTERES_VIDEO:
        raise ErreurVoix(f"Le texte de la voix off est trop long ({total} caractères, maximum {MAX_CARACTERES_VIDEO}) : raccourcissez les phrases.")
    if not any(textes):
        return [None] * len(scenes)
    uniques = sorted({(t, voix_de(sc)) for t, sc in zip(textes, scenes) if t})
    with ThreadPoolExecutor(max_workers=3) as groupe:
        audios = dict(zip(uniques, groupe.map(lambda tv: synthese(tv[0], tv[1], ton), uniques)))
    sorties = []
    for i, texte in enumerate(textes):
        if not texte:
            sorties.append(None)
            continue
        chemin = os.path.join(dossier, f"voix_{i}.mp3")
        with open(chemin, "wb") as fichier:
            fichier.write(audios[(texte, voix_de(scenes[i]))])
        sorties.append((chemin, duree_audio(chemin)))
    return sorties
