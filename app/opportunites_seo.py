"""
Opportunites SEO d'un client, sans abonnement supplementaire :
1. recherches « presque en page 1 » : les requetes pour lesquelles Google affiche deja le site du client (donnees de la Search Console, deja connectee)
   en bas de la page 1 ou en page 2, classees par nombre de visites supplementaires possibles, avec ce qu'il faut faire (article ou retouche de page) ;
2. analyse des pages du site : titres, descriptions, balises H1, contenu, images sans texte alternatif, liens casses, plan du site, robots.txt.
Rien n'est montre au client : c'est un outil de travail de l'agence, donc les problemes y sont dits franchement.
"""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as DelaiDepasse, as_completed, wait
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

from . import geo_technique, search_console

# --------------------------------------------------------------------------- 1. recherches proches de la page 1

SEUIL_IMPRESSIONS = 15                   # en dessous, trop peu de donnees pour conclure
MAX_OPPORTUNITES = 40
CTR_PAR_POSITION = {1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.065, 6: 0.05, 7: 0.04, 8: 0.032, 9: 0.028, 10: 0.024}      # ordres de grandeur moyens
MOTS_QUESTION = {"comment", "pourquoi", "quel", "quelle", "quels", "quelles", "combien", "quand", "peut", "faut", "faire", "est", "differences", "difference"}
MOTS_GENERIQUES = {
    "sarl", "sas", "sasu", "eurl", "sci", "les", "des", "chez", "entreprise", "societe", "garage", "cabinet", "salon", "restaurant", "boulangerie", "plomberie",
    "electricite", "menuiserie", "maconnerie", "toiture", "couverture", "peinture", "chauffage", "agence", "atelier", "institut", "pharmacie", "service", "services",
    "pro", "and", "the", "france", "belgique", "groupe", "ets", "etablissements",
}
LIBELLES_CATEGORIES = {
    "page2": "Page 2 : une petite poussée suffit pour passer en page 1",
    "bas_page1": "Bas de la page 1 : monter de quelques places change beaucoup",
    "sans_clic": "Très visible mais peu cliqué : le titre ne donne pas envie",
}


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte or "") if unicodedata.category(c) != "Mn")


def _mots(texte: str) -> list:
    return re.findall(r"[a-z0-9]+", _sans_accents(texte).lower())


def ctr_attendu(position: float) -> float:
    """Part de clics habituellement obtenue a cette position (moyenne generale : un ordre de grandeur, pas une promesse)."""
    pos = max(1.0, float(position or 0))
    if pos <= 10:
        bas = int(pos)
        haut = min(10, bas + 1)
        return CTR_PAR_POSITION[bas] + (CTR_PAR_POSITION[haut] - CTR_PAR_POSITION[bas]) * (pos - bas)
    if pos <= 20:
        return max(0.005, 0.02 - (pos - 10) * 0.0015)
    return 0.003


def est_requete_de_marque(requete: str, nom_marque: str) -> bool:
    """Requete qui contient le nom de l'entreprise : les gens la cherchent deja, ce n'est pas une opportunite."""
    mots_nom = [m for m in _mots(nom_marque) if len(m) >= 3 and m not in MOTS_GENERIQUES]
    if not mots_nom:
        return False
    mots_requete = set(_mots(requete))
    return all(m in mots_requete for m in mots_nom)


def _categorie(position: float, impressions: int, clics: int) -> str:
    ctr = clics / impressions if impressions else 0.0
    if position > 10.5:
        return "page2"
    if position > 3.5:
        return "bas_page1"
    if impressions >= 50 and ctr < 0.5 * ctr_attendu(position):
        return "sans_clic"
    return ""


def _gain_estime(categorie: str, position: float, impressions: int, clics: int) -> int:
    """Visites supplementaires possibles sur la periode (estimation) si la page gagne des places, ou si son titre donne plus envie de cliquer."""
    if categorie == "sans_clic":
        ctr = clics / impressions if impressions else 0.0
        return max(0, round(impressions * (ctr_attendu(position) * 0.8 - ctr)))
    cible = 3.0 if position <= 6.5 else (7.0 if position > 10.5 else position - 3.0)
    return max(0, round(impressions * (ctr_attendu(cible) - ctr_attendu(position))))


def _est_accueil(page: str) -> bool:
    chemin = urlparse(page or "").path
    return chemin in ("", "/")


