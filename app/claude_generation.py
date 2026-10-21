"""
Generation de posts via l'API Claude. Reprend la meme logique que le script
en ligne de commande generer_posts.py (prompt SEO local + sortie structuree
en JSON), adaptee en fonction pure pour la plateforme web.
"""

import difflib
import json
import unicodedata
import os
import re
from datetime import date

from anthropic import Anthropic as _AnthropicSDK

from .texte_google import REGLES_GOOGLE_POST, nettoyer_texte_google
from dotenv import load_dotenv

# La plupart des consignes de ce module sont ecrites sans accents : sans rappel explicite, l'IA reprend parfois ce style (ou celui
# d'une reponse orale sans accents) et livre des posts sans accents. Cette consigne est donc ajoutee a CHAQUE appel.
CONSIGNE_ACCENTS = (
    "IMPORTANT. Règle d'écriture : tout texte en français que tu rédiges doit avoir une orthographe correcte avec tous ses accents, cédilles et "
    "ligatures (é, è, ê, à, â, ô, û, î, ç, œ...), même si les consignes qui précèdent sont écrites sans accents et même si le texte fourni "
    "(réponse orale, transcription, brouillon) en est dépourvu : corrige simplement l'orthographe, sans rien changer au fond. "
    "Ne produis jamais de français sans accents."
)


RAPPEL_ACCENTS = "\n\n(Rappel : tout le texte en français doit avoir ses accents, y compris dans les premiers mots : é, è, à, ç, ô...)"


class Anthropic(_AnthropicSDK):
    """Client Anthropic qui ajoute CONSIGNE_ACCENTS (message systeme) a chaque appel de messages.create."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        creer_origine = self.messages.create

        def creer(*a, **k):
            messages = k.get("messages")
            if messages and messages[-1].get("role") == "user":      # rappel en fin de demande, la ou il pese le plus
                dernier = dict(messages[-1])
                if isinstance(dernier.get("content"), str):
                    dernier["content"] += RAPPEL_ACCENTS
                elif isinstance(dernier.get("content"), list):
                    dernier["content"] = list(dernier["content"]) + [{"type": "text", "text": RAPPEL_ACCENTS.strip()}]
                k["messages"] = list(messages[:-1]) + [dernier]
            systeme = k.get("system")
            if systeme is None:
                k["system"] = CONSIGNE_ACCENTS
            elif isinstance(systeme, str):
                k["system"] = systeme + "\n\n" + CONSIGNE_ACCENTS
            elif isinstance(systeme, list):
                k["system"] = systeme + [{"type": "text", "text": CONSIGNE_ACCENTS}]
            return creer_origine(*a, **k)

        self.messages.create = creer


DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
FICHIER_PROMPT = os.path.join(DOSSIER_APP, "prompts", "prompt_generation_posts.txt")

MODELE_CLAUDE = "claude-sonnet-5"

LIBELLES_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _periode_publication_cible() -> str:
    """
    Mois suivant le mois courant : Jonathan genere systematiquement ses posts
    vers la fin d'un mois pour les programmer sur le mois suivant (voir la
    carte "Programmer tous les brouillons" sur la fiche client) - c'est donc
    cette periode-la, et non la date du jour, qui doit guider un eventuel
    contexte saisonnier dans les textes generes.
    """
    aujourdhui = date.today()
    mois_suivant = aujourdhui.month + 1 if aujourdhui.month < 12 else 1
    annee_suivante = aujourdhui.year if aujourdhui.month < 12 else aujourdhui.year + 1
    return f"{LIBELLES_MOIS[mois_suivant - 1]} {annee_suivante}"

# Chaque module qui a besoin de .env le charge lui-meme : on ne peut pas
# compter sur l'ordre des imports pour garantir que main.py l'a deja fait.
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))
CLE_API = os.getenv("ANTHROPIC_API_KEY")

SCHEMA_REPONSE = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titre": {"type": "string"},
                    "texte": {"type": "string"},
                    "prompt_image": {"type": "string"},
                },
                "required": ["titre", "texte", "prompt_image"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


def _construire_prompt(
    contenu_site: str, nombre_posts: int, sujets_deja_traites: list[str] = None, localisation: dict = None
) -> str:
    with open(FICHIER_PROMPT, "r", encoding="utf-8") as f:
        gabarit_prompt = f.read()

    instructions = gabarit_prompt.replace("{NOMBRE_POSTS}", str(nombre_posts)) + "\n\n" + REGLES_GOOGLE_POST

    instruction_prompt_image = (
        "\n\n---\n\n"
        "Consigne technique supplementaire : pour chaque post, redige aussi un prompt "
        "en anglais destine a un generateur d'images (decrivant une photo ou illustration "
        "adaptee au post, sans texte incruste ni logo), a fournir en plus du titre et du texte."
    )

    instruction_periode = (
        "\n\n---\n\n"
        f"Periode reelle de publication : ces posts seront programmes et publies courant "
        f"{_periode_publication_cible()} (pas a la date d'aujourd'hui). C'est une information "
        f"de contexte pour toi uniquement : si tu evoques une saison, une fete ou un evenement "
        f"temporel, base-toi exclusivement sur cette periode-la. Ne cite en revanche JAMAIS "
        f"le mois ni l'annee de facon litterale dans le texte (proscrit : « en ce mois de "
        f"septembre 2026 », « en septembre 2026 »...) - ca sonne artificiel. Prefere des "
        f"formulations naturelles et indirectes (« cette rentree », « a l'approche de "
        f"l'hiver », « ces prochaines semaines »...), ou n'evoque tout simplement aucune "
        f"periode si ce n'est pas naturel pour ce post."
    )

    instruction_deja_traites = ""
    if sujets_deja_traites:
        liste = "\n".join(f"- {s}" for s in sujets_deja_traites)
        instruction_deja_traites = (
            "\n\n---\n\n"
            "Posts deja generes precedemment pour cette entreprise (sujet et angle) - "
            f"ne repete AUCUN de ces sujets ni angles, chaque nouveau post doit apporter "
            f"quelque chose de reellement nouveau :\n{liste}"
        )

    instruction_localisation = ""
    if localisation:
        instruction_localisation = (
            "\n\n---\n\n"
            "Zone geographique ciblee (parametre explicitement choisi pour cette fiche, "
            f"prioritaire sur toute autre indication de localisation dans le contenu ci-dessus) : "
            f"un rayon d'environ {localisation['rayon_km']} km autour de "
            f"{localisation['ville']} (latitude {localisation['latitude']}, longitude "
            f"{localisation['longitude']}). Si tu cites une ou plusieurs localites (ville, "
            "quartier, commune...) dans un post, elles doivent obligatoirement se situer dans "
            "cette zone - jamais au-dela. En cas de doute sur la distance reelle d'une localite, "
            "ne la cite pas : reste generique (« la region », « votre secteur », « a proximite »...) "
            "plutot que de risquer de mentionner un lieu trop eloigne."
        )

    return (
        "Voici le contenu du site web de l'entreprise (base de connaissance) :\n\n"
        f"{contenu_site}\n\n"
        "---\n\n"
        f"{instructions}"
        f"{instruction_prompt_image}"
        f"{instruction_periode}"
        f"{instruction_deja_traites}"
        f"{instruction_localisation}"
    )


_CARACTERES_INDESIRABLES = re.compile(r"[`　-〿＀-￯]+")
# Constate en reel : une suite de "&#8203;" (le CODE HTML d'un espace de
# largeur nulle) generee comme texte visible au lieu d'un vrai caractere
# invisible - jamais legitime dans un post, retiree sans risque. Motif
# numerique uniquement (jamais &amp; &nbsp; etc., qui pourraient
# legitimement apparaitre tels quels dans un nom d'entreprise ou un texte).
_ENTITES_HTML_INDESIRABLES = re.compile(r"&#x?[0-9a-fA-F]+;")


# Formes SANS accent qui ne sont pas des mots francais : si un texte en contient plusieurs, il a ete ecrit sans accents.
_MOTS_SANS_ACCENT = re.compile(
    r"\b(?:etre|ete|pieces?|reponses?|possedez|possede|possedent|etablissements?|equipes?|reelles?|reels?|immediats?|immediate|metaux|precieux|"
    r"precieuse|apres|deja|tres|problemes?|negatifs?|negatives?|decouvrez|decouvrir|resultats?|securite|qualite|specialistes?|experiences?|"
    r"references?|referencement|ecrire|ecrit|creer|creation|etudes?|etats?|etapes?|eviter|elements?|meme|memes|francais|prefere|differents?|"
    r"evenements?|energie|ecoute|ecoutons|accueillons|realise|realisee|realisation|beneficier|systeme|"
    r"generalement|numero|telephone|developpement|evaluation|evaluer|verifier|verification|regles?|periode|priorite|interets?)\b",
    re.IGNORECASE,
)


def _semble_sans_accents(texte: str) -> bool:
    """Vrai si le texte contient au moins deux formes sans accent differentes qui n'existent pas telles quelles en francais."""
    trouves = {m.lower() for m in _MOTS_SANS_ACCENT.findall(texte)}
    return len(trouves) >= 2


def _retablir_accents(texte: str) -> str:
    """
    Filet de securite : l'IA livre parfois un texte francais sans accents (elle reprend le style sans accents des consignes ou d'une
    reponse orale). Si le texte en a l'air, une courte passe de correction orthographique remet les accents, sans rien changer d'autre.
    En cas d'echec, le texte d'origine est conserve.
    """
    if not CLE_API or len(texte) < 40 or not _semble_sans_accents(texte):
        return texte
    try:
        reponse = _AnthropicSDK(api_key=CLE_API).messages.create(
            model=MODELE_CLAUDE, max_tokens=4096, thinking={"type": "disabled"},
            system=("Tu es correcteur typographique. Tu reçois un texte en français écrit sans accents. Rends exactement le même texte avec "
                    "les accents, cédilles et ligatures rétablis (é, è, ê, à, â, ô, û, î, ç, œ...). Ne change aucun mot, aucune ponctuation, "
                    "aucun saut de ligne, aucun emoji ; laisse les hashtags, adresses web et chiffres tels quels. Réponds uniquement par le texte corrigé."),
            messages=[{"role": "user", "content": texte}],
        )
        corrige = next((b.text for b in reponse.content if b.type == "text"), "").strip()
    except Exception:
        return texte
    if not corrige or abs(len(corrige) - len(texte)) > max(12, len(texte) * 0.15):
        return texte
    return corrige


def _nettoyer_texte_genere(texte: str) -> str:
    """
    Filet de securite contre de rares artefacts de generation (ex. suites de
    backticks, de ponctuation/caracteres CJK isoles ou de codes HTML
    d'espaces invisibles, sans lien avec le contenu demande, qui se glissent
    occasionnellement dans la sortie du modele) - jamais legitimes dans un
    post en francais, retires sans risque. Remplace aussi le tiret cadratin
    (—) par un tiret simple : meme avec la consigne de style demandee au
    modele, il peut lui arriver d'en glisser un malgre tout - ce filet
    garantit qu'aucun n'atteint jamais le client.
    """
    texte = _CARACTERES_INDESIRABLES.sub("", texte)
    texte = _ENTITES_HTML_INDESIRABLES.sub("", texte)
    # Les espaces/tabulations laisses par l'artefact ci-dessus (souvent une
    # longue suite entrecoupee d'espaces) sont regroupes pour ne pas laisser
    # de grands vides visibles a la place.
    texte = re.sub(r"[ \t]{2,}", " ", texte)
    texte = re.sub(r"[ \t]+\n", "\n", texte)
    # Idem si l'artefact occupait une ou plusieurs lignes entieres : ne pas
    # laisser plusieurs lignes vides d'affilee a la place.
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    texte = texte.replace("—", "-")
    return _retablir_accents(texte.strip())


def _nettoyer_champs_post(post: dict, google: bool = False) -> dict:
    for cle in ("titre", "texte"):
        if post.get(cle):
            post[cle] = _nettoyer_texte_genere(post[cle])
    if google and post.get("texte"):
        post["texte"] = nettoyer_texte_google(post["texte"])
    return post


def generer_posts_pour_client(
    contenu_site: str, nombre_posts: int, sujets_deja_traites: list[str] = None, localisation: dict = None
) -> list[dict]:
    """
    Appelle l'API Claude et renvoie une liste de dictionnaires :
    [{"titre": str, "texte": str, "prompt_image": str}, ...]
    sujets_deja_traites : sujets des posts precedents de ce client, pour que
    l'IA evite de repeter les memes themes d'une generation a l'autre (voir
    _sujets_deja_traites_client dans main.py).
    localisation : {"ville": str, "latitude": float, "longitude": float, "rayon_km": int}
    optionnel (voir Client.localisation_active) - restreint les localites que
    l'IA peut citer a ce rayon, au lieu de se fier uniquement au contenu_site.

    Il arrive (rarement, sur de gros lots) que la reponse JSON arrive coupee en plein milieu d'un texte
    (« Unterminated string ») : on retente alors une fois le lot entier, puis, si ca persiste, on genere par
    petits lots de 4 en passant les titres deja produits comme sujets a eviter, plutot que d'echouer.
    """
    if not CLE_API:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquant dans plateforme_web/.env."
        )

    try:
        return _generer_lot_posts(contenu_site, nombre_posts, sujets_deja_traites, localisation)
    except Exception as premiere_erreur:
        if nombre_posts <= 1:
            raise
        try:
            return _generer_lot_posts(contenu_site, nombre_posts, sujets_deja_traites, localisation)
        except Exception:
            pass

    posts, deja = [], list(sujets_deja_traites or [])
    while len(posts) < nombre_posts:
        lot = min(4, nombre_posts - len(posts))
        try:
            nouveaux = _generer_lot_posts(contenu_site, lot, deja, localisation)
        except Exception:
            nouveaux = _generer_lot_posts(contenu_site, lot, deja, localisation)
        posts.extend(nouveaux)
        deja.extend(f"{p['titre']} - {p['texte'][:150]}" for p in nouveaux)
    return posts[:nombre_posts]


def _generer_lot_posts(
    contenu_site: str, nombre_posts: int, sujets_deja_traites: list[str] = None, localisation: dict = None
) -> list[dict]:
    prompt_complet = _construire_prompt(contenu_site, nombre_posts, sujets_deja_traites, localisation)

    client = Anthropic(api_key=CLE_API)
    # Un budget fixe (8192 auparavant) se faisait tronquer en plein milieu
    # d'une chaine JSON des 7 posts (l'input par defaut du formulaire) - meme
    # cause que le budget fixe de adapter_post_multi_reseaux, deja corrige
    # ailleurs. Chaque post fait 1200-1400 caracteres (voir
    # prompts/prompt_generation_posts.txt) + titre + prompt d'image : large
    # marge par post plutot que de calculer un budget precis. En streaming
    # (obligatoire au-dela de 21333 tokens - voir le SDK Anthropic, sans quoi
    # jusqu'a 20 posts, le max du formulaire, ferait echouer l'appel avant
    # meme de contacter l'API) : cf.
    # https://github.com/anthropics/anthropic-sdk-python#long-requests
    max_tokens = min(64000, 2200 * max(nombre_posts, 1) + 2000)
    with client.messages.stream(
        model=MODELE_CLAUDE,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REPONSE}},
        messages=[{"role": "user", "content": prompt_complet}],
    ) as flux:
        reponse = flux.get_final_message()

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    try:
        donnees = json.loads(bloc_texte)
    except json.JSONDecodeError as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete ({erreur}, arret : {reponse.stop_reason}).") from erreur
    posts = donnees["posts"]
    for post in posts:
        # Filet de securite : il est arrive que l'IA inverse les deux champs
        # (le corps complet du post dans "titre", un mot-cle comme "texte")
        # malgre les consignes - un titre nettement plus long que le texte
        # trahit cette inversion.
        if len(post["titre"]) > len(post["texte"]):
            post["titre"], post["texte"] = post["texte"], post["titre"]
        _nettoyer_champs_post(post, google=True)
    return posts


SCHEMA_POST_UNIQUE = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "texte": {"type": "string"},
        "prompt_image": {"type": "string"},
    },
    "required": ["titre", "texte", "prompt_image"],
    "additionalProperties": False,
}


def _blocs_theme_et_reference(theme: str, contenu_site_reference: str) -> tuple[str, str]:
    bloc_theme = f"\nTheme demande :\n« {theme.strip()} »\n" if theme.strip() else ""
    bloc_reference = (
        "\nContenu du site d'une fiche existante, fourni comme source d'inspiration "
        "pour le fond uniquement (expertise, ton, type de conseils) — jamais pour des "
        "details geographiques ou le nom de l'entreprise :\n"
        f"{contenu_site_reference.strip()}\n"
        if contenu_site_reference.strip() else ""
    )
    return bloc_theme, bloc_reference


def generer_post_generique(theme: str = "", contenu_site_reference: str = "") -> dict:
    """
    Genere un post unique destine a etre publie tel quel sur plusieurs fiches a la
    fois (pas lie a un client precis). theme et/ou contenu_site_reference (contenu
    du site d'un client existant, repris comme source d'inspiration sur le fond
    uniquement) : au moins l'un des deux doit etre fourni. Les details geographiques
    (ville, region, adresse...) sont explicitement exclus, la fiche pouvant etre
    publiee sur des localites differentes.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not theme.strip() and not contenu_site_reference.strip():
        raise RuntimeError("Fournissez un theme ou une fiche de reference.")

    bloc_theme, bloc_reference = _blocs_theme_et_reference(theme, contenu_site_reference)

    prompt = (
        "Tu rediges un post Google Business Profile (Google Posts) destine a etre publie "
        "tel quel sur plusieurs fiches d'etablissements differents, potentiellement situes "
        "dans des villes ou regions differentes (pas un seul client precis).\n"
        f"{bloc_theme}"
        f"{bloc_reference}\n"
        "Consignes :\n"
        "- Reste generique et geographiquement neutre : n'inclus AUCUN nom de ville, region, "
        "adresse ou reference locale, meme si la source d'inspiration en contient. N'invente "
        "aucun detail specifique a une entreprise en particulier (pas de nom d'entreprise, "
        "pas d'offre commerciale precise).\n"
        "- Ton professionnel, clair, engageant. Pas de jargon inutile.\n"
        "- Longueur : entre 1200 et 1500 caracteres (espaces compris), comme un vrai Google Post developpe.\n"
        f"- {REGLES_GOOGLE_POST}\n"
        "- Passe des lignes entre les idees pour aerer le texte (pas un seul bloc compact) : "
        "utilise des sauts de ligne (\\n\\n) entre les paragraphes.\n"
        "- N'utilise jamais de tiret cadratin (—) : remplace par une virgule, un deux-points ou "
        "un tiret simple (-).\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique (ex. « le cabinet reçoit au cabinet »). Relis-toi avant de conclure : la derniere phrase doit etre claire, naturelle et utile.\n"
        "- Redige aussi un titre court et un prompt en anglais pour un generateur d'images. "
        "Vise une scene concrete et specifique, directement liee au sujet precis (un lieu, un "
        "objet ou une situation reconnaissable, pas une metaphore abstraite). Evite les cliches "
        "d'illustration IA generique (cadenas/bouclier de securite, tableau de bord abstrait, "
        "ampoule, poignee de main, reseau de points/globe connecte, engrenages) sauf si le sujet "
        "les impose vraiment. Sans texte incruste, sans logo, sans reference geographique."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=2048,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_POST_UNIQUE}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return _nettoyer_champs_post(json.loads(bloc_texte), google=True)


SPECIALITE_SEO_LOCAL = (
    "referencement local (SEO local), specialise sur Google Business Profile, Google AI Overviews et "
    "Google Local Services Ads"
)


def _specialite_auteur(nom_client: str = "") -> str:
    """
    Domaine d'expertise de l'auteur des posts "expert". Sans nom d'entreprise : le SEO local historique.
    Avec un nom : le metier de CETTE entreprise (deduit de son nom et du contenu de son site), et le SEO
    local seulement si c'est une agence de marketing / SEO - jamais impose a un serrurier ou un avocat.
    """
    if not (nom_client or "").strip():
        return SPECIALITE_SEO_LOCAL
    return (
        f"metier de l'entreprise « {nom_client.strip()} » (deduis-le de son nom et du contenu de son site quand il est "
        "fourni). Parle UNIQUEMENT de ce metier : ses prestations, les conseils pratiques utiles a ses clients, les "
        "erreurs frequentes, les coulisses. Ne parle de referencement local, de Google Business Profile ou de "
        "marketing digital que si cette entreprise est elle-meme une agence de marketing ou de SEO"
    )


def generer_post_expert(
    theme: str = "", contexte_expert: str = "", contenu_article: str = "", nom_client: str = "",
    deja_traites: list = None, texte_actuel: str = "",
) -> dict:
    """
    Variante de generer_post_generique() pensee pour une seule fiche bien
    precise, dont le proprietaire EST l'expert (typiquement Jonathan
    lui-meme) : contrairement au post generique (volontairement neutre et
    publiable sur n'importe quelle fiche), ici on veut au contraire une prise
    de position personnelle et affirmee, a la premiere personne, sur
    l'actualite ou un sujet de fond lie au SEO local / Google Business
    Profile / Google AI Overviews / Google Local Services Ads - de quoi
    construire une image d'expert reconnu sur ces sujets, pas juste informer.
    theme et/ou contexte_expert (contenu du site de la fiche, repris pour le
    ton et l'angle d'expertise) : au moins l'un des deux doit etre fourni.
    contenu_article (optionnel) : extrait reel d'un article source (voir
    veille_actualite.py + suggerer_sujets_actualite) quand le sujet vient
    d'une suggestion tendance - impose de s'en tenir aux faits qu'il contient
    plutot que d'inventer, et de reformuler plutot que de recopier (voir
    consignes ci-dessous).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not theme.strip() and not contexte_expert.strip() and not (nom_client or "").strip():
        raise RuntimeError("Fournissez un sujet ou une fiche de reference.")

    bloc_theme = f"\nSujet demande :\n« {theme.strip()} »\n" if theme.strip() else ""
    bloc_contexte = (
        "\nContenu du site de l'auteur, pour retrouver son ton et son angle "
        "d'expertise (pas pour du remplissage litteral) :\n"
        f"{contexte_expert.strip()}\n"
        if contexte_expert.strip() else ""
    )
    bloc_article = (
        "\nExtrait reel de l'article source sur lequel porte ce sujet - c'est ta SEULE "
        "source de faits sur cette actualite precise, ne t'appuie sur aucune autre "
        "connaissance pour les details factuels (dates, chiffres, noms de fonctionnalites) :\n"
        f"« {contenu_article.strip()} »\n"
        if contenu_article.strip() else ""
    )

    bloc_deja = ""
    if deja_traites:
        liste = "\n".join(f"- {t}" for t in deja_traites[:14])
        bloc_deja = (
            "\nPosts DEJA publies ou programmes pour cette entreprise (le nouveau post doit s'en distinguer nettement : "
            "autre sujet ou autre angle, autre premiere ligne, autres exemples, autre structure ; ne reprends aucune de leurs "
            f"formulations) :\n{liste}\n"
        )
    if (texte_actuel or "").strip():
        bloc_deja += (
            "\nTexte actuellement affiche dans le composeur, que l'utilisateur ne veut PAS revoir (propose un post "
            f"clairement different, pas une variante) :\n« {texte_actuel.strip()[:1200]} »\n"
        )

    consigne_sourcage = (
        "- Base-toi strictement sur les faits presents dans l'extrait source fourni : "
        "n'invente aucun detail factuel (date, chiffre, fonctionnalite, citation) qui n'y "
        "figure pas. Si l'extrait ne suffit pas a etayer un point, reste general ou passe a "
        "l'analyse/l'avis plutot que de combler par une supposition presentee comme un fait.\n"
        "- L'extrait peut etre en anglais : redige en francais naturel, sans traduction mot a mot. Si l'article decrit "
        "un test ou une fonctionnalite observee aux Etats-Unis, dis-le clairement et n'affirme pas qu'elle est deja "
        "disponible en France ou en Belgique, sauf si l'extrait le precise.\n"
        "- Reformule entierement avec tes propres mots et ta propre structure : ne recopie "
        "aucune phrase ni formulation de l'extrait source, meme partiellement (pas de "
        "plagiat). L'objectif est ton avis et ta voix sur ce que dit l'article, pas un "
        "resume ou une traduction de celui-ci.\n"
        if contenu_article.strip() else
        "- Reste factuellement prudent sur l'actualite recente (dates, fonctionnalites "
        "precises) si le sujet fourni ne donne pas assez de details fiables : dans ce cas, "
        "privilegie une analyse de fond plutot que d'inventer des faits.\n"
    )

    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')} - utilise cette date comme repere "
        "temporel reel (n'ecris jamais une annee anterieure par reflexe).\n"
        "Tu rediges un post pour les reseaux sociaux et "
        "Google Business Profile d'un expert reconnu dans son domaine, a savoir le "
        f"{_specialite_auteur(nom_client)}. L'objectif n'est PAS d'informer platement, mais de construire une "
        "image d'expert que les lecteurs ont envie de suivre : une vraie prise de position, "
        "une analyse, un conseil concret tire de l'experience terrain - pas un simple resume "
        "d'actualite.\n"
        f"{bloc_theme}"
        f"{bloc_contexte}"
        f"{bloc_article}"
        f"{bloc_deja}\n"
        "Consignes :\n"
        "- Personne grammaticale : si le contexte contient un bloc « VOIX DU CLIENT », suis la sienne. Sinon "
        "deduis-la du contenu du site (« nous » = entreprise ou equipe, « je » = independant seul ; un nom de "
        "personne dans le nom de l'entreprise suggere un independant). En cas de doute, ecris a « nous » pour une "
        "entreprise et a « je » pour un independant clairement identifie. Ecris toujours a la premiere personne "
        "(« nous avons vu » / « j'ai vu », « ce que nous recommandons » / « ce que je recommande »...).\n"
        "- La toute premiere ligne doit se suffire a elle-meme et donner envie de lire la "
        "suite meme coupee juste apres (c'est la partie visible avant le « voir plus » sur "
        "la plupart des reseaux) : une observation surprenante, un chiffre frappant, un "
        "constat tranche ou une question - jamais une formule plate du type « Aujourd'hui, "
        "parlons de... » ou « Petite reflexion sur... ».\n"
        "- Prends position : un avis clair, un conseil actionnable, ou une mise en garde. "
        "Evite le ton neutre et consensuel d'un article encyclopedique, et bannis le jargon "
        "marketing creux (« game changer », « disruptif », « levier de croissance »...).\n"
        "- Phrases courtes, un paragraphe = une seule idee (1 a 3 phrases max) : c'est ce qui "
        "rend un post facile a lire sur mobile et donne du rythme, plutot que des paragraphes "
        "denses. Un exemple concret marque plus qu'une affirmation vague.\n"
        "- N'ecris AUCUN pourcentage ni frequence chiffree (« 9 fois sur 10 », « neuf fois sur dix », « la moitie des cas ») et n'invente AUCUNE anciennete (« depuis 15 ans », « apres toutes ces annees ») ni AUCUN chiffre, statistique, nombre d'avis ou d'interventions, annee d'experience, "
        "client, resultat ou realisation : uniquement ce qui figure dans le contexte fourni. A defaut, "
        "reste qualitatif.\n"
        "- Termine sur une phrase forte et memorable (une conviction, une question ouverte au "
        "lecteur, ou un conseil resume en une ligne) plutot que de s'eteindre sur une "
        "formule generique.\n"
        f"{consigne_sourcage}"
        "- Aucune reference geographique ni nom de ville (l'auteur n'est rattache a aucune "
        "localite en particulier ici).\n"
        "- Ton professionnel mais humain, pas de tiret cadratin (—) : remplace par une "
        "virgule, un deux-points ou un tiret simple (-).\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique (ex. « le cabinet reçoit au cabinet »). Relis-toi avant de conclure : la derniere phrase doit etre claire, naturelle et utile.\n"
        "- Longueur : entre 800 et 1300 caracteres (espaces compris) - un texte de base "
        "assez court pour rester adaptable a chaque reseau ensuite.\n"
        "- Passe des lignes entre chaque paragraphe (\\n\\n) plutot qu'un bloc compact.\n"
        "- Redige aussi un titre court et un prompt en anglais pour un generateur d'images. "
        "Vise une scene concrete et specifique, directement liee au sujet precis (un lieu, un "
        "objet ou une situation reconnaissable, pas une metaphore abstraite). Evite les cliches "
        "d'illustration IA generique (cadenas/bouclier de securite, tableau de bord abstrait, "
        "ampoule, poignee de main, reseau de points/globe connecte, engrenages) sauf si le sujet "
        "les impose vraiment. Sans texte incruste, sans logo, sans visage reconnaissable."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=2048,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_POST_UNIQUE}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return _nettoyer_champs_post(json.loads(bloc_texte))


