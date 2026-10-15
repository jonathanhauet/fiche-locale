"""
Montage automatique d'une video face camera (« talking head ») : transcription (Gemini), coupe des silences et des
hesitations, retouches demandees en langage naturel et choix d'extraits pour des Shorts (Claude, voir
claude_generation.py), sous-titres incrustes a la volee (ASS), musique de fond avec baisse automatique sous la voix,
rendu via ffmpeg (binaire externe, voir montage_video.py et nixpacks.toml).

Les montages tournent en tache de fond (un thread par montage, etat garde en memoire et fichiers dans un dossier
temporaire du serveur) : la page interroge l'etat toutes les quelques secondes. Rien n'est conserve durablement :
un redemarrage du serveur ou plus de DUREE_CONSERVATION_HEURES supprime les montages.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import unicodedata
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from . import charte_montage, montage_video, sons
from .montage_video import FFMPEG, FFPROBE

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))
DOSSIER_POLICES = os.path.join(DOSSIER_APP, "static", "polices")

DOSSIER_MONTAGES = os.path.join(tempfile.gettempdir(), "fiche_locale_montages")
DUREE_CONSERVATION_HEURES = 6
DUREE_MAX_SOURCE = 75 * 60
TAILLE_MAX_SOURCE = 900 * 1024 * 1024

MODELE_TRANSCRIPTION = os.getenv("GEMINI_MODELE_TRANSCRIPTION", "gemini-flash-latest")
DUREE_MORCEAU_TRANSCRIPTION = 240.0  # secondes : plus court = horodatages plus fiables, et traite en parallele

SILENCE_MINI_COUPE = 0.7   # un silence plus court est une respiration naturelle : on le garde
MARGE_AUTOUR_PAROLE = 0.18  # on garde cette marge de silence avant et apres chaque coupe, pour ne pas hacher la voix
DUREE_MINI_SEGMENT = 0.15
FPS = 25


class ErreurMontage(RuntimeError):
    pass


# --------------------------------------------------------------------------- ffmpeg / ffprobe

def _lancer(commande: list, delai: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(commande, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=delai)
    except FileNotFoundError as erreur:
        raise ErreurMontage("ffmpeg n'est pas installé sur ce serveur.") from erreur
    except subprocess.TimeoutExpired as erreur:
        raise ErreurMontage("Le traitement de la vidéo a pris trop de temps.") from erreur


def sonde(chemin: str) -> dict:
    """Duree, dimensions (apres rotation eventuelle d'un smartphone) et presence d'une piste audio."""
    if not montage_video.FFPROBE_DISPONIBLE:
        infos = montage_video.sonde_ffmpeg(chemin)
        if not infos["largeur"]:
            raise ErreurMontage("Fichier vidéo illisible.")
        return infos
    resultat = _lancer([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", chemin], 60)
    if resultat.returncode != 0:
        raise ErreurMontage("Fichier vidéo illisible.")
    donnees = json.loads(resultat.stdout or "{}")
    flux = donnees.get("streams", [])
    video = next((s for s in flux if s.get("codec_type") == "video"), None)
    if not video:
        raise ErreurMontage("Aucune image vidéo dans ce fichier.")
    largeur, hauteur = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for donnees_laterales in video.get("side_data_list", []) or []:
        if "rotation" in donnees_laterales:
            rotation = int(round(float(donnees_laterales["rotation"])))
    rotation = rotation or int(float((video.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rotation) % 180 == 90:
        largeur, hauteur = hauteur, largeur
    duree = float(donnees.get("format", {}).get("duration") or video.get("duration") or 0)
    return {"duree": duree, "largeur": largeur, "hauteur": hauteur, "audio": any(s.get("codec_type") == "audio" for s in flux)}


def normaliser_enregistrement(source: str) -> str:
    """
    Enregistrement fait dans le navigateur (webm de MediaRecorder : ni duree ni index, images a cadence variable) -> mp4 classique a
    30 images/s, que le reste du montage sait decouper precisement. Renvoie le chemin du nouveau fichier.
    """
    sortie = os.path.splitext(source)[0] + "-norm.mp4"
    resultat = _lancer([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", source, "-vf", "fps=30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", sortie], 1800)
    if resultat.returncode != 0 or not os.path.exists(sortie):
        raise ErreurMontage("L'enregistrement n'a pas pu être converti : " + (resultat.stderr or "")[-200:].strip())
    return sortie


def extraire_audio(video: str, sortie_wav: str) -> None:
    """WAV mono 16 kHz : sans dependance a un codec particulier, et lisible directement par Gemini."""
    resultat = _lancer([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", sortie_wav], 900)
    if resultat.returncode != 0:
        raise ErreurMontage("Impossible d'extraire le son de la vidéo.")


def detecter_silences(audio_wav: str) -> list:
    """[(debut, fin), ...] des silences. Seuil adapte au niveau de la voix (une piece bruyante ne doit pas tout garder)."""
    niveau = _lancer([FFMPEG, "-hide_banner", "-i", audio_wav, "-af", "volumedetect", "-f", "null", "-"], 600).stderr
    correspondance = re.search(r"mean_volume:\s*(-?[\d.]+) dB", niveau)
    moyen = float(correspondance.group(1)) if correspondance else -25.0
    seuil = max(-45.0, min(-26.0, moyen - 14.0))
    sortie = _lancer([FFMPEG, "-hide_banner", "-i", audio_wav, "-af", f"silencedetect=noise={seuil}dB:d=0.4", "-f", "null", "-"], 600).stderr
    silences, debut = [], None
    for ligne in sortie.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", ligne)
        if m:
            debut = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*(-?[\d.]+)", ligne)
        if m and debut is not None:
            silences.append((debut, float(m.group(1))))
            debut = None
    return silences


# --------------------------------------------------------------------------- transcription

PROMPT_TRANSCRIPTION = (
    "Transcris fidèlement cet extrait audio en français, découpé en courtes phrases de sous-titres (3 à 8 mots chacune, "
    "sans pause longue à l'intérieur d'une phrase). Pour chaque phrase donne : debut et fin en secondes depuis le début de "
    "CET extrait (une décimale, précis à une demi-seconde près), texte (avec la ponctuation, sans rien inventer ni "
    "reformuler), et hesitation = true uniquement si la phrase n'est qu'une hésitation (« euh », « hum », « ben ») ou un "
    "faux départ aussitôt répété, sinon false. Réponds uniquement par un tableau JSON, sans commentaire : "
    '[{"debut": 0.0, "fin": 1.8, "texte": "…", "hesitation": false}]. Si l\'extrait ne contient aucune parole, réponds [].'
)


def _json_depuis_texte(texte: str):
    texte = (texte or "").strip()
    texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte, flags=re.IGNORECASE)
    return json.loads(texte)


def _transcrire_morceau(octets_wav: bytes, decalage: float, duree: float) -> list:
    from google import genai
    from google.genai import types

    cle = os.getenv("GEMINI_API_KEY")
    if not cle:
        raise ErreurMontage("GEMINI_API_KEY manquant dans plateforme_web/.env.")
    client = genai.Client(api_key=cle)
    derniere_erreur = None
    for _ in range(3):
        try:
            reponse = client.models.generate_content(
                model=MODELE_TRANSCRIPTION,
                contents=[types.Part.from_bytes(data=octets_wav, mime_type="audio/wav"), PROMPT_TRANSCRIPTION],
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            )
            brut = _json_depuis_texte(reponse.text)
            break
        except Exception as erreur:  # reponse non JSON, quota, coupure reseau... on retente
            derniere_erreur = erreur
            time.sleep(2)
    else:
        raise ErreurMontage(f"Transcription impossible : {derniere_erreur}")
    phrases = []
    for element in brut if isinstance(brut, list) else []:
        try:
            debut, fin = float(element["debut"]), float(element["fin"])
            texte = " ".join(str(element["texte"]).split())
        except (KeyError, TypeError, ValueError):
            continue
        if not texte or fin <= debut:
            continue
        debut, fin = max(0.0, min(debut, duree)), max(0.0, min(fin, duree))
        phrases.append({"debut": debut + decalage, "fin": fin + decalage, "texte": texte, "hesitation": bool(element.get("hesitation"))})
    return phrases


def _morceaux(duree: float, silences: list) -> list:
    """Bornes des morceaux de transcription, calees sur un silence proche quand c'est possible."""
    bornes, debut = [], 0.0
    while duree - debut > DUREE_MORCEAU_TRANSCRIPTION * 1.25:
        cible = debut + DUREE_MORCEAU_TRANSCRIPTION
        proches = [(abs((d + f) / 2 - cible), (d + f) / 2) for d, f in silences if abs((d + f) / 2 - cible) < 25]
        fin = min(proches)[1] if proches else cible
        bornes.append((debut, fin))
        debut = fin
    bornes.append((debut, duree))
    return bornes


def transcrire(audio_wav: str, duree: float, silences: list, progression=None) -> list:
    """[{debut, fin, texte, hesitation}, ...] sur toute la video (morceaux transcrits en parallele)."""
    bornes = _morceaux(duree, silences)
    with wave.open(audio_wav, "rb") as fichier:
        cadence = fichier.getframerate()
        morceaux = []
        for debut, fin in bornes:
            fichier.setpos(int(debut * cadence))
            donnees = fichier.readframes(int((fin - debut) * cadence))
            tampon = tempfile.SpooledTemporaryFile()
            with wave.open(tampon, "wb") as sortie:
                sortie.setnchannels(1)
                sortie.setsampwidth(2)
                sortie.setframerate(cadence)
                sortie.writeframes(donnees)
            tampon.seek(0)
            morceaux.append((tampon.read(), debut, fin - debut))
    termines = 0
    verrou = threading.Lock()

    def travail(element):
        nonlocal termines
        resultat = _transcrire_morceau(*element)
        with verrou:
            termines += 1
            if progression:
                progression(termines / len(morceaux))
        return resultat

    with ThreadPoolExecutor(max_workers=4) as pool:
        resultats = list(pool.map(travail, morceaux))
    phrases = [p for lot in resultats for p in lot]
    phrases.sort(key=lambda p: p["debut"])
    return caler_sur_voix(phrases, silences)


def caler_sur_voix(phrases: list, silences: list) -> list:
    """
    Les horodatages de la transcription sont approximatifs (une fin peut deborder de plus d'une seconde dans un silence) :
    un debut tombant dans un silence est ramene a la fin de ce silence (debut reel de la voix), une fin tombant dans un
    silence au debut de ce silence. Si le recalage donnerait une phrase trop courte, l'horodatage d'origine est conserve.
    """
    sorties = []
    for p in phrases:
        debut, fin = p["debut"], p["fin"]
        for s_debut, s_fin in silences:
            if s_debut < debut < s_fin:
                debut = s_fin
            if s_debut < fin < s_fin:
                fin = s_debut
        sorties.append({**p, "debut": debut, "fin": fin} if fin - debut >= 0.3 else p)
    return sorties


# --------------------------------------------------------------------------- decoupage

def _fusionner(intervalles: list) -> list:
    fusion = []
    for debut, fin in sorted(i for i in intervalles if i[1] > i[0]):
        if fusion and debut <= fusion[-1][1]:
            fusion[-1] = (fusion[-1][0], max(fusion[-1][1], fin))
        else:
            fusion.append((debut, fin))
    return fusion


def segments_a_garder(duree: float, silences: list, couper_silences: bool = True, retirer: list = None,
                      debut: float = 0.0, fin: float = None) -> list:
    """
    Parties de la video a conserver dans [debut, fin] : on retire les silences longs (en gardant MARGE_AUTOUR_PAROLE de
    chaque cote) et les intervalles demandes (hesitations, consignes). Au moins DUREE_MINI_SEGMENT par morceau.
    """
    fin = duree if fin is None else min(fin, duree)
    coupes = list(retirer or [])
    if couper_silences:
        for s_debut, s_fin in silences:
            if s_fin - s_debut < SILENCE_MINI_COUPE:
                continue
            a = -1.0 if s_debut < 0.05 else s_debut + MARGE_AUTOUR_PAROLE
            b = duree + 1.0 if s_fin > duree - 0.05 else s_fin - MARGE_AUTOUR_PAROLE
            coupes.append((a, b))
    gardes, position = [], debut
    for c_debut, c_fin in _fusionner(coupes):
        if c_fin <= position:
            continue
        if c_debut > position:
            gardes.append((position, min(c_debut, fin)))
        position = max(position, c_fin)
        if position >= fin:
            break
    if position < fin:
        gardes.append((position, fin))
    return [(a, b) for a, b in gardes if b - a >= DUREE_MINI_SEGMENT]


def duree_montee(segments: list) -> float:
    return sum(b - a for a, b in segments)


def _remapper(instant: float, segments: list, vers_debut: bool) -> float:
    """Instant de la video d'origine -> instant du montage (None si tout autour est coupe)."""
    cumul = 0.0
    for debut, fin in segments:
        if instant < debut:
            return cumul if vers_debut else None
        if instant <= fin:
            return cumul + (instant - debut)
        cumul += fin - debut
    return None if vers_debut else cumul


def phrases_sur_montage(phrases: list, segments: list) -> list:
    """Phrases recalees sur la chronologie du montage ; une phrase entierement coupee disparait."""
    sorties = []
    for p in phrases:
        debut = _remapper(p["debut"], segments, True)
        fin = _remapper(p["fin"], segments, False)
        if debut is None or fin is None or fin - debut < 0.25:
            continue
        sorties.append({**p, "debut": debut, "fin": fin})
    return sorties


# --------------------------------------------------------------------------- sous-titres (ASS)

def _horodatage_ass(secondes: float) -> str:
    centiemes = int(round(secondes * 100))
    return f"{centiemes // 360000}:{centiemes // 6000 % 60:02d}:{centiemes // 100 % 60:02d}.{centiemes % 100:02d}"


def _couleur_ass(rgb: tuple) -> str:
    return "&H00{:02X}{:02X}{:02X}".format(rgb[2], rgb[1], rgb[0])


def _echapper_ass(texte: str) -> str:
    return texte.replace("\\", "").replace("{", "(").replace("}", ")")


def _lignes_sous_titre(mots: list, max_car: int) -> list:
    """Coupe en 1 ou 2 lignes equilibrees (indices de mots ou passer a la ligne)."""
    total = sum(len(m) for m in mots) + len(mots) - 1
    if total <= max_car or len(mots) < 3:
        return []
    cumul, meilleur, ecart = 0, len(mots) // 2, 10 ** 6
    for i in range(1, len(mots)):
        cumul += len(mots[i - 1]) + 1
        if abs(cumul - total / 2) < ecart:
            meilleur, ecart = i, abs(cumul - total / 2)
    return [meilleur]


def _normaliser(mot: str) -> str:
    """Mot en minuscules, sans accents ni ponctuation (comparaison des mots-cles)."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFKD", mot or "") if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", sans_accents.lower())


def _mots_pop(texte: str, majuscules: bool = True) -> list:
    mots = [re.sub(r"[,;:.]+$", "", m) for m in _echapper_ass(texte).split()]
    return [(m.upper() if majuscules else m.lower()) for m in mots if m]


def _groupes_pop(mots: list, max_car: int) -> list:
    """Groupes de 1 a 3 mots affiches ensemble (indices de mots), sur au plus deux lignes."""
    groupes, courant = [], []
    for indice, mot in enumerate(mots):
        essai = courant + [indice]
        total = sum(len(mots[i]) for i in essai) + len(essai) - 1
        if courant and (len(essai) > 3 or total > max_car * 2 - 2):
            groupes.append(courant)
            courant = [indice]
        else:
            courant = essai
    if courant:
        groupes.append(courant)
    return groupes


def _coupure_pop(mots_groupe: list, max_car: int):
    """Position (dans le groupe) du mot qui commence la 2e ligne, ou None si tout tient sur une ligne."""
    total = sum(len(m) for m in mots_groupe) + len(mots_groupe) - 1
    if total <= max_car or len(mots_groupe) < 2:
        return None
    if len(mots_groupe) == 2:
        return 1
    cumul, meilleur, ecart = 0, 1, 10 ** 6
    for i in range(1, len(mots_groupe)):
        cumul += len(mots_groupe[i - 1]) + 1
        if abs(cumul - total / 2) < ecart:
            meilleur, ecart = i, abs(cumul - total / 2)
    return meilleur


def _lignes_titre(debut: float, fin: float, texte: str, vertical: bool) -> str:
    # Pas de retour a la ligne automatique (WrapStyle 2) : le titre est coupe ici, sur 3 lignes au plus.
    lignes = textwrap.wrap(_echapper_ass(texte).upper(), width=18 if vertical else 34)[:3]
    return f"Dialogue: 1,{_horodatage_ass(debut)},{_horodatage_ass(fin)},Titre,,0,0,0,,{{\\fad(250,350)}}" + "\\N".join(lignes)


POLICE_SERIF = "DM Serif Display"


def _dialogues_pop(phrases: list, accent: tuple, max_car: int, gros: set, pastille: tuple = None, contour: int = 8, editorial: bool = False) -> list:
    """
    Sous-titres « pop » : 1 a 3 mots a la fois, en majuscules, le mot prononce grossit et prend la couleur d'accent ; les mots
    importants restent en couleur et plus gros. Un evenement par mot, calé sur la voix (duree repartie selon la longueur des mots).
    editorial=True : style « magazine » (minuscules, mots importants en serif italique, sans pastille ni couleur d'accent).
    """
    couleur_accent = _couleur_ass(accent)
    couleur_pastille = _couleur_ass(pastille) if pastille else None
    # Une « pastille » est le contour epais, arrondi, de la couleur de la charte : le mot reste en blanc par-dessus.
    contour_pastille = int(contour * 2.4)
    normal = f"\\3c&H00000000&\\bord{contour}"
    sorties = []
    for p in phrases:
        mots = _mots_pop(p["texte"], majuscules=not editorial)
        if not mots:
            continue
        poids = [len(m) + 1 for m in mots]
        total_poids, duree = sum(poids), max(0.05, p["fin"] - p["debut"])
        debuts, cumul = [], 0.0
        for poids_mot in poids:
            debuts.append(p["debut"] + duree * cumul / total_poids)
            cumul += poids_mot
        fins = debuts[1:] + [p["fin"]]
        for groupe in _groupes_pop(mots, max_car):
            mots_groupe = [mots[i] for i in groupe]
            coupure = _coupure_pop(mots_groupe, max_car)
            for rang, indice in enumerate(groupe):
                morceaux = []
                for j, mot in enumerate(mots_groupe):
                    cle = _normaliser(mot) in gros
                    if editorial:
                        if cle:
                            taille = "\\fscx90\\fscy90\\t(0,90,\\fscx132\\fscy132)\\t(90,190,\\fscx120\\fscy120)" if j == rang else "\\fscx118\\fscy118"
                            balise = f"{{\\fn{POLICE_SERIF}\\i1\\c&H00FFFFFF&{normal}{taille}}}"
                        else:
                            taille = "\\fscx86\\fscy86\\t(0,90,\\fscx112\\fscy112)\\t(90,190,\\fscx102\\fscy102)" if j == rang else "\\fscx100\\fscy100"
                            balise = f"{{\\fnPoppins\\i0\\c&H00FFFFFF&{normal}{taille}}}"
                    elif cle and couleur_pastille:
                        taille = "\\fscx82\\fscy82\\t(0,90,\\fscx118\\fscy118)\\t(90,190,\\fscx108\\fscy108)" if j == rang else "\\fscx108\\fscy108"
                        balise = f"{{\\c&H00FFFFFF&\\3c{couleur_pastille}\\bord{contour_pastille}{taille}}}"
                    elif j == rang:
                        balise = f"{{\\c{couleur_accent}{normal}\\fscx82\\fscy82\\t(0,90,\\fscx118\\fscy118)\\t(90,190,\\fscx108\\fscy108)}}"
                    elif cle:
                        balise = f"{{\\c{couleur_accent}{normal}\\fscx122\\fscy122}}"
                    else:
                        balise = f"{{\\c&H00FFFFFF&{normal}\\fscx100\\fscy100}}"
                    if j:
                        morceaux.append("\\N" if j == coupure else " ")
                    morceaux.append(balise + mot)
                fin = max(fins[indice], debuts[indice] + 0.05)
                sorties.append(f"Dialogue: 0,{_horodatage_ass(debuts[indice])},{_horodatage_ass(fin)},Pop,,0,0,0,,{''.join(morceaux)}")
    return sorties


def ecrire_ass(phrases: list, chemin: str, largeur: int, hauteur: int, accent: tuple = (255, 214, 10), titre: str = "",
               pop: bool = False, titres: list = None, mots_gros: set = None, barre: float = 0.0, pastille: tuple = None, editorial: bool = False) -> None:
    """
    Sous-titres, titre d'accroche, titres ajoutes a la main (titres : [(debut, fin, texte)]) et barre de progression (barre : duree
    totale en secondes, 0 = pas de barre). Par defaut sous-titres « karaoke » : les mots se colorent au rythme de la voix ;
    pop=True : style tendance, quelques mots a la fois qui « popent » (voir _dialogues_pop).
    """
    vertical = hauteur > largeur
    taille = 84 if vertical else 60
    taille_pop = 94 if vertical else 66
    contour = 7 if vertical else 5
    marge_v = 430 if vertical else 80
    max_car = 20 if vertical else 38
    max_car_pop = 14 if vertical else 30
    hauteur_barre = 18 if vertical else 12
    lignes = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {largeur}", f"PlayResY: {hauteur}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sous,Poppins,{taille},{_couleur_ass(accent)},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{contour},2,2,60,60,{marge_v},1",
        f"Style: Pop,Poppins,{taille_pop},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{contour + 1},3,2,50,50,{marge_v},1",
        f"Style: Titre,Poppins,{int(taille * 0.9)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{contour},2,8,70,70,{int(hauteur * 0.09)},1",
        f"Style: Barre,Poppins,20,{_couleur_ass(accent)},&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if titre:
        lignes.append(_lignes_titre(0.2, 3.6, titre, vertical))
    for debut, fin, texte in (titres or []):
        lignes.append(_lignes_titre(debut, fin, texte, vertical))
    if pop:
        lignes += _dialogues_pop(phrases, accent, max_car_pop, mots_gros or set(), pastille, contour + 1, editorial)
    else:
        for p in phrases:
            mots = _echapper_ass(p["texte"]).split()
            if not mots:
                continue
            poids = [len(m) + 1 for m in mots]
            duree_cs = max(1, int(round((p["fin"] - p["debut"]) * 100)))
            coupures = set(_lignes_sous_titre(mots, max_car))
            texte, restant_cs, restant_poids = "", duree_cs, sum(poids)
            for i, (mot, poids_mot) in enumerate(zip(mots, poids)):
                cs = max(1, round(restant_cs * poids_mot / restant_poids)) if restant_poids else 1
                restant_cs -= cs
                restant_poids -= poids_mot
                if i in coupures:
                    texte += "\\N"
                texte += f"{{\\k{cs}}}{mot}" + (" " if i < len(mots) - 1 and (i + 1) not in coupures else "")
            lignes.append(f"Dialogue: 0,{_horodatage_ass(p['debut'])},{_horodatage_ass(p['fin'])},Sous,,0,0,0,,{texte}")
    if barre > 0:
        # Rectangle (dessin ASS) ancre a gauche, dont la largeur grandit de 1 % a 100 % pendant toute la video.
        lignes.append(
            f"Dialogue: 3,{_horodatage_ass(0)},{_horodatage_ass(barre)},Barre,,0,0,0,,"
            f"{{\\an7\\pos(0,{hauteur - hauteur_barre})\\fscx1\\t(0,{int(barre * 1000)},\\fscx100)\\p1}}"
            f"m 0 0 l {largeur} 0 {largeur} {hauteur_barre} 0 {hauteur_barre}"
        )
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write("\n".join(lignes) + "\n")


# --------------------------------------------------------------------------- rendu

def _chemin_filtre(chemin: str) -> str:
    return chemin.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def expression_zoom(zooms: list) -> str:
    """
    Facteur de zoom (expression ffmpeg en fonction du temps t) : 1, plus une bosse douce par fenetre (debut, fin, amplitude), ou un
    saut franc sans transition si la fenetre porte un quatrieme element « sec » (cadrage alterne a chaque coupe).
    """
    termes = []
    for fenetre in zooms:
        a, b, amp = fenetre[:3]
        if len(fenetre) > 3 and fenetre[3] == "sec":
            termes.append(f"{amp:.3f}*between(t,{a:.2f},{b:.2f})")
        elif len(fenetre) > 3 and fenetre[3] == "punch":
            termes.append(f"{amp:.3f}*between(t,{a:.2f},{b:.2f})*max(0,1-(t-{a:.2f})/{max(0.05, b - a):.2f})")
        else:
            termes.append(f"{amp:.3f}*between(t,{a:.2f},{b:.2f})*min(1,(t-{a:.2f})/0.20)*min(1,({b:.2f}-t)/0.35)")
    return "1+" + "+".join(termes)


def rendre(source: str, infos: dict, segments: list, sortie: str, format_sortie: str, fichier_ass: str = None,
           musique: str = None, progression=None, zooms: list = None, visage_x: float = None, illustrations: list = None,
           transitions: dict = None, bruitages: list = None, panneau: tuple = None) -> None:
    """
    Assemble les segments de `source`, recadre (en vertical, centre sur le visage si visage_x est connu : 0 = bord gauche, 1 = bord
    droit), applique les zooms (debut, fin, amplitude) sur la chronologie du montage, incruste les illustrations
    (illustrations : [{"debut", "fin", "chemin", "mode"}], temps du montage, mode « plein » ecran ou « incrust » en cadre), puis les
    sous-titres, mixe la musique (baisse sous la voix) et encode. Une illustration marquee « partage » passe en ecran coupe (vertical) :
    panneau uni de couleur `panneau` en haut (la ou s'affiche l'illustration), vous en bas, recadre sur le haut du corps. transitions : {"flash": [(debut, fin)], "glitch": [...], "secousse": [...]}
    (temps du montage) appliquees sur les coupes ; bruitages : [{"t", "fichier", "volume"}] mixes par-dessus la voix.
    """
    if not segments:
        raise ErreurMontage("Il ne reste rien à monter (tout a été coupé).")
    dossier = os.path.dirname(sortie)
    liste = os.path.join(dossier, f"segments-{uuid.uuid4().hex[:6]}.txt")
    chemin_source = source.replace("\\", "/").replace("'", "'\\''")
    with open(liste, "w", encoding="utf-8") as fichier:
        for debut, fin in segments:
            fichier.write(f"file '{chemin_source}'\ninpoint {debut:.3f}\noutpoint {fin:.3f}\n")

    vertical = format_sortie == "vertical"
    largeur, hauteur = (1080, 1920) if vertical else (1920, 1080)
    portrait_source = infos["hauteur"] > infos["largeur"]
    if vertical:
        if portrait_source:
            recadrage = f"scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,crop={largeur}:{hauteur}"
        elif visage_x is not None:
            recadrage = f"crop=w=ih*9/16:h=ih:x='max(0,min(iw-ow,{visage_x:.4f}*iw-ow/2))':y=0,scale={largeur}:{hauteur}"
        else:
            recadrage = f"crop=ih*9/16:ih,scale={largeur}:{hauteur}"
    else:
        recadrage = f"scale={largeur}:{hauteur}:force_original_aspect_ratio=decrease,pad={largeur}:{hauteur}:(ow-iw)/2:(oh-ih)/2:black"
    filtre_video = f"[0:v]{recadrage},setsar=1,fps={FPS}"
    transitions = transitions or {}
    secousses = transitions.get("secousse") or []
    zooms_effectifs = list(zooms or []) + [(a, b, 0.06, "sec") for a, b in secousses]      # une secousse a besoin d'un peu de marge d'image
    if zooms_effectifs:
        facteur = expression_zoom(zooms_effectifs)
        decalage_x = decalage_y = ""
        if secousses:
            decalage_x = "+" + "+".join(f"between(t,{a:.2f},{b:.2f})*{24 if vertical else 30}*sin(70*(t-{a:.2f}))*(1-(t-{a:.2f})/{max(0.05, b - a):.2f})" for a, b in secousses)
            decalage_y = "+" + "+".join(f"between(t,{a:.2f},{b:.2f})*{18 if vertical else 20}*cos(85*(t-{a:.2f}))*(1-(t-{a:.2f})/{max(0.05, b - a):.2f})" for a, b in secousses)
        filtre_video += (f",scale=w='trunc({largeur}*({facteur})/2)*2':h='trunc({hauteur}*({facteur})/2)*2':eval=frame:flags=bilinear,"
                         f"crop={largeur}:{hauteur}:x='(iw-ow)/2{decalage_x}':y='(ih-oh)/2{decalage_y}'")
    if transitions.get("flash"):
        filtre_video += ",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=fill:enable='" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in transitions["flash"]) + "'"
    if transitions.get("glitch"):
        filtre_video += ",rgbashift=rh=-12:bh=12:rv=5:bv=-5:enable='" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in transitions["glitch"]) + "'"
    etiquette, parties = "v0", [filtre_video + "[v0]"]
    partages = [(i["debut"], i["fin"]) for i in (illustrations or []) if i.get("partage")] if vertical else []
    demi = hauteur // 2
    if partages:
        activation = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in partages)
        couleur_panneau = "0x%02X%02X%02X" % tuple(panneau or (243, 241, 236))
        parties += [
            "[v0]split=2[vp][vq]",
            f"[vq]crop={largeur}:{demi}:0:{int(hauteur * PART_HAUT_PARTAGE)}[vb]",
            f"color=c={couleur_panneau}:s={largeur}x{demi}:r={FPS}:d={duree_montee(segments):.3f}[pn]",
            f"[vp][pn]overlay=x=0:y=0:enable='{activation}'[vs1]",
            f"[vs1][vb]overlay=x=0:y={demi}:enable='{activation}'[vs]",
        ]
        etiquette = "vs"
    premiere_image = 2 if musique else 1
    images = []
    for k, illustration in enumerate(illustrations or []):
        debut_i, fin_i = illustration["debut"], illustration["fin"]
        duree_i = max(0.5, fin_i - debut_i)
        genre_entree = "video" if illustration.get("video") else "sequence" if illustration.get("sequence") else "image"
        images.append((illustration["sequence"] if genre_entree == "sequence" else illustration["chemin"], duree_i, genre_entree))
        carte = illustration.get("mode") == "carte"
        partage = bool(illustration.get("partage")) and vertical
        if carte:
            # Carte graphique (PNG transparent a sa taille) : elle « pope » a l'arrivee (de 82 a 100 % en 0,28 s) avant le fondu.
            mise = "scale=w='trunc(iw*(0.82+0.18*min(1,t/0.28))/2)*2':h=-2:eval=frame:flags=bilinear"
            x = "(W-w)/2"
            fraction_bas = 0.65 if vertical else 0.82
            y = {"haut": "H*0.07", "centre": "(H-h)/2", "bas": f"H*{fraction_bas}-h"}.get(illustration.get("place"), "H*0.07")
            if partage:      # centree dans la moitie haute, reduite si elle est plus haute que le panneau
                mise = f"scale='min(iw,{int(largeur * 0.926)})':'min(ih,{int(hauteur * 0.458)})':force_original_aspect_ratio=decrease," + mise
                y = f"({demi}-h)/2"
        elif partage:
            mise = f"scale={int(largeur * 0.86)}:{int(hauteur * 0.42)}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=iw+16:ih+16:8:8:white"
            x, y = "(W-w)/2", f"({demi}-h)/2"
        elif illustration.get("mode") == "plein":
            mise = f"scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,crop={largeur}:{hauteur}"
            x, y = "0", "0"
        else:
            mise = f"scale={int(largeur * 0.78)}:-2,pad=iw+16:ih+16:8:8:white"
            x, y = "(W-w)/2", ("H*0.10" if vertical else "H*0.08")
        # Un plan video est coupe a la duree voulue, remis a zero puis decale a son instant ; une image fixe l'est par son -t d'entree.
        depart = f"fps={FPS},trim=duration={duree_i:.3f},setpts=PTS-STARTPTS," if illustration.get("video") else ""
        if genre_entree == "sequence":      # carte animee : ses images s'enchainent puis la derniere reste affichee jusqu'a la fin
            depart = f"fps={FPS},tpad=stop_mode=clone:stop_duration={duree_i:.3f},trim=duration={duree_i:.3f},setpts=PTS-STARTPTS,"
        parties.append(
            f"[{premiere_image + k}:v]{depart}format=rgba,{mise},fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={max(0.0, duree_i - 0.25):.2f}:d=0.25:alpha=1,"
            f"setpts=PTS+{debut_i:.3f}/TB[i{k}]"
        )
        parties.append(f"[{etiquette}][i{k}]overlay=x={x}:y={y}:enable='between(t,{debut_i:.3f},{fin_i:.3f})'[v{k + 1}]")
        etiquette = f"v{k + 1}"
    if fichier_ass:
        parties.append(f"[{etiquette}]ass='{_chemin_filtre(fichier_ass)}':fontsdir='{_chemin_filtre(DOSSIER_POLICES)}'[v]")
    else:
        parties.append(f"[{etiquette}]null[v]")
    filtre_video = ";".join(parties)

    commande = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
                "-f", "concat", "-safe", "0", "-i", liste]
    if musique:
        commande += ["-stream_loop", "-1", "-i", musique]
    for chemin_image, duree_image, genre_entree in images:
        if genre_entree == "sequence":
            commande += ["-f", "concat", "-safe", "0", "-i", chemin_image]
        elif genre_entree == "video":
            commande += ["-i", chemin_image]
        else:
            commande += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duree_image:.3f}", "-i", chemin_image]
    bruitages = bruitages or []
    sons_distincts = sorted({b["fichier"] for b in bruitages})
    premier_son = premiere_image + len(images)
    for fichier_son in sons_distincts:
        commande += ["-i", fichier_son]
    etiquette_audio = "ab" if bruitages else "a"
    # Avec des bruitages (stereo), la voix passe aussi en stereo AVANT la normalisation : sinon un son source mono perdrait 3 dB au mixage.
    stereo = ",aformat=channel_layouts=stereo" if bruitages else ""
    if infos["audio"] and musique:
        filtre_audio = (f"[0:a]aresample=48000{stereo},asplit=2[v1][v2];[1:a]aresample=48000,volume=0.30[m];"
                        "[m][v1]sidechaincompress=threshold=0.04:ratio=9:attack=30:release=600[md];"
                        f"[v2][md]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=11[{etiquette_audio}]")
    elif infos["audio"]:
        filtre_audio = f"[0:a]aresample=48000{stereo},loudnorm=I=-16:TP=-1.5:LRA=11[{etiquette_audio}]"
    elif musique:
        filtre_audio = f"[1:a]aresample=48000,volume=0.5[{etiquette_audio}]"
    else:
        filtre_audio = ""
    if bruitages:
        # Chaque bruitage est retarde a son instant puis mixe, sans changer le volume de la voix (normalisee avant).
        parties_audio = [filtre_audio] if filtre_audio else []
        emplois = {}
        for i, b in enumerate(bruitages):
            emplois.setdefault(b["fichier"], []).append(i)
        etiquettes = []
        for k, fichier_son in enumerate(sons_distincts):
            n = len(emplois[fichier_son])
            sources = [f"fx{k}_{j}" for j in range(n)]
            entree = f"[{premier_son + k}:a]aresample=48000,aformat=channel_layouts=stereo"
            parties_audio.append(entree + (f",asplit={n}" + "".join(f"[{x}]" for x in sources) if n > 1 else f"[{sources[0]}]"))
            for j, i in enumerate(emplois[fichier_son]):
                ms = int(max(0.0, bruitages[i]["t"]) * 1000)
                parties_audio.append(f"[{sources[j]}]volume={bruitages[i]['volume']:.2f},adelay={ms}|{ms}[sx{i}]")
                etiquettes.append(f"[sx{i}]")
        base = "[ab]" if filtre_audio else ""
        parties_audio.append(f"{base}{''.join(etiquettes)}amix=inputs={len(etiquettes) + (1 if filtre_audio else 0)}:duration={'first' if filtre_audio else 'longest'}:"
                             "dropout_transition=0:normalize=0[a]")
        filtre_audio = ";".join(parties_audio)
    graphe = filtre_video + (";" + filtre_audio if filtre_audio else "")
    commande += ["-filter_complex", graphe, "-map", "[v]"]
    if filtre_audio:
        commande += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    commande += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(FPS),
                 "-t", f"{duree_montee(segments):.3f}", "-movflags", "+faststart", sortie]

    total = duree_montee(segments)
    processus = subprocess.Popen(commande, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    erreurs = []
    lecteur = threading.Thread(target=lambda: erreurs.append(processus.stderr.read()), daemon=True)
    lecteur.start()
    for ligne in processus.stdout:
        if ligne.startswith("out_time_us=") and progression and total:
            try:
                progression(min(1.0, int(ligne.split("=")[1]) / 1_000_000 / total))
            except ValueError:
                pass
    processus.wait()
    lecteur.join(timeout=5)
    try:
        os.remove(liste)
    except OSError:
        pass
    if processus.returncode != 0 or not os.path.exists(sortie):
        detail = (erreurs[0] if erreurs else "")[-400:]
        raise ErreurMontage(f"Le rendu de la vidéo a échoué. {detail}".strip())


# --------------------------------------------------------------------------- style tendance : zooms, cadrage sur le visage

MAX_ZOOMS = 40
ECART_MINI_ZOOM = 3.2           # secondes entre deux zooms automatiques
DUREE_ZOOM = 2.4
AMPLITUDES_ZOOM = (0.10, 0.17)  # alternance : zoom leger, puis plus marque
FORCE_ZOOM = {"leger": 0.10, "fort": 0.20}
EFFETS_DEFAUT = {"zooms": True, "mots": True, "accroche": True, "barre": True, "visage": True}
PART_HAUT_PARTAGE = 0.13        # en ecran coupe, la moitie basse montre le cadre vertical a partir de 13 % de sa hauteur (visage et epaules)


def calculer_zooms(phrases: list, duree: float, mots_cles=()) -> list:
    """
    Fenetres de zoom automatiques (debut, fin, amplitude) sur la chronologie du montage : au debut de certaines phrases, jamais
    pendant l'accroche (1,5 premiere seconde), espacees d'au moins ECART_MINI_ZOOM (davantage sur une longue video), en privilegiant
    les phrases qui contiennent un mot-cle. Alternance de zooms legers et plus marques.
    """
    if duree < 6:
        return []
    cles = {_normaliser(m) for m in mots_cles}
    espacement = max(ECART_MINI_ZOOM, duree / MAX_ZOOMS)
    candidats = []
    for p in phrases:
        if p["fin"] - p["debut"] < 1.0 or p["debut"] < 1.5 or p["debut"] > duree - 1.0:
            continue
        a_cle = any(_normaliser(m) in cles for m in p["texte"].split()) if cles else False
        candidats.append((0 if a_cle else 1, p))
    choisis = []
    for _, p in sorted(candidats, key=lambda c: (c[0], c[1]["debut"])):
        if len(choisis) >= MAX_ZOOMS:
            break
        if all(abs(p["debut"] - c["debut"]) >= espacement for c in choisis):
            choisis.append(p)
    choisis.sort(key=lambda p: p["debut"])
    return [(p["debut"], min(p["debut"] + DUREE_ZOOM, p["fin"] + 0.4, duree), AMPLITUDES_ZOOM[i % 2]) for i, p in enumerate(choisis)]


PROMPT_VISAGE = (
    "Pour chacune de ces {n} images (extraites d'une meme video), donne la position horizontale du centre du visage de la personne "
    "qui parle, entre 0 (bord gauche de l'image) et 1 (bord droit). Mets null si aucun visage n'est visible. Reponds uniquement "
    "par un tableau JSON de {n} valeurs, par exemple [0.42, 0.45, null]."
)


def position_visage(source: str, infos: dict, dossier: str):
    """Position horizontale moyenne du visage (0 a 1, mediane de 3 images) pour cadrer le format vertical ; None si introuvable."""
    try:
        from google import genai
        from google.genai import types

        cle = os.getenv("GEMINI_API_KEY")
        if not cle or infos["duree"] < 3:
            return None
        parties = []
        for rang, fraction in enumerate((0.2, 0.5, 0.8)):
            image = os.path.join(dossier, f"visage-{rang}.jpg")
            resultat = _lancer([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{infos['duree'] * fraction:.2f}", "-i", source,
                                "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5", image], 120)
            if resultat.returncode == 0 and os.path.exists(image):
                with open(image, "rb") as fichier:
                    parties.append(types.Part.from_bytes(data=fichier.read(), mime_type="image/jpeg"))
        if not parties:
            return None
        client = genai.Client(api_key=cle)
        reponse = client.models.generate_content(
            model=MODELE_TRANSCRIPTION, contents=parties + [PROMPT_VISAGE.format(n=len(parties))],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
        valeurs = sorted(float(v) for v in _json_depuis_texte(reponse.text) if isinstance(v, (int, float)) and 0 <= v <= 1)
        return max(0.15, min(0.85, valeurs[len(valeurs) // 2])) if valeurs else None
    except Exception:
        return None


# --------------------------------------------------------------------------- revision : conversions de temps et actions

def montage_vers_source(instant: float, segments: list, fin_de_plage: bool = False) -> float:
    """Instant du montage -> instant de la video d'origine (borne au debut/fin du montage)."""
    cumul = 0.0
    for debut, fin in segments:
        longueur = fin - debut
        if instant < cumul + longueur or (fin_de_plage and instant <= cumul + longueur):
            return debut + max(0.0, instant - cumul)
        cumul += longueur
    return segments[-1][1] if segments else 0.0


def soustraire_intervalles(segments: list, retirer: list) -> list:
    """Segments sans les intervalles retires (en temps de la video d'origine)."""
    restants = []
    for debut, fin in segments:
        morceaux = [(debut, fin)]
        for r_debut, r_fin in retirer:
            suivants = []
            for m_debut, m_fin in morceaux:
                if r_fin <= m_debut or r_debut >= m_fin:
                    suivants.append((m_debut, m_fin))
                    continue
                if r_debut > m_debut:
                    suivants.append((m_debut, r_debut))
                if r_fin < m_fin:
                    suivants.append((r_fin, m_fin))
            morceaux = suivants
        restants += morceaux
    return [(a, b) for a, b in restants if b - a >= DUREE_MINI_SEGMENT]


def _mmss(secondes: float) -> str:
    secondes = int(round(max(0.0, secondes)))
    return f"{secondes // 60}:{secondes % 60:02d}"


def appliquer_actions(sortie: dict, actions: list) -> tuple:
    """
    Applique les actions issues des notes de revision (temps en secondes sur le montage actuel) a une COPIE de l'etat d'une sortie.
    Renvoie (nouvel etat, resume des modifications, motifs des actions refusees). Les temps sont tous convertis avec le montage
    de depart avant la premiere modification, pour que les notes restent coherentes entre elles.
    """
    segments_depart = list(sortie["segments"])
    segments = list(segments_depart)
    effets = copy.deepcopy(sortie["effets"])
    phrases = copy.deepcopy(sortie["phrases"])
    duree = duree_montee(segments_depart)
    resume, refus = [], []
    for action in actions:
        genre = action.get("type")
        debut_m = max(0.0, min(float(action.get("debut") or 0), duree))
        fin_m = max(0.0, min(float(action.get("fin") or 0), duree))
        debut_s = montage_vers_source(debut_m, segments_depart)
        fin_s = montage_vers_source(fin_m, segments_depart, True)
        texte = " ".join(str(action.get("texte") or "").split())
        if genre == "couper":
            if fin_m - debut_m < 0.2:
                refus.append(f"Coupe trop courte à {_mmss(debut_m)}.")
                continue
            restants = soustraire_intervalles(segments, [(debut_s, fin_s)])
            if not restants:
                refus.append("Cette coupe retirerait toute la vidéo.")
                continue
            segments = restants
            resume.append(f"Coupé de {_mmss(debut_m)} à {_mmss(fin_m)}")
        elif genre == "zoom":
            if fin_m - debut_m < 0.5:
                fin_s = min(debut_s + 2.5, segments_depart[-1][1])
            effets["zoom_manuel"].append((debut_s, min(fin_s, debut_s + 8.0), FORCE_ZOOM.get(action.get("force"), FORCE_ZOOM["leger"])))
            resume.append(f"Zoom ajouté à {_mmss(debut_m)}")
        elif genre == "sans_zoom":
            effets["sans_zoom"].append((debut_s, max(fin_s, debut_s + 0.5)))
            effets["zoom_manuel"] = [z for z in effets["zoom_manuel"] if z[1] <= debut_s or z[0] >= max(fin_s, debut_s + 0.5)]
            resume.append(f"Zoom retiré vers {_mmss(debut_m)}")
        elif genre == "titre":
            if not texte:
                refus.append("Titre sans texte.")
                continue
            effets["titres"].append((debut_s, debut_s + (max(fin_s - debut_s, 0) if fin_m - debut_m >= 1.0 else 3.0), texte[:80]))
            resume.append(f"Titre ajouté à {_mmss(debut_m)} : « {texte[:50]} »")
        elif genre == "mot_en_gros":
            mots = [_normaliser(m) for m in texte.split() if _normaliser(m)]
            if not mots:
                refus.append("Mot à mettre en valeur introuvable dans la note.")
                continue
            effets["mots_gros"] = sorted(set(effets["mots_gros"]) | set(mots))
            resume.append("Mis en valeur : " + ", ".join(texte.split()))
        elif genre == "corriger_texte":
            ancien = " ".join(str(action.get("ancien") or "").split())
            if not ancien or not texte:
                refus.append("Correction de sous-titre incomplète.")
                continue
            motif = re.compile(re.escape(ancien), re.IGNORECASE)
            proches = sorted((p for p in phrases if motif.search(p["texte"])), key=lambda p: abs((p["debut"] + p["fin"]) / 2 - debut_s))
            if not proches or abs((proches[0]["debut"] + proches[0]["fin"]) / 2 - debut_s) > 15:
                refus.append(f"Texte « {ancien} » introuvable près de {_mmss(debut_m)}.")
                continue
            proches[0]["texte"] = motif.sub(lambda _: texte, proches[0]["texte"], count=1)
            resume.append(f"Sous-titre corrigé : « {ancien} » devient « {texte} »")
        else:
            refus.append("Action non reconnue.")
    nouvel = {**sortie, "segments": segments, "effets": effets, "phrases": phrases}
    return nouvel, resume, refus


TRANSITIONS = {"aucune": "Aucune", "melange": "Variées (flash, zoom, glitch, secousse)", "flash": "Flash blanc", "zoom": "Zoom-punch", "glitch": "Glitch", "secousse": "Secousse"}
_DUREE_TRANSITION = {"flash": 0.12, "zoom": 0.35, "glitch": 0.20, "secousse": 0.30}
_ORDRE_MELANGE = ("flash", "zoom", "glitch", "secousse")
ECART_MINI_TRANSITION = 1.5
MAX_TRANSITIONS = 60
VOLUMES_SONS = {"whoosh": 0.55, "pop": 0.70, "impact": 0.65, "clic": 0.60}


def fenetres_transitions(segments: list, mode: str, duree: float) -> dict:
    """
    Fenetres (debut, fin) du montage ou placer un effet de transition sur une coupe : {"flash": [...], "zoom": [...], "glitch": [...],
    "secousse": [...]}. Pas plus d'une transition toutes les ECART_MINI_TRANSITION secondes, pour ne pas surcharger.
    """
    resultat = {cle: [] for cle in _ORDRE_MELANGE}
    if mode not in TRANSITIONS or mode == "aucune":
        return resultat
    cumul, derniere, total = 0.0, -10.0, 0
    for rang, (debut, fin) in enumerate(segments):
        if rang > 0 and cumul - derniere >= ECART_MINI_TRANSITION and total < MAX_TRANSITIONS and cumul < duree - 0.3:
            genre = _ORDRE_MELANGE[total % len(_ORDRE_MELANGE)] if mode == "melange" else mode
            resultat[genre].append((cumul, min(cumul + _DUREE_TRANSITION[genre], duree)))
            derniere, total = cumul, total + 1
        cumul += fin - debut
    return resultat


def evenements_sons(zooms: list, titres: list, illustrations: list, accroche: bool, fenetres: dict) -> list:
    """
    Bruitages a poser sur le montage : [{"t", "fichier", "volume", "son"}] (temps du montage). Un « whoosh » sur chaque zoom doux, un « pop »
    sur chaque titre et illustration, un impact sur l'accroche, un son adapte a chaque transition. Au plus un son tous les 0,25 s.
    """
    brut = []
    for fenetre in zooms:
        if len(fenetre) == 3 or fenetre[3] == "doux":
            brut.append((fenetre[0], "whoosh"))
    brut += [(t[0], "pop") for t in titres]
    brut += [(i["debut"], "pop") for i in illustrations]
    if accroche:
        brut.append((0.25, "impact"))
    for genre, son in (("flash", "clic"), ("zoom", "whoosh"), ("glitch", "clic"), ("secousse", "impact")):
        brut += [(a, son) for a, _ in fenetres.get(genre, [])]
    evenements, dernier = [], -1.0
    for t, nom in sorted(brut):
        if t - dernier < 0.25:
            continue
        evenements.append({"t": round(t, 3), "son": nom, "fichier": sons.chemin_son(nom), "volume": VOLUMES_SONS[nom]})
        dernier = t
    return evenements[:80]


def _instant_montage(instant: float, segments: list, duree: float) -> float:
    """Instant de la video d'origine -> instant du montage ; un instant dans une partie coupee tombe sur la jonction, apres la fin sur la duree."""
    resultat = _remapper(instant, segments, True)
    return duree if resultat is None else resultat


def zooms_du_rendu(sortie: dict, phrases_montage: list, duree: float) -> list:
    """Fenetres de zoom (chronologie du montage) : automatiques sauf zones « sans zoom », plus les zooms demandes a la main."""
    effets, segments = sortie["effets"], sortie["segments"]
    if effets.get("alterne"):
        # Cadrage alterne a chaque coupe : un morceau sur deux est legerement zoome, sans transition (rythme « jump cut »).
        zooms, cumul = [], 0.0
        for rang, (debut, fin) in enumerate(segments):
            if rang % 2 == 1:
                zooms.append((cumul, cumul + fin - debut, 0.12, "sec"))
            cumul += fin - debut
        for debut, fin, amplitude in effets["zoom_manuel"]:
            a, b = _remapper(debut, segments, True), _remapper(fin, segments, False)
            if a is not None and b is not None and b - a >= 0.4:
                zooms.append((a, min(b, duree), amplitude))
        return sorted(zooms)
    interdits = []
    for debut, fin in effets["sans_zoom"]:
        a, b = _remapper(debut, segments, True), _remapper(fin, segments, False)
        if a is not None and b is not None:
            interdits.append((a, b))
    zooms = []
    if effets["actif"]["zooms"]:
        zooms = [z for z in calculer_zooms(phrases_montage, duree, list(effets["mots_cles"]) + list(effets["mots_gros"]))
                 if not any(z[0] < b and z[1] > a for a, b in interdits)]
    for debut, fin, amplitude in effets["zoom_manuel"]:
        a, b = _remapper(debut, segments, True), _remapper(fin, segments, False)
        if a is not None and b is not None and b - a >= 0.4:
            zooms = [z for z in zooms if not (z[0] < b and z[1] > a)] + [(a, min(b, duree), amplitude)]
    return sorted(zooms)[:MAX_ZOOMS + 10]


def plan_effets(sortie: dict) -> dict:
    """
    Tout ce qui est applique a une sortie, sur la chronologie du MONTAGE (apres coupes) : zooms (y compris les « punch » des transitions),
    titres, illustrations, fenetres de transition, bruitages, et les reglages de sous-titres. Sert au rendu comme a l'apercu de l'editeur.
    """
    segments, effets = sortie["segments"], sortie["effets"]
    duree = duree_montee(segments)
    phrases_montage = phrases_sur_montage(sortie["phrases"], segments)
    zooms = zooms_du_rendu(sortie, phrases_montage, duree)
    titres = []
    for debut, fin, texte in effets["titres"]:
        a, b = _remapper(debut, segments, True), _remapper(fin, segments, False)
        if a is not None and b is not None and b - a >= 0.5:
            titres.append((a, b, texte))
    pop = effets["style"] == "tendance" and effets["actif"]["mots"]
    gros = {_normaliser(m) for m in list(effets["mots_cles"]) + list(effets["mots_gros"])} if pop else {_normaliser(m) for m in effets["mots_gros"]}
    barre = duree if effets["style"] == "tendance" and effets["actif"]["barre"] else 0.0
    titre = effets["titre"] if (effets["actif"]["accroche"] or sortie["mode"] == "shorts") else ""
    fenetres = fenetres_transitions(segments, effets.get("transition", "aucune"), duree)
    zooms = sorted(list(zooms) + [(a, b, 0.10, "punch") for a, b in fenetres["zoom"]])
    illustrations = []
    vertical = sortie["format"] == "vertical"
    for illustration in effets.get("illustrations", []):
        a, b = _instant_montage(illustration["debut"], segments, duree), _instant_montage(illustration["fin"], segments, duree)
        fichier = illustration.get("fichier_video") if illustration.get("kind") == "video" else illustration["fichier"]
        if b - a >= 0.5 and fichier and os.path.exists(fichier):
            mode, place = illustration.get("mode", "incrust"), illustration.get("place", "haut")
            partage = vertical and mode != "plein" and (bool(effets.get("partage")) or mode == "partage" or place == "partage")
            if mode == "partage":
                mode = "incrust"
            if place == "partage":
                place = "haut"
            sequence = illustration.get("sequence")
            illustrations.append({"id": illustration.get("id"), "debut": a, "fin": b, "chemin": fichier, "mode": mode, "video": illustration.get("kind") == "video",
                                  "place": place, "partage": partage, "sequence": sequence if sequence and os.path.exists(sequence) else None})
    bruitages = evenements_sons(zooms, titres, illustrations, effets["actif"]["accroche"] and bool(effets["titre"]), fenetres) if effets.get("bruitages") else []
    charte = charte_montage.nettoyer(effets.get("charte"))
    pastille = charte_montage.rgb(charte["principale"]) if pop and effets.get("pastille") else None
    fond, texte = charte_montage.rgb(charte["fond"]), charte_montage.rgb(charte["texte"])
    panneau = tuple(int(round(f + (t - f) * 0.06)) for f, t in zip(fond, texte))      # fond de la charte, tres legerement teinte : les cartes blanches ressortent
    return {"duree": duree, "phrases": phrases_montage, "zooms": zooms, "titres": titres, "pop": pop, "gros": gros, "barre": barre, "titre": titre, "pastille": pastille,
            "fenetres": fenetres, "illustrations": illustrations, "bruitages": bruitages, "panneau": panneau, "editorial": bool(pop and effets.get("editorial"))}


def _rendre_sortie(montage: dict, sortie: dict, suivi) -> tuple:
    """Rend (ou re-rend) une sortie depuis la video d'origine et son etat (segments, effets, phrases) : (chemin du fichier, duree)."""
    infos, segments = montage["infos"], sortie["segments"]
    largeur, hauteur = (1080, 1920) if sortie["format"] == "vertical" else (1920, 1080)
    plan = plan_effets(sortie)
    duree, phrases_montage, barre, titre = plan["duree"], plan["phrases"], plan["barre"], plan["titre"]
    dessiner = bool(phrases_montage and sortie["sous_titres"]) or bool(plan["titres"]) or barre > 0 or (titre and sortie["sous_titres"])
    fichier_ass = None
    if dessiner:
        fichier_ass = os.path.join(montage["dossier"], f"sous-titres-{uuid.uuid4().hex[:6]}.ass")
        ecrire_ass(phrases_montage if sortie["sous_titres"] else [], fichier_ass, largeur, hauteur, sortie["accent"], titre if sortie["sous_titres"] else "",
                   pop=plan["pop"], titres=plan["titres"], mots_gros=plan["gros"], barre=barre, pastille=plan["pastille"], editorial=plan["editorial"])
    chemin = os.path.join(montage["dossier"], f"montage-{uuid.uuid4().hex[:6]}-{sortie['numero']}.mp4")
    options = dict(zooms=plan["zooms"], visage_x=sortie.get("visage_x"), illustrations=plan["illustrations"], transitions=plan["fenetres"], bruitages=plan["bruitages"], panneau=plan["panneau"])
    try:
        rendre(montage["source"], infos, segments, chemin, sortie["format"], fichier_ass, sortie["musique"], suivi, **options)
    except ErreurMontage:
        if not fichier_ass:
            raise
        # Incrustation des sous-titres impossible sur ce serveur (ffmpeg sans libass...) : montage sans eux.
        rendre(montage["source"], infos, segments, chemin, sortie["format"], None, sortie["musique"], suivi, **options)
        montage["avertissement"] = "Les sous-titres n'ont pas pu être incrustés sur ce serveur : montage livré sans sous-titres."
    return chemin, duree


def _figer_etat_rendu(sortie: dict) -> None:
    """Memorise l'etat (coupes, effets, textes) qui correspond au fichier video actuel : l'editeur modifie l'etat avant le rendu."""
    sortie["etat_rendu"] = {"segments": list(sortie["segments"]), "effets": copy.deepcopy(sortie["effets"]), "phrases": copy.deepcopy(sortie["phrases"]),
                            "sous_titres": sortie["sous_titres"]}


def _version_rendue(sortie: dict) -> dict:
    """Instantane de la version video actuelle (fichier + l'etat qui l'a produite), pour pouvoir y revenir."""
    rendu = sortie.get("etat_rendu") or {"segments": list(sortie["segments"]), "effets": copy.deepcopy(sortie["effets"]), "phrases": copy.deepcopy(sortie["phrases"]),
                                         "sous_titres": sortie["sous_titres"]}
    return {"chemin": sortie["chemin"], "duree": sortie["duree"], "version": sortie["version"], "resume": sortie["resume"], **copy.deepcopy(rendu)}


def annuler_revision(identifiant: str, numero: int) -> bool:
    """Revient a la version precedente d'une sortie (annule la derniere revision) ; False s'il n'y en a pas."""
    montage = MONTAGES.get(identifiant)
    if not montage or montage["etat"] == "en_cours" or not (0 <= numero < len(montage["sorties"])):
        return False
    sortie = montage["sorties"][numero]
    if not sortie["historique"]:
        return False
    precedente = sortie["historique"].pop()
    sortie.update(precedente)
    sortie["annulations"] = []
    _figer_etat_rendu(sortie)
    return True


def lancer_revision(identifiant: str, numero: int, notes: list, interpreter) -> None:
    """
    Applique les notes de relecture (« à 0:12, zoom ici », « enlève cette phrase »...) a une sortie et la re-rend depuis la video
    d'origine, sans rien retranscrire. interpreter(notes, phrases_montage, duree) -> (actions, ignorees) est fourni par main.py (Claude).
    notes : [{"t": secondes sur le montage, "texte": ...}]. La version precedente reste disponible (annuler_revision).
    """
    montage = MONTAGES[identifiant]
    montage.update(etat="en_cours", etape="Lecture de vos notes", progression=0.05, erreur="", avertissement="")

    def travail():
        try:
            sortie = montage["sorties"][numero]
            phrases_montage = phrases_sur_montage(sortie["phrases"], sortie["segments"])
            actions, ignorees = interpreter(notes, phrases_montage, duree_montee(sortie["segments"]))
            nouvel, resume, refus = appliquer_actions(sortie, actions)
            motifs = [f"Non appliqué : {m}" for m in list(ignorees) + refus]
            if not resume:
                raise ErreurMontage("Aucune de vos notes n'a pu être appliquée. " + " ".join(motifs)[:500])
            montage.update(etape="Nouveau rendu de la vidéo", progression=0.2)
            suivi = lambda p: montage.__setitem__("progression", 0.2 + 0.8 * p)
            chemin, duree = _rendre_sortie(montage, nouvel, suivi)
            sortie["historique"] = (sortie["historique"] + [_version_rendue(sortie)])[-5:]
            sortie.update(segments=nouvel["segments"], effets=nouvel["effets"], phrases=nouvel["phrases"], chemin=chemin, duree=round(duree, 1),
                          version=sortie["version"] + 1, resume=resume + motifs, annulations=[])
            _figer_etat_rendu(sortie)
            montage.update(etat="termine", etape="Terminé", progression=1.0)
        except Exception as erreur:  # la version precedente reste intacte et affichee
            montage.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()


# --------------------------------------------------------------------------- montages en tache de fond

MONTAGES: dict = {}
_VERROU = threading.Lock()


def _nettoyer_anciens() -> None:
    limite = time.time() - DUREE_CONSERVATION_HEURES * 3600
    with _VERROU:
        for identifiant in [i for i, m in MONTAGES.items() if m["cree"] < limite]:
            shutil.rmtree(os.path.join(DOSSIER_MONTAGES, identifiant), ignore_errors=True)
            MONTAGES.pop(identifiant, None)
    if os.path.isdir(DOSSIER_MONTAGES):
        for nom in os.listdir(DOSSIER_MONTAGES):
            chemin = os.path.join(DOSSIER_MONTAGES, nom)
            if nom not in MONTAGES and os.path.isdir(chemin) and os.path.getmtime(chemin) < limite:
                shutil.rmtree(chemin, ignore_errors=True)


def nouveau_dossier_montage() -> tuple:
    _nettoyer_anciens()
    identifiant = uuid.uuid4().hex[:12]
    dossier = os.path.join(DOSSIER_MONTAGES, identifiant)
    os.makedirs(dossier, exist_ok=True)
    return identifiant, dossier


def etat_montage(identifiant: str):
    montage = MONTAGES.get(identifiant)
    if not montage:
        return None
    etat = {k: montage[k] for k in ("etat", "etape", "progression", "erreur", "avertissement")}
    etat["sorties"] = [{"titre": o["titre"], "duree": o["duree"], "version": o["version"], "resume": o["resume"], "peut_annuler": bool(o["historique"])}
                       for o in montage["sorties"]]
    return etat


def fichier_sortie(identifiant: str, numero: int):
    montage = MONTAGES.get(identifiant)
    if not montage or not (0 <= numero < len(montage["sorties"])):
        return None
    chemin = montage["sorties"][numero]["chemin"]
    return chemin if os.path.exists(chemin) else None


def lancer_montage(identifiant: str, dossier: str, source: str, options: dict, choisir_retouches, choisir_extraits, choisir_tendance=None,
                   aligner_script=None, preparer_sortie=None) -> None:
    """
    Demarre (ou relance) un montage en tache de fond. options : silences, hesitations, sous_titres, format ("vertical" /
    "horizontal"), musique (chemin ou None), mode ("montage" / "shorts"), nb_shorts, duree_max_short, consignes, accent, style
    ("tendance" / "sobre"), effets (zooms, mots, accroche, barre, visage : booleens, tous actifs par defaut).
    choisir_retouches(phrases, consignes) -> [(debut, fin)] a retirer ; choisir_extraits(phrases, nb, duree_min, duree_max)
    -> [{"debut", "fin", "titre"}] ; choisir_tendance(phrases) -> {"accroche", "mots_cles"} (style tendance) : fournis par main.py
    (Claude), pour garder ce module independant de l'IA. Script lie (options["script"]) : aligner_script(phrases, script) ->
    {"textes", "retirer", "avertissement"} corrige les sous-titres d'apres le script et retire les prises ratees ; preparer_sortie(montage, sortie)
    est appele sur chaque sortie avant son rendu (options["cartes_auto"] : cartes proposees par l'IA).
    """
    montage = MONTAGES.setdefault(identifiant, {"cree": time.time(), "source": source, "dossier": dossier, "transcription": None,
                                                "silences": None, "infos": None, "audio": None, "visage_x": "inconnu", "verrou": threading.RLock()})
    montage.update(etat="en_cours", etape="Analyse de la vidéo", progression=0.0, erreur="", sorties=[], avertissement="")
    if options.get("libelle"):
        montage["libelle"] = str(options["libelle"])[:120]

    def fixer(etape, progression=None):
        montage["etape"] = etape
        if progression is not None:
            montage["progression"] = progression

    def travail():
        nonlocal source
        try:
            if source.lower().endswith(".webm"):
                fixer("Préparation de l'enregistrement", 0.01)
                source = montage["source"] = normaliser_enregistrement(source)
            if montage["infos"] is None:
                montage["infos"] = sonde(source)
            infos = montage["infos"]
            if infos["duree"] > DUREE_MAX_SOURCE:
                raise ErreurMontage(f"Vidéo trop longue ({int(infos['duree'] // 60)} min, {DUREE_MAX_SOURCE // 60} min maximum).")
            if not infos["audio"]:
                raise ErreurMontage("Cette vidéo n'a pas de son : rien à transcrire ni à couper.")
            if montage["audio"] is None:
                fixer("Extraction du son", 0.03)
                audio = os.path.join(dossier, "audio.wav")
                extraire_audio(source, audio)
                montage["audio"] = audio
                fixer("Détection des silences", 0.08)
                montage["silences"] = detecter_silences(audio)
            if montage["transcription"] is None:
                fixer("Transcription de votre voix", 0.12)
                montage["transcription"] = transcrire(
                    montage["audio"], infos["duree"], montage["silences"], lambda p: fixer("Transcription de votre voix", 0.12 + 0.28 * p),
                )
            script = (options.get("script") or "").strip()
            if montage.get("transcription_brute") is not None and montage.get("script_cle") != script:
                montage["transcription"] = copy.deepcopy(montage["transcription_brute"])        # un autre script (ou plus de script) : on repart de la transcription brute
                montage["script_cle"], montage["retirer_script"], montage["avert_script"] = None, [], ""
            phrases = montage["transcription"]
            if not phrases:
                montage["avertissement"] = "Aucune parole détectée : montage sans sous-titres."
            retirer = []
            if script and phrases and aligner_script:
                if montage.get("script_cle") != script:
                    fixer("Alignement sur votre script", 0.41)
                    brute = montage.get("transcription_brute")
                    if brute is None:
                        brute = montage["transcription_brute"] = copy.deepcopy(phrases)
                    try:
                        alignement = aligner_script(brute, script)
                        corrigees = copy.deepcopy(brute)
                        for p, texte in zip(corrigees, alignement["textes"]):
                            p["texte"] = texte
                        montage["transcription"] = corrigees
                        montage["retirer_script"] = [(brute[i]["debut"], brute[i]["fin"]) for i in alignement["retirer"] if 0 <= i < len(brute)]
                        montage["avert_script"] = alignement.get("avertissement") or ""
                        montage["script_cle"] = script
                    except Exception:
                        montage["avert_script"] = "Le script n'a pas pu être utilisé (l'IA n'a pas répondu) : montage fait sans lui."
                phrases = montage["transcription"]
                retirer += montage.get("retirer_script") or []
                if montage.get("avert_script"):
                    montage["avertissement"] = montage["avert_script"]

            if options.get("hesitations"):
                retirer += [(p["debut"], p["fin"]) for p in phrases if p.get("hesitation")]
            if (options.get("consignes") or "").strip() and phrases:
                fixer("Application de vos consignes", 0.42)
                retirer += choisir_retouches(phrases, options["consignes"].strip())

            sorties = []
            format_sortie = options.get("format", "vertical")
            accent = options.get("accent") or (255, 214, 10)
            style = "sobre" if options.get("style") == "sobre" else "tendance"
            actifs = {cle: bool((options.get("effets") or {}).get(cle, valeur)) for cle, valeur in EFFETS_DEFAUT.items()}

            if options.get("mode") == "shorts":
                fixer("Choix des meilleurs extraits", 0.45)
                extraits = choisir_extraits(phrases, int(options.get("nb_shorts") or 3), 25.0, float(options.get("duree_max_short") or 90))
                if not extraits:
                    raise ErreurMontage("Aucun extrait exploitable n'a été trouvé dans cette vidéo.")
                travaux = [(e, segments_a_garder(infos["duree"], montage["silences"], options.get("silences", True), retirer, e["debut"], e["fin"])) for e in extraits]
            else:
                travaux = [({"titre": "Montage complet"}, segments_a_garder(infos["duree"], montage["silences"], options.get("silences", True), retirer))]

            visage_x = None
            if style == "tendance" and actifs["visage"] and format_sortie == "vertical" and infos["largeur"] > infos["hauteur"]:
                if montage.get("visage_x") == "inconnu":
                    fixer("Repérage de votre visage pour le cadrage", 0.46)
                    montage["visage_x"] = position_visage(source, infos, dossier)
                visage_x = montage["visage_x"] if isinstance(montage["visage_x"], float) else None

            for numero, (extrait, segments) in enumerate(travaux, start=1):
                base = 0.5 + 0.5 * (numero - 1) / len(travaux)
                fixer(f"Montage {numero}/{len(travaux)}" if len(travaux) > 1 else "Montage de la vidéo", base)
                accroche, mots_cles = "", []
                phrases_extrait = phrases_sur_montage(phrases, segments)
                if style == "tendance" and choisir_tendance and phrases_extrait and (actifs["accroche"] or actifs["mots"] or actifs["zooms"]):
                    try:
                        analyse = choisir_tendance(phrases_extrait)
                        accroche, mots_cles = str(analyse.get("accroche") or "")[:90], [str(m) for m in (analyse.get("mots_cles") or [])][:14]
                    except Exception:
                        montage["avertissement"] = "L'accroche et les mots-clés automatiques n'ont pas pu être générés : montage livré sans eux."
                sortie = {
                    "numero": numero, "titre": extrait["titre"], "mode": options.get("mode") or "montage", "format": format_sortie, "accent": accent,
                    "musique": options.get("musique"), "sous_titres": bool(options.get("sous_titres", True)), "visage_x": visage_x,
                    "segments": segments, "phrases": copy.deepcopy(phrases), "version": 1, "resume": [], "historique": [],
                    "effets": {"style": style, "actif": actifs, "titre": extrait["titre"] if options.get("mode") == "shorts" else accroche,
                               "mots_cles": mots_cles, "mots_gros": [], "zoom_manuel": [], "sans_zoom": [], "titres": [], "illustrations": [],
                               "alterne": bool(options.get("alterne", False)), "bruitages": bool(options.get("bruitages", style == "tendance")),
                               "pastille": bool(options.get("pastille", style == "tendance")), "charte": charte_montage.nettoyer(options.get("charte")),
                               "editorial": bool(options.get("editorial", False)), "partage": bool(options.get("partage", False)),
                               "transition": options.get("transition") if options.get("transition") in TRANSITIONS else ("melange" if style == "tendance" else "aucune")},
                }
                if options.get("cartes_auto") and preparer_sortie:
                    fixer("Création des cartes", base)
                    try:
                        preparer_sortie(montage, sortie)
                    except Exception:
                        montage["avertissement"] = (montage.get("avertissement") or "") + " Les cartes automatiques n'ont pas pu être créées : ajoutez-les depuis l'éditeur."
                suivi = lambda p, b=base, n=len(travaux): montage.__setitem__("progression", b + 0.5 * p / n)
                chemin, duree = _rendre_sortie(montage, sortie, suivi)
                sortie.update(chemin=chemin, duree=round(duree, 1), annulations=[])
                _figer_etat_rendu(sortie)
                sorties.append(sortie)
                montage["sorties"] = list(sorties)
            montage.update(etat="termine", etape="Terminé", progression=1.0)
        except Exception as erreur:  # rien ne doit rester bloque sur « en cours »
            montage.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
