"""Generation d'images via Gemini. Meme logique que le script generer_image.py."""

import base64
import os
import re

from dotenv import load_dotenv
from google import genai

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

MODELE_GEMINI = "gemini-3.1-flash-image"
CLE_GEMINI = os.getenv("GEMINI_API_KEY")


INSTRUCTION_PERSONNE = (
    "The attached photos show one real person (the author) from several angles. Create a photorealistic "
    "image in which THIS SAME PERSON is clearly the main subject, with a recognizable face that stays "
    "faithful to the reference photos (facial features, hair, skin tone, age, build). The face must be "
    "clearly visible: not turned away, not tiny, not hidden. It must look like a candid smartphone snapshot, "
    "not a studio or stock photo, with no legible text anywhere. Scene to depict: "
)

# Les prompts de post demandent souvent "no recognizable faces" / "no people" : contradictoire
# (et prioritaire, selon Gemini) quand on veut justement qu'une personne apparaisse.
_INTERDICTIONS_VISAGE = re.compile(
    r"(?:,\s*|\.\s*|\s+)?\b(?:no|without|avoid(?:ing)?|zero)\s+(?:any\s+)?"
    r"(?:(?:recogni[sz]able|identifiable|visible|human|real)\s+)*"
    r"(?:faces?|people|persons?|humans?|individuals?)\b[^,.;]*|\bfaceless\b",
    re.IGNORECASE,
)


def _prompt_avec_personne(prompt_image: str) -> str:
    """Prefixe le prompt d'une consigne explicite sur les photos de reference et en retire les interdictions de visage."""
    nettoye = re.sub(r"\s*,(?:\s*,)+", ",", _INTERDICTIONS_VISAGE.sub("", prompt_image)).strip(" ,.")
    return f"{INSTRUCTION_PERSONNE}{nettoye}"


def generer_image(prompt_image: str, aspect_ratio: str = None, images_reference: list[bytes] = None) -> bytes:
    """
    Genere une image via Gemini a partir d'un prompt et renvoie les octets
    (PNG). aspect_ratio optionnel (ex. "1:1", "4:5", "16:9") : sans lui,
    Gemini renvoie son format par defaut (paysage large, ~1408x768) - laisse
    tel quel pour les appelants existants (post Google Business Profile
    unique), mais a fournir explicitement pour une image partagee entre
    plusieurs reseaux (voir /publication-multi/{client_id}/generer_image),
    Instagram affichant tres mal une image en paysage contrairement aux
    3 autres reseaux.

    images_reference (optionnel, voir models.PhotoReferenceClient) : photos
    du client a utiliser comme reference pour qu'il apparaisse comme sujet
    de l'image generee (cohesion de personnage geree nativement par
    gemini-3.1-flash-image, jusqu'a 14 images de reference). Sans elles,
    l'appel reste un simple prompt texte comme avant.
    """
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")

    client = genai.Client(api_key=CLE_GEMINI)
    if images_reference:
        entree = [{"type": "text", "text": _prompt_avec_personne(prompt_image)}] + [
            {"type": "image", "data": base64.b64encode(octets).decode("utf-8"), "mime_type": "image/jpeg"}
            for octets in images_reference
        ]
    else:
        entree = prompt_image
    parametres = {"model": MODELE_GEMINI, "input": entree}
    if aspect_ratio:
        parametres["response_format"] = {"type": "image", "aspect_ratio": aspect_ratio}
    interaction = client.interactions.create(**parametres)
    return base64.b64decode(interaction.output_image.data)
