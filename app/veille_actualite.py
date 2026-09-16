"""
Veille d'actualite gratuite (flux RSS de sites specialises, aucune cle/API
necessaire) sur les sujets pertinents pour un expert SEO local : Google
Business Profile, Google AI Overviews, Google Local Services Ads, SEO local.

Pas de Google News (utilise dans une premiere version) : ses liens
redirigent vers une page de consentement Google plutot que l'article reel
(mur de consentement RGPD), et sa description ne contient que le titre
re-enveloppe, jamais le contenu - inutilisable pour generer un post
strictement base sur les faits reels de l'article (voir
claude_generation.generer_post_expert). Les flux RSS directs des sites
ci-dessous exposent eux le contenu reel (au moins un resume substantiel,
souvent l'article complet via content:encoded).

Pas de Google Trends non plus : pas d'API officielle gratuite, et les
tendances generiques du jour (people, sport...) n'ont aucun rapport avec le
sujet.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import requests

SOURCES_VEILLE = [
    "https://www.abondance.com/feed",
    "https://searchengineland.com/feed",
    "https://www.blogdumoderateur.com/feed/",
    "https://www.seroundtable.com/index.rdf",
]

MOTS_CLES_PERTINENCE = [
    "google business", "business profile", "fiche google", "fiche d'établissement",
    "ai overview", "ai overviews", "google ai mode", "local services ads",
    "seo local", "référencement local", "google maps", "avis client", "avis google",
    "local pack", "google ads local", "google local",
]

NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
EN_TETES = {"User-Agent": "Mozilla/5.0 (compatible; FicheLocale/1.0)"}
LONGUEUR_EXTRAIT = 2500


def _texte_depuis_html(html: str) -> str:
    """Version texte brut d'un fragment HTML : suffisant pour donner un contenu lisible a l'IA, pas besoin de preserver la mise en forme."""
    sans_balises = re.sub(r"<[^>]+>", " ", html or "")
    texte = unescape(sans_balises)
    return re.sub(r"\s+", " ", texte).strip()


def _pertinent(titre: str, extrait: str) -> bool:
    haystack = f"{titre} {extrait}".lower()
    return any(mot in haystack for mot in MOTS_CLES_PERTINENCE)


def _recuperer_flux(url: str, limite: int) -> list[dict]:
    reponse = requests.get(url, headers=EN_TETES, timeout=15)
    if reponse.status_code != 200:
        return []

    racine = ET.fromstring(reponse.content)
    resultats = []
    for item in racine.findall("./channel/item"):
        titre = (item.findtext("title") or "").strip()

        contenu_brut = item.findtext(NS_CONTENT) or item.findtext("description") or ""
        extrait = _texte_depuis_html(contenu_brut)[:LONGUEUR_EXTRAIT]

        if not _pertinent(titre, extrait):
            continue

        date_publication = None
        pub_date = item.findtext("pubDate")
        if pub_date:
            try:
                date_publication = parsedate_to_datetime(pub_date)
            except (TypeError, ValueError):
                pass

        domaine = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        resultats.append({
            "titre": titre,
            "source": domaine,
            "url": (item.findtext("link") or "").strip(),
            "date_publication": date_publication,
            "extrait": extrait,
        })
        if len(resultats) >= limite:
            break
    return resultats


def rechercher_actualites(limite_par_source: int = 8, limite_totale: int = 25) -> list[dict]:
    """
    Interroge chaque flux de SOURCES_VEILLE, ne garde que les articles dont
    le titre ou le contenu touche a la thematique (voir _pertinent), deduplique
    par titre et renvoie les plus recents en premier. Chaque flux qui echoue
    est ignore silencieusement plutot que de faire echouer toute la veille.
    """
    vus = set()
    tous = []
    for url in SOURCES_VEILLE:
        try:
            articles = _recuperer_flux(url, limite_par_source)
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
