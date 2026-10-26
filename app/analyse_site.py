"""
Analyse du site d'un client pour preparer sa charte graphique : couleurs, polices, logo, nom, CMS et reseaux sociaux. Lecture statique de la page d'accueil et de ses
feuilles de style (pas de navigateur) : heuristique, donc a valider par une personne. Toutes les lectures passent par geo_technique (jamais d'adresse privee).
"""

import math
import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from . import geo_technique, wordpress_style

NB_MAX_FEUILLES = 8
TAILLE_MAX_FEUILLE = 700_000
TAILLE_MAX_IMAGE = 6_000_000
GENERIQUES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "inherit", "initial", "unset", "revert", "-apple-system",
    "blinkmacsystemfont", "segoe ui", "roboto", "helvetica neue", "helvetica", "arial", "apple color emoji", "segoe ui emoji", "segoe ui symbol", "noto color emoji",
    "oxygen-sans", "ubuntu", "cantarell", "droid sans", "fira sans", "oxygen", "lucida grande", "tahoma", "verdana", "times new roman", "georgia", "courier new", "emoji", "math",
}
POLICES_ICONES = ("awesome", "dashicons", "eicons", "icon", "glyph", "swiper", "slick", "elementskit", "themify", "genericons", "mono", "material symbols", "material icons", "etmodules", "fontello", "linearicons")
RESEAUX = (("facebook", "facebook.com"), ("instagram", "instagram.com"), ("linkedin", "linkedin.com"), ("youtube", "youtube.com"), ("tiktok", "tiktok.com"), ("x", "twitter.com"), ("x", "x.com"))
FILTRE_PARTAGE = re.compile(r"sharer|share\?|intent/|/share|/dialog/|plugins/like|login", re.IGNORECASE)
# (nom, marqueurs dans le code de la page, indication pour une modification du site)
CMS = (
    ("WordPress", ("wp-content/", "wp-includes/", "wp-json"), "Modifiable : fichiers et balisage possibles (thème, extension ou code ajouté)."),
    ("Wix", ("static.wixstatic.com", "wix.com", "x-wix"), "Plateforme fermée : pas d'accès aux fichiers du serveur, à vérifier au cas par cas."),
    ("Webador", ("webador", "cdn.webador"), "Plateforme fermée : possibilités très limitées, à vérifier avec leur support."),
    ("Squarespace", ("squarespace", "static1.squarespace.com"), "Plateforme fermée : modifications limitées, à vérifier au cas par cas."),
    ("Shopify", ("cdn.shopify.com", "shopify"), "Thème modifiable, pas d'accès aux fichiers racine : à vérifier."),
    ("Jimdo", ("jimdo", "jimcdn.com"), "Plateforme fermée : possibilités limitées."),
    ("Weebly", ("weebly",), "Plateforme fermée : possibilités limitées."),
    ("Webflow", ("webflow", "assets.website-files.com"), "Code personnalisé possible, fichiers racine limités : à vérifier."),
    ("PrestaShop", ("prestashop", "/modules/ps_"), "Modifiable (thème, modules) : à vérifier."),
    ("Joomla", ("joomla", "/media/jui/"), "Modifiable : fichiers et balisage possibles."),
    ("Drupal", ("drupal", "/sites/default/files"), "Modifiable : fichiers et balisage possibles."),
    ("Framer", ("framer.com", "framerusercontent"), "Plateforme fermée : possibilités limitées."),
    ("Systeme.io", ("systeme.io",), "Plateforme fermée : possibilités limitées."),
    ("GoDaddy", ("websitebuilder", "godaddy"), "Plateforme fermée : possibilités limitées."),
)


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titre, self.meta, self.liens, self.imgs, self.ancres, self.styles, self.json_ld = "", {}, [], [], [], [], []
        self._cible, self._entete = None, 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._cible = "titre"
        elif tag == "style":
            self._cible = "style"; self.styles.append("")
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._cible = "ld"; self.json_ld.append("")
        elif tag == "meta":
            cle = (a.get("property") or a.get("name") or "").lower()
            if cle and cle not in self.meta:
                self.meta[cle] = a.get("content", "")
        elif tag == "link":
            self.liens.append(a)
        elif tag in ("header", "nav"):
            self._entete += 1
        elif tag == "img":
            a["_entete"] = bool(self._entete)
            self.imgs.append(a)
        elif tag == "a" and a.get("href"):
            self.ancres.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("title", "style", "script"):
            self._cible = None
        elif tag in ("header", "nav") and self._entete:
            self._entete -= 1

    def handle_data(self, data):
        if self._cible == "titre":
            self.titre += data
        elif self._cible == "style" and self.styles:
            self.styles[-1] += data
        elif self._cible == "ld" and self.json_ld:
            self.json_ld[-1] += data


