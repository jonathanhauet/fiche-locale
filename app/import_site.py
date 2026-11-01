"""
Import du texte d'un site dans la base de connaissances d'un client : on part de l'adresse du site, on retrouve ses pages (plan du site, sinon liens de la page
d'accueil), on en lit le texte utile (sans menus, pieds de page ni formulaires) et on en fait un seul document. Lecture statique, sans navigateur : un site
construit entierement en JavaScript peut donner peu de texte (le message le dit). Toutes les lectures passent par geo_technique (jamais d'adresse privee).
"""

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

from . import geo_technique

NB_MAX_PAGES = 40
NB_MAX_SITEMAPS = 6
TAILLE_MAX_PAGE = 6000                  # caracteres gardes par page
TAILLE_MAX_TOTALE = 90_000              # caracteres du document final
TEXTE_MIN_PAGE = 80                     # en dessous, la page est consideree comme vide (site en JavaScript, page de redirection...)
DELAI_MAX = 110                         # secondes pour l'ensemble de l'import
EXTENSIONS_IGNOREES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".mp4", ".mp3", ".css", ".js", ".xml", ".json", ".ico", ".doc", ".docx", ".xls", ".xlsx")
CHEMINS_IGNORES = re.compile(
    r"/(wp-admin|wp-login|wp-json|wp-content|feed|tag|tags|author|auteur|category/uncategorized|panier|cart|checkout|commande|mon-compte|my-account|login|connexion|recherche|search"
    r"|mentions-legales|mentions|politique-de-confidentialite|confidentialite|privacy|cookies|cgv|cgu|conditions-generales|plan-du-site|sitemap)(/|$|-)", re.IGNORECASE)
LIGNE_INUTILE = re.compile(r"cookie|tous droits r[ée]serv|all rights reserved|©|mentions l[ée]gales|politique de confidentialit[ée]|accepter|refuser|^menu$|^fermer$|^rechercher$|^skip to", re.IGNORECASE)
BLOCS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "ul", "ol", "table", "blockquote", "dd", "dt", "figcaption", "address", "main"}
IGNORES = {"script", "style", "noscript", "svg", "template", "iframe", "select", "option", "nav", "header", "footer", "aside", "form", "button", "head", "canvas", "dialog"}
VIDES = {"br", "img", "hr", "input", "meta", "link", "source", "area", "base", "col", "embed", "param", "track", "wbr"}


