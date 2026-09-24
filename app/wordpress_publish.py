"""
Publication d'articles de blog sur le WordPress d'un client, via l'API REST
officielle de WordPress (integree depuis la version 5.6, authentification par
"mot de passe d'application" : Utilisateurs > Profil cote WordPress, distinct
du vrai mot de passe et revocable a tout moment sans le changer).

Site WordPress auto-heberge uniquement (WordPress.com utilise un autre systeme
d'acces). Un plugin de securite ou un hebergeur peut bloquer l'API : voir les
messages d'erreur de tester_connexion.
"""

import html
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from . import wordpress_style

MESSAGE_MOTS_DE_PASSE_DESACTIVES = (
    "Les mots de passe d'application sont désactivés sur ce site WordPress. Causes fréquentes : le site est configuré "
    "en http:// (Réglages > Général : les deux adresses doivent commencer par https://), il est derrière un proxy ou "
    "Cloudflare qui fait croire à WordPress qu'il n'est pas en HTTPS, ou un plugin de sécurité (ou l'hébergeur) "
    "désactive cette fonction."
)
DELAI_SECONDES = 60
FUSEAU_LOCAL = ZoneInfo("Europe/Brussels")


def normaliser_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url.rstrip("/")


def _appeler(methode: str, url_site: str, chemin: str, utilisateur: str, mot_de_passe: str, **kwargs):
    base = normaliser_url(url_site)
    try:
        reponse = requests.request(
            methode, f"{base}/wp-json/wp/v2/{chemin}", auth=(utilisateur.strip(), mot_de_passe.strip()),
            timeout=DELAI_SECONDES, **kwargs,
        )
    except requests.RequestException as erreur:
        raise RuntimeError(
            f"Impossible de joindre le site ({base}) : adresse incorrecte, site hors ligne ou certificat HTTPS invalide."
        ) from erreur
    if reponse.status_code == 401:
        try:
            code = (reponse.json() or {}).get("code", "")
        except ValueError:
            code = ""
        if code.startswith("application_passwords_disabled"):
            raise RuntimeError(MESSAGE_MOTS_DE_PASSE_DESACTIVES)
        raise RuntimeError("Identifiants refusés : vérifiez l'identifiant et le mot de passe d'application.")
    if reponse.status_code == 403:
        raise RuntimeError(
            "Accès refusé par le site (droits insuffisants pour cet utilisateur, ou un plugin de sécurité bloque l'API)."
        )
    if reponse.status_code == 404:
        raise RuntimeError(
            "API WordPress introuvable à cette adresse : vérifiez l'URL du site, ou les permaliens (Réglages > Permaliens) "
            "et les plugins de sécurité qui masquent l'API."
        )
    return reponse


def tester_connexion(url_site: str, utilisateur: str, mot_de_passe: str) -> str:
    """Renvoie le nom affiche de l'utilisateur connecte ; RuntimeError (message clair, en francais) sinon."""
    reponse = _appeler("GET", url_site, "users/me", utilisateur, mot_de_passe, params={"context": "edit"})
    if reponse.status_code != 200:
        raise RuntimeError(f"Réponse inattendue du site (code {reponse.status_code}).")
    try:
        donnees = reponse.json()
    except ValueError as erreur:
        raise RuntimeError("Le site n'a pas renvoyé une réponse WordPress valide (JSON attendu).") from erreur
    capacites = donnees.get("capabilities") or {}
    if capacites and not capacites.get("publish_posts") and not capacites.get("edit_posts"):
        raise RuntimeError("Cet utilisateur WordPress n'a pas le droit de créer des articles (rôle trop limité).")
    return donnees.get("name") or utilisateur


def _inline(texte: str) -> str:
    """Gras, italique et liens, sur du texte deja echappe en HTML."""
    texte = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texte)
    texte = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", texte)
    texte = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)", r'<a href="\2">\1</a>', texte,
    )
    return texte


def decouper_titre(texte: str) -> tuple[str, str]:
    """
    Separe le titre du corps : la premiere ligne "# Titre" (markdown) devient le
    titre de l'article ; sinon, la premiere ligne non vide (tronquee) sert de titre
    et le corps reste entier.
    """
    lignes = (texte or "").strip().split("\n")
    for indice, ligne in enumerate(lignes):
        if not ligne.strip():
            continue
        if re.match(r"^#\s+\S", ligne):
            return re.sub(r"^#\s+", "", ligne).strip(), "\n".join(lignes[indice + 1:]).strip()
        return ligne.strip()[:80], "\n".join(lignes).strip()
    return "", ""


