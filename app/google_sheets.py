"""
Ecriture d'un Google Sheet (API Google Sheets v4) : remplit et met en forme des onglets a partir de tableaux neutres
(voir reporting_mensuel.tableaux_par_pays). Le meme fichier est reutilise a chaque mise a jour : les onglets geres sont
reecrits en entier, les autres onglets du fichier ne sont pas touches.
"""

import re
import time

import requests

BASE = "https://sheets.googleapis.com/v4/spreadsheets"
COULEURS = {"entete": "1F4E8C", "bande": "E8EEF8", "total": "D5DEF0", "titre": "1F4E8C"}


class ErreurSheets(Exception):
    pass


def extraire_id(texte: str) -> str:
    """Identifiant d'un Sheet a partir de son adresse (ou de l'identifiant lui-meme) ; chaine vide si non reconnu."""
    texte = (texte or "").strip()
    trouve = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", texte)
    if trouve:
        return trouve.group(1)
    return texte if re.fullmatch(r"[A-Za-z0-9_-]{20,}", texte) else ""


def _message_erreur(reponse) -> str:
    try:
        detail = (reponse.json().get("error") or {}).get("message", "")
    except Exception:
        detail = ""
    minuscule = detail.lower()
    if reponse.status_code == 403 and ("has not been used" in minuscule or "disabled" in minuscule):
        return "L'API Google Sheets n'est pas activée dans le projet Google Cloud de la plateforme (API et services > Bibliothèque > Google Sheets API > Activer)."
    if reponse.status_code == 404:
        return "Sheet introuvable, ou non accessible avec le compte Google connecté."
    if reponse.status_code == 403:
        return "Le compte Google connecté n'a pas le droit de modifier ce Sheet."
    if reponse.status_code == 401:
        return "Connexion Google Sheets expirée : reconnectez-la."
    return f"Erreur Google Sheets (code {reponse.status_code}) : {detail[:200]}"


def _appel(identifiants, methode: str, url: str, **options):
    for essai in range(3):
        reponse = requests.request(methode, url, headers={"Authorization": f"Bearer {identifiants.token}"}, timeout=90, **options)
        if reponse.status_code in (429, 500, 502, 503) and essai < 2:
            time.sleep(5 * (essai + 1))
            continue
        break
    if reponse.status_code >= 400:
        raise ErreurSheets(_message_erreur(reponse))
    return reponse.json()


def _couleur(hexa: str) -> dict:
    return {"red": int(hexa[0:2], 16) / 255, "green": int(hexa[2:4], 16) / 255, "blue": int(hexa[4:6], 16) / 255}


def _nom_plage(nom: str) -> str:
    return "'" + nom.replace("'", "''") + "'"


def _format(sheet_id: int, ligne_debut: int, ligne_fin: int, col_debut: int, col_fin: int, format_cellule: dict) -> dict:
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": ligne_debut, "endRowIndex": ligne_fin, "startColumnIndex": col_debut, "endColumnIndex": col_fin},
        "cell": {"userEnteredFormat": format_cellule},
        "fields": "userEnteredFormat(" + ",".join(format_cellule) + ")",
    }}


