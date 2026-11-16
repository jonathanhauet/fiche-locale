"""
Suggestions de recherches pour l'audit, tirees du site du prospect : les recherches testees sur la carte et les questions posees aux IA doivent parler de
ce que l'entreprise fait VRAIMENT (ses services, tels qu'ils sont ecrits sur son site), pas seulement de sa categorie Google.

On lit la page d'accueil et quelques pages du menu (titres, intertitres, liens), puis Claude propose des recherches que des clients taperaient et des questions
qu'ils poseraient a une IA, en s'appuyant uniquement sur les services presents dans le texte lu. Sans cle Claude ou si l'appel echoue, une methode simple
(entrees du menu + categorie) prend le relais.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

MOTS_GENERIQUES = {
    "accueil", "contact", "contactez-nous", "nous contacter", "blog", "actualites", "actualités", "a propos", "à propos", "qui sommes-nous", "mentions legales", "mentions légales",
    "cgv", "cgu", "conditions generales", "conditions générales", "politique de confidentialite", "politique de confidentialité", "panier", "mon compte", "connexion", "se connecter",
    "faq", "plan du site", "recrutement", "devis", "devis gratuit", "rendez-vous", "prendre rendez-vous", "avis", "avis clients", "temoignages", "témoignages", "realisations", "réalisations",
    "galerie", "newsletter", "cookies", "rechercher", "menu", "retour", "suivant", "précédent", "precedent", "lire la suite", "en savoir plus", "voir plus", "nos engagements", "notre equipe", "notre équipe",
}
MAX_PAGES_LUES = 3
MAX_TITRES = 40
MAX_LIENS = 60


class _Lecteur(HTMLParser):
    """Titre, description, intertitres (h1 a h3) et liens internes d'une page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titre = ""
        self.description = ""
        self.titres = []
        self.liens = []
        self._pile = []
        self._tampon = None
        self._lien = None

    def handle_starttag(self, balise, attrs):
        attrs = dict(attrs)
        self._pile.append(balise)
        if balise == "meta" and (attrs.get("name") or "").lower() == "description":
            self.description = attrs.get("content") or ""
        elif balise in ("h1", "h2", "h3"):
            self._tampon = []
        elif balise == "a" and attrs.get("href"):
            self._lien = {"href": attrs["href"], "texte": []}

    def handle_endtag(self, balise):
        if balise in ("h1", "h2", "h3") and self._tampon is not None:
            texte = re.sub(r"\s+", " ", "".join(self._tampon)).strip()
            if 3 <= len(texte) <= 110:
                self.titres.append(texte)
            self._tampon = None
        elif balise == "a" and self._lien is not None:
            texte = re.sub(r"\s+", " ", "".join(self._lien["texte"])).strip()
            if texte:
                self.liens.append((texte, self._lien["href"]))
            self._lien = None
        if balise in self._pile:
            while self._pile and self._pile.pop() != balise:
                pass

    def handle_data(self, donnees):
        if self._pile and self._pile[-1] == "title" and not self.titre:
            self.titre = donnees.strip()
        if self._tampon is not None:
            self._tampon.append(donnees)
        if self._lien is not None:
            self._lien["texte"].append(donnees)


def lire_page(html: str, url_base: str) -> dict:
    """{"titre", "description", "titres": [...], "liens": [(texte, url interne)]} d'une page."""
    lecteur = _Lecteur()
    try:
        lecteur.feed(html or "")
        lecteur.close()
    except Exception:
        pass
    hote = (urlparse(url_base).netloc or "").lower().removeprefix("www.")
    liens, vus = [], set()
    for texte, href in lecteur.liens:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(url_base, href).split("#")[0]
        analyse = urlparse(url)
        if analyse.scheme not in ("http", "https") or (analyse.netloc or "").lower().removeprefix("www.") != hote:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|css|js)(\?|$)", analyse.path, re.I):
            continue
        cle = (analyse.path.rstrip("/") or "/", texte.lower())
        if cle in vus or len(texte) < 3 or len(texte) > 45:
            continue
        vus.add(cle)
        liens.append((texte, url))
    return {"titre": re.sub(r"\s+", " ", lecteur.titre).strip()[:160], "description": re.sub(r"\s+", " ", lecteur.description).strip()[:300], "titres": lecteur.titres[:MAX_TITRES], "liens": liens[:MAX_LIENS]}