SCHEMA_QUESTIONS_INTERVIEW = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["questions"],
    "additionalProperties": False,
}


QUESTION_SUJET_LIBRE = (
    "Envie de parler d'autre chose ? Vous avez la parole : un sujet qui vous tient à cœur, une actualité, "
    "un cas marquant, c'est vous qui choisissez."
)


def generer_questions_interview(
    contexte_expert: str = "", sujets_deja_traites: list[str] = None, nombre: int = 5, nom_client: str = "",
    avec_sujet_libre: bool = False,
) -> list[str]:
    """
    Questions courtes, pensees pour etre repondues a l'oral en 30-90 secondes
    (voir capture vocale mobile) plutot que pour etre publiees telles
    quelles - contrairement a suggerer_sujets_evergreen qui donne des angles
    de post, ici on demande une vraie question a laquelle l'auteur repond
    spontanement (son avis, son experience terrain, une anecdote client...),
    la redaction du post se faisant ensuite a partir de cette reponse (voir
    generer_post_depuis_reponse).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_contexte = (
        f"\nContenu du site de l'auteur, pour ancrer les questions dans son activite reelle :\n{contexte_expert.strip()}\n"
        if contexte_expert.strip() else ""
    )
    bloc_deja_traites = ""
    if sujets_deja_traites:
        liste = "\n".join(f"- {s}" for s in sujets_deja_traites)
        bloc_deja_traites = f"\nSujets/questions deja traites recemment (evite de reposer une question trop proche) :\n{liste}\n"

    prompt = (
        f"Propose {nombre - 1 if avec_sujet_libre else nombre} questions courtes a poser a un expert, dans le domaine suivant : le "
        f"{_specialite_auteur(nom_client)}, pour l'aider a produire du contenu regulierement sans avoir a partir "
        "d'une page blanche.\n"
        f"{bloc_contexte}"
        f"{bloc_deja_traites}\n"
        "Consignes :\n"
        "- Chaque question doit se repondre spontanement a l'oral en 30 a 90 secondes, sans "
        "recherche ni preparation : elle porte sur son avis, son experience terrain, une "
        "anecdote client, une erreur qu'il voit souvent, un conseil qu'il donne souvent - pas "
        "sur un fait precis qu'il faudrait verifier.\n"
        "- Formulee directement a la deuxieme personne, comme si on la lui posait a l'oral "
        "(« Quel est... », « Racontez... », « Qu'est-ce que vous repondez quand... »), pas a "
        "la troisieme personne.\n"
        "- Varie les angles (avis tranche, anecdote, conseil pratique, erreur frequente, "
        "prediction) plutot que de poser 5 variantes de la meme question.\n"
        "- Courte et concrete, jamais une generalite deja vue partout."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1024,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_QUESTIONS_INTERVIEW}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    questions = [q.strip() for q in json.loads(bloc_texte)["questions"] if q.strip()]
    if avec_sujet_libre:
        # La derniere proposition n'est pas une question de l'IA : elle laisse le client choisir son sujet.
        questions = questions[:max(1, nombre - 1)] + [QUESTION_SUJET_LIBRE]
    return questions


MARQUEUR_BLOC_VOIX = "=== VOIX DU CLIENT"


def analyser_voix_client(reponses: list[str]) -> str:
    """
    Portrait de la facon de parler et de penser d'un client, deduit de ses
    reponses vocales transcrites (Client.profil_voix). Ces vocaux sont la
    meilleure source de personnalite dont on dispose : ce portrait est
    reinjecte dans tous les prompts de redaction (voir
    main._contexte_ia_client) pour que les textes generes sonnent comme lui,
    pas comme une agence generique. Uniquement des observations tirees des
    transcriptions, jamais d'invention.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    reponses = [r.strip() for r in reponses if r and r.strip()]
    # Trop court pour en tirer une voix : l'IA broderait ou s'excuserait, et ce texte finirait dans les prompts.
    if sum(len(r) for r in reponses) < 300:
        return ""

    transcriptions = "\n\n".join(f"Vocal {i}:\n« {r[:3000]} »" for i, r in enumerate(reponses, 1))
    prompt = (
        "Voici des transcriptions brutes de reponses vocales d'une meme personne (un chef "
        "d'entreprise), a des questions sur son metier.\n\n"
        f"{transcriptions}\n\n"
        "Redige un portrait de sa VOIX, destine a un redacteur qui devra ecrire des posts et des "
        "reponses en son nom de facon a ce qu'on croie l'entendre parler. Reste factuel : "
        "n'invente rien, ne decris que ce qui ressort de ces transcriptions. Structure en puces "
        "courtes sous ces rubriques :\n"
        "- Registre et ton (familier/soutenu, direct, pedagogue, humour, assurance, "
        "bienveillance...).\n"
        "- Rythme et tournures (longueur des phrases, connecteurs favoris, facon d'enchainer les "
        "idees, comment il commence et conclut).\n"
        "- Expressions et mots recurrents : cite-les litteralement, entre guillemets (2 a 8).\n"
        "- Convictions et positions defendues (ce qu'il croit, ce qu'il refuse, ses principes).\n"
        "- Facon d'argumenter (exemples concrets, anecdotes clients, comparaisons, chiffres...).\n"
        "- A eviter pour rester fidele (ce qui sonnerait faux dans sa bouche).\n"
        "Maximum 1500 caracteres au total, en francais, sans tiret cadratin (—). Texte brut, avec les accents : "
        "pas de titre, pas de gras, pas de markdown, juste les rubriques suivies de leurs puces (« - »)."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1500,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "")
    return _nettoyer_texte_genere(texte)


def formater_bloc_voix(profil: str, extraits: list[str]) -> str:
    """Bloc de contexte "voix du client" a placer en tete du contexte IA (vide si rien de connu)."""
    profil = (profil or "").strip()
    extraits = [e.strip() for e in extraits if e and e.strip()]
    if not profil and not extraits:
        return ""
    morceaux = [
        f"{MARQUEUR_BLOC_VOIX} (a reproduire a l'ecrit) ===\n"
        "Ce client s'exprime a l'oral dans les vocaux ci-dessous. Tout texte ecrit en son nom doit "
        "sonner comme LUI : reprends son registre, son rythme, ses tournures et ses expressions, "
        "defends ses convictions, appuie-toi sur son vocabulaire plutot que sur du jargon marketing "
        "generique. Tu peux nettoyer les hesitations et structurer, mais ne le transforme jamais "
        "en voix d'agence lisse."
    ]
    if profil:
        morceaux.append(f"Portrait de sa facon de parler :\n{profil}")
    if extraits:
        liste = "\n".join(f"« {e} »" for e in extraits)
        morceaux.append(f"Extraits de ses vocaux (transcription brute, pour sentir son rythme) :\n{liste}")
    morceaux.append("=== FIN VOIX DU CLIENT ===")
    return "\n\n".join(morceaux)


def generer_post_depuis_reponse(question: str, reponse_orale: str, contexte_expert: str = "") -> dict:
    """
    Transforme une reponse orale (dictee, donc transcription brute :
    hesitations, phrases incompletes, repetitions) a une question
    d'interview (voir generer_questions_interview) en post structure, meme
    style que generer_post_expert (accroche, phrases courtes, chute
    memorable). Contrairement a generer_post_expert, la source de verite ici
    EST la reponse de l'auteur, pas un article externe : jamais un fait
    ajoute qu'il n'a pas dit, mais reformulation/structuration/polissage
    assumes - une transcription orale brute n'est pas destinee a etre
    publiee telle quelle.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not reponse_orale.strip():
        raise RuntimeError("Aucune reponse fournie.")

    bloc_contexte = (
        f"\nContenu du site de l'auteur, pour retrouver son ton et son angle d'expertise :\n{contexte_expert.strip()}\n"
        if contexte_expert.strip() else ""
    )

    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')} - utilise cette date comme repere "
        "temporel reel (n'ecris jamais une annee anterieure par reflexe).\n"
        "Tu transformes une reponse orale dictee (transcription automatique, donc parfois "
        "hesitante ou mal structuree) de l'auteur de la fiche en un post structure pour les "
        "reseaux sociaux.\n"
        + (
            "\nIl n'y avait pas de question imposee : l'auteur a choisi librement le sujet dont il avait envie de parler. "
            "Deduis le sujet de sa reponse.\n"
            if question.strip() == QUESTION_SUJET_LIBRE else f"\nQuestion posee :\n« {question.strip()} »\n"
        ) +
        f"\nSa reponse orale (transcription brute) :\n« {reponse_orale.strip()} »\n"
        f"{bloc_contexte}\n"
        "Consignes :\n"
        "- Cette transcription EST la seule source de faits/avis a utiliser : n'ajoute aucun "
        "detail, chiffre ou exemple qu'elle ne contient pas. En revanche, tu peux librement "
        "corriger les hesitations, reformuler les phrases incompletes, couper les repetitions "
        "et reorganiser l'ordre pour que ca se lise bien - un oral brut n'est pas publiable "
        "tel quel, mais le fond doit rester exactement le sien.\n"
        "- N'invente jamais une citation de client, une situation ou une frequence qu'il n'a pas "
        "evoquee (pas de « on me pose cette question a chaque fois » ni de dialogue imagine) : "
        "l'accroche doit venir de ce qu'il dit reellement, meme si elle est moins spectaculaire.\n"
        "- Conserve SA VOIX : garde ses tournures, ses expressions et son vocabulaire a lui, "
        "meme familiers (le bloc « VOIX DU CLIENT » du contexte, s'il existe, precise sa facon "
        "de parler). Le post doit se lire comme quelqu'un qui parle et qu'on reconnait, pas comme "
        "un communique d'entreprise.\n"
        "- Determine la personne grammaticale a partir du contenu du site ci-dessous : une "
        "personne seule qui parle en son nom (artisan, independant...) s'ecrit a la premiere "
        "personne du singulier (« je »), une equipe/entreprise avec plusieurs collaborateurs "
        "s'ecrit a la premiere personne du pluriel (« nous »). A defaut d'indice clair, utilise "
        "« je ».\n"
        "- La toute premiere ligne doit se suffire a elle-meme et accrocher (avant la coupure "
        "« voir plus » sur la plupart des reseaux) - jamais une formule plate.\n"
        "- Phrases courtes, un paragraphe = une seule idee (1 a 3 phrases max).\n"
        "- Termine sur une phrase forte et memorable plutot qu'une formule generique.\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique (ex. « le cabinet reçoit au cabinet »). Relis-toi avant de conclure : la derniere phrase doit etre claire, naturelle et utile.\n"
        "- Ton humain et direct, pas de jargon marketing creux, pas de tiret "
        "cadratin (—) : remplace par une virgule, un deux-points ou un tiret simple (-).\n"
        "- Aucune reference geographique ni nom de ville.\n"
        "- Longueur : entre 800 et 1300 caracteres (espaces compris).\n"
        "- Passe des lignes entre chaque paragraphe (\\n\\n) plutot qu'un bloc compact.\n"
        "- Redige aussi un titre court et un prompt en anglais pour un generateur d'images. "
        "Vise une scene concrete et specifique, directement liee au sujet precis (un lieu, un "
        "objet ou une situation reconnaissable, pas une metaphore abstraite). Evite les cliches "
        "d'illustration IA generique (cadenas/bouclier de securite, tableau de bord abstrait, "
        "ampoule, poignee de main, reseau de points/globe connecte, engrenages) sauf si le sujet "
        "les impose vraiment. Sans texte incruste, sans logo, sans visage reconnaissable."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=2048,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_POST_UNIQUE}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return _nettoyer_champs_post(json.loads(bloc_texte))


SCHEMA_SUGGESTIONS_SUJETS = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sujet": {"type": "string"},
                    "index_article": {"type": "integer"},
                },
                "required": ["sujet", "index_article"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


SCHEMA_VEILLE_CLIENT = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["seo", "metier"]},
        "requetes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "requetes"],
    "additionalProperties": False,
}


def definir_veille_client(nom_client: str, contexte: str = "") -> dict:
    """
    Decide quelle veille d'actualite convient a un client : "seo" (agence / consultant en SEO, marketing digital,
    referencement local : veille SEO habituelle) ou "metier" (tout autre metier : actualite de son secteur), et,
    pour "metier", 3 requetes de recherche d'actualite (francophone, Belgique / France) tirees de son activite.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    bloc_contexte = f"\nContenu de son site web :\n{contexte.strip()[:3500]}\n" if (contexte or "").strip() else ""
    prompt = (
        f"Entreprise : « {nom_client} ».\n{bloc_contexte}\n"
        "Deduis de son nom (et de son site s'il est fourni) son metier. Renvoie :\n"
        "- type : « seo » UNIQUEMENT si l'entreprise est elle-meme une agence ou un consultant en referencement (SEO), "
        "referencement local, marketing digital ou webmarketing ; « metier » pour tout autre activite.\n"
        "- requetes : 3 requetes courtes (2 a 4 mots) pour chercher dans l'actualite francophone (Belgique, France) "
        "des sujets utiles a cette entreprise pour communiquer aupres de ses clients : son metier et ses clients "
        "(prevention, conseils, reglementation, materiel, saison), pas l'actualite de l'entreprise elle-meme ni "
        "de sa concurrence. Pour « seo », renvoie une liste vide."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=400, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_VEILLE_CLIENT}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucune reponse exploitable.")
    donnees = json.loads(bloc)
    requetes = [r.strip() for r in donnees["requetes"] if r.strip()][:4]
    if donnees["type"] == "metier" and not requetes:
        raise RuntimeError("Aucune requete de veille deduite pour ce client.")
    return {"type": donnees["type"], "requetes": requetes}


def suggerer_sujets_actualite(
    articles: list[dict], nombre: int = 5, sujets_deja_traites: list[str] = None, nom_client_metier: str = "",
) -> list[dict]:
    """
    A partir d'articles d'actualite deja recuperes (voir veille_actualite.py),
    fait choisir et reformuler par l'IA les {nombre} sujets les plus
    interessants a commenter pour un expert SEO local/Google Business Profile
    - pas une simple liste de titres recopies, mais des angles de post
    concrets (utilisables tels quels dans le champ "sujet" du generateur de
    post). Renvoie [{"sujet", "titre_article", "source", "url"}, ...], chaque
    suggestion reliee a l'article qui l'a inspiree (index renvoye par l'IA,
    remappe ici vers l'article reel plutot que de faire confiance a l'IA
    pour recopier une URL sans erreur).
    sujets_deja_traites : sujets des posts precedents de cette fiche (voir
    _sujets_deja_traites_client dans main.py), pour que l'IA evite de
    reformuler un angle deja utilise recemment - meme logique que pour la
    generation de posts classique.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not articles:
        raise RuntimeError("Aucun article d'actualite disponible pour le moment.")

    liste_articles = "\n".join(
        f"{i}. {a['titre']} ({a['source'] or 'source inconnue'}, "
        f"{a['date_publication'].strftime('%d/%m/%Y') if a['date_publication'] else 'date inconnue'})"
        for i, a in enumerate(articles)
    )

    bloc_deja_traites = ""
    if sujets_deja_traites:
        liste_deja_traites = "\n".join(f"- {s}" for s in sujets_deja_traites)
        bloc_deja_traites = (
            "\nSujets deja traites recemment sur cette fiche (evite de reformuler un angle "
            f"trop proche de l'un de ceux-ci, meme sur un article different) :\n{liste_deja_traites}\n"
        )

    if nom_client_metier:
        introduction = (
            f"Voici une liste d'articles d'actualite recente en lien avec l'activite de l'entreprise « {nom_client_metier} » "
            f"(deduis son metier de son nom) :\n\n{liste_articles}\n{bloc_deja_traites}\n"
            f"Choisis les {nombre} articles les plus interessants a commenter pour ce professionnel qui veut publier du "
            "contenu utile et credible aupres de ses clients (pas juste relayer l'info). Ecarte les faits divers "
            "sans conseil utile et tout angle alarmiste : transforme l'actualite en conseil concret et rassurant. "
            "Pour chacun, reformule un sujet de post concret et accrocheur "
        )
    else:
        introduction = (
            "Voici une liste d'articles d'actualite recente sur le SEO local, Google Business "
            "Profile, Google AI Overviews et Google Local Services Ads :\n\n"
            f"{liste_articles}\n"
            f"{bloc_deja_traites}\n"
            f"Choisis les {nombre} articles les plus interessants a commenter pour un expert "
            "SEO local qui veut publier du contenu qui donne envie de le suivre (pas juste "
            "relayer l'info). Pour chacun, reformule un sujet de post concret et accrocheur "
        )
    prompt = (
        f"{introduction}"
        "(une phrase courte, en francais, prete a etre utilisee comme angle de redaction - "
        "pas juste le titre de l'article recopie), et indique l'index de l'article source.\n"
        "Consignes :\n"
        f"- Exactement {nombre} suggestions, toutes sur des articles differents.\n"
        "- Privilegie les sujets les plus recents et les plus specifiques (evite les "
        "generalites deja connues).\n"
        "- Chaque sujet doit donner un angle clair (ex : une consequence pratique, une "
        "question que ca souleve, un conseil qui en decoule), pas juste redire le titre.\n"
        "- Les articles sont en francais ou en anglais ; les sources marquees « (États-Unis) » sont americaines. "
        "Formule toujours le sujet en francais. Pour un article americain, presente la nouveaute comme une "
        "tendance venue des Etats-Unis (« aux Etats-Unis, Google teste... ») sans affirmer qu'elle est deja "
        "disponible en France ou en Belgique, sauf si l'article le dit. Garde un bon melange de sources "
        "francophones et americaines."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=max(1536, 220 * nombre + 700),
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_SUGGESTIONS_SUJETS}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    suggestions = []
    for item in json.loads(bloc_texte)["suggestions"]:
        index = item.get("index_article")
        if not isinstance(index, int) or not (0 <= index < len(articles)):
            continue
        article = articles[index]
        suggestions.append({
            "sujet": item["sujet"].strip(),
            "titre_article": article["titre"],
            "source": article["source"],
            "url": article["url"],
            # Contenu reel de l'article (voir veille_actualite.py), transmis au
            # navigateur pour etre renvoye tel quel a generer_post_expert le
            # moment venu - evite d'avoir a re-televerser toute la veille pour
            # retrouver l'article d'origine au moment de la redaction.
            "extrait": article.get("extrait", ""),
        })
    return suggestions


SCHEMA_SUGGESTIONS_EVERGREEN = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sujet": {"type": "string"},
                    "categorie": {"type": "string"},
                },
                "required": ["sujet", "categorie"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def suggerer_sujets_evergreen(
    contexte_expert: str = "", sujets_deja_traites: list[str] = None, nombre: int = 5, nom_client: str = "",
) -> list[dict]:
    """
    Sujets de post independants de l'actualite du jour (contrairement a
    suggerer_sujets_actualite) : la veille peut manquer de matiere fraiche
    certains jours (rien de neuf publie sur un creneau aussi precis), alors
    que ce type de sujet ne s'epuise jamais - utile pour garder un rythme de
    publication regulier sans etre bloque par un jour calme cote actualite.
    Renvoie [{"sujet", "categorie"}, ...] (pas d'article source, donc pas de
    grounding a transmettre a generer_post_expert - ces sujets s'appuient sur
    l'expertise de l'auteur, pas sur un fait d'actualite precis).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_contexte = (
        f"\nContenu du site de l'auteur, pour ancrer les sujets dans son activite reelle :\n{contexte_expert.strip()}\n"
        if contexte_expert.strip() else ""
    )
    bloc_deja_traites = ""
    if sujets_deja_traites:
        liste = "\n".join(f"- {s}" for s in sujets_deja_traites)
        bloc_deja_traites = f"\nSujets deja traites recemment (evite de reformuler un angle trop proche) :\n{liste}\n"

    prompt = (
        f"Propose {nombre} sujets de post pour un expert, dans le domaine suivant : le "
        f"{_specialite_auteur(nom_client)}, qui veut publier regulierement pour construire sa visibilite - "
        "meme les jours ou il n'y a pas d'actualite fraiche sur ces sujets precis.\n"
        f"{bloc_contexte}"
        f"{bloc_deja_traites}\n"
        "Varie les types d'angles, par exemple (sans s'y limiter) : demonter un mythe ou une idee "
        "recue repandue dans ce domaine, repondre a une question que les clients posent "
        "frequemment, proposer un test ou une verification que le lecteur peut faire lui-meme en "
        "quelques minutes, un avant/apres ou un cas concret type, une prediction ou une tendance a "
        "surveiller. Chaque sujet doit etre concret et actionnable, pas une generalite deja vue "
        "partout (« l'importance de la qualite » par exemple).\n"
        f"Pour chacun, indique aussi sa categorie (2-3 mots, ex : \"Mythe demonte\", \"Question "
        "frequente\", \"Test pratique\")."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=max(1536, 220 * nombre + 700),
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_SUGGESTIONS_EVERGREEN}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return [
        {"sujet": item["sujet"].strip(), "categorie": item["categorie"].strip()}
        for item in json.loads(bloc_texte)["suggestions"]
    ]


