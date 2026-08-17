"""
Suggestions de mots-cles via l'autocompletion Google (endpoint non officiel,
le meme que celui interroge par les navigateurs) - gratuit, contrairement a
l'API DataForSEO deja utilisee pour le suivi de positions (voir
rank_tracking.py). Pas de volume de recherche exact : uniquement des
requetes reellement tapees par les internautes, ce qui reste tres utile pour
trouver des idees de mots-cles et de contenu.
"""

import concurrent.futures

import requests

URL_SUGGESTIONS = "https://suggestqueries.google.com/complete/search"
DELAI_APPEL = 4  # secondes, par appel individuel
NB_THREADS = 8

# Prefixes de type "question" : utiles pour trouver des idees de contenu
# (posts, FAQ) en plus des mots-cles bruts a suivre en position.
PREFIXES_QUESTIONS = [
    "comment", "pourquoi", "combien", "quand", "où", "quel", "quelle", "qui",
]

# Suffixes a forte intention commerciale/locale, plus pertinents pour une
# entreprise locale qu'un simple balayage alphabetique.
SUFFIXES_LOCAUX = [
    "avis", "prix", "tarif", "près de moi", "horaires", "pas cher", "urgence", "ouvert",
]

ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _appeler_autocomplete(requete: str) -> list[str]:
    reponse = requests.get(
        URL_SUGGESTIONS,
        params={"client": "firefox", "q": requete, "hl": "fr", "gl": "fr"},
        timeout=DELAI_APPEL,
    )
    reponse.raise_for_status()
    donnees = reponse.json()
    return donnees[1] if len(donnees) > 1 else []


def _executer_en_parallele(requetes: list[str]) -> set[str]:
    resultats = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=NB_THREADS) as executeur:
        futurs = [executeur.submit(_appeler_autocomplete, r) for r in requetes]
        for futur in concurrent.futures.as_completed(futurs):
            try:
                resultats.update(futur.result())
            except Exception:
                continue  # une suggestion qui echoue ne doit pas bloquer les autres
    return resultats


def rechercher(mot_cle_racine: str) -> dict:
    """
    Explore l'autocompletion Google autour du mot-cle racine (seul, +chaque
    lettre de l'alphabet, +suffixes a intention locale, +prefixes
    "question") pour en tirer un large eventail de suggestions reellement
    recherchees. Renvoie {"suggestions": [...], "questions": [...]} -
    dedupliques, sans le mot-cle racine lui-meme, tries alphabetiquement.
    """
    racine = mot_cle_racine.strip()
    if not racine:
        return {"suggestions": [], "questions": []}

    requetes_suggestions = (
        [racine]
        + [f"{racine} {lettre}" for lettre in ALPHABET]
        + [f"{racine} {suffixe}" for suffixe in SUFFIXES_LOCAUX]
    )
    requetes_questions = [f"{prefixe} {racine}" for prefixe in PREFIXES_QUESTIONS]

    suggestions = _executer_en_parallele(requetes_suggestions)
    questions = _executer_en_parallele(requetes_questions)

    suggestions.discard(racine)
    questions -= suggestions

    return {
        "suggestions": sorted(suggestions, key=str.lower),
        "questions": sorted(questions, key=str.lower),
    }
