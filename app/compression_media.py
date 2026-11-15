"""
Compresseur de photos et de videos : plusieurs fichiers a la fois, plusieurs formats de sortie, taille de sortie reglable.

Taille de sortie, au choix : un poids maximal par fichier (ex. 500 Ko, 20 Mo), un pourcentage du poids d'origine, ou une qualite fixe. Pour une taille cible,
les photos sont recherchees par dichotomie sur la qualite (puis reduites si la qualite seule ne suffit pas) et les videos sont encodees en deux passes avec un
debit calcule d'apres la duree (resolution reduite automatiquement quand le poids demande est tres bas).

Rien n'est garde dans la base : chaque lot vit dans un dossier temporaire sous un identifiant secret, avec ses fichiers d'origine et ses resultats, et disparait
a la fermeture ou au bout de quelques heures (purger).
"""

import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps, features

try:                                   # photos d'iPhone (HEIC) : lues et ecrites si la bibliotheque est installee
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_POSSIBLE = True
except Exception:                      # pragma: no cover - dependance optionnelle
    HEIC_POSSIBLE = False

RACINE = os.path.join(tempfile.gettempdir(), "compresseur_media_fiche_locale")
DUREE_VIE_SECONDES = 4 * 3600
MAX_FICHIERS = 40
TAILLE_MAX_PHOTO = 120 * 1024 * 1024
TAILLE_MAX_VIDEO = 800 * 1024 * 1024
TAILLE_MAX_LOT = 2 * 1024 * 1024 * 1024
MAX_LOTS = 12
DUREE_MAX_VIDEO_SECONDES = 3 * 3600
DELAI_MAX_ENCODAGE = 3 * 3600
PIXELS_MAX = 120_000_000
Image.MAX_IMAGE_PIXELS = PIXELS_MAX

EXTENSIONS_PHOTO = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "avif", "heic", "heif"}
EXTENSIONS_VIDEO = {"mp4", "mov", "m4v", "mkv", "avi", "webm", "wmv", "flv", "3gp", "mts", "m2ts", "mpg", "mpeg", "ogv", "ts"}

FORMATS_PHOTO = {"jpg": ("JPEG", "jpg", "Photo JPG (le plus compatible)"), "webp": ("WEBP", "webp", "WebP (plus léger, très répandu)"),
                 "png": ("PNG", "png", "PNG (sans perte, transparence)"), "avif": ("AVIF", "avif", "AVIF (le plus léger, récent)")}
FORMATS_VIDEO = {"mp4": ("libx264", "mp4", "MP4 H.264 (compatible partout)"), "mp4_h265": ("libx265", "mp4", "MP4 H.265 (≈ 30 % plus léger, moins compatible)"),
                 "webm": ("libvpx-vp9", "webm", "WebM VP9 (web, Android)")}
VITESSES = {"rapide": "veryfast", "equilibre": "medium", "lent": "slow"}
NIVEAUX_QUALITE_VIDEO = {"haute": {"libx264": 20, "libx265": 24, "libvpx-vp9": 28}, "equilibree": {"libx264": 24, "libx265": 28, "libvpx-vp9": 33},
                         "legere": {"libx264": 29, "libx265": 33, "libvpx-vp9": 38}}
HAUTEURS_VIDEO = (0, 2160, 1440, 1080, 720, 480, 360)         # 0 = pas de limite
COTES_PHOTO = (0, 3840, 2560, 1920, 1600, 1200, 800)


class ErreurMedia(Exception):
    """Erreur a montrer telle quelle a l'utilisateur."""


# ---------------------------------------------------------------------------------------------------------------- capacites

def formats_photo_disponibles() -> dict:
    ok = {"jpg": True, "webp": features.check("webp"), "png": True, "avif": features.check("avif")}
    return {k: v for k, v in FORMATS_PHOTO.items() if ok[k]}


def _ffmpeg() -> str:
    from . import montage_video
    return montage_video.FFMPEG


_encodeurs_cache = {}