def generer_posts_generiques(theme: str = "", contenu_site_reference: str = "", nombre_posts: int = 5) -> list[dict]:
    """
    Variante de generer_post_generique() qui produit plusieurs propositions
    differentes en un seul appel (page "Posts en masse" : Jonathan choisit
    ensuite celle qu'il prefere), plutot qu'une seule redaction imposee -
    meme logique de neutralite geographique que la version unique.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not theme.strip() and not contenu_site_reference.strip():
        raise RuntimeError("Fournissez un theme ou une fiche de reference.")

    bloc_theme, bloc_reference = _blocs_theme_et_reference(theme, contenu_site_reference)

    prompt = (
        f"Redige {nombre_posts} propositions DIFFERENTES de post Google Business Profile "
        "(Google Posts), chacune destinee a etre publiee telle quelle sur plusieurs fiches "
        "d'etablissements differents, potentiellement situes dans des villes ou regions "
        "differentes (pas un seul client precis).\n"
        f"{bloc_theme}"
        f"{bloc_reference}\n"
        "Consignes :\n"
        "- Reste generique et geographiquement neutre : n'inclus AUCUN nom de ville, region, "
        "adresse ou reference locale, meme si la source d'inspiration en contient. N'invente "
        "aucun detail specifique a une entreprise en particulier (pas de nom d'entreprise, "
        "pas d'offre commerciale precise).\n"
        "- Chaque proposition doit traiter le theme sous un angle different (accroche, conseil "
        "pratique, question, chiffre-cle, temoignage generique...), pour offrir un vrai choix.\n"
        "- Ton professionnel, clair, engageant. Pas de jargon inutile.\n"
        "- Longueur : entre 1200 et 1500 caracteres (espaces compris), comme un vrai Google Post developpe.\n"
        f"- {REGLES_GOOGLE_POST}\n"
        "- Passe des lignes entre les idees pour aerer le texte (pas un seul bloc compact) : "
        "utilise des sauts de ligne (\\n\\n) entre les paragraphes.\n"
        "- N'utilise jamais de tiret cadratin (—) : remplace par une virgule, un deux-points ou "
        "un tiret simple (-).\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique (ex. « le cabinet reçoit au cabinet »). Relis-toi avant de conclure : la derniere phrase doit etre claire, naturelle et utile.\n"
        "- Pour chaque proposition, redige aussi un titre court et un prompt en anglais pour un "
        "generateur d'images (illustration adaptee au theme, sans texte incruste ni logo, sans "
        "reference geographique)."
    )

    client = Anthropic(api_key=CLE_API)
    # Meme correction que generer_posts_pour_client (streaming + budget
    # proportionnel) : un budget fixe se fait tronquer en plein milieu d'une
    # chaine JSON passe quelques posts (1200-1500 caracteres chacun).
    max_tokens = min(64000, 2200 * max(nombre_posts, 1) + 2000)
    with client.messages.stream(
        model=MODELE_CLAUDE,
        max_tokens=max_tokens,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REPONSE}},
        messages=[{"role": "user", "content": prompt}],
    ) as flux:
        reponse = flux.get_final_message()

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    return [_nettoyer_champs_post(post, google=True) for post in json.loads(bloc_texte)["posts"]]


def suggerer_reponse_avis(
    commentaire_avis: str, note: int, contenu_site: str, consignes_avis: str = "", auteur: str = "",
) -> str:
    """Suggere une reponse courte et professionnelle a un avis Google, a relire avant envoi."""
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_consignes_client = (
        f"\nConsignes de style propres a cette entreprise (a respecter en priorite) :\n{consignes_avis}\n"
        "Attention : ces consignes concernent le style de l'entreprise qui repond (ex. un prenom pour "
        "signer), jamais le nom du client a qui l'on s'adresse.\n"
        if consignes_avis.strip()
        else ""
    )

    bloc_auteur = (
        f"Nom du client qui a laisse cet avis (fourni par Google, eventuellement un pseudo) : {auteur.strip()}\n"
        "Si tu commences par une salutation nominative, utilise UNIQUEMENT ce nom (ou son prenom s'il est "
        "visible) - ne jamais utiliser un autre nom trouve ailleurs dans ce prompt (ex. un prenom de "
        "signature mentionne dans les consignes de style, qui designe l'entreprise, pas le client).\n\n"
        if auteur.strip()
        else "Nom du client inconnu : n'utilise aucune salutation nominative (pas de \"Bonjour X\"), "
        "reste sur une formule generale comme \"Bonjour,\" ou \"Merci beaucoup,\".\n\n"
    )

    avis_sans_commentaire = not commentaire_avis.strip()
    if avis_sans_commentaire:
        bloc_avis = (
            f"Avis recu : uniquement une note de {note}/5, sans aucun commentaire ecrit.\n\n"
            "Il n'y a donc pas de contenu a commenter ou personnaliser : ne demande jamais au client "
            "de preciser son avis ou de fournir plus de details, ce message ne lui sera pas transmis. "
            "Redige simplement un remerciement court et chaleureux adapte a une note "
            f"de {note}/5 (positif si {note} est eleve, plus neutre/invitant a revenir en echange si {note} "
            "est moyen ou bas), sans faire reference a un commentaire qui n'existe pas."
        )
    else:
        bloc_avis = f"Avis recu (note {note}/5) :\n« {commentaire_avis} »"

    prompt = (
        "Tu rediges une reponse a un avis client Google, au nom de l'entreprise elle-meme "
        "(a la premiere personne, comme si le gerant repondait directement).\n\n"
        f"Contexte de l'entreprise :\n{contenu_site}\n"
        f"{bloc_consignes_client}\n"
        f"{bloc_auteur}"
        f"{bloc_avis}\n\n"
        "Consignes :\n"
        "- Remercie sincerement si l'avis est positif ; reste courtois, professionnel et jamais "
        "defensif si l'avis est negatif ou mitige (propose si besoin un echange direct pour resoudre "
        "le probleme, sans admettre de faute que tu ne connais pas).\n"
        "- Reste bref : 2 a 4 phrases.\n"
        "- Personnalise en fonction du contenu reel de l'avis s'il y en a un. N'invente aucun detail "
        "specifique que tu ne connais pas (nom d'employe, date, evenement precis).\n"
        "- Pas de formule signature du type « L'equipe de ... ». Reste naturel et humain.\n"
        "- N'utilise jamais de tiret cadratin (—) : remplace par une virgule, un deux-points ou "
        "un tiret simple (-).\n"
        "- Reponds uniquement avec le texte de la reponse, sans guillemets ni commentaire autour."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1024,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return _nettoyer_texte_genere(bloc_texte)


def resumer_avis_positifs(avis: list[dict]) -> str:
    """
    Resume en 1-2 phrases chaleureuses ce que plusieurs avis positifs (note et
    commentaire) mettent en avant en commun, pour le recap mensuel envoye au
    client. Utilise seulement quand il y a plusieurs avis (sinon la citation
    directe suffit, voir recap_mensuel.py).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_avis = "\n\n".join(
        f"- ({a.get('note', '?')}/5) « {a['commentaire']} »" for a in avis
    )

    prompt = (
        "Voici plusieurs avis Google recents et positifs laisses par des clients d'une entreprise :\n\n"
        f"{bloc_avis}\n\n"
        "Redige un resume chaleureux en 1 a 2 phrases, destine a etre envoye PAR l'agence qui gere la "
        "fiche Google A l'entreprise elle-meme (pas au client final), pour lui faire plaisir en lui "
        "montrant ce que ses propres clients apprecient. Mets en avant les points communs qui reviennent "
        "(ex. reactivite, qualite du travail, accueil...). Ne cite pas les auteurs nommement. Tutoie "
        "l'entreprise (le reste de l'email la tutoie). Reste naturel, evite les formules toutes faites et "
        "les superlatifs excessifs. N'utilise jamais de tiret cadratin (—) : remplace par une virgule, "
        "un deux-points ou un tiret simple (-). Reponds uniquement avec le texte du resume, sans "
        "guillemets ni commentaire autour."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=512,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return _nettoyer_texte_genere(bloc_texte)


SCHEMA_SEMENCES_MOTS_CLES = {
    "type": "object",
    "properties": {
        "semences": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["semences"],
    "additionalProperties": False,
}


def suggerer_semences_mots_cles(contenu_site: str, categorie: str, ville: str, limite: int = 10) -> list[str]:
    """
    Propose des mots-cles de depart pertinents pour ce client precis, a
    passer ensuite a google_ads_keywords.idees_mots_cles pour en obtenir le
    volume de recherche reel - melange volontaire de mots-cles courts
    (categorie + ville, l'essentiel du trafic) et de longue traine (services
    precis identifies dans le contenu du site, moins de volume individuel
    mais plus facile a bien positionner et souvent mieux converti).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    prompt = (
        f"Voici les informations d'une entreprise geree en SEO local :\n\n"
        f"Categorie Google : {categorie or 'non renseignee'}\n"
        f"Ville : {ville or 'non renseignee'}\n\n"
        f"Contenu de son site web :\n{(contenu_site or '(aucun contenu de site fourni)').strip()[:6000]}\n\n"
        f"Propose {limite} mots-cles de recherche Google pertinents pour cette entreprise, en francais, "
        "melangeant deux types :\n"
        "- des mots-cles courts (2-4 mots), du type \"categorie + ville\" ou variantes proches - le gros du "
        "volume de recherche ;\n"
        "- des mots-cles de longue traine (4-8 mots), plus specifiques, refletant des services ou "
        "problematiques precises identifiees dans le contenu du site (pas juste \"categorie ville\" repete) - "
        "moins de volume individuel mais plus faciles a bien positionner.\n"
        "Chaque mot-cle doit etre une requete plausible telle qu'un internaute la taperait reellement dans "
        "Google, pas une phrase. Ne repete pas deux fois la meme idee sous une forme a peine differente. "
        "Reponds uniquement avec la liste demandee."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1024,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_SEMENCES_MOTS_CLES}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    semences = json.loads(bloc_texte)["semences"]
    return [s.strip() for s in semences if s.strip()][:limite]


SCHEMA_QUESTIONS_VISIBILITE_IA = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def suggerer_questions_visibilite_ia(
    contenu_site: str, categorie: str, ville: str, questions_deja_suivies: list[str] = None, nombre: int = 5,
) -> list[str]:
    """
    Propose des questions plausibles qu'un internaute poserait a ChatGPT ou
    Gemini pour trouver ce type d'entreprise localement (voir ia_visibilite.py,
    RequeteVisibiliteIA) - contrairement a suggerer_semences_mots_cles (mots-cles
    courts pour Google Ads), ce sont de vraies questions conversationnelles,
    telles qu'on les taperait dans un assistant IA plutot que dans un moteur
    de recherche.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_deja_suivies = ""
    if questions_deja_suivies:
        liste = "\n".join(f"- {q}" for q in questions_deja_suivies)
        bloc_deja_suivies = (
            f"\nQuestions deja suivies pour ce client (ne propose ni doublon ni variante trop proche) :\n{liste}\n"
        )

    prompt = (
        f"Voici les informations d'une entreprise geree en SEO local :\n\n"
        f"Categorie Google : {categorie or 'non renseignee'}\n"
        f"Ville : {ville or 'non renseignee'}\n\n"
        f"Contenu de son site web :\n{(contenu_site or '(aucun contenu de site fourni)').strip()[:6000]}\n"
        f"{bloc_deja_suivies}\n"
        f"Propose {nombre} questions plausibles qu'une personne poserait a ChatGPT ou Gemini pour trouver ce "
        "type d'entreprise localement - de vraies questions conversationnelles telles qu'on les taperait dans "
        "un assistant IA (ex. « Quel est le meilleur serrurier a Ath ? », « Je cherche un plombier fiable pres "
        "de chez moi a Ath, qui recommandez-vous ? »), jamais des mots-cles Google courts. Varie les "
        "formulations (question directe, demande de recommandation, urgence...) et les angles (service "
        "precis, avis, disponibilite...) plutot que plusieurs variantes de la meme idee. Reponds uniquement "
        "avec la liste demandee."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1024,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_QUESTIONS_VISIBILITE_IA}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    return [q.strip() for q in json.loads(bloc_texte)["questions"] if q.strip()][:nombre]


SCHEMA_CONTENU_QUESTION_IA = {
    "type": "object",
    "properties": {
        "post": {"type": "string", "description": "Publication complete qui repond a la question"},
        "reponse_faq": {"type": "string", "description": "Reponse courte (2 a 3 phrases) pour la FAQ du site"},
    },
    "required": ["post", "reponse_faq"],
    "additionalProperties": False,
}


def generer_contenu_pour_question_ia(
    nom_client: str, contenu_site: str, ville: str, question: str, concurrents_cites: list = None,
) -> dict:
    """
    Contenu qui repond precisement a une question ou les assistants IA ne citent pas le client (voir geo_score) :
    une publication (a envoyer dans le composeur) et une reponse courte pour la FAQ du site. Les assistants reprennent
    les pages qui repondent directement a la question avec des faits verifiables, d'ou la consigne de n'utiliser que le
    contenu fourni.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    bloc_concurrents = ""
    if concurrents_cites:
        bloc_concurrents = f"Les assistants citent actuellement a la place : {', '.join(concurrents_cites[:4])} (ne les mentionne jamais).\n"
    prompt = (
        f"Une personne demande a un assistant IA : « {question} »\n"
        f"{bloc_concurrents}"
        f"L'entreprise {nom_client}{(' (' + ville + ')') if ville else ''} n'est pas citee dans la reponse. Redige du contenu qui repond "
        "precisement a cette question, au nom de l'entreprise, pour que les assistants puissent la reprendre.\n\n"
        f"Contenu de son site web (seule source de faits autorisee) :\n{(contenu_site or '(aucun contenu fourni)').strip()[:6000]}\n\n"
        "Fournis :\n"
        "1. « post » : une publication de 120 a 170 mots. Premiere phrase : la reponse directe a la question, en reprenant ses mots-cles "
        "(activite + ville). Ensuite 2 a 4 faits concrets issus du site (services, zone desservie, specificites, delais si indiques). "
        "Nom de l'entreprise et ville cites naturellement. Une phrase d'appel a l'action simple a la fin.\n"
        "2. « reponse_faq » : la meme reponse en 2 a 3 phrases autonomes, a coller dans la FAQ du site (commence par le nom de l'entreprise).\n\n"
        "Regles : francais naturel, tutoiement interdit, pas d'emoji, pas de hashtag, pas de tiret cadratin. N'invente AUCUN fait (prix, annees "
        "d'experience, certifications, avis, chiffres) : si le site ne donne pas l'information, ne l'ecris pas. Pas de superlatifs non prouves "
        "(« le meilleur », « numero 1 »). Reponds uniquement avec le JSON demande."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=1500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_CONTENU_QUESTION_IA}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc_texte = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    donnees = json.loads(bloc_texte)
    return {"post": _nettoyer_texte_genere(donnees["post"]), "reponse_faq": _nettoyer_texte_genere(donnees["reponse_faq"])}


SCHEMA_PLAN_ACTION = {
    "type": "object",
    "properties": {
        "priorites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titre": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["titre", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["priorites"],
    "additionalProperties": False,
}


def generer_plan_action_audit(
    nom_entreprise: str, completude: list[dict], analyse_site: dict = None, releves: list[dict] = None,
    citations_resultats: list[dict] = None, opportunites_mots_cles: list[dict] = None,
    autorite_site: dict = None,
) -> list[dict]:
    """
    Synthese en 3 priorites concretes a partir des constats deja etablis par
    l'audit (completude de la fiche, analyse technique du site, visibilite
    sur les mots-cles testes) - section "Plan d'action" de audit_prospect_pdf.py.
    Ne fait aucune analyse elle-meme, se contente de hierarchiser et reformuler
    des constats deja calcules ailleurs (evite d'inventer des chiffres).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    points_a_ameliorer = [item["libelle"] for item in completude if not item["complet"]]
    bloc_completude = (
        "Elements incomplets sur la fiche Google : " + ", ".join(points_a_ameliorer) + "."
        if points_a_ameliorer else "La fiche Google est complete sur tous les points verifies."
    )

    bloc_site = "Aucun site web renseigne, ou analyse technique non disponible."
    if analyse_site:
        blocages = [p["libelle"] for p in analyse_site.get("points_bloquants", [])]
        bloc_site = (
            f"Score de performance du site : {analyse_site.get('score_performance')}/100. "
            f"Score SEO : {analyse_site.get('score_seo')}/100. "
            + ("Points techniques a corriger : " + ", ".join(blocages) + "."
               if blocages else "Aucun point technique bloquant releve.")
        )

    bloc_visibilite = "Aucun mot-cle teste."
    if releves:
        lignes = []
        for releve in releves:
            resume = releve["resume"]
            ligne = f"- \"{releve['mot_cle']}\" : visible sur {resume['pourcentage_couverture']}% de la zone testee"
            if resume.get("position_moyenne"):
                ligne += f", position moyenne {resume['position_moyenne']}"
            lignes.append(ligne)
        bloc_visibilite = "\n".join(lignes)

    bloc_citations = "Presence sur les annuaires locaux non verifiee."
    if citations_resultats:
        valides = [r for r in citations_resultats if r.get("erreur") is None]
        if valides:
            absents = [r["nom"] for r in valides if not r["trouve"]]
            bloc_citations = (
                "Annuaires locaux ou l'entreprise n'a PAS ete retrouvee : " + ", ".join(absents) + "."
                if absents else "L'entreprise est presente sur tous les annuaires locaux verifies."
            )

    bloc_opportunites = "Aucune opportunite de mot-cle supplementaire identifiee."
    if opportunites_mots_cles:
        bloc_opportunites = "Mots-cles a fort volume non encore travailles : " + ", ".join(
            f'"{idee["mot_cle"]}" ({idee["volume_moyen_mensuel"]} recherches/mois)'
            for idee in opportunites_mots_cles[:5]
        ) + "."

    bloc_autorite = "Autorite du site (backlinks) non verifiee."
    if autorite_site:
        bloc_autorite = (
            f"Score d'autorite du site : {autorite_site['rang']}/100, avec {autorite_site['backlinks']} "
            f"backlinks provenant de {autorite_site['domaines_referents']} domaines differents."
        )

    prompt = (
        "Voici les constats d'un audit de visibilite locale Google realise pour l'entreprise "
        f'"{nom_entreprise}" :\n\n'
        f"1. Completude de la fiche Google Business Profile :\n{bloc_completude}\n\n"
        f"2. Audit technique du site web :\n{bloc_site}\n\n"
        f"3. Visibilite sur les mots-cles testes (recherche geolocalisee autour de la fiche) :\n{bloc_visibilite}\n\n"
        f"4. Presence sur les annuaires locaux :\n{bloc_citations}\n\n"
        f"5. Opportunites de mots-cles :\n{bloc_opportunites}\n\n"
        f"6. Autorite du site (backlinks) :\n{bloc_autorite}\n\n"
        "A partir de ces constats reels uniquement (n'invente aucun element non mentionne ci-dessus), "
        "identifie les 3 priorites d'action les plus impactantes pour ameliorer la visibilite locale de "
        "cette entreprise, classees de la plus urgente/impactante a la moins urgente. Pour chacune :\n"
        "- un titre court et concret (5-8 mots), qui donne envie d'agir ;\n"
        "- une description de 2 a 3 phrases expliquant pourquoi c'est important et ce qu'il faut faire "
        "concretement, en te basant sur les constats ci-dessus (jamais de conseil generique deconnecte "
        "des donnees fournies).\n"
        "Ton professionnel et direct, destine a convaincre un dirigeant non technique de l'interet de se "
        "faire accompagner. N'utilise jamais de tiret cadratin (—) : remplace par une virgule, un "
        "deux-points ou un tiret simple (-)."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1536,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_PLAN_ACTION}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    priorites = json.loads(bloc_texte)["priorites"]
    for priorite in priorites:
        priorite["titre"] = _nettoyer_texte_genere(priorite["titre"])
        priorite["description"] = _nettoyer_texte_genere(priorite["description"])
        # Filet de securite : il est arrive (rarement) que le modele laisse des
        # fragments de la structure JSON elle-meme dans le texte d'un champ -
        # jamais legitime ici, donc on rejette plutot que d'exposer ca au prospect.
        if "{" in priorite["titre"] or "}" in priorite["titre"] or "{" in priorite["description"] or "}" in priorite["description"]:
            raise RuntimeError("Sortie IA malformee pour le plan d'action.")
    return priorites[:3]


TONS_RESEAUX = {
    "google": (
        "Ton professionnel et informatif, oriente SEO local (met en avant un service ou un avantage "
        "concret pour un client a proximite). Sobre, pas d'emoji, pas de hashtag. "
        + REGLES_GOOGLE_POST
    ),
    "facebook": (
        "Ton chaleureux et communautaire, comme si on s'adressait directement aux habitants du quartier "
        "ou de la ville. Peut etre un peu plus developpe et conversationnel qu'un post Google. Quelques "
        "emojis pour donner de la vie (3 a 6 sur tout le post), toujours en lien avec ce qui est dit "
        "(un objet, un geste, une idee), places en debut de phrase cle ou en fin de paragraphe, jamais "
        "un par ligne ni en rafale. Pas de hashtag (peu efficaces sur Facebook, contrairement a Instagram)."
    ),
    "instagram": (
        "Ton dynamique et accrocheur des la premiere ligne, phrases courtes, emojis bienvenus "
        "(avec moderation, jamais plus de 3-4). Format plus visuel/rythme que les autres reseaux. "
        "Termine par 5 a 15 hashtags pertinents et specifiques (pas de hashtags generiques ou hors "
        "sujet type #love #instagood), sur une ligne separee a la fin du texte."
    ),
    "linkedin": (
        "Ton professionnel qui valorise l'expertise et le savoir-faire, oriente credibilite plutot que "
        "promotion directe. Phrases construites, avec un saut de ligne entre presque chaque phrase pour "
        "la lisibilite (format tres aere, typique de LinkedIn). Emoji tres discret et fonctionnel "
        "uniquement si utile pour structurer (ex. avant une liste), jamais decoratif. Termine par 1 a 3 "
        "hashtags cibles (pas plus)."
    ),
}

NOMS_RESEAUX = {
    "google": "Google Business Profile", "facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn",
    "wordpress": "WordPress", "youtube": "YouTube",
}


def prompt_image_depuis_texte(texte_post: str) -> str:
    """
    Prompt d'image (en anglais) deduit du texte d'un post ecrit a la main :
    les generateurs de post fournissent eux-memes un prompt_image, mais un
    texte saisi ou colle directement dans le composeur n'en a pas.
    Memes consignes de fond que ces generateurs (scene concrete et
    specifique, pas de cliches d'illustration IA, ni texte ni logo).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not texte_post.strip():
        raise RuntimeError("Aucun texte de post fourni.")

    prompt = (
        "Voici le texte d'un post pour les reseaux sociaux :\n\n"
        f"{texte_post.strip()[:2500]}\n\n"
        "Ecris un prompt, en anglais, pour un generateur d'images qui illustre ce post. "
        "Vise une scene concrete et specifique, directement liee a son sujet precis (un lieu, un objet ou une "
        "situation reconnaissable, pas une metaphore abstraite), rendue comme une photo prise sur le vif au "
        "smartphone (cadrage naturel, lumiere naturelle inegale, leger grain, lieu vecu et legerement en desordre, "
        "pas de flou d'arriere-plan artificiel) et non comme une photo de banque d'images. Aucun texte lisible "
        "nulle part (papiers, ecrans, affiches). Evite les cliches d'illustration IA generique (cadenas/bouclier de "
        "securite, tableau de bord abstrait, ampoule, poignee de main, reseau de points/globe connecte, engrenages) "
        "sauf si le sujet les impose vraiment. Sans logo, sans reference geographique.\n"
        "Reponds uniquement par le prompt, en 2 a 3 phrases, sans introduction ni guillemets."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=400, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip().strip('"')
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun prompt exploitable.")
    return _nettoyer_texte_genere(texte)


def prompt_video_depuis_texte(texte_post: str) -> str:
    """
    Prompt video (en anglais) deduit du texte d'un post ecrit a la main, pour Veo (voir veo_video.py). Meme
    esprit que prompt_image_depuis_texte : une scene concrete et specifique, pas une metaphore abstraite - mais
    pensee pour un court plan filme (quelques secondes) plutot qu'une photo fixe.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not texte_post.strip():
        raise RuntimeError("Aucun texte de post fourni.")

    prompt = (
        "Voici le texte d'un post pour les reseaux sociaux :\n\n"
        f"{texte_post.strip()[:2500]}\n\n"
        "Ecris un prompt, en anglais, pour un generateur de video (quelques secondes, un seul plan) qui illustre "
        "ce post. Vise une scene concrete et specifique, directement liee a son sujet precis (un lieu, un objet "
        "ou une situation reconnaissable, pas une metaphore abstraite), decris un mouvement de camera simple et "
        "realiste (lent traveling, leger zoom, camera fixe) et une action naturelle qui se deroule dans le plan. "
        "Rendu comme filme sur le vif au smartphone, pas comme une publicite lissee. Aucun texte lisible nulle "
        "part (papiers, ecrans, affiches). Evite les cliches d'illustration IA generique (cadenas/bouclier de "
        "securite, tableau de bord abstrait, ampoule, poignee de main, reseau de points/globe connecte, engrenages) "
        "sauf si le sujet les impose vraiment. Sans logo, sans reference geographique.\n"
        "Reponds uniquement par le prompt, en 2 a 3 phrases, sans introduction ni guillemets."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=400, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip().strip('"')
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun prompt exploitable.")
    return _nettoyer_texte_genere(texte)


def storyboard_video_depuis_texte(texte_post: str, nb_scenes: int = 3) -> list:
    """
    Decoupe le texte d'un post en plusieurs plans video (en anglais, pour Veo - voir veo_video.py) qui se suivent
    logiquement pour raconter une seule histoire coherente (ex : plan d'ensemble, puis un detail ou une action,
    puis une conclusion) plutot que des scenes sans rapport entre elles simplement juxtaposees. Chaque plan est
    ensuite genere separement par Veo puis enchaine (voir montage_video.monter_video, parametre videos).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not texte_post.strip():
        raise RuntimeError("Aucun texte de post fourni.")
    nb_scenes = max(2, min(int(nb_scenes or 3), 4))

    prompt = (
        "Voici le texte d'un post pour les reseaux sociaux :\n\n"
        f"{texte_post.strip()[:2500]}\n\n"
        f"Decoupe-le en exactement {nb_scenes} plans video courts (quelques secondes chacun) qui se suivent "
        "logiquement pour raconter une seule histoire coherente autour de ce sujet (par exemple : un plan "
        "d'ensemble ou d'etablissement, puis un plan de detail ou d'action, puis un plan de conclusion) - jamais "
        "des scenes sans rapport entre elles. Pour chaque plan, ecris un prompt en anglais pour un generateur de "
        "video : une scene concrete et specifique liee au sujet (pas une metaphore abstraite), un mouvement de "
        "camera simple et realiste (leger travelling, zoom lent, camera fixe), une action naturelle qui se "
        "deroule dans le plan. Rendu comme filme sur le vif au smartphone, pas comme une publicite lissee. Aucun "
        "texte lisible nulle part (papiers, ecrans, affiches). Evite les cliches d'illustration IA generique "
        "(cadenas/bouclier de securite, tableau de bord abstrait, ampoule, poignee de main, reseau de points/"
        "globe connecte, engrenages) sauf si le sujet les impose vraiment. Sans logo, sans reference geographique.\n"
        f"Reponds uniquement par les {nb_scenes} prompts, un par ligne, numerotes (1. ... 2. ... etc), sans autre texte."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=700, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip()
    lignes = [re.sub(r"^\s*\d+[\.\)]\s*", "", l).strip().strip('"') for l in texte.splitlines()]
    lignes = [l for l in lignes if l][:nb_scenes]
    if len(lignes) < 2:
        raise RuntimeError("L'IA n'a pas renvoyé assez de plans exploitables.")
    return lignes


# Styles proposes pour le carrousel d'images IA (voir storyboard_images_depuis_texte) : le style "photo" plus
# l'ensemble des styles d'illustration deja proposes pour une image isolee (OPTIONS_VISUEL["illustration"],
# defini plus bas dans ce fichier) - meme vocabulaire partout, et la reaction (avec_reaction) fonctionne avec
# n'importe lequel d'entre eux, pas seulement la BD.
STYLES_CARROUSEL_IA = [("", "Laisser l'IA choisir", ""), ("photo", "Photo realiste", "a realistic photograph, natural and well-balanced light, no illustration effect")]


def storyboard_images_depuis_texte(texte_post: str, nb_slides: int = 4, avec_reaction: bool = False, style_consigne: str = "") -> list:
    """
    Decoupe le texte d'un post en plusieurs scenes illustrees (prompts anglais) qui se suivent logiquement pour
    raconter une seule histoire, pour un carrousel d'images generees par IA (voir gemini_images.py). style_consigne :
    description en anglais du style visuel a repeter a l'identique sur chaque plan (voir STYLES_CARROUSEL_IA) - si
    vide, l'IA choisit elle-meme un style coherent. avec_reaction : ajoute a chaque scene un petit cameo de "la
    personne des photos de reference" qui reagit a ce qui s'y passe, en coin, pose/position differente a chaque
    plan - jamais decrit physiquement (meme regle que prompt_image_avec_options : le visage vient des photos, pas
    du texte) - fonctionne avec n'importe quel style_consigne.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not texte_post.strip():
        raise RuntimeError("Aucun texte de post fourni.")
    nb_slides = max(3, min(int(nb_slides or 4), 5))

    consigne_reaction = (
        "\nPour chaque plan, ajoute aussi un petit cameo de \"the person from the reference photos\" qui reagit a "
        "ce qui se passe dans la scene, en retrait dans un coin (varie le coin et sa pose/expression d'un plan a "
        "l'autre pour correspondre a ce qui s'y dit : surpris, qui hoche la tete, qui pointe du doigt, pouce leve, "
        "pensif...). Ne decris jamais son physique (visage, cheveux, age, vetements) : le visage vient des photos "
        "de reference, garde-le toujours bien visible et reconnaissable."
        if avec_reaction else ""
    )

    if style_consigne.strip():
        consigne_style = (
            f"Precise systematiquement ce style visuel, dans ces termes exacts : \"{style_consigne.strip()}\" - le "
            f"meme style sur les {nb_slides} plans, pour que la serie reste coherente visuellement."
        )
    else:
        consigne_style = (
            f"Choisis librement un style visuel adapte au sujet (photo realiste ou illustration), et precise-le "
            f"dans chacun des {nb_slides} prompts, dans les memes termes exacts a chaque fois, pour que la serie "
            f"reste coherente visuellement."
        )

    prompt = (
        "Voici le texte d'un post pour les reseaux sociaux :\n\n"
        f"{texte_post.strip()[:2500]}\n\n"
        f"Decoupe-le en exactement {nb_slides} plans qui se suivent logiquement pour raconter une seule histoire "
        "coherente autour de ce sujet (par exemple : une scene d'ouverture qui plante le decor, un ou deux plans "
        "de detail ou d'action qui developpent le propos, puis une scene de conclusion) - jamais des scenes sans "
        "rapport entre elles. Pour chaque plan, ecris un prompt en anglais pour un generateur d'images : une scene "
        f"concrete et specifique liee au sujet (pas une metaphore abstraite). {consigne_style} Aucun texte lisible "
        f"nulle part (bulles, affiches, ecrans)."
        f"{consigne_reaction}\n"
        f"Reponds uniquement par les {nb_slides} prompts, un par ligne, numerotes (1. ... 2. ... etc), sans autre texte."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=900, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip()
    lignes = [re.sub(r"^\s*\d+[\.\)]\s*", "", l).strip().strip('"') for l in texte.splitlines()]
    lignes = [l for l in lignes if l][:nb_slides]
    if len(lignes) < 2:
        raise RuntimeError("L'IA n'a pas renvoyé assez de plans exploitables.")
    return lignes


def prompt_image_avec_auteur(prompt_image: str, texte_post: str = "") -> str:
    """
    Reecrit un prompt d'image pour que l'AUTEUR du post (la personne des
    photos de reference envoyees a Gemini) en soit le sujet principal. Les
    prompts produits par les generateurs de post decrivent des scenes sans
    personne (ecrans, icones, mains...) : envoyes tels quels avec des photos
    de reference, Gemini ne les utilise pas et l'auteur n'apparait jamais.
    Le physique n'est volontairement jamais decrit : le visage vient des
    photos, pas du texte.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    prompt = (
        "Voici le prompt (en anglais) prevu pour illustrer un post de reseau social, puis le texte du post.\n\n"
        f"Prompt d'origine :\n{prompt_image.strip()}\n\n"
        f"Texte du post :\n{texte_post.strip()[:1500] or '(non fourni)'}\n\n"
        "Reecris ce prompt, en anglais, pour une PHOTO REALISTE dans laquelle l'auteur du post est le "
        "sujet principal. L'auteur est la personne des photos de reference jointes a la generation : ecris "
        "\"the person from the reference photos\" et ne decris JAMAIS son physique (visage, cheveux, age, "
        "vetements) - le visage vient des photos.\n"
        "Style : une photo PRISE SUR LE VIF AU SMARTPHONE, pas une photo de banque d'images. Concretement : "
        "cadrage naturel et legerement imparfait (un peu decentre, pas de composition parfaite) ; la personne "
        "est en train d'AGIR (regarde son ecran, ecrit, parle a quelqu'un hors champ, telephone...) et ne pose "
        "PAS face a l'objectif, sans sourire pose ; son visage reste bien visible (de trois quarts ou de profil "
        "leger, jamais de dos ni de loin) ; lumiere naturelle inegale (fenetre, ombres marquees, un peu de "
        "grain), aucune retouche, peau naturelle avec sa texture ; PAS de flou d'arriere-plan artificiel ni de "
        "faible profondeur de champ ; lieu vecu et legerement en desordre (cables, tasse, papiers, objets du "
        "quotidien), jamais impeccable.\n"
        "Interdits : AUCUN texte lisible nulle part (documents, ecrans, affiches, etiquettes, vetements) - les "
        "papiers sont retournes, flous ou hors champ, les ecrans montrent une interface sans lettres ; aucun "
        "logo ; aucune illustration ni style dessin ; ne pas ecrire \"professional\", \"studio\", \"stock photo\" "
        "ni \"shallow depth of field\".\n"
        "Reponds uniquement par le nouveau prompt, en 3 a 5 phrases, sans introduction ni guillemets."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=500, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip().strip('"')
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun prompt exploitable.")
    return _nettoyer_texte_genere(texte)


def adapter_post_multi_reseaux(
    texte_base: str, reseaux: list[str], contenu_site: str = "", hashtags_fixes: str = "",
) -> dict[str, str]:
    """
    A partir d'un texte de post de base, genere une variante adaptee au ton
    de chaque reseau demande (voir TONS_RESEAUX) - meme message de fond,
    formulation differente. reseaux : sous-ensemble de TONS_RESEAUX.keys().
    hashtags_fixes (Client.hashtags_fixes) : hashtags de marque propres au
    client, a inclure en plus des hashtags contextuels generes par l'IA, sur
    les reseaux qui en utilisent (Instagram, LinkedIn - voir TONS_RESEAUX).
    Renvoie {reseau: texte_adapte, ...} (memes cles que reseaux).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")

    reseaux = [r for r in reseaux if r in TONS_RESEAUX]
    if not reseaux:
        raise RuntimeError("Aucun reseau valide fourni.")

    bloc_tons = "\n".join(f"- {NOMS_RESEAUX[r]} : {TONS_RESEAUX[r]}" for r in reseaux)
    bloc_contexte = f"\nContexte sur l'entreprise (voix de l'auteur, site web) :\n{contenu_site.strip()[:7000]}\n" if contenu_site.strip() else ""
    bloc_hashtags_fixes = (
        f"\nHashtags de marque a toujours inclure en plus des hashtags contextuels, uniquement sur les "
        f"reseaux qui utilisent des hashtags (dans le respect du nombre indique pour chacun) : "
        f"{hashtags_fixes.strip()}\n"
        if hashtags_fixes.strip() else ""
    )

    prompt = (
        f"Voici un texte de post de base a publier sur plusieurs reseaux sociaux :\n\n"
        f'"{texte_base.strip()}"\n'
        f"{bloc_contexte}"
        f"{bloc_hashtags_fixes}\n"
        "Adapte ce message pour chacun des reseaux suivants, en gardant le meme fond (memes informations, "
        "meme offre, memes coordonnees le cas echeant, SAUF pour Google Business Profile qui n'accepte ni numero de telephone, "
        "ni lien, ni e-mail : retire-les de sa version) mais en ajustant le ton et la formulation :\n"
        f"{bloc_tons}\n\n"
        "Ne raccourcis ni n'allonge exagerement par rapport a l'original sauf si le ton du reseau l'exige "
        "naturellement. N'utilise jamais de tiret cadratin (—) : remplace par une virgule, un deux-points "
        "ou un tiret simple (-).\n"
        "Conserve strictement la meme personne grammaticale que le texte d'origine (s'il est ecrit a la "
        "premiere personne du singulier \"je\", reste en \"je\" : ne bascule jamais vers un \"nous\" "
        "d'entreprise generique).\n"
        "Si le contexte contient un bloc « VOIX DU CLIENT », l'auteur a une facon de parler bien a lui : "
        "garde ses tournures et ses expressions sur chaque reseau (on adapte le format et le ton du "
        "reseau, pas la personnalite de l'auteur).\n"
        "Phrases courtes, un paragraphe = une seule idee (1 a 3 phrases max), et passe des lignes entre "
        "chaque paragraphe (\\n\\n) plutot qu'un bloc compact : c'est ce qui rend un post lisible sur mobile."
    )

    schema = {
        "type": "object",
        "properties": {r: {"type": "string"} for r in reseaux},
        "required": reseaux,
        "additionalProperties": False,
    }

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        # 2048 etait trop juste avec 4 reseaux et un texte de base long (ex. le
        # generateur expert produit jusqu'a 1300 caracteres) : la reponse JSON
        # se faisait tronquer en plein milieu d'une chaine ("Unterminated
        # string" au parsing). Large marge plutot que de recalculer un budget
        # precis par nombre de reseaux/longueur, le cout supplementaire est
        # negligeable pour du texte.
        max_tokens=6144,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )

    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")

    variantes = json.loads(bloc_texte)
    resultat = {r: _nettoyer_texte_genere(variantes[r]) for r in reseaux}
    if "google" in resultat:
        resultat["google"] = nettoyer_texte_google(resultat["google"])      # filet de securite si l'IA a garde un contact
    return resultat


def generer_article_blog(sujet: str, contexte: str = "", lien_cta: str = "", texte_cta_impose: bool = False,
                         mot_cle: str = "", ville: str = "", pages: list = None, faits: str = "") -> str:
    """
    Article de blog complet (markdown : "# Titre", puis "## " pour les
    sous-titres) a partir d'un sujet ou d'un texte de base, pour publication
    sur le WordPress du client (voir wordpress_publish.py). contexte : contenu
    du site du client, avec sa "voix" en tete s'il y en a une (voir
    main._contexte_ia_client). mot_cle / ville : mot-cle principal et zone visee (repartis dans le titre, l'introduction, un sous-titre
    et la conclusion) ; pages : [{"titre", "url"}] du site du client, seules adresses que l'article peut utiliser pour ses liens internes ;
    faits : transcription brute d'un vocal du client (cas, prix, delais, materiel), seule source de faits reels de l'article.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (sujet or "").strip():
        raise RuntimeError("Aucun sujet fourni.")

    bloc_contexte = f"\nContexte sur l'entreprise (site web, voix de l'auteur) :\n{contexte.strip()[:7000]}\n" if contexte.strip() else ""
    consignes_seo = ""
    if (mot_cle or "").strip():
        consignes_seo += (f"\n- Mot-cle principal : « {mot_cle.strip()} ». Il figure dans le titre (au debut), dans les 100 premiers mots, dans au moins un "
                          "sous-titre « ## » et dans la conclusion, de facon naturelle (5 fois au maximum dans tout l'article).")
    if (ville or "").strip():
        consignes_seo += (f"\n- Ville ou zone ciblee : « {ville.strip()} ». Cite-la dans le titre si la longueur le permet, dans l'introduction et dans la "
                          "conclusion, sans bourrage.")
    if pages:
        liste_pages = "\n".join(f"  * {p['titre']} : {p['url']}" for p in pages[:40])
        consignes_seo += ("\n- Liens internes : ajoute 2 ou 3 liens vers les pages ci-dessous quand ils aident vraiment le lecteur, au format "
                          "[texte d'ancre naturel](adresse) dans le corps des paragraphes (jamais dans un titre ni dans l'encadre « A retenir », jamais deux fois "
                          "la meme page, ancre descriptive et jamais « cliquez ici »). N'invente aucune adresse : utilise uniquement celles de cette liste.\n"
                          f"Pages du site :\n{liste_pages}")
    bloc_faits = ""
    if (faits or "").strip():
        bloc_faits = ("\nPAROLES DU CLIENT (transcription brute d'un message vocal : c'est la SEULE source de faits reels de cet article) :\n"
                      f'"""\n{faits.strip()[:6000]}\n"""\n')
        consignes_seo += ("\n- Appuie l'article sur ces paroles du client : reprends ses exemples concrets (cas, prix, delais, materiel, lieux) tels qu'il les a dits, "
                          "au bon endroit, comme preuves d'experience, a la personne grammaticale de l'auteur. N'ajoute aucun autre fait precis. Ignore les hesitations "
                          "et les repetitions. N'inclus aucun nom de client final, numero de telephone ni plaque d'immatriculation cites dans ces paroles.")
    phrase_autres_liens = "Hors des liens internes autorises, n'utilise aucun autre lien." if pages else "N'utilise aucun autre lien."
    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')} (repere temporel reel, n'ecris jamais une annee "
        "anterieure par reflexe).\n"
        "Redige un article de blog complet, en francais, pour le site de l'entreprise decrite ci-dessous, a "
        "partir de ce sujet ou de ce texte de base :\n\n"
        f"\"{sujet.strip()}\"\n"
        f"{bloc_contexte}{bloc_faits}\n"
        "Consignes :\n"
        "- Format markdown : la toute premiere ligne est le titre, sous la forme « # Titre » : accrocheur, "
        "50 caracteres MAXIMUM (espaces compris, jamais plus de 55 : il s'affiche dans Google avec le nom du site), avec le "
        "mot-cle principal en premier. Ensuite une introduction (sans titre) dont les deux premieres phrases forment a "
        "elles seules un resume de 130 a 155 caracteres, car elles servent de description dans les resultats Google, puis "
        "3 a 5 sections avec des sous-titres « ## », puis une section « ## Questions frequentes » avec 3 ou 4 questions que les clients se posent "
        "vraiment (chacune en sous-titre « ### Question ? » suivi d'une reponse de 2 a 3 phrases, factuelle, sans affirmation inventee). Termine par une courte "
        "conclusion avec un appel a l'action (contacter l'entreprise), sans numero de telephone invente.\n"
        "- Longueur : 800 a 1300 mots (FAQ comprise). Paragraphes courts (2 a 4 phrases), listes a puces quand elles aident. "
        "Gras (**mot**) avec moderation.\n"
        "- Ajoute UN SEUL encadre « A retenir » (2 a 3 phrases qui resument l'essentiel), place apres "
        "la section la plus importante, sous la forme de lignes commencant par « > » (ex. « > **A retenir :** ... »).\n"
        "- Utile et concret pour un lecteur qui cherche ce service : conseils pratiques, erreurs frequentes, "
        "questions que se posent les clients. Optimise pour le referencement local de facon naturelle : "
        "mentionne les services et la zone d'intervention quand le contexte les donne, sans bourrage de mots-cles.\n"
        "- N'invente aucun fait precis que le contexte ne contient pas (prix, chiffres, certifications, delais, "
        "nom de collaborateur, avis client). Reste general plutot que de risquer une affirmation fausse.\n"
        "- Si le contexte contient un bloc « VOIX DU CLIENT », ecris comme lui : son registre, ses tournures, "
        "sa personne grammaticale. A defaut, « nous » pour une entreprise, « je » pour un independant seul.\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique (ex. « le cabinet reçoit au cabinet »). Relis-toi avant de conclure : la derniere phrase doit etre claire, naturelle et utile.\n"
        "- Pas de tiret cadratin (—), pas d'emoji, aucun commentaire autour : uniquement l'article."
        + consignes_seo
        + (
            "\n- Le bouton d'appel a l'action est ajoute automatiquement apres ton texte : n'en ecris pas."
            if texte_cta_impose else
            (
                f"\n- Termine l'article par UNE ligne seule, exactement de la forme [[Texte du bouton|{lien_cta}]] : "
                "le texte (2 a 4 mots) invite a contacter l'entreprise, a la personne grammaticale de l'auteur "
                "(« Me contacter » si l'auteur est un independant qui dit « je », « Nous contacter » pour une equipe), "
                "ou plus precis si le sujet s'y prete (« Demander un devis », « Prendre rendez-vous »). "
                + phrase_autres_liens
                if lien_cta else ""
            )
        )
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=8000,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc_texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if not bloc_texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    return _titre_court_si_besoin(_nettoyer_texte_genere(bloc_texte))


TITRE_ARTICLE_MAX = 58


def _titre_court_si_besoin(article: str) -> str:
    """Si le titre « # ... » depasse TITRE_ARTICLE_MAX caracteres, il est raccourci par l'IA ; en cas d'echec, l'article reste tel quel."""
    lignes = article.split("\n")
    for indice, ligne in enumerate(lignes):
        if not ligne.strip():
            continue
        correspondance = re.match(r"^#\s+(\S.*)$", ligne)
        if not correspondance or len(correspondance.group(1)) <= TITRE_ARTICLE_MAX:
            return article
        try:
            client = Anthropic(api_key=CLE_API)
            reponse = client.messages.create(
                model=MODELE_CLAUDE, max_tokens=120, thinking={"type": "disabled"},
                messages=[{"role": "user", "content": (
                    f"Raccourcis ce titre d'article à 50 caractères maximum (espaces compris), en gardant le mot-clé principal "
                    f"en premier et le même sens, sans tiret cadratin ni emoji. Réponds uniquement par le nouveau titre.\n\n"
                    f"{correspondance.group(1)}"
                )}],
            )
            nouveau = next((b.text for b in reponse.content if b.type == "text"), "").strip().strip("«»\"#").strip()
            nouveau = _nettoyer_texte_genere(nouveau).replace("\n", " ")
            if nouveau and len(nouveau) <= TITRE_ARTICLE_MAX:
                lignes[indice] = f"# {nouveau}"
                return "\n".join(lignes)
        except Exception:
            pass
        return article
    return article


# ---------------------------------------------------------------------------
# Options du visuel (cases a cocher du composeur multi-reseaux) : chaque choix
# ajoute une consigne d'image ; tout est facultatif, et sans aucun choix la
# generation reste exactement celle d'avant. (id, libelle affiche, consigne en anglais)
# ---------------------------------------------------------------------------
OPTIONS_VISUEL = {
    "type": [
        ("instantane", "Photo sur le vif", "a candid snapshot taken on a smartphone in the middle of a real moment (natural, slightly imperfect framing, uneven natural light, a bit of grain)"),
        ("portrait", "Portrait posé, professionnel", "a posed professional portrait: the subject looks toward the camera, relaxed and confident, clean natural look, well lit"),
        ("action", "Scène de travail en action", "a work-in-action scene: the subject is actively doing the job, absorbed in the task and not looking at the camera"),
        ("detail", "Gros plan sur un détail", "a close-up detail shot (hands, a tool, a material or a screen) with no face visible"),
        ("realisation", "Réalisation ou produit", "a showcase of a finished piece of work or a product, well lit, the result is the hero of the image"),
        ("lieu", "Lieu ou ambiance", "an atmosphere shot of the place itself (storefront, workshop or office), with no people or only tiny figures far in the background"),
        ("illustration", "Illustration simple", "a simple flat vector-style illustration, clean shapes and a limited palette, no photorealism"),
    ],
    "illustration": [
        ("plat", "Plat (vectoriel)", "a clean flat vector illustration: simple geometric shapes, limited color palette, subtle shadows"),
        ("isometrique", "Isométrique", "an isometric illustration of a small cutaway scene: clean lines, soft flat colors, tidy details"),
        ("bd", "BD (ligne claire)", "a comic-book (bande dessinee) illustration in the franco-belgian clear-line style: confident ink outlines, flat colors, expressive characters with natural proportions"),
        ("humour", "Humour (dessin d'humour)", "a humorous cartoon drawing in the spirit of a witty web comic: simplified characters with big expressive faces, thick hand-drawn outlines, bright flat colors, a light comedic situation; no speech bubbles containing text"),
        ("schema", "Schéma dessiné à la main", "a hand-drawn explanatory diagram, like a sketch on a whiteboard or paper: simple line drawings linked by arrows and brackets, marker strokes; unlabeled shapes, icons and arrows only"),
        ("croquis", "Croquis au crayon", "a loose pencil sketch with visible construction lines and hatching on off-white paper, unfinished sketchbook look, a single accent color"),
        ("aquarelle", "Aquarelle", "a soft watercolor illustration with gentle washes and visible paper texture"),
        ("craie", "Craie sur tableau noir", "a chalk drawing on a dark blackboard: white and pastel chalk lines, hand-drawn look"),
        ("vintage", "Affiche vintage", "a retro vintage poster illustration: limited palette, screen-print texture, bold simple shapes"),
        ("decoupe", "Papier découpé", "a layered paper-cut illustration with soft shadows between the layers"),
    ],
    "cadrage": [
        ("large", "Plan large", "wide shot showing the whole space"),
        ("moyen", "Plan moyen", "medium shot, subject framed from the waist up"),
        ("gros_plan", "Gros plan", "tight close-up"),
        ("dessus", "Vue du dessus", "top-down view of objects laid on a table"),
        ("selfie", "Selfie", "selfie-style framing at arm's length, slightly wide-angle"),
    ],
    "decor": [
        ("atelier", "Atelier ou bureau", "in a workshop or an office"),
        ("client", "Chez le client", "at a client's place (a home or a business)"),
        ("exterieur", "Extérieur ou chantier", "outdoors, on site or on a job site"),
        ("cafe", "Café ou coworking", "in a cafe or a coworking space"),
        ("neutre", "Fond neutre", "against a plain, neutral background"),
    ],
    "lumiere": [
        ("douce", "Lumière naturelle douce", "soft natural daylight"),
        ("doree", "Heure dorée", "warm golden-hour light"),
        ("epuree", "Lumineux et épuré", "a bright, airy, clean look"),
        ("chaleureux", "Chaleureux", "warm, cozy tones"),
        ("saison", "Ambiance de saison", "a seasonal atmosphere matching the current period ({saison})"),
    ],
    "personnes": [
        ("moi", "Moi", "the person from the reference photos is the main subject"),
        ("moi_client", "Moi avec un client ou un collègue", "the person from the reference photos interacting naturally with a second, different person (a client or a colleague, who is NOT the reference person)"),
        ("lieu_reference", "Le lieu / bâtiment (photos de référence)", "the exact place shown in the reference photos is the subject"),
        ("mains", "Mes mains seulement", "only hands are visible, no face"),
        ("personne", "Personne", "no people at all"),
        ("equipe", "Une équipe", "a small team of two to four people working together, faces not the focus"),
    ],
}
# Complete apres coup : les styles d'illustration sont definis juste au-dessus, STYLES_CARROUSEL_IA est declare
# plus haut dans le fichier (avec storyboard_images_depuis_texte) pour rester a cote de la fonction qui l'utilise.
STYLES_CARROUSEL_IA += OPTIONS_VISUEL["illustration"]
STYLES_CARROUSEL_IA_PAR_ID = {identifiant: consigne for identifiant, _libelle, consigne in STYLES_CARROUSEL_IA}
LIBELLES_GROUPES_VISUEL = {
    "type": "Type de visuel", "illustration": "Style d'illustration", "cadrage": "Cadrage", "decor": "Décor", "lumiere": "Lumière et ambiance",
    "personnes": "Qui ou quoi apparaît",
}
# Le type impose des limites : ces types ne montrent pas de visage, donc jamais les photos de reference d'un
# visage - sauf "lieu_reference", qui a justement pour but de montrer le lieu (voir REFERENCE_LIEU ci-dessous).
TYPES_SANS_VISAGE = {"detail", "lieu"}
# Parmi les styles d'illustration, seul le schema (diagramme, formes et fleches) n'a pas de personnage :
# les autres (BD, humour, aquarelle...) peuvent tres bien mettre en scene la personne des photos de reference.
ILLUSTRATIONS_SANS_VISAGE = {"schema"}
PERSONNES_AVEC_REFERENCE = {"moi", "moi_client"}
PERSONNES_SANS_REFERENCE = {"mains", "personne", "equipe"}
# Photos de reference d'un lieu (chantier, programme immobilier...) plutot que d'un visage : memes photos de
# reference client (voir models.PhotoReferenceClient), mais reutilisees pour reproduire fidelement un lieu/batiment
# plutot qu'une personne - jamais soumises aux memes limites que les photos de visage (TYPES_SANS_VISAGE).
REFERENCE_LIEU = {"lieu_reference"}


def _index_options(groupe: str) -> dict:
    return {i: (libelle, consigne) for i, libelle, consigne in OPTIONS_VISUEL[groupe]}


def saison_courante(aujourdhui: date = None) -> str:
    mois = (aujourdhui or date.today()).month
    return "winter" if mois in (12, 1, 2) else "spring" if mois in (3, 4, 5) else "summer" if mois in (6, 7, 8) else "autumn"


def nom_couleur(couleur: str) -> str:
    """Nom anglais approximatif d'une couleur #rrggbb (pour la decrire a un generateur d'images)."""
    import colorsys
    try:
        r, g, b = (int(couleur[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (ValueError, IndexError, TypeError):
        return ""
    teinte, saturation, valeur = colorsys.rgb_to_hsv(r, g, b)
    if valeur < 0.2:
        return "near-black"
    if saturation < 0.15:
        return "grey" if valeur < 0.85 else "white"
    degres = teinte * 360
    for limite, nom in ((15, "red"), (40, "orange"), (65, "yellow"), (165, "green"), (200, "teal"), (255, "blue"), (290, "purple"), (345, "pink")):
        if degres < limite:
            return nom
    return "red"


def choisir_options_visuel(options: dict, varier: bool = False, hasard=None) -> dict:
    """
    Valide les options recues du navigateur (ids inconnus ignores) et, si `varier`,
    tire au hasard un choix pour chaque groupe laisse vide (type, cadrage, decor,
    lumiere - jamais "qui apparait"), en evitant les combinaisons absurdes.
    Renvoie {"type", "cadrages", "decors", "lumieres", "personnes", "aleatoires": [(groupe, id)]}.
    """
    import random
    hasard = hasard or random
    options = options or {}
    connus = {g: set(_index_options(g)) for g in OPTIONS_VISUEL}

    def un(groupe):
        valeur = options.get(groupe)
        return valeur if valeur in connus[groupe] else None

    def plusieurs(groupe):
        valeurs = options.get(groupe)
        valeurs = valeurs if isinstance(valeurs, list) else ([valeurs] if valeurs else [])
        return [v for v in dict.fromkeys(valeurs) if v in connus[groupe]]

    choix = {
        "type": un("type"), "cadrages": plusieurs("cadrage"), "decors": plusieurs("decor"),
        "lumieres": plusieurs("lumiere"), "personnes": un("personnes"), "illustration": un("illustration"), "aleatoires": [],
    }
    if choix["illustration"] and not choix["type"]:
        choix["type"] = "illustration"  # choisir un style d'illustration revient a choisir le type "illustration"
    if choix["type"] != "illustration":
        choix["illustration"] = None
    if varier:
        if not choix["type"]:
            choix["type"] = hasard.choice([i for i in connus["type"] if i != "illustration"])
            choix["aleatoires"].append(("type", choix["type"]))
        type_choisi = choix["type"]
        if type_choisi == "illustration" and not choix["illustration"]:
            choix["illustration"] = hasard.choice(sorted(connus["illustration"]))
            choix["aleatoires"].append(("illustration", choix["illustration"]))
        if not choix["cadrages"] and type_choisi != "illustration":
            candidats = {"detail": ["gros_plan", "dessus"], "lieu": ["large", "moyen"]}.get(type_choisi, sorted(connus["cadrage"]))
            choix["cadrages"] = [hasard.choice(candidats)]
            choix["aleatoires"].append(("cadrage", choix["cadrages"][0]))
        if not choix["decors"] and type_choisi != "illustration":
            choix["decors"] = [hasard.choice(sorted(connus["decor"]))]
            choix["aleatoires"].append(("decor", choix["decors"][0]))
        if not choix["lumieres"] and type_choisi != "illustration":
            choix["lumieres"] = [hasard.choice(sorted(connus["lumiere"]))]
            choix["aleatoires"].append(("lumiere", choix["lumieres"][0]))
    return choix


def options_visuel_actives(choix: dict, couleur_marque: bool = False, varier: bool = False) -> bool:
    return bool(
        choix["type"] or choix["cadrages"] or choix["decors"] or choix["lumieres"] or choix["personnes"]
        or couleur_marque or varier
    )


def libelles_choix_aleatoires(choix: dict) -> list[str]:
    return [f"{LIBELLES_GROUPES_VISUEL[groupe]} : {_index_options(groupe)[i][0]}" for groupe, i in choix["aleatoires"]]


def prompt_image_avec_options(
    idee: str, texte_post: str, choix: dict, avec_reference: bool, recents: list[str] = None, couleur: str = "",
    couleurs_secondaires: list = None, texte_fr: bool = False,
) -> str:
    """
    Prompt d'image (anglais) qui applique les options cochees a l'idee de la scene (ou, sans idee,
    au texte du post). Sans "type" impose, on garde le style de base des generateurs (photo sur le
    vif au smartphone). `recents` : descriptions des dernieres images de ce client, a ne pas refaire.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (idee or "").strip() and not (texte_post or "").strip():
        raise RuntimeError("Aucun texte ni description fourni.")

    saison = saison_courante()
    lignes = []
    if choix["type"] == "illustration" and choix.get("illustration"):
        lignes.append(f"- Visual type: {_index_options('illustration')[choix['illustration']][1]}. It must look like a drawing, never a photograph.")
    elif choix["type"]:
        lignes.append(f"- Visual type: {_index_options('type')[choix['type']][1]}.")
    for groupe, cle in (("cadrage", "cadrages"), ("decor", "decors"), ("lumiere", "lumieres")):
        consignes = [_index_options(groupe)[i][1].replace("{saison}", saison) for i in choix[cle]]
        if consignes:
            libelle = {"cadrage": "Framing", "decor": "Setting", "lumiere": "Light and mood"}[groupe]
            lignes.append(f"- {libelle}: " + "; ".join(consignes) + ".")
    personnes = choix["personnes"]
    if personnes in REFERENCE_LIEU:
        if avec_reference:
            consigne_style = (
                " Render it fully in the chosen illustration style (not a photo pasted in) while keeping it clearly recognizable."
                if choix["type"] == "illustration" else ""
            )
            lignes.append(
                "- Place: this must be the exact building/place shown in the reference photos, never a different one. "
                "Reproduce its real architecture, materials, proportions, colors and surroundings faithfully. You may "
                "change the angle, the time of day, the season, the weather, or add people/staging/furnishings if the "
                f"scene calls for it, but the building itself must stay accurate and recognizable.{consigne_style}"
            )
        # Sans photo de reference disponible, rien a ajouter : l'IA improvise le lieu a partir du texte seul.
    elif avec_reference and personnes not in PERSONNES_SANS_REFERENCE:
        consigne = _index_options("personnes")[personnes][1] if personnes in PERSONNES_AVEC_REFERENCE else _index_options("personnes")["moi"][1]
        consigne_style = (
            " Draw them as a character fully rendered in the chosen illustration style (not a photo pasted in), "
            "while keeping their recognizable likeness (face shape, hair, skin tone)."
            if choix["type"] == "illustration" else ""
        )
        lignes.append(
            f"- People: {consigne}. Write \"the person from the reference photos\" and NEVER describe their physique "
            f"(face, hair, age, clothes): the face comes from the photos. Keep the face clearly visible.{consigne_style}"
        )
    elif personnes:
        lignes.append(f"- People: {_index_options('personnes')[personnes][1]}.")
    nom = nom_couleur(couleur)
    if couleur and nom:
        lignes.append(
            f"- Brand color: work the color {nom} ({couleur}) in naturally (an object, a garment, a decor accent), "
            "never as an overall tint or filter."
            + (
                " Secondary brand colors to use sparingly as small accents: "
                + ", ".join(f"{nom_couleur(c)} ({c})" for c in couleurs_secondaires if nom_couleur(c)) + "."
                if couleurs_secondaires else ""
            )
        )
    directives = "\n".join(lignes) or "- (no specific direction: use your judgment)"

    style_de_base = "" if choix["type"] else (
        "Unless a visual type is imposed above, render it as a candid smartphone snapshot (natural, slightly "
        "imperfect framing, uneven natural light, a bit of grain, lived-in place), not a stock photo, with no "
        "artificial background blur.\n"
    )
    bloc_recents = ""
    if recents:
        liste = "\n".join(f"- {r.strip()[:300]}" for r in recents)
        bloc_recents = (
            "Images already produced recently for this client. Do NOT repeat their setting, framing, subject "
            f"placement or props: propose something clearly different.\n{liste}\n\n"
        )

    prompt = (
        f"Post text:\n{(texte_post or '(not provided)').strip()[:2000]}\n\n"
        f"Initial idea for the visual (may be empty):\n{(idee or '').strip() or '(none)'}\n\n"
        f"{bloc_recents}"
        "Write an image-generation prompt, in English, illustrating this post and applying EXACTLY these art "
        f"directions:\n{directives}\n\n"
        f"{style_de_base}"
        "Aim for a concrete, specific scene directly tied to the post's subject (not an abstract metaphor). "
        + (
            "Text in the image: include at most 4 very short labels (1 to 3 common words each) written in FRENCH and "
            "relevant to the post's subject. In the prompt you write, give these labels between double quotes and state "
            "that they must be rendered EXACTLY as written, with correct French spelling and accents, and that no other "
            "text appears. No logo, no geographic reference. "
            if texte_fr else
            "No text anywhere (papers, screens, signs, clothes, labels, numbers), no logo, no geographic reference. "
        ) +
        "Avoid generic "
        "AI-illustration cliches (padlock/shield, abstract dashboard, light bulb, handshake, connected globe, gears) "
        "unless the subject truly requires them.\n"
        "Reply with the prompt only, in 3 to 5 sentences, without introduction or quotation marks."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=600, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), "").strip().strip('"')
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun prompt exploitable.")
    return _nettoyer_texte_genere(texte)


SCHEMA_CARROUSEL = {
    "type": "object",
    "properties": {
        "couverture": {
            "type": "object",
            "properties": {"titre": {"type": "string"}, "sous_titre": {"type": "string"}},
            "required": ["titre", "sous_titre"], "additionalProperties": False,
        },
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"titre": {"type": "string"}, "texte": {"type": "string"}},
                "required": ["titre", "texte"], "additionalProperties": False,
            },
        },
        "cta": {
            "type": "object",
            "properties": {"titre": {"type": "string"}, "texte": {"type": "string"}, "bouton": {"type": "string"}},
            "required": ["titre", "texte", "bouton"], "additionalProperties": False,
        },
    },
    "required": ["couverture", "points", "cta"], "additionalProperties": False,
}


def generer_carrousel(sujet: str, contexte: str = "", nb_points: int = 4) -> dict:
    """
    Textes d'un carrousel (couverture, N slides "point", slide finale avec bouton) a partir d'un sujet
    ou d'un texte de base ; le dessin est fait par carrousel_visuel.py. Textes courts : ils doivent
    tenir sur une image.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (sujet or "").strip():
        raise RuntimeError("Aucun sujet fourni.")
    nb_points = max(3, min(6, int(nb_points or 4)))

    bloc_contexte = f"\nContexte sur l'entreprise (site web, voix de l'auteur) :\n{contexte.strip()[:5000]}\n" if contexte.strip() else ""
    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')}.\n"
        "Redige le texte d'un carrousel pour les reseaux sociaux (Instagram, LinkedIn), en francais, pour "
        "l'entreprise decrite ci-dessous, a partir de ce sujet ou de ce texte de base :\n\n"
        f"\"{sujet.strip()}\"\n"
        f"{bloc_contexte}\n"
        f"Structure : une couverture, exactement {nb_points} slides de contenu, puis une slide finale.\n"
        "Consignes :\n"
        "- Couverture : titre accrocheur de 8 mots maximum (une promesse ou une question qui donne envie de "
        "faire glisser), sous-titre de 10 mots maximum.\n"
        "- Slides de contenu : un titre de 6 mots maximum et un texte de 25 mots maximum (1 ou 2 phrases). "
        "Une seule idee par slide, concrete et utile, dans un ordre logique.\n"
        "- Slide finale : un titre de 8 mots maximum qui invite a agir, un texte de 15 mots maximum, et un "
        "texte de bouton de 2 a 3 mots adapte a l'entreprise (ex. « Me contacter » pour un independant, "
        "« Nous contacter » pour une equipe, « Demander un devis »). Aucun numero de telephone, adresse ou lien.\n"
        "- N'invente aucun fait precis que le contexte ne contient pas (prix, chiffres, certifications, delais, "
        "avis). Reste general plutot que de risquer une affirmation fausse. Ton positif.\n"
        "- Si le contexte contient un bloc « VOIX DU CLIENT », ecris comme lui (registre, personne grammaticale).\n"
        "- Pas de tiret cadratin (—), pas d'emoji, pas de hashtag.\n"
        "- Chaque phrase apporte une information nouvelle : pas de redite ni de tournure tautologique."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=2000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_CARROUSEL}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    donnees = json.loads(bloc)

    def propre(texte):
        return _nettoyer_texte_genere(str(texte or "")).replace("—", "-").strip()

    return {
        "couverture": {k: propre(v) for k, v in donnees["couverture"].items()},
        "points": [{k: propre(v) for k, v in pt.items()} for pt in donnees["points"]][:nb_points],
        "cta": {k: propre(v) for k, v in donnees["cta"].items()},
    }


def generer_post_avis(avis: str, auteur: str, note: int, nom_client: str, contexte: str = "") -> str:
    """
    Court texte de post qui met en avant un avis client (remerciement + ce que l'avis illustre), sans
    inventer aucun fait et sans recopier l'avis en entier (il est deja sur le visuel).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (avis or "").strip():
        raise RuntimeError("Aucun avis fourni.")
    bloc_contexte = f"\nContexte sur l'entreprise (site web, voix de l'auteur) :\n{contexte.strip()[:4000]}\n" if (contexte or "").strip() else ""
    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')}.\n"
        f"Redige un court post pour les reseaux sociaux de l'entreprise « {nom_client} », en francais, qui met en "
        f"avant l'avis Google suivant ({note}/5, par {auteur or 'un client'}) :\n\n"
        f"\"{avis.strip()[:1500]}\"\n"
        f"{bloc_contexte}\n"
        "Consignes :\n"
        "- 300 a 600 caracteres, paragraphes courts separes par une ligne vide. La premiere ligne donne envie de lire "
        "la suite. Remercie chaleureusement la personne (par son prenom seulement, jamais son nom de famille) et "
        "dis ce que cet avis represente pour l'entreprise, sans exageration.\n"
        "- N'inclus pas l'avis en entier : il figure deja sur le visuel. Tu peux en reprendre un court passage entre guillemets.\n"
        "- N'invente aucun fait, chiffre, prix, delai ou detail d'intervention qui ne figure pas dans l'avis ou le contexte.\n"
        "- Personne grammaticale : si le contexte contient un bloc « VOIX DU CLIENT », suis la sienne. Sinon « nous » pour "
        "une entreprise, « je » pour un independant clairement identifie. Une seule personne dans tout le texte.\n"
        "- Termine par une courte invitation a contacter l'entreprise, sans numero de telephone ni lien.\n"
        "- Pas de tiret cadratin (—), pas de hashtag, au plus un emoji. Reponds uniquement avec le texte du post."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=800, thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    texte = next((b.text for b in reponse.content if b.type == "text"), "").strip().strip('"')
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    return _nettoyer_texte_genere(texte).replace("\u2014", "-")


def suggerer_titre_photo(texte_post: str, nom_client: str = "", contexte: str = "") -> dict:
    """Titre court et etiquette (sous-titre) pour habiller une photo, a partir du texte du post."""
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (texte_post or "").strip():
        raise RuntimeError("Ecrivez d'abord le texte de base.")
    bloc_contexte = f"\nContexte sur l'entreprise :\n{contexte.strip()[:2500]}\n" if (contexte or "").strip() else ""
    prompt = (
        f"Post de l'entreprise « {nom_client} » :\n« {texte_post.strip()[:1500]} »\n{bloc_contexte}\n"
        "Propose le texte a imprimer sur la photo qui illustre ce post, en francais :\n"
        "- titre : accrocheur, 3 a 7 mots, en une phrase qui donne envie de lire (une promesse, une question ou un conseil), "
        "sans point final ; il peut finir par deux-points si l'etiquette le complete (ex. « Bien reprendre apres l'ete : »).\n"
        "- sous_titre : etiquette de 1 a 3 mots qui complete le titre (ex. « Nos astuces », « Notre conseil », « A savoir »), "
        "ou une chaine vide si le titre se suffit.\n"
        "Aucun emoji, pas de tiret cadratin (—), aucun chiffre ou fait qui ne soit pas dans le post."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=300, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_TITRE_PHOTO}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    donnees = json.loads(bloc)
    return {"titre": _nettoyer_texte_genere(donnees["titre"]).replace("\u2014", "-"), "sous_titre": _nettoyer_texte_genere(donnees["sous_titre"]).replace("\u2014", "-")}


SCHEMA_TITRE_PHOTO = {
    "type": "object",
    "properties": {"titre": {"type": "string"}, "sous_titre": {"type": "string"}},
    "required": ["titre", "sous_titre"], "additionalProperties": False,
}


STYLE_SCRIPT_VIDEO = """
Le ton de Jonathan Hauët (Expert Produit Google, spécialiste en référencement local) dans ses scripts vidéo face caméra :
- Intro quasi systématique : il se présente ("Bonjour à tous, je suis Jonathan Hauët, Expert Produit Google" ou une variante
  courte comme "Salut à tous, c'est Jonathan") et invite tôt à s'abonner à sa chaîne pour les prochains conseils.
- Ton direct, pédagogue, oral : phrases courtes, "vous", quelques questions rhétoriques ("Vous l'avez compris ?",
  "Alors comment faire ?"), jamais de jargon non expliqué.
- Structure : une accroche qui pose le sujet et pourquoi il compte, puis le cœur du sujet en étapes numérotées ou en
  liste de points (raisons, erreurs, étapes à suivre...), souvent illustré par un exemple concret ou vécu ("Un
  professionnel que j'ai accompagné...", "Exemple réel :"), puis une conclusion qui résume le message.
- Conclusion quasi systématique : invite à s'abonner et/ou à commenter, puis une formule de clôture courte
  ("À très vite pour une nouvelle vidéo !", "À très bientôt !").
- Vocabulaire récurrent : "fiche d'établissement Google" / "fiche Google Business", "visibilité locale",
  "référencement local", "Pack Local". Pas d'anglicisme sauf les termes propres au métier (SEO, Google Ads...).
- Jamais de tiret cadratin (—) : un tiret simple ou une virgule à la place.
- Longueur : celle demandée dans la consigne de format (à défaut, environ 400 à 900 mots, dans l'esprit de ses scripts existants).
- Écrit avec les accents français corrects partout (é, è, à, ê, ô, ç...).
""".strip()

EXEMPLES_SCRIPT_VIDEO = [
    (
        "Salut tout le monde.\n"
        "Aujourd'hui une vidéo courte et explicative sur la mise en place des liens vers vos réseaux sociaux sur "
        "votre fiche d'établissement Google.\n"
        "Depuis peu, Google vous propose de mettre en place des liens sur votre fiche vers plusieurs types de "
        "plateforme : Facebook, Instagram, Twitter, YouTube, TikTok et Pinterest.\n"
        "Jusqu'à présent, Google se permettait de mettre en place ces liens automatiquement sur votre fiche s'il "
        "repérait grâce à son algorithme l'existence de réseaux sociaux pour votre entreprise.\n"
        "Aujourd'hui, vous avez la main, et je vous montre comment faire.\n"
        "Direction votre application Google Maps sur votre téléphone. Cliquez sur « Vos Fiches d'établissement » "
        "puis sur « éditer la fiche ».\n"
        "Dans l'onglet « Contact », ou en descendant quelque peu sur votre écran, vous allez voir cette nouvelle "
        "section « Profils sur les réseaux sociaux ». En cliquant sur Modifier, l'icône du crayon, vous allez "
        "pouvoir ajouter ou modifier les différents liens vers vos réseaux sociaux.\n"
        "Il vous suffit ensuite de cliquer sur Enregistrer pour voir apparaître en quelques heures ces nouveaux "
        "liens à disposition de vos clients et futurs clients sur votre fiche.\n"
        "Je ne vais pas vous faire un cours sur les réseaux sociaux mais simplement vous donner un conseil très "
        "simple. Votre présence sur les réseaux ne répond qu'à une seule logique : celle de communiquer avec vos "
        "clients ou potentiels clients. Ne vous challengez pas à être présent sur 5 réseaux sociaux différents si "
        "votre clientèle n'est majoritairement présente que sur une d'entre elle.\n"
        "Un conseil simple pour votre entreprise : mieux vaut travailler très bien sur un réseau social que "
        "moyennement sur trois différents.\n"
        "Placez le ou les bons liens, et profitez de cette nouvelle opportunité de trafic que vous propose votre "
        "fiche d'établissement Google Business. À très vite pour une nouvelle vidéo !"
    ),
    (
        "Bonjour à tous,\n"
        "Je suis Jonathan Hauët, Expert Produit Google et spécialiste du référencement local.\n"
        "Dans cette vidéo, je vais vous expliquer comment demander la propriété d'une fiche Google Business.\n"
        "Que vous ayez perdu vos identifiants, qu'un ancien salarié soit encore gestionnaire, ou que la fiche ait "
        "déjà été revendiquée par quelqu'un d'autre... la procédure est simple, à condition de bien suivre les "
        "étapes.\n"
        "Première étape : Recherchez votre établissement sur Google. Tapez le nom de votre entreprise dans la "
        "barre de recherche. Si une fiche existe, elle apparaîtra sur la droite de l'écran, ou directement dans "
        "les résultats de recherche. À ce moment-là, vous verrez un lien intitulé « Vous êtes le propriétaire de "
        "cet établissement ? ». Cliquez dessus. Puis, cliquez sur le bouton « Demander l'accès ».\n"
        "Deuxième étape : Remplissez le formulaire. Google va vous poser quelques questions. Votre nom, votre "
        "lien avec l'entreprise, et éventuellement un numéro de téléphone ou un justificatif. Prenez le temps de "
        "tout renseigner sérieusement.\n"
        "Troisième étape : Attendez la réponse du propriétaire actuel. Dès que vous avez envoyé votre demande, "
        "Google envoie un email à la personne actuellement en charge de la fiche. Cette personne a trois jours "
        "pour répondre. Si elle accepte votre demande, vous recevrez un email de confirmation et vous deviendrez "
        "officiellement gestionnaire de la fiche.\n"
        "Et si la demande est refusée ou ignorée ? Si vous ne recevez pas de réponse après trois jours, retournez "
        "dans l'email de confirmation que Google vous a envoyé, cliquez sur « Afficher la demande », et suivez les "
        "instructions à l'écran pour revendiquer et vérifier vous-même la propriété de la fiche.\n"
        "Voilà. Vous avez maintenant toutes les clés pour reprendre la main sur votre fiche Google Business. Une "
        "fiche bien gérée, c'est plus de visibilité, plus d'appels, et surtout, plus de clients.\n"
        "Merci pour votre attention, et à très bientôt dans une prochaine vidéo."
    ),
]


# Formats de script : longueur imposee (mots, a ~2,5 mots par seconde face camera) et consigne de structure.
FORMATS_SCRIPT_VIDEO = {
    "court": {
        "libelle": "court", "mots": (70, 120), "max_tokens": 1100, "duree": "30 à 45 secondes",
        "consigne": (
            "FORMAT COURT, percutant (Reel, Short, TikTok) : entre 70 et 120 mots, environ 30 à 45 secondes à l'oral. "
            "Une seule idée, une seule promesse. Commence DIRECTEMENT par une accroche qui arrête le pouce (un chiffre, une erreur "
            "courante, une question qui pique), sans « Bonjour à tous » ni présentation. Phrases très courtes, rythme rapide, "
            "aucun détail secondaire. Termine par une phrase de clôture et un appel à l'action en une phrase (commenter, s'abonner, "
            "ou prendre contact). Les deux exemples ci-dessus servent uniquement pour le ton : ne copie NI leur longueur NI leur structure."
        ),
    },
    "moyen": {
        "libelle": "moyen", "mots": (350, 550), "max_tokens": 2400, "duree": "environ 2 minutes 30 à 3 minutes 30",
        "consigne": (
            "FORMAT MOYEN : entre 350 et 550 mots, environ 2 minutes 30 à 3 minutes 30 à l'oral. Présentation courte, accroche, "
            "3 à 5 points ou étapes concrets, un exemple, conclusion avec appel à s'abonner."
        ),
    },
    "long": {
        "libelle": "long", "mots": (900, 1400), "max_tokens": 5000, "duree": "6 à 9 minutes",
        "consigne": (
            "FORMAT LONG : entre 900 et 1400 mots, environ 6 à 9 minutes à l'oral. Présentation, accroche qui annonce le plan, "
            "5 à 8 parties clairement séparées (chacune sur son paragraphe, avec une transition parlée), plusieurs exemples concrets "
            "ou vécus, un récapitulatif, puis la conclusion avec appel à s'abonner et à commenter."
        ),
    },
}
FORMAT_SCRIPT_DEFAUT = "moyen"


def generer_script_video_youtube(sujet: str = "", angle: str = "", sujets_deja_traites: list = None, format_script: str = FORMAT_SCRIPT_DEFAUT) -> dict:
    """
    Script à lire face caméra pour une vidéo YouTube, dans le ton de Jonathan Hauët (voir STYLE_SCRIPT_VIDEO et
    EXEMPLES_SCRIPT_VIDEO, deux de ses scripts réels servant de modèle de style). sujet : le thème de la vidéo,
    optionnel - si vide, l'IA choisit elle-même un sujet dans son domaine d'expertise (Google Business Profile,
    SEO local), comme pour generer_post_expert. angle : précision facultative (ex. un angle particulier, un
    public cible, une longueur souhaitée). Renvoie {"script", "sujet_utilise"} - sujet_utilise n'est rempli que
    lorsque le sujet a été choisi par l'IA (vide sinon), pour l'afficher à l'utilisateur. sujets_deja_traites :
    sujets des vidéos précédentes (voir models.ScriptVideo), pour ne pas reproposer le même sujet ni un angle trop proche.
    format_script : « court », « moyen » ou « long » (voir FORMATS_SCRIPT_VIDEO) ; la longueur est imposee et verifiee (une seule reecriture
    si le nombre de mots est loin de la cible). Renvoie aussi "mots" et "duree_estimee" (secondes de lecture).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    sujet = (sujet or "").strip()
    angle = (angle or "").strip()
    format_choisi = FORMATS_SCRIPT_VIDEO.get(format_script) or FORMATS_SCRIPT_VIDEO[FORMAT_SCRIPT_DEFAUT]
    mots_mini, mots_maxi = format_choisi["mots"]

    bloc_angle = f"\nPrécision supplémentaire à respecter : {angle[:500]}\n" if angle else ""
    exemples = "\n\n---\n\n".join(EXEMPLES_SCRIPT_VIDEO)

    if sujet:
        consigne_sujet = f"sur le sujet suivant : « {sujet[:500]} »"
        consigne_format = "Réponds uniquement par le script, prêt à être lu, sans titre ni note avant ou après."
    else:
        consigne_sujet = (
            "sur un sujet que tu choisis toi-même, dans l'esprit de l'expertise de Jonathan (fiche d'établissement "
            "Google, Google Business Profile, SEO local, avis clients, visibilité locale) - un sujet concret et "
            "utile, différent des deux exemples ci-dessus"
        )
        consigne_format = (
            "Réponds en commençant par une première ligne exactement sous la forme \"SUJET : <le sujet choisi, "
            "6 à 12 mots>\", puis une ligne vide, puis le script, prêt à être lu, sans autre titre ni note."
        )

    bloc_deja = ""
    if sujets_deja_traites:
        liste = "\n".join(f"- {t[:120]}" for t in sujets_deja_traites[:40])
        precision = " (même si le sujet demandé y ressemble : trouve un angle nouveau)" if sujet else ""
        bloc_deja = (
            "\nVidéos que Jonathan a déjà faites ou préparées (ne reprends pas un de ces sujets, ni un angle trop "
            f"proche){precision} :\n{liste}\n"
        )
    prompt = (
        f"{STYLE_SCRIPT_VIDEO}\n\n"
        f"Voici deux exemples réels de scripts déjà écrits par Jonathan, à prendre comme modèle de ton et de "
        f"structure (pas de contenu, le sujet du jour est différent) :\n\n{exemples}\n\n---\n\n"
        f"Écris maintenant, dans ce même ton, un nouveau script vidéo face caméra {consigne_sujet}.{bloc_angle}\n"
        f"{format_choisi['consigne']} Cette longueur prime sur toute autre indication.\n"
        f"{bloc_deja}{consigne_format}"
    )
    client = Anthropic(api_key=CLE_API)
    messages = [{"role": "user", "content": prompt}]

    def ecrire():
        reponse = client.messages.create(model=MODELE_CLAUDE, max_tokens=format_choisi["max_tokens"], thinking={"type": "disabled"}, messages=messages)
        return next((b.text for b in reponse.content if b.type == "text"), "").strip()

    def compter(brut):
        lignes = brut.splitlines()
        if lignes and re.match(r"^SUJET\s*:", lignes[0].strip(), re.IGNORECASE):
            brut = "\n".join(lignes[1:])
        return len(brut.split())

    texte = ecrire()
    if not texte:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    nombre = compter(texte)
    if nombre > mots_maxi * 1.2 or nombre < mots_mini * 0.8:       # trop loin de la cible : une seule reecriture, en gardant le meilleur des deux
        messages += [{"role": "assistant", "content": texte}, {"role": "user", "content": (
            f"Ce script fait {nombre} mots : il doit en faire entre {mots_mini} et {mots_maxi} ({format_choisi['duree']} à l'oral). "
            f"Réécris-le à la bonne longueur, même ton, même format de réponse, sans commentaire.")}]
        nouveau = ecrire()
        if nouveau and abs(compter(nouveau) - (mots_mini + mots_maxi) / 2) < abs(nombre - (mots_mini + mots_maxi) / 2):
            texte = nouveau

    sujet_utilise = ""
    if not sujet:
        correspondance = re.match(r"^SUJET\s*:\s*(.+)$", texte.splitlines()[0].strip(), re.IGNORECASE)
        if correspondance:
            sujet_utilise = correspondance.group(1).strip()
            texte = texte[len(texte.splitlines()[0]):].lstrip("\n")

    script = _nettoyer_texte_genere(texte.strip())
    nombre_mots = len(script.split())
    return {"script": script, "sujet_utilise": sujet_utilise, "mots": nombre_mots, "duree_estimee": int(round(nombre_mots / MOTS_PAR_SECONDE_SCRIPT)),
            "format": next(cle for cle, valeur in FORMATS_SCRIPT_VIDEO.items() if valeur is format_choisi)}


SCHEMA_KIT_VIDEO = {
    "type": "object",
    "properties": {
        "titres": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "chapitres": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"titre": {"type": "string"}, "paragraphe": {"type": "integer"}},
                "required": ["titre", "paragraphe"], "additionalProperties": False,
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "script_short": {"type": "string"},
        "post_linkedin": {"type": "string"},
        "post_google": {"type": "string"},
        "miniatures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"texte": {"type": "string"}, "scene": {"type": "string"}},
                "required": ["texte", "scene"], "additionalProperties": False,
            },
        },
    },
    "required": ["titres", "description", "chapitres", "tags", "script_short", "post_linkedin", "post_google", "miniatures"],
    "additionalProperties": False,
}

MOTS_PAR_SECONDE_SCRIPT = 2.5  # ~150 mots/minute, rythme de lecture face camera


def _horodatage(secondes: int) -> str:
    return f"{secondes // 60}:{secondes % 60:02d}"


def formater_chapitres(paragraphes: list, chapitres: list) -> str:
    """
    Chapitres YouTube (« 0:00 Titre ») a partir des paragraphes du script et du paragraphe ou commence chaque
    chapitre : minutage estime d'apres le nombre de mots lus avant (voir MOTS_PAR_SECONDE_SCRIPT). YouTube exige
    un premier chapitre a 0:00, au moins 3 chapitres et 10 secondes minimum entre deux - sinon on n'en renvoie aucun.
    """
    mots_avant, cumul = [], 0
    for paragraphe in paragraphes:
        mots_avant.append(cumul)
        cumul += len(paragraphe.split())
    lignes, dernier = [], -100
    for chapitre in sorted(chapitres, key=lambda c: c["paragraphe"]):
        indice = max(0, min(int(chapitre["paragraphe"]) - 1, len(paragraphes) - 1))
        secondes = 0 if not lignes else int(mots_avant[indice] / MOTS_PAR_SECONDE_SCRIPT)
        if lignes and secondes - dernier < 10:
            continue
        lignes.append(f"{_horodatage(secondes)} {_nettoyer_texte_genere(chapitre['titre'])}")
        dernier = secondes
    return "\n".join(lignes) if len(lignes) >= 3 else ""


def _consigne_scene_miniature(avec_personne: bool) -> str:
    return (
        "the person from the reference photos, never described physically (the face comes from the photos), placed in "
        "the right third of the frame, large, face fully visible, with an expressive reaction that matches the topic "
        "(surprised, pointing, thinking, thumbs up...)"
        if avec_personne else "a striking symbolic object or scene related to the topic, placed in the right third"
    )


def generer_kit_publication_video(script: str, avec_personne: bool = True) -> dict:
    """
    Declinaisons d'un script video face camera, dans le ton de Jonathan : 3 titres YouTube, description (avec
    chapitres minutes), tags, version courte pour les Shorts, posts LinkedIn et Google, et 2 concepts de
    miniature (texte court + scene en anglais pour Gemini). avec_personne : la scene met en avant la personne des
    photos de reference (jamais decrite physiquement : le visage vient des photos).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    paragraphes = [p.strip() for p in (script or "").splitlines() if p.strip()]
    if not paragraphes:
        raise RuntimeError("Aucun script fourni.")

    script_numerote = "\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphes, start=1))[:9000]
    consigne_scene = _consigne_scene_miniature(avec_personne)
    prompt = (
        f"{STYLE_SCRIPT_VIDEO}\n\n"
        "Voici le script d'une vidéo YouTube de Jonathan (paragraphes numérotés entre crochets) :\n\n"
        f"{script_numerote}\n\n"
        "Prépare son kit de publication, en français, avec les champs suivants :\n"
        "- titres : 3 titres YouTube différents (60 caractères maximum chacun), accrocheurs et honnêtes, avec le mot-clé principal "
        "tôt dans le titre.\n"
        "- description : 700 à 1100 caractères, première phrase très accrocheuse (elle s'affiche avant « Afficher plus »), "
        "puis ce que la vidéo apporte, puis une invitation à s'abonner. Sans chapitres (ajoutés à part) ni hashtags.\n"
        "- chapitres : 4 à 7 chapitres dans l'ordre ; pour chacun, un titre court et le numéro du paragraphe où il commence "
        "(le premier chapitre commence au paragraphe 1).\n"
        "- tags : 8 à 12 mots-clés de recherche YouTube.\n"
        "- script_short : version à lire pour un YouTube Short, 90 à 120 mots, première phrase qui accroche, un seul conseil "
        "clé, termine par une invitation à s'abonner.\n"
        "- post_linkedin : post LinkedIn à la première personne, 900 à 1300 caractères, première ligne accroche, paragraphes "
        "courts, une question finale, 3 hashtags maximum.\n"
        "- post_google : post Google Business Profile de 600 à 900 caractères, conseil concret tiré de la vidéo, sans hashtags.\n"
        f"- miniatures : exactement 2 concepts de miniature différents. Pour chacun, texte : 2 à 4 mots en français qui "
        f"donnent envie de cliquer (sans ponctuation compliquée), et scene : une description en ANGLAIS de la scène à "
        f"générer, sans aucun texte dans l'image, avec {consigne_scene}, laissant la moitié gauche de l'image dégagée et "
        "contrastée pour y superposer le texte, couleurs saturées et lisibles en petit format.\n"
        "Aucun emoji ni tiret cadratin, aucun chiffre ou fait absent du script."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=5000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_KIT_VIDEO}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        kit = json.loads(bloc)
    except json.JSONDecodeError as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur

    chapitres = formater_chapitres(paragraphes, kit["chapitres"])
    description = _nettoyer_texte_genere(kit["description"])
    if chapitres:
        description += "\n\n" + chapitres
    return {
        "titres": [_nettoyer_texte_genere(t) for t in kit["titres"]][:3],
        "description": description,
        "tags": [_nettoyer_texte_genere(t) for t in kit["tags"]][:15],
        "script_short": _nettoyer_texte_genere(kit["script_short"]),
        "post_linkedin": _nettoyer_texte_genere(kit["post_linkedin"]),
        "post_google": _nettoyer_texte_genere(kit["post_google"]),
        "miniatures": [
            {"texte": _nettoyer_texte_genere(m["texte"]), "scene": m["scene"].strip()} for m in kit["miniatures"]
        ][:2],
    }


SCHEMA_CONCEPTS_MINIATURES = {
    "type": "object",
    "properties": {
        "miniatures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"texte": {"type": "string"}, "scene": {"type": "string"}},
                "required": ["texte", "scene"], "additionalProperties": False,
            },
        },
    },
    "required": ["miniatures"], "additionalProperties": False,
}


def generer_concepts_miniatures(script: str, deja_proposes: list = None, avec_personne: bool = True) -> list:
    """
    2 NOUVEAUX concepts de miniature pour ce script (texte court en francais + scene en anglais pour Gemini),
    differents de ceux deja proposes (deja_proposes : [{"texte", "scene"}, ...]) - pour « regenerer » quand les
    premieres propositions ne plaisent pas : autre accroche, autre composition, autre expression.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (script or "").strip():
        raise RuntimeError("Aucun script fourni.")
    deja = ""
    if deja_proposes:
        liste = "\n".join(f"- « {m.get('texte', '')} » : {str(m.get('scene', ''))[:220]}" for m in deja_proposes[-8:])
        deja = (
            "\nMiniatures déjà proposées et refusées (propose des accroches, des compositions, des décors, des couleurs "
            f"et des expressions CLAIREMENT différents) :\n{liste}\n"
        )
    prompt = (
        f"{STYLE_SCRIPT_VIDEO}\n\nVoici le script d'une vidéo YouTube de Jonathan :\n\n{script.strip()[:7000]}\n{deja}\n"
        "Propose exactement 2 nouveaux concepts de miniature YouTube différents l'un de l'autre. Pour chacun, texte : 2 à "
        "4 mots en français qui donnent envie de cliquer (sans ponctuation compliquée), et scene : une description en "
        f"ANGLAIS de la scène à générer, sans aucun texte dans l'image, avec {_consigne_scene_miniature(avec_personne)}, "
        "laissant la moitié gauche de l'image dégagée et contrastée pour y superposer le texte, couleurs saturées et "
        "lisibles en petit format. Aucun emoji ni tiret cadratin."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=1500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_CONCEPTS_MINIATURES}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        concepts = json.loads(bloc)["miniatures"]
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    return [{"texte": _nettoyer_texte_genere(m["texte"]), "scene": m["scene"].strip()} for m in concepts][:2]


SCHEMA_REACTIONS = {
    "type": "object",
    "properties": {"reactions": {"type": "array", "items": {"type": "string"}}},
    "required": ["reactions"], "additionalProperties": False,
}


def generer_reactions_carrousel(textes: list) -> list:
    """
    Une reaction (expression + geste, en anglais, pour Gemini) par slide d'un carrousel : la personne des photos de
    reference reagit a ce que dit la slide, avec une expression differente d'une slide a l'autre. Jamais de description
    physique (le visage vient des photos). Renvoie autant d'elements que de textes.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not textes:
        raise RuntimeError("Aucune slide.")
    liste = "\n".join(f"{i}. {t or '(slide sans texte)'}" for i, t in enumerate(textes, start=1))
    prompt = (
        "Voici les textes des slides d'un carrousel :\n\n" + liste + "\n\n"
        f"Pour chacune des {len(textes)} slides, dans l'ordre, décris en ANGLAIS, en une phrase courte, la réaction d'une "
        "personne qui commente ce que dit la slide : expression du visage et geste des épaules/mains (surprised with raised "
        "eyebrows, nodding with a confident smile, thumbs up, pointing at the viewer, thoughtful hand on chin, index finger "
        "raised, impressed, laughing...). Varie nettement l'expression d'une slide à l'autre, en cohérence avec le sens de "
        "chaque slide (une mise en garde donne un air sérieux ou inquiet, un conseil un sourire ou un pouce levé, la slide "
        "finale un sourire engageant). Ne décris JAMAIS le physique (visage, cheveux, âge, vêtements). "
        f"Réponds par exactement {len(textes)} réactions."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=1200, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REACTIONS}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        reactions = [str(r).strip() for r in json.loads(bloc)["reactions"]]
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    defaut = "a friendly confident smile looking at the viewer"
    return [(reactions[i] if i < len(reactions) and reactions[i] else defaut) for i in range(len(textes))]


# --------------------------------------------------------------------------- montage video (voir montage_parole.py)

def _transcription_pour_prompt(phrases: list, limite: int = 60000) -> str:
    return "\n".join(f"[{p['debut']:.1f}-{p['fin']:.1f}] {p['texte']}" for p in phrases)[:limite]


SCHEMA_RETOUCHES = {
    "type": "object",
    "properties": {
        "retirer": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"debut": {"type": "number"}, "fin": {"type": "number"}, "raison": {"type": "string"}},
                "required": ["debut", "fin", "raison"], "additionalProperties": False,
            },
        },
    },
    "required": ["retirer"], "additionalProperties": False,
}


def retouches_depuis_consignes(phrases: list, consignes: str) -> list:
    """
    Parties de la video a RETIRER pour respecter des consignes de montage en langage naturel (« coupe la partie ou je parle
    du prix », « enleve l'introduction »...), a partir de la transcription horodatee. Renvoie [(debut, fin), ...] en secondes,
    calees sur des debuts/fins de phrases. Ne retire rien qui n'est pas demande.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not phrases or not (consignes or "").strip():
        return []
    prompt = (
        "Voici la transcription horodatée (en secondes) d'une vidéo face caméra :\n\n"
        f"{_transcription_pour_prompt(phrases)}\n\n"
        f"Consignes de montage de Jonathan : « {consignes.strip()[:1000]} »\n\n"
        "Donne les intervalles à RETIRER du montage pour respecter ces consignes, et uniquement ce qu'elles demandent "
        "(si elles ne demandent aucune coupe, par exemple une simple remarque de style, renvoie une liste vide). "
        "Chaque intervalle commence au début d'une phrase et se termine à la fin d'une phrase de la transcription, "
        "sans couper une phrase en deux. Précise brièvement la raison."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=2000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_RETOUCHES}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        retirer = json.loads(bloc)["retirer"]
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    resultat = []
    for element in retirer:
        debut, fin = float(element["debut"]), float(element["fin"])
        if fin > debut:
            resultat.append((max(0.0, debut - 0.05), fin + 0.05))
    return resultat


SCHEMA_EXTRAITS = {
    "type": "object",
    "properties": {
        "extraits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"debut": {"type": "number"}, "fin": {"type": "number"}, "titre": {"type": "string"}},
                "required": ["debut", "fin", "titre"], "additionalProperties": False,
            },
        },
    },
    "required": ["extraits"], "additionalProperties": False,
}


def choisir_extraits_shorts(phrases: list, nombre: int, duree_min: float = 25.0, duree_max: float = 90.0) -> list:
    """
    Les `nombre` meilleurs moments d'une longue video pour en faire des Shorts verticaux : autonomes (compris sans le
    reste), accroche forte des les premieres secondes, sans chevauchement. Renvoie [{"debut", "fin", "titre"}, ...]
    classes dans l'ordre de la video, bornes recalees sur des phrases entieres et duree comprise entre duree_min et duree_max.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not phrases:
        return []
    prompt = (
        f"Voici la transcription horodatée (en secondes) d'une vidéo face caméra de Jonathan, expert Google Business :\n\n"
        f"{_transcription_pour_prompt(phrases)}\n\n"
        f"Choisis les {nombre} meilleurs moments pour en faire des Shorts verticaux. Chaque moment : autonome (compréhensible "
        f"sans le reste de la vidéo), une idée complète, une accroche forte dès la première phrase, durée entre "
        f"{int(duree_min)} et {int(duree_max)} secondes (silences compris), commence au début d'une phrase et se termine à "
        "la fin d'une phrase. Les moments ne se chevauchent pas. Pour chacun, donne un titre court et accrocheur en français "
        "(8 mots maximum, sans emoji ni tiret cadratin). Si la vidéo est trop courte pour autant de moments, en proposer moins."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=4000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_EXTRAITS}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)["extraits"]
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur

    # Bornes recalees sur la phrase la plus proche (l'IA se trompe parfois d'une seconde) et duree verifiee.
    extraits = []
    for element in brut:
        debut = min(phrases, key=lambda p: abs(p["debut"] - float(element["debut"])))["debut"]
        fin = min(phrases, key=lambda p: abs(p["fin"] - float(element["fin"])))["fin"]
        if fin - debut > duree_max:
            candidates = [p["fin"] for p in phrases if debut + duree_min <= p["fin"] <= debut + duree_max]
            fin = max(candidates) if candidates else debut + duree_max
        if fin - debut < duree_min * 0.6:
            continue
        extraits.append({"debut": debut, "fin": fin, "titre": _nettoyer_texte_genere(element["titre"])})
    extraits.sort(key=lambda e: e["debut"])
    sans_chevauchement = []
    for e in extraits:
        if not sans_chevauchement or e["debut"] >= sans_chevauchement[-1]["fin"]:
            sans_chevauchement.append(e)
    return sans_chevauchement[:nombre]


REGLES_MONTAGE: list = []          # preferences de montage de l'utilisateur (voir regles_montage.py), mises a jour avant chaque appel


def definir_regles_montage(regles: list) -> None:
    REGLES_MONTAGE[:] = [str(r)[:240] for r in (regles or [])][:30]


def _bloc_regles() -> str:
    if not REGLES_MONTAGE:
        return ""
    return "\n\nPréférences de Jonathan pour ses montages, à respecter chaque fois que c'est possible :\n" + "\n".join(f"- {r}" for r in REGLES_MONTAGE)


SCHEMA_TENDANCE = {
    "type": "object",
    "properties": {"accroche": {"type": "string"}, "mots_cles": {"type": "array", "items": {"type": "string"}}},
    "required": ["accroche", "mots_cles"], "additionalProperties": False,
}


def tendance_depuis_transcription(phrases: list) -> dict:
    """
    Pour le style « tendance » d'un montage : une accroche courte affichee pendant les 3 premieres secondes et les mots-cles a faire
    ressortir dans les sous-titres (et sur lesquels placer des zooms). Tire uniquement de ce que la personne dit, rien d'invente.
    Renvoie {"accroche": str, "mots_cles": [str, ...]}.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not phrases:
        return {"accroche": "", "mots_cles": []}
    prompt = (
        "Voici la transcription horodatée (en secondes) d'une vidéo courte face caméra :\n\n"
        f"{_transcription_pour_prompt(phrases)}\n\n"
        "1) accroche : une phrase d'accroche de 3 à 8 mots, en français, qui donne envie de regarder la vidéo jusqu'au bout "
        "(question, promesse ou chiffre fort), fidèle à ce qui est dit dans la vidéo, sans rien inventer, sans emoji, sans guillemets, "
        "sans point final.\n"
        "2) mots_cles : 6 à 12 mots importants à faire ressortir à l'écran (chiffres, noms, verbes forts, termes clés), écrits "
        "exactement comme dans la transcription, un seul mot chacun, pas de mots vides (le, de, et, que...)."
    )
    prompt += _bloc_regles()
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=800, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_TENDANCE}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
        accroche, mots_cles = _nettoyer_texte_genere(str(brut["accroche"])).strip(" .\"«»"), brut["mots_cles"]
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    return {"accroche": accroche, "mots_cles": [m.strip() for m in mots_cles if isinstance(m, str) and m.strip() and " " not in m.strip()][:14]}


SCHEMA_ALIGNEMENT_SCRIPT = {
    "type": "object",
    "properties": {
        "phrases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"indice": {"type": "integer"}, "texte": {"type": "string"}, "retirer": {"type": "boolean"}},
                "required": ["indice", "texte", "retirer"], "additionalProperties": False,
            },
        },
    },
    "required": ["phrases"], "additionalProperties": False,
}
TAILLE_LOT_ALIGNEMENT = 80
PART_MAX_RETIREE = 0.6


