"""
Suivi des agences GoldUnion pour leurs responsables : une page en lecture seule (lien secret) qui regroupe, pour chaque agence,
l'etat de la fiche Google (reliee, validee, groupe), adresse, telephone, horaires, date d'ouverture, fermetures prevues, etat de la
page agence du site, indicateurs Maps du dernier mois et positionnement naturel (DataForSEO, un point a l'agence).

Les donnees sont lues chez Google puis gardees (models.SuiviAgence) : la page ne fait aucun appel externe quand elle s'ouvre.
La lecture se fait en tache de fond (chaque nuit, ou a la demande) ; les positions (payantes, quelques centimes) une fois par semaine.
"""

import json
import re
import secrets
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import geo_technique, google_business, google_location, google_oauth, google_performance, models, rank_tracking, reporting_mensuel

MOTS_CLES_DEFAUT = "achat or,rachat or,vente or,goldunion"
NB_LECTURES_PARALLELES = 6
NB_MESURES_PARALLELES = 4
DUREE_MAX_MAJ = timedelta(hours=2)
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
CODES_JOUR = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_verrou = threading.Lock()


# ---------- reglages

def lire_parametre(db) -> "models.ParametreSuiviGoldUnion":
    """Ligne de reglages (creee au premier appel)."""
    parametre = db.query(models.ParametreSuiviGoldUnion).first()
    if not parametre:
        parametre = models.ParametreSuiviGoldUnion(mots_cles=MOTS_CLES_DEFAUT)
        db.add(parametre)
        db.commit()
    return parametre


def ids_etiquettes(parametre) -> list:
    return [int(x) for x in (parametre.etiquette_ids or "").split(",") if x.strip().isdigit()]


def mots_cles(parametre) -> list:
    return [m.strip() for m in (parametre.mots_cles or MOTS_CLES_DEFAUT).split(",") if m.strip()]


def clients_suivis(db, parametre) -> list:
    """Agences suivies : celles des etiquettes choisies, sinon toutes les etiquettes dont le nom commence par « GoldUnion »."""
    ids = ids_etiquettes(parametre)
    if not ids:
        ids = [e.id for e in db.query(models.Etiquette).all() if (e.nom or "").lower().startswith("goldunion")]
    if not ids:
        return []
    return (
        db.query(models.Client).filter(models.Client.etiquettes.any(models.Etiquette.id.in_(ids))).order_by(models.Client.nom).all()
    )


def nouveau_jeton() -> str:
    return secrets.token_urlsafe(24)


def jeton_valide(parametre, jeton: str) -> bool:
    return bool(parametre and parametre.token and jeton and len(jeton) >= 20 and secrets.compare_digest(parametre.token, jeton))


# ---------- mise en forme des donnees Google

def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texte or "") if not unicodedata.combining(c)).lower()


def _heure(h: dict) -> str:
    heures, minutes = int((h or {}).get("hours") or 0), int((h or {}).get("minutes") or 0)
    return f"{heures}h{minutes:02d}" if minutes else f"{heures}h"


def resumer_horaires(regular_hours: dict) -> str:
    """« Lun–Ven 9h30–12h30, 14h–19h ; Sam 9h–12h » (jours consecutifs identiques regroupes) ; vide si Google n'en donne pas."""
    par_jour = {code: [] for code in CODES_JOUR}
    for periode in (regular_hours or {}).get("periods", []):
        jour = periode.get("openDay")
        if jour in par_jour and (periode.get("openTime") is not None or periode.get("closeTime") is not None):
            ouverture = periode.get("openTime") or {}
            par_jour[jour].append(((int(ouverture.get("hours") or 0), int(ouverture.get("minutes") or 0)), f"{_heure(periode.get('openTime'))}–{_heure(periode.get('closeTime'))}"))
    par_jour = {jour: [texte for _, texte in sorted(plages)] for jour, plages in par_jour.items()}      # plages dans l'ordre de la journee
    if not any(par_jour.values()):
        return ""
    paquets, courant = [], None
    for i, code in enumerate(CODES_JOUR):
        creneaux = tuple(par_jour[code])
        if not creneaux:
            courant = None
            continue
        if courant and courant["creneaux"] == creneaux and courant["fin"] == i - 1:
            courant["fin"] = i
        else:
            courant = {"debut": i, "fin": i, "creneaux": creneaux}
            paquets.append(courant)
    morceaux = []
    for p in paquets:
        longueur = p["fin"] - p["debut"] + 1
        jours = JOURS[p["debut"]] if longueur == 1 else (f"{JOURS[p['debut']]}, {JOURS[p['fin']]}" if longueur == 2 else f"{JOURS[p['debut']]}–{JOURS[p['fin']]}")
        morceaux.append(f"{jours} {', '.join(p['creneaux'])}")
    return " ; ".join(morceaux)


