"""
Outils SEO des articles WordPress : pages du site du client pour les liens internes (recuperees automatiquement), validation de ces liens,
section FAQ et ses donnees structurees, bloc « Ou nous trouver » (adresse et lien de la fiche Google), controle avant publication,
detection des sujets deja traites et champs Yoast / Rank Math (description et mot-cle principal) quand le site les expose.
"""

import html
import json
import re
import time
import unicodedata
from urllib.parse import urlparse

import requests

from . import tics_ia, wordpress_style

DELAI_SECONDES = 12
DUREE_CACHE_SECONDES = 6 * 3600
MAX_PAGES = 60
MAX_LIENS_INTERNES = 3
_cache_pages = {}          # hote -> (horodatage, pages)

# Pages utilitaires du site : inutiles (ou nuisibles) comme destination d'un lien dans un article.
MOTIF_PAGES_EXCLUES = re.compile(
    r"(mentions?-legales?|legal|confidentialit|politique|cgv|cgu|conditions|cookies?|panier|cart|checkout|commande|mon-compte|my-account|"
    r"plan-du-site|sitemap|404|merci|thank|login|connexion|wp-|hello-world|accueil-provisoire|maintenance|coming-soon)", re.IGNORECASE)
CLES_YOAST = {"description": "_yoast_wpseo_metadesc", "mot_cle": "_yoast_wpseo_focuskw"}
CLES_RANK_MATH = {"description": "rank_math_description", "mot_cle": "rank_math_focus_keyword"}


def normaliser_url(url: str) -> str:
    """Adresse comparable : sans protocole, sans « www. », sans parametres ni barre finale."""
    analyse = urlparse((url or "").strip())
    hote = (analyse.netloc or "").lower()
    if hote.startswith("www."):
        hote = hote[4:]
    return f"{hote}{analyse.path.rstrip('/')}"


def hote(url: str) -> str:
    return normaliser_url(url).split("/", 1)[0]


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texte or "") if not unicodedata.combining(c)).lower()


def _mots(texte: str) -> list:
    return [m for m in re.findall(r"[a-z0-9]+", _sans_accents(texte)) if len(m) > 2]


# --------------------------------------------------------------------------- pages du site

def _nettoyer_titre(brut: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", brut or "")).split())


def _titre_depuis_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip().capitalize() or url


def _pages_rest(base: str) -> list:
    """Pages puis articles publies, via l'API publique de WordPress (titre et adresse exacts)."""
    resultat = []
    for type_contenu in ("pages", "posts"):
        try:
            r = requests.get(f"{base}/wp-json/wp/v2/{type_contenu}", params={"per_page": 100, "_fields": "link,title"}, headers=wordpress_style.EN_TETES, timeout=DELAI_SECONDES)
            if r.status_code != 200:
                continue
            for element in r.json():
                lien, titre = element.get("link") or "", _nettoyer_titre((element.get("title") or {}).get("rendered", ""))
                if lien and titre:
                    resultat.append({"titre": titre, "url": lien, "type": "page" if type_contenu == "pages" else "article"})
        except (requests.RequestException, ValueError):
            continue
    return resultat


def _loc(xml: str) -> list:
    return [html.unescape(m.strip()) for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "")]


def _pages_sitemap(base: str) -> list:
    resultat = []
    for chemin in ("/wp-sitemap.xml", "/sitemap_index.xml", "/sitemap.xml"):
        try:
            r = requests.get(base + chemin, headers=wordpress_style.EN_TETES, timeout=DELAI_SECONDES)
        except requests.RequestException:
            continue
        if r.status_code != 200 or "<loc" not in r.text:
            continue
        urls = _loc(r.text)
        if "<sitemapindex" in r.text:
            enfants = sorted(urls, key=lambda u: 0 if "page" in u.lower() else 1)[:4]
            urls = []
            for enfant in enfants:
                try:
                    urls += _loc(requests.get(enfant, headers=wordpress_style.EN_TETES, timeout=DELAI_SECONDES).text)
                except requests.RequestException:
                    continue
        resultat = [{"titre": _titre_depuis_url(u), "url": u, "type": "page"} for u in urls]
        if resultat:
            break
    return resultat


