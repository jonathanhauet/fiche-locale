"""
Verifie si un client est cite par les IA generatives (ChatGPT, Gemini) en
reponse a des questions de recherche locale representatives - complement
au classement Google classique (voir rank_tracking.py) pour le "GEO"
(visibilite dans les reponses IA) plutot que le SEO traditionnel.
"""

import json
import os

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from google import genai

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLE_OPENAI = os.getenv("OPENAI_API_KEY")
CLE_GEMINI = os.getenv("GEMINI_API_KEY")
CLE_ANTHROPIC = os.getenv("ANTHROPIC_API_KEY")

MODELE_CHATGPT = "gpt-4o-mini"
MODELE_GEMINI = "gemini-3.6-flash"
MODELE_CLAUDE = "claude-sonnet-5"

MODELES_DISPONIBLES = {"chatgpt": "ChatGPT", "gemini": "Gemini"}

SCHEMA_ANALYSE = {
    "type": "object",
    "properties": {
        "client_cite": {"type": "boolean"},
        "position": {"type": ["integer", "null"]},
        "concurrents_cites": {"type": "array", "items": {"type": "string"}},
        "suggestion": {"type": "string"},
    },
    "required": ["client_cite", "position", "concurrents_cites", "suggestion"],
    "additionalProperties": False,
}


def interroger_chatgpt(requete: str) -> str:
    if not CLE_OPENAI:
        raise RuntimeError("OPENAI_API_KEY manquant dans plateforme_web/.env.")
    reponse = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {CLE_OPENAI}"},
        json={"model": MODELE_CHATGPT, "messages": [{"role": "user", "content": requete}]},
        timeout=30,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la requete ChatGPT (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()["choices"][0]["message"]["content"]


def interroger_gemini(requete: str) -> str:
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")
    client = genai.Client(api_key=CLE_GEMINI)
    interaction = client.interactions.create(model=MODELE_GEMINI, input=requete)
    return interaction.output_text or ""


INTERROGATEURS = {"chatgpt": interroger_chatgpt, "gemini": interroger_gemini}


def analyser_reponse(client_nom: str, reponse_ia: str) -> dict:
    """
    Utilise Claude pour extraire, a partir d'une reponse brute de
    ChatGPT/Gemini a une question de recherche locale : si le client est
    cite, sa position approximative, les concurrents cites, et une
    suggestion d'amelioration. La suggestion reste une estimation
    plausible de l'IA, pas une certitude sur son fonctionnement interne.
    """
    if not CLE_ANTHROPIC:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    prompt = (
        f'Voici la reponse d\'une IA a une question de recherche locale. Analyse-la pour le compte '
        f'de l\'entreprise "{client_nom}".\n\n'
        f"Reponse de l'IA a analyser :\n\"\"\"\n{reponse_ia}\n\"\"\"\n\n"
        "Determine si cette entreprise precise est citee nommement (pas seulement son secteur "
        "d'activite), sa position approximative parmi les entreprises citees (1 = premiere "
        "mentionnee, null si non citee), la liste des autres entreprises/concurrents cites dans "
        "la reponse, et une suggestion courte et concrete en francais pour ameliorer sa presence "
        "et etre mieux cite (laisse vide si deja bien place)."
    )

    client = Anthropic(api_key=CLE_ANTHROPIC)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1024,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_ANALYSE}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'analyse IA n'a renvoye aucun texte exploitable.")

    return json.loads(bloc_texte)


def verifier_une_requete(client_nom: str, modele: str, requete_texte: str) -> dict:
    """
    Interroge le modele donne puis fait analyser la reponse par Claude.
    Renvoie un dict pret a stocker dans ResultatVisibiliteIA (cle "erreur"
    non vide en cas d'echec, plutot que de lever une exception - un modele
    en panne ne doit pas empecher de verifier les autres requetes/modeles).
    """
    try:
        reponse_brute = INTERROGATEURS[modele](requete_texte)
    except Exception as erreur:
        return {
            "client_cite": False, "position": None, "concurrents_cites": [],
            "suggestion": "", "reponse_brute": "", "erreur": f"Echec de l'interrogation : {erreur}",
        }

    try:
        analyse = analyser_reponse(client_nom, reponse_brute)
    except Exception as erreur:
        return {
            "client_cite": False, "position": None, "concurrents_cites": [],
            "suggestion": "", "reponse_brute": reponse_brute, "erreur": f"Echec de l'analyse : {erreur}",
        }

    analyse["reponse_brute"] = reponse_brute
    analyse["erreur"] = ""
    return analyse
