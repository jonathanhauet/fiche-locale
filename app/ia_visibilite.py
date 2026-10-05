"""
Verifie si un client est cite par les IA generatives (ChatGPT, Gemini) en
reponse a des questions de recherche locale representatives - complement
au classement Google classique (voir rank_tracking.py) pour le "GEO"
(visibilite dans les reponses IA) plutot que le SEO traditionnel.
"""

import json
import os
import re
from urllib.parse import urlparse

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
CLE_PERPLEXITY = os.getenv("PERPLEXITY_API_KEY")

MODELE_CHATGPT = "gpt-4o-mini"
MODELE_GEMINI = "gemini-flash-latest"
MODELE_PERPLEXITY = "sonar"
MODELE_CLAUDE = "claude-sonnet-5"

# Les assistants sont interroges AVEC leur recherche web (comme pour un utilisateur) : sans elle, l'API repond de
# memoire et ne reflete pas ce que voit un acheteur. Perplexity n'apparait que si sa cle est configuree.
MODELES_DISPONIBLES = {"chatgpt": "ChatGPT (avec recherche web)", "gemini": "Gemini (avec Google Search)"}
if CLE_PERPLEXITY:
    MODELES_DISPONIBLES["perplexity"] = "Perplexity"


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


def domaine_depuis(valeur: str) -> str:
    """« https://www.exemple.be/page » ou « exemple.be » -> « exemple.be » (sans www)."""
    valeur = (valeur or "").strip().lower()
    if "://" in valeur:
        valeur = urlparse(valeur).netloc
    valeur = valeur.split("/")[0].split("?")[0]
    return valeur[4:] if valeur.startswith("www.") else valeur


def _ajouter_source(sources: list, titre: str, url: str) -> None:
    domaine = domaine_depuis(titre if "." in (titre or "") and " " not in (titre or "").strip() else url)
    if domaine and all(s["domaine"] != domaine or s["url"] != url for s in sources):
        sources.append({"domaine": domaine, "url": url or "", "titre": titre or domaine})


def interroger_chatgpt(requete: str) -> tuple:
    """(texte, sources) : ChatGPT avec recherche web ; repli sur l'API classique (sans sources) si la recherche est refusee."""
    if not CLE_OPENAI:
        raise RuntimeError("OPENAI_API_KEY manquant dans plateforme_web/.env.")
    entetes = {"Authorization": f"Bearer {CLE_OPENAI}"}
    reponse = requests.post(
        "https://api.openai.com/v1/responses", headers=entetes, timeout=90,
        json={"model": MODELE_CHATGPT, "tools": [{"type": "web_search"}], "input": requete},
    )
    if reponse.status_code == 200:
        texte, sources = "", []
        for element in reponse.json().get("output", []):
            if element.get("type") != "message":
                continue
            for contenu in element.get("content", []):
                texte += contenu.get("text", "")
                for annotation in contenu.get("annotations", []):
                    if annotation.get("type") == "url_citation":
                        url = re.sub(r"[?&]utm_source=openai.*$", "", annotation.get("url", ""))
                        _ajouter_source(sources, annotation.get("title", ""), url)
        if texte.strip():
            return texte, sources
    reponse = requests.post(
        "https://api.openai.com/v1/chat/completions", headers=entetes, timeout=30,
        json={"model": MODELE_CHATGPT, "messages": [{"role": "user", "content": requete}]},
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la requete ChatGPT (code {reponse.status_code}) : {reponse.text[:300]}")
    return reponse.json()["choices"][0]["message"]["content"], []


def interroger_gemini(requete: str) -> tuple:
    """(texte, sources) : Gemini avec Google Search (les sources viennent des pages que Google a consultees)."""
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")
    from google.genai import types

    client = genai.Client(api_key=CLE_GEMINI)
    reponse = client.models.generate_content(
        model=MODELE_GEMINI, contents=requete,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    sources = []
    try:
        metadonnees = reponse.candidates[0].grounding_metadata
        for morceau in (metadonnees.grounding_chunks or []) if metadonnees else []:
            if morceau.web:
                _ajouter_source(sources, morceau.web.title or "", morceau.web.uri or "")
    except (IndexError, AttributeError):
        pass
    return reponse.text or "", sources


def interroger_perplexity(requete: str) -> tuple:
    """(texte, sources) : Perplexity (recherche web integree, sources toujours fournies)."""
    if not CLE_PERPLEXITY:
        raise RuntimeError("PERPLEXITY_API_KEY manquant dans plateforme_web/.env.")
    reponse = requests.post(
        "https://api.perplexity.ai/chat/completions", headers={"Authorization": f"Bearer {CLE_PERPLEXITY}"}, timeout=60,
        json={"model": MODELE_PERPLEXITY, "messages": [{"role": "user", "content": requete}]},
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la requete Perplexity (code {reponse.status_code}) : {reponse.text[:300]}")
    donnees = reponse.json()
    sources = []
    for resultat in donnees.get("search_results") or []:
        _ajouter_source(sources, resultat.get("title", ""), resultat.get("url", ""))
    for url in donnees.get("citations") or []:
        _ajouter_source(sources, "", url)
    return donnees["choices"][0]["message"]["content"], sources


INTERROGATEURS = {"chatgpt": interroger_chatgpt, "gemini": interroger_gemini, "perplexity": interroger_perplexity}


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
        reponse_brute, sources = INTERROGATEURS[modele](requete_texte)
    except Exception as erreur:
        return {
            "client_cite": False, "position": None, "concurrents_cites": [], "sources": [],
            "suggestion": "", "reponse_brute": "", "erreur": f"Echec de l'interrogation : {erreur}",
        }

    try:
        analyse = analyser_reponse(client_nom, reponse_brute)
    except Exception as erreur:
        return {
            "client_cite": False, "position": None, "concurrents_cites": [], "sources": sources,
            "suggestion": "", "reponse_brute": reponse_brute, "erreur": f"Echec de l'analyse : {erreur}",
        }

    analyse["reponse_brute"] = reponse_brute
    analyse["sources"] = sources
    analyse["erreur"] = ""
    return analyse
