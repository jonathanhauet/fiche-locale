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

LIBELLES = {"whoosh": "Whoosh", "swoosh": "Swoosh", "pop": "Pop", "impact": "Impact", "clic": "Clic", "ding": "Ding"}


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


def _souffle(duree: float, f_debut: float, f_fin: float, pic: float, q: float, graine: int) -> list:
    """
    Souffle de whoosh : bruit ROSE (plus doux et plus naturel que le bruit blanc, qui donne un « sssh » dur) passe par un filtre passe-bande
    resonant dont la frequence glisse (echelle exponentielle) de f_debut a f_fin, avec une enveloppe qui monte jusqu'a `pic` (0 a 1) puis
    retombe : un air qui passe, pas un sifflement continu. Un dernier filtre passe-bas supprime le sifflement aigu.
    """
    hasard = random.Random(graine)
    n, sortie = int(duree * CADENCE), []
    x1 = x2 = y1 = y2 = lissage = 0.0
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    for i in range(n):
        u = i / n
        blanc = hasard.uniform(-1, 1)
        b0 = 0.99886 * b0 + blanc * 0.0555179
        b1 = 0.99332 * b1 + blanc * 0.0750759
        b2 = 0.96900 * b2 + blanc * 0.1538520
        b3 = 0.86650 * b3 + blanc * 0.3104856
        b4 = 0.55000 * b4 + blanc * 0.5329522
        b5 = -0.7616 * b5 - blanc * 0.0168980
        rose = b0 + b1 + b2 + b3 + b4 + b5 + b6 + blanc * 0.5362
        b6 = blanc * 0.115926
        w0 = 2 * math.pi * f_debut * (f_fin / f_debut) ** u / CADENCE
        alpha = math.sin(w0) / (2 * q)
        y = (alpha * rose - alpha * x2 + 2 * math.cos(w0) * y1 - (1 - alpha) * y2) / (1 + alpha)
        x2, x1, y2, y1 = x1, rose, y1, y
        lissage += 0.35 * (y - lissage)
        enveloppe = (u / pic) ** 2 if u < pic else math.cos((u - pic) / (1 - pic) * math.pi / 2) ** 2
        sortie.append(lissage * enveloppe)
    return sortie


def _whoosh() -> list:
    """Whoosh de transition : air qui passe, du grave vers l'aigu (0,5 s)."""
    return _souffle(0.50, 260, 2800, 0.52, 2.0, 1)


def _swoosh() -> list:
    """Swoosh de zoom : court, qui monte vite avec le cadrage (0,34 s)."""
    return _souffle(0.34, 450, 4000, 0.64, 2.6, 5)


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


GENERATEURS = {"whoosh": _whoosh, "swoosh": _swoosh, "pop": _pop, "impact": _impact, "clic": _clic, "ding": _ding}


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