def conges_prevus(special_hours: dict, aujourdhui: date = None, maximum: int = 8) -> list:
    """Dates exceptionnelles a venir : [{"date": "AAAA-MM-JJ", "libelle": "Fermé" | "9h30–12h30, 14h–19h"}], les plus proches d'abord."""
    aujourdhui = aujourdhui or date.today()
    jours = {}
    for periode in (special_hours or {}).get("specialHourPeriods", []):
        debut = periode.get("startDate") or {}
        try:
            jour = date(int(debut["year"]), int(debut["month"]), int(debut["day"]))
        except (KeyError, TypeError, ValueError):
            continue
        fin = periode.get("endDate") or {}
        try:
            dernier = date(int(fin["year"]), int(fin["month"]), int(fin["day"]))
        except (KeyError, TypeError, ValueError):
            dernier = jour
        if dernier < aujourdhui:
            continue
        entree = jours.setdefault(jour, {"ferme": False, "creneaux": [], "jusqu_au": dernier})
        if periode.get("closed") or periode.get("openTime") is None:
            entree["ferme"] = True
        else:
            entree["creneaux"].append(f"{_heure(periode.get('openTime'))}–{_heure(periode.get('closeTime'))}")
    resultat = []
    for jour in sorted(jours):
        entree = jours[jour]
        libelle = "Fermé" if entree["ferme"] or not entree["creneaux"] else ", ".join(entree["creneaux"])
        item = {"date": jour.isoformat(), "libelle": libelle}
        if entree["jusqu_au"] > jour:
            item["jusqu_au"] = entree["jusqu_au"].isoformat()
        resultat.append(item)
    return resultat[:maximum]


def _date_ouverture(infos: dict) -> str:
    d = (infos.get("openInfo") or {}).get("openingDate") or {}
    if not d.get("year"):
        return ""
    return f"{int(d['year']):04d}-{int(d.get('month') or 1):02d}-{int(d.get('day') or 1):02d}" if d.get("day") else f"{int(d['year']):04d}-{int(d.get('month') or 1):02d}"


def _adresse(infos: dict) -> str:
    a = infos.get("storefrontAddress") or {}
    ligne = ", ".join(x for x in (a.get("addressLines") or []) if x)
    ville = " ".join(x for x in (a.get("postalCode"), a.get("locality")) if x)
    return ", ".join(x for x in (ligne, ville) if x)


def verifier_page_site(url: str, ville: str = "") -> dict:
    """
    Etat de la page agence du site : {"etat": "absent" | "ok" | "ville_absente" | "erreur", "code": int, "detail": str}.
    Adresses privees refusees, redirections suivies avec controle ; « ville_absente » = la page repond mais ne parle pas de la ville de l'agence.
    """
    url = (url or "").strip()
    if not url:
        return {"etat": "absent", "code": 0, "detail": "Aucun site renseigné sur la fiche"}
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    courant = url
    try:
        for _ in range(6):
            if not geo_technique._hote_public(urlparse(courant).hostname or ""):
                return {"etat": "erreur", "code": 0, "detail": "Adresse non publique"}
            reponse = requests.get(courant, headers={"User-Agent": geo_technique.UA_NAVIGATEUR}, timeout=geo_technique.DELAI_REQUETE, allow_redirects=False, stream=True)
            if reponse.status_code in (301, 302, 303, 307, 308) and reponse.headers.get("Location"):
                courant = urljoin(courant, reponse.headers["Location"])
                reponse.close()
                continue
            code = reponse.status_code
            if code != 200:
                reponse.close()
                return {"etat": "erreur", "code": code, "detail": f"La page répond {code}"}
            contenu = reponse.raw.read(600_000, decode_content=True).decode(reponse.encoding or "utf-8", errors="ignore")
            reponse.close()
            if ville and _sans_accents(ville) not in _sans_accents(contenu):
                return {"etat": "ville_absente", "code": 200, "detail": f"La page ne mentionne pas « {ville} »"}
            return {"etat": "ok", "code": 200, "detail": ""}
        return {"etat": "erreur", "code": 0, "detail": "Trop de redirections"}
    except requests.RequestException:
        return {"etat": "erreur", "code": 0, "detail": "Site injoignable"}


