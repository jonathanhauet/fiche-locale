"""
Enregistrements video partageables (facon Loom) : on se filme (camera, ecran, ou ecran avec la camera en rond) depuis le navigateur, la video est envoyee
par morceaux a la plateforme PENDANT l'enregistrement, puis traitee en arriere-plan (decoupe du debut et de la fin au choix, sequence de fin animee avec le logo
du client, conversion en MP4 lisible partout) et hebergee sur OVH ; une page publique `/v/<jeton>` la lit, et une duree de conservation choisie declenche sa
suppression automatique (fichier OVH compris). Le fichier hebergee porte un nom aleatoire : sans le lien, impossible de le deviner.
"""

import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta

from PIL import Image, ImageDraw

from . import carrousel_visuel as cv
from . import models, ovh_upload

DUREES_JOURS = (1, 7, 30, 90, 0)                     # 0 = illimitee
DUREE_DEFAUT = 7
LIBELLES_DUREE = {1: "1 jour", 7: "7 jours", 30: "30 jours", 90: "90 jours", 0: "Illimitée"}
MODES = {"camera": "Caméra seule", "ecran_camera": "Écran + caméra", "ecran": "Écran seul"}
TAILLE_MORCEAU_MAX = 16 * 1024 * 1024
TAILLE_MAX = 500 * 1024 * 1024
DUREE_MAX_SECONDES = 30 * 60
EXTENSIONS = ("webm", "mp4")
ENVOI_EXPIRE_APRES = timedelta(hours=3)             # un envoi jamais termine est abandonne (et ses morceaux supprimes) au bout de ce delai
TRAITEMENT_EXPIRE_APRES = timedelta(hours=6)        # un traitement bloque (serveur redemarre en route...) est abandonne au bout de ce delai
DOSSIER_TEMP = os.path.join(tempfile.gettempdir(), "enregistrements_fiche_locale")
DUREE_FIN = 3.0                                     # secondes de la sequence de fin animee
IMAGES_PAR_SECONDE = 30
LARGEUR_MAX, HAUTEUR_MAX = 1920, 1080
COULEUR_FIN_DEFAUT = "#1f4e8c"
COULEUR_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def nouveau_jeton() -> str:
    return secrets.token_urlsafe(18)


def duree_valide(jours) -> int:
    try:
        jours = int(jours)
    except (TypeError, ValueError):
        return DUREE_DEFAUT
    return jours if jours in DUREES_JOURS else DUREE_DEFAUT


def expiration(jours: int, depuis: datetime = None):
    """Date de suppression (UTC naif, comme le reste de la base), ou None si la video est conservee sans limite."""
    return None if not jours else (depuis or datetime.utcnow()) + timedelta(days=jours)


def couleur_valide(couleur, defaut: str = COULEUR_FIN_DEFAUT) -> str:
    couleur = str(couleur or "").strip()
    return couleur.lower() if COULEUR_HEX.fullmatch(couleur) else defaut


# --------------------------------------------------------------------------- reception des morceaux

def dossier_morceaux(identifiant: int) -> str:
    return os.path.join(DOSSIER_TEMP, str(int(identifiant)))


def enregistrer_morceau(identifiant: int, numero: int, octets: bytes) -> None:
    if not 0 <= numero < 5000 or len(octets) > TAILLE_MORCEAU_MAX:
        raise ValueError("morceau invalide")
    dossier = dossier_morceaux(identifiant)
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, f"{numero:05d}.part"), "wb") as fichier:
        fichier.write(octets)


def compter_morceaux(identifiant: int) -> int:
    """Nombre de morceaux recus d'affilee depuis le premier (0 s'il n'y en a aucun) : ce qu'on peut recuperer si l'enregistrement a ete interrompu."""
    n = 0
    while os.path.exists(os.path.join(dossier_morceaux(identifiant), f"{n:05d}.part")):
        n += 1
    return n


def extension_probable(identifiant: int) -> str:
    """« mp4 » si le premier morceau recu est un MP4 (Safari), sinon « webm » : utile pour recuperer un enregistrement interrompu."""
    try:
        with open(os.path.join(dossier_morceaux(identifiant), "00000.part"), "rb") as fichier:
            return "mp4" if fichier.read(12)[4:8] == b"ftyp" else "webm"
    except OSError:
        return "webm"


