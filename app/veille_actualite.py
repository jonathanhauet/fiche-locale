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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests

SOURCES_VEILLE = [
    # France
    "https://www.abondance.com/feed",
    "https://www.blogdumoderateur.com/feed/",
    # Etats-Unis / Amerique du Nord : souvent en avance sur les nouveautes Google (tests, fonctionnalites locales)
    "https://www.seroundtable.com/index.rdf",
    "https://www.searchenginejournal.com/feed/",
    "https://www.searchenginewatch.com/feed/",
    "https://nearmedia.co/feed/",
    "https://whitespark.ca/feed/",
    # Search Engine Land bloque les lecteurs automatiques (403) : ne fonctionne pas.
]
DOMAINES_ETATS_UNIS = {
    "seroundtable.com", "searchenginejournal.com", "searchenginewatch.com", "nearmedia.co", "whitespark.ca",
    "searchengineland.com",
}
LIBELLE_ETATS_UNIS = "États-Unis"

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
        est_us = domaine in DOMAINES_ETATS_UNIS
        resultats.append({
            "titre": titre,
            "source": f"{domaine} ({'Canada' if domaine.endswith('.ca') else LIBELLE_ETATS_UNIS})" if est_us else domaine,
            "pays": "US" if est_us else "FR",
            "url": (item.findtext("link") or "").strip(),
            "date_publication": date_publication,
            "extrait": extrait,
        })
        if len(resultats) >= limite:
            break
    return resultats


def rechercher_actualites(limite_par_source: int = 8, limite_totale: int = 27) -> list[dict]:
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
    # Un quota par pays : sans lui, les sources americaines (plus nombreuses et plus prolifiques) noient les
    # francophones, ou l'inverse selon les jours.
    quota_fr, quota_us = limite_totale * 4 // 9, limite_totale * 5 // 9
    fr = [a for a in tous if a["pays"] == "FR"][:quota_fr]
    us = [a for a in tous if a["pays"] == "US"][:quota_us]
    reste = [a for a in tous if a not in fr and a not in us]
    retenus = fr + us
    retenus += reste[:max(0, limite_totale - len(retenus))]  # un pays sans matiere laisse la place a l'autre
    retenus.sort(key=lambda a: a["date_publication"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return retenus[:limite_totale]


# ---------------------------------------------------------------------------
# Veille par metier : actualite du secteur d'un client (hors SEO), a partir de requetes deduites de son activite.
# Bing Actualites (extrait + vraie URL de l'article) complete par Google Actualites (titres, plus nombreux).
# ---------------------------------------------------------------------------
JOURS_MAX_ACTUALITE_METIER = 75


def _url_reelle_bing(lien: str) -> str:
    try:
        return parse_qs(urlparse(lien).query).get("url", [""])[0]
    except Exception:
        return ""


def _articles_bing(requete: str) -> list[dict]:
    reponse = requests.get(
        "https://www.bing.com/news/search", params={"q": requete, "format": "rss", "setlang": "fr", "mkt": "fr-BE"},
        headers=EN_TETES, timeout=15,
    )
    if reponse.status_code != 200:
        return []
    resultats = []
    for item in ET.fromstring(reponse.content).findall("./channel/item"):
        url = _url_reelle_bing(item.findtext("link") or "")
        titre = (item.findtext("title") or "").strip()
        if not titre:
            continue
        try:
            date_publication = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            date_publication = None
        resultats.append({
            "titre": titre, "source": re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if url else "Bing Actualités",
            "pays": "FR", "url": url or (item.findtext("link") or "").strip(), "url_reelle": url,
            "date_publication": date_publication, "extrait": _texte_depuis_html(item.findtext("description") or "")[:LONGUEUR_EXTRAIT],
        })
    return resultats


def _articles_google_actualites(requete: str) -> list[dict]:
    reponse = requests.get(
        "https://news.google.com/rss/search", params={"q": requete, "hl": "fr", "gl": "BE", "ceid": "BE:fr"},
        headers=EN_TETES, timeout=15,
    )
    if reponse.status_code != 200:
        return []
    resultats = []
    for item in ET.fromstring(reponse.content).findall("./channel/item"):
        titre = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and titre.endswith(f" - {source}"):
            titre = titre[: -len(source) - 3].strip()
        if not titre:
            continue
        try:
            date_publication = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            date_publication = None
        # Lien Google (redirection) : ouvre l'article pour un lecteur, mais pas de contenu exploitable ici.
        resultats.append({
            "titre": titre, "source": source or "Google Actualités", "pays": "FR",
            "url": (item.findtext("link") or "").strip(), "url_reelle": "", "date_publication": date_publication, "extrait": "",
        })
    return resultats


def rechercher_actualites_metier(requetes: list[str], limite_par_requete: int = 8, limite_totale: int = 25) -> list[dict]:
    """
    Articles recents (75 jours max) sur les requetes de veille d'un client, dedoublonnes par titre, les plus recents
    d'abord. Une source qui echoue est ignoree ; leve RuntimeError si rien n'est trouve du tout.
    """
    limite_date = datetime.now(timezone.utc) - timedelta(days=JOURS_MAX_ACTUALITE_METIER)
    vus, tous = set(), []
    for requete in [r.strip() for r in requetes if r.strip()][:4]:
        for recuperer in (_articles_bing, _articles_google_actualites):
            try:
                articles = recuperer(requete)
            except Exception:
                continue
            gardes = 0
            for article in articles:
                cle = re.sub(r"\W+", " ", article["titre"].lower()).strip()[:80]
                date_article = article["date_publication"]
                if not cle or cle in vus or (date_article and date_article.tzinfo and date_article < limite_date):
                    continue
                vus.add(cle)
                tous.append(article)
                gardes += 1
                if gardes >= limite_par_requete:
                    break
    if not tous:
        raise RuntimeError("Aucune actualité récente trouvée sur les thèmes suivis pour ce client.")
    tous.sort(key=lambda a: a["date_publication"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return tous[:limite_totale]


def extraire_texte_article(url: str, longueur: int = LONGUEUR_EXTRAIT) -> str:
    """Texte des paragraphes d'une page d'article (au mieux : "" si la page est bloquee ou illisible)."""
    if not url:
        return ""
    try:
        reponse = requests.get(url, headers=EN_TETES, timeout=8)
        if reponse.status_code != 200 or "html" not in (reponse.headers.get("content-type") or ""):
            return ""
        paragraphes = re.findall(r"<p[^>]*>(.*?)</p>", reponse.text, flags=re.S | re.I)
        texte = " ".join(_texte_depuis_html(p) for p in paragraphes)
        texte = re.sub(r"\s+", " ", texte).strip()
        return texte[:longueur] if len(texte) >= 300 else ""
    except Exception:
        return ""


def enrichir_suggestions(suggestions: list[dict], articles: list[dict]) -> None:
    """Remplace l'extrait court de chaque suggestion par le texte de l'article quand la page est lisible (en parallele)."""
    par_titre = {a["titre"]: a for a in articles}
    a_traiter = [(s, par_titre.get(s["titre_article"], {}).get("url_reelle", "")) for s in suggestions]
    with ThreadPoolExecutor(max_workers=5) as pool:
        textes = list(pool.map(lambda x: extraire_texte_article(x[1]), a_traiter))
    for (suggestion, _), texte in zip(a_traiter, textes):
        if texte:
            suggestion["extrait"] = texte