def lire_agence(travail: dict) -> dict:
    """Donnees d'une agence d'apres sa fiche Google. Ne leve jamais d'exception : les erreurs sont notees dans « erreur »."""
    donnees = {"erreur": "", "fiche": {"creee": bool(travail.get("location_id")), "validee": None, "groupe": travail.get("groupe", "")}}
    identifiants = travail.get("identifiants")
    if not travail.get("location_id") or not travail.get("account_id"):
        donnees["fiche"]["creee"] = False
        donnees["erreur"] = "Fiche Google non reliée à la plateforme."
        return donnees
    if not identifiants:
        donnees["erreur"] = "Compte Google non valide (à reconnecter)."
        return donnees
    try:
        infos = google_location.obtenir_infos_fiche(identifiants, travail["location_id"])
    except Exception as erreur:
        donnees["erreur"] = f"Lecture de la fiche impossible : {str(erreur)[:160]}"
        return donnees
    adresse = infos.get("storefrontAddress") or {}
    ville = (adresse.get("locality") or "").strip()
    latlng = infos.get("latlng") or {}
    donnees.update({
        "nom_google": infos.get("title", ""), "adresse": _adresse(infos), "ville": ville, "pays": adresse.get("regionCode", ""),
        "telephone": (infos.get("phoneNumbers") or {}).get("primaryPhone", ""), "site": infos.get("websiteUri", ""),
        "maps_uri": (infos.get("metadata") or {}).get("mapsUri", ""), "horaires": resumer_horaires(infos.get("regularHours")),
        "date_ouverture": _date_ouverture(infos), "conges": conges_prevus(infos.get("specialHours")),
        "statut_ouverture": google_location.LIBELLES_STATUT_OUVERTURE.get((infos.get("openInfo") or {}).get("status", ""), (infos.get("openInfo") or {}).get("status", "")),
        "lat": latlng.get("latitude"), "lng": latlng.get("longitude"),
    })
    donnees["fiche"]["validee"] = bool(google_location.fiche_validee(infos))
    donnees["site_etat"] = verifier_page_site(donnees["site"], ville or travail.get("ville_secours", ""))
    try:
        fin = date.today().replace(day=1) - timedelta(days=1)
        stats = google_performance.recuperer_statistiques_par_mois(identifiants, travail["location_id"], fin.replace(day=1), fin)
        indicateurs = reporting_mensuel.indicateurs(stats.get((fin.year, fin.month), {}))
        donnees["stats"] = {"mois": f"{fin.year}-{fin.month:02d}", **{k: indicateurs[k] for k in ("vues", "appels", "itineraires", "site")}}
    except Exception as erreur:
        donnees["stats"] = {}
        donnees["erreur"] = f"Statistiques Maps indisponibles : {str(erreur)[:120]}"
    return donnees


def mesurer_positions(donnees: dict, nom_agence: str, mots: list, mesure=None) -> dict:
    """Position naturelle (un point, a l'agence) pour chaque mot-cle + ville. Agences hors France : non mesurees (mots-cles en francais)."""
    mesure = mesure or rank_tracking.verifier_position
    maj = datetime.utcnow().isoformat(timespec="seconds")
    if (donnees.get("pays") or "FR") != "FR":
        return {"maj": maj, "mots": {}, "non_applicable": True}
    ville, lat, lng = donnees.get("ville") or "", donnees.get("lat"), donnees.get("lng")
    if not ville or lat is None or lng is None:
        return {"maj": maj, "mots": {}, "erreur": "Ville ou coordonnées de la fiche introuvables."}
    resultat = {"maj": maj, "mots": {}}
    for mot in mots:
        try:
            position, _, _ = mesure(f"{mot} {ville}", lat, lng, nom_agence)
            resultat["mots"][mot] = position
        except Exception as erreur:
            resultat["erreur"] = str(erreur)[:160]
            break
    return resultat