def assembler(identifiant: int, nombre: int, extension: str) -> str:
    """Recolle les morceaux dans l'ordre ; ValueError s'il en manque ou si le total depasse la limite. Renvoie le chemin du fichier assemble."""
    dossier = dossier_morceaux(identifiant)
    chemins = [os.path.join(dossier, f"{n:05d}.part") for n in range(nombre)]
    if nombre < 1 or not all(os.path.exists(c) for c in chemins):
        raise ValueError("Des morceaux de la vidéo n'ont pas été reçus : recommencez l'envoi.")
    if sum(os.path.getsize(c) for c in chemins) > TAILLE_MAX:
        raise ValueError(f"Vidéo trop lourde ({TAILLE_MAX // (1024 * 1024)} Mo au maximum).")
    sortie = os.path.join(dossier, f"complet.{extension}")
    with open(sortie, "wb") as destination:
        for chemin in chemins:
            with open(chemin, "rb") as source:
                shutil.copyfileobj(source, destination, 1024 * 1024)
    return sortie


# --------------------------------------------------------------------------- ffmpeg

def _ffmpeg() -> str:
    from . import montage_video
    return montage_video.FFMPEG


def reparer_duree(chemin: str, extension: str) -> str:
    """
    Un WebM enregistre par le navigateur n'a ni duree ni index : le lecteur ne sait pas avancer dans la video. Une copie « -c copy » par ffmpeg (sans reencodage,
    quelques secondes) les ajoute. Au moindre souci, on garde le fichier d'origine : la video reste lisible.
    """
    try:
        sortie = chemin + ".repare." + extension
        subprocess.run([_ffmpeg(), "-y", "-i", chemin, "-c", "copy", sortie], capture_output=True, timeout=600, check=True)
        if os.path.exists(sortie) and os.path.getsize(sortie) > 1024:
            return sortie
    except Exception:
        pass
    return chemin


def sonder(chemin: str) -> dict:
    """Duree (secondes ou None), definition et presence d'une piste audio, lues avec ffmpeg (le ffmpeg embarque n'a pas ffprobe)."""
    sortie = subprocess.run([_ffmpeg(), "-hide_banner", "-i", chemin], capture_output=True, text=True, timeout=120).stderr
    duree = None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", sortie)
    if m:
        duree = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = re.search(r",\s*(\d{2,5})x(\d{2,5})[\s,\[]", sortie)
    return {"duree": duree, "largeur": int(v.group(1)) if v else 0, "hauteur": int(v.group(2)) if v else 0, "audio": "Audio:" in sortie}


def definition_sortie(largeur: int, hauteur: int) -> tuple:
    """Definition de la video finale : celle de l'enregistrement, ramenee a 1920x1080 au plus, en nombres pairs (obligatoire pour H.264)."""
    largeur, hauteur = (largeur or 1280), (hauteur or 720)
    k = min(1.0, LARGEUR_MAX / largeur, HAUTEUR_MAX / hauteur)
    return max(320, int(largeur * k) // 2 * 2), max(180, int(hauteur * k) // 2 * 2)


# --------------------------------------------------------------------------- sequence de fin animee (images dessinees une a une)

def _sortie(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _rebond(x: float) -> float:
    x = max(0.0, min(1.0, x))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _texte_sur(couleur: tuple) -> tuple:
    luminance = (0.2126 * couleur[0] + 0.7152 * couleur[1] + 0.0722 * couleur[2]) / 255
    return (17, 17, 17) if luminance > 0.6 else (255, 255, 255)


def _avec_opacite(image: Image.Image, opacite: float) -> Image.Image:
    image = image.convert("RGBA")
    alpha = image.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacite))))
    image.putalpha(alpha)
    return image