def _url(adresse: str) -> str:
    adresse = (adresse or "").strip()
    if not adresse:
        return ""
    if not re.match(r"^https?://", adresse, re.IGNORECASE):
        adresse = "https://" + adresse
    parsed = urlparse(adresse)
    return adresse if parsed.hostname and "." in parsed.hostname else ""


def _octets(url: str, maximum: int = TAILLE_MAX_IMAGE) -> tuple:
    """(octets, type de contenu) d'une image, adresses privees refusees, redirections controlees ; (b'', '') en cas d'echec."""
    courant = url
    for _ in range(5):
        if not geo_technique._hote_public(urlparse(courant).hostname or ""):
            return b"", ""
        try:
            r = requests.get(courant, headers={"User-Agent": geo_technique.UA_NAVIGATEUR}, timeout=geo_technique.DELAI_REQUETE, allow_redirects=False, stream=True)
        except requests.RequestException:
            return b"", ""
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            courant = urljoin(courant, r.headers["Location"]); r.close(); continue
        if r.status_code != 200:
            r.close(); return b"", ""
        contenu = r.raw.read(maximum + 1, decode_content=True)
        type_contenu = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        r.close()
        return (contenu, type_contenu) if len(contenu) <= maximum else (b"", "")
    return b"", ""


def telecharger_image(url: str) -> bytes:
    """Octets d'une image du site pour l'importer comme logo. RuntimeError (message clair) si impossible."""
    if not _url(url):
        raise RuntimeError("Adresse d'image invalide.")
    octets, type_contenu = _octets(url)
    if not octets or not (type_contenu.startswith("image/") or re.search(r"\.(png|jpe?g|webp|gif)(\?|$)", url, re.IGNORECASE)):
        raise RuntimeError("Impossible de récupérer cette image.")
    if "svg" in type_contenu or octets[:5] in (b"<?xml", b"<svg ") or url.lower().split("?")[0].endswith(".svg"):
        raise RuntimeError("Cette image est au format SVG : téléverse plutôt un PNG.")
    return octets


def _nom_police(valeur: str):
    """Premiere police nommee d'une declaration font-family (sans guillemets), ou '' si generique, icone ou variable."""
    for morceau in valeur.split(","):
        nom = morceau.strip().strip("'\"").strip()
        bas = nom.lower()
        if not nom or nom.startswith(("var(", "-", "$")) or "(" in nom or "!important" in bas:
            continue
        if bas in GENERIQUES:
            continue
        if any(m in bas for m in POLICES_ICONES):
            return ""
        return re.sub(r"\s+", " ", nom)[:40]
    return ""


def _polices_google(textes: list) -> list:
    familles = []
    for texte in textes:
        for m in re.finditer(r"fonts\.googleapis\.com/css2?\?([^\"')\s>]+)", texte):
            requete = m.group(1).replace("&amp;", "&")
            for valeur in parse_qs(requete).get("family", []):
                nom = unquote(valeur.split(":")[0]).replace("+", " ").strip()
                if nom and nom not in familles:
                    familles.append(nom)
    return familles