def _type_action(requete: str, page: str) -> str:
    """« article » : ecrire (ou etoffer) un contenu qui repond a la recherche ; « page » : retoucher la page deja affichee."""
    mots = _mots(requete)
    if not page or _est_accueil(page) or len(mots) >= 5 or any(m in MOTS_QUESTION for m in mots):
        return "article"
    return "page"


def _conseil(categorie: str, action: str, requete: str) -> str:
    if categorie == "sans_clic":
        return ("Beaucoup d'affichages mais peu de clics : réécris le titre et la description Google de la page pour donner envie de cliquer "
                "(bénéfice concret, ville, délai ou tarif si c'est exact).")
    if action == "article":
        debut = "Écris un article" if categorie == "page2" else "Écris ou étoffe un article"
        return f"{debut} qui répond précisément à « {requete} » (titre avec la recherche, réponse claire en haut, questions fréquentes, liens vers les pages services)."
    if categorie == "page2":
        return f"Reprends « {requete} » dans le titre, le premier paragraphe et un sous-titre de la page, puis ajoute des liens internes vers elle."
    return ("Retravaille le titre et la description Google (la recherche en tête, un bénéfice concret) et enrichis le contenu : exemples, "
            "questions fréquentes, photos.")


def analyser_recherches(lignes_requetes: list, lignes_requete_page: list, nom_marque: str = "") -> dict:
    """
    Classe les recherches proches de la page 1. lignes_* : lignes brutes de la Search Console ({"keys": [...], "clicks", "impressions", "position"}).
    Renvoie {"opportunites": [...], "pages": [...], "resume": {...}} ; opportunites triees par visites supplementaires possibles.
    """
    page_par_requete = {}
    for ligne in lignes_requete_page or []:
        cles = ligne.get("keys") or []
        if len(cles) < 2:
            continue
        impr = ligne.get("impressions", 0)
        if impr > page_par_requete.get(cles[0], (0, ""))[0]:
            page_par_requete[cles[0]] = (impr, cles[1])
    opportunites, ecartees_marque, total_impr = [], 0, 0
    for ligne in lignes_requetes or []:
        cles = ligne.get("keys") or []
        if not cles:
            continue
        requete, impr, clics, position = cles[0], int(ligne.get("impressions", 0)), int(ligne.get("clicks", 0)), float(ligne.get("position", 0.0) or 0.0)
        total_impr += impr
        if impr < SEUIL_IMPRESSIONS or position <= 0 or position > 20:
            continue
        if est_requete_de_marque(requete, nom_marque):
            ecartees_marque += 1
            continue
        categorie = _categorie(position, impr, clics)
        if not categorie:
            continue
        gain = _gain_estime(categorie, position, impr, clics)
        if gain < 1:
            continue
        page = page_par_requete.get(requete, (0, ""))[1]
        action = _type_action(requete, page)
        opportunites.append({
            "requete": requete, "page": page, "impressions": impr, "clics": clics, "ctr": round(clics / impr, 4) if impr else 0.0, "position": round(position, 1),
            "categorie": categorie, "gain": gain, "action": action, "conseil": _conseil(categorie, action, requete),
        })
    opportunites.sort(key=lambda o: (-o["gain"], -o["impressions"]))
    opportunites = opportunites[:MAX_OPPORTUNITES]
    pages = {}
    for o in opportunites:
        if o["action"] == "page" and o["page"]:
            p = pages.setdefault(o["page"], {"page": o["page"], "gain": 0, "requetes": []})
            p["gain"] += o["gain"]
            p["requetes"].append(o["requete"])
    pages = sorted(pages.values(), key=lambda p: -p["gain"])
    resume = {
        "nb": len(opportunites), "gain_total": sum(o["gain"] for o in opportunites), "ecartees_marque": ecartees_marque, "impressions": total_impr,
        "par_categorie": {cle: sum(1 for o in opportunites if o["categorie"] == cle) for cle in LIBELLES_CATEGORIES},
        "nb_articles": sum(1 for o in opportunites if o["action"] == "article"),
    }
    return {"opportunites": opportunites, "pages": pages[:10], "resume": resume}