def entrees_menu(liens: list) -> list:
    """Intitules de menu qui decrivent un service ou un produit (les rubriques generiques — contact, blog, mentions legales... — sont ecartees)."""
    resultat, vus = [], set()
    for texte, _ in liens:
        cle = texte.lower().strip(" :-")
        if cle in MOTS_GENERIQUES or any(m in cle for m in ("mentions", "confidentialit", "cookies", "conditions", "connexion", "panier", "mon compte")):
            continue
        if re.fullmatch(r"[\d\W]+", cle) or len(cle.split()) > 7 or cle in vus:
            continue
        vus.add(cle)
        resultat.append(texte.strip())
    return resultat


def lire_site(url: str, lire_html) -> dict:
    """Page d'accueil + quelques pages du menu. lire_html(url) -> texte HTML ('' si illisible)."""
    accueil_html = lire_html(url)
    if not accueil_html:
        return {}
    accueil = lire_page(accueil_html, url)
    menu = entrees_menu(accueil["liens"])
    cibles = []
    for texte, lien in accueil["liens"]:
        if texte in menu and urlparse(lien).path.rstrip("/") != urlparse(url).path.rstrip("/") and lien not in [c[1] for c in cibles]:
            cibles.append((texte, lien))
        if len(cibles) >= MAX_PAGES_LUES:
            break

    def une(cible):
        try:
            return lire_page(lire_html(cible[1]), cible[1])
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(pool.map(une, cibles))
    return {"accueil": accueil, "menu": menu[:30], "pages": [{"intitule": c[0], "titre": p.get("titre", ""), "titres": p.get("titres", [])[:12]} for c, p in zip(cibles, pages) if p]}


# ---------------------------------------------------------------------------------------------------------------- suggestions

def _nettoyer_mot_cle(mot: str, ville: str) -> str:
    mot = re.sub(r"\s+", " ", str(mot or "")).strip(" .,;\"'").lower()
    if not mot or len(mot) > 70:
        return ""
    if ville.lower() not in mot:
        mot = f"{mot} {ville.lower()}"
    return mot


def _nettoyer_question(question: str, ville: str) -> str:
    question = re.sub(r"\s+", " ", str(question or "")).strip()
    if not question or len(question) > 160:
        return ""
    if ville.lower() not in question.lower():
        question = question.rstrip(" ?.") + f" à {ville}" + (" ?" if question.endswith("?") else "")
    return question[0].upper() + question[1:]


def heuristique(fiche: dict, ville: str, site: dict) -> dict:
    """Suggestions sans IA : intitules du menu et categorie Google, avec la ville."""
    categorie = (fiche.get("categorie") or "").strip()
    services = (site or {}).get("menu", [])[:6]
    mots, questions = [], []
    if categorie:
        mots.append({"mot_cle": f"{categorie.lower()} {ville.lower()}", "pourquoi": "Catégorie de la fiche Google."})
        questions.append({"question": f"Quel est le meilleur {categorie.lower()} à {ville} ?", "pourquoi": "Question classique d'un client pour sa catégorie."})
    for s in services:
        mots.append({"mot_cle": f"{s.lower()} {ville.lower()}", "pourquoi": "Rubrique du menu du site."})
        questions.append({"question": f"Qui peut m'aider pour « {s.lower()} » à {ville} ?", "pourquoi": "Rubrique du menu du site."})
    return {"mots_cles": mots[:6], "questions_ia": questions[:5]}


SCHEMA = {
    "type": "object",
    "properties": {
        "services": {"type": "array", "items": {"type": "string"}},
        "mots_cles": {"type": "array", "items": {"type": "object", "properties": {"mot_cle": {"type": "string"}, "pourquoi": {"type": "string"}}, "required": ["mot_cle", "pourquoi"], "additionalProperties": False}},
        "questions_ia": {"type": "array", "items": {"type": "object", "properties": {"question": {"type": "string"}, "pourquoi": {"type": "string"}}, "required": ["question", "pourquoi"], "additionalProperties": False}},
    },
    "required": ["services", "mots_cles", "questions_ia"], "additionalProperties": False,
}


