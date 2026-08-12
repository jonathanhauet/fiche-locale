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


def generer_image(prompt_image: str) -> bytes:
    """Genere une image via Gemini a partir d'un prompt et renvoie les octets (PNG)."""
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")

    client = genai.Client(api_key=CLE_GEMINI)
    interaction = client.interactions.create(model=MODELE_GEMINI, input=prompt_image)
    return base64.b64decode(interaction.output_image.data)
