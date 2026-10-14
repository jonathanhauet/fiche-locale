"""
Export Excel des clients avec les donnees disponibles gratuitement via les
API Google Business Profile deja utilisees dans la plateforme (fiche, avis,
photos, posts, liens de rendez-vous) - volontairement AUCUNE donnee payante
(positions/DataForSEO).

Pour plusieurs dizaines de fiches, la lecture chez Google prend plusieurs minutes : elle se fait en parallele et en tache de fond
(voir lancer / etat_tache / fichier_tache), la page interroge l'avancement puis telecharge le fichier une fois pret. Une requete
HTTP unique depasserait le delai de l'hebergeur (« upstream error »).
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import google_business, google_location, google_oauth, google_place_actions, google_reviews

ENTETES = [
    "Nom", "Nom sur Google", "Prénom", "Email", "Compte Google", "Étiquettes",
    "Téléphone", "Site web", "Adresse", "Catégorie principale",
    "Statut d'ouverture", "Complétude infos (hors photos)", "Complétude infos + photos", "Éléments manquants (estimation)", "Note moyenne", "Nombre d'avis",
    "Photos sur la fiche", "Posts publiés (plateforme)",
    "Lien RDV / réservation présent", "Lien RDV / réservation", "Lien fiche Google", "Erreur",
]
COLONNE_NOM_GOOGLE = ENTETES.index("Nom sur Google")
COLONNE_TELEPHONE = ENTETES.index("Téléphone")
COLONNE_SITE = ENTETES.index("Site web")
COLONNE_ADRESSE = ENTETES.index("Adresse")
COLONNE_CATEGORIE = ENTETES.index("Catégorie principale")
COLONNE_OUVERTURE = ENTETES.index("Statut d'ouverture")
COLONNE_LIEN_FICHE = ENTETES.index("Lien fiche Google")
COLONNE_COMPLETUDE_INFOS = ENTETES.index("Complétude infos (hors photos)")
COLONNE_COMPLETUDE_TOTALE = ENTETES.index("Complétude infos + photos")
COLONNE_MANQUANTS = ENTETES.index("Éléments manquants (estimation)")
COLONNE_NOTE = ENTETES.index("Note moyenne")
COLONNE_NB_AVIS = ENTETES.index("Nombre d'avis")
COLONNE_PHOTOS = ENTETES.index("Photos sur la fiche")
COLONNE_RDV_PRESENT = ENTETES.index("Lien RDV / réservation présent")
COLONNE_RDV_LIEN = ENTETES.index("Lien RDV / réservation")
# Liens d'action de la fiche Google qui servent a prendre un rendez-vous ou a reserver (les autres, comme commander, ne comptent pas ici).
TYPES_RDV = ("APPOINTMENT", "ONLINE_APPOINTMENT", "DINING_RESERVATION")
NB_LECTURES_PARALLELES = 6
DUREE_CONSERVATION = 2 * 3600


def _adresse_lisible(infos: dict) -> str:
    adresse = infos.get("storefrontAddress") or {}
    lignes = ", ".join(adresse.get("addressLines") or [])
    reste = " ".join(p for p in [adresse.get("postalCode", ""), adresse.get("locality", "")] if p)
    return ", ".join(p for p in [lignes, reste] if p)


def preparer(db, clients: list) -> list:
    """
    Travaux a executer (donnees simples, sans objet de base de donnees : ils sont lus ensuite en parallele) : pour chaque client la
    ligne de depart, et les identifiants Google a utiliser (lus ici, dans la session de base de donnees).
    """
    identifiants_par_compte = {}
    travaux = []
    for client in clients:
        nb_posts = sum(1 for p in client.posts if p.statut == "PUBLIE_LIVE")
        ligne = [""] * len(ENTETES)
        for colonne, valeur in (("Nom", client.nom), ("Prénom", client.prenom), ("Email", client.email),
                                ("Compte Google", client.compte_google.libelle if client.compte_google else ""), ("Étiquettes", ", ".join(e.nom for e in client.etiquettes))):
            ligne[ENTETES.index(colonne)] = valeur
        ligne[ENTETES.index("Posts publiés (plateforme)")] = nb_posts
        identifiants, lire = None, bool(client.account_id and client.location_id)
        if lire:
            if client.compte_google_id not in identifiants_par_compte:
                identifiants_par_compte[client.compte_google_id] = google_oauth.obtenir_identifiants(db, client.compte_google_id)
            identifiants = identifiants_par_compte[client.compte_google_id]
            if not identifiants:
                ligne[-1] = "Compte Google non valide"
        travaux.append({"ligne": ligne, "identifiants": identifiants, "account_id": client.account_id, "location_id": client.location_id,
                        "lire": lire and bool(identifiants)})
    return travaux


def _lire_google(travail: dict) -> list:
    """Complete la ligne d'un client avec ce que dit Google (fiche, liens de rendez-vous, avis, photos). Ne leve jamais d'exception."""
    ligne, identifiants = travail["ligne"], travail["identifiants"]
    if not travail["lire"]:
        return ligne
    erreurs = []
    # None = pas pu etre verifie (erreur de lecture) : on n'affirme alors jamais qu'un element manque.
    manquants_champs = None
    manquants_photos = None
    total_infos = score_infos = None
    try:
        infos = google_location.obtenir_infos_fiche(identifiants, travail["location_id"])
        completude = google_location.score_completude(infos)
        manquants_champs = completude["manquants"]
        categorie_principale = ((infos.get("categories") or {}).get("primaryCategory") or {}).get("displayName", "")
        statut_ouverture = (infos.get("openInfo") or {}).get("status", "")
        ligne[COLONNE_NOM_GOOGLE] = infos.get("title", "")          # nom de l'etablissement tel qu'affiche sur Google
        ligne[COLONNE_TELEPHONE] = (infos.get("phoneNumbers") or {}).get("primaryPhone", "")
        ligne[COLONNE_SITE] = infos.get("websiteUri", "")
        ligne[COLONNE_ADRESSE] = _adresse_lisible(infos)
        ligne[COLONNE_CATEGORIE] = categorie_principale
        ligne[COLONNE_OUVERTURE] = google_location.LIBELLES_STATUT_OUVERTURE.get(statut_ouverture, statut_ouverture)
        ligne[COLONNE_COMPLETUDE_INFOS] = f"{completude['score']}/{completude['total']}"
        total_infos, score_infos = completude["total"], completude["score"]
        ligne[COLONNE_LIEN_FICHE] = (infos.get("metadata") or {}).get("mapsUri", "")
    except Exception as erreur:
        erreurs.append(f"Fiche : {erreur}")

    try:
        liens_rdv = [l.get("uri", "") for l in google_place_actions.lister_liens(identifiants, travail["location_id"])
                     if l.get("placeActionType") in TYPES_RDV and l.get("uri")]
        ligne[COLONNE_RDV_PRESENT] = "Oui" if liens_rdv else "Non"
        ligne[COLONNE_RDV_LIEN] = " ; ".join(liens_rdv)
    except Exception as erreur:
        ligne[COLONNE_RDV_PRESENT] = "Non vérifié"
        erreurs.append(f"Liens RDV : {erreur}")

    try:
        resume_avis = google_reviews.resume_rapide(identifiants, travail["account_id"], travail["location_id"])
        ligne[COLONNE_NOTE] = resume_avis["note_moyenne"] if resume_avis["note_moyenne"] is not None else ""
        ligne[COLONNE_NB_AVIS] = resume_avis["total_avis"]
    except Exception as erreur:
        erreurs.append(f"Avis : {erreur}")

    try:
        photos = google_business.lister_photos(identifiants, travail["account_id"], travail["location_id"])
        ligne[COLONNE_PHOTOS] = len(photos)
        manquants_photos = google_location.photos_manquantes(photos)
    except Exception as erreur:
        erreurs.append(f"Photos : {erreur}")

    if manquants_champs is not None or manquants_photos is not None:
        manquants = (manquants_champs or []) + (manquants_photos or [])
        ligne[COLONNE_MANQUANTS] = ", ".join(manquants) if manquants else "Aucun"
    if score_infos is not None and manquants_photos is not None:
        # Note globale : les champs de la fiche + les photos que Google recommande (vitrine, intérieur, logo, couverture).
        total = total_infos + len(google_location.CRITERES_PHOTOS)
        ligne[COLONNE_COMPLETUDE_TOTALE] = f"{total - len(manquants_champs or []) - len(manquants_photos)}/{total}"

    ligne[-1] = " ; ".join(erreurs)
    return ligne


def construire_classeur(lignes: list) -> bytes:
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Clients"
    feuille.append(ENTETES)
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    for ligne in lignes:
        feuille.append(ligne)
    for indice, entete in enumerate(ENTETES, start=1):
        feuille.column_dimensions[get_column_letter(indice)].width = max(14, len(entete) + 2)
    feuille.freeze_panes = "A2"
    feuille.auto_filter.ref = f"A1:{get_column_letter(len(ENTETES))}{max(2, len(lignes) + 1)}"
    tampon = BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def executer(travaux: list, progression=None) -> bytes:
    """Lit Google pour tous les travaux (en parallele, ordre conserve) et renvoie le classeur Excel."""
    faits, verrou = [0], threading.Lock()

    def un_travail(travail):
        ligne = _lire_google(travail)
        with verrou:
            faits[0] += 1
            if progression:
                progression(faits[0], len(travaux))
        return ligne

    with ThreadPoolExecutor(max_workers=NB_LECTURES_PARALLELES) as pool:
        lignes = list(pool.map(un_travail, travaux))
    return construire_classeur(lignes)


def generer_export(db, clients: list) -> bytes:
    """Export complet en une fois (sans suivi d'avancement)."""
    return executer(preparer(db, clients))


# --------------------------------------------------------------------------- export en tache de fond

TACHES: dict = {}


def lancer(db, clients: list, nom_fichier: str) -> str:
    """Prepare les travaux (identifiants lus dans la session de base de donnees), puis lit Google en tache de fond. Renvoie l'identifiant."""
    limite = time.time() - DUREE_CONSERVATION
    for identifiant in [i for i, t in TACHES.items() if t["cree"] < limite]:
        TACHES.pop(identifiant, None)
    travaux = preparer(db, clients)
    identifiant = uuid.uuid4().hex
    tache = {"etat": "en_cours", "faits": 0, "total": len(travaux), "erreur": "", "octets": b"", "nom_fichier": nom_fichier, "cree": time.time()}
    TACHES[identifiant] = tache

    def suivi(faits, total):
        tache["faits"] = faits

    def travail():
        try:
            tache["octets"] = executer(travaux, suivi)
            tache["etat"] = "termine"
        except Exception as erreur:
            tache["etat"], tache["erreur"] = "erreur", str(erreur)[:300] or erreur.__class__.__name__

    threading.Thread(target=travail, daemon=True).start()
    return identifiant


def etat_tache(identifiant: str):
    tache = TACHES.get(identifiant)
    return {k: tache[k] for k in ("etat", "faits", "total", "erreur")} if tache else None


def fichier_tache(identifiant: str):
    """(octets, nom de fichier) d'un export termine, sinon None."""
    tache = TACHES.get(identifiant)
    return (tache["octets"], tache["nom_fichier"]) if tache and tache["etat"] == "termine" else None