def aligner_sur_script(phrases: list, script: str) -> dict:
    """
    Compare la transcription d'une video lue face camera au script prepare : corrige l'orthographe des mots mal entendus (noms, termes
    techniques, accents) en reprenant celle du script, et repere les phrases a retirer (prises ratees deja reprises ensuite, faux departs,
    remarques hors script comme « on la refait »). Ne rajoute jamais un mot qui n'a pas ete prononce. Renvoie
    {"textes": [str par phrase], "retirer": [indices], "avertissement": str}.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    textes = [p["texte"] for p in phrases]
    retirer = set()
    client = Anthropic(api_key=CLE_API)
    for debut in range(0, len(phrases), TAILLE_LOT_ALIGNEMENT):
        lot = list(range(debut, min(len(phrases), debut + TAILLE_LOT_ALIGNEMENT)))
        transcription = "\n".join(f"[{i}] {phrases[i]['texte']}" for i in lot)
        prompt = (
            f"Script prévu (la personne le lit face caméra) :\n\n{script.strip()[:12000]}\n\n"
            f"Transcription automatique de sa prise vidéo, phrase par phrase (extrait {lot[0]} à {lot[-1]}) :\n\n{transcription}\n\n"
            "Pour chaque phrase de la transcription, renvoie : indice (le numéro entre crochets), texte (ce qui a été prononcé, avec l'orthographe "
            "exacte du script pour les mots mal transcrits : noms propres, termes techniques, accents ; ne change pas le sens, ne rajoute jamais "
            "un mot qui n'a pas été prononcé, ne complète pas avec le script), retirer (true si la phrase est une prise ratée à supprimer : "
            "début de phrase abandonné, phrase répétée ensuite en meilleure version, remarque hors script comme « on la refait », « attends », "
            "« je recommence » ; false pour toute phrase utile, y compris si elle s'écarte légèrement du script). Quand une phrase du script a "
            "été dite plusieurs fois, garde la dernière prise complète et retire les précédentes. Dans le doute, ne retire pas."
        )
        prompt += _bloc_regles()
        reponse = client.messages.create(
            model=MODELE_CLAUDE, max_tokens=8000, thinking={"type": "disabled"},
            output_config={"format": {"type": "json_schema", "schema": SCHEMA_ALIGNEMENT_SCRIPT}},
            messages=[{"role": "user", "content": prompt}],
        )
        bloc = next((b.text for b in reponse.content if b.type == "text"), None)
        if not bloc:
            raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
        try:
            brut = json.loads(bloc)["phrases"]
        except (json.JSONDecodeError, KeyError, TypeError) as erreur:
            raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
        for element in brut:
            i = element.get("indice")
            if not isinstance(i, int) or i not in lot:
                continue
            nouveau = " ".join(str(element.get("texte") or "").split())
            ancien = textes[i]
            # Garde-fou : un texte qui s'allonge beaucoup serait du script recopie, pas ce qui a ete dit.
            if nouveau and len(nouveau) <= len(ancien) * 1.5 + 12:
                textes[i] = nouveau
            if element.get("retirer") is True:
                retirer.add(i)
    avertissement = ""
    if phrases and len(retirer) > PART_MAX_RETIREE * len(phrases):
        retirer, avertissement = set(), "L'IA voulait retirer trop de phrases : aucune coupe n'a été faite d'après le script."
    return {"textes": textes, "retirer": sorted(retirer), "avertissement": avertissement}


ACTIONS_REVISION = ("couper", "zoom", "sans_zoom", "titre", "mot_en_gros", "corriger_texte")
SCHEMA_REVISION = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ACTIONS_REVISION)},
                    "debut": {"type": "number"}, "fin": {"type": "number"},
                    "texte": {"type": "string"}, "ancien": {"type": "string"}, "force": {"type": "string", "enum": ["leger", "fort", "aucune"]},
                },
                "required": ["type", "debut", "fin", "texte", "ancien", "force"], "additionalProperties": False,
            },
        },
        "ignorees": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"note": {"type": "string"}, "raison": {"type": "string"}},
                "required": ["note", "raison"], "additionalProperties": False,
            },
        },
    },
    "required": ["actions", "ignorees"], "additionalProperties": False,
}


def actions_depuis_annotations(notes: list, phrases: list, duree: float) -> tuple:
    """
    Traduit les notes de relecture d'une video montee (« à 0:12, zoom ici », « enlève cette phrase », « mets le mot prix en gros »,
    « le sous-titre dit Googel ») en actions precises sur le montage. notes : [{"t": secondes, "texte": str}]. Les temps sont ceux du
    montage actuel. Renvoie (actions, ignorees) : actions = [{"type", "debut", "fin", "texte", "ancien", "force"}], ignorees = [str]
    (notes qui ne correspondent a aucune action possible, avec la raison).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    liste = "\n".join(f"- à {n['t']:.1f} s : {n['texte']}" for n in notes)
    prompt = (
        f"Voici la transcription horodatée (en secondes, sur la vidéo montée de {duree:.1f} s) :\n\n"
        f"{_transcription_pour_prompt(phrases)}\n\n"
        f"Notes de relecture de Jonathan, avec l'instant de la vidéo où il les a écrites :\n{liste}\n\n"
        "Traduis chaque note en actions, parmi uniquement :\n"
        "- couper : retirer un passage (debut et fin sur des débuts et fins de phrases proches de l'instant de la note).\n"
        "- zoom : ajouter un zoom sur un moment (debut, fin ; force leger ou fort ; 'fort' si la note demande du punch).\n"
        "- sans_zoom : retirer le zoom automatique autour d'un moment.\n"
        "- titre : afficher un texte à l'écran (texte, debut ; fin = debut + 3 s sauf indication).\n"
        "- mot_en_gros : mettre un ou plusieurs mots en valeur dans les sous-titres (texte = les mots, tels que dits).\n"
        "- corriger_texte : corriger un mot ou une expression mal transcrite dans les sous-titres (ancien = le texte erroné tel qu'écrit "
        "dans la transcription, texte = la version correcte, debut = l'instant approximatif).\n"
        "Pour chaque action, mets 0 dans debut et fin si inutilisés, '' dans texte et ancien si inutilisés, et force = 'aucune' "
        "sauf pour zoom. Une note qui ne correspond à aucune de ces actions (musique, style général, couleur...) va dans 'ignorees' avec "
        "une raison courte. N'invente aucune action qui n'est pas demandée."
    )
    prompt += _bloc_regles()
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=2500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REVISION}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
        actions = [a for a in brut["actions"] if a.get("type") in ACTIONS_REVISION]
        ignorees = [f"« {i['note'][:80]} » : {i['raison'][:120]}" for i in brut["ignorees"]]
    except (json.JSONDecodeError, KeyError, TypeError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    return actions, ignorees


ACTIONS_EDITEUR = ("couper", "restaurer", "zoom", "retirer_zoom", "titre", "mot_en_gros", "accroche", "illustration", "alterne")
SCHEMA_COMMANDE_EDITEUR = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ACTIONS_EDITEUR)},
                    "debut": {"type": "number"}, "fin": {"type": "number"}, "texte": {"type": "string"},
                    "force": {"type": "string", "enum": ["leger", "fort", "aucune"]},
                    "mode": {"type": "string", "enum": ["incrust", "plein", "aucun"]},
                    "prompt": {"type": "string"}, "valeur": {"type": "boolean"},
                },
                "required": ["type", "debut", "fin", "texte", "force", "mode", "prompt", "valeur"], "additionalProperties": False,
            },
        },
        "reponse": {"type": "string"},
    },
    "required": ["actions", "reponse"], "additionalProperties": False,
}


