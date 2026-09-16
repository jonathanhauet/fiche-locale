"""
Veille d'actualite gratuite (flux RSS Google Actualites, pas d'API/cle
necessaire) sur les sujets pertinents pour un expert SEO local : Google
Business Profile, Google AI Overviews, Google Local Services Ads, SEO local.
Pas de Google Trends (pas d'API officielle gratuite, et les recherches
generiques tendance du jour - people, sport, actualite generale - n'ont de
toute facon aucun rapport avec ce sujet precis).
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

URL_FLUX = "https://news.google.com/rss/search"

REQUETES_VEILLE = [
    "Google Business Profile",
    "Google AI Overviews SEO",
    "Google Local Services Ads",
    "référencement local Google",
    "mise à jour algorithme Google SEO",
]

EN_TETES = {"User-Agent": "Mozilla/5.0 (compatible; FicheLocale/1.0)"}


def _recuperer_flux(requete: str, limite: int) -> list[dict]:
    reponse = requests.get(
        URL_FLUX, params={"q": requete, "hl": "fr", "gl": "FR", "ceid": "FR:fr"},
        headers=EN_TETES, timeout=15,
    )
    if reponse.status_code != 200:
        return []

    racine = ET.fromstring(reponse.content)
    resultats = []
    for item in racine.findall("./channel/item")[:limite]:
        titre_brut = (item.findtext("title") or "").strip()
        # Convention Google Actualites : "Titre de l'article - Nom du media".
        if " - " in titre_brut:
            titre, source = titre_brut.rsplit(" - ", 1)
        else:
            titre, source = titre_brut, ""

        date_publication = None
        pub_date = item.findtext("pubDate")
        if pub_date:
            try:
                date_publication = parsedate_to_datetime(pub_date)
            except (TypeError, ValueError):
                pass

        resultats.append({
            "titre": titre,
            "source": source,
            "url": (item.findtext("link") or "").strip(),
            "date_publication": date_publication,
        })
    return resultats


def rechercher_actualites(limite_par_requete: int = 6, limite_totale: int = 25) -> list[dict]:
    """
    Interroge chaque requete de REQUETES_VEILLE, deduplique par titre et
    renvoie les articles les plus recents en premier. Chaque echec de flux
    individuel est ignore silencieusement (une requete qui echoue ne doit pas
    faire echouer toute la veille) plutot que de remonter une exception.
    """
    vus = set()
    tous = []
    for requete in REQUETES_VEILLE:
        try:
            articles = _recuperer_flux(requete, limite_par_requete)
        except Exception:
            continue
        for article in articles:
            cle = article["titre"].strip().lower()
            if not cle or cle in vus:
                continue
            vus.add(cle)
            tous.append(article)

    tous.sort(key=lambda a: a["date_publication"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return tous[:limite_totale]
