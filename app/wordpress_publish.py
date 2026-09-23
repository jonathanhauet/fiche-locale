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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

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


def markdown_vers_html(texte: str) -> str:
    """
    Conversion volontairement minimale (titres ##/###, listes, gras, italique,
    liens, paragraphes) : le texte reste editable comme du texte simple dans le
    composeur, sans dependance supplementaire. Tout le reste est echappe.
    """
    blocs, paragraphe, liste, type_liste = [], [], [], None

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

    for ligne in (texte or "").split("\n"):
        brute = ligne.strip()
        titre = re.match(r"^(#{2,4})\s+(.+)$", brute)
        puce = re.match(r"^[-*]\s+(.+)$", brute)
        numero = re.match(r"^\d+[.)]\s+(.+)$", brute)
        if not brute:
            vider_paragraphe()
            vider_liste()
        elif titre:
            vider_paragraphe()
            vider_liste()
            niveau = len(titre.group(1))
            blocs.append(f"<h{niveau}>{_inline(html.escape(titre.group(2)))}</h{niveau}>")
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
    return "\n".join(blocs)


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
    publier_le: datetime = None, image_id: int = None,
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
    reponse = _appeler("POST", url_site, "posts", utilisateur, mot_de_passe, json=corps)
    if reponse.status_code not in (200, 201):
        raise RuntimeError(f"Échec de la création de l'article (code {reponse.status_code}) : {reponse.text[:300]}")
    donnees = reponse.json()
    return {"id": donnees.get("id"), "lien": donnees.get("link", ""), "statut": donnees.get("status", "")}