def _carte_logo(logo: Image.Image, largeur: int, hauteur: int) -> Image.Image:
    """Le logo sur une carte blanche arrondie (lisible sur n'importe quelle couleur), tenant dans 34 % de la largeur et 26 % de la hauteur."""
    logo = logo.convert("RGBA")
    maxi_l, maxi_h = int(largeur * 0.34), int(hauteur * 0.26)
    pad = int(min(maxi_l, maxi_h) * 0.12)
    k = min((maxi_l - 2 * pad) / logo.width, (maxi_h - 2 * pad) / logo.height)
    logo = logo.resize((max(1, int(logo.width * k)), max(1, int(logo.height * k))), Image.LANCZOS)
    carte = Image.new("RGBA", (logo.width + 2 * pad, logo.height + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(carte).rounded_rectangle((0, 0, carte.width - 1, carte.height - 1), radius=int(pad * 1.2), fill=(255, 255, 255, 255))
    carte.alpha_composite(logo, (pad, pad))
    return carte


def image_fin(largeur: int, hauteur: int, couleur: str, logo: Image.Image = None, texte: str = "", t: float = DUREE_FIN, carte: Image.Image = None) -> Image.Image:
    """
    Image de la sequence de fin a l'instant t (secondes) : un cercle de la couleur choisie s'ouvre depuis le centre, le logo apparait sur sa carte blanche en
    rebondissant, un filet se trace, puis le texte facultatif monte en fondu. A t = DUREE_FIN, tout est en place.
    """
    fond = cv._rgb(couleur_valide(couleur))
    sombre = cv._melange(fond, (0, 0, 0), 0.35)
    image = Image.new("RGB", (largeur, hauteur), sombre)
    rayon = (largeur ** 2 + hauteur ** 2) ** 0.5 / 2 * _sortie(t / 0.6)
    if rayon > 1:
        ImageDraw.Draw(image).ellipse((largeur / 2 - rayon, hauteur / 2 - rayon, largeur / 2 + rayon, hauteur / 2 + rayon), fill=fond)
    couche = image.convert("RGBA")
    if carte is None and logo is not None:
        carte = _carte_logo(logo, largeur, hauteur)
    texte = (texte or "").strip()
    encre = _texte_sur(fond)
    decalage_bloc = int(hauteur * 0.04) if texte else 0                  # avec du texte, le logo est remonte pour centrer l'ensemble
    if carte is not None:
        avance = (t - 0.35) / 0.8
        if avance > 0:
            echelle = 0.55 + 0.45 * _rebond(avance)
            w, h = max(1, int(carte.width * echelle)), max(1, int(carte.height * echelle))
            reduit = _avec_opacite(carte.resize((w, h), Image.LANCZOS), (t - 0.35) / 0.35)
            ombre = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
            ImageDraw.Draw(ombre).rounded_rectangle((20, 26, w + 20, h + 26), radius=int(h * 0.12), fill=(0, 0, 0, 70))
            cx, cy = largeur // 2, hauteur // 2 - decalage_bloc - int(hauteur * 0.01)
            couche.alpha_composite(_avec_opacite(ombre, (t - 0.35) / 0.35), (cx - w // 2 - 20, cy - h // 2 - 20))
            couche.alpha_composite(reduit, (cx - w // 2, cy - h // 2))
        bas_carte = hauteur // 2 - decalage_bloc - int(hauteur * 0.01) + carte.height // 2
    else:
        bas_carte = hauteur // 2 - int(hauteur * 0.06)
    if texte:
        trait = _sortie((t - 0.85) / 0.45)
        if trait > 0:
            demi = int(largeur * 0.07 * trait)
            y = bas_carte + int(hauteur * 0.045)
            ImageDraw.Draw(couche).rounded_rectangle((largeur // 2 - demi, y, largeur // 2 + demi, y + max(2, int(hauteur * 0.006))), radius=3, fill=encre + (170,))
        taille = max(18, int(hauteur * 0.052))
        police = cv._police("gras", taille)
        while taille > 14 and ImageDraw.Draw(couche).textlength(texte, font=police) > largeur * 0.82:
            taille = int(taille * 0.92)
            police = cv._police("gras", taille)
        avance_t = _sortie((t - 1.0) / 0.55)
        if avance_t > 0:
            calque = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
            y = bas_carte + int(hauteur * 0.085) + int((1 - avance_t) * hauteur * 0.035)
            ImageDraw.Draw(calque).text((largeur // 2, y), texte, font=police, fill=encre + (255,), anchor="mt")
            couche.alpha_composite(_avec_opacite(calque, avance_t))
    return couche.convert("RGB")


def ecrire_sequence_fin(dossier: str, largeur: int, hauteur: int, couleur: str, logo: Image.Image = None, texte: str = "") -> int:
    """Dessine les images de la sequence de fin (fin_0000.png...) et renvoie leur nombre."""
    os.makedirs(dossier, exist_ok=True)
    n = int(DUREE_FIN * IMAGES_PAR_SECONDE)
    carte = _carte_logo(logo, largeur, hauteur) if logo is not None else None
    for i in range(n):
        image_fin(largeur, hauteur, couleur, None, texte, i / IMAGES_PAR_SECONDE, carte).save(os.path.join(dossier, f"fin_{i:04d}.png"), compress_level=1)
    return n


def apercu_fin(largeur: int, hauteur: int, couleur: str, logo: Image.Image = None, texte: str = "") -> Image.Image:
    """Image finale de la sequence (tout est en place), pour la montrer avant l'enregistrement."""
    return image_fin(largeur, hauteur, couleur, logo, texte, DUREE_FIN)


# --------------------------------------------------------------------------- traitement : decoupe + sequence de fin + MP4

def commande_traitement(entree: str, sortie: str, info: dict, debut: float, fin: float, dossier_fin: str = None, definition: tuple = (1280, 720)) -> list:
    """Commande ffmpeg : une seule passe (decoupe, mise a la definition, sequence de fin collee a la suite, H.264 + AAC, MP4 « faststart »)."""
    L, H = definition
    utile = max(0.1, fin - debut)
    graphe = [
        f"[0:v]trim=start={debut:.3f}:end={fin:.3f},setpts=PTS-STARTPTS,fps={IMAGES_PAR_SECONDE},scale={L}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={L}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p[v0]",
    ]
    if info.get("audio"):
        graphe.append(f"[0:a]atrim=start={debut:.3f}:end={fin:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a0]")
    else:
        graphe.append(f"anullsrc=r=48000:cl=stereo:d={utile:.3f}[a0]")
    entrees = ["-i", entree]
    if dossier_fin:
        entrees += ["-framerate", str(IMAGES_PAR_SECONDE), "-i", os.path.join(dossier_fin, "fin_%04d.png")]
        graphe.append(f"[1:v]fps={IMAGES_PAR_SECONDE},scale={L}:{H},setsar=1,format=yuv420p[v1]")
        graphe.append(f"anullsrc=r=48000:cl=stereo:d={DUREE_FIN}[a1]")
        graphe.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
        etiquettes = ["[v]", "[a]"]
    else:
        etiquettes = ["[v0]", "[a0]"]
    return [_ffmpeg(), "-y", "-hide_banner", *entrees, "-filter_complex", ";".join(graphe), "-map", etiquettes[0], "-map", etiquettes[1],
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", str(IMAGES_PAR_SECONDE), "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", sortie]


def traiter(entree: str, sortie: str, duree_connue: float, debut: float = 0.0, fin: float = 0.0, sequence_fin: dict = None, rappel=None) -> dict:
    """
    Decoupe (debut/fin en secondes, 0 = pas de decoupe), ajoute la sequence de fin ({"couleur", "texte", "logo": image PIL ou None}) et convertit en MP4.
    rappel(pourcentage) est appele regulierement. Renvoie {"duree": secondes de la video finale}. Leve RuntimeError si ffmpeg echoue.
    """
    info = sonder(entree)
    duree_source = info["duree"] or float(duree_connue or 0)
    if duree_source <= 0:
        raise RuntimeError("Durée de la vidéo illisible.")
    debut = max(0.0, float(debut or 0))
    fin = min(duree_source, float(fin)) if fin else duree_source
    if fin - debut < 0.5:
        debut, fin = 0.0, duree_source
    definition = definition_sortie(info["largeur"], info["hauteur"])
    dossier_fin = None
    if sequence_fin:
        dossier_fin = os.path.join(os.path.dirname(sortie), "sequence_fin")
        ecrire_sequence_fin(dossier_fin, definition[0], definition[1], sequence_fin.get("couleur"), sequence_fin.get("logo"), sequence_fin.get("texte", ""))
    total = (fin - debut) + (DUREE_FIN if dossier_fin else 0)
    journal = open(sortie + ".log", "w", encoding="utf-8")
    processus = subprocess.Popen(commande_traitement(entree, sortie, info, debut, fin, dossier_fin, definition), stdout=subprocess.PIPE, stderr=journal, text=True)
    minuteur = threading.Timer(7200, processus.kill)
    minuteur.start()
    dernier = -1
    try:
        for ligne in processus.stdout:
            if ligne.startswith("out_time_ms=") and rappel:
                try:
                    pourcent = min(99, int(int(ligne.split("=", 1)[1]) / 1_000_000 / total * 100))
                except (ValueError, ZeroDivisionError):
                    continue
                if pourcent >= dernier + 3:
                    dernier = pourcent
                    rappel(pourcent)
        processus.wait()
    finally:
        minuteur.cancel()
        journal.close()
    if processus.returncode != 0 or not os.path.exists(sortie) or os.path.getsize(sortie) < 1024:
        try:
            reste = open(sortie + ".log", encoding="utf-8", errors="replace").read()[-300:]
        except OSError:
            reste = ""
        raise RuntimeError("Conversion impossible : " + reste.strip().replace("\n", " "))
    return {"duree": total}


# --------------------------------------------------------------------------- cycle de vie

def _nettoyer(identifiant: int) -> None:
    shutil.rmtree(dossier_morceaux(identifiant), ignore_errors=True)


def envoyer_vers_hebergement(fabrique_session, identifiant: int, nombre: int, extension: str, duree: float, vignette: bytes = None, options: dict = None) -> None:
    """
    Assemble les morceaux, decoupe / ajoute la sequence de fin / convertit en MP4, envoie sur OVH puis passe l'enregistrement a PRET (ou ECHEC avec la raison).
    Si la conversion echoue, la video d'origine (WebM repare) est publiee telle quelle avec un avertissement. A lancer dans un thread.
    """
    options = options or {}
    session = fabrique_session()

    def avancer(pourcent: int, etape: str):
        ligne = session.get(models.Enregistrement, identifiant)
        if ligne:
            ligne.progression, ligne.etape = pourcent, etape
            session.commit()

    try:
        ligne = session.get(models.Enregistrement, identifiant)
        if not ligne:
            return
        try:
            avancer(2, "Assemblage de la vidéo")
            brut = assembler(identifiant, nombre, extension)
            repare = reparer_duree(brut, extension)
            avertissement = ""
            chemin, extension_finale, duree_finale = repare, extension, float(duree or 0)
            try:
                mp4 = os.path.join(dossier_morceaux(identifiant), "final.mp4")
                resultat = traiter(repare, mp4, duree, options.get("debut", 0), options.get("fin", 0), options.get("sequence_fin"),
                                   lambda p: avancer(5 + int(p * 0.88), "Traitement de la vidéo"))
                chemin, extension_finale, duree_finale = mp4, "mp4", resultat["duree"]
            except Exception as erreur:
                avertissement = f"Conversion en MP4 impossible, vidéo publiée telle quelle ({str(erreur)[:160]})."
            avancer(95, "Mise en ligne")
            nom = f"enreg-{secrets.token_hex(10)}.{extension_finale}"
            with open(chemin, "rb") as fichier:
                url = ovh_upload.envoyer_octets(fichier, nom)
            nom_vignette = url_vignette = ""
            if vignette:
                try:
                    nom_vignette = f"enreg-{secrets.token_hex(10)}.jpg"
                    url_vignette = ovh_upload.envoyer_octets(vignette, nom_vignette)
                except Exception:
                    nom_vignette = url_vignette = ""
            ligne = session.get(models.Enregistrement, identifiant)
            ligne.nom_fichier, ligne.url, ligne.taille = nom, url, os.path.getsize(chemin)
            ligne.nom_vignette, ligne.url_vignette = nom_vignette, url_vignette
            ligne.duree = max(0.0, min(duree_finale, float(DUREE_MAX_SECONDES + DUREE_FIN)))
            ligne.etat, ligne.erreur, ligne.progression, ligne.etape = "PRET", avertissement, 100, ""
            ligne.expire_le = expiration(ligne.jours_conservation)          # la duree de conservation court a partir du moment ou le lien est utilisable
        except Exception as erreur:
            session.rollback()
            ligne = session.get(models.Enregistrement, identifiant)
            if ligne:
                ligne.etat, ligne.erreur, ligne.etape = "ECHEC", str(erreur)[:300], ""
        session.commit()
    finally:
        session.close()
        _nettoyer(identifiant)


def lancer_envoi(fabrique_session, identifiant: int, nombre: int, extension: str, duree: float, vignette: bytes = None, options: dict = None) -> threading.Thread:
    fil = threading.Thread(target=envoyer_vers_hebergement, args=(fabrique_session, identifiant, nombre, extension, duree, vignette, options), daemon=True)
    fil.start()
    return fil


def supprimer_fichiers(ligne) -> None:
    """Retire de l'hebergement la video et sa vignette (au mieux : un echec distant n'empeche pas de marquer l'enregistrement supprime)."""
    for nom in (ligne.nom_fichier, ligne.nom_vignette):
        if nom:
            try:
                ovh_upload.supprimer_fichier(nom)
            except Exception:
                pass
    ligne.nom_fichier = ligne.url = ligne.nom_vignette = ligne.url_vignette = ""


def supprimer(db, ligne) -> None:
    """
    Supprime les fichiers. Une video qui a ete en ligne reste en base (etat SUPPRIMEE) pour que le lien partage affiche « plus disponible » ; un enregistrement
    jamais mis en ligne (envoi abandonne, recommencer, echec) est efface completement.
    """
    jamais_en_ligne = ligne.etat in ("ENVOI", "ECHEC") and not ligne.url
    supprimer_fichiers(ligne)
    _nettoyer(ligne.id)
    if jamais_en_ligne:
        db.delete(ligne)
    else:
        ligne.etat, ligne.expire_le = "SUPPRIMEE", None
    db.commit()


def est_expiree(ligne, maintenant: datetime = None) -> bool:
    return bool(ligne.expire_le and ligne.expire_le <= (maintenant or datetime.utcnow()))


def disponible(ligne) -> bool:
    return ligne.etat == "PRET" and bool(ligne.url) and not est_expiree(ligne)


def purger(db, maintenant: datetime = None) -> dict:
    """Passage quotidien : supprime les videos arrivees a echeance et abandonne les envois ou traitements jamais termines. Renvoie {"supprimees", "abandonnes"}."""
    maintenant = maintenant or datetime.utcnow()
    supprimees = abandonnes = 0
    for ligne in db.query(models.Enregistrement).filter(models.Enregistrement.etat == "PRET", models.Enregistrement.expire_le.isnot(None), models.Enregistrement.expire_le <= maintenant).all():
        supprimer(db, ligne)
        supprimees += 1
    for etat, delai, message in (("ENVOI", ENVOI_EXPIRE_APRES, "Envoi jamais terminé."), ("TRAITEMENT", TRAITEMENT_EXPIRE_APRES, "Traitement interrompu.")):
        for ligne in db.query(models.Enregistrement).filter(models.Enregistrement.etat == etat, models.Enregistrement.cree_le <= maintenant - delai).all():
            ligne.etat, ligne.erreur = "ECHEC", message
            _nettoyer(ligne.id)
            abandonnes += 1
    db.commit()
    return {"supprimees": supprimees, "abandonnes": abandonnes}


def en_dict(ligne, lien: str = "") -> dict:
    reste = None
    if ligne.expire_le and ligne.etat == "PRET":
        reste = max(0, (ligne.expire_le - datetime.utcnow()).days)
    return {
        "id": ligne.id, "token": ligne.token, "titre": ligne.titre or "Sans titre", "mode": MODES.get(ligne.mode, ligne.mode), "etat": ligne.etat, "erreur": ligne.erreur or "",
        "duree": int(ligne.duree or 0), "taille_mo": round((ligne.taille or 0) / 1048576, 1), "lien": lien, "protege": bool(ligne.mot_de_passe_hash),
        "illimitee": ligne.expire_le is None, "jours_restants": reste, "vignette": ligne.url_vignette or "", "progression": ligne.progression or 0, "etape": ligne.etape or "",
        "recuperable": ligne.etat == "ENVOI" and compter_morceaux(ligne.id) > 0,
        "cree_le": ligne.cree_le, "expire_le": ligne.expire_le, "client_id": ligne.client_id,
    }
