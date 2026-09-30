"""
Montage video simple (diaporama de photos ou clip existant, texte incruste, musique de fond) via ffmpeg - deuxieme
brique video de la plateforme, apres la generation IA (voir veo_video.py). ffmpeg est un binaire externe (pas une
bibliotheque Python) : il doit etre installe sur la machine (voir nixpacks.toml pour Railway ; en local, installe
via `winget install Gyan.FFmpeg` ou equivalent).

Simplifications assumees pour cette premiere version : le son d'origine d'une video de depart (footage reel
televerse) n'est jamais conserve - seule la musique de fond choisie (si une l'est) sert de son, sinon la video est
muette. Le texte incruste est une seule ligne courte (pas de retour a la ligne automatique).
"""

import os
import subprocess
import tempfile

FFMPEG = os.getenv("FFMPEG_BINAIRE", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BINAIRE", "ffprobe")

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
POLICE_TEXTE = os.path.join(DOSSIER_APP, "static", "polices", "Poppins-Bold.ttf")

LARGEUR, HAUTEUR = 1080, 1920  # format vertical (Shorts / Reels / Stories)
DUREE_PAR_PHOTO_DEFAUT = 3.0
VOLUME_MUSIQUE = 0.25
DUREE_FONDU_MUSIQUE = 1.5
DUREE_MAX_TEXTE = 70


def _echapper_texte_ffmpeg(texte: str) -> str:
    texte = texte.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    return texte.replace("\n", " ").strip()[:DUREE_MAX_TEXTE]


def _echapper_chemin_ffmpeg(chemin: str) -> str:
    return chemin.replace("\\", "/").replace(":", "\\:")


def _executer_ffmpeg(args: list) -> None:
    resultat = subprocess.run([FFMPEG, "-y", *args], capture_output=True, timeout=180)
    if resultat.returncode != 0:
        raise RuntimeError(f"Echec du montage vidéo : {resultat.stderr.decode('utf-8', 'ignore')[-600:]}")


def duree_media(chemin: str) -> float:
    """Duree en secondes d'un fichier audio ou video (via ffprobe)."""
    resultat = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", chemin],
        capture_output=True, timeout=30,
    )
    try:
        return float(resultat.stdout.decode().strip())
    except ValueError:
        return 8.0


def duree_octets_audio(octets: bytes) -> float:
    """Duree en secondes d'un fichier audio fourni en octets (ecrit dans un fichier temporaire pour ffprobe)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(octets)
        chemin = f.name
    try:
        return duree_media(chemin)
    finally:
        os.unlink(chemin)


def _diaporama(photos: list, dossier: str, duree_par_photo: float) -> str:
    """Assemble une liste de photos (octets) en une video (un plan fixe par photo), via le demuxeur concat."""
    chemins = []
    for i, octets in enumerate(photos):
        chemin_brut = os.path.join(dossier, f"photo_{i}.jpg")
        with open(chemin_brut, "wb") as f:
            f.write(octets)
        chemins.append(chemin_brut)

    chemin_liste = os.path.join(dossier, "liste.txt")
    with open(chemin_liste, "w", encoding="utf-8") as f:
        for chemin in chemins:
            f.write(f"file '{os.path.basename(chemin)}'\n")
            f.write(f"duration {duree_par_photo}\n")
        f.write(f"file '{os.path.basename(chemins[-1])}'\n")  # repete la derniere image : quirk du demuxeur concat

    chemin_video = os.path.join(dossier, "diaporama.mp4")
    _executer_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", chemin_liste,
        "-r", "25", "-pix_fmt", "yuv420p", chemin_video,
    ])
    return chemin_video


def _filtre_texte(texte: str) -> str:
    police = _echapper_chemin_ffmpeg(POLICE_TEXTE)
    txt = _echapper_texte_ffmpeg(texte)
    return (
        f"drawtext=fontfile='{police}':text='{txt}':fontcolor=white:fontsize=64:"
        f"box=1:boxcolor=black@0.55:boxborderw=24:x=(w-text_w)/2:y=h-th-140"
    )


def monter_video(
    photos: list = None, video_base: bytes = None, texte: str = "", musique: bytes = None,
    duree_par_photo: float = DUREE_PAR_PHOTO_DEFAUT,
) -> bytes:
    """
    Construit une courte video verticale (1080x1920) a partir soit d'une liste de photos (diaporama, un plan fixe
    par photo), soit d'une video existante (clip Veo ou televersee) - l'un des deux est obligatoire. Texte incruste
    en bas et musique de fond en boucle (coupee en fondu a la fin de la video) tous les deux facultatifs. Renvoie
    les octets MP4. Bloque le temps de l'encodage (quelques secondes a une minute selon la duree) : a appeler
    depuis un thread separe (voir run_in_threadpool), jamais directement dans une route async.
    """
    if not photos and not video_base:
        raise RuntimeError("Aucune photo ni vidéo de départ fournie.")

    with tempfile.TemporaryDirectory() as dossier:
        if video_base:
            chemin_video = os.path.join(dossier, "source.mp4")
            with open(chemin_video, "wb") as f:
                f.write(video_base)
        else:
            chemin_video = _diaporama(photos, dossier, duree_par_photo)

        chemin_sortie = os.path.join(dossier, "sortie.mp4")
        filtres = [f"scale={LARGEUR}:{HAUTEUR}:force_original_aspect_ratio=increase,crop={LARGEUR}:{HAUTEUR}"]
        if texte.strip():
            filtres.append(_filtre_texte(texte))
        filtre_video = ",".join(filtres)

        if musique:
            chemin_musique = os.path.join(dossier, "musique.mp3")
            with open(chemin_musique, "wb") as f:
                f.write(musique)
            duree = duree_media(chemin_video)
            fin_fondu = max(duree - DUREE_FONDU_MUSIQUE, 0)
            filtre_complexe = (
                f"[0:v]{filtre_video}[v];"
                f"[1:a]volume={VOLUME_MUSIQUE},afade=t=out:st={fin_fondu}:d={DUREE_FONDU_MUSIQUE}[a]"
            )
            args = [
                "-i", chemin_video, "-stream_loop", "-1", "-i", chemin_musique,
                "-filter_complex", filtre_complexe, "-map", "[v]", "-map", "[a]", "-shortest",
                "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
            ]
        else:
            args = ["-i", chemin_video, "-vf", filtre_video, "-an", "-c:v", "libx264"]

        args += ["-pix_fmt", "yuv420p", "-preset", "veryfast", "-movflags", "+faststart", chemin_sortie]
        _executer_ffmpeg(args)

        with open(chemin_sortie, "rb") as f:
            return f.read()