def commande_editeur(commande: str, phrases: list, segments: list, duree: float) -> dict:
    """
    Traduit une commande en langage naturel donnee dans l'editeur de montage (« retire les repetitions », « mets un zoom sur chaque
    chiffre », « illustre les idees principales », « propose une meilleure accroche ») en actions. Les temps sont ceux de la video
    d'origine, comme dans la transcription fournie (les phrases deja coupees y sont signalees). Renvoie
    {"actions": [{"type", "debut", "fin", "texte", "force", "mode", "prompt", "valeur"}], "reponse": str}.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    gardees = [(a, b) for a, b in segments]

    def coupee(p):
        milieu = (p["debut"] + p["fin"]) / 2
        return not any(a <= milieu <= b for a, b in gardees)

    transcription = "\n".join(f"[{p['debut']:.1f}-{p['fin']:.1f}]{' (COUPÉ)' if coupee(p) else ''} {p['texte']}" for p in phrases)[:60000]
    prompt = (
        f"Voici la transcription horodatée (secondes de la vidéo d'origine) d'une vidéo face caméra en cours de montage ; les phrases "
        f"déjà coupées sont marquées (COUPÉ). Durée de la vidéo d'origine : {duree:.1f} s.\n\n{transcription}\n\n"
        f"Commande de Jonathan : « {commande.strip()[:500]} »\n\n"
        "Traduis cette commande en actions (12 au maximum), parmi uniquement :\n"
        "- couper : retirer un passage (debut, fin en secondes d'origine, sur des débuts et fins de phrases).\n"
        "- restaurer : remettre un passage coupé.\n"
        "- zoom : zoom sur un moment (debut, fin d'environ 2 à 3 s ; force leger ou fort).\n"
        "- retirer_zoom : retirer le zoom automatique autour de debut.\n"
        "- titre : texte affiché à l'écran (texte court, debut, fin = debut + 3 s).\n"
        "- mot_en_gros : mots à mettre en valeur dans les sous-titres (texte = les mots, tels que dits).\n"
        "- accroche : remplacer le titre d'accroche du début (texte de 3 à 8 mots, fidèle à la vidéo).\n"
        "- illustration : une image à afficher pendant un moment concret (debut, fin = debut + 3 s environ, mode incrust ou plein ; "
        "prompt = une description en anglais d'une scène ou d'un objet précis et réaliste lié à ce qui est dit, sans texte écrit ni "
        "logo ; 4 illustrations au maximum).\n"
        "- alterne : activer (valeur true) ou désactiver (false) le cadrage alterné à chaque coupe.\n"
        "Pour chaque action, mets 0 dans debut et fin si inutilisés, '' dans texte et prompt si inutilisés, force = 'aucune' sauf "
        "pour zoom, mode = 'aucun' sauf pour illustration, valeur = false sauf pour alterne. N'invente aucune action qui n'est pas "
        "demandée. Dans 'reponse', écris en 1 à 3 phrases en français ce que tu as fait (ou, si la commande demande des propositions, "
        "donne-les)."
    )
    prompt += _bloc_regles()
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=3000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_COMMANDE_EDITEUR}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
        actions = [a for a in brut["actions"] if a.get("type") in ACTIONS_EDITEUR][:12]
        return {"actions": actions, "reponse": _nettoyer_texte_genere(str(brut["reponse"]))}
    except (json.JSONDecodeError, KeyError, TypeError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur


SCHEMA_KIT_MONTAGE = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"}, "texte": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}}, "accroches": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titre", "texte", "hashtags", "accroches"], "additionalProperties": False,
}


def kit_publication_montage(phrases: list, accroche: str = "") -> dict:
    """
    Texte de publication d'une video montee (Reel, Short, TikTok, post de reseau) a partir de ce qui y est dit : titre court, legende,
    hashtags et trois accroches de rechange. Rien d'invente. Renvoie {"titre", "texte", "hashtags", "accroches"}.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    transcription = " ".join(p["texte"] for p in phrases)[:7000]
    if not transcription.strip():
        raise RuntimeError("Aucune parole dans cette vidéo : rien à partir de quoi écrire une légende.")
    prompt = (
        "Voici ce qui est dit dans une courte vidéo face caméra de Jonathan (transcription) :\n\n"
        f"« {transcription} »\n\n"
        + (f"Accroche affichée au début de la vidéo : « {accroche} »\n\n" if accroche else "")
        + "Prépare sa publication sur les réseaux, en français, à la première personne comme dans la vidéo :\n"
        "- titre : 40 à 60 caractères, accrocheur et honnête, sans point final.\n"
        "- texte : légende de 250 à 450 caractères, première ligne qui donne envie de regarder, une idée concrète tirée de la vidéo, "
        "une invitation à réagir ou à contacter ; passe des lignes entre les idées ; pas de hashtag dans ce texte ; 2 emojis au plus.\n"
        "- hashtags : 5 à 8 hashtags pertinents, sans le signe #, sans espace, en minuscules.\n"
        "- accroches : 3 autres accroches possibles pour le début de la vidéo (3 à 8 mots chacune).\n"
        "Aucun fait, chiffre ou promesse absent de la transcription. Aucun tiret cadratin."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=1500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_KIT_MONTAGE}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
        hashtags = [re.sub(r"[^\w]", "", h.lstrip("#")).lower() for h in brut["hashtags"]]
        return {
            "titre": _nettoyer_texte_genere(brut["titre"]).strip(" ."), "texte": _nettoyer_texte_genere(brut["texte"]),
            "hashtags": [h for h in hashtags if h][:8], "accroches": [_nettoyer_texte_genere(a).strip(" .") for a in brut["accroches"]][:3],
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur


SCHEMA_CARTES = {
    "type": "object",
    "properties": {
        "cartes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["etape", "info", "chiffre", "citation", "titre", "checklist", "appel_action", "evolution"]},
                    "debut": {"type": "number"}, "duree": {"type": "number"}, "numero": {"type": "string"},
                    "titre": {"type": "string"}, "sous_titre": {"type": "string"}, "valeur": {"type": "string"}, "libelle": {"type": "string"},
                    "texte": {"type": "string"}, "auteur": {"type": "string"}, "avant": {"type": "string"}, "mot": {"type": "string"}, "elements": {"type": "string"},
                },
                "required": ["type", "debut", "duree", "numero", "titre", "sous_titre", "valeur", "libelle", "texte", "auteur", "avant", "mot", "elements"], "additionalProperties": False,
            },
        },
    },
    "required": ["cartes"], "additionalProperties": False,
}


