"""
Bruitages du montage video (whoosh, pop, impact...) synthetises par le code : aucun fichier sonore sous licence a gerer. Les sons sont
generes une fois (WAV mono 44,1 kHz) dans un dossier temporaire puis reutilises, voir chemin_son.
"""

import math
import os
import random
import struct
import tempfile
import wave

CADENCE = 44100
DOSSIER_SONS = os.path.join(tempfile.gettempdir(), "fiche_locale_sons")

LIBELLES = {"whoosh": "Whoosh", "pop": "Pop", "impact": "Impact", "clic": "Clic", "ding": "Ding"}


def _ecrire(chemin: str, echantillons: list) -> None:
    crete = max(1e-9, max(abs(e) for e in echantillons))
    with wave.open(chemin, "wb") as fichier:
        fichier.setnchannels(1)
        fichier.setsampwidth(2)
        fichier.setframerate(CADENCE)
        fichier.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, e / crete * 0.9)) * 32767)) for e in echantillons))


def _bruit_filtre(duree: float, depart: float, fin: float, graine: int) -> list:
    """Bruit blanc passe par un filtre dont la frequence de coupure glisse de `depart` a `fin` (0 a 1) : le « souffle » d'un whoosh."""
    hasard = random.Random(graine)
    n, sortie, memoire = int(duree * CADENCE), [], 0.0
    for i in range(n):
        x = i / n
        coefficient = depart + (fin - depart) * x
        memoire += coefficient * (hasard.uniform(-1, 1) - memoire)
        enveloppe = math.sin(math.pi * x) ** 1.6
        sortie.append(memoire * enveloppe)
    return sortie


def _whoosh() -> list:
    return _bruit_filtre(0.55, 0.02, 0.45, 1)


def _pop() -> list:
    n = int(0.14 * CADENCE)
    return [math.sin(2 * math.pi * (520 + 700 * math.exp(-i / (0.03 * CADENCE))) * i / CADENCE) * math.exp(-i / (0.035 * CADENCE)) for i in range(n)]


def _impact() -> list:
    hasard = random.Random(2)
    n = int(0.7 * CADENCE)
    return [(math.sin(2 * math.pi * (55 + 90 * math.exp(-i / (0.05 * CADENCE))) * i / CADENCE) * 0.9 + hasard.uniform(-1, 1) * 0.35 * math.exp(-i / (0.02 * CADENCE)))
            * math.exp(-i / (0.16 * CADENCE)) for i in range(n)]


def _clic() -> list:
    hasard = random.Random(3)
    n = int(0.07 * CADENCE)
    return [hasard.uniform(-1, 1) * math.exp(-i / (0.012 * CADENCE)) for i in range(n)]


def _ding() -> list:
    n = int(0.8 * CADENCE)
    return [(math.sin(2 * math.pi * 1320 * i / CADENCE) + 0.5 * math.sin(2 * math.pi * 1980 * i / CADENCE)) * math.exp(-i / (0.2 * CADENCE)) for i in range(n)]


GENERATEURS = {"whoosh": _whoosh, "pop": _pop, "impact": _impact, "clic": _clic, "ding": _ding}


def chemin_son(nom: str) -> str:
    """Chemin du fichier WAV d'un bruitage (genere au premier usage)."""
    if nom not in GENERATEURS:
        raise KeyError(nom)
    os.makedirs(DOSSIER_SONS, exist_ok=True)
    chemin = os.path.join(DOSSIER_SONS, f"{nom}.wav")
    if not os.path.exists(chemin) or os.path.getsize(chemin) < 100:
        temporaire = chemin + f".{os.getpid()}.tmp"
        _ecrire(temporaire, GENERATEURS[nom]())
        os.replace(temporaire, chemin)
    return chemin