def detecter_polices(html_page: str, css: str, liens_police: list) -> dict:
    """{"titres": [...], "texte": [...], "autres": [...]} : polices du site, les plus probables en tete (3 au plus par liste)."""
    titres, texte, toutes = Counter(), Counter(), Counter()
    for selecteur, declarations in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        for valeur in re.findall(r"(?<![\w-])font-family\s*:\s*([^;}]+)", declarations):
            nom = _nom_police(valeur)
            if not nom:
                continue
            toutes[nom] += 1
            sel = selecteur.lower()
            if re.search(r"(?<![\w-])h[1-6](?![\w-])|entry-title|page-title|site-title|\.title\b|heading", sel):
                titres[nom] += 1
            if re.search(r"(?<![\w-])(body|html|p)(?![\w-])|entry-content|\.content\b", sel):
                texte[nom] += 1
    for valeur in re.findall(r"--[\w-]*(?:font-family|typography-(?:primary|secondary|text|accent)-font-family|font-heading|font-body|font-title|heading-font|body-font)[\w-]*\s*:\s*([^;}]+)", css, re.IGNORECASE):
        nom = _nom_police(valeur)
        if nom:
            toutes[nom] += 1
    for nom in re.findall(r"@font-face\s*\{[^}]*?font-family\s*:\s*([^;}]+)", css, re.IGNORECASE):
        nom = _nom_police(nom)
        if nom:
            toutes[nom] += 2
    google = _polices_google([html_page, css] + liens_police)
    for position, nom in enumerate(google):
        toutes[nom] += 3
    t_titres = [n for n, _ in titres.most_common(3)] or []
    t_texte = [n for n, _ in texte.most_common(3) if n not in t_titres[:1]] or [n for n, _ in texte.most_common(3)]
    restantes = [n for n, _ in toutes.most_common(8) if n not in t_titres and n not in t_texte]
    if not t_titres and google:
        t_titres = google[:1]
    if not t_texte and len(google) > 1:
        t_texte = google[1:2]
    return {"titres": t_titres[:3], "texte": t_texte[:3], "autres": restantes[:4]}


def _nombre(texte: str) -> float:
    texte = texte.strip()
    return float(texte[:-1]) / 100 if texte.endswith("%") else float(texte)


def _oklch_vers_hex(clarte: float, chroma: float, teinte: float) -> str:
    a, b = chroma * math.cos(math.radians(teinte)), chroma * math.sin(math.radians(teinte))
    l_, m_, s_ = clarte + 0.3963377774 * a + 0.2158037573 * b, clarte - 0.1055613458 * a - 0.0638541728 * b, clarte - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lineaire = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    valeurs = []
    for v in lineaire:
        v = max(0.0, min(1.0, v))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        valeurs.append(round(max(0.0, min(1.0, v)) * 255))
    return "#%02x%02x%02x" % tuple(valeurs)


def _hsl_vers_hex(teinte: float, saturation: float, clarte: float) -> str:
    teinte = (teinte % 360) / 360
    q = clarte * (1 + saturation) if clarte < 0.5 else clarte + saturation - clarte * saturation
    p = 2 * clarte - q

    def canal(t):
        t %= 1
        return p + (q - p) * 6 * t if t < 1 / 6 else q if t < 1 / 2 else p + (q - p) * (2 / 3 - t) * 6 if t < 2 / 3 else p
    return "#%02x%02x%02x" % tuple(round(canal(teinte + d) * 255) for d in (1 / 3, 0, -1 / 3))