def encodeurs_video() -> set:
    """Encodeurs video disponibles dans le ffmpeg du serveur (certaines versions n'ont pas H.265 ou VP9)."""
    if "v" not in _encodeurs_cache:
        try:
            sortie = subprocess.run([_ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout
        except Exception:
            sortie = ""
        _encodeurs_cache["v"] = {nom for nom in ("libx264", "libx265", "libvpx-vp9", "libopus", "aac", "libvorbis") if re.search(r"\s" + re.escape(nom) + r"\s", sortie)}
    return _encodeurs_cache["v"]


def formats_video_disponibles() -> dict:
    enc = encodeurs_video()
    audio_mp4, audio_webm = "aac" in enc, "libopus" in enc or "libvorbis" in enc
    return {k: v for k, v in FORMATS_VIDEO.items() if v[0] in enc and (audio_webm if v[1] == "webm" else audio_mp4)}


def type_de_fichier(nom: str) -> str:
    ext = nom.rsplit(".", 1)[-1].lower() if "." in nom else ""
    if ext in EXTENSIONS_PHOTO:
        if ext in ("heic", "heif") and not HEIC_POSSIBLE:
            raise ErreurMedia("Les photos HEIC (iPhone) ne sont pas prises en charge sur ce serveur.")
        return "photo"
    if ext in EXTENSIONS_VIDEO:
        return "video"
    raise ErreurMedia(f"Format non pris en charge : « {nom} » (photos : JPG, PNG, WebP, HEIC, GIF, BMP, TIFF, AVIF ; vidéos : MP4, MOV, MKV, AVI, WebM, WMV, 3GP…).")


# ---------------------------------------------------------------------------------------------------------------- reglages

def lire_reglages(brut: dict) -> dict:
    """Valide et complete les reglages recus (tout est borne : rien de ce qui vient du navigateur n'est utilise tel quel)."""
    brut = brut if isinstance(brut, dict) else {}

    def nombre(valeur, defaut, mini, maxi):
        try:
            v = float(valeur)
        except (TypeError, ValueError):
            return defaut
        return max(mini, min(v, maxi)) if not math.isnan(v) else defaut

    def choix(valeur, autorises, defaut):
        return valeur if valeur in autorises else defaut

    photo = brut.get("photo") if isinstance(brut.get("photo"), dict) else {}
    video = brut.get("video") if isinstance(brut.get("video"), dict) else {}
    return {
        "photo": {
            "format": choix(photo.get("format"), ["origine", *FORMATS_PHOTO], "jpg"),
            "mode": choix(photo.get("mode"), ["taille", "pourcentage", "qualite"], "taille"),
            "cible_octets": int(nombre(photo.get("cible_octets"), 500 * 1024, 5 * 1024, 200 * 1024 * 1024)),
            "pourcentage": nombre(photo.get("pourcentage"), 30, 1, 95),
            "qualite": int(nombre(photo.get("qualite"), 75, 1, 100)),
            "cote_max": int(choix(int(nombre(photo.get("cote_max"), 0, 0, 20000)), COTES_PHOTO, 0)),
            "metadonnees": bool(photo.get("metadonnees")),
        },
        "video": {
            "format": choix(video.get("format"), list(FORMATS_VIDEO), "mp4"),
            "mode": choix(video.get("mode"), ["taille", "pourcentage", "qualite"], "taille"),
            "cible_octets": int(nombre(video.get("cible_octets"), 20 * 1024 * 1024, 50 * 1024, 4 * 1024 ** 3)),
            "pourcentage": nombre(video.get("pourcentage"), 30, 1, 95),
            "niveau": choix(video.get("niveau"), list(NIVEAUX_QUALITE_VIDEO), "equilibree"),
            "hauteur_max": int(choix(int(nombre(video.get("hauteur_max"), -1, -1, 5000)), (-1, *HAUTEURS_VIDEO), -1)),       # -1 = automatique
            "son": choix(video.get("son"), ["garder", "retirer"], "garder"),
            "vitesse": choix(video.get("vitesse"), list(VITESSES), "equilibre"),
        },
    }


# ---------------------------------------------------------------------------------------------------------------- photos

def _ouvrir_photo(chemin: str) -> tuple:
    """(image orientee comme l'original, message). Une image animee (GIF, WebP) est ramenee a sa premiere image."""
    try:
        image = Image.open(chemin)
        anime = getattr(image, "is_animated", False)
        image.load()
    except Exception:
        raise ErreurMedia("Image illisible ou corrompue.")
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    message = "Image animée : seule la première image est conservée." if anime else ""
    return image, message


def _vers_srgb(image: Image.Image) -> Image.Image:
    """Convertit en sRGB si l'image a un profil couleur (photos d'iPhone en Display P3) : sinon les couleurs paraissent ternes une fois le profil retire."""
    icc = image.info.get("icc_profile")
    if not icc:
        return image
    try:
        from PIL import ImageCms
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.profileToProfile(image, source, ImageCms.createProfile("sRGB"), outputMode=image.mode if image.mode in ("RGB", "RGBA", "L") else "RGB")
    except Exception:
        return image


def _encoder_photo(image: Image.Image, format_pillow: str, qualite: int, exif=None) -> bytes:
    tampon = io.BytesIO()
    options = {}
    if format_pillow == "JPEG":
        options = {"quality": qualite, "optimize": True, "progressive": True, "subsampling": "4:2:0" if qualite < 90 else "4:4:4"}
    elif format_pillow == "WEBP":
        options = {"quality": qualite, "method": 5}
    elif format_pillow == "AVIF":
        options = {"quality": qualite, "speed": 7}
    elif format_pillow == "PNG":
        options = {"optimize": True, "compress_level": 9}
    if exif:
        options["exif"] = exif
    image.save(tampon, format_pillow, **options)
    return tampon.getvalue()


def _preparer_mode(image: Image.Image, format_pillow: str) -> Image.Image:
    """Mode couleur accepte par le format : le JPEG n'a pas de transparence (fond blanc), les palettes sont developpees."""
    if format_pillow == "JPEG":
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            fond = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            fond.paste(rgba, mask=rgba.split()[-1])
            return fond
        return image.convert("RGB") if image.mode != "RGB" else image
    if image.mode in ("P", "CMYK", "I;16", "I", "F"):
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    return image


def _reduire(image: Image.Image, cote_max: int) -> Image.Image:
    plus_grand = max(image.size)
    if cote_max and plus_grand > cote_max:
        k = cote_max / plus_grand
        return image.resize((max(1, round(image.width * k)), max(1, round(image.height * k))), Image.LANCZOS)
    return image


def compresser_photo(source: str, sortie_sans_extension: str, reglages: dict, taille_origine: int) -> dict:
    """
    Compresse une photo. Renvoie {"chemin", "format", "taille", "largeur", "hauteur", "qualite", "message"}.
    Mode « taille » ou « pourcentage » : meilleure qualite qui tient dans la taille cible, puis reduction des dimensions si la qualite seule ne suffit pas.
    """
    r = reglages["photo"]
    image, message = _ouvrir_photo(source)
    format_origine = (Image.open(source).format or "JPEG").upper()
    if r["format"] == "origine":
        cle = {"JPEG": "jpg", "WEBP": "webp", "PNG": "png", "AVIF": "avif", "MPO": "jpg"}.get(format_origine, "jpg")
        if cle not in formats_photo_disponibles():
            cle = "jpg"
    else:
        cle = r["format"]
    if cle not in formats_photo_disponibles():
        raise ErreurMedia("Ce format de sortie n'est pas disponible sur ce serveur.")
    format_pillow, extension, _ = FORMATS_PHOTO[cle]
    exif = None
    if r["metadonnees"]:
        try:
            exif = image.getexif().tobytes() or None
        except Exception:
            exif = None
    image = _vers_srgb(image)
    image = _preparer_mode(_reduire(image, r["cote_max"]), format_pillow)
    sans_qualite = format_pillow == "PNG"

    if r["mode"] == "qualite":
        octets = _encoder_photo(image, format_pillow, r["qualite"], exif)
        qualite = r["qualite"]
    else:
        cible = r["cible_octets"] if r["mode"] == "taille" else int(taille_origine * r["pourcentage"] / 100)
        cible = max(cible, 5 * 1024)
        octets, qualite, image, avertissement = _viser_taille(image, format_pillow, cible, exif)
        message = (message + " " + avertissement).strip()
    chemin = f"{sortie_sans_extension}.{extension}"
    with open(chemin, "wb") as f:
        f.write(octets)
    return {"chemin": chemin, "format": extension.upper(), "taille": len(octets), "largeur": image.width, "hauteur": image.height,
            "qualite": None if sans_qualite else qualite, "message": message}


QUALITE_MIN = {"JPEG": 30, "WEBP": 30, "AVIF": 25}
COTE_MIN = 160


def _meilleur_encodage(image: Image.Image, format_pillow: str, cible: int, exif) -> tuple:
    """(octets, qualite, image encodee) : la plus haute qualite qui tient sous `cible` ; si meme la plus basse depasse, le plus petit essai (pour juger de la reduction a faire)."""
    if format_pillow == "PNG":
        simple = _encoder_photo(image, "PNG", 0, exif)
        if len(simple) <= cible or image.mode not in ("RGB", "RGBA"):
            return simple, 0, image
        try:                                                           # palette de 256 couleurs : bien plus leger pour une image peu complexe
            palette = image.quantize(256, method=Image.Quantize.FASTOCTREE if image.mode == "RGBA" else Image.Quantize.MEDIANCUT)
            reduit = _encoder_photo(palette, "PNG", 0, exif)
            if len(reduit) < len(simple):
                return reduit, 0, palette
        except Exception:
            pass
        return simple, 0, image
    bas, haut = QUALITE_MIN[format_pillow], 95
    plus_bas = _encoder_photo(image, format_pillow, bas, exif)
    if len(plus_bas) > cible:
        return plus_bas, bas, image
    meilleur = (plus_bas, bas)
    bas += 1
    while bas <= haut:                                                 # dichotomie : la plus haute qualite qui tient
        milieu = (bas + haut) // 2
        essai = _encoder_photo(image, format_pillow, milieu, exif)
        if len(essai) <= cible:
            meilleur = (essai, milieu)
            bas = milieu + 1
        else:
            haut = milieu - 1
    return meilleur[0], meilleur[1], image


def _viser_taille(image: Image.Image, format_pillow: str, cible: int, exif) -> tuple:
    """(octets, qualite, image finale, avertissement) : le plus de qualite possible sous `cible` octets, en reduisant l'image si la qualite seule ne suffit pas."""
    echelle = 1.0
    octets, qualite, finale = b"", 0, image
    for etape in range(14):
        courante = image if echelle >= 0.999 else image.resize((max(1, round(image.width * echelle)), max(1, round(image.height * echelle))), Image.LANCZOS)
        octets, qualite, finale = _meilleur_encodage(courante, format_pillow, cible, exif)
        if len(octets) <= cible:
            return octets, qualite, finale, ("" if etape == 0 else f"Image réduite à {courante.width}×{courante.height} px pour tenir dans la taille demandée.")
        if max(courante.size) <= COTE_MIN:
            break
        echelle *= max(0.5, min(0.93, math.sqrt(cible / len(octets)) * 0.95))
    return octets, qualite, finale, "La taille demandée est trop basse pour cette image : elle a été réduite au maximum sans l'atteindre."


# ---------------------------------------------------------------------------------------------------------------- videos

def sonder_video(chemin: str) -> dict:
    """Duree (s), definition, presence d'une piste audio (le ffmpeg embarque n'a pas ffprobe : on lit la sortie de « ffmpeg -i »)."""
    try:
        sortie = subprocess.run([_ffmpeg(), "-hide_banner", "-i", chemin], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120).stderr
    except Exception:
        raise ErreurMedia("Impossible de lire cette vidéo.")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", sortie)
    duree = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    v = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", sortie)
    fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", sortie)
    if not v or not duree:
        raise ErreurMedia("Cette vidéo est illisible ou son format n'est pas reconnu.")
    rotation = re.search(r"rotate\s*:\s*(-?\d+)", sortie) or re.search(r"displaymatrix:\s*rotation of\s*(-?\d+)", sortie)
    largeur, hauteur = int(v.group(1)), int(v.group(2))
    if rotation and abs(int(rotation.group(1))) in (90, 270):
        largeur, hauteur = hauteur, largeur
    return {"duree": duree, "largeur": largeur, "hauteur": hauteur, "audio": bool(re.search(r"Stream #\d+:\d+.*Audio:", sortie)), "fps": min(60.0, float(fps.group(1))) if fps else 30.0}


def _hauteur_pour_debit(debit_video: float, largeur: int, hauteur: int, fps: float, bpp_mini: float) -> int:
    """Plus grande hauteur (<= celle de la source) dont le debit par pixel reste correct ; en dessous de ce debit l'image devient pateuse."""
    h = hauteur
    while h > 240 and debit_video / (max(2, round(largeur * h / hauteur)) * h * fps) < bpp_mini:
        plus_petites = [s for s in (2160, 1440, 1080, 720, 480, 360, 240) if s < h]
        h = plus_petites[0] if plus_petites else 240
    return min(h, hauteur)


BPP_MINI = {"libx264": 0.045, "libx265": 0.03, "libvpx-vp9": 0.035}


def plan_video(info: dict, reglages: dict, taille_origine: int) -> dict:
    """
    Calcule les parametres d'encodage : encodeur, debit (mode taille), CRF (mode qualite), hauteur de sortie, audio.
    Renvoie {"encodeur", "ext", "debit_video", "debit_audio", "crf", "hauteur", "son", "message"}.
    """
    r = reglages["video"]
    encodeur, extension, _ = FORMATS_VIDEO[r["format"]]
    son = info["audio"] and r["son"] == "garder"
    plan = {"encodeur": encodeur, "ext": extension, "son": son, "message": "", "debit_video": None, "debit_audio": 0, "crf": None, "hauteur": info["hauteur"]}
    limite = r["hauteur_max"]
    if r["mode"] == "qualite":
        plan["crf"] = NIVEAUX_QUALITE_VIDEO[r["niveau"]][encodeur]
        plan["debit_audio"] = 128_000 if son else 0
        if limite > 0:
            plan["hauteur"] = min(info["hauteur"], limite)
        return plan
    cible = r["cible_octets"] if r["mode"] == "taille" else int(taille_origine * r["pourcentage"] / 100)
    cible = max(cible, 50 * 1024)
    bits = cible * 8 * 0.965                                       # marge pour le conteneur et les ecarts du debit
    par_seconde = bits / info["duree"]
    if son:
        plan["debit_audio"] = 128_000 if par_seconde > 1_200_000 else 96_000 if par_seconde > 600_000 else 64_000 if par_seconde > 250_000 else 40_000
        plan["debit_audio"] = min(plan["debit_audio"], int(par_seconde * 0.3))
        plan["debit_audio"] = max(plan["debit_audio"], 24_000)
    plan["debit_video"] = max(15_000, int(par_seconde - plan["debit_audio"]))
    hauteur = info["hauteur"]
    if limite == -1:                                               # automatique : la plus grande hauteur dont la qualite reste correcte pour ce poids
        hauteur = _hauteur_pour_debit(plan["debit_video"], info["largeur"], info["hauteur"], info["fps"], BPP_MINI[encodeur])
        if hauteur < info["hauteur"]:
            plan["message"] = f"Résolution réduite à {hauteur} p pour que l'image reste nette avec ce poids."
    elif limite > 0:
        hauteur = min(hauteur, limite)
    plan["hauteur"] = hauteur
    if plan["debit_video"] / (max(2, round(info["largeur"] * hauteur / info["hauteur"])) * hauteur * info["fps"]) < BPP_MINI[encodeur] * 0.6:
        plan["message"] = (plan["message"] + " Taille demandée très basse pour cette durée : la qualité sera faible.").strip()
    return plan


def _filtre_echelle(info: dict, hauteur: int) -> list:
    if hauteur and hauteur < info["hauteur"]:
        return ["-vf", f"scale=-2:{hauteur // 2 * 2}:flags=lanczos"]
    return ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]           # dimensions paires, obligatoires pour H.264 / H.265 en 4:2:0


def _commande_encodage(source: str, sortie: str, info: dict, plan: dict, vitesse: str, passe: int = 0, journal: str = "", debit_video: int = None) -> list:
    """Commande ffmpeg d'une passe (0 = passe unique, 1 / 2 = deux passes)."""
    enc = plan["encodeur"]
    cmd = [_ffmpeg(), "-hide_banner", "-y", "-i", source, "-map", "0:v:0", *_filtre_echelle(info, plan["hauteur"]), "-pix_fmt", "yuv420p", "-c:v", enc]
    if enc == "libx264":
        cmd += ["-preset", VITESSES[vitesse], "-profile:v", "high"]
    elif enc == "libx265":
        cmd += ["-preset", VITESSES[vitesse], "-tag:v", "hvc1"]
    else:
        cmd += ["-row-mt", "1", "-deadline", "good", "-cpu-used", {"rapide": "5", "equilibre": "3", "lent": "1"}[vitesse]]
    if plan["crf"] is not None:
        cmd += ["-crf", str(plan["crf"])] + (["-b:v", "0"] if enc == "libvpx-vp9" else [])
    else:
        debit = debit_video or plan["debit_video"]
        cmd += ["-b:v", str(debit), "-maxrate", str(int(debit * 1.6)), "-bufsize", str(int(debit * 3))]
        if enc == "libx265":
            cmd += ["-x265-params", f"pass={passe}:stats={journal}:log-level=error"] if passe else []
        elif passe:
            cmd += ["-pass", str(passe), "-passlogfile", journal]
    if passe == 1:
        return cmd + ["-an", "-f", "null", os.devnull]
    if plan["son"]:
        if plan["ext"] == "webm":
            cmd += ["-map", "0:a:0", "-c:a", "libopus" if "libopus" in encodeurs_video() else "libvorbis", "-b:a", str(plan["debit_audio"] or 96_000)]
        else:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", str(plan["debit_audio"] or 128_000), "-ac", "2"]
    else:
        cmd += ["-an"]
    if plan["ext"] == "mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-map_metadata", "-1"]                                 # ni GPS ni autres metadonnees dans la video compressee
    return cmd + ["-sn", "-dn", sortie]


