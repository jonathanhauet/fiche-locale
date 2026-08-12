"""
Generation de posts via l'API Claude. Reprend la meme logique que le script
en ligne de commande generer_posts.py (prompt SEO local + sortie structuree
en JSON), adaptee en fonction pure pour la plateforme web.
"""

import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
FICHIER_PROMPT = os.path.join(DOSSIER_APP, "prompts", "prompt_generation_posts.txt")

MODELE_CLAUDE = "claude-sonnet-5"

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


def _construire_prompt(contenu_site: str, nombre_posts: int) -> str:
    with open(FICHIER_PROMPT, "r", encoding="utf-8") as f:
        gabarit_prompt = f.read()

    instructions = gabarit_prompt.replace("{NOMBRE_POSTS}", str(nombre_posts))

    instruction_prompt_image = (
        "\n\n---\n\n"
        "Consigne technique supplementaire : pour chaque post, redige aussi un prompt "
        "en anglais destine a un generateur d'images (decrivant une photo ou illustration "
        "adaptee au post, sans texte incruste ni logo), a fournir en plus du titre et du texte."
    )

    return (
        "Voici le contenu du site web de l'entreprise (base de connaissance) :\n\n"
        f"{contenu_site}\n\n"
        "---\n\n"
        f"{instructions}"
        f"{instruction_prompt_image}"
    )


def generer_posts_pour_client(contenu_site: str, nombre_posts: int) -> list[dict]:
    """
    Appelle l'API Claude et renvoie une liste de dictionnaires :
    [{"titre": str, "texte": str, "prompt_image": str}, ...]
    """
    if not CLE_API:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquant dans plateforme_web/.env."
        )

    prompt_complet = _construire_prompt(contenu_site, nombre_posts)

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
    return donnees["posts"]


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

    bloc_theme = f"\nTheme demande :\n« {theme.strip()} »\n" if theme.strip() else ""
    bloc_reference = (
        "\nContenu du site d'une fiche existante, fourni comme source d'inspiration "
        "pour le fond uniquement (expertise, ton, type de conseils) — jamais pour des "
        "details geographiques ou le nom de l'entreprise :\n"
        f"{contenu_site_reference.strip()}\n"
        if contenu_site_reference.strip() else ""
    )

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
        "- Longueur adaptee a un Google Post (quelques phrases, pas un roman).\n"
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

    return json.loads(bloc_texte)


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

    return bloc_texte.strip()