def opportunites_recherches(identifiants, site: str, nom_marque: str = "", nb_jours: int = 90) -> dict:
    """Lit la Search Console (nb_jours jours) puis classe les opportunites ; ajoute la periode lue."""
    fin = date.today() - timedelta(days=search_console.RETARD_DONNEES_JOURS)
    debut = fin - timedelta(days=nb_jours - 1)
    requetes = search_console._requete(identifiants, site, debut, fin, ["query"], limite=2500)
    requete_page = search_console._requete(identifiants, site, debut, fin, ["query", "page"], limite=5000)
    resultat = analyser_recherches(requetes, requete_page, nom_marque)
    resultat["periode"] = {"debut": debut.isoformat(), "fin": fin.isoformat(), "jours": nb_jours}
    resultat["libelles"] = LIBELLES_CATEGORIES
    return resultat


# --------------------------------------------------------------------------- 2. analyse des pages du site

MAX_PAGES = 30
MAX_LIENS_VERIFIES = 40
EXTENSIONS_IGNOREES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".css", ".js", ".ico", ".mp4", ".mp3", ".xml", ".json", ".doc", ".docx", ".xls", ".xlsx", ".txt")
NIVEAUX = ("grave", "moyen", "leger")
POIDS = {"grave": 6, "moyen": 3, "leger": 1}
BALISES_IGNOREES = {"script", "style", "noscript", "svg", "template", "head"}


class _AnalyseurPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titre, self.description, self.robots, self.canonique, self.langue = "", "", "", "", ""
        self.viewport = False
        self.h1, self.nb_h2 = [], 0
        self.images, self.images_sans_alt = 0, 0
        self.liens, self.tel = [], False
        self.mots = 0
        self._ignore = 0
        self._dans_titre = False
        self._dans_h1 = False
        self._h1_courant = []
        self._titre_pris = False

    def handle_starttag(self, balise, attrs):
        a = dict(attrs)
        if balise == "html":
            self.langue = (a.get("lang") or "").strip().lower()
        elif balise == "title" and not self._titre_pris:
            self._dans_titre = True
        elif balise == "meta":
            nom = (a.get("name") or "").lower()
            if nom == "description" and not self.description:
                self.description = (a.get("content") or "").strip()
            elif nom == "robots":
                self.robots = (a.get("content") or "").lower()
            elif nom == "viewport":
                self.viewport = True
        elif balise == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonique = (a.get("href") or "").strip()
        elif balise == "h1":
            self._dans_h1, self._h1_courant = True, []
        elif balise == "h2":
            self.nb_h2 += 1
        elif balise == "img":
            self.images += 1
            if not (a.get("alt") or "").strip():
                self.images_sans_alt += 1
        elif balise == "a" and a.get("href"):
            href = a["href"].strip()
            if href.lower().startswith("tel:"):
                self.tel = True
            else:
                self.liens.append(href)
        if balise in BALISES_IGNOREES:
            self._ignore += 1

    def handle_endtag(self, balise):
        if balise == "title":
            self._dans_titre = False
            self._titre_pris = True
        elif balise == "h1" and self._dans_h1:
            self._dans_h1 = False
            self.h1.append(" ".join("".join(self._h1_courant).split()))
        if balise in BALISES_IGNOREES and self._ignore:
            self._ignore -= 1

    def handle_data(self, donnees):
        if self._dans_titre:
            self.titre += donnees
        if self._ignore:
            return
        if self._dans_h1:
            self._h1_courant.append(donnees)
        self.mots += len(re.findall(r"\w+", donnees))


def analyser_html(html: str) -> dict:
    """Elements utiles au SEO d'une page : titre, description, H1, nombre de mots, images, liens, robots, canonical."""
    p = _AnalyseurPage()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    return {
        "titre": " ".join(p.titre.split()), "description": " ".join(p.description.split()), "h1": [h for h in p.h1 if h], "nb_h2": p.nb_h2, "mots": p.mots,
        "images": p.images, "images_sans_alt": p.images_sans_alt, "robots": p.robots, "canonique": p.canonique, "viewport": p.viewport, "langue": p.langue,
        "liens": p.liens, "tel": p.tel,
    }


