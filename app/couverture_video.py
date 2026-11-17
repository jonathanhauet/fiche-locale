"""
Couverture d'une video publiee sur Facebook / Instagram : l'image affichee dans le fil et sur la grille du profil.

Sans couverture fournie, Meta prend la premiere image de la video - souvent un fond blanc ou noir d'introduction (ex. un
dessin sur fond blanc), qui donne une vignette « vide » dans le fil. On extrait donc une image avec ffmpeg :
- au moment choisi par l'utilisateur (curseur du bloc « Couverture » du composeur ou de la page de modification) ;
- sinon automatiquement : la premiere image « nette » des premieres secondes, en sautant celles qui sont surtout
  blanches, surtout noires ou unies (meme regle que l'apercu dans le navigateur, voir _choix_couverture_video.html).
Tout echec (ffmpeg absent, video illisible) renvoie None : la publication part alors sans couverture, comme avant.
"""

import os
import statistics
import subprocess
import tempfile

# Analyse automatique : 4 images par seconde sur les premieres secondes, reduites a une petite grille de gris.
DUREE_ANALYSE_SECONDES = 10
IMAGES_PAR_SECONDE = 4
LARGEUR_ANALYSE, HAUTEUR_ANALYSE = 32, 56
SEUIL_CLAIR, SEUIL_SOMBRE = 225, 30           # un pixel « blanc » / « noir » (niveau de gris 0-255)
PART_MAX_CLAIRE, PART_MAX_SOMBRE = 0.45, 0.60  # au-dela, l'image passe pour un fond blanc / noir
ECART_TYPE_MIN = 18                            # en dessous, image quasi unie (fondu, ecran de titre vide)


def _ffmpeg() -> str:
    from . import montage_video
    return montage_video.FFMPEG


def image_est_nette(pixels: bytes) -> bool:
    """pixels : niveaux de gris (0-255) d'une image reduite. Faux si elle est surtout blanche, surtout noire ou unie."""
    if not pixels:
        return False
    n = len(pixels)
    part_claire = sum(1 for p in pixels if p >= SEUIL_CLAIR) / n
    part_sombre = sum(1 for p in pixels if p <= SEUIL_SOMBRE) / n
    return part_claire <= PART_MAX_CLAIRE and part_sombre <= PART_MAX_SOMBRE and statistics.pstdev(pixels) >= ECART_TYPE_MIN


def moment_automatique(chemin_video: str) -> float:
    """Instant (secondes) de la premiere image nette des premieres secondes ; 0 si aucune ne convient ou si l'analyse echoue."""
    try:
        sortie = subprocess.run(
            [_ffmpeg(), "-v", "error", "-t", str(DUREE_ANALYSE_SECONDES), "-i", chemin_video,
             "-vf", f"fps={IMAGES_PAR_SECONDE},scale={LARGEUR_ANALYSE}:{HAUTEUR_ANALYSE},format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    taille = LARGEUR_ANALYSE * HAUTEUR_ANALYSE
    nettes = [image_est_nette(sortie[i * taille:(i + 1) * taille]) for i in range(len(sortie) // taille)]
    # Deux echantillons nets de suite, et on garde le second : une image prise pile sur une transition (fondu, coupe)
    # peut encore montrer le plan precedent une fois extraite a cet instant exact.
    for indice in range(len(nettes) - 1):
        if nettes[indice] and nettes[indice + 1]:
            return (indice + 1) / IMAGES_PAR_SECONDE
    return next((i / IMAGES_PAR_SECONDE for i, nette in enumerate(nettes) if nette), 0.0)


def image_a(chemin_video: str, secondes: float):
    """Image JPEG (octets) de la video a l'instant donne, ou None si ffmpeg echoue. Un instant au-dela de la fin donne la derniere image."""
    for depart in (max(0.0, secondes), None):
        commande = [_ffmpeg(), "-v", "error"]
        commande += ["-ss", f"{depart:.3f}", "-i", chemin_video] if depart is not None else ["-sseof", "-0.1", "-i", chemin_video]
        commande += ["-frames:v", "1", "-q:v", "2", "-f", "image2", "-vcodec", "mjpeg", "-"]
        try:
            octets = subprocess.run(commande, capture_output=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if octets:
            return octets
    return None


def couverture(octets_video: bytes, secondes=None):
    """
    (jpeg, instant) : image de couverture tiree de la video - a l'instant demande, sinon choisie automatiquement.
    (None, None) si l'extraction echoue.
    """
    if not octets_video:
        return None, None
    descripteur, chemin = tempfile.mkstemp(suffix=".mp4")
    try:
        with os.fdopen(descripteur, "wb") as fichier:
            fichier.write(octets_video)
        instant = moment_automatique(chemin) if secondes is None else float(secondes)
        jpeg = image_a(chemin, instant)
        return (jpeg, instant) if jpeg else (None, None)
    finally:
        try:
            os.remove(chemin)
        except OSError:
            pass


def lire_instant(valeur):
    """Instant saisi dans le formulaire (« 3.25 ») -> float >= 0, ou None (automatique) si vide ou invalide."""
    try:
        instant = float(str(valeur or "").replace(",", ".").strip())
    except ValueError:
        return None
    return instant if instant >= 0 else None
