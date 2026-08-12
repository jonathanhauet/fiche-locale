"""Envoi d'un fichier vers l'hebergement OVH par FTP. Meme logique que upload_ovh.py."""

import io
import os
from ftplib import FTP, FTP_TLS

from dotenv import load_dotenv

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

FTP_HOTE = os.getenv("OVH_FTP_HOTE")
FTP_UTILISATEUR = os.getenv("OVH_FTP_UTILISATEUR")
FTP_MOTDEPASSE = os.getenv("OVH_FTP_MOTDEPASSE")
FTP_DOSSIER = os.getenv("OVH_FTP_DOSSIER", "").strip("/")
URL_PUBLIQUE_BASE = os.getenv("OVH_URL_PUBLIQUE_BASE", "").rstrip("/")


def envoyer_octets(octets: bytes, nom_fichier_distant: str) -> str:
    """Envoie des octets (image) vers le dossier OVH configure, renvoie l'URL publique."""
    manquants = [
        nom for nom, valeur in [
            ("OVH_FTP_HOTE", FTP_HOTE),
            ("OVH_FTP_UTILISATEUR", FTP_UTILISATEUR),
            ("OVH_FTP_MOTDEPASSE", FTP_MOTDEPASSE),
            ("OVH_URL_PUBLIQUE_BASE", URL_PUBLIQUE_BASE),
        ] if not valeur
    ]
    if manquants:
        raise RuntimeError("Valeur(s) manquante(s) dans .env : " + ", ".join(manquants))

    try:
        ftp = FTP_TLS()
        ftp.connect(FTP_HOTE, 21, timeout=30)
        ftp.login(FTP_UTILISATEUR, FTP_MOTDEPASSE)
        ftp.prot_p()
    except Exception:
        ftp = FTP()
        ftp.connect(FTP_HOTE, 21, timeout=30)
        ftp.login(FTP_UTILISATEUR, FTP_MOTDEPASSE)

    if FTP_DOSSIER:
        try:
            ftp.cwd(FTP_DOSSIER)
        except Exception:
            try:
                ftp.mkd(FTP_DOSSIER)
                ftp.cwd(FTP_DOSSIER)
            except Exception as erreur:
                ftp.quit()
                raise RuntimeError(
                    f"Impossible de creer ou d'acceder au dossier distant '{FTP_DOSSIER}'. Detail : {erreur}"
                )

    ftp.storbinary(f"STOR {nom_fichier_distant}", io.BytesIO(octets))
    ftp.quit()

    return f"{URL_PUBLIQUE_BASE}/{nom_fichier_distant}"