def _executer(cmd: list, duree: float, progression, annule: threading.Event, base: float, part: float, dossier_travail: str = None) -> None:
    """Lance ffmpeg et remonte l'avancement (base + part * fraction) ; leve ErreurMedia si annule, en echec ou trop long."""
    cmd = cmd[:2] + ["-progress", "pipe:1", "-nostats"] + cmd[2:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=dossier_travail)
    erreurs = []
    lecteur = threading.Thread(target=lambda: erreurs.extend(proc.stderr.readlines()[-30:]), daemon=True)
    lecteur.start()
    debut = time.time()
    try:
        for ligne in proc.stdout:
            if annule.is_set():
                proc.kill()
                raise ErreurMedia("Annulé.")
            if time.time() - debut > DELAI_MAX_ENCODAGE:
                proc.kill()
                raise ErreurMedia("Encodage trop long : abandon.")
            m = re.match(r"out_time_(?:us|ms)=(\d+)", ligne)
            if m and duree:
                progression(min(0.999, base + part * min(1.0, int(m.group(1)) / 1_000_000 / duree)))
        proc.wait(timeout=60)
    except ErreurMedia:
        raise
    except Exception:
        proc.kill()
        raise ErreurMedia("Encodage interrompu.")
    lecteur.join(timeout=5)
    if proc.returncode != 0:
        detail = " ".join(l.strip() for l in erreurs if l.strip())[-200:]
        raise ErreurMedia("L'encodage a échoué" + (f" : {detail}" if detail else "."))


