"""
Montage automatique d'une video face camera (« talking head ») : transcription (Gemini), coupe des silences et des
hesitations, retouches demandees en langage naturel et choix d'extraits pour des Shorts (Claude, voir
claude_generation.py), sous-titres incrustes a la volee (ASS), musique de fond avec baisse automatique sous la voix,
rendu via ffmpeg (binaire externe, voir montage_video.py et nixpacks.toml).

Les montages tournent en tache de fond (un thread par montage, etat garde en memoire et fichiers dans un dossier
temporaire du serveur) : la page interroge l'etat toutes les quelques secondes. Rien n'est conserve durablement :
un redemarrage du serveur ou plus de DUREE_CONSERVATION_HEURES supprime les montages.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

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


def ecrire_ass(phrases: list, chemin: str, largeur: int, hauteur: int, accent: tuple = (255, 214, 10), titre: str = "") -> None:
    """Sous-titres « karaoke » : les mots se colorent au rythme de la voix (duree repartie selon la longueur des mots)."""
    vertical = hauteur > largeur
    taille = 84 if vertical else 60
    contour = 7 if vertical else 5
    marge_v = 430 if vertical else 80
    max_car = 20 if vertical else 38
    lignes = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {largeur}", f"PlayResY: {hauteur}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sous,Poppins,{taille},{_couleur_ass(accent)},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{contour},2,2,60,60,{marge_v},1",
        f"Style: Titre,Poppins,{int(taille * 0.9)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{contour},2,8,70,70,{int(hauteur * 0.09)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if titre:
        # Pas de retour a la ligne automatique (WrapStyle 2) : le titre est coupe ici, sur 3 lignes au plus.
        titre_lignes = textwrap.wrap(_echapper_ass(titre).upper(), width=18 if vertical else 34)[:3]
        lignes.append(f"Dialogue: 1,{_horodatage_ass(0.2)},{_horodatage_ass(3.6)},Titre,,0,0,0,,{{\\fad(250,350)}}" + "\\N".join(titre_lignes))
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
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write("\n".join(lignes) + "\n")


# --------------------------------------------------------------------------- rendu

def _chemin_filtre(chemin: str) -> str:
    return chemin.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def rendre(source: str, infos: dict, segments: list, sortie: str, format_sortie: str, fichier_ass: str = None,
           musique: str = None, progression=None) -> None:
    """Assemble les segments de `source`, recadre, incruste les sous-titres, mixe la musique (baisse sous la voix) et encode."""
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
        else:
            recadrage = f"crop=ih*9/16:ih,scale={largeur}:{hauteur}"
    else:
        recadrage = f"scale={largeur}:{hauteur}:force_original_aspect_ratio=decrease,pad={largeur}:{hauteur}:(ow-iw)/2:(oh-ih)/2:black"
    filtre_video = f"[0:v]{recadrage},setsar=1,fps={FPS}"
    if fichier_ass:
        filtre_video += f",ass='{_chemin_filtre(fichier_ass)}':fontsdir='{_chemin_filtre(DOSSIER_POLICES)}'"
    filtre_video += "[v]"

    commande = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
                "-f", "concat", "-safe", "0", "-i", liste]
    if musique:
        commande += ["-stream_loop", "-1", "-i", musique]
    if infos["audio"] and musique:
        filtre_audio = ("[0:a]aresample=48000,asplit=2[v1][v2];[1:a]aresample=48000,volume=0.30[m];"
                        "[m][v1]sidechaincompress=threshold=0.04:ratio=9:attack=30:release=600[md];"
                        "[v2][md]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    elif infos["audio"]:
        filtre_audio = "[0:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    elif musique:
        filtre_audio = "[1:a]aresample=48000,volume=0.5[a]"
    else:
        filtre_audio = ""
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
    etat["sorties"] = [{"titre": o["titre"], "duree": o["duree"]} for o in montage["sorties"]]
    return etat


def fichier_sortie(identifiant: str, numero: int):
    montage = MONTAGES.get(identifiant)
    if not montage or not (0 <= numero < len(montage["sorties"])):
        return None
    chemin = montage["sorties"][numero]["chemin"]
    return chemin if os.path.exists(chemin) else None


def lancer_montage(identifiant: str, dossier: str, source: str, options: dict, choisir_retouches, choisir_extraits) -> None:
    """
    Demarre (ou relance) un montage en tache de fond. options : silences, hesitations, sous_titres, format ("vertical" /
    "horizontal"), musique (chemin ou None), mode ("montage" / "shorts"), nb_shorts, duree_max_short, consignes, accent.
    choisir_retouches(phrases, consignes) -> [(debut, fin)] a retirer ; choisir_extraits(phrases, nb, duree_min, duree_max)
    -> [{"debut", "fin", "titre"}] : fournis par main.py (Claude), pour garder ce module independant de l'IA.
    """
    montage = MONTAGES.setdefault(identifiant, {"cree": time.time(), "source": source, "dossier": dossier, "transcription": None,
                                                "silences": None, "infos": None, "audio": None})
    montage.update(etat="en_cours", etape="Analyse de la vidéo", progression=0.0, erreur="", sorties=[], avertissement="")

    def fixer(etape, progression=None):
        montage["etape"] = etape
        if progression is not None:
            montage["progression"] = progression

    def travail():
        try:
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
            phrases = montage["transcription"]
            if not phrases:
                montage["avertissement"] = "Aucune parole détectée : montage sans sous-titres."

            retirer = []
            if options.get("hesitations"):
                retirer += [(p["debut"], p["fin"]) for p in phrases if p.get("hesitation")]
            if (options.get("consignes") or "").strip() and phrases:
                fixer("Application de vos consignes", 0.42)
                retirer += choisir_retouches(phrases, options["consignes"].strip())

            sorties = []
            format_sortie = options.get("format", "vertical")
            largeur, hauteur = (1080, 1920) if format_sortie == "vertical" else (1920, 1080)
            accent = options.get("accent") or (255, 214, 10)

            if options.get("mode") == "shorts":
                fixer("Choix des meilleurs extraits", 0.45)
                extraits = choisir_extraits(phrases, int(options.get("nb_shorts") or 3), 25.0, float(options.get("duree_max_short") or 90))
                if not extraits:
                    raise ErreurMontage("Aucun extrait exploitable n'a été trouvé dans cette vidéo.")
                travaux = [(e, segments_a_garder(infos["duree"], montage["silences"], options.get("silences", True), retirer, e["debut"], e["fin"])) for e in extraits]
            else:
                travaux = [({"titre": "Montage complet"}, segments_a_garder(infos["duree"], montage["silences"], options.get("silences", True), retirer))]

            for numero, (extrait, segments) in enumerate(travaux, start=1):
                base = 0.5 + 0.5 * (numero - 1) / len(travaux)
                fixer(f"Montage {numero}/{len(travaux)}" if len(travaux) > 1 else "Montage de la vidéo", base)
                fichier_ass = None
                if options.get("sous_titres", True) and phrases:
                    fichier_ass = os.path.join(dossier, f"sous-titres-{numero}.ass")
                    ecrire_ass(phrases_sur_montage(phrases, segments), fichier_ass, largeur, hauteur, accent,
                               extrait["titre"] if options.get("mode") == "shorts" else "")
                sortie = os.path.join(dossier, f"montage-{uuid.uuid4().hex[:6]}-{numero}.mp4")
                suivi = lambda p, b=base, n=len(travaux): montage.__setitem__("progression", b + 0.5 * p / n)
                try:
                    rendre(source, infos, segments, sortie, format_sortie, fichier_ass, options.get("musique"), suivi)
                except ErreurMontage:
                    if not fichier_ass:
                        raise
                    # Incrustation des sous-titres impossible sur ce serveur (ffmpeg sans libass...) : montage sans eux.
                    rendre(source, infos, segments, sortie, format_sortie, None, options.get("musique"), suivi)
                    montage["avertissement"] = "Les sous-titres n'ont pas pu être incrustés sur ce serveur : montage livré sans sous-titres."
                sorties.append({
                    "chemin": sortie, "titre": extrait["titre"], "duree": round(duree_montee(segments), 1),
                    "duree_origine": round(infos["duree"], 1) if len(travaux) == 1 else round(extrait["fin"] - extrait["debut"], 1),
                })
                montage["sorties"] = list(sorties)
            montage.update(etat="termine", etape="Terminé", progression=1.0)
        except Exception as erreur:  # rien ne doit rester bloque sur « en cours »
            montage.update(etat="erreur", erreur=str(erreur) or erreur.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
