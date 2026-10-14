"""
Du montage a la publication : la video rendue est hebergee (OVH, URL publique : le composeur multi-reseaux et la bibliotheque en ont besoin) et
un texte de publication est prepare a partir de ce qui y est dit. Tout se fait en tache de fond (l'envoi d'une video par FTP peut durer plus
d'une minute) ; l'etat se lit avec etat_publication.
"""

import os
import threading
import uuid

from . import montage_parole as mp


class ErreurPublication(RuntimeError):
    pass


def _phrases_du_montage(sortie: dict) -> list:
    return mp.phrases_sur_montage(sortie["phrases"], sortie["segments"])


def non_rendu(sortie: dict) -> bool:
    """Vrai si l'etat de l'editeur (coupes, effets, textes) differe de la video actuellement rendue."""
    rendu = sortie.get("etat_rendu")
    if not rendu:
        return False
    return (list(sortie["segments"]) != list(rendu["segments"]) or sortie["effets"] != rendu["effets"] or sortie["phrases"] != rendu["phrases"]
            or sortie["sous_titres"] != rendu["sous_titres"])


def etat_publication(montage: dict, numero: int, version: int = None) -> dict:
    """
    {"hebergement": absent|en_cours|pret|erreur, "url", "kit_etat", "kit", "erreur", "version"}. Si `version` est donnee et que la video a ete
    rendue de nouveau depuis, la preparation precedente ne compte plus (etat « absent »).
    """
    courant = (montage.get("publication") or {}).get(numero)
    if not courant or (version is not None and courant["version"] != version):
        return {"hebergement": "absent", "url": "", "kit_etat": "absent", "kit": None, "erreur": "", "version": None}
    return {cle: courant.get(cle) for cle in ("hebergement", "url", "kit_etat", "kit", "erreur", "version")}


def lancer_preparation(montage: dict, sortie: dict, numero: int, heberger, generer_kit) -> dict:
    """
    Heberge la version rendue (heberger(octets, nom_fichier) -> URL publique) et prepare le texte de publication (generer_kit(phrases,
    accroche) -> {"titre", "texte", "hashtags", "accroches"}). Les deux travaillent en parallele ; ce qui est deja fait pour cette version n'est
    pas refait.
    """
    if not sortie.get("chemin") or not os.path.exists(sortie["chemin"]):
        raise ErreurPublication("La vidéo rendue n'est plus disponible : refaites un rendu.")
    publications = montage.setdefault("publication", {})
    courant = publications.get(numero)
    if not courant or courant["version"] != sortie["version"]:
        courant = publications[numero] = {"version": sortie["version"], "hebergement": "absent", "url": "", "nom_distant": "", "kit_etat": "absent", "kit": None, "erreur": ""}
    courant["erreur"] = ""
    chemin, phrases, accroche = sortie["chemin"], _phrases_du_montage(sortie), sortie["effets"].get("titre", "")

    if courant["hebergement"] in ("absent", "erreur"):
        courant["hebergement"] = "en_cours"

        def hebergement():
            try:
                with open(chemin, "rb") as fichier:
                    octets = fichier.read()
                nom = f"montage-{uuid.uuid4().hex[:12]}.mp4"
                courant["url"], courant["nom_distant"] = heberger(octets, nom), nom
                courant["hebergement"] = "pret"
            except Exception as erreur:
                courant.update(hebergement="erreur", erreur=f"Hébergement de la vidéo impossible : {str(erreur)[:250]}")

        threading.Thread(target=hebergement, daemon=True).start()

    if courant["kit_etat"] in ("absent", "erreur"):
        courant["kit_etat"] = "en_cours"

        def kit():
            try:
                courant["kit"] = generer_kit(phrases, accroche)
                courant["kit_etat"] = "pret"
            except Exception as erreur:
                courant.update(kit_etat="erreur", erreur=courant["erreur"] or f"Texte de publication impossible : {str(erreur)[:250]}")

        threading.Thread(target=kit, daemon=True).start()
    return etat_publication(montage, numero)
