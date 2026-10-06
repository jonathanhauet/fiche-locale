"""
Regles d'un post Google Business Profile : Google refuse un post dont le texte contient un numero de telephone, un lien
ou une adresse e-mail (le contact passe par le bouton d'appel a l'action), ou depasse 1500 caracteres. Module sans
dependance : utilise a la fois par la generation IA (claude_generation) et par l'envoi a Google (google_publish).
"""

import re

LONGUEUR_MAX = 1500

REGLES_GOOGLE_POST = (
    "Regles Google Business Profile (un post qui les enfreint est refuse) : AUCUN numero de telephone, AUCUN lien ni "
    "adresse de site web, AUCUNE adresse e-mail dans le texte (le bouton d'appel a l'action s'en charge : ecris « appelez-nous » "
    "ou « contactez-nous » sans donner le numero ni l'adresse). Pas de mots entiers en MAJUSCULES pour crier, pas de hashtag. "
    "1500 caracteres maximum, espaces compris."
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Le point ou la virgule qui suit un lien fait partie de la phrase, pas du lien : ils restent.
_URL = re.compile(
    r"(?:(?:https?://|www\.)\S*[^\s.,;:!?)\]])"
    r"|(?:\b[\w-]+(?:\.[\w-]+)*\.(?:be|fr|com|net|org|eu|ch|lu|io)\b(?:/\S*[^\s.,;:!?)\]])?)",
    re.IGNORECASE,
)
# « au 07 68 25 95 63 », « par telephone au ... », « tel : ... », « numero : ... » : l'annonce est retiree avec le numero.
_ANNONCE_TELEPHONE = (
    r"(?:\b(?:par\s+t[ée]l[ée]phone\s+)?(?:au|aux)\s+(?:(?:num[ée]ro|n°)\s*:?\s*)?"
    r"|\b(?:t[ée]l[ée]phone|t[ée]l\.?|num[ée]ro|appel(?:ez)?)\s*:?\s*)"
)
_TELEPHONE = re.compile(r"(?:" + _ANNONCE_TELEPHONE + r")?(?<![\w/])(?:\+|00)?\(?\d[\d ().\-/]{7,}\d(?![\w])", re.IGNORECASE)


def _retirer_telephone(correspondance) -> str:
    chiffres = re.sub(r"\D", "", correspondance.group(0))
    return "" if 9 <= len(chiffres) <= 15 else correspondance.group(0)      # une date ou un prix ne sont pas touches


def _couper(texte: str, longueur: int) -> str:
    if len(texte) <= longueur:
        return texte
    morceau = texte[:longueur]
    fin = max(morceau.rfind(". "), morceau.rfind(".\n"), morceau.rfind("! "), morceau.rfind("? "), morceau.rfind("\n\n"))
    if fin > longueur * 0.6:
        return morceau[:fin + 1].rstrip()
    return morceau[: morceau.rfind(" ")].rstrip(" ,;:") + "…"


def contient_contact(texte: str) -> bool:
    """Vrai si le texte contient un telephone, un lien ou une adresse e-mail (ce que Google refuse)."""
    return nettoyer_texte_google(texte, couper=False) != (texte or "")


def nettoyer_texte_google(texte: str, couper: bool = True) -> str:
    """Retire telephone, liens et e-mails d'un texte de post Google, range la ponctuation et limite a 1500 caracteres."""
    resultat = texte or ""
    resultat = _EMAIL.sub("", resultat)
    resultat = _URL.sub("", resultat)
    resultat = _TELEPHONE.sub(_retirer_telephone, resultat)
    resultat = re.sub(r"\(\s*\)", "", resultat)
    resultat = re.sub(r"[ \t]{2,}", " ", resultat)
    resultat = re.sub(r" +([,.])", r"\1", resultat)
    resultat = re.sub(r"[ \t]+\n", "\n", resultat)
    resultat = re.sub(r"\n{3,}", "\n\n", resultat).strip()
    return _couper(resultat, LONGUEUR_MAX) if couper else resultat