def pages_du_site(url_site: str, forcer: bool = False) -> list:
    """
    Pages utilisables pour des liens internes : [{"titre", "url", "type"}] (pages d'abord, puis articles), recuperees automatiquement
    depuis l'API WordPress, sinon depuis le plan du site. Accueil et pages utilitaires exclus ; garde en memoire 6 heures.
    """
    base = (url_site or "").strip().rstrip("/")
    if not base:
        return []
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    maintenant = time.time()
    en_cache = _cache_pages.get(hote(base))
    if en_cache and not forcer and maintenant - en_cache[0] < DUREE_CACHE_SECONDES:
        return en_cache[1]
    brutes = _pages_rest(base) or _pages_sitemap(base)
    vues, pages = set(), []
    for page in brutes:
        cle = normaliser_url(page["url"])
        chemin = cle.split("/", 1)[1] if "/" in cle else ""
        if not chemin or cle in vues or hote(page["url"]) != hote(base) or MOTIF_PAGES_EXCLUES.search(chemin):
            continue
        vues.add(cle)
        pages.append(page)
    pages.sort(key=lambda p: 0 if p["type"] == "page" else 1)
    pages = pages[:MAX_PAGES]
    if pages:
        _cache_pages[hote(base)] = (maintenant, pages)
    return pages


# --------------------------------------------------------------------------- liens internes

MOTIF_LIEN = re.compile(r"(?<!\[)\[([^\[\]\n]+)\]\((https?://[^\s)]+)\)")


def liens_internes_valides(article: str, pages: list, url_article: str = "") -> tuple:
    """
    Ne garde que les liens vers de vraies pages du site (liste `pages`), 3 au plus et une seule fois chacune : tout autre lien ecrit par l'IA
    est remplace par son texte. Le bouton final [[texte|lien]] n'est jamais touche. Renvoie (article, [{"texte", "url"}]).
    """
    autorisees = {normaliser_url(p["url"]): p["url"] for p in pages}
    propre = normaliser_url(url_article)
    gardes, deja = [], set()

    def remplacer(m):
        texte, url = m.group(1), m.group(2)
        cle = normaliser_url(url)
        if cle in autorisees and cle != propre and cle not in deja and len(gardes) < MAX_LIENS_INTERNES:
            deja.add(cle)
            gardes.append({"texte": texte, "url": autorisees[cle]})
            return f"[{texte}]({autorisees[cle]})"
        return texte

    lignes = []
    for ligne in (article or "").split("\n"):
        # pas de lien dans les titres ni dans le bouton final
        lignes.append(ligne if ligne.startswith("#") or ligne.strip().startswith("[[") else MOTIF_LIEN.sub(remplacer, ligne))
    return "\n".join(lignes), gardes


def liens_du_texte(article: str) -> list:
    return [{"texte": m.group(1), "url": m.group(2)} for m in MOTIF_LIEN.finditer("\n".join(l for l in (article or "").split("\n") if not l.strip().startswith("[[")))]


# --------------------------------------------------------------------------- FAQ

MOTIF_TITRE_FAQ = re.compile(r"^##\s+(questions?\s+fr[ée]quentes?|faq)\b", re.IGNORECASE)


def _texte_brut(texte: str) -> str:
    texte = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texte)
    return " ".join(re.sub(r"\*\*?|`", "", texte).split())


def faq_depuis_markdown(article: str) -> list:
    """[(question, reponse)] de la section « ## Questions frequentes » (sous-titres « ### Question ? » suivis de leur reponse)."""
    dans_faq, faq, question, reponse = False, [], None, []

    def clore():
        if question and reponse:
            faq.append((question, _texte_brut(" ".join(reponse))))

    for ligne in (article or "").split("\n"):
        brute = ligne.strip()
        if MOTIF_TITRE_FAQ.match(brute):
            dans_faq = True
            continue
        if dans_faq and re.match(r"^##\s", brute):
            break
        if not dans_faq:
            continue
        m = re.match(r"^###\s+(.+)$", brute)
        if m:
            clore()
            question, reponse = _texte_brut(m.group(1)), []
        elif brute and question and not brute.startswith("[["):
            reponse.append(brute.lstrip("-*> ").strip())
    clore()
    return faq


def json_ld_faq(faq: list) -> str:
    """Balise de donnees structurees FAQPage (a coller dans le corps de l'article) ; vide s'il y a moins de 2 questions."""
    if len(faq) < 2:
        return ""
    donnees = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": r}} for q, r in faq]}
    return '<script type="application/ld+json">' + json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/") + "</script>"


# --------------------------------------------------------------------------- bloc local

def bloc_local(nom: str, infos: dict) -> str:
    """Section « Ou nous trouver » en markdown, a partir des seules donnees reelles de la fiche Google (adresse, telephone, lien de la fiche)."""
    infos = infos or {}
    adresse = infos.get("storefrontAddress") or {}
    rue = ", ".join(adresse.get("addressLines") or [])
    cp_ville = " ".join(x for x in (adresse.get("postalCode"), adresse.get("locality")) if x)
    texte_adresse = ", ".join(x for x in (rue, cp_ville) if x)
    if not texte_adresse:
        return ""
    ligne = f"{nom}, {texte_adresse}."
    telephone = (infos.get("phoneNumbers") or {}).get("primaryPhone") or ""
    if telephone:
        ligne += f" Téléphone : {telephone}."
    maps = (infos.get("metadata") or {}).get("mapsUri") or ""
    if maps.startswith("http"):
        ligne += f" [Voir notre fiche Google et l'itinéraire]({maps})."
    return f"## Où nous trouver\n\n{ligne}"