def compresser_video(source: str, sortie_sans_extension: str, reglages: dict, taille_origine: int, progression=lambda f: None, annule: threading.Event = None) -> dict:
    """Compresse une video. Renvoie {"chemin", "format", "taille", "largeur", "hauteur", "message"}."""
    annule = annule or threading.Event()
    info = sonder_video(source)
    if info["duree"] > DUREE_MAX_VIDEO_SECONDES:
        raise ErreurMedia("Vidéo trop longue (3 heures maximum).")
    disponibles = formats_video_disponibles()
    if reglages["video"]["format"] not in disponibles:
        raise ErreurMedia("Ce format de sortie n'est pas disponible sur ce serveur.")
    plan = plan_video(info, reglages, taille_origine)
    vitesse = reglages["video"]["vitesse"]
    sortie = f"{sortie_sans_extension}.{plan['ext']}"
    dossier = os.path.dirname(sortie)
    journal = os.path.basename(sortie_sans_extension) + "-passes"          # nom relatif : ffmpeg est lance dans le dossier de sortie
    cible = None if plan["crf"] is not None else (reglages["video"]["cible_octets"] if reglages["video"]["mode"] == "taille" else int(taille_origine * reglages["video"]["pourcentage"] / 100))
    try:
        if plan["crf"] is not None:
            _executer(_commande_encodage(source, sortie, info, plan, vitesse), info["duree"], progression, annule, 0.0, 1.0, dossier)
        else:
            debit = plan["debit_video"]
            for tentative in range(2):
                _executer(_commande_encodage(source, sortie, info, plan, vitesse, 1, journal, debit), info["duree"], progression, annule, 0.0 if not tentative else 0.5, 0.4 if not tentative else 0.2, dossier)
                _executer(_commande_encodage(source, sortie, info, plan, vitesse, 2, journal, debit), info["duree"], progression, annule, 0.4 if not tentative else 0.7, 0.55 if not tentative else 0.3, dossier)
                taille = os.path.getsize(sortie)
                if tentative == 0 and cible and taille > cible * 1.04 and taille > 20_000:       # depasse la cible : un second essai avec un debit corrige
                    debit = max(10_000, int(debit * cible / taille * 0.96))
                    continue
                break
    finally:
        for nom in os.listdir(dossier):
            if nom.startswith(journal):
                try:
                    os.remove(os.path.join(dossier, nom))
                except OSError:
                    pass
    if annule.is_set():
        raise ErreurMedia("Annulé.")
    if not os.path.exists(sortie) or os.path.getsize(sortie) < 100:
        raise ErreurMedia("L'encodage n'a produit aucun fichier.")
    taille = os.path.getsize(sortie)
    message = plan["message"]
    if cible and taille > cible * 1.1:
        message = (message + f" La taille obtenue dépasse un peu la cible ({_octets(taille)}).").strip()
    largeur = max(2, round(info["largeur"] * plan["hauteur"] / info["hauteur"]) // 2 * 2)
    return {"chemin": sortie, "format": plan["ext"].upper(), "taille": taille, "largeur": largeur, "hauteur": plan["hauteur"], "message": message, "duree": info["duree"]}


def _octets(n: float) -> str:
    return f"{n / 1048576:.1f} Mo".replace(".", ",") if n >= 1048576 else f"{max(1, round(n / 1024))} Ko"


# ---------------------------------------------------------------------------------------------------------------- lots

_lots = {}
_verrou = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compression_media")


def _id_valide(ident: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", ident or ""):
        raise ErreurMedia("Lot introuvable.")
    return ident


def _lot(ident: str) -> dict:
    with _verrou:
        lot = _lots.get(_id_valide(ident))
    if not lot:
        raise ErreurMedia("Lot introuvable ou expiré : recommencez.")
    return lot


def creer_lot() -> str:
    purger()
    with _verrou:
        if len(_lots) >= MAX_LOTS:
            raise ErreurMedia("Trop de lots ouverts en même temps : fermez-en un d'abord.")
        ident = secrets.token_hex(16)
        os.makedirs(os.path.join(RACINE, ident, "entree"), exist_ok=True)
        os.makedirs(os.path.join(RACINE, ident, "sortie"), exist_ok=True)
        _lots[ident] = {"id": ident, "cree": time.time(), "maj": time.time(), "fichiers": [], "en_cours": False, "annule": threading.Event(), "suivant": 1, "verrou": threading.Lock(), "reglages": None}
    return ident


def _nom_sur(nom: str) -> str:
    nom = os.path.basename((nom or "fichier").replace("\\", "/"))
    nom = re.sub(r"[^\w .()\-]", "_", nom).strip(" .") or "fichier"
    return nom[:120]


def ajouter_fichier(ident: str, nom: str, flux) -> dict:
    """Enregistre un fichier envoye (flux de lecture) dans le lot, avec controle du type et de la taille. Renvoie sa description."""
    lot = _lot(ident)
    with lot["verrou"]:
        if lot["en_cours"]:
            raise ErreurMedia("Une compression est en cours : attendez la fin pour ajouter des fichiers.")
        if len(lot["fichiers"]) >= MAX_FICHIERS:
            raise ErreurMedia(f"Maximum {MAX_FICHIERS} fichiers par lot.")
        nom = _nom_sur(nom)
        genre = type_de_fichier(nom)
        numero = lot["suivant"]
        lot["suivant"] += 1
        chemin = os.path.join(RACINE, ident, "entree", f"{numero}-{nom}")
    limite = TAILLE_MAX_PHOTO if genre == "photo" else TAILLE_MAX_VIDEO
    ecrit = 0
    try:
        with open(chemin, "wb") as f:
            while True:
                morceau = flux.read(1024 * 1024)
                if not morceau:
                    break
                ecrit += len(morceau)
                if ecrit > limite:
                    raise ErreurMedia(f"« {nom} » est trop lourd (maximum {limite // 1048576} Mo pour une {'photo' if genre == 'photo' else 'vidéo'}).")
                f.write(morceau)
        if ecrit == 0:
            raise ErreurMedia(f"« {nom} » est vide.")
        total = sum(x["taille"] for x in lot["fichiers"]) + ecrit
        if total > TAILLE_MAX_LOT:
            raise ErreurMedia("Le lot dépasse 2 Go : compressez-le en plusieurs fois.")
        details = {}
        if genre == "photo":
            try:
                with Image.open(chemin) as im:
                    details = {"largeur": im.width, "hauteur": im.height}
            except Exception:
                raise ErreurMedia(f"« {nom} » n'est pas une image lisible.")
        else:
            info = sonder_video(chemin)
            details = {"largeur": info["largeur"], "hauteur": info["hauteur"], "duree": round(info["duree"], 1)}
    except Exception:
        try:
            os.remove(chemin)
        except OSError:
            pass
        raise
    fichier = {"n": numero, "nom": nom, "type": genre, "taille": ecrit, "chemin": chemin, "statut": "attente", "progression": 0.0, "message": "", "sortie": None, **details}
    with lot["verrou"]:
        lot["fichiers"].append(fichier)
        lot["maj"] = time.time()
    return _public(fichier)


def _public(f: dict) -> dict:
    """Description d'un fichier pour le navigateur (jamais de chemin du serveur)."""
    sortie = f.get("sortie")
    return {"n": f["n"], "nom": f["nom"], "type": f["type"], "taille": f["taille"], "statut": f["statut"], "progression": round(f["progression"], 3), "message": f["message"],
            "largeur": f.get("largeur"), "hauteur": f.get("hauteur"), "duree": f.get("duree"),
            "sortie": {"nom": os.path.basename(sortie["chemin"]), "taille": sortie["taille"], "format": sortie["format"], "largeur": sortie["largeur"], "hauteur": sortie["hauteur"]} if sortie else None}


def etat(ident: str) -> dict:
    lot = _lot(ident)
    with lot["verrou"]:
        fichiers = [_public(f) for f in lot["fichiers"]]
        termines = [f for f in lot["fichiers"] if f["statut"] in ("ok", "erreur", "annule")]
        avant = sum(f["taille"] for f in lot["fichiers"] if f["sortie"])
        apres = sum(f["sortie"]["taille"] for f in lot["fichiers"] if f["sortie"])
        avancement = sum((1.0 if f["statut"] in ("ok", "erreur", "annule") else f["progression"]) for f in lot["fichiers"]) / max(1, len(lot["fichiers"]))
        return {"id": ident, "fichiers": fichiers, "en_cours": lot["en_cours"], "avancement": round(avancement, 3), "termines": len(termines), "avant": avant, "apres": apres,
                "ok": sum(1 for f in lot["fichiers"] if f["statut"] == "ok")}


def retirer_fichier(ident: str, numero: int) -> dict:
    lot = _lot(ident)
    with lot["verrou"]:
        if lot["en_cours"]:
            raise ErreurMedia("Une compression est en cours.")
        fichier = next((f for f in lot["fichiers"] if f["n"] == numero), None)
        if not fichier:
            raise ErreurMedia("Fichier introuvable.")
        lot["fichiers"].remove(fichier)
    for chemin in (fichier["chemin"], (fichier["sortie"] or {}).get("chemin")):
        if chemin:
            try:
                os.remove(chemin)
            except OSError:
                pass
    return etat(ident)


def lancer(ident: str, reglages_bruts: dict) -> dict:
    """Demarre la compression de tous les fichiers du lot (en arriere-plan). Relancer remplace les resultats precedents."""
    lot = _lot(ident)
    reglages = lire_reglages(reglages_bruts)
    with lot["verrou"]:
        if lot["en_cours"]:
            raise ErreurMedia("Une compression est déjà en cours.")
        if not lot["fichiers"]:
            raise ErreurMedia("Ajoutez d'abord des fichiers.")
        if any(f["type"] == "video" for f in lot["fichiers"]) and reglages["video"]["format"] not in formats_video_disponibles():
            raise ErreurMedia("Ce format vidéo n'est pas disponible sur ce serveur : choisissez MP4 H.264.")
        lot["en_cours"] = True
        lot["annule"].clear()
        lot["reglages"] = reglages
        for f in lot["fichiers"]:
            if f.get("sortie"):
                try:
                    os.remove(f["sortie"]["chemin"])
                except OSError:
                    pass
            f.update(statut="attente", progression=0.0, message="", sortie=None)
        restants = [len(lot["fichiers"])]
    for f in list(lot["fichiers"]):
        _pool.submit(_traiter, lot, f, reglages, restants)
    return etat(ident)


def _traiter(lot: dict, fichier: dict, reglages: dict, restants: list) -> None:
    try:
        if lot["annule"].is_set():
            fichier.update(statut="annule", message="Annulé.")
            return
        fichier["statut"] = "en_cours"
        base = os.path.join(RACINE, lot["id"], "sortie", f"{fichier['n']}-{os.path.splitext(fichier['nom'])[0]}-compresse")
        def progres(fraction):
            fichier["progression"] = max(fichier["progression"], fraction)
        if fichier["type"] == "photo":
            sortie = compresser_photo(fichier["chemin"], base, reglages, fichier["taille"])
        else:
            sortie = compresser_video(fichier["chemin"], base, reglages, fichier["taille"], progres, lot["annule"])
        if sortie["taille"] >= fichier["taille"] and reglages[fichier["type"]]["mode"] != "qualite" and not reglages[fichier["type"]].get("cote_max"):
            sortie["message"] = (sortie["message"] + " Ce fichier est déjà plus léger que la cible : le résultat n'est pas plus petit que l'original.").strip()
        fichier.update(statut="ok", progression=1.0, sortie=sortie, message=sortie["message"])
    except ErreurMedia as erreur:
        fichier.update(statut="annule" if str(erreur) == "Annulé." else "erreur", message=str(erreur))
    except Exception as erreur:
        fichier.update(statut="erreur", message="Traitement impossible : " + str(erreur)[:120])
    finally:
        with lot["verrou"]:
            restants[0] -= 1
            if restants[0] <= 0:
                lot["en_cours"] = False
            lot["maj"] = time.time()


def annuler(ident: str) -> dict:
    lot = _lot(ident)
    lot["annule"].set()
    return etat(ident)


def chemin_sortie(ident: str, numero: int) -> tuple:
    """(chemin, nom de telechargement) du resultat d'un fichier."""
    lot = _lot(ident)
    with lot["verrou"]:
        fichier = next((f for f in lot["fichiers"] if f["n"] == numero), None)
        if not fichier or not fichier["sortie"]:
            raise ErreurMedia("Résultat indisponible.")
        return fichier["sortie"]["chemin"], re.sub(r"^\d+-", "", os.path.basename(fichier["sortie"]["chemin"]))


def archive_zip(ident: str) -> tuple:
    """(chemin du ZIP, nom) de tous les resultats ; les fichiers sont deja compresses : le ZIP les stocke sans les recompresser."""
    lot = _lot(ident)
    with lot["verrou"]:
        sorties = [(f["sortie"]["chemin"], re.sub(r"^\d+-", "", os.path.basename(f["sortie"]["chemin"]))) for f in lot["fichiers"] if f["sortie"]]
    if not sorties:
        raise ErreurMedia("Aucun résultat à télécharger.")
    chemin = os.path.join(RACINE, ident, "resultats.zip")
    deja = {}
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_STORED) as z:
        for source, nom in sorties:
            deja[nom] = deja.get(nom, 0) + 1
            if deja[nom] > 1:
                base, ext = os.path.splitext(nom)
                nom = f"{base}-{deja[nom]}{ext}"
            z.write(source, nom)
    return chemin, "fichiers-compresses.zip"


def miniature(ident: str, numero: int, cote: int = 160, sortie: bool = True) -> bytes:
    """Miniature JPEG d'une photo (le resultat par defaut, sinon l'original)."""
    lot = _lot(ident)
    with lot["verrou"]:
        fichier = next((f for f in lot["fichiers"] if f["n"] == numero), None)
        if not fichier or fichier["type"] != "photo":
            raise ErreurMedia("Miniature indisponible.")
        chemin = fichier["sortie"]["chemin"] if sortie and fichier["sortie"] else fichier["chemin"]
    image, _ = _ouvrir_photo(chemin)
    image.thumbnail((cote, cote))
    tampon = io.BytesIO()
    _preparer_mode(image, "JPEG").save(tampon, "JPEG", quality=80)
    return tampon.getvalue()


def fermer(ident: str) -> None:
    ident = _id_valide(ident)
    with _verrou:
        lot = _lots.pop(ident, None)
    if lot:
        lot["annule"].set()
    shutil.rmtree(os.path.join(RACINE, ident), ignore_errors=True)


def purger(maintenant: float = None) -> int:
    """Efface les lots trop anciens, et les dossiers orphelins (apres un redemarrage du serveur)."""
    maintenant = maintenant or time.time()
    supprimes = 0
    with _verrou:
        anciens = [i for i, l in _lots.items() if maintenant - l["maj"] > DUREE_VIE_SECONDES and not l["en_cours"]]
        for i in anciens:
            _lots.pop(i)["annule"].set()
        connus = set(_lots)
    for ident in anciens:
        shutil.rmtree(os.path.join(RACINE, ident), ignore_errors=True)
        supprimes += 1
    if os.path.isdir(RACINE):
        for nom in os.listdir(RACINE):
            if nom not in connus and nom not in anciens:
                dossier = os.path.join(RACINE, nom)
                try:
                    age = maintenant - os.path.getmtime(dossier)
                except OSError:
                    continue
                if age > 600:                                        # dossier sans lot en memoire : reste d'avant un redemarrage
                    shutil.rmtree(dossier, ignore_errors=True)
                    supprimes += 1
    return supprimes
