"""
Telechargement groupe des photos des fiches Google d'un reseau : un fichier ZIP, un dossier par agence, prepare en arriere-plan
(pour 200 agences, la preparation prend plusieurs minutes). Trois portees : la devanture seule, les photos cles, ou toutes.
"""

import csv
import io
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import requests

from . import audit_photos, google_oauth

NB_FICHES_MAX = 400
MAX_PHOTOS_TOUTES = 60                 # par agence, les plus recentes
PORTEES = {"devanture": "Devanture (photos classées Extérieur)", "cles": "Photos clés (couverture, profil, logo, extérieur, intérieur)", "toutes": "Toutes les photos (60 plus récentes)"}
TAILLES = {"1600": "Réduites (1 600 px)", "0": "Originales"}
DUREE_CONSERVATION = 2 * 3600
DOSSIER = os.path.join(tempfile.gettempdir(), "fiche_locale_photos")
TACHES = {}
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
NOMS_CATEGORIE = {"COVER": "couverture", "PROFILE": "profil", "LOGO": "logo", "EXTERIOR": "devanture", "INTERIOR": "interieur", "ADDITIONAL": "autre",
                  "PRODUCT": "produit", "AT_WORK": "au-travail", "TEAMS": "equipe", "FOOD_AND_DRINK": "plat", "MENU": "menu"}


def nom_sur(texte: str, defaut: str = "agence") -> str:
    """Nom de dossier ou de fichier sans caracteres interdits ni accents."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFKD", texte or "") if not unicodedata.combining(c))
    propre = re.sub(r"[^A-Za-z0-9 ._()-]+", "", sans_accents).strip(" .")
    return re.sub(r"\s+", " ", propre)[:80] or defaut


def selectionner(photos: list, portee: str) -> list:
    """Photos a telecharger pour une agence selon la portee choisie."""
    if portee == "devanture":
        return [p for p in photos if p["categorie"] == "EXTERIOR"]
    if portee == "cles":
        gardees = []
        for categorie, nombre in (("COVER", 1), ("PROFILE", 1), ("LOGO", 1), ("EXTERIOR", 3), ("INTERIOR", 3)):
            gardees += [p for p in photos if p["categorie"] == categorie][:nombre]
        return gardees
    return sorted(photos, key=lambda p: p.get("date_publication") or "", reverse=True)[:MAX_PHOTOS_TOUTES]


def _nettoyer_anciennes():
    os.makedirs(DOSSIER, exist_ok=True)
    limite = time.time() - DUREE_CONSERVATION
    for nom in os.listdir(DOSSIER):
        chemin = os.path.join(DOSSIER, nom)
        if os.path.getmtime(chemin) < limite:
            try:
                os.remove(chemin) if os.path.isfile(chemin) else shutil.rmtree(chemin)
            except OSError:
                pass
    for identifiant in [i for i, t in TACHES.items() if t["cree_le"] < limite]:
        TACHES.pop(identifiant, None)


def lancer(db, clients: list, portee: str, taille: str) -> str:
    """Prepare les identifiants (session de base de donnees), puis demarre le travail en arriere-plan. Renvoie l'identifiant de la tache."""
    _nettoyer_anciennes()
    travaux, non_lues = [], []
    for client in clients:
        if not client.account_id or not client.location_id:
            non_lues.append((client.nom, "Pas de fiche Google associée."))
            continue
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            non_lues.append((client.nom, "Compte Google non valide (à reconnecter)."))
            continue
        travaux.append((client.nom, identifiants, client.account_id, client.location_id))
    identifiant = uuid.uuid4().hex
    TACHES[identifiant] = {
        "etat": "en_cours", "fiches_total": len(travaux) + len(non_lues), "fiches_faites": len(non_lues), "photos": 0, "agence_en_cours": "",
        "taille_mo": 0.0, "erreur": "", "non_lues": non_lues, "sans_photo": [], "chemin": os.path.join(DOSSIER, identifiant + ".zip"), "cree_le": time.time(),
    }
    threading.Thread(target=_travail, args=(identifiant, travaux, portee, taille), daemon=True).start()
    return identifiant