def cartes_depuis_transcription(phrases: list, duree: float, nombre_max: int = 6) -> list:
    """
    Propose des cartes graphiques a afficher sur une video face camera (etape numerotee, information, chiffre cle, citation, titre), placees
    la ou la personne dit quelque chose de concret : un numero d'etape, un chiffre, une definition, une phrase forte. Uniquement ce qui est
    dit, rien d'invente. Renvoie [{"type", "debut", "duree", "textes": {...}}] (temps de la transcription fournie).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    transcription = _transcription_pour_prompt(phrases)
    if not transcription.strip():
        return []
    prompt = (
        f"Voici la transcription horodatée (secondes) d'une vidéo face caméra de {duree:.0f} s :\n\n{transcription}\n\n"
        f"Propose au plus {nombre_max} cartes graphiques à afficher à l'écran pendant la vidéo, uniquement là où la personne dit quelque chose de "
        "concret qui gagne à être montré : un numéro d'étape (type etape), une information à retenir (info), un chiffre clé (chiffre), une phrase "
        "forte (citation), un titre de partie (titre), une liste de 3 ou 4 points énumérés à voix haute (checklist), un mot à écrire en commentaire "
        "(« commente X », « écris X en commentaire » : appel_action) ou un passage d'une valeur à une autre (« je suis passé de A à B » : evolution). Pas plus d'une carte toutes les 8 secondes, jamais pendant les 2 premières secondes ; "
        "debut = début de la phrase concernée, duree entre 2,5 et 4 secondes. Textes courts, repris de ce qui est dit, sans rien inventer : "
        "etape (numero, titre 4 à 7 mots, sous_titre facultatif), info (titre, sous_titre facultatif), chiffre (valeur comme « 137 % » ou « 3 », "
        "libelle), citation (texte, auteur laissé vide), titre (titre), "
        "checklist (titre de la liste, elements = 3 ou 4 points courts séparés par « | », repris tels que dits), appel_action (mot = le mot demandé, en minuscules), "
        "evolution (libelle ce qui change, avant = ancienne valeur, valeur = nouvelle valeur, comme dits). Mets '' dans les champs non utilisés, et '0' dans numero sauf pour etape. "
        "Si rien ne s'y prête, renvoie une liste vide."
    )
    prompt += _bloc_regles()
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=2500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_CARTES}}, messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)["cartes"]
    except (json.JSONDecodeError, KeyError, TypeError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    cartes, dernier = [], -100.0
    for c in sorted(brut, key=lambda x: float(x["debut"])):
        debut = float(c["debut"])
        if debut < 2.0 or debut - dernier < 8.0 or debut > duree - 1.0:
            continue
        textes = {cle: _nettoyer_texte_genere(str(c.get(cle) or "")) for cle in ("numero", "titre", "sous_titre", "valeur", "libelle", "texte", "auteur", "avant", "mot", "elements")}
        if textes["numero"] in ("0", ""):
            textes["numero"] = ""
        cartes.append({"type": c["type"], "debut": debut, "duree": max(2.0, min(4.5, float(c["duree"]) or 3.0)), "textes": textes})
        dernier = debut
    return cartes[:nombre_max]


SCHEMA_QUESTION_ARTICLE = {
    "type": "object",
    "properties": {"question": {"type": "string"}, "mot_cle": {"type": "string"}},
    "required": ["question", "mot_cle"], "additionalProperties": False,
}


def question_visee_article(titre: str, texte: str, client_nom: str = "") -> dict:
    """
    Question que l'article cherche a couvrir (celle qu'un habitant poserait a Google ou a un assistant IA) et son mot-cle local principal,
    pour mesurer ensuite si l'article fonctionne. Renvoie {"question", "mot_cle"}.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    prompt = (
        f"Voici un article de blog publié pour l'entreprise locale « {client_nom} ».\n\nTitre : {titre}\n\n{(texte or '')[:5000]}\n\n"
        "1) question : la question précise, en français, qu'un habitant poserait à un assistant IA (ChatGPT, Gemini) ou à Google et à laquelle "
        "cet article répond le mieux, avec la ville ou le quartier si l'article en parle, 6 à 16 mots, sans nom d'entreprise.\n"
        "2) mot_cle : le mot-clé local principal de recherche Google (2 à 4 mots, par exemple « garage vidange Marseille »), sans nom d'entreprise."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=500, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_QUESTION_ARTICLE}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
        return {"question": _nettoyer_texte_genere(str(brut["question"])).strip(" \"«»"), "mot_cle": _nettoyer_texte_genere(str(brut["mot_cle"])).strip(" \"«»")}
    except (json.JSONDecodeError, KeyError) as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur


SCHEMA_STYLE_REFERENCE = {
    "type": "object",
    "properties": {
        "resume": {"type": "string"},
        "sous_titres": {"type": "string", "enum": ["aucun", "classiques", "animes"]},
        "pastille": {"type": "boolean"}, "cartes": {"type": "boolean"}, "barre_progression": {"type": "boolean"},
        "titre_accroche": {"type": "boolean"}, "bruitages_probables": {"type": "boolean"},
        "couleur_principale": {"type": "string"}, "couleur_accent": {"type": "string"},
        "police": {"type": "string", "enum": ["Poppins", "Bebas Neue", "DM Serif Display", "Kalam"]},
    },
    "required": ["resume", "sous_titres", "pastille", "cartes", "barre_progression", "titre_accroche", "bruitages_probables",
                 "couleur_principale", "couleur_accent", "police"],
    "additionalProperties": False,
}


def decrire_style_reference(images: list, rythme: dict) -> dict:
    """
    Decrit le style de montage d'une video de reference a partir d'images extraites a intervalles reguliers (octets JPEG) et de son rythme
    ({"nb_coupes", "duree_moyenne_plan"}) : sous-titres, pastilles sur les mots-cles, cartes graphiques, barre de progression, titre d'accroche,
    palette et police les plus proches. Renvoie un dict conforme a SCHEMA_STYLE_REFERENCE.
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not images:
        raise RuntimeError("Aucune image exploitable dans cette vidéo.")
    import base64
    contenu = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(octets).decode()}} for octets in images[:8]]
    contenu.append({"type": "text", "text": (
        f"Ces {len(images)} images viennent d'une même courte vidéo de réseaux sociaux, prises à intervalles réguliers. Mesure : {rythme.get('nb_coupes', 0)} coupes, "
        f"plan moyen de {rythme.get('duree_moyenne_plan', 0):.1f} s. Décris son style de montage pour pouvoir le reproduire : sous_titres ('aucun', 'classiques' "
        "ou 'animes' si des mots s'animent ou se colorent un par un), pastille (un mot-clé est-il en surbrillance sur un fond coloré ?), cartes (des cartes "
        "ou éléments graphiques dessinés apparaissent-ils ?), barre_progression, titre_accroche (un gros titre au début), bruitages_probables, "
        "couleur_principale et couleur_accent (codes #rrggbb de la palette dominante des éléments graphiques), police (la plus proche parmi les "
        "choix), et resume : 2 phrases en français sur ce qui fait son style. Réponds seulement d'après ce que tu vois.")})
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=900, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_STYLE_REFERENCE}}, messages=[{"role": "user", "content": contenu}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        return json.loads(bloc)
    except json.JSONDecodeError as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur


SCHEMA_MODELE_VISUEL = {
    "type": "object",
    "properties": {
        "couverture": {"type": "object", "properties": {"titre": {"type": "string"}, "sous_titre": {"type": "string"}}, "required": ["titre", "sous_titre"], "additionalProperties": False},
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {cle: {"type": "string"} for cle in ("mythe", "realite", "avant", "apres", "titre", "texte", "valeur", "libelle", "auteur", "soustexte")},
                "required": ["mythe", "realite", "avant", "apres", "titre", "texte", "valeur", "libelle", "auteur", "soustexte"], "additionalProperties": False,
            },
        },
        "cta": {"type": "object", "properties": {"titre": {"type": "string"}, "texte": {"type": "string"}, "bouton": {"type": "string"}}, "required": ["titre", "texte", "bouton"], "additionalProperties": False},
    },
    "required": ["couverture", "elements", "cta"], "additionalProperties": False,
}
CONSIGNES_MODELE_VISUEL = {
    "mythe_realite": "Chaque élément oppose une idée reçue fréquente dans ce métier (mythe : une phrase courte, comme on la dit) à la réalité (realite : 1 à 2 phrases claires). Champs : mythe, realite.",
    "avant_apres": "Chaque élément décrit une situation AVANT (avant : un problème concret, 1 phrase) et APRÈS (apres : la situation améliorée, 1 à 2 phrases). Champs : avant, apres. N'invente aucun résultat chiffré.",
    "pas_a_pas": "Chaque élément est une étape d'une méthode, dans l'ordre. Champs : titre (4 à 8 mots, verbe d'action) et texte (1 à 2 phrases).",
    "conseil_local": "Chaque élément est un conseil concret utile aux habitants de la zone de l'entreprise, sans citer de lieu qui n'est pas dans le contexte. Champs : titre (4 à 8 mots) et texte (1 à 2 phrases).",
    "chiffre_cle": "Chaque élément met en avant UN chiffre réel, présent dans le texte de base ou le contexte (valeur : le chiffre court, comme « 137 % » ; libelle : ce qu'il mesure, 1 phrase). Si le texte ne contient aucun chiffre, renvoie une liste d'éléments vide : n'invente jamais un chiffre.",
    "temoignage": "Chaque élément reprend un VRAI avis client présent dans le texte de base (texte : l'extrait le plus parlant, tel quel, 1 à 3 phrases ; auteur : prénom et initiale du nom, comme « Marie D. »). Si le texte ne contient aucun avis client, renvoie une liste d'éléments vide : n'invente jamais un témoignage.",
    "post_typo": "Un seul élément : texte = une phrase forte et simple tirée du message (14 mots au maximum), soustexte = une précision courte (facultative, '' sinon).",
}


def contenu_modele_visuel(modele: str, texte_base: str, contexte: str, nb: int) -> dict:
    """
    Redige le contenu d'un modele de visuel (mythe ou realite, avant / apres, pas a pas...) a partir du texte de base du post, dans la voix de l'entreprise,
    sans rien inventer (chiffres, resultats et avis uniquement s'ils sont dans le texte). Renvoie {"couverture", "elements", "cta"} (voir modeles_visuels).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    consigne = CONSIGNES_MODELE_VISUEL.get(modele)
    if not consigne:
        raise RuntimeError("Modèle inconnu.")
    prompt = (
        f"Contexte sur l'entreprise (voix, activité, zone) :\n{(contexte or '')[:6000]}\n\n"
        f"Texte de base du post :\n« {(texte_base or '').strip()[:3000]} »\n\n"
        f"Prépare le contenu d'un visuel pour les réseaux sociaux, modèle « {modele} », avec {nb} élément(s). {consigne}\n"
        "Couverture : titre accrocheur (8 mots au maximum) et sous-titre court (facultatif, '' sinon). Slide finale : titre court, texte d'une phrase, bouton "
        "(2 à 3 mots, ex. « Prendre rendez-vous »). Pour le modèle post_typo, laisse couverture et cta vides. Mets '' dans les champs d'un élément non utilisés par le modèle. "
        "Français correct, ton de l'entreprise, aucun fait, chiffre, résultat ni promesse absent du texte de base ou du contexte. Aucun emoji ni tiret cadratin."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=3000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_MODELE_VISUEL}}, messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    try:
        brut = json.loads(bloc)
    except json.JSONDecodeError as erreur:
        raise RuntimeError(f"Reponse de l'IA incomplete (arret : {reponse.stop_reason}).") from erreur
    nettoye = lambda valeur: _nettoyer_texte_genere(str(valeur or ""))
    return {
        "couverture": {c: nettoye(v) for c, v in brut["couverture"].items()}, "cta": {c: nettoye(v) for c, v in brut["cta"].items()},
        "elements": [{c: nettoye(v) for c, v in e.items()} for e in brut["elements"]],
    }


# --------------------------------------------------------------------------- video motion design (voir motion_video.py)

_CHAMPS_SCENE_MOTION = ("texte", "sous_texte", "valeur", "suffixe", "libelle", "legende", "titre", "numero", "kicker", "auteur", "nom", "accroche", "bouton", "contact", "action", "accessoire")
SCHEMA_SCENARIO_MOTION = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "legende": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["hook", "chiffre", "liste", "etape", "citation", "marque", "cta", "photo", "mosaique", "avant_apres", "realisation", "personnage", "recherche"]},
                    "duree": {"type": "number"},
                    "puces": {"type": "array", "items": {"type": "string"}},
                    "perso": {"type": "integer"},
                    **{champ: {"type": "string"} for champ in _CHAMPS_SCENE_MOTION},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "photo": {"type": "integer"},
                    "photos": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["type", "duree", "perso", "puces", *_CHAMPS_SCENE_MOTION, "items", "photo", "photos"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["titre", "legende", "scenes"],
    "additionalProperties": False,
}

TONS_MOTION = {
    "elegant": "élégant et posé : phrases courtes, vocabulaire soigné, rythme lent",
    "dynamique": "dynamique et affirmé : phrases percutantes, rythme rapide, verbes d'action",
    "chaleureux": "chaleureux et humain : proche du client, simple, rassurant",
    "humour": "décalé et léger : une pointe d'humour, sans jamais ridiculiser le métier ni le client",
    "premium": "premium et sobre : peu de mots, chaque mot compte, aucune exagération",
}