COULEUR_NEUTRE = "#555555"


def _slug(texte: str, deja: set) -> str:
    base = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:60] or "section"
    slug, numero = base, 2
    while slug in deja:
        slug, numero = f"{base}-{numero}", numero + 1
    deja.add(slug)
    return slug


def _teinte(couleur: str, part: float = 0.92) -> str:
    """Couleur melangee a du blanc (fond d'encadre : meme teinte que l'accent, tres claire)."""
    canaux = [int(couleur[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * part):02x}" for c in canaux)


def _texte_sur(couleur: str) -> str:
    """Blanc ou noir, selon le meilleur contraste sur cette couleur de fond (bouton)."""
    lum = wordpress_style.luminance(couleur)
    return "#ffffff" if (1.05 / (lum + 0.05)) >= ((lum + 0.05) / 0.05) else "#111111"


def markdown_vers_html(texte: str, couleur: str = "") -> str:
    """
    Conversion volontairement minimale, sans dependance : titres ##/###, listes,
    gras, italique, liens, paragraphes, plus trois blocs mis en forme -
    encadre ("> texte"), bouton ("[[Texte|https://...]]" seul sur sa ligne) et
    sommaire automatique (a partir de 4 sous-titres "##"). Les blocs utilisent la
    couleur d'accent du site (voir wordpress_style) et ne fixent JAMAIS de
    police : ils heritent de celle du theme. Styles en ligne (pas de plugin
    ni de feuille de style a installer) ; un site qui les filtre garde le texte
    intact, sans la mise en forme. Tout le reste est echappe.
    """
    couleur = wordpress_style.normaliser_couleur(couleur) or COULEUR_NEUTRE
    fond, texte_bouton = _teinte(couleur), _texte_sur(couleur)
    blocs, paragraphe, liste, citation = [], [], [], []
    type_liste = None
    titres_h2, slugs, premier_h2 = [], set(), None

    def vider_paragraphe():
        if paragraphe:
            blocs.append("<p>" + _inline(html.escape(" ".join(paragraphe))) + "</p>")
            paragraphe.clear()

    def vider_liste():
        nonlocal type_liste
        if liste:
            items = "".join(f"<li>{_inline(html.escape(i))}</li>" for i in liste)
            blocs.append(f"<{type_liste}>{items}</{type_liste}>")
            liste.clear()
        type_liste = None

    def vider_citation():
        if citation:
            paragraphes, courant = [], []
            for ligne in citation + [""]:
                if ligne:
                    courant.append(ligne)
                elif courant:
                    paragraphes.append(" ".join(courant))
                    courant = []
            contenu = "".join(f'<p style="margin:0 0 .5em;">{_inline(html.escape(p))}</p>' for p in paragraphes)
            blocs.append(
                f'<div class="fl-encadre" style="border-left:4px solid {couleur};background:{fond};'
                f'padding:1em 1.25em;margin:1.5em 0;border-radius:4px;">{contenu}</div>'
            )
            citation.clear()

    for ligne in (texte or "").split("\n"):
        brute = ligne.strip()
        titre = re.match(r"^(#{2,4})\s+(.+)$", brute)
        puce = re.match(r"^[-*]\s+(.+)$", brute)
        numero = re.match(r"^\d+[.)]\s+(.+)$", brute)
        bouton = re.match(r"^\[\[(.+?)\|(https?://[^\]\s]+)\]\]$", brute)
        cite = re.match(r"^>\s?(.*)$", brute)
        if cite:
            vider_paragraphe()
            vider_liste()
            citation.append(cite.group(1).strip())
            continue
        vider_citation()
        if not brute:
            vider_paragraphe()
            vider_liste()
        elif titre:
            vider_paragraphe()
            vider_liste()
            niveau = len(titre.group(1))
            attribut = ""
            if niveau == 2:
                slug = _slug(titre.group(2), slugs)
                titres_h2.append((slug, titre.group(2)))
                attribut = f' id="{slug}"'
                if premier_h2 is None:
                    premier_h2 = len(blocs)
            blocs.append(f"<h{niveau}{attribut}>{_inline(html.escape(titre.group(2)))}</h{niveau}>")
        elif bouton:
            vider_paragraphe()
            vider_liste()
            blocs.append(
                f'<p class="fl-cta" style="margin:1.75em 0;"><a href="{html.escape(bouton.group(2), quote=True)}" '
                f'style="display:inline-block;background:{couleur};color:{texte_bouton};padding:.8em 1.6em;'
                f'border-radius:6px;text-decoration:none;font-weight:600;">{html.escape(bouton.group(1).strip())}</a></p>'
            )
        elif puce or numero:
            vider_paragraphe()
            genre = "ul" if puce else "ol"
            if type_liste and type_liste != genre:
                vider_liste()
            type_liste = genre
            liste.append((puce or numero).group(1))
        else:
            vider_liste()
            paragraphe.append(brute)
    vider_paragraphe()
    vider_liste()
    vider_citation()

    if len(titres_h2) >= 4 and premier_h2 is not None:
        entrees = "".join(f'<li><a href="#{slug}">{html.escape(nom)}</a></li>' for slug, nom in titres_h2)
        blocs.insert(
            premier_h2,
            f'<nav class="fl-sommaire" style="border:1px solid #e5e7eb;border-left:4px solid {couleur};'
            f'padding:1em 1.25em;margin:1.5em 0;border-radius:4px;"><strong>Sommaire</strong>'
            f'<ol style="margin:.5em 0 0 1.2em;">{entrees}</ol></nav>',
        )
    return '<div class="fl-article">\n' + "\n".join(blocs) + "\n</div>"


def extrait_depuis_corps(texte: str, longueur: int = 155) -> str:
    """Resume automatique (champ "extrait" de WordPress) : debut du premier vrai paragraphe, coupe proprement."""
    for bloc in re.split(r"\n\s*\n", (texte or "").strip()):
        bloc = bloc.strip()
        if not bloc or re.match(r"^(#{1,4}\s|[-*>]\s?|\d+[.)]\s|\[\[)", bloc):
            continue
        clair = re.sub(r"\*\*?|\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(bloc.split()))
        if len(clair) <= longueur:
            return clair
        coupe = clair[:longueur]
        fin_phrase = max(coupe.rfind(". "), coupe.rfind("? "), coupe.rfind("! "))
        if fin_phrase >= longueur // 2:
            return coupe[:fin_phrase + 1]
        return coupe.rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return ""


def envoyer_image(url_site: str, utilisateur: str, mot_de_passe: str, octets: bytes, nom_fichier: str = "article.jpg") -> int:
    """Televerse l'image dans la mediatheque WordPress et renvoie son identifiant (image mise en avant)."""
    reponse = _appeler(
        "POST", url_site, "media", utilisateur, mot_de_passe, data=octets,
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"', "Content-Type": "image/jpeg"},
    )
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Échec de l'envoi de l'image sur WordPress (code {reponse.status_code}) : {reponse.text[:300]}")
    return reponse.json()["id"]


def creer_article(
    url_site: str, utilisateur: str, mot_de_passe: str, titre: str, contenu_html: str,
    publier_le: datetime = None, image_id: int = None, extrait: str = "",
) -> dict:
    """
    Cree l'article : publie tout de suite, ou programme (statut "future", gere par
    WordPress lui-meme) si publier_le (heure locale de Bruxelles, naive) est donne.
    Renvoie {"id", "lien", "statut"}.
    """
    corps = {"title": titre, "content": contenu_html, "status": "publish"}
    if publier_le:
        corps["status"] = "future"
        corps["date_gmt"] = (
            publier_le.replace(tzinfo=FUSEAU_LOCAL).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        )
    if image_id:
        corps["featured_media"] = image_id
    if extrait:
        corps["excerpt"] = extrait
    reponse = _appeler("POST", url_site, "posts", utilisateur, mot_de_passe, json=corps)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Échec de la création de l'article (code {reponse.status_code}) : {reponse.text[:300]}")
    donnees = reponse.json()
    return {"id": donnees.get("id"), "lien": donnees.get("link", ""), "statut": donnees.get("status", "")}
