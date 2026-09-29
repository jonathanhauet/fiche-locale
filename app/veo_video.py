"""
Generation de courtes videos via Veo (Google), pour les Shorts YouTube / Reels Instagram / videos Facebook et
LinkedIn du composeur multi-reseaux. Meme principe que gemini_images.py pour les images : le texte du post sert
de base a un prompt video, une photo de reference du client peut servir d'image de depart.

Cout reel (tarif officiel Google, verifie le 2026-09-29) : Lite ~0,05 $/s (720p), Fast ~0,10 $/s (720p). La duree
par defaut observee est de 8 secondes, soit environ 0,40 $ (Lite) ou 0,80 $ (Fast) par video generee.
"""

import os
import time

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

CLE_GEMINI = os.getenv("GEMINI_API_KEY")

MODELES = {"lite": "veo-3.1-lite-generate-preview", "fast": "veo-3.1-fast-generate-preview"}
QUALITE_DEFAUT = "lite"
ATTENTE_SECONDES = 8
ATTENTE_MAX_SECONDES = 280  # Veo indique lui-meme jusqu'a quelques minutes ; au-dela, on abandonne plutot que de bloquer indefiniment la requete.


def generer_video(prompt: str, qualite: str = QUALITE_DEFAUT, image_depart: bytes = None, aspect_ratio: str = "9:16") -> bytes:
    """
    Genere une courte video (texte seul, ou a partir d'une image de depart) et renvoie les octets MP4. Bloque
    jusqu'a la fin de la generation (quelques dizaines de secondes a quelques minutes) : a appeler depuis un
    thread separe (voir run_in_threadpool), jamais directement dans une route async.
    """
    if not CLE_GEMINI:
        raise RuntimeError("GEMINI_API_KEY manquant dans plateforme_web/.env.")
    if not (prompt or "").strip():
        raise RuntimeError("Aucune description fournie pour la vidéo.")

    modele = MODELES.get(qualite, MODELES[QUALITE_DEFAUT])
    client = genai.Client(api_key=CLE_GEMINI)

    source = types.GenerateVideosSource(
        prompt=prompt,
        image=types.Image(image_bytes=image_depart, mime_type="image/jpeg") if image_depart else None,
    )
    operation = client.models.generate_videos(
        model=modele, source=source, config=types.GenerateVideosConfig(aspect_ratio=aspect_ratio, resolution="720p"),
    )

    debut = time.monotonic()
    while not operation.done:
        if time.monotonic() - debut > ATTENTE_MAX_SECONDES:
            raise RuntimeError("La génération a pris trop de temps (plus de 4 minutes) : réessayez.")
        time.sleep(ATTENTE_SECONDES)
        operation = client.operations.get(operation)

    if getattr(operation, "error", None):
        raise RuntimeError(f"Échec de la génération vidéo : {operation.error}")
    videos = getattr(operation.result, "generated_videos", None) or []
    if not videos:
        raise RuntimeError("Aucune vidéo n'a été générée (le sujet a peut-être été refusé par les filtres de contenu).")

    uri = videos[0].video.uri
    reponse = requests.get(uri, headers={"x-goog-api-key": CLE_GEMINI}, timeout=60)
    if reponse.status_code != 200:
        raise RuntimeError(f"Échec du téléchargement de la vidéo générée (code {reponse.status_code}).")
    return reponse.content