MOTIF_BOUTON = re.compile(r"^\[\[(.+?)\|(\S+?)\]\]$")


def inserer_avant_bouton(article: str, bloc: str) -> str:
    """Place `bloc` juste avant le bouton final (ou a la fin) ; ne fait rien s'il est vide ou deja present."""
    if not bloc or "## Où nous trouver" in article:
        return article
    lignes = article.rstrip().split("\n")
    for indice in range(len(lignes) - 1, -1, -1):
        if MOTIF_BOUTON.match(lignes[indice].strip()):
            return "\n".join(lignes[:indice]).rstrip() + "\n\n" + bloc + "\n\n" + "\n".join(lignes[indice:])
    return article.rstrip() + "\n\n" + bloc


# --------------------------------------------------------------------------- image

def texte_alternatif(titre: str, nom_client: str = "") -> str:
    """Texte alternatif de l'image mise en avant : le titre de l'article et le nom de l'entreprise (125 caracteres au plus)."""
    texte = " ".join((titre or "").split())
    if nom_client and _sans_accents(nom_client) not in _sans_accents(texte):
        texte = f"{texte} - {nom_client}"
    return texte[:125].rstrip(" -")


# --------------------------------------------------------------------------- doublons

STOP = {"les", "des", "une", "pour", "dans", "avec", "que", "qui", "sur", "est", "son", "ses", "aux", "par", "vos", "votre", "nos", "notre", "comment", "quel", "quelle", "quels"}


def sujets_proches(sujet: str, pages: list, seuil: float = 0.6) -> list:
    """Pages ou articles du site dont le titre recouvre au moins 60 % des mots du sujet (risque de deux contenus pour la meme recherche)."""
    mots = {m for m in _mots(sujet) if m not in STOP}
    if len(mots) < 2:
        return []
    proches = []
    for page in pages:
        commun = mots & {m for m in _mots(page["titre"]) if m not in STOP}
        if len(commun) / len(mots) >= seuil:
            proches.append({"titre": page["titre"], "url": page["url"]})
    return proches[:3]


# --------------------------------------------------------------------------- controle avant publication

def _contient(texte: str, expression: str) -> bool:
    mots = [m for m in _mots(expression) if m not in STOP] or _mots(expression)
    cible = set(_mots(texte))
    return bool(mots) and all(m in cible for m in mots)


def _controle(code, libelle, statut, detail=""):
    return {"code": code, "libelle": libelle, "statut": statut, "detail": detail}