# Modeles a structure imposee : l'IA n'ecrit que les textes, la structure et les photos sont fixees par le code (elle ne voit pas les photos).
MODELES_MOTION = {
    "avant_apres": {"scenes": ("hook", "avant_apres", "cta"), "durees": (2.8, 5.2, 3.2), "photos_min": 2},
    "realisation": {"scenes": ("hook", "realisation", "cta"), "durees": (2.8, 5.2, 3.2), "photos_min": 1},
    "personnage": {"scenes": ("hook", "personnage", "personnage", "personnage", "cta"), "durees": (2.6, 4.2, 4.2, 4.4, 3.2), "photos_min": 0},
    "recherche_google": {"scenes": ("hook", "recherche", "personnage", "personnage", "cta"), "durees": (2.6, 7.6, 4.2, 4.4, 3.2), "photos_min": 0},
}
CONSIGNES_MODELES = {
    "avant_apres": (
        "MODÈLE « AVANT / APRÈS » : écris EXACTEMENT 3 scènes, dans cet ordre : 1) hook : accroche qui annonce le chantier et la commune (texte de 4 à 9 mots, sous_texte facultatif) ; "
        "2) avant_apres : titre (3 à 8 mots, du type « Avant / Après : une toiture *nettoyée* à Pessac »), legende (une phrase sur ce qui a été fait, sans promesse non fournie) ; "
        "3) cta : texte (3 à 6 mots), bouton, contact. Les photos « avant » puis « après » sont choisies par l'utilisateur : ne mets aucun numéro de photo et ne décris pas les images."
    ),
    "personnage": (
        "MODÈLE « PERSONNAGE ANIMÉ » : un petit personnage en illustration parle au spectateur dans des bulles de dialogue. Écris EXACTEMENT 5 scènes, dans cet ordre : "
        "1) hook : accroche (texte de 4 à 9 mots) ; 2), 3) et 4) personnage : texte = ce que dit le personnage, à la 1re personne du pluriel ou du singulier, comme l'entreprise parlerait "
        "(8 à 16 mots, une idée par bulle : le besoin du client, ce que l'entreprise fait, le bénéfice), action (une seule parmi : salut, explique, pointe, idee, celebre, reflechit), "
        "accessoire (un seul parmi : aucun, ampoule, cle, maison, voiture, coeur, etoile, telephone, goutte, calendrier, bouclier, epingle, etoiles, recherche, fiche, avis, carte ; choisis celui qui illustre le sujet) ; la 2e scène "
        "personnage commence par saluer (action salut), la dernière fête le résultat (action celebre) ; perso : 1, 2, 3 ou 4 (le personnage, le même dans les 3 scènes) ; "
        "5) cta : texte (3 à 6 mots), bouton, contact."
    ),
    "recherche_google": (
        "MODÈLE « RECHERCHE GOOGLE » : une recherche locale animée, puis un personnage qui en tire la leçon. Écris EXACTEMENT 5 scènes, dans cet ordre : 1) hook : accroche "
        "(texte de 4 à 9 mots) ; 2) recherche : texte = la requête qu'un client taperait (2 à 4 mots, métier et ville, comme « plombier Pessac »), nom = nom de l'entreprise, "
        "libelle = son métier (1 à 2 mots), valeur = sa note UNIQUEMENT si elle figure dans les faits (sinon vide), suffixe = son nombre d'avis UNIQUEMENT s'il figure dans les faits "
        "(sinon vide), legende = une phrase pédagogique courte, sans chiffre ni promesse ; 3) et 4) personnage : texte = ce que dit le personnage (8 à 16 mots, une idée par bulle : "
        "pourquoi la fiche compte, comment la compléter), action (salut, explique, pointe, idee, celebre ou reflechit), accessoire (parmi epingle, etoiles, recherche, fiche, avis, "
        "carte, aucun), puces (0 à 3 mini-étiquettes d'interface de 2 à 4 mots, comme « Ouvert maintenant », « Itinéraire », « Appeler » ; une note comme « 4,9 ★ » UNIQUEMENT si elle "
        "figure dans les faits), perso (1 à 4, le même dans les 2 scènes) ; 5) cta : texte (3 à 6 mots), bouton, contact."
    ),
    "realisation": (
        "MODÈLE « RÉALISATION DE LA SEMAINE » : écris EXACTEMENT 3 scènes, dans cet ordre : 1) hook : accroche qui présente la réalisation (texte de 4 à 9 mots) ; "
        "2) realisation : kicker (« Réalisation de la semaine » ou variante courte), titre (3 à 8 mots : ce qui a été fait), sous_texte (la COMMUNE seule), texte (une ou deux phrases sur le "
        "chantier, uniquement d'après le brief et les faits) ; 3) cta : texte (3 à 6 mots), bouton, contact. Les photos sont choisies par l'utilisateur : ne mets aucun numéro de photo "
        "et ne décris pas les images."
    ),
}


CONSIGNE_STYLE_GOOGLE = (
    "Style visuel GOOGLE (fond blanc, couleurs de Google, illustrations) : la vidéo explique à des chefs d'entreprise comment fonctionne la présence locale sur Google "
    "(fiche d'établissement, avis, recherche locale, Google Maps). Ton pédagogique et factuel, aucune promesse de résultat, aucun chiffre qui ne figure pas dans les faits. "
    "Pour les scènes personnage, privilégie ces objets : epingle (localisation), etoiles (note d'avis), recherche (barre de recherche), fiche (fiche d'établissement), "
    "avis (avis client), carte (plan Maps).\n"
)


def generer_scenario_motion(nom: str, ville: str = "", brief: str = "", faits: str = "", contenu_site: str = "", ton: str = "dynamique", duree_cible: int = 20,
                            nb_photos: int = 0, avec_logo: bool = False, modele: str = "libre", style: str = "") -> dict:
    """
    Scenario d'une video de motion design (sans tournage) pour un client, rendue par motion_video.py : accroche, revelation, 2 a 3 temps forts, chute.
    Renvoie {"titre", "legende", "scenes": [...]} ; les scenes sont nettoyees par motion_video.nettoyer_scenario. Les chiffres viennent
    uniquement de `faits` (jamais inventes) ; tout est positif, puisque la video est montree au client.
    """
    from . import motion_video

    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    nom, ville, brief, faits = (nom or "").strip()[:120], (ville or "").strip()[:80], (brief or "").strip()[:1200], (faits or "").strip()[:800]
    duree_cible = max(12, min(int(duree_cible or 20), 30))
    nombre_scenes = max(4, min(round(duree_cible / 3), 9))
    consigne_ton = TONS_MOTION.get(ton, TONS_MOTION["dynamique"])
    structure = MODELES_MOTION.get(modele)
    if structure and nb_photos < structure["photos_min"]:
        raise ValueError("Ce modèle demande au moins " + ("2 photos : la 1re avant, la 2e après." if modele == "avant_apres" else "1 photo du chantier."))
    prompt = (
        "Tu écris le scénario d'une courte vidéo de motion design (aucun tournage : texte animé, formes, chiffres, couleurs), pour les réseaux "
        "sociaux, au format Reel. Elle est montrée au client lui-même : elle doit le valoriser.\n\n"
        f"Entreprise : {nom or '(à déduire du brief)'}{' - ' + ville if ville else ''}\n"
        + (f"Ce que le client veut faire passer :\n{brief}\n\n" if brief else "")
        + (f"Faits vérifiés que tu peux utiliser (seuls chiffres autorisés) :\n{faits}\n\n" if faits else "Aucun chiffre n'est fourni : n'utilise AUCUNE scène « chiffre » et n'invente aucun chiffre.\n\n")
        + (f"Extrait du site du client (pour comprendre son activité et son vocabulaire) :\n{(contenu_site or '')[:3500]}\n\n" if contenu_site else "")
        + (f"Photos réelles du client disponibles : {nb_photos} (numérotées de 1 à {nb_photos} ; tu ne les vois pas, ne décris donc jamais ce qu'elles montrent). "
           "Utilise-les avec des scènes « photo » et « mosaique » : jamais deux scènes photo de suite, au plus 3 scènes photo ou mosaïque en tout, "
           "et répartis les numéros pour que chaque photo serve.\n" if nb_photos else "Aucune photo disponible : n'utilise ni scène photo ni scène mosaique.\n")
        + ("Le logo du client sera affiché dans la scène marque.\n" if avec_logo else "")
        + f"Ton : {consigne_ton}.\n"
        + (CONSIGNE_STYLE_GOOGLE if style == "google" else "")
        + (CONSIGNES_MODELES[modele] + "\n\n" if structure else f"Durée visée : environ {duree_cible} secondes, soit {nombre_scenes} scènes de 2,5 à 4 secondes.\n\n")
        + "Types de scènes disponibles (remplis uniquement les champs utiles à la scène, laisse les autres vides) :\n"
        "- hook : accroche d'ouverture. texte (4 à 9 mots), sous_texte (une phrase courte, facultative).\n"
        "- marque : révélation du nom. nom, accroche (slogan de 3 à 6 mots).\n"
        "- chiffre : un chiffre fourni dans les faits. valeur (nombre seul, ex. 4,8 ou 127), suffixe (ex. /5, ans, %), libelle (3 à 6 mots), legende (source ou précision courte).\n"
        "- liste : titre (3 à 6 mots), items (3 ou 4 éléments de 2 à 6 mots).\n"
        "- etape : numero (01, 02...), kicker (1 à 2 mots), titre (3 à 7 mots), texte (une phrase). Utilise TOUJOURS au moins 2 étapes à la suite (01, 02, éventuellement 03), jamais une seule.\n"
        "- citation : texte (un avis ou une phrase client, 8 à 20 mots, UNIQUEMENT s'il est fourni dans les faits), auteur.\n"
        "- cta : texte (3 à 6 mots), bouton (2 à 4 mots), contact (lignes : site, téléphone ou adresse, UNIQUEMENT ceux fournis).\n"
        "- photo : une photo du client en plein cadre. photo (son numéro), texte (4 à 9 mots posés sur l'image), sous_texte (facultatif).\n"
        "- mosaique : trois photos en cartes. titre (3 à 7 mots), photos (3 numéros).\n"
        "- avant_apres : curseur qui révèle la photo « après ». titre, photos (2 numéros : avant puis après), legende (une phrase).\n"
        "- realisation : une réalisation. kicker, titre, sous_texte (la commune), texte (description), photos (1 à 3 numéros).\n"
        "- personnage : un personnage animé qui parle dans une bulle (maximum 3 scènes de ce type, de préférence à la suite). texte (8 à 16 mots, ce qu'il dit), "
        "action (salut, explique, pointe, idee, celebre ou reflechit), accessoire (aucun, ampoule, cle, maison, voiture, coeur, etoile, telephone, goutte, calendrier, bouclier, epingle, etoiles, recherche, fiche, avis ou carte), "
        "perso (1 à 4, le même numéro dans toutes les scènes personnage), puces (facultatif : 0 à 3 mini-étiquettes d'interface flottantes de 2 à 4 mots, comme « Ouvert maintenant », "
        "« Itinéraire », « Appeler » ; une note comme « 4,9 ★ » UNIQUEMENT si elle figure dans les faits).\n"
        "- recherche : une recherche Google locale animée (la requête se tape, le plan apparaît, puis les résultats dont le premier est l'entreprise). texte (la requête, 2 à 4 mots), "
        "nom, libelle (le métier), valeur (note, UNIQUEMENT si fournie dans les faits), suffixe (nombre d'avis, UNIQUEMENT s'il est fourni), legende (phrase pédagogique, facultative). "
        "Une seule scène de ce type par vidéo.\n\n"
        "Dans les champs texte, texte et titre uniquement : entoure UN mot clé de *astérisques* pour le surligner sur une pastille de couleur, et "
        "UN autre mot de _tirets bas_ pour le mettre en serif italique.\n\n"
        "Trame obligatoire : une accroche (hook) qui arrête le pouce, la révélation (marque), 2 ou 3 temps forts (chiffre, liste, etape, citation), "
        "puis une chute (cta). Chaque texte doit rester lisible : pas plus de 9 mots par ligne de titre. "
        "Règles : français soigné avec accents ; jamais de tiret cadratin ; pas de formule creuse (« leader », « excellence », « solutions sur mesure ») ; "
        "aucune promesse que le client ne tiendrait pas ; rien de négatif, de comparatif ou de critique ; ne cite aucun prix ni délai absent des faits.\n"
        "titre : titre interne de la vidéo (3 à 6 mots). legende : texte du post à publier avec la vidéo (3 à 4 lignes, 3 hashtags à la fin)."
    )
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=4000, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_SCENARIO_MOTION}},
        messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    donnees = json.loads(bloc)
    scenes = motion_video.nettoyer_scenario({"scenes": [{**{k: v for k, v in scene.items() if v not in ("", [], 0)}} for scene in donnees.get("scenes", [])]})
    if structure:                          # modele a structure imposee : seules les 3 scenes prevues, durees et photos fixees par le code
        attendues = list(structure["scenes"])
        retenues = []
        for type_scene in attendues:
            trouvee = next((s for s in scenes if s["type"] == type_scene and s not in retenues), None)
            if trouvee is None:
                raise RuntimeError(f"L'IA n'a pas ecrit la scene « {type_scene} » : reessayez.")
            retenues.append(trouvee)
        for scene, duree_scene in zip(retenues, structure["durees"]):
            scene["duree"] = duree_scene
            if scene["type"] == "personnage":
                scene["perso"] = 1
            if scene["type"] == "recherche":
                scene["valeur"] = scene.get("valeur", "") if str(scene.get("valeur", "")).replace(",", "").replace(".", "").isdigit() else ""
            if scene["type"] == "avant_apres":
                scene["photos"] = [1, 2]
            elif scene["type"] == "realisation":
                scene["photos"] = list(range(1, min(nb_photos, 3) + 1))
        return {"titre": _nettoyer_texte_genere(donnees.get("titre", "")).replace("\u2014", "-")[:120], "legende": _nettoyer_texte_genere(donnees.get("legende", "")).replace("\u2014", "-")[:1200],
                "scenes": retenues}
    if not nb_photos:                      # garde-fou : sans photo, jamais de scene photo
        scenes = [s for s in scenes if s["type"] not in ("photo", "mosaique", "avant_apres", "realisation")]
    else:                                  # numeros hors plage : ramenes dans les photos disponibles
        for s in scenes:
            if "photo" in s:
                s["photo"] = min(s["photo"], nb_photos)
            if "photos" in s:
                s["photos"] = [min(n, nb_photos) for n in s["photos"]]
        scenes = [s for s in scenes if not (s["type"] == "photo" and "photo" not in s) and not (s["type"] == "mosaique" and not s.get("photos"))]
    if not scenes:
        raise RuntimeError("L'IA n'a renvoye aucune scene exploitable.")
    return {"titre": _nettoyer_texte_genere(donnees.get("titre", "")).replace("\u2014", "-")[:120], "legende": _nettoyer_texte_genere(donnees.get("legende", "")).replace("\u2014", "-")[:1200],
            "scenes": scenes}


# --------------------------------------------------------------------------- suggestions pour la video motion design (a partir du site du client)

SCHEMA_IDEES_MOTION = {
    "type": "object",
    "properties": {"idees": {"type": "array", "items": {
        "type": "object", "properties": {"titre": {"type": "string"}, "brief": {"type": "string"}}, "required": ["titre", "brief"], "additionalProperties": False}}},
    "required": ["idees"], "additionalProperties": False,
}
SCHEMA_FAITS_SITE = {"type": "object", "properties": {"faits": {"type": "array", "items": {"type": "string"}}}, "required": ["faits"], "additionalProperties": False}


def _json_claude(prompt: str, schema: dict, max_tokens: int = 2500) -> dict:
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE, max_tokens=max_tokens, thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": schema}}, messages=[{"role": "user", "content": prompt}],
    )
    bloc = next((b.text for b in reponse.content if b.type == "text"), None)
    if not bloc:
        raise RuntimeError("L'IA n'a renvoye aucun texte exploitable.")
    return json.loads(bloc)


def suggerer_idees_video_motion(nom: str, contenu_site: str, deja: list = None, nombre: int = 6) -> list:
    """Idees (6 par defaut, jusqu'a 10) de videos de motion design pour un client, tirees uniquement du contenu de son site : [{"titre", "brief"}]."""
    nombre = max(1, min(int(nombre or 6), 10))
    contenu = (contenu_site or "").strip()[:6000]
    if not contenu:
        raise RuntimeError("Aucun contenu de site enregistre pour ce client.")
    exclusions = ("\nIdées déjà affichées (n'en reproduis aucune, propose des angles différents) :\n" + "\n".join(f"- {x[:120]}" for x in (deja or [])[:20]) + "\n") if deja else ""
    prompt = (
        f"Voici le contenu du site de l'entreprise « {(nom or '').strip()[:120]} » :\n\n{contenu}\n{exclusions}\n"
        f"Propose {nombre} idées de courtes vidéos de motion design (texte animé, sans tournage, 15 à 25 secondes) pour ses réseaux sociaux. "
        "Chaque idée = un angle différent (un service précis, une question que se posent les clients, une promesse tenue, une façon de travailler, une zone desservie, "
        "un avantage concret). titre : 3 à 7 mots. brief : 1 à 2 phrases qui disent ce que la vidéo doit faire passer, en t'appuyant UNIQUEMENT sur ce que dit le site. "
        "Règles : français soigné avec accents ; jamais de tiret cadratin ; n'invente aucun chiffre, aucun délai, aucun prix, aucun avis ; rien de négatif ni de comparatif ; "
        "pas de formule creuse."
    )
    idees = _json_claude(prompt, SCHEMA_IDEES_MOTION).get("idees", [])
    propres = [{"titre": _nettoyer_texte_genere(i.get("titre", "")).replace("\u2014", "-")[:90], "brief": _nettoyer_texte_genere(i.get("brief", "")).replace("\u2014", "-")[:400]} for i in idees]
    propres = [i for i in propres if i["titre"] and i["brief"]][:nombre]
    if not propres:
        raise RuntimeError("L'IA n'a propose aucune idee exploitable.")
    return propres


def extraire_faits_site(nom: str, contenu_site: str) -> list:
    """Faits verifiables ECRITS sur le site du client (anciennete, zone, services, garanties, certifications, coordonnees...), jamais deduits ni inventes."""
    contenu = (contenu_site or "").strip()[:8000]
    if not contenu:
        raise RuntimeError("Aucun contenu de site enregistre pour ce client.")
    prompt = (
        f"Voici le contenu du site de l'entreprise « {(nom or '').strip()[:120]} » :\n\n{contenu}\n\n"
        "Relève les faits précis et vérifiables que le site AFFIRME lui-même et qui valorisent l'entreprise : ancienneté ou année de création, zone desservie, "
        "services proposés, garanties, certifications ou labels, délais ou tarifs annoncés, moyens (matériel, équipe), site web, téléphone, adresse, horaires. "
        "Une ligne par fait, courte, reprise fidèlement du site (n'arrondis pas, ne déduis rien, n'invente rien, ne mélange pas deux faits). "
        "Si le site n'affirme rien de précis sur un sujet, ne le mentionne pas. Au plus 10 faits, les plus utiles pour une vidéo d'abord. "
        "N'inclus aucun avis ni aucune note : ils ne figurent pas sur le site. Français soigné, jamais de tiret cadratin."
    )
    faits = _json_claude(prompt, SCHEMA_FAITS_SITE, 1500).get("faits", [])
    return [_nettoyer_texte_genere(f).replace("\u2014", "-")[:200] for f in faits if str(f).strip()][:10]


SCHEMA_CATEGORIE = {"type": "object", "properties": {"categorie_id": {"type": ["integer", "null"]}}, "required": ["categorie_id"], "additionalProperties": False}


def choisir_categorie_wordpress(titre: str, texte: str, categories: list) -> int:
    """
    Choisit parmi les categories EXISTANTES du site celle qui convient le mieux a un article (jamais de nouvelle categorie) : renvoie son identifiant,
    ou None si aucune ne convient vraiment. categories : [{"id", "nom", "nombre", "defaut"}] ; la categorie par defaut (« Non classe ») n'est pas proposee.
    """
    utiles = [c for c in (categories or []) if not c.get("defaut")]
    if not utiles or not (titre or texte or "").strip():
        return None
    liste = "\n".join(f"- {c['id']} : {c['nom']} ({c.get('nombre', 0)} articles)" for c in utiles[:60])
    prompt = (
        f"Article de blog :\nTitre : {(titre or '').strip()[:200]}\nDébut du texte : {(texte or '').strip()[:1500]}\n\n"
        f"Catégories qui existent déjà sur le site :\n{liste}\n\n"
        "Choisis la catégorie la plus adaptée à cet article, d'après son sujet principal. Réponds par son identifiant. "
        "Si aucune catégorie ne correspond vraiment (même de loin), réponds null : n'en invente pas et ne force pas un choix."
    )
    identifiant = _json_claude(prompt, SCHEMA_CATEGORIE, 200).get("categorie_id")
    return identifiant if identifiant in {c["id"] for c in utiles} else None


SCHEMA_IDEES_ARTICLES = {
    "type": "object",
    "properties": {"idees": {"type": "array", "items": {
        "type": "object",
        "properties": {"categorie_id": {"type": ["integer", "null"]}, "titre": {"type": "string"}, "angle": {"type": "string"}, "mot_cle": {"type": "string"}},
        "required": ["categorie_id", "titre", "angle", "mot_cle"], "additionalProperties": False}}},
    "required": ["idees"], "additionalProperties": False,
}


def _normaliser_titre(titre: str) -> str:
    sans_accents = "".join(c for c in unicodedata.normalize("NFKD", titre or "") if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", sans_accents.lower()).strip()


def suggerer_articles_par_categorie(contexte_client: str, ville: str, categories: list, articles: list, nombre: int = 6) -> list:
    """
    Idees d'articles de blog qui comblent les categories peu fournies du site, sans repeter l'existant. categories : [{"id", "nom", "nombre"}] (sans la categorie par
    defaut) ; articles : [{"titre", "categories": [ids]}] deja publies. Renvoie [{"categorie_id", "categorie", "titre", "angle", "mot_cle"}] ; les idees trop proches
    d'un article existant sont retirees.
    """
    if not categories:
        raise RuntimeError("Le site n'a aucune catégorie : créez-en une sur WordPress pour recevoir des idées par catégorie.")
    nombre = max(3, min(int(nombre or 6), 10))
    lignes = []
    for c in sorted(categories, key=lambda c: (c.get("nombre", 0), c["nom"])):
        titres = [a["titre"] for a in articles if c["id"] in a.get("categories", [])][:12]
        lignes.append(f"- {c['id']} : {c['nom']} ({c.get('nombre', 0)} article(s))" + (" - titres existants : " + " | ".join(titres) if titres else " - aucun article pour l'instant"))
    autres = [a["titre"] for a in articles][:40]
    prompt = (
        f"Contexte de l'entreprise :\n{(contexte_client or '').strip()[:2500]}\n" + (f"Zone principale : {ville.strip()[:80]}\n" if (ville or "").strip() else "") +
        "\nCatégories du blog de son site, de la moins fournie à la plus fournie :\n" + "\n".join(lignes[:40]) + "\n\n"
        f"Tous les titres déjà publiés (ne pas les répéter, même reformulés) :\n" + "\n".join(f"- {t}" for t in autres) + "\n\n"
        f"Propose {nombre} idées d'articles de blog NOUVELLES. Priorité aux catégories les moins fournies (0 à 3 articles) : au plus 2 idées par catégorie, et répartis-les sur plusieurs catégories. "
        "Chaque idée répond à une vraie question de client ou à un besoin concret (comment, quand, pourquoi, combien de temps, que faire si...), utile à quelqu'un qui cherche ce service "
        "près de chez lui. titre : 70 caractères au plus. angle : une phrase qui dit ce que l'article apporte. mot_cle : la requête principale visée (service + sujet, avec la ville si pertinent). "
        "categorie_id : l'identifiant de la catégorie la plus adaptée parmi celles listées, jamais une catégorie inventée. "
        "Règles : français soigné avec accents ; jamais de tiret cadratin ; n'invente aucun chiffre, tarif, délai ou garantie ; rien de négatif sur un concurrent."
    )
    brutes = _json_claude(prompt, SCHEMA_IDEES_ARTICLES, 3000).get("idees", [])
    existants = [_normaliser_titre(a["titre"]) for a in articles]
    noms = {c["id"]: c["nom"] for c in categories}
    idees, vus = [], []
    for brute in brutes:
        titre = _nettoyer_texte_genere(str(brute.get("titre", ""))).replace("\u2014", "-").strip()[:120]
        norme = _normaliser_titre(titre)
        if not norme or any(difflib.SequenceMatcher(None, norme, autre).ratio() > 0.8 for autre in existants + vus):
            continue
        vus.append(norme)
        identifiant = brute.get("categorie_id") if brute.get("categorie_id") in noms else None
        idees.append({"categorie_id": identifiant, "categorie": noms.get(identifiant, ""), "titre": titre,
                      "angle": _nettoyer_texte_genere(str(brute.get("angle", ""))).replace("\u2014", "-").strip()[:300],
                      "mot_cle": " ".join(str(brute.get("mot_cle", "")).split())[:120]})
    if not idees:
        raise RuntimeError("L'IA n'a proposé aucune idée nouvelle exploitable : réessayez.")
    return idees[:10]