def _hote(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _lien_interne(base: str, href: str, hote_site: str) -> str:
    """Adresse absolue sans ancre ni parametres si le lien mene a une page du meme site, sinon ''."""
    if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return ""
    absolue = urldefrag(urljoin(base, href))[0]
    analyse = urlparse(absolue)
    if analyse.scheme not in ("http", "https") or _hote(absolue) != hote_site:
        return ""
    chemin = analyse.path or "/"
    if chemin.lower().endswith(EXTENSIONS_IGNOREES):
        return ""
    return f"{analyse.scheme}://{analyse.netloc}{chemin}"


def _urls_sitemap(base: str) -> list:
    """Adresses du plan du site (sitemap.xml, avec un niveau de sous-plans) ; [] s'il n'y en a pas."""
    code, texte, _, _ = geo_technique._get(urljoin(base, "/sitemap.xml"), max_octets=800_000)
    if code != 200 or "<loc" not in texte.lower():
        return []
    locs = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", texte, flags=re.IGNORECASE)
    sous_plans = [u for u in locs if u.lower().endswith(".xml")][:3]
    pages = [u for u in locs if not u.lower().endswith(".xml")]
    for sous in sous_plans:
        code, t, _, _ = geo_technique._get(sous, max_octets=800_000)
        if code == 200:
            pages += re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", t, flags=re.IGNORECASE)
    return pages


def _robots_bloque_tout(base: str) -> bool:
    code, texte, _, _ = geo_technique._get(urljoin(base, "/robots.txt"), max_octets=100_000)
    if code != 200:
        return False
    agent_toutes = False
    for ligne in texte.splitlines():
        ligne = ligne.split("#")[0].strip().lower()
        if ligne.startswith("user-agent:"):
            agent_toutes = ligne.split(":", 1)[1].strip() == "*"
        elif agent_toutes and re.match(r"disallow:\s*/\s*$", ligne):
            return True
    return False


def _probleme(niveau: str, code: str, message: str, conseil: str) -> dict:
    return {"niveau": niveau, "code": code, "message": message, "conseil": conseil}


def _problemes_page(p: dict, accueil: bool, ville: str) -> list:
    pb = []
    if p["code"] != 200:
        pb.append(_probleme("grave", "erreur_http", f"La page répond avec une erreur (code {p['code'] or 'aucune réponse'}).", "Corrige la page ou supprime les liens qui y mènent."))
        return pb
    if (p.get("url") or "").startswith("http://"):
        pb.append(_probleme("grave", "http", "La page n'est pas en HTTPS.", "Active le HTTPS sur tout le site (redirection automatique)."))
    if "noindex" in p["robots"]:
        pb.append(_probleme("grave", "noindex", "La page demande à Google de ne pas l'afficher (noindex).", "Retire le noindex si la page doit apparaître dans Google."))
    titre = p["titre"]
    if not titre:
        pb.append(_probleme("grave", "titre_absent", "Pas de titre de page.", "Ajoute un titre unique de 30 à 60 caractères avec la prestation et la ville."))
    elif len(titre) < 25:
        pb.append(_probleme("moyen", "titre_court", f"Titre trop court ({len(titre)} caractères).", "Vise 30 à 60 caractères : prestation, ville, bénéfice."))
    elif len(titre) > 65:
        pb.append(_probleme("leger", "titre_long", f"Titre trop long ({len(titre)} caractères) : Google le coupe.", "Raccourcis à 60 caractères en gardant les mots importants au début."))
    if not p["description"]:
        pb.append(_probleme("moyen", "description_absente", "Pas de description Google.", "Rédige 120 à 155 caractères qui donnent envie de cliquer (prestation, ville, bénéfice)."))
    elif len(p["description"]) < 70:
        pb.append(_probleme("leger", "description_courte", f"Description courte ({len(p['description'])} caractères).", "Vise 120 à 155 caractères."))
    elif len(p["description"]) > 165:
        pb.append(_probleme("leger", "description_longue", f"Description trop longue ({len(p['description'])} caractères) : elle sera coupée.", "Raccourcis à 155 caractères."))
    if not p["h1"]:
        pb.append(_probleme("grave", "h1_absent", "Pas de titre principal (H1).", "Ajoute un H1 qui dit clairement de quoi parle la page."))
    elif len(p["h1"]) > 1:
        pb.append(_probleme("leger", "h1_multiple", f"{len(p['h1'])} titres principaux (H1) sur la même page.", "Garde un seul H1 ; les autres deviennent des sous-titres (H2)."))
    if not accueil and p["mots"] < 200:
        pb.append(_probleme("moyen", "contenu_mince", f"Peu de texte ({p['mots']} mots).", "Étoffe la page : à qui elle s'adresse, ce que tu fais, exemples, questions fréquentes (300 mots au moins)."))
    if p["images_sans_alt"]:
        pb.append(_probleme("leger", "images_sans_alt", f"{p['images_sans_alt']} image(s) sur {p['images']} sans texte alternatif.", "Décris chaque image en quelques mots (utile à Google et à l'accessibilité)."))
    if not p["viewport"]:
        pb.append(_probleme("moyen", "pas_mobile", "Pas de réglage d'affichage mobile (viewport).", "Vérifie que le site s'affiche correctement sur téléphone."))
    if accueil and ville:
        v = _sans_accents(ville).lower()
        if v not in _sans_accents(titre + " " + " ".join(p["h1"])).lower():
            pb.append(_probleme("moyen", "ville_absente", f"Ni le titre ni le H1 de l'accueil ne citent « {ville} ».", "Pour la recherche locale, cite la ville dans le titre et le H1 de l'accueil."))
    if accueil and not p["tel"]:
        pb.append(_probleme("leger", "tel_absent", "Aucun numéro de téléphone cliquable sur l'accueil.", "Mets le numéro en lien « tel: » : un appel en un clic depuis un mobile."))
    return pb


def _recuperer(url: str) -> dict:
    code, html, finale, type_contenu = geo_technique._get(url, max_octets=900_000)
    if code == 200 and "html" not in (type_contenu or "").lower() and "<html" not in html[:2000].lower():
        return {"url": url, "code": code, "ignore": True}
    page = {"url": finale or url, "code": code, "ignore": False}
    if code == 200:
        page.update(analyser_html(html))
    return page


def _statut_lien(url: str) -> int:
    code, _, _, _ = geo_technique._get(url, max_octets=2000)
    return code


def explorer(url_site: str, ville: str = "", max_pages: int = MAX_PAGES, delai_total: float = 75.0) -> dict:
    """
    Parcourt le site (accueil, plan du site, liens internes ; max_pages pages) et renvoie
    {"url", "score", "pages": [...], "problemes_globaux": [...], "points_forts": [...], "resume": {...}}.
    Leve geo_technique.ErreurControle si l'adresse est invalide ou injoignable.
    """
    base = geo_technique.normaliser_url(url_site)
    hote_site = _hote(base)
    accueil = _recuperer(base)
    if accueil["code"] != 200:
        raise geo_technique.ErreurControle("Le site ne répond pas (ou refuse la visite) : impossible de l'analyser.")
    racine = urlparse(accueil["url"])
    base_finale = f"{racine.scheme}://{racine.netloc}/"
    hote_site = _hote(base_finale) or hote_site
    sitemap = _urls_sitemap(base_finale)
    a_visiter, vus = [], {accueil["url"].rstrip("/")}
    candidates = [_lien_interne(base_finale, u, hote_site) for u in sitemap] + [_lien_interne(accueil["url"], h, hote_site) for h in accueil.get("liens", [])]
    for u in candidates:
        if u and u.rstrip("/") not in vus:
            vus.add(u.rstrip("/"))
            a_visiter.append(u)
    a_visiter = a_visiter[:max(0, max_pages - 1)]
    pages = [accueil]
    with ThreadPoolExecutor(max_workers=6) as groupe:
        futures = {groupe.submit(_recuperer, u): u for u in a_visiter}
        try:
            for futur in as_completed(futures, timeout=delai_total):
                try:
                    pages.append(futur.result())
                except Exception:
                    pages.append({"url": futures[futur], "code": 0, "ignore": False})
        except DelaiDepasse:
            for futur in futures:
                futur.cancel()
    pages = [p for p in pages if not p.get("ignore")]
    casses = [{"url": p["url"], "code": p["code"]} for p in pages[1:] if p["code"] in (0, 404, 410)]       # liens de l'accueil ou du plan du site qui menent a une page absente
    pages = [pages[0]] + [p for p in pages[1:] if p["code"] not in (0, 404, 410)]
    # liens internes cites mais non visites : verifies (un lien casse est un vrai probleme pour le visiteur et pour Google)
    deja = {p["url"].rstrip("/") for p in pages} | {u.rstrip("/") for u in a_visiter}
    a_verifier = []
    for p in pages:
        for href in p.get("liens", []):
            cible = _lien_interne(p["url"], href, hote_site)
            if cible and cible.rstrip("/") not in deja and cible not in a_verifier:
                a_verifier.append(cible)
    a_verifier = a_verifier[:MAX_LIENS_VERIFIES]
    if a_verifier:
        with ThreadPoolExecutor(max_workers=8) as groupe:
            futurs = {groupe.submit(_statut_lien, u): u for u in a_verifier}
            wait(futurs, timeout=40)
            for futur, u in futurs.items():
                if not futur.done() or futur.exception():
                    continue
                code_lien = futur.result()
                if code_lien in (0, 404, 410) or code_lien >= 500:
                    casses.append({"url": u, "code": code_lien})
    # problemes par page, puis doublons entre pages
    for p in pages:
        chemin = urlparse(p["url"]).path
        p["accueil"] = chemin in ("", "/")
        p["problemes"] = _problemes_page(p, p["accueil"], ville) if p["code"] else []
        p.pop("liens", None)
    for champ, code, libelle, conseil in (("titre", "titre_double", "Même titre que d'autres pages", "Donne un titre unique à chaque page."),
                                          ("description", "description_double", "Même description que d'autres pages", "Rédige une description unique pour chaque page.")):
        comptes = {}
        for p in pages:
            if p.get(champ):
                comptes.setdefault(p[champ].lower(), []).append(p)
        for groupe_pages in comptes.values():
            if len(groupe_pages) > 1:
                for p in groupe_pages:
                    p["problemes"].append(_probleme("moyen", code, f"{libelle} ({len(groupe_pages)} pages).", conseil))
    globaux = []
    if base_finale.startswith("http://"):
        globaux.append(_probleme("grave", "site_http", "Le site n'est pas en HTTPS.", "Active le HTTPS (certificat) et redirige toutes les adresses."))
    if _robots_bloque_tout(base_finale):
        globaux.append(_probleme("grave", "robots_bloque", "Le fichier robots.txt interdit l'accès à tout le site.", "Retire « Disallow: / » : Google ne peut pas explorer le site."))
    if not sitemap:
        globaux.append(_probleme("moyen", "pas_de_plan", "Pas de plan du site (sitemap.xml) trouvé.", "Ajoute un sitemap.xml et déclare-le dans la Search Console."))
    if casses:
        globaux.append(_probleme("moyen", "liens_casses", f"{len(casses)} lien(s) interne(s) cassé(s).", "Corrige ou retire ces liens : " + ", ".join(c["url"] for c in casses[:5]) + ("..." if len(casses) > 5 else "")))
    nb = len(pages)
    penalites = sum(POIDS[pb["niveau"]] for p in pages for pb in p["problemes"]) + sum(POIDS[pb["niveau"]] * 2 for pb in globaux)
    score = max(0, min(100, round(100 - 100 * penalites / max(1, nb * 12))))
    forts = []
    ok_pages = [p for p in pages if p["code"] == 200]
    if ok_pages and all(p["titre"] for p in ok_pages) and not any(pb["code"] == "titre_double" for p in pages for pb in p["problemes"]):
        forts.append("Chaque page a un titre, et ils sont tous différents.")
    if ok_pages and all(p["description"] for p in ok_pages):
        forts.append("Chaque page a une description Google.")
    if ok_pages and all(len(p["h1"]) == 1 for p in ok_pages):
        forts.append("Chaque page a un seul titre principal (H1).")
    if base_finale.startswith("https://"):
        forts.append("Le site est en HTTPS.")
    if sitemap:
        forts.append(f"Un plan du site est présent ({len(sitemap)} adresses).")
    if not casses and a_verifier:
        forts.append("Aucun lien interne cassé parmi ceux vérifiés.")
    moyenne = round(sum(p["mots"] for p in ok_pages) / len(ok_pages)) if ok_pages else 0
    if moyenne >= 300:
        forts.append(f"Pages riches en texte (environ {moyenne} mots en moyenne).")
    resume = {"nb_pages": nb, "score": score, "mots_moyens": moyenne, "liens_verifies": len(a_verifier), "liens_casses": len(casses),
              **{niveau: sum(1 for p in pages for pb in p["problemes"] if pb["niveau"] == niveau) + sum(1 for pb in globaux if pb["niveau"] == niveau) for niveau in NIVEAUX}}
    pages.sort(key=lambda p: (-sum(POIDS[pb["niveau"]] for pb in p["problemes"]), p["url"]))
    return {"url": base_finale, "score": score, "pages": pages, "problemes_globaux": globaux, "points_forts": forts, "resume": resume}
