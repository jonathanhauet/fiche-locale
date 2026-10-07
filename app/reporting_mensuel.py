"""
Reporting mensuel par agence : pour une selection de fiches Google, les indicateurs de performance mois par mois
(vues, clics vers le site, appels, demandes d'itineraire, messages), consultables en ligne (page filtrable) ou dans un
classeur Excel a un seul onglet. Une seule requete Google par fiche couvre toute la periode.
"""

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import google_oauth, google_performance

MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
NB_MOIS_MAX = 13           # l'API Google conserve environ 18 mois d'historique

VUES_RECHERCHE = ["Vues sur Recherche Google (ordinateur)", "Vues sur Recherche Google (mobile)"]
VUES_MAPS = ["Vues sur Maps (ordinateur)", "Vues sur Maps (mobile)"]

# (cle, libelle de colonne) - dans l'ordre d'affichage ; les deux dernieres sont des sous-totaux des vues.
COLONNES = [
    ("vues", "Vues de la fiche sur Google (Recherche + Maps)"),
    ("site", "Clics vers le site web"),
    ("appels", "Clics sur « Appeler »"),
    ("itineraires", "Demandes d'itinéraire"),
    ("messages", "Messages reçus"),
    ("vues_recherche", "dont vues sur Recherche Google"),
    ("vues_maps", "dont vues sur Google Maps"),
]


def indicateurs(totaux: dict) -> dict:
    """Totaux bruts d'un mois (libelles Google) -> indicateurs simples, cles de COLONNES."""
    recherche = sum(totaux.get(l, 0) for l in VUES_RECHERCHE)
    maps = sum(totaux.get(l, 0) for l in VUES_MAPS)
    return {
        "vues": recherche + maps, "site": totaux.get("Clics vers le site web", 0), "appels": totaux.get("Clics sur \"Appeler\"", 0),
        "itineraires": totaux.get("Demandes d'itinéraire", 0), "messages": totaux.get("Messages reçus", 0),
        "vues_recherche": recherche, "vues_maps": maps,
    }


def derniers_mois(mois_fin: date, nb_mois: int) -> list:
    """[(annee, mois), ...] du plus ancien au plus recent, se terminant au mois de mois_fin (inclus)."""
    resultat = []
    annee, mois = mois_fin.year, mois_fin.month
    for _ in range(nb_mois):
        resultat.append((annee, mois))
        mois -= 1
        if mois == 0:
            annee, mois = annee - 1, 12
    return resultat[::-1]


def libelle_mois(annee_mois: tuple) -> str:
    return f"{MOIS_COURTS[annee_mois[1] - 1]} {annee_mois[0]}"


def periode(mois: list) -> tuple:
    """(premier jour du premier mois, dernier jour du dernier mois)."""
    return date(mois[0][0], mois[0][1], 1), date(mois[-1][0], mois[-1][1], monthrange(mois[-1][0], mois[-1][1])[1])


def lire_fiche(db, client, mois: list) -> dict:
    """{"client", "mois": {(annee, mois): totaux}, "erreur"} pour UNE fiche (un seul appel a Google)."""
    entree = {"client": client, "mois": {}, "erreur": ""}
    if not client.account_id or not client.location_id:
        entree["erreur"] = "Pas de fiche Google associée."
        return entree
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        entree["erreur"] = "Compte Google non valide (à reconnecter)."
        return entree
    debut, fin = periode(mois)
    try:
        entree["mois"] = google_performance.recuperer_statistiques_par_mois(identifiants, client.location_id, debut, fin)
    except Exception as erreur:
        entree["erreur"] = str(erreur)[:200]
    return entree


def collecter(db, clients: list, mois: list) -> list:
    """
    Resultats de lire_fiche pour chaque fiche, dans l'ordre. Les identifiants sont lus ici (session de base de donnees)
    puis les appels Google partent en parallele.
    """
    debut, fin = periode(mois)
    travaux, resultats = [], []
    for client in clients:
        entree = {"client": client, "mois": {}, "erreur": ""}
        resultats.append(entree)
        if not client.account_id or not client.location_id:
            entree["erreur"] = "Pas de fiche Google associée."
            continue
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            entree["erreur"] = "Compte Google non valide (à reconnecter)."
            continue
        travaux.append((entree, identifiants, client.location_id))

    def lire(travail):
        entree, identifiants, location_id = travail
        try:
            entree["mois"] = google_performance.recuperer_statistiques_par_mois(identifiants, location_id, debut, fin)
        except Exception as erreur:
            entree["erreur"] = str(erreur)[:200]

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lire, travaux))
    return resultats


def _evolution_positive(precedent: int, dernier: int) -> str:
    """Evolution mise en avant uniquement si elle est favorable (les chiffres reels restent toujours visibles)."""
    if precedent > 0 and dernier > precedent:
        return f"+{round((dernier - precedent) / precedent * 100)} %"
    return ""


def generer_excel(resultats: list, mois: list) -> bytes:
    """Classeur a un seul onglet « Détail » (une ligne par agence et par mois) + « Fiches non lues » si besoin."""
    classeur = Workbook()
    detail = classeur.active
    detail.title = "Détail"
    entete_fond = PatternFill("solid", fgColor="1F4E8C")
    gras_blanc = Font(bold=True, color="FFFFFF")
    ordonnes = sorted(resultats, key=lambda r: (r["client"].nom or "").lower())
    valides = [r for r in ordonnes if r["mois"]]

    detail.append(["Agence", "Mois"] + [libelle for _, libelle in COLONNES] + ["Évolution des vues vs mois précédent"])
    for cellule in detail[1]:
        cellule.font, cellule.fill = gras_blanc, entete_fond
        cellule.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for entree in valides:
        precedent = None
        for m in mois:
            valeurs = indicateurs(entree["mois"].get(m, {}))
            evolution = _evolution_positive(precedent, valeurs["vues"]) if precedent is not None else ""
            detail.append([entree["client"].nom, libelle_mois(m)] + [valeurs[cle] for cle, _ in COLONNES] + [evolution])
            precedent = valeurs["vues"]
    fin_filtre = detail.max_row
    detail.auto_filter.ref = f"A1:{get_column_letter(detail.max_column)}{fin_filtre}"     # filtre par agence ou par mois dans Excel

    if len(valides) > 1:                                  # totaux toutes agences, sous le tableau filtrable
        detail.append([])
        for m in mois:
            totaux = {cle: sum(indicateurs(e["mois"].get(m, {}))[cle] for e in valides) for cle, _ in COLONNES}
            detail.append(["Toutes agences", libelle_mois(m)] + [totaux[cle] for cle, _ in COLONNES])
            for cellule in detail[detail.max_row]:
                cellule.font = Font(bold=True)

    detail.column_dimensions["A"].width = 38
    detail.column_dimensions["B"].width = 12
    for indice in range(3, len(COLONNES) + 4):
        detail.column_dimensions[get_column_letter(indice)].width = 24
    detail.row_dimensions[1].height = 48
    detail.freeze_panes = "C2"

    en_erreur = [r for r in ordonnes if not r["mois"]]
    if en_erreur:
        notes = classeur.create_sheet("Fiches non lues")
        notes.append(["Agence", "Raison"])
        for cellule in notes[1]:
            cellule.font = Font(bold=True)
        for entree in en_erreur:
            notes.append([entree["client"].nom, entree["erreur"] or "Aucune donnée renvoyée par Google."])
        notes.column_dimensions["A"].width = 38
        notes.column_dimensions["B"].width = 70

    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