def requetes_mise_en_forme(sheet_id: int, tableau: dict, position: int) -> list:
    """Requetes batchUpdate qui dimensionnent, nettoient puis mettent en forme un onglet (avant l'ecriture des valeurs)."""
    lignes, styles = tableau["lignes"], tableau["styles"]
    nb_colonnes = len(lignes[0])
    gel_lignes, gel_colonnes = tableau["gel"]
    requetes = [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "index": position}, "fields": "index"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {
                "rowCount": max(len(lignes) + 20, 100), "columnCount": max(nb_colonnes + 1, 10), "frozenRowCount": gel_lignes, "frozenColumnCount": gel_colonnes}},
            "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
        }},
        {"updateCells": {"range": {"sheetId": sheet_id}, "fields": "userEnteredFormat"}},
        {"clearBasicFilter": {"sheetId": sheet_id}},
    ]
    for indice, largeur in enumerate(tableau["largeurs"]):
        requetes.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": indice, "endIndex": indice + 1}, "properties": {"pixelSize": int(largeur * 7 + 10)}, "fields": "pixelSize"}})
    requetes.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 60}, "fields": "pixelSize"}})

    premiere_numerique = tableau["premiere_numerique"]
    if premiere_numerique < nb_colonnes and len(lignes) > 1:
        requetes.append(_format(sheet_id, 1, len(lignes), premiere_numerique, nb_colonnes - 1, {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}))
        requetes.append(_format(sheet_id, 1, len(lignes), nb_colonnes - 1, nb_colonnes, {"horizontalAlignment": "RIGHT"}))

    # Une requete par suite de lignes de meme style.
    debut = 0
    while debut < len(styles):
        fin = debut
        while fin + 1 < len(styles) and styles[fin + 1] == styles[debut]:
            fin += 1
        style = styles[debut]
        if style == "entete":
            requetes.append(_format(sheet_id, debut, fin + 1, 0, nb_colonnes, {
                "backgroundColor": _couleur(COULEURS["entete"]), "textFormat": {"bold": True, "foregroundColor": _couleur("FFFFFF")},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}))
        elif style == "bande":
            requetes.append(_format(sheet_id, debut, fin + 1, 0, nb_colonnes, {"backgroundColor": _couleur(COULEURS["bande"])}))
        elif style == "total":
            requetes.append(_format(sheet_id, debut, fin + 1, 0, nb_colonnes, {"backgroundColor": _couleur(COULEURS["total"]), "textFormat": {"bold": True}}))
        elif style == "titre":
            requetes.append(_format(sheet_id, debut, fin + 1, 0, 1, {"textFormat": {"bold": True, "fontSize": 12, "foregroundColor": _couleur(COULEURS["titre"])}}))
        debut = fin + 1

    if tableau["filtre"] and len(lignes) > 1:
        requetes.append({"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": len(lignes), "startColumnIndex": 0, "endColumnIndex": nb_colonnes}}}})
    return requetes


def ecrire(identifiants, spreadsheet_id: str, titre_fichier: str, tableaux: list):
    """
    Cree le Sheet si spreadsheet_id est vide, puis (re)ecrit et met en forme chaque onglet de `tableaux` (liste de dicts :
    nom, lignes, styles, largeurs, filtre, gel, premiere_numerique). Renvoie (identifiant, adresse).
    """
    if not tableaux:
        raise ErreurSheets("Rien à écrire.")
    adresse = ""
    if not spreadsheet_id:
        cree = _appel(identifiants, "POST", BASE, json={"properties": {"title": titre_fichier, "locale": "fr_FR"}, "sheets": [{"properties": {"title": tableaux[0]["nom"]}}]})
        spreadsheet_id, adresse = cree["spreadsheetId"], cree.get("spreadsheetUrl", "")
    url_sheet = f"{BASE}/{spreadsheet_id}"

    meta = _appel(identifiants, "GET", url_sheet, params={"fields": "spreadsheetUrl,sheets.properties(sheetId,title,index)"})
    adresse = adresse or meta.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
    ids = {f["properties"]["title"]: f["properties"]["sheetId"] for f in meta.get("sheets", [])}

    manquants = [t for t in tableaux if t["nom"] not in ids]
    if manquants:
        reponse = _appel(identifiants, "POST", f"{url_sheet}:batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": t["nom"]}}} for t in manquants]})
        for tableau, reponse_ajout in zip(manquants, reponse.get("replies", [])):
            ids[tableau["nom"]] = reponse_ajout["addSheet"]["properties"]["sheetId"]

    requetes = []
    for position, tableau in enumerate(tableaux):
        requetes += requetes_mise_en_forme(ids[tableau["nom"]], tableau, position)
    _appel(identifiants, "POST", f"{url_sheet}:batchUpdate", json={"requests": requetes})

    _appel(identifiants, "POST", f"{url_sheet}/values:batchClear", json={"ranges": [_nom_plage(t["nom"]) for t in tableaux]})
    donnees = [{"range": f"{_nom_plage(t['nom'])}!A1", "values": [["" if v is None else v for v in ligne] for ligne in t["lignes"]]} for t in tableaux]
    _appel(identifiants, "POST", f"{url_sheet}/values:batchUpdate", json={"valueInputOption": "RAW", "data": donnees})
    return spreadsheet_id, adresse
