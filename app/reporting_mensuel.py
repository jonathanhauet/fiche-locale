"""
Reporting mensuel par agence : pour une selection de fiches Google, les indicateurs de performance mois par mois
(vues, clics vers le site, appels, demandes d'itineraire, messages), consultables en ligne (page filtrable) ou dans un
classeur Excel a un seul onglet. Une seule requete Google par fiche couvre toute la periode.
"""

import unicodedata
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import google_location, google_oauth, google_performance

MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
NB_MOIS_MAX = 13           # l'API Google conserve environ 18 mois d'historique

VUES_RECHERCHE = ["Vues sur Recherche Google (ordinateur)", "Vues sur Recherche Google (mobile)"]
VUES_MAPS = ["Vues sur Maps (ordinateur)", "Vues sur Maps (mobile)"]

PAYS = {
    "FR": "France", "US": "États-Unis", "GB": "Royaume-Uni", "ES": "Espagne", "IT": "Italie", "BE": "Belgique", "DE": "Allemagne", "CH": "Suisse",
    "LU": "Luxembourg", "PT": "Portugal", "NL": "Pays-Bas", "CA": "Canada", "MC": "Monaco", "AT": "Autriche", "IE": "Irlande", "MA": "Maroc",
    "TN": "Tunisie", "DZ": "Algérie", "SN": "Sénégal", "CI": "Côte d'Ivoire", "RE": "La Réunion", "GP": "Guadeloupe", "MQ": "Martinique", "GF": "Guyane",
    "NC": "Nouvelle-Calédonie", "PF": "Polynésie française", "AD": "Andorre", "PL": "Pologne", "SE": "Suède", "DK": "Danemark", "NO": "Norvège",
    "FI": "Finlande", "GR": "Grèce", "RO": "Roumanie", "AE": "Émirats arabes unis", "AU": "Australie", "MX": "Mexique", "BR": "Brésil",
}
PAYS_INCONNU = "Pays non renseigné"


def nom_pays(code: str) -> str:
    """Nom francais du pays a partir du code ISO a 2 lettres (storefrontAddress.regionCode)."""
    code = (code or "").strip().upper()
    return PAYS.get(code) or code or PAYS_INCONNU


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


def collecter(db, clients: list, mois: list, avec_pays: bool = False) -> list:
    """
    Resultats de lire_fiche pour chaque fiche, dans l'ordre. Les identifiants sont lus ici (session de base de donnees)
    puis les appels Google partent en parallele. avec_pays : lit aussi le pays de l'adresse de la fiche (cle "pays").
    """
    debut, fin = periode(mois)
    travaux, resultats = [], []
    for client in clients:
        entree = {"client": client, "mois": {}, "erreur": "", "pays": ""}
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
        if avec_pays:
            try:
                entree["pays"] = nom_pays(((google_location.obtenir_infos_fiche(identifiants, location_id).get("storefrontAddress")) or {}).get("regionCode"))
            except Exception:
                entree["pays"] = PAYS_INCONNU

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lire, travaux))
    return resultats


def _evolution_positive(precedent: int, dernier: int) -> str:
    """Evolution mise en avant uniquement si elle est favorable (les chiffres reels restent toujours visibles)."""
    if precedent > 0 and dernier > precedent:
        return f"+{round((dernier - precedent) / precedent * 100)} %"
    return ""


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c)).lower()


def _ligne_entete(feuille, titres: list):
    feuille.append(titres)
    for cellule in feuille[feuille.max_row]:
        cellule.font = Font(bold=True, color="FFFFFF")
        cellule.fill = PatternFill("solid", fgColor="1F4E8C")
        cellule.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _fiche_non_lues(classeur, ordonnes: list):
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


def _nom_onglet(nom: str, utilises: set) -> str:
    """Nom d'onglet Excel valide (31 caracteres, sans \\ / ? * [ ] :) et unique."""
    propre = "".join(c for c in nom if c not in "\\/?*[]:")[:31].strip() or "Pays"
    base, n = propre, 2
    while propre.lower() in utilises:
        propre, n = f"{base[:28]} {n}", n + 1
    utilises.add(propre.lower())
    return propre


