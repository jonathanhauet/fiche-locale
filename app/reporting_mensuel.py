"""
Reporting mensuel par agence : pour une selection de fiches Google, les indicateurs de performance mois par mois
(vues, clics vers le site, appels, demandes d'itineraire, messages), dans un classeur Excel comparable d'un mois et
d'une agence a l'autre. Une seule requete Google par fiche couvre toute la periode.
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

# (titre de la feuille, libelle de l'indicateur, fonction de calcul a partir des totaux du mois)
INDICATEURS = [
    ("Vues", "Vues de la fiche sur Google (Recherche + Maps)", lambda t: sum(t.get(l, 0) for l in VUES_RECHERCHE + VUES_MAPS)),
    ("Clics site web", "Clics vers le site web", lambda t: t.get("Clics vers le site web", 0)),
    ("Appels", "Clics sur « Appeler »", lambda t: t.get("Clics sur \"Appeler\"", 0)),
    ("Itinéraires", "Demandes d'itinéraire", lambda t: t.get("Demandes d'itinéraire", 0)),
    ("Messages", "Messages reçus", lambda t: t.get("Messages reçus", 0)),
]
INDICATEURS_DETAIL = INDICATEURS + [
    ("", "dont vues sur Recherche Google", lambda t: sum(t.get(l, 0) for l in VUES_RECHERCHE)),
    ("", "dont vues sur Google Maps", lambda t: sum(t.get(l, 0) for l in VUES_MAPS)),
]


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


def collecter(db, clients: list, mois: list) -> list:
    """
    [{"client": Client, "mois": {(annee, mois): {libelle: total}}, "erreur": str}, ...] dans l'ordre des fiches.
    Les identifiants sont lus ici (session de base de donnees) puis les appels Google partent en parallele.
    """
    debut = date(mois[0][0], mois[0][1], 1)
    fin = date(mois[-1][0], mois[-1][1], monthrange(mois[-1][0], mois[-1][1])[1])
    travaux = []
    resultats = []
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
    """Evolution du dernier mois, mise en avant uniquement si elle est favorable (les chiffres reels restent toujours visibles)."""
    if precedent > 0 and dernier > precedent:
        return f"+{round((dernier - precedent) / precedent * 100)} %"
    return ""


def generer_excel(resultats: list, mois: list) -> bytes:
    classeur = Workbook()
    classeur.remove(classeur.active)
    entete_fond = PatternFill("solid", fgColor="1F4E8C")
    gras_blanc = Font(bold=True, color="FFFFFF")
    ordonnes = sorted(resultats, key=lambda r: (r["client"].nom or "").lower())
    valides = [r for r in ordonnes if r["mois"]]

    def valeur(entree, cle_mois, calcul) -> int:
        return calcul(entree["mois"].get(cle_mois, {}))

    for titre, libelle, calcul in INDICATEURS:
        feuille = classeur.create_sheet(titre)
        feuille.append([libelle])
        feuille["A1"].font = Font(bold=True, size=13)
        feuille.append(["Agence"] + [libelle_mois(m) for m in mois] + ["Total", "Évolution du dernier mois"])
        for cellule in feuille[2]:
            cellule.font, cellule.fill, cellule.alignment = gras_blanc, entete_fond, Alignment(horizontal="center", wrap_text=True)
        totaux_mois = [0] * len(mois)
        for entree in valides:
            valeurs = [valeur(entree, m, calcul) for m in mois]
            totaux_mois = [a + b for a, b in zip(totaux_mois, valeurs)]
            feuille.append([entree["client"].nom] + valeurs + [sum(valeurs), _evolution_positive(valeurs[-2], valeurs[-1]) if len(valeurs) > 1 else ""])
        if len(valides) > 1:
            feuille.append(["Total toutes agences"] + totaux_mois + [sum(totaux_mois), _evolution_positive(totaux_mois[-2], totaux_mois[-1]) if len(mois) > 1 else ""])
            for cellule in feuille[feuille.max_row]:
                cellule.font = Font(bold=True)
        feuille.column_dimensions["A"].width = 38
        for indice in range(2, len(mois) + 4):
            feuille.column_dimensions[get_column_letter(indice)].width = 13 if indice <= len(mois) + 2 else 22
        feuille.freeze_panes = "B3"

    detail = classeur.create_sheet("Détail")
    detail.append(["Agence", "Mois"] + [libelle for _, libelle, _ in INDICATEURS_DETAIL])
    for cellule in detail[1]:
        cellule.font, cellule.fill = gras_blanc, entete_fond
    for entree in valides:
        for m in mois:
            detail.append([entree["client"].nom, libelle_mois(m)] + [valeur(entree, m, calcul) for _, _, calcul in INDICATEURS_DETAIL])
    detail.column_dimensions["A"].width = 38
    detail.column_dimensions["B"].width = 12
    for indice in range(3, len(INDICATEURS_DETAIL) + 3):
        detail.column_dimensions[get_column_letter(indice)].width = 24
    detail.freeze_panes = "C2"
    detail.auto_filter.ref = detail.dimensions        # filtre par agence ou par mois directement dans Excel
    classeur.move_sheet("Détail", offset=-(len(classeur.sheetnames) - 1))     # l'onglet principal s'ouvre en premier
    classeur.active = 0

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