class _LecteurPage(HTMLParser):
    """Texte des pages : titres et paragraphes, avec en priorite ce qui est dans <main> ou <article>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titre, self.description, self.liens = "", "", []
        self._pile, self._ligne, self._niveau_titre = [], [], 0
        self.tout, self.principal = [], []
        self._dans_titre = False

    def _vider(self):
        texte = " ".join("".join(self._ligne).split())
        self._ligne = []
        if not texte:
            return
        if self._niveau_titre:
            texte = "#" * min(self._niveau_titre, 3) + " " + texte
        elif self._pile and "li" in self._pile:
            texte = "- " + texte
        if not any(b in IGNORES for b in self._pile):
            self.tout.append(texte)
            if "main" in self._pile or "article" in self._pile:
                self.principal.append(texte)

    def handle_starttag(self, balise, attrs):
        attrs = dict(attrs)
        if balise == "title" and not self.titre:
            self._dans_titre = True
        elif balise == "meta" and (attrs.get("name") or "").lower() == "description" and not self.description:
            self.description = " ".join((attrs.get("content") or "").split())
        elif balise == "a" and attrs.get("href"):
            self.liens.append(attrs["href"])
        if balise in BLOCS:
            self._vider()
        if balise in VIDES:
            return
        self._pile.append(balise)
        if len(balise) == 2 and balise[0] == "h" and balise[1] in "123456":
            self._niveau_titre = int(balise[1])

    def handle_endtag(self, balise):
        if balise == "title":
            self._dans_titre = False
        if balise in BLOCS:
            self._vider()
        if balise in self._pile:
            while self._pile and self._pile.pop() != balise:
                pass
        if len(balise) == 2 and balise[0] == "h" and balise[1] in "123456":
            self._niveau_titre = 0

    def handle_data(self, donnees):
        if self._dans_titre:
            self.titre += donnees
            return
        if any(b in IGNORES for b in self._pile):
            return
        self._ligne.append(donnees)

    def close(self):
        super().close()
        self._vider()


def lire_page(html: str) -> dict:
    """{"titre", "description", "lignes", "liens"} : lignes de texte de la page (titres precedes de #, listes de -)."""
    lecteur = _LecteurPage()
    try:
        lecteur.feed(html)
        lecteur.close()
    except Exception:
        pass
    principal = lecteur.principal if sum(len(l) for l in lecteur.principal) >= 200 else lecteur.tout
    lignes, precedente = [], ""
    for ligne in principal:
        if ligne == precedente or (len(ligne) < 40 and LIGNE_INUTILE.search(ligne)) or len(ligne.strip("#- ")) < 2:
            continue
        lignes.append(ligne); precedente = ligne
    return {"titre": " ".join(lecteur.titre.split()), "description": lecteur.description, "lignes": lignes, "liens": lecteur.liens}


def _hote(url: str) -> str:
    hote = (urlparse(url).hostname or "").lower()
    return hote[4:] if hote.startswith("www.") else hote


def _propre(url: str, base: str):
    """URL absolue sans ancre ni parametres de suivi, ou None si elle sort du site ou n'est pas une page de texte."""
    absolue, _ = urldefrag(urljoin(base, url.strip()))
    analyse = urlparse(absolue)
    if analyse.scheme not in ("http", "https") or _hote(absolue) != _hote(base):
        return None
    chemin = analyse.path or "/"
    if chemin.lower().endswith(EXTENSIONS_IGNOREES) or CHEMINS_IGNORES.search(chemin):
        return None
    if analyse.query and not re.fullmatch(r"(?:page_id|p|id)=\d+", analyse.query):
        return None
    return f"{analyse.scheme}://{analyse.netloc}{chemin}" + (f"?{analyse.query}" if analyse.query else "")


def _sitemaps(accueil: str) -> list:
    """Pages listees dans le plan du site (robots.txt, puis adresses habituelles) ; sous-plans lus aussi (quelques-uns)."""
    racine = f"{urlparse(accueil).scheme}://{urlparse(accueil).netloc}"
    candidats = []
    code, robots, _, _ = geo_technique._get(racine + "/robots.txt", max_octets=200_000)
    if code == 200:
        candidats += [m.strip() for m in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)]
    candidats += [racine + "/sitemap.xml", racine + "/sitemap_index.xml", racine + "/wp-sitemap.xml", racine + "/sitemap-index.xml"]
    vus, pages, lus = set(), [], 0
    file = list(dict.fromkeys(candidats))
    while file and lus < NB_MAX_SITEMAPS:
        adresse = file.pop(0)
        if adresse in vus:
            continue
        vus.add(adresse); lus += 1
        code, xml, _, _ = geo_technique._get(adresse, max_octets=2_000_000)
        if code != 200 or "<loc" not in xml:
            continue
        for loc in geo_technique._lire_loc(xml):
            if loc.lower().endswith(".xml") or "sitemap" in loc.lower().rsplit("/", 1)[-1]:
                if not re.search(r"(?i)(post|article|blog|news|actualit|product|produit|attachment|media|image|category|tag|author|user|taxonomy)", loc.rsplit("/", 1)[-1]):
                    file.append(loc)
            else:
                pages.append(loc)
        if pages:
            break
    return pages


def _classer(urls: list) -> list:
    """Pages les plus utiles d'abord : l'accueil, puis les pages peu profondes (services, a propos, contact), les articles de blog en dernier."""
    def cle(url):
        chemin = urlparse(url).path.strip("/")
        profondeur = chemin.count("/") + (1 if chemin else 0)
        blog = 1 if re.search(r"(?i)(^|/)(blog|actualit|news|articles?|conseils?)(/|$)|/20\d\d/", "/" + chemin) and profondeur >= 2 else 0
        return (blog, profondeur, len(chemin))
    return sorted(urls, key=cle)


