"""
Generation de posts via l'API Claude. Reprend la meme logique que le script
en ligne de commande generer_posts.py (prompt SEO local + sortie structuree
en JSON), adaptee en fonction pure pour la plateforme web.
"""

import json
import os
import re
from datetime import date

from anthropic import Anthropic
from dotenv import load_dotenv

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

    instructions = gabarit_prompt.replace("{NOMBRE_POSTS}", str(nombre_posts))

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
    return texte.strip()


def _nettoyer_champs_post(post: dict) -> dict:
    for cle in ("titre", "texte"):
        if post.get(cle):
            post[cle] = _nettoyer_texte_genere(post[cle])
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
    """
    if not CLE_API:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquant dans plateforme_web/.env."
        )

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

    donnees = json.loads(bloc_texte)
    posts = donnees["posts"]
    for post in posts:
        # Filet de securite : il est arrive que l'IA inverse les deux champs
        # (le corps complet du post dans "titre", un mot-cle comme "texte")
        # malgre les consignes - un titre nettement plus long que le texte
        # trahit cette inversion.
        if len(post["titre"]) > len(post["texte"]):
            post["titre"], post["texte"] = post["texte"], post["titre"]
        _nettoyer_champs_post(post)
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

    return _nettoyer_champs_post(json.loads(bloc_texte))


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


def generer_post_expert(theme: str = "", contexte_expert: str = "", contenu_article: str = "", nom_client: str = "") -> dict:
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

    consigne_sourcage = (
        "- Base-toi strictement sur les faits presents dans l'extrait source fourni : "
        "n'invente aucun detail factuel (date, chiffre, fonctionnalite, citation) qui n'y "
        "figure pas. Si l'extrait ne suffit pas a etayer un point, reste general ou passe a "
        "l'analyse/l'avis plutot que de combler par une supposition presentee comme un fait.\n"
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
        f"{bloc_article}\n"
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


def generer_questions_interview(
    contexte_expert: str = "", sujets_deja_traites: list[str] = None, nombre: int = 5, nom_client: str = "",
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
        f"Propose {nombre} questions courtes a poser a un expert, dans le domaine suivant : le "
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

    return [q.strip() for q in json.loads(bloc_texte)["questions"] if q.strip()]


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
        f"\nQuestion posee :\n« {question.strip()} »\n"
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


def suggerer_sujets_actualite(articles: list[dict], nombre: int = 5, sujets_deja_traites: list[str] = None) -> list[dict]:
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

    prompt = (
        "Voici une liste d'articles d'actualite recente sur le SEO local, Google Business "
        "Profile, Google AI Overviews et Google Local Services Ads :\n\n"
        f"{liste_articles}\n"
        f"{bloc_deja_traites}\n"
        f"Choisis les {nombre} articles les plus interessants a commenter pour un expert "
        "SEO local qui veut publier du contenu qui donne envie de le suivre (pas juste "
        "relayer l'info). Pour chacun, reformule un sujet de post concret et accrocheur "
        "(une phrase courte, en francais, prete a etre utilisee comme angle de redaction - "
        "pas juste le titre de l'article recopie), et indique l'index de l'article source.\n"
        "Consignes :\n"
        f"- Exactement {nombre} suggestions, toutes sur des articles differents.\n"
        "- Privilegie les sujets les plus recents et les plus specifiques (evite les "
        "generalites deja connues).\n"
        "- Chaque sujet doit donner un angle clair (ex : une consequence pratique, une "
        "question que ca souleve, un conseil qui en decoule), pas juste redire le titre."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=1536,
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
        max_tokens=1536,
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
    return [_nettoyer_champs_post(post) for post in json.loads(bloc_texte)["posts"]]


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
        "concret pour un client a proximite). Sobre, pas d'emoji, pas de hashtag."
    ),
    "facebook": (
        "Ton chaleureux et communautaire, comme si on s'adressait directement aux habitants du quartier "
        "ou de la ville. Peut etre un peu plus developpe et conversationnel qu'un post Google. Pas de "
        "hashtag (peu efficaces sur Facebook, contrairement a Instagram)."
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
    "wordpress": "WordPress",
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
        "meme offre, memes coordonnees le cas echeant) mais en ajustant le ton et la formulation :\n"
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
    return {r: _nettoyer_texte_genere(variantes[r]) for r in reseaux}


def generer_article_blog(sujet: str, contexte: str = "", lien_cta: str = "", texte_cta_impose: bool = False) -> str:
    """
    Article de blog complet (markdown : "# Titre", puis "## " pour les
    sous-titres) a partir d'un sujet ou d'un texte de base, pour publication
    sur le WordPress du client (voir wordpress_publish.py). contexte : contenu
    du site du client, avec sa "voix" en tete s'il y en a une (voir
    main._contexte_ia_client).
    """
    if not CLE_API:
        raise RuntimeError("ANTHROPIC_API_KEY manquant dans plateforme_web/.env.")
    if not (sujet or "").strip():
        raise RuntimeError("Aucun sujet fourni.")

    bloc_contexte = f"\nContexte sur l'entreprise (site web, voix de l'auteur) :\n{contexte.strip()[:7000]}\n" if contexte.strip() else ""
    prompt = (
        f"Nous sommes le {date.today().strftime('%d/%m/%Y')} (repere temporel reel, n'ecris jamais une annee "
        "anterieure par reflexe).\n"
        "Redige un article de blog complet, en francais, pour le site de l'entreprise decrite ci-dessous, a "
        "partir de ce sujet ou de ce texte de base :\n\n"
        f"\"{sujet.strip()}\"\n"
        f"{bloc_contexte}\n"
        "Consignes :\n"
        "- Format markdown : la toute premiere ligne est le titre, sous la forme « # Titre » (accrocheur, "
        "60 caracteres environ, avec le sujet principal et si pertinent la zone d'intervention). Ensuite une "
        "introduction (sans titre), puis 3 a 5 sections avec des sous-titres « ## ». Termine par une courte "
        "conclusion avec un appel a l'action (contacter l'entreprise), sans numero de telephone invente.\n"
        "- Longueur : 800 a 1200 mots. Paragraphes courts (2 a 4 phrases), listes a puces quand elles aident. "
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
        + (
            "\n- Le bouton d'appel a l'action est ajoute automatiquement apres ton texte : n'en ecris pas."
            if texte_cta_impose else
            (
                f"\n- Termine l'article par UNE ligne seule, exactement de la forme [[Texte du bouton|{lien_cta}]] : "
                "le texte (2 a 4 mots) invite a contacter l'entreprise, a la personne grammaticale de l'auteur "
                "(« Me contacter » si l'auteur est un independant qui dit « je », « Nous contacter » pour une equipe), "
                "ou plus precis si le sujet s'y prete (« Demander un devis », « Prendre rendez-vous »). "
                "N'utilise aucun autre lien."
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
    return _nettoyer_texte_genere(bloc_texte)


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
        ("mains", "Mes mains seulement", "only hands are visible, no face"),
        ("personne", "Personne", "no people at all"),
        ("equipe", "Une équipe", "a small team of two to four people working together, faces not the focus"),
    ],
}
LIBELLES_GROUPES_VISUEL = {
    "type": "Type de visuel", "cadrage": "Cadrage", "decor": "Décor", "lumiere": "Lumière et ambiance",
    "personnes": "Qui apparaît",
}
# Le type impose des limites : ces types ne montrent pas de visage, donc jamais les photos de reference.
TYPES_SANS_VISAGE = {"detail", "illustration", "lieu"}
PERSONNES_AVEC_REFERENCE = {"moi", "moi_client"}
PERSONNES_SANS_REFERENCE = {"mains", "personne", "equipe"}


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
        "lumieres": plusieurs("lumiere"), "personnes": un("personnes"), "aleatoires": [],
    }
    if varier:
        if not choix["type"]:
            choix["type"] = hasard.choice([i for i in connus["type"] if i != "illustration"])
            choix["aleatoires"].append(("type", choix["type"]))
        type_choisi = choix["type"]
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
    couleurs_secondaires: list = None,
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
    if choix["type"]:
        lignes.append(f"- Visual type: {_index_options('type')[choix['type']][1]}.")
    for groupe, cle in (("cadrage", "cadrages"), ("decor", "decors"), ("lumiere", "lumieres")):
        consignes = [_index_options(groupe)[i][1].replace("{saison}", saison) for i in choix[cle]]
        if consignes:
            libelle = {"cadrage": "Framing", "decor": "Setting", "lumiere": "Light and mood"}[groupe]
            lignes.append(f"- {libelle}: " + "; ".join(consignes) + ".")
    personnes = choix["personnes"]
    if avec_reference and personnes not in PERSONNES_SANS_REFERENCE:
        consigne = _index_options("personnes")[personnes][1] if personnes in PERSONNES_AVEC_REFERENCE else _index_options("personnes")["moi"][1]
        lignes.append(
            f"- People: {consigne}. Write \"the person from the reference photos\" and NEVER describe their physique "
            "(face, hair, age, clothes): the face comes from the photos. Keep the face clearly visible."
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
        "No legible text anywhere (papers, screens, signs, clothes), no logo, no geographic reference. Avoid generic "
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
