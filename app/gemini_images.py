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

# Photos de reference de plusieurs agences d'une meme marque (facades, interieurs, equipes) : elles ne donnent que l'ambiance. Aucun logo, aucune
# enseigne, aucun texte ne doit etre recopie (un faux logo ou un mot deforme pourrait porter prejudice a la marque), et personne ne doit ressembler
# aux vraies personnes des photos.
INSTRUCTION_AGENCES = (
    "The attached photos show several real agencies of the same brand (storefronts, interiors, reception desks, displays). Use them ONLY as "
    "inspiration for the overall atmosphere: the color palette, the materials, the furniture, the lighting and the level of finish. Create a NEW "
    "photorealistic image of an agency in that same style, as a candid, natural photo. STRICT RULES ABOUT WRITING AND BRANDS: do NOT reproduce, copy "
    "or invent any logo, brand mark, brand emblem, sign, shop sign, lettering, text, number or symbol. Achieve this by COMPOSING the shot so that "
    "nothing carrying writing is in view: screens switched off (dark glass) or turned away from the camera, any license plate and vehicle badge kept "
    "out of frame by choosing an angle or a crop that hides them, no posters or signs, no printed documents, plain clothing without prints. Never "
    "leave conspicuous empty white rectangles (blank screens, blank plates, blank posters): a surface that must stay visible looks natural, dark or "
    "softly out of focus. Any person in the image must be a generic person who does not resemble the people in the attached photos. "
    "Scene to depict: "
)

# Variante libre (reglage par defaut des images d'agences) : le logo et l'enseigne de la marque, ainsi que du texte, peuvent apparaitre ; seules les vraies
# personnes des photos ne doivent pas etre reproduites. Le mode prudent (INSTRUCTION_AGENCES) interdit au contraire tout ecrit et toute marque.
INSTRUCTION_AGENCES_LIBRE = (
    "The attached photos show several real agencies of the same brand (storefronts, interiors, reception desks, displays). Use them as the reference "
    "for the brand's look: the color palette, the materials, the furniture, the lighting, the level of finish and the style of the signage. Create a NEW "
    "photorealistic image of an agency in that same style, as a candid, natural photo. The brand's logo and signage may appear where they would "
    "naturally be (storefront sign, reception wall, screens, displays) ONLY when the official logo image is attached to this request (it is then copied "
    "exactly, see the OFFICIAL LOGO rule); you must never redraw, approximate or invent a logo from the photos. Any other text must be "
    "short and spelled correctly. Any person in the image must be a generic person who does not resemble the people in the attached photos. "
    "Scene to depict: "
)

# Les prompts de post demandent souvent "no recognizable faces" / "no people" : contradictoire
# (et prioritaire, selon Gemini) quand on veut justement qu'une personne apparaisse.
_INTERDICTIONS_VISAGE = re.compile(
    r"(?:,\s*|\.\s*|\s+)?\b(?:no|without|avoid(?:ing)?|zero)\s+(?:any\s+)?"
    r"(?:(?:recogni[sz]able|identifiable|visible|human|real)\s+)*"
    r"(?:faces?|people|persons?|humans?|individuals?)\b[^,.;]*|\bfaceless\b",
    re.IGNORECASE,
)


def _prompt_avec_personne(prompt_image: str, instruction: str = INSTRUCTION_PERSONNE, nettoyer_interdictions_visage: bool = True) -> str:
    """
    Prefixe le prompt d'une consigne explicite sur les photos de reference et, pour un sujet humain, en retire les interdictions de visage
    (contradictoires avec « la personne apparait »). Pour les lieux (nettoyer_interdictions_visage=False), le prompt reste intact.
    """
    if not nettoyer_interdictions_visage:
        return f"{instruction}{prompt_image.strip()}"
    nettoye = re.sub(r"\s*,(?:\s*,)+", ",", _INTERDICTIONS_VISAGE.sub("", prompt_image)).strip(" ,.")
    return f"{instruction}{nettoye}"


CONSIGNE_SANS_TEXTE = (
    "\n\nIMPORTANT: the image must contain absolutely no text: no letters, words, numbers, labels, captions, "
    "signs or writing of any kind anywhere in the picture (use plain shapes, icons and arrows instead)."
)

# Jamais de logo ni de marque dessines : meme quand quelques mots sont autorises (un logo approximatif peut porter prejudice a une entreprise).
CONSIGNE_SANS_LOGO = (
    "\n\nIMPORTANT: do not draw any logo, brand mark, brand emblem (car badges included), trademark or shop sign anywhere. Compose the shot so "
    "that nothing carrying writing is visible: screens switched off or turned away, license plates and badges out of frame, no posters or signs. "
    "Never show conspicuous blank white rectangles (empty plates, blank screens): keep such surfaces out of view, or natural and out of focus."
)


# Logo officiel de la charte graphique du client, joint en DERNIERE image : seul logo que l'IA a le droit de montrer, recopie a l'identique (jamais un logo invente).
CONSIGNE_LOGO_CHARTE = (
    "\n\nOFFICIAL LOGO: the LAST attached image is this brand's official logo. If a logo appears anywhere in the scene (sign, wall, screen, clothing, "
    "document), it must be an exact copy of that logo: same shapes, colors and wording, never a redrawn or invented one. Do not show any other logo or "
    "brand mark, and no framed picture, poster or card carrying a made-up name or logo. If you cannot reproduce it faithfully, keep logos out of view."
)


def generer_image(
    prompt_image: str, aspect_ratio: str = None, images_reference: list[bytes] = None, texte_autorise: bool = False,
    instruction_personne: str = INSTRUCTION_PERSONNE, nettoyer_interdictions_visage: bool = True, sans_logo: bool = False, logo: bytes = None,
) -> bytes:
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
    # Par defaut jamais de texte dans l'image : Gemini l'invente en anglais et avec des fautes. Le texte n'est
    # autorise que si le prompt en fournit lui-meme les mots exacts (option « mots en francais » du composeur).
    if not texte_autorise:
        prompt_image = prompt_image + CONSIGNE_SANS_TEXTE
    if sans_logo:
        prompt_image = prompt_image + CONSIGNE_SANS_LOGO
    if logo:
        prompt_image = prompt_image + CONSIGNE_LOGO_CHARTE
    pieces = lambda liste: [{"type": "image", "data": base64.b64encode(octets).decode("utf-8"), "mime_type": "image/jpeg"} for octets in liste]
    if images_reference:
        entree = [{"type": "text", "text": _prompt_avec_personne(prompt_image, instruction_personne, nettoyer_interdictions_visage)}] + pieces(list(images_reference) + ([logo] if logo else []))
    elif logo:
        entree = [{"type": "text", "text": prompt_image}] + pieces([logo])
    else:
        entree = prompt_image
    parametres = {"model": MODELE_GEMINI, "input": entree}
    if aspect_ratio:
        parametres["response_format"] = {"type": "image", "aspect_ratio": aspect_ratio}
    interaction = client.interactions.create(**parametres)
    return base64.b64decode(interaction.output_image.data)
