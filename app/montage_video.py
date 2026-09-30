"""
Montage video simple (diaporama de photos avec effet de zoom lent, clip existant, ou combinaison des deux, texte
incruste, musique de fond) via ffmpeg - deuxieme brique video de la plateforme, apres la generation IA (voir
veo_video.py). ffmpeg est un binaire externe (pas une bibliotheque Python) : il doit etre installe sur la machine
(voir nixpacks.toml pour Railway ; en local, installe via `winget install Gyan.FFmpeg` ou equivalent).

Simplifications assumees pour cette version : le son d'origine d'une video de depart (footage reel televerse ou
genere par IA) n'est jamais conserve - seule la musique de fond choisie (si une l'est) sert de son, sinon le
montage est muet. Le texte incruste est une seule ligne courte (pas de retour a la ligne automatique), applique
sur l'ensemble du montage (pas par segment).
"""

import os
import subprocess
import tempfile

FFMPEG = os.getenv("FFMPEG_BINAIRE", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BINAIRE", "ffprobe")

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
POLICE_TEXTE = os.path.join(DOSSIER_APP, "static", "polices", "Poppins-Bold.ttf")

LARGEUR, HAUTEUR = 1080, 1920  # format vertical (Shorts / Reels / Stories)
FPS = 25
DUREE_PAR_PHOTO_DEFAUT = 3.0
DUREE_MAX_SEGMENT_VIDEO = 20.0  # securite : un clip de depart demesure ralentirait tout le montage pour rien
DUREE_FONDU_SEGMENT = 0.6  # fondu enchaine entre deux plans (photo ou video)
ZOOM_MAX_PHOTO = 1.15  # amplitude du zoom lent (Ken Burns) sur une photo - discret, pas un effet "gadget"
VOLUME_MUSIQUE = 0.25
DUREE_FONDU_MUSIQUE = 1.5
DUREE_MAX_TEXTE = 70


def _echapper_texte_ffmpeg(texte: str) -> str:
    texte = texte.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    return texte.replace("\n", " ").strip()[:DUREE_MAX_TEXTE]


def _echapper_chemin_ffmpeg(chemin: str) -> str:
    return chemin.replace("\\", "/").replace(":", "\\:")


def _executer_ffmpeg(args: list) -> None:
    try:
        resultat = subprocess.run([FFMPEG, "-y", *args], capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Le montage a pris trop de temps (fichier probablement invalide ou trop volumineux).")
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


def _segment_normalise(segment: dict, dossier: str, index: int, duree_par_photo: float) -> tuple:
    """
    Convertit un segment (photo ou video) en un clip normalise (1080x1920, meme cadence) pret a etre enchaine
    avec les autres. Une photo devient un plan avec un lent zoom (Ken Burns) plutot qu'une image figee. Renvoie
    (chemin_du_clip, duree_en_secondes).
    """
    chemin_sortie = os.path.join(dossier, f"seg_{index}.mp4")

    if segment["type"] == "photo":
        chemin_brut = os.path.join(dossier, f"brut_{index}.jpg")
        with open(chemin_brut, "wb") as f:
            f.write(segment["octets"])
        duree = duree_par_photo
        nb_frames = max(int(round(duree * FPS)), 1)
        filtre = (
            f"scale={LARGEUR * 2}:{HAUTEUR * 2}:force_original_aspect_ratio=increase,"
            f"crop={LARGEUR * 2}:{HAUTEUR * 2},"
            f"zoompan=z='min(zoom+0.0012,{ZOOM_MAX_PHOTO})':d={nb_frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={LARGEUR}x{HAUTEUR}:fps={FPS}"
        )
        _executer_ffmpeg([
            "-loop", "1", "-i", chemin_brut, "-frames:v", str(nb_frames),
            "-vf", filtre, "-pix_fmt", "yuv420p", chemin_sortie,
        ])
        return chemin_sortie, duree

    chemin_brut = os.path.join(dossier, f"brut_{index}.mp4")
    with open(chemin_brut, "wb") as f:
        f.write(segment["octets"])
    duree = min(duree_media(chemin_brut), DUREE_MAX_SEGMENT_VIDEO)
    filtre = f"scale={LARGEUR}:{HAUTEUR}:force_original_aspect_ratio=increase,crop={LARGEUR}:{HAUTEUR},fps={FPS}"
    _executer_ffmpeg([
        "-i", chemin_brut, "-t", str(duree), "-vf", filtre, "-an", "-pix_fmt", "yuv420p", chemin_sortie,
    ])
    return chemin_sortie, duree


def _assembler_segments(segments: list, dossier: str, duree_par_photo: float) -> str:
    """Normalise chaque segment puis les enchaine avec un fondu croise (xfade) - un seul segment : aucun fondu necessaire."""
    clips = [_segment_normalise(segment, dossier, i, duree_par_photo) for i, segment in enumerate(segments)]
    if len(clips) == 1:
        return clips[0][0]

    entrees = []
    for chemin, _ in clips:
        entrees += ["-i", chemin]

    cumul = clips[0][1]
    precedent = "0:v"
    parties_filtre = []
    for i in range(1, len(clips)):
        offset = max(cumul - DUREE_FONDU_SEGMENT, 0)
        etiquette = f"v{i}" if i < len(clips) - 1 else "vout"
        parties_filtre.append(
            f"[{precedent}][{i}:v]xfade=transition=fade:duration={DUREE_FONDU_SEGMENT}:offset={offset:.3f}[{etiquette}]"
        )
        cumul = offset + clips[i][1]
        precedent = etiquette

    chemin_concat = os.path.join(dossier, "concat.mp4")
    _executer_ffmpeg([
        *entrees, "-filter_complex", ";".join(parties_filtre), "-map", "[vout]",
        "-pix_fmt", "yuv420p", "-r", str(FPS), chemin_concat,
    ])
    return chemin_concat


def _filtre_texte(texte: str) -> str:
    police = _echapper_chemin_ffmpeg(POLICE_TEXTE)
    txt = _echapper_texte_ffmpeg(texte)
    return (
        f"drawtext=fontfile='{police}':text='{txt}':fontcolor=white:fontsize=64:"
        f"box=1:boxcolor=black@0.55:boxborderw=24:x=(w-text_w)/2:y=h-th-140"
    )


def monter_video(
    photos: list = None, video_base: bytes = None, videos: list = None, texte: str = "", musique: bytes = None,
    duree_par_photo: float = DUREE_PAR_PHOTO_DEFAUT,
) -> bytes:
    """
    Construit une courte video verticale (1080x1920) a partir d'une ou plusieurs videos existantes (clips Veo ou
    televerses - video_base pour un seul, videos pour plusieurs plans a enchainer) et/ou d'une liste de photos
    (chacune devenant un plan avec un leger zoom) - au moins l'un des trois est necessaire. L'ordre du montage est
    toujours : video_base, puis chaque element de videos, puis chaque photo - tous les plans relies par un fondu
    enchaine. Texte incruste et musique de fond en boucle (coupee en fondu a la fin) tous les deux facultatifs.
    Renvoie les octets MP4. Bloque le temps de l'encodage (quelques secondes a une minute ou deux selon le nombre
    de plans) : a appeler depuis un thread separe (voir run_in_threadpool), jamais directement dans une route async.
    """
    if not photos and not video_base and not videos:
        raise RuntimeError("Aucune photo ni vidéo de départ fournie.")

    segments = []
    if video_base:
        segments.append({"type": "video", "octets": video_base})
    for octets in (videos or []):
        segments.append({"type": "video", "octets": octets})
    for octets in (photos or []):
        segments.append({"type": "photo", "octets": octets})

    with tempfile.TemporaryDirectory() as dossier:
        chemin_video = _assembler_segments(segments, dossier, duree_par_photo)

        chemin_sortie = os.path.join(dossier, "sortie.mp4")
        filtre_video = _filtre_texte(texte) if texte.strip() else None

        if musique:
            chemin_musique = os.path.join(dossier, "musique.mp3")
            with open(chemin_musique, "wb") as f:
                f.write(musique)
            duree = duree_media(chemin_video)
            fin_fondu = max(duree - DUREE_FONDU_MUSIQUE, 0)
            filtre_v = f"[0:v]{filtre_video}[v];" if filtre_video else "[0:v]copy[v];"
            filtre_complexe = (
                f"{filtre_v}"
                f"[1:a]volume={VOLUME_MUSIQUE},afade=t=out:st={fin_fondu}:d={DUREE_FONDU_MUSIQUE}[a]"
            )
            args = [
                "-i", chemin_video, "-stream_loop", "-1", "-i", chemin_musique,
                "-filter_complex", filtre_complexe, "-map", "[v]", "-map", "[a]", "-shortest",
                "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
            ]
        elif filtre_video:
            args = ["-i", chemin_video, "-vf", filtre_video, "-an", "-c:v", "libx264"]
        else:
            args = ["-i", chemin_video, "-an", "-c:v", "libx264"]

        args += ["-pix_fmt", "yuv420p", "-preset", "veryfast", "-movflags", "+faststart", chemin_sortie]
        _executer_ffmpeg(args)

        with open(chemin_sortie, "rb") as f:
            return f.read()