def controle_seo(article: str, mot_cle: str = "", ville: str = "", url_site: str = "", avec_image: bool = False) -> dict:
    """Liste de verifications d'un article (markdown, premiere ligne « # Titre ») avant publication : {"controles": [...], "score": 0 a 100}."""
    from . import wordpress_publish

    lignes = [l.rstrip() for l in (article or "").split("\n")]
    titre = next((re.match(r"^#\s+(.+)$", l).group(1).strip() for l in lignes if re.match(r"^#\s+\S", l)), "")
    corps_lignes = [l for l in lignes if not re.match(r"^#\s", l)]
    corps = "\n".join(corps_lignes)
    sous_titres = [re.match(r"^##\s+(.+)$", l).group(1) for l in lignes if re.match(r"^##\s+\S", l) and not MOTIF_TITRE_FAQ.match(l.strip())]
    mots_corps = re.findall(r"\w+", re.sub(r"\[\[.*?\]\]|[#>*`]", " ", corps))
    texte_intro = " ".join(mots_corps[:100])
    c = []
    c.append(_controle("titre", "Titre court (58 caractères au plus)", "ok" if 0 < len(titre) <= 58 else "avertissement" if titre else "erreur",
                       f"{len(titre)} caractères" if titre else "Aucun titre : la première ligne doit être « # Titre »."))
    if mot_cle:
        c.append(_controle("mot_cle_titre", "Mot-clé dans le titre", "ok" if _contient(titre, mot_cle) else "avertissement", "" if _contient(titre, mot_cle) else f"« {mot_cle} » n'apparaît pas dans le titre."))
        c.append(_controle("mot_cle_intro", "Mot-clé dans les 100 premiers mots", "ok" if _contient(texte_intro, mot_cle) else "avertissement"))
        c.append(_controle("mot_cle_h2", "Mot-clé dans un sous-titre", "ok" if any(_contient(s, mot_cle) for s in sous_titres) else "avertissement"))
    if ville:
        dans = _contient(titre, ville) or _contient(texte_intro, ville)
        c.append(_controle("ville", "Ville citée dans le titre ou l'introduction", "ok" if dans else "avertissement", "" if dans else f"« {ville} » n'apparaît ni dans le titre ni dans l'introduction."))
    c.append(_controle("sections", "3 à 6 sous-titres « ## »", "ok" if 3 <= len(sous_titres) <= 6 else "avertissement", f"{len(sous_titres)} sous-titre(s)"))
    nb = len(mots_corps)
    c.append(_controle("longueur", "Longueur de 700 à 1 500 mots", "ok" if 700 <= nb <= 1500 else "avertissement", f"{nb} mots"))
    extrait = wordpress_publish.extrait_depuis_corps(corps)
    c.append(_controle("extrait", "Introduction utilisable comme description Google (120 à 158 caractères)", "ok" if 120 <= len(extrait) <= 158 else "avertissement", f"{len(extrait)} caractères"))
    liens = liens_du_texte(article)
    internes = [l for l in liens if url_site and hote(l["url"]) == hote(url_site)]
    c.append(_controle("liens", "Au moins 2 liens internes vers le site", "ok" if len(internes) >= 2 else "avertissement" if url_site else "info",
                       f"{len(internes)} lien(s) interne(s)" if url_site else "Adresse du site inconnue."))
    c.append(_controle("faq", "Section « Questions fréquentes » (3 questions ou plus)", "ok" if len(faq_depuis_markdown(article)) >= 3 else "avertissement"))
    local = any(("google.com/maps" in l["url"] or "maps.app.goo.gl" in l["url"] or "g.page" in l["url"]) for l in liens) or "## Où nous trouver" in article
    c.append(_controle("local", "Bloc local (adresse et lien vers la fiche Google)", "ok" if local else "avertissement", "" if local else "Ajouté automatiquement s'il manque, quand la fiche Google est reliée."))
    c.append(_controle("bouton", "Bouton d'appel à l'action", "ok" if any(MOTIF_BOUTON.match(l.strip()) for l in lignes) else "avertissement"))
    tics = tics_ia.detecter(corps)
    c.append(_controle("style", "Style naturel (pas de tournures typiques d'IA)", "ok" if not tics else "avertissement",
                       "" if not tics else "À reformuler : " + " ; ".join(f"{t['exemple']} ({t['conseil']})" for t in tics[:3])))
    c.append(_controle("image", "Image mise en avant (texte alternatif ajouté automatiquement)", "ok" if avec_image else "avertissement", "" if avec_image else "Aucune image choisie."))
    notes = [x for x in c if x["statut"] in ("ok", "avertissement", "erreur")]
    score = round(100 * sum(1 for x in notes if x["statut"] == "ok") / len(notes)) if notes else 0
    return {"controles": c, "score": score}


# --------------------------------------------------------------------------- Yoast / Rank Math

def meta_exposee(url_site: str) -> dict:
    """
    Champs SEO que WordPress accepte d'ecrire par l'API : {"description": cle, "mot_cle": cle} (vide si le site n'expose rien). Par defaut Yoast et
    Rank Math ne les exposent pas : il faut un petit code sur le site (voir snippet_meta()).
    """
    base = (url_site or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        r = requests.options(f"{base}/wp-json/wp/v2/posts", headers=wordpress_style.EN_TETES, timeout=8)
        proprietes = (((r.json().get("schema") or {}).get("properties") or {}).get("meta") or {}).get("properties") or {}
    except (requests.RequestException, ValueError, AttributeError):
        return {}
    for cles in (CLES_YOAST, CLES_RANK_MATH):
        trouvees = {nom: cle for nom, cle in cles.items() if cle in proprietes}
        if trouvees:
            return trouvees
    return {}


def meta_seo(url_site: str, extrait: str, mot_cle: str = "") -> dict:
    """Valeurs a envoyer dans le champ « meta » de l'article ({} si le site n'expose pas les champs SEO)."""
    exposees = meta_exposee(url_site)
    valeurs = {}
    if exposees.get("description") and extrait:
        valeurs[exposees["description"]] = extrait
    if exposees.get("mot_cle") and mot_cle:
        valeurs[exposees["mot_cle"]] = mot_cle
    return valeurs


SNIPPET_META = """// Fiche Locale : autorise la description et le mot-clé SEO à être renseignés par l'API (Yoast SEO et Rank Math)
add_action('init', function () {
    foreach (['_yoast_wpseo_metadesc', '_yoast_wpseo_focuskw', 'rank_math_description', 'rank_math_focus_keyword'] as $cle) {
        register_post_meta('post', $cle, [
            'show_in_rest' => true, 'single' => true, 'type' => 'string',
            'auth_callback' => function () { return current_user_can('edit_posts'); },
        ]);
    }
});"""
