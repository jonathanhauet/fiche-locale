"""
Musiques de fond creees par l'IA (Lyria, de Google, via la cle Gemini) : l'IA propose d'abord une description adaptee au texte qui sera publie
(ambiance, genre, energie, tempo), Lyria compose ensuite un morceau instrumental, et le tempo reel est mesure pour caler les montages.
"""

import array
import base64
import json
import os
import subprocess
import time

from dotenv import load_dotenv
from google import genai

from . import claude_generation, montage_video

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(DOSSIER_APP), ".env"))

CLE_GEMINI = os.getenv("GEMINI_API_KEY")
MODELE_MORCEAU = "lyria-3.5"                       # morceau complet, duree demandee dans la description
MODELE_CLIP = "lyria-3-clip-preview"               # extrait de 30 secondes
PRIX_MORCEAU, PRIX_CLIP = 0.08, 0.04               # dollars par generation (page tarifs Gemini)

DUREES = {"30": ("30 secondes", 30), "60": ("1 minute", 60), "120": ("2 minutes", 120)}
DUREE_DEFAUT = "60"

AMBIANCES = {
    "chaleureuse": ("Chaleureuse", "warm, friendly and reassuring"),
    "dynamique": ("Dynamique", "upbeat, energetic and motivating"),
    "professionnelle": ("Professionnelle", "calm, professional and trustworthy"),
    "inspirante": ("Inspirante", "uplifting, inspiring and hopeful"),
    "elegante": ("Élégante", "elegant, refined and premium"),
    "joyeuse": ("Joyeuse", "playful, cheerful and festive"),
    "moderne": ("Moderne", "modern, clean and tech-savvy"),
}
GENRES = {
    "acoustique": ("Acoustique", "acoustic guitar with light percussion"),
    "electro": ("Électro léger", "light electronic music with soft synths"),
    "pop": ("Pop", "modern pop instrumental"),
    "piano": ("Piano et cordes", "gentle piano with strings"),
    "jazz": ("Jazz doux", "smooth lounge jazz"),
    "cinematique": ("Cinématique", "cinematic with a gradual build"),
    "funk": ("Funk", "groovy funk with a tight rhythm section"),
    "lofi": ("Lo-fi", "relaxed lo-fi beats"),
}
ENERGIES = {"calme": ("Calme", (70, 90)), "modere": ("Modérée", (95, 115)), "dynamique": ("Rythmée", (120, 135))}

TAILLE_MAX_DESCRIPTION = 900


def _borner(valeur, choix: dict, defaut: str) -> str:
    valeur = str(valeur or "").strip().lower()
    return valeur if valeur in choix else defaut


def bpm_pour_energie(energie: str) -> int:
    bas, haut = ENERGIES[_borner(energie, ENERGIES, "modere")][1]
    return (bas + haut) // 2


def construire_description(ambiance: str = "chaleureuse", genre: str = "acoustique", energie: str = "modere", duree: str = DUREE_DEFAUT, bpm=None, libre: str = "") -> str:
    """Description anglaise envoyee a Lyria, assemblee a partir des choix de l'interface (et d'un complement libre facultatif)."""
    ambiance, genre, energie, duree = _borner(ambiance, AMBIANCES, "chaleureuse"), _borner(genre, GENRES, "acoustique"), _borner(energie, ENERGIES, "modere"), _borner(duree, DUREES, DUREE_DEFAUT)
    try:
        bpm = int(float(bpm)) if bpm not in (None, "") else bpm_pour_energie(energie)
    except (TypeError, ValueError):
        bpm = bpm_pour_energie(energie)
    bpm = max(60, min(bpm, 170))
    secondes = DUREES[duree][1]
    texte = (
        f"Instrumental background music for a short promotional video, about {secondes} seconds long. Mood: {AMBIANCES[ambiance][1]}. Style: {GENRES[genre][1]}. "
        f"Steady tempo of about {bpm} BPM, constant throughout. No vocals, no lyrics, no spoken words. Keep the melody discreet so that a voice can be heard over it, "
        "with a clear intro and a clean, natural ending."
    )
    libre = " ".join(str(libre or "").split())
    if libre:
        texte += " Additional direction: " + libre
    return texte[:TAILLE_MAX_DESCRIPTION]