def couleur_en_hex(valeur: str) -> str:
    """Valeur CSS de couleur (#hex, rgb(), hsl(), oklch(), ou jeu de variables « H S% L% » style Tailwind) en #rrggbb ; '' si illisible."""
    valeur = valeur.strip().rstrip(";").strip()
    brut = wordpress_style.normaliser_couleur(valeur)
    if brut:
        return brut
    try:
        m = re.match(r"oklch\(\s*([\d.]+%?)\s+([\d.]+%?)\s+([\d.]+)", valeur, re.IGNORECASE)
        if m:
            chroma = _nombre(m.group(2)) * (0.4 if m.group(2).endswith("%") else 1)
            return _oklch_vers_hex(_nombre(m.group(1)), chroma, float(m.group(3)))
        m = re.match(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%", valeur, re.IGNORECASE) or re.match(r"^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$", valeur)
        if m:
            return _hsl_vers_hex(float(m.group(1)), float(m.group(2)) / 100, float(m.group(3)) / 100)
        m = re.match(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})", valeur, re.IGNORECASE)
        if m:
            return "#%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups())
    except (ValueError, ZeroDivisionError):
        return ""
    return ""


ORDRE_VARIABLES = (("primary", "variable --primary du site"), ("brand", "variable --brand du site"), ("accent", "variable --accent du site"), ("main", "variable de couleur principale du site"),
                   ("ink", "variable de couleur foncée du site"), ("secondary", "variable --secondary du site"), ("dark", "variable de couleur foncée du site"))


def couleurs_variables(css: str) -> list:
    """Couleurs des variables CSS nommees par le site lui-meme (primary, accent, brand...), y compris en oklch/hsl : [{"couleur", "source"}], sans doublon."""
    trouvees = []
    for mot, source in ORDRE_VARIABLES:
        for nom, valeur in re.findall(r"(--[\w-]*" + mot + r"[\w-]*)\s*:\s*([^;}]+)", css, re.IGNORECASE):
            if re.search(r"foreground|background|bg|contrast|text|border|shadow|ring|muted|soft|light|tint|rgb\b", nom, re.IGNORECASE):
                continue
            couleur = couleur_en_hex(valeur)
            if couleur and wordpress_style.est_couleur_d_accent(couleur) and all(t["couleur"] != couleur for t in trouvees):
                trouvees.append({"couleur": couleur, "source": f"{source} ({nom})"})
    return trouvees