# ---------- problemes a signaler

def problemes(donnees: dict, groupe_attendu: str = "") -> list:
    """Points d'attention d'une agence (textes courts)."""
    liste = []
    fiche = donnees.get("fiche") or {}
    if not fiche.get("creee"):
        liste.append("Fiche non reliée")
    elif donnees.get("erreur") and not donnees.get("adresse"):
        liste.append("Fiche illisible")
    elif fiche.get("validee") is False:
        liste.append("Fiche non validée")
    if groupe_attendu and fiche.get("creee") and _sans_accents(groupe_attendu) not in _sans_accents(fiche.get("groupe", "")):
        liste.append("Hors du bon groupe")
    etat = (donnees.get("site_etat") or {}).get("etat")
    if etat == "erreur":
        liste.append("Page agence en erreur")
    elif etat == "ville_absente":
        liste.append("Page agence sans la ville")
    elif etat == "absent" and fiche.get("creee"):
        liste.append("Pas de site sur la fiche")
    if donnees.get("statut_ouverture") and "définitivement" in donnees["statut_ouverture"].lower():
        liste.append("Fermée définitivement")
    return liste


def libelle_date_ouverture(valeur: str) -> str:
    parties = (valeur or "").split("-")
    if len(parties) == 3:
        return f"{parties[2]}/{parties[1]}/{parties[0]}"
    if len(parties) == 2:
        return f"{parties[1]}/{parties[0]}"
    return ""


def lignes(db, parametre) -> list:
    """Une entree par agence suivie : {"client", "responsable", "donnees", "positions", "maj_le", "problemes"}, triee par nom."""
    clients = clients_suivis(db, parametre)
    suivis = {s.client_id: s for s in db.query(models.SuiviAgence).filter(models.SuiviAgence.client_id.in_([c.id for c in clients] or [0])).all()}
    resultat = []
    for client in clients:
        suivi = suivis.get(client.id)
        donnees = json.loads(suivi.donnees_json) if suivi and suivi.donnees_json else {}
        positions = json.loads(suivi.positions_json) if suivi and suivi.positions_json else {}
        resultat.append({
            "client": client, "responsable": (client.responsable or "").strip(), "donnees": donnees, "positions": positions,
            "maj_le": suivi.maj_le if suivi else None,
            "problemes": problemes(donnees, parametre.groupe_attendu or "") if donnees else ["Pas encore lue"],
        })
    return resultat


def synthese(entrees: list) -> dict:
    lues = [e for e in entrees if e["donnees"]]
    return {
        "total": len(entrees), "lues": len(lues),
        "validees": sum(1 for e in lues if (e["donnees"].get("fiche") or {}).get("validee")),
        "avec_probleme": sum(1 for e in entrees if e["problemes"]),
        "sites_en_erreur": sum(1 for e in lues if (e["donnees"].get("site_etat") or {}).get("etat") in ("erreur", "ville_absente")),
        "avec_conges": sum(1 for e in lues if e["donnees"].get("conges")),
    }


# ---------- lecture en tache de fond

def _noms_groupes(travaux_identifiants: dict) -> dict:
    """{compte_google_id: {account_id: nom du groupe/compte}} ; une erreur de lecture laisse simplement le nom vide."""
    resultat = {}
    for cle, identifiants in travaux_identifiants.items():
        noms = {}
        if identifiants:
            try:
                for compte in google_business._lister_tous_les_comptes(identifiants):
                    noms[compte["name"].split("/")[-1]] = compte.get("accountName", "")
            except Exception:
                pass
        resultat[cle] = noms
    return resultat


def _noter(session_factory, **champs):
    db = session_factory()
    try:
        parametre = lire_parametre(db)
        for cle, valeur in champs.items():
            setattr(parametre, cle, valeur)
        db.commit()
    finally:
        db.close()


def est_en_cours(parametre) -> bool:
    return bool(parametre and parametre.statut == "en_cours" and parametre.maj_demarree_le and datetime.utcnow() - parametre.maj_demarree_le < DUREE_MAX_MAJ)