def proposer_avec_claude(fiche: dict, ville: str, site: dict) -> dict:
    """Claude propose des recherches et des questions d'apres le texte du site (et uniquement lui)."""
    import anthropic
    from . import ia_visibilite

    if not ia_visibilite.CLE_ANTHROPIC:
        raise RuntimeError("ANTHROPIC_API_KEY manquant.")
    contenu = {
        "entreprise": fiche.get("titre"), "categorie_google": fiche.get("categorie"), "ville": ville, "titre_du_site": (site.get("accueil") or {}).get("titre"),
        "description_du_site": (site.get("accueil") or {}).get("description"), "intertitres_accueil": (site.get("accueil") or {}).get("titres", [])[:25],
        "menu": site.get("menu", []), "pages": site.get("pages", []),
    }
    consigne = (
        "Tu prépares un audit de visibilité locale. À partir UNIQUEMENT du contenu du site ci-dessous, repère les services ou produits que l'entreprise propose réellement (« services »), "
        "puis propose :\n"
        "- « mots_cles » : 6 recherches courtes que des clients taperaient dans Google Maps pour trouver ce type d'entreprise dans la ville (ex. « plombier lens », « dépannage chauffe-eau lens »), "
        "des plus larges aux plus précises, avec la ville, en minuscules, sans guillemets ; chacune doit correspondre à un service réellement présent sur le site ;\n"
        "- « questions_ia » : 5 questions en français naturel qu'un client poserait à ChatGPT ou Gemini pour trouver ou choisir cette entreprise dans la ville "
        "(ex. « Quel est le meilleur plombier à Lens pour une fuite d'eau ? »), variées (trouver, choisir, urgence, prix, avis), avec la ville, sans citer le nom de l'entreprise.\n"
        "Pour chaque proposition, « pourquoi » = en une courte phrase, quel service ou quelle page du site la justifie. N'invente aucun service absent du contenu.\n\n"
        + json.dumps(contenu, ensure_ascii=False)
    )
    client = anthropic.Anthropic(api_key=ia_visibilite.CLE_ANTHROPIC)
    reponse = client.messages.create(model=ia_visibilite.MODELE_CLAUDE, max_tokens=1800, thinking={"type": "disabled"}, output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                                     messages=[{"role": "user", "content": consigne}])
    texte = next((b.text for b in reponse.content if b.type == "text"), None)
    if not texte:
        raise RuntimeError("Réponse vide.")
    return json.loads(texte)


def suggerer(fiche: dict, ville: str, lire_html, proposer=None) -> dict:
    """
    {"mots_cles": [{"mot_cle", "pourquoi"}], "questions_ia": [{"question", "pourquoi"}], "services": [...], "source": "site+ia" | "site" | "categorie", "message": str}.
    Les recherches sont nettoyees (ville presente, sans doublon, longueur raisonnable) quelle que soit leur origine.
    """
    proposer = proposer or proposer_avec_claude
    site = {}
    url = (fiche.get("site_web") or "").strip()
    if url:
        try:
            site = lire_site(url if "://" in url else "https://" + url, lire_html)
        except Exception:
            site = {}
    sortie, source, message = None, "categorie", ""
    if site:
        try:
            sortie = proposer(fiche, ville, site)
            source = "site+ia"
        except Exception:
            sortie = heuristique(fiche, ville, site)
            source, message = "site", "Suggestions tirées du menu du site (l'IA n'a pas pu les affiner)."
    else:
        sortie = heuristique(fiche, ville, {})
        message = "Site illisible ou absent : suggestions tirées de la catégorie de la fiche." if url else "Aucun site relié à la fiche : suggestions tirées de sa catégorie."
    mots, vus = [], set()
    for m in sortie.get("mots_cles", []):
        mot = _nettoyer_mot_cle(m.get("mot_cle") if isinstance(m, dict) else m, ville)
        if mot and mot not in vus:
            vus.add(mot)
            mots.append({"mot_cle": mot, "pourquoi": str((m.get("pourquoi") if isinstance(m, dict) else "") or "")[:140]})
    questions, vues = [], set()
    for q in sortie.get("questions_ia", []):
        question = _nettoyer_question(q.get("question") if isinstance(q, dict) else q, ville)
        if question and question.lower() not in vues:
            vues.add(question.lower())
            questions.append({"question": question, "pourquoi": str((q.get("pourquoi") if isinstance(q, dict) else "") or "")[:140]})
    if not mots and not questions:
        sortie = heuristique(fiche, ville, {})
        mots, questions = sortie["mots_cles"], sortie["questions_ia"]
    return {"mots_cles": mots[:6], "questions_ia": questions[:5], "services": [str(s)[:60] for s in (sortie.get("services") or [])[:10]] if isinstance(sortie.get("services"), list) else [], "source": source, "message": message}