def _telecharger(url: str, taille: str):
    """(octets, extension) ou None."""
    adresse = audit_photos.reduire(url, int(taille)) if int(taille) else url
    for _ in range(2):
        try:
            reponse = requests.get(adresse, timeout=60)
        except requests.RequestException:
            continue
        type_contenu = reponse.headers.get("content-type", "").split(";")[0].strip().lower()
        if reponse.status_code == 200 and type_contenu in EXTENSIONS:
            return reponse.content, EXTENSIONS[type_contenu]
    return None


def _travail(identifiant: str, travaux: list, portee: str, taille: str):
    tache = TACHES[identifiant]
    lignes_index = [["Agence", "Fichier", "Catégorie Google", "Date d'ajout", "Largeur (px)", "Hauteur (px)", "Description"]]
    dossiers_utilises = set()
    try:
        with zipfile.ZipFile(tache["chemin"], "w", zipfile.ZIP_STORED) as archive, ThreadPoolExecutor(max_workers=6) as pool:
            for nom, identifiants, account_id, location_id in travaux:
                tache["agence_en_cours"] = nom
                dossier, n = nom_sur(nom), 2
                while dossier.lower() in dossiers_utilises:
                    dossier, n = f"{nom_sur(nom)} ({n})", n + 1
                dossiers_utilises.add(dossier.lower())
                try:
                    photos, _ = audit_photos.lire_photos(identifiants, account_id, location_id)
                except Exception as erreur:
                    tache["non_lues"].append((nom, str(erreur)[:150]))
                    tache["fiches_faites"] += 1
                    continue
                choisies = selectionner(photos, portee)
                if not choisies:
                    tache["sans_photo"].append(nom)
                resultats = list(pool.map(lambda p: _telecharger(p["url"], taille), choisies))
                compteurs = {}
                for photo, resultat in zip(choisies, resultats):
                    if not resultat:
                        continue
                    octets, extension = resultat
                    categorie = NOMS_CATEGORIE.get(photo["categorie"], "autre")
                    compteurs[categorie] = compteurs.get(categorie, 0) + 1
                    date = (photo.get("date_publication") or "")[:10] if (photo.get("date_publication") or "")[:4] > "2000" else ""
                    fichier = f"{categorie}-{compteurs[categorie]:02d}{('_' + date) if date else ''}.{extension}"
                    archive.writestr(f"{dossier}/{fichier}", octets)
                    tache["photos"] += 1
                    tache["taille_mo"] = round(os.path.getsize(tache["chemin"]) / 1048576, 1)
                    lignes_index.append([nom, f"{dossier}/{fichier}", audit_photos.LIBELLES_CATEGORIE.get(photo["categorie"], photo["categorie"] or "Autre"), date,
                                         photo.get("largeur") or "", photo.get("hauteur") or "", (photo.get("description") or "")[:200]])
                tache["fiches_faites"] += 1
            sortie = io.StringIO()
            csv.writer(sortie, delimiter=";").writerows(lignes_index)
            archive.writestr("index.csv", "﻿" +sortie.getvalue())
            notes = [f"{n} : {raison}" for n, raison in tache["non_lues"]] + [f"{n} : aucune photo pour cette portée" for n in tache["sans_photo"]]
            if notes:
                archive.writestr("agences_a_verifier.txt", "\n".join(notes))
        tache["taille_mo"] = round(os.path.getsize(tache["chemin"]) / 1048576, 1)
        tache["etat"] = "termine"
    except Exception as erreur:
        tache["etat"] = "erreur"
        tache["erreur"] = str(erreur)[:300]
    tache["agence_en_cours"] = ""


def etat(identifiant: str):
    tache = TACHES.get(identifiant)
    if not tache:
        return None
    return {cle: tache[cle] for cle in ("etat", "fiches_total", "fiches_faites", "photos", "agence_en_cours", "taille_mo", "erreur")} | {
        "non_lues": [f"{n} : {r}" for n, r in tache["non_lues"]], "sans_photo": tache["sans_photo"],
    }
