"""
Enregistrements video partageables (facon Loom) : on se filme (camera, ecran, ou ecran avec la camera en rond) depuis le navigateur, la video est envoyee
par morceaux a la plateforme puis hebergee sur OVH ; une page publique `/v/<jeton>` la lit, et une duree de conservation choisie declenche sa suppression
automatique (fichier OVH compris). Le fichier hebergee porte un nom aleatoire : sans le lien, impossible de le deviner.
"""

import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta

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
DOSSIER_TEMP = os.path.join(tempfile.gettempdir(), "enregistrements_fiche_locale")


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


def dossier_morceaux(identifiant: int) -> str:
    return os.path.join(DOSSIER_TEMP, str(int(identifiant)))


def enregistrer_morceau(identifiant: int, numero: int, octets: bytes) -> None:
    if not 0 <= numero < 5000 or len(octets) > TAILLE_MORCEAU_MAX:
        raise ValueError("morceau invalide")
    dossier = dossier_morceaux(identifiant)
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, f"{numero:05d}.part"), "wb") as fichier:
        fichier.write(octets)


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


def reparer_duree(chemin: str, extension: str) -> str:
    """
    Un WebM enregistre par le navigateur n'a ni duree ni index : le lecteur ne sait pas avancer dans la video. Une copie « -c copy » par ffmpeg (sans reencodage,
    quelques secondes) les ajoute. Au moindre souci, on garde le fichier d'origine : la video reste lisible.
    """
    try:
        from . import montage_video
        sortie = chemin + ".repare." + extension
        subprocess.run([montage_video.FFMPEG, "-y", "-i", chemin, "-c", "copy", sortie], capture_output=True, timeout=600, check=True)
        if os.path.exists(sortie) and os.path.getsize(sortie) > 1024:
            return sortie
    except Exception:
        pass
    return chemin


def _nettoyer(identifiant: int) -> None:
    shutil.rmtree(dossier_morceaux(identifiant), ignore_errors=True)


def envoyer_vers_hebergement(fabrique_session, identifiant: int, nombre: int, extension: str, duree: float, vignette: bytes = None) -> None:
    """Assemble les morceaux, repare la duree, envoie sur OVH puis passe l'enregistrement a PRET (ou ECHEC avec la raison). A lancer dans un thread."""
    session = fabrique_session()
    try:
        ligne = session.get(models.Enregistrement, identifiant)
        if not ligne:
            return
        try:
            chemin = reparer_duree(assembler(identifiant, nombre, extension), extension)
            nom = f"enreg-{secrets.token_hex(10)}.{extension}"
            with open(chemin, "rb") as fichier:
                url = ovh_upload.envoyer_octets(fichier, nom)
            nom_vignette = url_vignette = ""
            if vignette:
                try:
                    nom_vignette = f"enreg-{secrets.token_hex(10)}.jpg"
                    url_vignette = ovh_upload.envoyer_octets(vignette, nom_vignette)
                except Exception:
                    nom_vignette = url_vignette = ""
            ligne.nom_fichier, ligne.url, ligne.taille = nom, url, os.path.getsize(chemin)
            ligne.nom_vignette, ligne.url_vignette = nom_vignette, url_vignette
            ligne.duree = max(0.0, min(float(duree or 0), float(DUREE_MAX_SECONDES)))
            ligne.etat, ligne.erreur = "PRET", ""
            ligne.expire_le = expiration(ligne.jours_conservation)          # la duree de conservation court a partir du moment ou le lien est utilisable
        except Exception as erreur:
            ligne.etat, ligne.erreur = "ECHEC", str(erreur)[:300]
        session.commit()
    finally:
        session.close()
        _nettoyer(identifiant)


def lancer_envoi(fabrique_session, identifiant: int, nombre: int, extension: str, duree: float, vignette: bytes = None) -> threading.Thread:
    fil = threading.Thread(target=envoyer_vers_hebergement, args=(fabrique_session, identifiant, nombre, extension, duree, vignette), daemon=True)
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
    """Supprime les fichiers ; la ligne reste (etat SUPPRIMEE) pour que le lien partage affiche « plus disponible » plutot qu'une page introuvable."""
    supprimer_fichiers(ligne)
    ligne.etat, ligne.expire_le = "SUPPRIMEE", None
    _nettoyer(ligne.id)
    db.commit()


def est_expiree(ligne, maintenant: datetime = None) -> bool:
    return bool(ligne.expire_le and ligne.expire_le <= (maintenant or datetime.utcnow()))


def disponible(ligne) -> bool:
    return ligne.etat == "PRET" and bool(ligne.url) and not est_expiree(ligne)


def purger(db, maintenant: datetime = None) -> dict:
    """Passage quotidien : supprime les videos arrivees a echeance et abandonne les envois jamais termines. Renvoie {"supprimees", "abandonnes"}."""
    maintenant = maintenant or datetime.utcnow()
    supprimees = abandonnes = 0
    for ligne in db.query(models.Enregistrement).filter(models.Enregistrement.etat == "PRET", models.Enregistrement.expire_le.isnot(None), models.Enregistrement.expire_le <= maintenant).all():
        supprimer(db, ligne)
        supprimees += 1
    for ligne in db.query(models.Enregistrement).filter(models.Enregistrement.etat == "ENVOI", models.Enregistrement.cree_le <= maintenant - ENVOI_EXPIRE_APRES).all():
        ligne.etat, ligne.erreur = "ECHEC", "Envoi jamais terminé."
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
        "illimitee": ligne.expire_le is None, "jours_restants": reste, "vignette": ligne.url_vignette or "",
        "cree_le": ligne.cree_le, "expire_le": ligne.expire_le, "client_id": ligne.client_id,
    }