def _couleurs_frequentes(css_et_html: str, deja: list) -> list:
    compteur = Counter()
    for m in re.finditer(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css_et_html):
        c = wordpress_style.normaliser_couleur(m.group(0))
        if c and wordpress_style.est_couleur_d_accent(c):
            compteur[c] += 1
    for m in re.finditer(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", css_et_html):
        c = wordpress_style.normaliser_couleur("#%02x%02x%02x" % tuple(min(255, int(x)) for x in m.groups()))
        if c and wordpress_style.est_couleur_d_accent(c):
            compteur[c] += 1
    return [{"couleur": c, "source": "couleur très utilisée sur le site"} for c, n in compteur.most_common(12) if n >= 2 and c not in deja][:5]


def _logos(page: _Page, base: str) -> list:
    trouves = []

    def ajouter(adresse, source):
        adresse = (adresse or "").strip()
        if not adresse or adresse.startswith("data:"):
            return
        absolue = urljoin(base, adresse)
        if urlparse(absolue).scheme not in ("http", "https") or any(t["url"] == absolue for t in trouves):
            return
        trouves.append({"url": absolue, "source": source, "svg": urlparse(absolue).path.lower().endswith(".svg")})

    import json
    for bloc in page.json_ld:
        try:
            donnees = json.loads(bloc)
        except ValueError:
            continue
        for noeud in (donnees if isinstance(donnees, list) else donnees.get("@graph", [donnees]) if isinstance(donnees, dict) else []):
            if not isinstance(noeud, dict):
                continue
            logo = noeud.get("logo")
            ajouter(logo.get("url") if isinstance(logo, dict) else logo if isinstance(logo, str) else "", "logo déclaré par le site")
    for img in sorted(page.imgs, key=lambda i: not i.get("_entete")):
        indice = " ".join([img.get("src", ""), img.get("alt", ""), img.get("class", ""), img.get("id", "")]).lower()
        if "logo" in indice or "brand" in indice:
            ajouter(img.get("data-src") or img.get("src"), "image « logo » de la page")
    icones = [l for l in page.liens if "icon" in l.get("rel", "").lower() and l.get("href")]
    icones.sort(key=lambda l: -int((re.search(r"(\d+)x\d+", l.get("sizes", "")) or [0, 0])[1] or 0))
    for lien in icones:
        if "apple-touch-icon" in lien.get("rel", "").lower():
            ajouter(lien["href"], "icône du site")
    ajouter(page.meta.get("og:image"), "image de partage du site")
    return trouves[:5]


def _cms(page: _Page, html_page: str) -> dict:
    generateur = page.meta.get("generator", "").lower()
    bas = html_page.lower()
    for nom, marqueurs, indice in CMS:
        if nom.lower() in generateur or any(m in bas for m in marqueurs):
            return {"nom": nom, "indice": indice}
    return {"nom": "", "indice": "CMS non identifié (site sur mesure ou outil inconnu) : à regarder de plus près."}


def analyser(adresse: str) -> dict:
    """Analyse la page d'accueil du site. RuntimeError (message clair) si le site est injoignable."""
    url = _url(adresse)
    if not url:
        raise RuntimeError("Renseigne l'adresse du site (par exemple https://www.exemple.fr).")
    code, html_page, url_finale, _ = geo_technique._get(url, max_octets=2_000_000)
    if code == 0:
        raise RuntimeError("Impossible de lire le site (adresse incorrecte, site hors ligne ou adresse non publique).")
    if code >= 400:
        raise RuntimeError(f"Le site a répondu avec le code {code}.")
    page = _Page()
    try:
        page.feed(html_page)
    except Exception:
        pass
    feuilles, liens_police = [], []
    for lien in page.liens:
        href = lien.get("href", "")
        if "stylesheet" not in lien.get("rel", "").lower() or not href:
            continue
        if "fonts.googleapis.com" in href:
            liens_police.append(href); continue
        if len(feuilles) < NB_MAX_FEUILLES:
            c, texte, _, _ = geo_technique._get(urljoin(url_finale, href), max_octets=TAILLE_MAX_FEUILLE)
            if c == 200:
                feuilles.append(texte)
    css = "\n".join(page.styles + feuilles)
    couleurs = couleurs_variables(css)
    couleurs += [c for c in wordpress_style.detecter_couleurs(html_page, feuilles) if all(c["couleur"] != x["couleur"] for x in couleurs)]
    couleurs += _couleurs_frequentes(css + "\n" + html_page[:300_000], [c["couleur"] for c in couleurs])
    nom = page.meta.get("og:site_name", "").strip()
    if not nom:
        segments = [s.strip() for s in re.split(r"\s[|\-–—·»]\s", re.sub(r"\s+", " ", page.titre).strip()) if s.strip()]
        nom = segments[0] if segments else ""
    reseaux, vus = [], set()
    for href in page.ancres:
        if FILTRE_PARTAGE.search(href):
            continue
        hote = (urlparse(href).hostname or "").lower().removeprefix("www.")
        for reseau, domaine in RESEAUX:
            if (hote == domaine or hote.endswith("." + domaine)) and reseau not in vus and len(urlparse(href).path.strip("/")) > 1:
                vus.add(reseau); reseaux.append({"reseau": reseau, "url": href.split("#")[0]}); break
    return {
        "url": url_finale, "nom": nom[:60], "slogan": re.sub(r"\s+", " ", page.meta.get("og:description") or page.meta.get("description", "")).strip()[:220],
        "couleurs": couleurs[:10], "polices": detecter_polices(html_page, css, liens_police), "logos": _logos(page, url_finale), "cms": _cms(page, html_page), "reseaux": reseaux,
    }