def rafraichir(session_factory, avec_positions: bool = False, seulement_positions: bool = False) -> bool:
    """
    Relit les fiches (et mesure les positions si demande). Renvoie False si une mise a jour est deja en cours.
    A lancer dans un fil (voir lancer) : l'etat d'avancement est ecrit dans ParametreSuiviGoldUnion.
    """
    if not _verrou.acquire(blocking=False):
        return False
    try:
        db = session_factory()
        try:
            parametre = lire_parametre(db)
            parametre.statut, parametre.message, parametre.maj_demarree_le = "en_cours", "Préparation…", datetime.utcnow()
            db.commit()
            mots = mots_cles(parametre)
            clients = clients_suivis(db, parametre)
            comptes = {}
            travaux = []
            for client in clients:
                if client.compte_google_id not in comptes:
                    comptes[client.compte_google_id] = google_oauth.obtenir_identifiants(db, client.compte_google_id) if client.account_id else None
                travaux.append({"client_id": client.id, "nom": client.nom, "account_id": client.account_id, "location_id": client.location_id,
                                "compte": client.compte_google_id, "identifiants": comptes[client.compte_google_id],
                                "ville_secours": (client.localisation_ville or "").strip()})
            existants = {s.client_id: s for s in db.query(models.SuiviAgence).all()}
            anciennes = {cid: json.loads(s.donnees_json) for cid, s in existants.items() if s.donnees_json}
        finally:
            db.close()
        total = len(travaux)
        if not total:
            _noter(session_factory, statut="ok", message="Aucune agence suivie : choisissez les étiquettes GoldUnion dans les réglages.", derniere_maj=datetime.utcnow())
            return True

        resultats = {}
        if not seulement_positions:
            noms = _noms_groupes(comptes)
            for t in travaux:
                t["groupe"] = noms.get(t["compte"], {}).get(t["account_id"], "")
            faits = [0]

            def lire(travail):
                resultats[travail["client_id"]] = lire_agence(travail)
                faits[0] += 1
                if faits[0] % 10 == 0:
                    _noter(session_factory, message=f"Lecture des fiches : {faits[0]}/{total}")

            with ThreadPoolExecutor(max_workers=NB_LECTURES_PARALLELES) as pool:
                list(pool.map(lire, travaux))
        donnees_finales = {t["client_id"]: resultats.get(t["client_id"]) or anciennes.get(t["client_id"], {}) for t in travaux}

        positions = {}
        if avec_positions or seulement_positions:
            if not rank_tracking.identifiants_configures():
                _noter(session_factory, message="Positions non mesurées : identifiants DataForSEO manquants.")
            else:
                faits = [0]

                def mesurer(travail):
                    positions[travail["client_id"]] = mesurer_positions(donnees_finales.get(travail["client_id"]) or {}, travail["nom"], mots)
                    faits[0] += 1
                    if faits[0] % 10 == 0:
                        _noter(session_factory, message=f"Positions : {faits[0]}/{total}")

                with ThreadPoolExecutor(max_workers=NB_MESURES_PARALLELES) as pool:
                    list(pool.map(mesurer, travaux))

        db = session_factory()
        try:
            maintenant = datetime.utcnow()
            suivis = {s.client_id: s for s in db.query(models.SuiviAgence).all()}
            for travail in travaux:
                suivi = suivis.get(travail["client_id"])
                if not suivi:
                    suivi = models.SuiviAgence(client_id=travail["client_id"])
                    db.add(suivi)
                if travail["client_id"] in resultats:
                    suivi.donnees_json, suivi.maj_le = json.dumps(resultats[travail["client_id"]]), maintenant
                if travail["client_id"] in positions:
                    suivi.positions_json, suivi.positions_maj_le = json.dumps(positions[travail["client_id"]]), maintenant
            parametre = lire_parametre(db)
            erreurs = sum(1 for r in resultats.values() if r.get("erreur") and not r.get("adresse"))
            parametre.statut = "ok"
            parametre.message = f"{total} agences" + (f", {erreurs} fiche(s) non lue(s)" if erreurs else "")
            if resultats:
                parametre.derniere_maj = maintenant
            if positions:
                parametre.positions_maj_le = maintenant
            db.commit()
        finally:
            db.close()
        return True
    except Exception as erreur:
        try:
            _noter(session_factory, statut="erreur", message=f"Échec de la mise à jour : {str(erreur)[:200]}")
        except Exception:
            pass
        return True
    finally:
        _verrou.release()