def _bloc_mensuel(feuille, etiquette_pays: str, nb_agences, groupes: list, mois: list, fond):
    """Ajoute, pour un ensemble d'agences (groupes), une ligne par mois : pays, nombre d'agences, mois, indicateurs, evolution positive."""
    precedent = None
    for m in mois:
        totaux = {cle: sum(indicateurs(e["mois"].get(m, {}))[cle] for e in groupes) for cle, _ in COLONNES}
        evolution = _evolution_positive(precedent, totaux["vues"]) if precedent is not None else ""
        feuille.append([etiquette_pays, nb_agences, libelle_mois(m)] + [totaux[cle] for cle, _ in COLONNES] + [evolution])
        precedent = totaux["vues"]
        if fond:
            for cellule in feuille[feuille.max_row]:
                cellule.fill = fond


def _excel_par_pays(resultats: list, mois: list) -> bytes:
    """
    Classeur « Synthèse » (par pays et par mois, puis toutes agences) + un onglet par pays (une ligne par agence et par mois),
    + « Fiches non lues » si besoin.
    """
    classeur = Workbook()
    synthese = classeur.active
    synthese.title = "Synthèse"
    ordonnes = sorted(resultats, key=lambda r: (r["client"].nom or "").lower())
    valides = [r for r in ordonnes if r["mois"]]
    par_pays = {}
    for entree in valides:
        par_pays.setdefault(entree.get("pays") or PAYS_INCONNU, []).append(entree)
    pays_tries = sorted(par_pays, key=lambda p: (p == PAYS_INCONNU, -len(par_pays[p]), _sans_accents(p)))       # le pays le plus fourni d'abord, l'inconnu en dernier

    entete = ["Pays", "Nb agences", "Mois"] + [libelle for _, libelle in COLONNES] + ["Évolution des vues vs mois précédent"]
    _ligne_entete(synthese, entete)
    bandes = [PatternFill("solid", fgColor="E8EEF8"), None]
    for rang, pays in enumerate(pays_tries):
        _bloc_mensuel(synthese, pays, len(par_pays[pays]), par_pays[pays], mois, bandes[rang % 2])
    if valides:
        synthese.append([])
        synthese.append(["Toutes agences"])
        synthese[synthese.max_row][0].font = Font(bold=True, size=12, color="1F4E8C")
        debut_total = synthese.max_row + 1
        _bloc_mensuel(synthese, "Toutes agences", len(valides), valides, mois, PatternFill("solid", fgColor="D5DEF0"))
        for ligne in synthese.iter_rows(min_row=debut_total, max_row=synthese.max_row):
            for cellule in ligne:
                cellule.font = Font(bold=True)
    synthese.column_dimensions["A"].width = 22
    synthese.column_dimensions["B"].width = 12
    synthese.column_dimensions["C"].width = 12
    for indice in range(4, len(COLONNES) + 5):
        synthese.column_dimensions[get_column_letter(indice)].width = 22
    synthese.row_dimensions[1].height = 48
    synthese.freeze_panes = "D2"

    utilises = {"synthèse", "fiches non lues"}
    for pays in pays_tries:
        feuille = classeur.create_sheet(_nom_onglet(pays, utilises))
        _ligne_entete(feuille, ["Agence", "Mois"] + [libelle for _, libelle in COLONNES] + ["Évolution des vues vs mois précédent"])
        for entree in par_pays[pays]:
            precedent = None
            for m in mois:
                valeurs = indicateurs(entree["mois"].get(m, {}))
                evolution = _evolution_positive(precedent, valeurs["vues"]) if precedent is not None else ""
                feuille.append([entree["client"].nom, libelle_mois(m)] + [valeurs[cle] for cle, _ in COLONNES] + [evolution])
                precedent = valeurs["vues"]
        feuille.auto_filter.ref = f"A1:{get_column_letter(feuille.max_column)}{feuille.max_row}"
        feuille.column_dimensions["A"].width = 38
        feuille.column_dimensions["B"].width = 12
        for indice in range(3, len(COLONNES) + 4):
            feuille.column_dimensions[get_column_letter(indice)].width = 22
        feuille.row_dimensions[1].height = 48
        feuille.freeze_panes = "C2"

    _fiche_non_lues(classeur, ordonnes)
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


def generer_excel(resultats: list, mois: list, par_pays: bool = False) -> bytes:
    """Classeur a un seul onglet « Détail » (une ligne par agence et par mois) + « Fiches non lues » si besoin. par_pays : voir _excel_par_pays."""
    if par_pays:
        return _excel_par_pays(resultats, mois)
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