SCHEMA_SUGGESTION = {
    "type": "object",
    "properties": {
        "ambiance": {"type": "string", "enum": list(AMBIANCES)},
        "genre": {"type": "string", "enum": list(GENRES)},
        "energie": {"type": "string", "enum": list(ENERGIES)},
        "bpm": {"type": "integer"},
        "complement": {"type": "string"},
        "resume": {"type": "string"},
    },
    "required": ["ambiance", "genre", "energie", "bpm", "complement", "resume"],
    "additionalProperties": False,
}


def suggerer(texte: str, contexte: str = "") -> dict:
    """
    Fait choisir par l'IA le style de musique qui sert le mieux le texte a publier : {ambiance, genre, energie, bpm, complement, resume, description}.
    « resume » est une phrase francaise pour l'utilisateur, « description » le texte anglais pret pour Lyria.
    """
    texte = " ".join(str(texte or "").split())[:2500]
    if len(texte) < 5:
        raise ValueError("Écris ou génère d'abord le texte à publier : la musique s'adapte à son message.")
    contexte = " ".join(str(contexte or "").split())[:300]
    prompt = (
        "Tu choisis la musique de fond instrumentale d'une courte vidéo publiée par une entreprise locale (réseaux sociaux, site, fiche Google). "
        "La musique doit servir le message du texte ci-dessous : son ton, son émotion, son public et son énergie.\n\n"
        f"Texte qui sera publié :\n\"\"\"\n{texte}\n\"\"\"\n"
        + (f"Contexte : {contexte}\n" if contexte else "")
        + "\nRéponds avec :\n"
        f"- ambiance : une clé parmi {', '.join(AMBIANCES)}.\n"
        f"- genre : une clé parmi {', '.join(GENRES)}.\n"
        f"- energie : une clé parmi {', '.join(ENERGIES)}.\n"
        "- bpm : un tempo entre 70 et 135 cohérent avec l'énergie (calme 70-90, modérée 95-115, rythmée 120-135).\n"
        "- complement : une courte phrase EN ANGLAIS (20 mots maximum) qui précise l'arrangement (instruments, évolution, progression) sans nom d'artiste, de groupe ni de morceau. "
        "La musique est toujours instrumentale, sans voix.\n"
        "- resume : une phrase en français, positive, qui explique ce choix à l'utilisateur (par exemple « Ambiance chaleureuse à la guitare acoustique, rythme modéré : "
        "elle accompagne un message de confiance. »). Sans tiret long."
    )
    client = claude_generation.Anthropic(api_key=claude_generation.CLE_API)
    reponse = client.messages.create(
        model=claude_generation.MODELE_CLAUDE, max_tokens=500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_SUGGESTION}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoyé aucune proposition exploitable.")
    donnees = json.loads(bloc)
    energie = _borner(donnees.get("energie"), ENERGIES, "modere")
    bas, haut = ENERGIES[energie][1]
    try:
        bpm = max(bas, min(int(donnees.get("bpm")), haut))
    except (TypeError, ValueError):
        bpm = bpm_pour_energie(energie)
    ambiance, genre = _borner(donnees.get("ambiance"), AMBIANCES, "chaleureuse"), _borner(donnees.get("genre"), GENRES, "acoustique")
    complement = " ".join(str(donnees.get("complement") or "").split())[:240]
    return {
        "ambiance": ambiance, "genre": genre, "energie": energie, "bpm": bpm, "complement": complement,
        "resume": " ".join(str(donnees.get("resume") or "").split())[:300],
        "description": construire_description(ambiance, genre, energie, DUREE_DEFAUT, bpm, complement),
    }