def lancer(session_factory, avec_positions: bool = False, seulement_positions: bool = False) -> bool:
    """Demarre la mise a jour dans un fil. False si une mise a jour est deja en cours."""
    db = session_factory()
    try:
        if est_en_cours(lire_parametre(db)):
            return False
    finally:
        db.close()
    threading.Thread(target=rafraichir, args=(session_factory, avec_positions, seulement_positions), daemon=True).start()
    return True


# ---------- responsables : import colle depuis un tableur

def importer_responsables(db, texte: str, clients: list) -> dict:
    """Lignes « agence ; responsable » (ou tabulation) : rapproche par nom (accents, casse et ponctuation ignores). {"mis_a_jour", "inconnus"}."""
    def forme(nom):
        return re.sub(r"[^a-z0-9]", "", _sans_accents(nom))
    index = {}
    for c in clients:
        index[forme(c.nom)] = c
        index.setdefault(forme(c.nom.split(" - ")[-1]), c)
    mis_a_jour, inconnus = 0, []
    for ligne in (texte or "").splitlines():
        morceaux = [m.strip() for m in re.split(r"[;\t]", ligne, maxsplit=1)]
        if len(morceaux) < 2 or not morceaux[0] or not morceaux[1]:
            continue
        client = index.get(forme(morceaux[0]))
        if client:
            client.responsable = morceaux[1][:120]
            mis_a_jour += 1
        else:
            inconnus.append(morceaux[0])
    db.commit()
    return {"mis_a_jour": mis_a_jour, "inconnus": inconnus}


# ---------- export Excel

ENTETES_EXCEL = [
    "Agence", "Responsable", "Pays", "Ville", "Adresse", "Téléphone", "Horaires", "Statut d'ouverture", "Date d'ouverture", "Fermetures / horaires exceptionnels à venir",
    "Fiche Google reliée", "Fiche validée", "Groupe Google", "Site de la fiche", "Page agence : état", "Vues Maps (mois dernier)", "Appels", "Itinéraires", "Clics site",
    "Mois des statistiques", "Points d'attention", "Mise à jour",
]


def generer_excel(entrees: list, mots: list) -> bytes:
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Agences GoldUnion"
    entetes = ENTETES_EXCEL + [f"Position « {m} »" for m in mots]
    feuille.append(entetes)
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    for e in entrees:
        d, fiche = e["donnees"], (e["donnees"].get("fiche") or {})
        etat_site = d.get("site_etat") or {}
        libelles_site = {"ok": "OK", "ville_absente": "Ville absente de la page", "erreur": "En erreur", "absent": "Aucun site"}
        stats = d.get("stats") or {}
        positions = (e["positions"] or {}).get("mots", {})
        feuille.append([
            e["client"].nom, e["responsable"], d.get("pays", ""), d.get("ville", ""), d.get("adresse", ""), d.get("telephone", ""), d.get("horaires", ""),
            d.get("statut_ouverture", ""), libelle_date_ouverture(d.get("date_ouverture", "")),
            " ; ".join(f"{c['date']} : {c['libelle']}" for c in d.get("conges", [])),
            "Oui" if fiche.get("creee") else "Non", {True: "Oui", False: "Non"}.get(fiche.get("validee"), ""), fiche.get("groupe", ""), d.get("site", ""),
            (libelles_site.get(etat_site.get("etat"), "") + (f" ({etat_site['detail']})" if etat_site.get("detail") else "")) if etat_site else "",
            stats.get("vues", ""), stats.get("appels", ""), stats.get("itineraires", ""), stats.get("site", ""), stats.get("mois", ""),
            " ; ".join(e["problemes"]), e["maj_le"].strftime("%d/%m/%Y %H:%M") if e["maj_le"] else "",
        ] + [positions.get(m, "") if positions.get(m) is not None else ("hors résultats" if m in positions else "") for m in mots])
    for i, largeur in enumerate([34, 20, 6, 18, 40, 16, 40, 16, 12, 40, 10, 10, 22, 34, 26, 12, 8, 10, 10, 10, 34, 16] + [14] * len(mots), start=1):
        feuille.column_dimensions[get_column_letter(i)].width = largeur
    feuille.freeze_panes = "B2"
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