def _lire(url: str) -> dict:
    code, html, finale, type_contenu = geo_technique._get(url, max_octets=1_500_000)
    if code != 200 or ("html" not in type_contenu.lower() and "<html" not in html[:2000].lower() and "<body" not in html[:5000].lower()):
        return {"url": url, "ok": False}
    page = lire_page(html)
    page.update(url=finale or url, ok=True)
    return page


def importer(adresse: str, nb_max: int = NB_MAX_PAGES) -> dict:
    """
    Lit le site et renvoie {"texte", "pages": [{"url", "titre", "caracteres"}], "vides": nombre, "avertissement"}.
    RuntimeError (message clair) si le site est injoignable ou ne contient aucun texte lisible.
    """
    debut = time.monotonic()
    try:
        accueil = geo_technique.normaliser_url(adresse)
    except geo_technique.ErreurControle:
        raise RuntimeError("Renseigne l'adresse du site (par exemple https://www.exemple.fr).")
    premiere = _lire(accueil)
    if not premiere["ok"]:
        raise RuntimeError("Impossible de lire le site (adresse incorrecte, site hors ligne, ou site qui bloque la lecture automatique).")
    accueil = premiere["url"]
    trouvees = {}
    for lien in [accueil] + _sitemaps(accueil) + premiere["liens"]:
        propre = _propre(lien, accueil)
        if propre:
            trouvees.setdefault(propre.rstrip("/") or propre, propre)
    cle_accueil = accueil.rstrip("/")
    ordre = [u for u in _classer(list(trouvees.values())) if u.rstrip("/") != cle_accueil][: max(0, nb_max - 1)]
    lues = {accueil: premiere}
    with ThreadPoolExecutor(max_workers=4) as executeur:
        for page in executeur.map(_lire, ordre):
            if time.monotonic() - debut > DELAI_MAX:
                break
            lues[page["url"]] = page
    pages = [accueil] + [u for u in lues if u != accueil]
    # Les lignes presentes sur beaucoup de pages (bandeaux, appels repetes, coordonnees du pied de page) sont gardees une seule fois, sur l'accueil.
    compte = Counter(l for u in pages if lues[u]["ok"] for l in set(lues[u]["lignes"]))
    nb_ok = sum(1 for u in pages if lues[u]["ok"])
    repetees = {l for l, n in compte.items() if nb_ok >= 4 and n > max(2, 0.4 * nb_ok)}
    blocs, resume, vides, total = [], [], 0, 0
    for url in pages:
        page = lues[url]
        if not page["ok"]:
            continue
        lignes = [l for l in page["lignes"] if l not in repetees or url == accueil]
        texte = "\n".join(lignes)
        if len(texte) < TEXTE_MIN_PAGE:
            vides += 1
            continue
        texte = texte[:TAILLE_MAX_PAGE].rsplit("\n", 1)[0] if len(texte) > TAILLE_MAX_PAGE else texte
        titre = page["titre"] or next((l.lstrip("# ") for l in lignes if l.startswith("#")), url)
        bloc = f"=== Page : {titre} ({url}) ===\n" + (f"Description : {page['description']}\n" if page["description"] else "") + texte
        if total + len(bloc) > TAILLE_MAX_TOTALE:
            break
        blocs.append(bloc); total += len(bloc)
        resume.append({"url": url, "titre": titre, "caracteres": len(texte)})
    if not blocs:
        raise RuntimeError("Le site ne contient pas de texte lisible automatiquement (il est probablement construit en JavaScript). Copie son contenu à la main, ou ajoute un document.")
    avertissement = ""
    if vides and vides >= len(blocs):
        avertissement = f"{vides} page(s) sans texte lisible ont été ignorées (site en partie construit en JavaScript ?) : vérifie que l'essentiel est bien là."
    return {"texte": f"SITE WEB : {accueil}\n\n" + "\n\n".join(blocs), "pages": resume, "vides": vides, "avertissement": avertissement}