def _signature_audio_valide(octets: bytes) -> bool:
    return octets[:3] == b"ID3" or octets[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or octets[:4] == b"RIFF" or octets[4:8] == b"ftyp"


def generer(description: str, duree: str = DUREE_DEFAUT) -> bytes:
    """Compose un morceau avec Lyria et renvoie les octets du fichier audio (MP3). Une nouvelle tentative est faite en cas d'echec passager."""
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")
    description = " ".join(str(description or "").split())[:TAILLE_MAX_DESCRIPTION]
    if len(description) < 10:
        raise ValueError("Décris la musique souhaitée (ou clique sur « Proposer une musique adaptée »).")
    duree = _borner(duree, DUREES, DUREE_DEFAUT)
    modele = MODELE_CLIP if duree == "30" else MODELE_MORCEAU
    client = genai.Client(api_key=CLE_GEMINI)
    derniere = None
    for tentative in range(2):
        try:
            interaction = client.interactions.create(model=modele, input=description)
            sortie = getattr(interaction, "output_audio", None)
            octets = base64.b64decode(sortie.data) if sortie is not None and getattr(sortie, "data", None) else b""
            if len(octets) > 2000 and _signature_audio_valide(octets):
                return octets
            derniere = RuntimeError("Google n'a pas renvoyé de musique exploitable (essaie une autre description).")
        except Exception as erreur:
            derniere = erreur
        time.sleep(2)
    raise RuntimeError(f"La génération de musique a échoué : {str(derniere)[:200]}")


def cout_estime(duree: str) -> float:
    return PRIX_CLIP if _borner(duree, DUREES, DUREE_DEFAUT) == "30" else PRIX_MORCEAU


# --- Mesure du tempo ------------------------------------------------------------------------------------------------------------------------

CADENCE = 100                                      # images d'enveloppe par seconde
FREQUENCE = 8000
SEUIL_CONFIANCE_BPM = 0.15                         # rapport « regularite du rythme / energie totale » : 0,4 a 0,7 sur des pistes rythmees, 0,06 sur du bruit


def _enveloppe_attaques(octets: bytes, secondes: int = 60):
    """Courbe des « attaques » (hausses d'energie) du debut du morceau, a CADENCE valeurs par seconde, ou None si le decodage echoue."""
    commande = [montage_video.FFMPEG, "-v", "error", "-i", "pipe:0", "-t", str(secondes), "-ac", "1", "-ar", str(FREQUENCE), "-f", "s16le", "pipe:1"]
    try:
        resultat = subprocess.run(commande, input=octets, capture_output=True, timeout=60)
    except Exception:
        return None
    brut = resultat.stdout
    if len(brut) < FREQUENCE * 2 * 8:
        return None
    echantillons = array.array("h")
    echantillons.frombytes(brut[: len(brut) // 2 * 2])
    pas = FREQUENCE // CADENCE
    energies = []
    for debut in range(0, len(echantillons) - pas, pas):
        energies.append(sum(abs(v) for v in echantillons[debut:debut + pas]) / pas)
    if len(energies) < CADENCE * 8:
        return None
    attaques = [max(0.0, energies[i] - energies[i - 1]) for i in range(1, len(energies))]
    moyenne = sum(attaques) / len(attaques)
    return [a - moyenne for a in attaques]


def mesurer_bpm(octets: bytes):
    """Tempo (battements par minute, entre 70 et 160) estime par autocorrelation des attaques, ou None si le morceau est trop flou pour conclure."""
    attaques = _enveloppe_attaques(octets)
    if not attaques:
        return None
    scores = {}
    for bpm in range(70, 161):
        decalage = round(CADENCE * 60 / bpm)
        scores[bpm] = sum(attaques[i] * attaques[i + decalage] for i in range(len(attaques) - decalage)) / (len(attaques) - decalage)
    meilleur = max(scores, key=scores.get)
    # un tempo et sa moitie ou son double se ressemblent : on garde le plus plausible pour une musique de fond
    for autre in (meilleur * 2, meilleur // 2):
        if 70 <= autre <= 160 and scores[autre] >= 0.9 * scores[meilleur] and abs(autre - 100) < abs(meilleur - 100):
            meilleur = autre
    variance = sum(a * a for a in attaques) / len(attaques)
    if variance <= 0 or scores[meilleur] / variance < SEUIL_CONFIANCE_BPM:       # bruit continu (~0,06) ou rythme trop flou : on ne propose pas de tempo
        return None
    return float(meilleur)
