"""
Tournures typiques des textes ecrits par une IA (« Dans un monde ou... », « Plongez dans... », « Il est important de noter que... ») : elles se repèrent
tout de suite et donnent un article ou un post impersonnel. Ce module fait deux choses, sans aucun appel externe :
- detecter() les signale (controle de l'article avant publication) ;
- nettoyer() retire seulement les tournures de remplissage qui peuvent disparaitre sans changer le sens ni casser la phrase.
Le reste (« n'hesitez pas a... », « que vous soyez... ou... ») n'est jamais supprime automatiquement : il est signale pour que tu le reformules.
"""

import re

# (motif, exemple affiche, conseil) : signalees, jamais supprimees
TICS = [
    (r"\bdans un monde o[uù]\b", "« Dans un monde où... »", "ouverture trop reconnaissable : commence par le fait concret"),
    (r"\b[àa] l['’]ère (?:du|de la|des|de l['’])\s?\w+", "« À l'ère du numérique »", "formule creuse : dis simplement ce qui a changé"),
    (r"\bplongez (?:dans|au c(?:œ|oe)ur)\b", "« Plongez dans... »", "remplace par une invitation simple (« découvrez », « regardez »)"),
    (r"\bn['’]hésitez pas [àa]\b", "« N'hésitez pas à... »", "remplace par une invitation précise (appelez-nous, écrivez-nous, passez nous voir)"),
    (r"\bque vous soyez\b[^.!?]{0,80}\bou\b", "« Que vous soyez... ou... »", "tournure passe-partout : cible une situation réelle"),
    (r"\bnon seulement\b[^.!?]{0,100}\bmais (?:aussi|également|encore)\b", "« Non seulement... mais aussi... »", "souvent du remplissage : garde l'idée la plus utile"),
    (r"\bforce est de constater\b", "« Force est de constater »", "dis directement ce que tu constates"),
    (r"\bil va sans dire\b", "« Il va sans dire »", "si cela va sans dire, ne le dis pas"),
    (r"\bil est (?:important|essentiel|crucial|primordial) de (?:noter|souligner|préciser|rappeler)\b", "« Il est important de noter que... »", "supprime l'annonce et dis la chose"),
    (r"\b(?:en somme|en conclusion|en résumé),", "« En conclusion, »", "inutile : la dernière partie se reconnaît sans l'annoncer"),
    (r"\bse démarquer\b|\bbooster?z?\b|\bboostez\b", "« Se démarquer », « booster »", "vocabulaire de pub générique : cite un bénéfice concret"),
    (r"\bparadigme\b|\bécosystème\b|\bsynergies?\b|\bholistique\b", "« écosystème », « synergie », « holistique »", "jargon : utilise un mot courant"),
    (r"\bpaysage (?:numérique|digital|concurrentiel)\b|\ben constante évolution\b", "« Paysage numérique en constante évolution »", "formule creuse"),
    (r"\béquipe (?:passionnée|dynamique|jeune et dynamique)\b", "« Équipe passionnée »", "montre-le par un exemple plutôt que de l'affirmer"),
    (r"\bsolutions? (?:innovantes?|sur mesure|complètes?|clés en main)\b", "« Solutions innovantes / sur mesure »", "précise ce que tu fais réellement"),
    (r"\bexcellence\b|\bleader (?:du|de|dans|sur)\b|\bincontournables?\b", "« Excellence », « leader », « incontournable »", "affirmation sans preuve : donne un fait vérifiable"),
    (r"\bce n['’]est (?:pas|plus) (?:seulement|simplement|juste|qu['’]un[e]?\b)[^.!?]{0,80}[,;:]\s*c['’]est\b", "« Ce n'est pas seulement X, c'est Y »", "balancement trop reconnaissable : dis directement Y"),
    (r"\bjoue(?:nt)? un rôle (?:clé|crucial|essentiel|central|majeur|fondamental)\b", "« Joue un rôle clé »", "dis ce que la chose fait concrètement"),
    (r"\bun (?:véritable )?gage de (?:qualité|confiance|sérénité|sécurité)\b", "« Un gage de qualité / de confiance »", "affirmation vide : donne le fait qui le prouve"),
    (r"!{2,}", "plusieurs points d'exclamation de suite", "un seul suffit, ou aucun"),
]
_TICS_COMPILES = [(re.compile(m, re.IGNORECASE | re.DOTALL), exemple, conseil) for m, exemple, conseil in TICS]

# tournures de remplissage retirees automatiquement : elles annoncent une phrase au lieu de la dire
_REMPLISSAGE = re.compile(
    r"(?P<debut>^|(?<=[.!?…]\s)|(?<=\n)|(?<=\n[>\-*]\s)|(?<=\n#\s))"
    r"(?:Il est (?:important|essentiel|crucial|primordial) de (?:noter|souligner|préciser|rappeler) que"
    r"|Il convient de (?:noter|souligner|préciser|rappeler) que"
    r"|Force est de constater que"
    r"|Il va sans dire que"
    r"|Il faut savoir que"
    r"|Notons que"
    r"|Soulignons que)\s+(?P<suite>\S)",
    re.IGNORECASE,
)


def detecter(texte: str) -> list:
    """[{"exemple", "conseil", "extrait"}] : une entree par type de tournure trouvee (au plus 8), avec un court extrait du texte."""
    texte = texte or ""
    trouves = []
    for motif, exemple, conseil in _TICS_COMPILES:
        correspondance = motif.search(texte)
        if correspondance:
            debut = max(0, correspondance.start() - 25)
            trouves.append({"exemple": exemple, "conseil": conseil, "extrait": " ".join(texte[debut:correspondance.end() + 25].split())})
    return trouves[:8]


def nettoyer(texte: str) -> str:
    """Retire les annonces de remplissage en debut de phrase (« Il est important de noter que ») et remet la majuscule : le reste du texte est inchange."""
    if not texte:
        return texte
    return _REMPLISSAGE.sub(lambda m: m.group("debut") + m.group("suite").upper(), texte)
