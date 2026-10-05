"""
Controle technique GEO : le site du client est-il lisible par les assistants IA (ChatGPT, Perplexity, Gemini, Claude) ?
Verifie robots.txt, le blocage par pare-feu, llms.txt, le sitemap, les donnees structurees (JSON-LD) et la lisibilite du
texte sans JavaScript. Fournit aussi deux generateurs : llms.txt (IA) et JSON-LD LocalBusiness (deterministe).
"""

import ipaddress
import json
import os
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from anthropic import Anthropic

MODELE_CLAUDE = "claude-sonnet-5"
DELAI_REQUETE = 12
UA_NAVIGATEUR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# (nom dans robots.txt, usage, indispensable pour etre cite, user-agent complet pour tester le pare-feu)
ROBOTS_IA = [
    ("OAI-SearchBot", "ChatGPT Search (réponses avec sources)", True,
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot"),
    ("ChatGPT-User", "ChatGPT quand un utilisateur demande de consulter une page", True,
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot"),
    ("PerplexityBot", "Perplexity", True,
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"),
    ("Googlebot", "Google (et ses réponses IA)", True,
     "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("GPTBot", "OpenAI, entraînement des modèles", False,
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot"),
    ("ClaudeBot", "Claude (Anthropic)", False,
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"),
    ("Google-Extended", "Gemini, utilisation du contenu par les modèles de Google", False, None),
]
MARQUEURS_DEFI = ("just a moment", "cf-chl", "attention required", "access denied", "captcha", "checking your browser")
TYPES_ENTREPRISE_LOCALE = {
    "localbusiness", "locksmith", "plumber", "electrician", "hairsalon", "beautysalon", "restaurant", "bakery", "dentist",
    "physician", "attorney", "legalservice", "autorepair", "realestateagent", "store", "healthandbeautybusiness", "foodestablishment",
    "homeandconstructionbusiness", "professionalservice", "medicalbusiness", "accountingservice", "autodealer", "dayspa",
    "gymnasium", "sportsactivitylocation", "veterinarycare", "generalcontractor", "roofingcontractor", "housepainter", "movingcompany",
    "cafeorcoffeeshop", "hotel", "lodgingbusiness", "childcare", "financialservice", "insuranceagency", "travelagency", "petstore",
}


class ErreurControle(Exception):
    pass


def normaliser_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ErreurControle("Aucune adresse de site renseignée.")
    if "://" not in url:
        url = "https://" + url
    analyse = urlparse(url)
    if analyse.scheme not in ("http", "https") or not analyse.netloc:
        raise ErreurControle("Adresse de site invalide.")
    return f"{analyse.scheme}://{analyse.netloc}{analyse.path or '/'}"


def _hote_public(hote: str) -> bool:
    """Refuse localhost et les adresses privees (le serveur ne doit pas servir a sonder un reseau interne)."""
    try:
        adresses = {info[4][0] for info in socket.getaddrinfo(hote, None)}
    except socket.gaierror:
        return False
    for adresse in adresses:
        ip = ipaddress.ip_address(adresse.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(adresses)


def _get(url: str, ua: str = UA_NAVIGATEUR, max_octets: int = 1_500_000):
    """(code, texte, url_finale, type_contenu) ; code 0 en cas d'erreur reseau. Redirections suivies a la main (controle d'hote)."""
    courant = url
    for _ in range(5):
        hote = urlparse(courant).hostname or ""
        if not _hote_public(hote):
            return 0, "", courant, ""
        try:
            reponse = requests.get(
                courant, headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "fr-FR,fr;q=0.9"},
                timeout=DELAI_REQUETE, allow_redirects=False, stream=True,
            )
        except requests.RequestException:
            return 0, "", courant, ""
        if reponse.status_code in (301, 302, 303, 307, 308) and reponse.headers.get("Location"):
            courant = urljoin(courant, reponse.headers["Location"])
            reponse.close()
            continue
        contenu = reponse.raw.read(max_octets, decode_content=True)
        reponse.close()
        encodage = reponse.encoding or "utf-8"
        try:
            texte = contenu.decode(encodage, errors="replace")
        except LookupError:
            texte = contenu.decode("utf-8", errors="replace")
        return reponse.status_code, texte, courant, reponse.headers.get("Content-Type", "")
    return 0, "", courant, ""


class _AnalyseurPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titre = ""
        self.description = ""
        self.nb_h1 = 0
        self.json_ld = []
        self.texte = []
        self._pile = []
        self._tampon_ld = None

    def handle_starttag(self, balise, attrs):
        attrs = dict(attrs)
        self._pile.append(balise)
        if balise == "meta" and (attrs.get("name") or "").lower() == "description":
            self.description = attrs.get("content") or ""
        elif balise == "h1":
            self.nb_h1 += 1
        elif balise == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._tampon_ld = []

    def handle_endtag(self, balise):
        if balise == "script" and self._tampon_ld is not None:
            self.json_ld.append("".join(self._tampon_ld))
            self._tampon_ld = None
        if balise in self._pile:
            while self._pile and self._pile.pop() != balise:
                pass

    def handle_data(self, donnees):
        if self._tampon_ld is not None:
            self._tampon_ld.append(donnees)
            return
        if self._pile and self._pile[-1] == "title":
            self.titre += donnees
        if not any(b in ("script", "style", "noscript", "head", "svg", "template") for b in self._pile):
            texte = donnees.strip()
            if texte:
                self.texte.append(texte)


def analyser_page(html: str) -> dict:
    analyseur = _AnalyseurPage()
    try:
        analyseur.feed(html)
        analyseur.close()
    except Exception:
        pass
    types = set()

    def collecter(noeud):
        if isinstance(noeud, list):
            for element in noeud:
                collecter(element)
        elif isinstance(noeud, dict):
            valeur = noeud.get("@type")
            for t in ([valeur] if isinstance(valeur, str) else valeur or []):
                types.add(str(t).split("/")[-1])
            collecter(noeud.get("@graph", []))
            for cle in ("mainEntity", "about", "publisher", "provider"):
                if cle in noeud:
                    collecter(noeud[cle])

    for bloc in analyseur.json_ld:
        try:
            collecter(json.loads(bloc))
        except ValueError:
            continue
    texte = " ".join(analyseur.texte)
    return {
        "titre": re.sub(r"\s+", " ", analyseur.titre).strip(), "description": analyseur.description.strip(), "nb_h1": analyseur.nb_h1,
        "types_json_ld": sorted(types), "nb_blocs_json_ld": len(analyseur.json_ld), "longueur_texte": len(texte),
        "extrait_texte": texte[:6000],
    }


def _lire_loc(xml: str) -> list:
    """Adresses des balises <loc> (avec ou sans CDATA)."""
    return [u.strip() for u in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)\s*(?:\]\]>)?\s*</loc>", xml)]


def _controle(code, titre, statut, detail, correctif=""):
    return {"code": code, "titre": titre, "statut": statut, "detail": detail, "correctif": correctif}


def _autorise_par_robots(lignes: list, agent: str, url: str) -> bool:
    parseur = RobotFileParser()
    parseur.parse(lignes)
    return parseur.can_fetch(agent, url)


def controler_site(url_site: str) -> dict:
    """Retourne {"url", "score", "controles": [...], "page": {...}, "sitemap_urls": [...]}."""
    url = normaliser_url(url_site)
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    controles = []

    code, html, url_finale, _ = _get(url)
    if code != 200:
        raise ErreurControle(
            f"Le site {url} n'a pas pu être consulté (code {code or 'aucune réponse'}). Vérifiez l'adresse ou que le site est en ligne."
        )
    base_finale = f"{urlparse(url_finale).scheme}://{urlparse(url_finale).netloc}"
    page = analyser_page(html)

    controles.append(
        _controle("https", "Site en HTTPS", "ok", "Le site est servi en connexion sécurisée.") if url_finale.startswith("https://")
        else _controle("https", "Site en HTTPS", "probleme", "Le site n'est pas servi en HTTPS.",
                       "Activez le certificat SSL chez l'hébergeur et redirigez tout vers https.")
    )

    code_robots, texte_robots, _, _ = _get(base_finale + "/robots.txt")
    lignes_robots = texte_robots.splitlines() if code_robots == 200 and "<html" not in texte_robots[:300].lower() else []
    for agent, usage, indispensable, _ua in ROBOTS_IA:
        autorise = _autorise_par_robots(lignes_robots, agent, base_finale + "/") if lignes_robots else True
        if autorise:
            controles.append(_controle(f"robots_{agent}", f"robots.txt : {agent}", "ok", f"{agent} ({usage}) est autorisé."))
        elif indispensable:
            controles.append(_controle(
                f"robots_{agent}", f"robots.txt : {agent}", "probleme", f"{agent} ({usage}) est bloqué par le fichier robots.txt.",
                f"Dans robots.txt, retirez le blocage de {agent} (ligne « User-agent: {agent} » suivie de « Disallow: / »).",
            ))
        else:
            controles.append(_controle(
                f"robots_{agent}", f"robots.txt : {agent}", "avertissement",
                f"{agent} ({usage}) est bloqué. C'est un choix possible, mais cela limite la présence dans cet assistant.",
                f"Si vous voulez apparaître aussi chez cet assistant, retirez le blocage de {agent} dans robots.txt.",
            ))

    bloques = []
    for agent, _usage, _ind, ua in ROBOTS_IA:
        if not ua or agent == "Googlebot":
            continue
        code_ia, texte_ia, _, _ = _get(url_finale, ua=ua)
        defi = any(m in texte_ia[:3000].lower() for m in MARQUEURS_DEFI) and len(texte_ia) < 20000
        if code_ia in (401, 403, 429, 503) or defi:
            bloques.append(agent)
    if bloques:
        controles.append(_controle(
            "pare_feu", "Pare-feu et robots IA", "avertissement",
            f"Le site refuse les visites présentées comme : {', '.join(bloques)}. Soit un pare-feu bloque ces robots, soit une protection "
            f"écarte les faux robots (cas fréquent, le test ne vient pas des vraies adresses d'OpenAI ou de Perplexity).",
            "Vérifiez dans le pare-feu de l'hébergeur (Cloudflare, Wordfence, Sucuri...) que les robots IA ne sont pas bloqués par défaut.",
        ))
    else:
        controles.append(_controle("pare_feu", "Pare-feu et robots IA", "ok", "Le site répond normalement aux robots des assistants IA."))

    code_llms, texte_llms, _, type_llms = _get(base_finale + "/llms.txt")
    if code_llms == 200 and "html" not in type_llms.lower() and texte_llms.strip() and not texte_llms.lstrip().startswith("<"):
        controles.append(_controle("llms_txt", "Fichier llms.txt", "ok", "Le site publie un fichier llms.txt."))
    else:
        controles.append(_controle(
            "llms_txt", "Fichier llms.txt", "avertissement",
            "Pas de fichier llms.txt : les assistants n'ont pas de résumé clair de l'activité et des pages importantes.",
            "Générez le fichier ci-dessous puis déposez-le à la racine du site (adresse /llms.txt).",
        ))

    sitemap_urls, sitemap_trouve = [], False
    candidats = [m.group(1).strip() for m in re.finditer(r"(?im)^\s*sitemap:\s*(\S+)", "\n".join(lignes_robots))]
    candidats += [base_finale + chemin for chemin in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
    for candidat in dict.fromkeys(candidats):
        code_sm, xml, _, _ = _get(candidat)
        if code_sm == 200 and ("<urlset" in xml or "<sitemapindex" in xml):
            sitemap_trouve = True
            sitemap_urls = _lire_loc(xml)
            if "<sitemapindex" in xml and sitemap_urls:
                # index de sitemaps : on lit d'abord celui des pages (les articles de blog viennent ensuite)
                enfants = sorted(sitemap_urls, key=lambda u: 0 if "page" in u.lower() and "post" not in u.lower() else 1)[:3]
                sitemap_urls = []
                for enfant in enfants:
                    code_sm2, xml2, _, _ = _get(enfant)
                    if code_sm2 == 200:
                        sitemap_urls += _lire_loc(xml2)
            break
    controles.append(
        _controle("sitemap", "Plan du site (sitemap)", "ok", f"Un sitemap est publié ({len(sitemap_urls)} adresses lues).") if sitemap_trouve
        else _controle("sitemap", "Plan du site (sitemap)", "avertissement", "Aucun sitemap.xml trouvé.",
                       "Activez le sitemap (extension SEO du site) et déclarez-le dans robots.txt.")
    )

    types_min = {t.lower() for t in page["types_json_ld"]}
    if types_min & TYPES_ENTREPRISE_LOCALE:
        controles.append(_controle("json_ld_local", "Données structurées de l'entreprise", "ok",
                                   f"Types trouvés : {', '.join(page['types_json_ld'])}."))
    elif types_min & {"organization", "website", "webpage"}:
        controles.append(_controle(
            "json_ld_local", "Données structurées de l'entreprise", "avertissement",
            f"Seuls des types généraux sont présents ({', '.join(page['types_json_ld'])}), pas de fiche d'entreprise locale.",
            "Ajoutez le bloc LocalBusiness généré ci-dessous (adresse, téléphone, horaires, zone de service).",
        ))
    else:
        controles.append(_controle(
            "json_ld_local", "Données structurées de l'entreprise", "probleme",
            "Aucune donnée structurée d'entreprise locale : les assistants doivent deviner l'adresse, les horaires et la zone desservie.",
            "Ajoutez le bloc LocalBusiness généré ci-dessous dans la page d'accueil.",
        ))
    controles.append(
        _controle("json_ld_faq", "Questions fréquentes balisées (FAQPage)", "ok", "Une FAQ balisée est présente.") if "faqpage" in types_min
        else _controle("json_ld_faq", "Questions fréquentes balisées (FAQPage)", "avertissement",
                       "Pas de FAQ balisée. Les assistants reprennent volontiers des réponses courtes et structurées.",
                       "Ajoutez une section de questions fréquentes (5 à 8 questions réelles de clients) balisée en FAQPage.")
    )

    longueur = page["longueur_texte"]
    if longueur >= 400:
        controles.append(_controle("texte_sans_js", "Texte lisible sans JavaScript", "ok", f"{longueur} caractères de texte lisibles directement."))
    elif longueur >= 150:
        controles.append(_controle("texte_sans_js", "Texte lisible sans JavaScript", "avertissement",
                                   f"Seulement {longueur} caractères de texte lisibles directement, le reste est peut-être chargé par JavaScript.",
                                   "Vérifiez que le contenu important (services, zone, contact) est bien écrit dans la page et non chargé après coup."))
    else:
        controles.append(_controle("texte_sans_js", "Texte lisible sans JavaScript", "probleme",
                                   f"Presque aucun texte n'est lisible directement ({longueur} caractères) : les robots IA ne lisent généralement pas le JavaScript.",
                                   "Faites afficher le contenu côté serveur (ou utilisez un site classique) pour que les assistants puissent le lire."))

    manques = []
    if not (10 <= len(page["titre"]) <= 70):
        manques.append("un titre de 10 à 70 caractères" if page["titre"] else "un titre")
    if len(page["description"]) < 50:
        manques.append("une meta description (50 caractères ou plus)")
    if page["nb_h1"] != 1:
        manques.append("un seul titre H1" if page["nb_h1"] else "un titre H1")
    controles.append(
        _controle("balises", "Titre, description et H1", "ok", "Titre, meta description et H1 sont en place.") if not manques
        else _controle("balises", "Titre, description et H1", "avertissement", "À corriger sur la page d'accueil : " + ", ".join(manques) + ".",
                       "Renseignez-les dans l'éditeur de la page ou l'extension SEO.")
    )

    points = {"ok": 1.0, "avertissement": 0.5, "probleme": 0.0}
    score = round(100 * sum(points[c["statut"]] for c in controles) / len(controles))
    return {"url": url_finale, "score": score, "controles": controles, "page": page, "sitemap_urls": sitemap_urls[:30]}


# ------------------------------------------------------------------ generateurs

TYPES_PAR_MOT_CLE = [
    ("serrur", "Locksmith"), ("plomb", "Plumber"), ("électric", "Electrician"), ("electric", "Electrician"), ("coiff", "HairSalon"),
    ("esthét", "BeautySalon"), ("esthet", "BeautySalon"), ("beauté", "BeautySalon"), ("restaurant", "Restaurant"), ("boulang", "Bakery"),
    ("dentiste", "Dentist"), ("médecin", "Physician"), ("medecin", "Physician"), ("avocat", "LegalService"), ("notaire", "LegalService"),
    ("garage", "AutoRepair"), ("réparation auto", "AutoRepair"), ("immobili", "RealEstateAgent"), ("comptab", "AccountingService"),
    ("vétérinaire", "VeterinaryCare"), ("veterinaire", "VeterinaryCare"), ("café", "CafeOrCoffeeShop"), ("hôtel", "Hotel"),
    ("hotel", "Hotel"), ("assurance", "InsuranceAgency"), ("entreprise de construction", "GeneralContractor"), ("couvreur", "RoofingContractor"),
    ("peintre", "HousePainter"), ("déménag", "MovingCompany"), ("demenag", "MovingCompany"), ("salle de sport", "GymnasiumOrHealthClub"),
]
JOURS_SCHEMA = {"MONDAY": "Monday", "TUESDAY": "Tuesday", "WEDNESDAY": "Wednesday", "THURSDAY": "Thursday", "FRIDAY": "Friday",
                "SATURDAY": "Saturday", "SUNDAY": "Sunday"}


def _heure(valeur: dict) -> str:
    valeur = valeur or {}
    heures, minutes = int(valeur.get("hours", 0) or 0), int(valeur.get("minutes", 0) or 0)
    if heures >= 24:
        return "23:59"
    return f"{heures:02d}:{minutes:02d}"


def type_schema(categorie: str) -> str:
    categorie = (categorie or "").lower()
    for mot, type_ in TYPES_PAR_MOT_CLE:
        if mot in categorie:
            return type_
    return "LocalBusiness"


def construire_json_ld(client, infos: dict = None, site_web: str = "", categorie: str = "") -> str:
    """Bloc <script type="application/ld+json"> LocalBusiness construit uniquement a partir de donnees connues (jamais inventees)."""
    infos = infos or {}
    donnees = {"@context": "https://schema.org", "@type": type_schema(categorie), "name": infos.get("title") or client.nom}
    if site_web:
        donnees["url"] = site_web
    telephone = (infos.get("phoneNumbers") or {}).get("primaryPhone")
    if telephone:
        donnees["telephone"] = telephone
    description = (infos.get("profile") or {}).get("description")
    if description:
        donnees["description"] = description
    adresse = infos.get("storefrontAddress") or {}
    if adresse:
        postale = {"@type": "PostalAddress"}
        if adresse.get("addressLines"):
            postale["streetAddress"] = ", ".join(adresse["addressLines"])
        for cle_source, cle_cible in (("locality", "addressLocality"), ("postalCode", "postalCode"), ("regionCode", "addressCountry")):
            if adresse.get(cle_source):
                postale[cle_cible] = adresse[cle_source]
        donnees["address"] = postale
    latlng = infos.get("latlng") or {}
    if latlng.get("latitude") is not None and latlng.get("longitude") is not None:
        donnees["geo"] = {"@type": "GeoCoordinates", "latitude": latlng["latitude"], "longitude": latlng["longitude"]}
    periodes = (infos.get("regularHours") or {}).get("periods") or []
    horaires = [
        {"@type": "OpeningHoursSpecification", "dayOfWeek": JOURS_SCHEMA[p["openDay"]], "opens": _heure(p.get("openTime")), "closes": _heure(p.get("closeTime"))}
        for p in periodes if p.get("openDay") in JOURS_SCHEMA and p.get("openDay") == p.get("closeDay")
    ]
    if horaires:
        donnees["openingHoursSpecification"] = horaires
    if client.localisation_ville:
        donnees["areaServed"] = {"@type": "City", "name": client.localisation_ville}
    lien_maps = (infos.get("metadata") or {}).get("mapsUri")
    if lien_maps:
        donnees["hasMap"] = lien_maps
        donnees["sameAs"] = [lien_maps]
    return '<script type="application/ld+json">\n' + json.dumps(donnees, ensure_ascii=False, indent=2) + "\n</script>"


def generer_llms_txt(client, infos: dict, site_web: str, urls_site: list, categorie: str = "") -> str:
    """Fichier llms.txt (resume du site pour les assistants IA) a partir de faits connus ; les liens viennent uniquement du sitemap."""
    adresse = infos.get("storefrontAddress") or {}
    faits = {
        "nom": infos.get("title") or client.nom, "site": site_web, "activite": categorie, "ville": client.localisation_ville or adresse.get("locality", ""),
        "adresse": ", ".join((adresse.get("addressLines") or []) + [adresse.get("postalCode", ""), adresse.get("locality", "")]).strip(", "),
        "telephone": (infos.get("phoneNumbers") or {}).get("primaryPhone", ""), "description_google": (infos.get("profile") or {}).get("description", ""),
    }
    prompt = (
        "Rédige un fichier llms.txt en français pour le site d'une entreprise locale, afin que les assistants IA comprennent son activité.\n\n"
        "Format exact (Markdown) :\n"
        "# Nom de l'entreprise\n> Résumé en 2 phrases : activité, zone desservie, ce qui la distingue (uniquement des faits fournis).\n\n"
        "## Services\n- liste courte des services (uniquement ceux qui ressortent des informations fournies)\n\n"
        "## Informations pratiques\n- Adresse, téléphone, zone desservie (si fournis)\n\n"
        "## Pages principales\n- [Titre de la page](adresse): une phrase\n\n"
        "Règles : n'invente aucun fait (prix, années d'expérience, certifications, avis). Pour « Pages principales », utilise UNIQUEMENT des "
        "adresses de la liste fournie (6 à 10 pages utiles : accueil, services, contact, à propos, FAQ). Pas d'emoji, pas de tiret cadratin. "
        "Réponds uniquement par le contenu du fichier.\n\n"
        f"Informations connues :\n{json.dumps(faits, ensure_ascii=False)}\n\n"
        f"Texte du site (extrait) :\n{(client.contenu_site or '')[:6000]}\n\n"
        f"Adresses disponibles :\n" + "\n".join(urls_site[:30] or [site_web])
    )
    reponse = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")).messages.create(
        model=MODELE_CLAUDE, max_tokens=1800, thinking={"type": "disabled"}, messages=[{"role": "user", "content": prompt}],
    )
    texte = next((b.text for b in reponse.content if b.type == "text"), "").strip()
    texte = re.sub(r"^```(?:markdown|md|text)?\s*|\s*```$", "", texte).strip()
    return texte.replace("—", "-").replace("–", "-")
