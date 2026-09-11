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


def _nettoyer_texte_genere(texte: str) -> str:
    """
    Filet de securite contre de rares artefacts de generation (ex. suites de
    backticks ou de ponctuation/caracteres CJK isoles, sans lien avec le
    contenu demande, qui se glissent occasionnellement dans la sortie du
    modele) - jamais legitimes dans un post en francais, retires sans risque.
    Remplace aussi le tiret cadratin (—) par un tiret simple : meme avec la
    consigne de style demandee au modele, il peut lui arriver d'en glisser
    un malgre tout - ce filet garantit qu'aucun n'atteint jamais le client.
    """
    texte = _CARACTERES_INDESIRABLES.sub("", texte)
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
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=8192,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REPONSE}},
        messages=[{"role": "user", "content": prompt_complet}],
    )

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
        "- Redige aussi un titre court et un prompt en anglais pour un generateur d'images "
        "(illustration adaptee au theme, sans texte incruste ni logo, sans reference geographique)."
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
        "- Pour chaque proposition, redige aussi un titre court et un prompt en anglais pour un "
        "generateur d'images (illustration adaptee au theme, sans texte incruste ni logo, sans "
        "reference geographique)."
    )

    client = Anthropic(api_key=CLE_API)
    reponse = client.messages.create(
        model=MODELE_CLAUDE,
        max_tokens=8192,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA_REPONSE}},
        messages=[{"role": "user", "content": prompt}],
    )

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
