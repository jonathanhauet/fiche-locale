"""
Detection de la couleur d'accent d'un site WordPress (boutons, liens, couleur
de theme) pour que les blocs ajoutes aux articles (encadre, bouton, sommaire)
s'accordent au site. La police n'est volontairement PAS recuperee : un article
s'affiche a l'interieur du theme, qui applique deja sa propre typographie aux
titres et paragraphes (voir wordpress_publish.markdown_vers_html, qui n'impose
jamais de police).

Lecture statique de la page d'accueil et de ses feuilles de style (pas de
navigateur) : heuristique, donc a valider par une personne avant usage.
"""

import re
from urllib.parse import urljoin

import requests

DELAI_SECONDES = 15
TAILLE_MAX_PAGE = 2_000_000
NB_MAX_FEUILLES = 6
TAILLE_MAX_FEUILLE = 600_000
EN_TETES = {"User-Agent": "Mozilla/5.0 (compatible; FicheLocale/1.0)"}


def normaliser_couleur(valeur: str):
    """#rgb, #rrggbb, #rrggbbaa, rgb()/rgba() -> "#rrggbb" ; None si non reconnu (ex. var(--x), hsl)."""
    valeur = re.sub(r"\s*!important\s*$", "", (valeur or "").strip().lower()).strip()
    m = re.fullmatch(r"#([0-9a-f]{3})", valeur)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})(?:[0-9a-f]{2})?", valeur)
    if m:
        return "#" + m.group(1)
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*[\d.]+%?\s*)?\)", valeur)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _rvb(couleur: str):
    return int(couleur[1:3], 16), int(couleur[3:5], 16), int(couleur[5:7], 16)


def luminance(couleur: str) -> float:
    def canal(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rvb(couleur)
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def _saturation(couleur: str) -> float:
    r, g, b = (c / 255 for c in _rvb(couleur))
    maxi, mini = max(r, g, b), min(r, g, b)
    return 0 if maxi == mini else (maxi - mini) / (1 - abs(maxi + mini - 1))


def est_couleur_d_accent(couleur: str) -> bool:
    """Ecarte blancs, noirs et gris : jamais la couleur de marque d'un site."""
    return _saturation(couleur) >= 0.25 and 0.03 <= luminance(couleur) <= 0.85


# Variables CSS usuelles (WordPress natif, Elementor, Astra, themes courants), par priorite.
MOTIFS_VARIABLES = [
    # Uniquement les noms choisis par le theme lui-meme : la palette par defaut de
    # WordPress (vivid-red, luminous-vivid-orange, vivid-green-cyan...) est presente
    # sur TOUS les sites et ne dit rien de la marque.
    (r"--wp--preset--color--(?:primary|accent|brand|main|secondary|tertiary|theme-[\w-]+)\s*:\s*([^;}]+)", "couleur du thème WordPress"),
    (r"--e-global-color-(?:primary|accent|secondary)\s*:\s*([^;}]+)", "couleur globale Elementor"),
    (r"--ast-global-color-0\s*:\s*([^;}]+)", "couleur principale du thème Astra"),
    (r"--(?:[\w-]*-)?(?:primary|accent|brand|main)(?:-color|-colour)?\s*:\s*([^;}]+)", "variable de couleur principale du thème"),
    (r"--(?:color|colour)-(?:primary|accent|brand|main)\s*:\s*([^;}]+)", "variable de couleur principale du thème"),
]
MOTIF_BOUTON = re.compile(
    r"(?:\.(?:btn|button|wp-block-button__link|elementor-button|et_pb_button|button-primary|cta)[\w-]*|button|input\[type=[\"']?submit[\"']?\])"
    r"[^{}]*\{[^}]*?background(?:-color)?\s*:\s*([^;}!]+)", re.IGNORECASE,
)
MOTIF_LIEN = re.compile(r"(?:^|[}\s,])a\s*\{[^}]*?[^-]color\s*:\s*([^;}!]+)", re.IGNORECASE)


def detecter_couleurs(html_page: str, feuilles: list[str]) -> list[dict]:
    """[{"couleur": "#rrggbb", "source": "..."}, ...] par priorite decroissante, sans doublon, sans neutres."""
    candidats = []

    def ajouter(valeur, source):
        couleur = normaliser_couleur(valeur)
        if couleur and est_couleur_d_accent(couleur) and all(c["couleur"] != couleur for c in candidats):
            candidats.append({"couleur": couleur, "source": source})

    m = re.search(r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\']([^"\']+)["\']', html_page, re.IGNORECASE)
    if m:
        ajouter(m.group(1), "couleur de thème déclarée par le site")

    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html_page, re.IGNORECASE | re.DOTALL) + feuilles)
    for motif, source in MOTIFS_VARIABLES:
        for valeur in re.findall(motif, css, re.IGNORECASE):
            ajouter(valeur, source)
    for valeur in MOTIF_BOUTON.findall(css):
        ajouter(valeur, "couleur des boutons")
    for valeur in MOTIF_LIEN.findall(css):
        ajouter(valeur, "couleur des liens")
    return candidats


def detecter_style_site(url_site: str) -> dict:
    """
    {"couleur": "#rrggbb" ou "", "candidats": [...]} pour la page d'accueil du site.
    RuntimeError (message clair) si le site est injoignable.
    """
    try:
        reponse = requests.get(url_site, headers=EN_TETES, timeout=DELAI_SECONDES)
    except requests.RequestException as erreur:
        raise RuntimeError("Impossible de lire la page d'accueil du site (adresse incorrecte ou site hors ligne).") from erreur
    if reponse.status_code != 200:
        raise RuntimeError(f"La page d'accueil du site a répondu avec le code {reponse.status_code}.")
    html_page = reponse.text[:TAILLE_MAX_PAGE]

    feuilles = []
    liens = re.findall(r"<link[^>]+rel=[\"']stylesheet[\"'][^>]*>", html_page, re.IGNORECASE)
    for lien in liens[:NB_MAX_FEUILLES * 3]:
        m = re.search(r"href=[\"']([^\"']+)[\"']", lien)
        if not m or len(feuilles) >= NB_MAX_FEUILLES or "fonts.googleapis" in m.group(1):
            continue
        try:
            feuille = requests.get(urljoin(url_site, m.group(1)), headers=EN_TETES, timeout=DELAI_SECONDES)
            if feuille.status_code == 200:
                feuilles.append(feuille.text[:TAILLE_MAX_FEUILLE])
        except requests.RequestException:
            continue

    candidats = detecter_couleurs(html_page, feuilles)
    return {"couleur": candidats[0]["couleur"] if candidats else "", "candidats": candidats[:6]}
