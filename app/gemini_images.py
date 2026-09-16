"""Generation d'images via Gemini. Meme logique que le script generer_image.py."""

import base64
import os

from dotenv import load_dotenv
from google import genai

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

MODELE_GEMINI = "gemini-3.1-flash-image"
CLE_GEMINI = os.getenv("GEMINI_API_KEY")


def generer_image(prompt_image: str, aspect_ratio: str = None) -> bytes:
    """
    Genere une image via Gemini a partir d'un prompt et renvoie les octets
    (PNG). aspect_ratio optionnel (ex. "1:1", "4:5", "16:9") : sans lui,
    Gemini renvoie son format par defaut (paysage large, ~1408x768) - laisse
    tel quel pour les appelants existants (post Google Business Profile
    unique), mais a fournir explicitement pour une image partagee entre
    plusieurs reseaux (voir /publication-multi/{client_id}/generer_image),
    Instagram affichant tres mal une image en paysage contrairement aux
    3 autres reseaux.
    """
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")

    client = genai.Client(api_key=CLE_GEMINI)
    parametres = {"model": MODELE_GEMINI, "input": prompt_image}
    if aspect_ratio:
        parametres["response_format"] = {"type": "image", "aspect_ratio": aspect_ratio}
    interaction = client.interactions.create(**parametres)
    return base64.b64decode(interaction.output_image.data)
