"""
Suivi des agences GoldUnion pour leurs responsables : une page en lecture seule (lien secret) qui regroupe, pour chaque agence,
l'etat de la fiche Google (reliee, validee, groupe), adresse, telephone, horaires, date d'ouverture, fermetures prevues, etat de la
page agence du site, avis (note, nouveaux avis, avis sans reponse), indicateurs Maps du dernier mois et positionnement naturel
(DataForSEO, grille de points autour de l'agence, avec l'evolution d'un mois a l'autre).

Les donnees sont lues chez Google puis gardees (models.SuiviAgence) : la page ne fait aucun appel externe quand elle s'ouvre.
Les fiches et les avis sont relus chaque nuit ; les positions (payantes) une fois par mois.
"""

import html as html_stdlib
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

import time

from . import geo_technique, geocodage, google_business, google_location, google_oauth, google_performance, google_reviews, models, rank_tracking, reporting_mensuel

MOTS_CLES_DEFAUT = "achat or,rachat or,vente or,goldunion"
TAILLES_GRILLE = (1, 3, 5)                    # 1 = un seul point, a l'adresse de l'agence
ECARTS_KM = (1.0, 2.0, 5.0)
COUT_PAR_POINT_CENTIMES = 0.2                 # 0,002 $ par requete DataForSEO (releve sur le solde du compte : 36 requetes = 0,072 $)
SEUIL_BAISSE_VISIBILITE = 10                  # points de visibilite perdus d'un mois a l'autre pour etre signale
NB_LECTURES_PARALLELES = 6
NB_MESURES_PARALLELES = 8
DUREE_MAX_MAJ = timedelta(hours=2)
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
CODES_JOUR = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_verrou = threading.Lock()
_verrou_geocodage = threading.Lock()
_dernier_geocodage = [0.0]


# ---------- reglages

def lire_parametre(db) -> "models.ParametreSuiviGoldUnion":
    """Ligne de reglages (creee au premier appel)."""
    parametre = db.query(models.ParametreSuiviGoldUnion).first()
    if not parametre:
        parametre = models.ParametreSuiviGoldUnion(mots_cles=MOTS_CLES_DEFAUT, taille_grille=3, ecart_km=2.0)
        db.add(parametre)
        db.commit()
    return parametre


def ids_etiquettes(parametre) -> list:
    return [int(x) for x in (parametre.etiquette_ids or "").split(",") if x.strip().isdigit()]


def mots_cles(parametre) -> list:
    return [m.strip() for m in (parametre.mots_cles or MOTS_CLES_DEFAUT).split(",") if m.strip()]


def taille_grille(parametre) -> int:
    return parametre.taille_grille if parametre.taille_grille in TAILLES_GRILLE else 3


def ecart_km(parametre) -> float:
    return float(parametre.ecart_km) if parametre.ecart_km in ECARTS_KM else 2.0


def cout_mesure_centimes(nb_agences: int, nb_mots: int, taille: int) -> float:
    """Cout estime d'une mesure complete des positions (une requete DataForSEO par agence, mot-cle et point de la grille)."""
    return round(nb_agences * nb_mots * taille * taille * COUT_PAR_POINT_CENTIMES, 1)


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


def geocoder_adresse(adresse: str) -> tuple:
    """(latitude, longitude) d'une adresse (Nominatim, au plus une requete par seconde pour tous les fils), ou (None, None)."""
    if not (adresse or "").strip(", "):
        return None, None
    with _verrou_geocodage:
        attente = 1.1 - (time.monotonic() - _dernier_geocodage[0])
        if attente > 0:
            time.sleep(attente)
        try:
            lieux = geocodage.rechercher_lieu(adresse)
        except Exception:
            lieux = []
        finally:
            _dernier_geocodage[0] = time.monotonic()
    return (lieux[0]["latitude"], lieux[0]["longitude"]) if lieux else (None, None)


def trouver_coordonnees(infos: dict, travail: dict) -> tuple:
    """
    Coordonnees de l'agence : celles deja gardees sur la fiche de la plateforme, sinon celles de Google (rarement renseignees),
    sinon l'adresse geocodee (d'abord l'adresse complete, puis le code postal et la ville).
    """
    if travail.get("lat_cache") is not None and travail.get("lng_cache") is not None:
        return travail["lat_cache"], travail["lng_cache"]
    latitude, longitude = google_location.coordonnees(infos)
    if latitude is not None and longitude is not None:
        return latitude, longitude
    adresse = infos.get("storefrontAddress") or {}
    for texte in (google_location.adresse_texte(infos), ", ".join(x for x in (" ".join(y for y in (adresse.get("postalCode"), adresse.get("locality")) if y), adresse.get("regionCode")) if x)):
        latitude, longitude = geocoder_adresse(texte)
        if latitude is not None:
            return latitude, longitude
    return None, None


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


def _forme_comparable(texte: str) -> str:
    """Minuscules, sans accents, ponctuation et tirets remplaces par des espaces : « Saint-Brieuc » = « Saint Brieuc » = « SAINT‑BRIEUC »."""
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", _sans_accents(texte)).split()) + " "


MOTS_VILLE_BANALS = {"saint", "sainte", "st", "ste", "le", "la", "les", "l", "de", "du", "des", "d", "sur", "sous", "en", "aux", "au", "et", "lez", "les"}


def ville_dans_page(ville: str, texte: str, adresse: str = "") -> bool:
    """
    La ville de l'agence figure-t-elle dans la page ? Soit en entier (« Saint-Brieuc » = « Saint Brieuc »), soit par tous ses mots significatifs, dans
    n'importe quel ordre (« Saint-Denis Réunion » : la page parle de « Saint-Denis 97400 » et de « La Réunion »). Les mots banals (saint, le, la, sur...) ne comptent pas.
    """
    cherchee = _forme_comparable(ville)
    ensemble = _forme_comparable(texte) + _forme_comparable(adresse)
    if cherchee in ensemble:
        return True
    mots = [m for m in cherchee.split() if m not in MOTS_VILLE_BANALS and len(m) >= 3]
    return bool(mots) and all(f" {m} " in ensemble for m in mots)


def _texte_visible(contenu: str) -> str:
    """Texte lisible d'une page : sans scripts, styles ni balises, entites HTML decodees."""
    sans_code = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", contenu or "")
    return " ".join(html_stdlib.unescape(re.sub(r"(?s)<[^>]+>", " ", sans_code)).split())


SEUIL_PAGE_DYNAMIQUE = 400      # moins de caracteres de texte : la page se construit probablement par JavaScript, on ne peut pas y chercher la ville


def verifier_page_site(url: str, ville: str = "") -> dict:
    """
    Etat de la page agence du site : {"etat": "absent" | "ok" | "ville_absente" | "indeterminee" | "erreur", "code": int, "detail": str}.
    Adresses privees refusees, redirections suivies avec controle ; « ville_absente » = la page repond mais ne parle pas de la ville de l'agence
    (la ville est cherchee dans le texte de la page ou dans son adresse, sans tenir compte des accents, des majuscules ni des tirets) ;
    « indeterminee » = page quasiment vide sans JavaScript : impossible de dire si la ville y figure.
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
            if ville:
                texte = _texte_visible(contenu)
                if not ville_dans_page(ville, texte, courant):
                    if len(texte) < SEUIL_PAGE_DYNAMIQUE:
                        return {"etat": "indeterminee", "code": 200, "detail": "Page construite par JavaScript : à vérifier à la main"}
                    return {"etat": "ville_absente", "code": 200, "detail": f"La page ne mentionne pas « {ville} »"}
            return {"etat": "ok", "code": 200, "detail": ""}
        return {"etat": "erreur", "code": 0, "detail": "Trop de redirections"}
    except requests.RequestException:
        return {"etat": "erreur", "code": 0, "detail": "Site injoignable"}


# ---------- avis

def _date_avis(texte: str):
    try:
        return datetime.fromisoformat(re.sub(r"\.\d+", "", texte or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def lire_avis(identifiants, account_id: str, location_id: str, maintenant: datetime = None) -> dict:
    """
    Avis d'une fiche en UN appel (50 avis les plus recents + note moyenne et total donnes par Google) : note, total, nouveaux avis sur 30 jours,
    avis sans reponse (60 jours) et avis negatifs (1-2 etoiles) sans reponse (90 jours).
    """
    maintenant = maintenant or datetime.utcnow()
    reponse = requests.get(f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews",
                           headers={"Authorization": f"Bearer {identifiants.token}"}, params={"pageSize": 50}, timeout=30)
    if reponse.status_code != 200:
        raise RuntimeError(f"Lecture des avis impossible (code {reponse.status_code})")
    donnees = reponse.json()
    avis = donnees.get("reviews", [])
    nouveaux = sans_reponse = negatifs = 0
    for a in avis:
        cree = _date_avis(a.get("createTime"))
        if not cree:
            continue
        age = (maintenant - cree).days
        note = google_reviews.NOTES.get(a.get("starRating"), 0)
        repondu = bool((a.get("reviewReply") or {}).get("comment"))
        nouveaux += age <= 30
        sans_reponse += (age <= 60 and not repondu)
        negatifs += (age <= 90 and not repondu and 1 <= note <= 2)
    note_moyenne = donnees.get("averageRating")
    return {
        "total": int(donnees.get("totalReviewCount") or 0), "note": round(float(note_moyenne), 1) if note_moyenne is not None else None,
        "nouveaux_30j": nouveaux, "nouveaux_plus": len(avis) >= 50 and nouveaux >= 50, "sans_reponse": sans_reponse, "negatifs_sans_reponse": negatifs,
        "maj": maintenant.isoformat(timespec="seconds"),
    }


def _historique_avis(ancien: list, avis: dict, aujourdhui: date = None) -> list:
    """Une valeur par mois (la derniere connue) : sert a dire comment la note et le nombre d'avis evoluent d'un mois a l'autre."""
    mois = (aujourdhui or date.today()).strftime("%Y-%m")
    return ([h for h in (ancien or []) if h.get("mois") != mois] + [{"mois": mois, "total": avis["total"], "note": avis["note"]}])[-14:]


def resume_avis(avis: dict) -> dict:
    """Avis d'une agence avec l'evolution depuis la fin du mois precedent (None tant qu'aucun mois precedent n'est connu)."""
    if not avis or avis.get("total") is None:
        return {}
    historique = avis.get("historique") or []
    mois = historique[-1]["mois"] if historique else None
    precedent = next((h for h in reversed(historique[:-1]) if mois and h.get("mois", "") < mois), None)
    evolution_total = avis["total"] - precedent["total"] if precedent else None
    evolution_note = round(avis["note"] - precedent["note"], 1) if precedent and avis.get("note") is not None and precedent.get("note") is not None else None
    return {**{k: avis.get(k) for k in ("total", "note", "nouveaux_30j", "nouveaux_plus", "sans_reponse", "negatifs_sans_reponse")},
            "evolution_total": evolution_total, "evolution_note": evolution_note}


# ---------- lecture d'une agence

def lire_agence(travail: dict) -> dict:
    """Donnees d'une agence d'apres sa fiche Google. Ne leve jamais d'exception : les erreurs sont notees dans « erreur »."""
    donnees = {"erreur": "", "fiche": {"creee": bool(travail.get("location_id")), "validee": None, "groupe": travail.get("groupe", "")}}
    identifiants = travail.get("identifiants")
    ancien_avis = travail.get("avis_ancien") or {}
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
    latitude, longitude = trouver_coordonnees(infos, travail)
    donnees.update({
        "nom_google": infos.get("title", ""), "adresse": _adresse(infos), "ville": ville, "pays": adresse.get("regionCode", ""),
        "telephone": (infos.get("phoneNumbers") or {}).get("primaryPhone", ""), "site": infos.get("websiteUri", ""),
        "maps_uri": (infos.get("metadata") or {}).get("mapsUri", ""), "horaires": resumer_horaires(infos.get("regularHours")),
        "date_ouverture": _date_ouverture(infos), "conges": conges_prevus(infos.get("specialHours")),
        "statut_ouverture": google_location.LIBELLES_STATUT_OUVERTURE.get((infos.get("openInfo") or {}).get("status", ""), (infos.get("openInfo") or {}).get("status", "")),
        "lat": latitude, "lng": longitude,
    })
    donnees["fiche"]["validee"] = bool(google_location.fiche_validee(infos))
    donnees["site_etat"] = verifier_page_site(donnees["site"], ville or travail.get("ville_secours", ""))
    erreurs = []
    try:
        fin = date.today().replace(day=1) - timedelta(days=1)
        stats = google_performance.recuperer_statistiques_par_mois(identifiants, travail["location_id"], fin.replace(day=1), fin)
        indicateurs = reporting_mensuel.indicateurs(stats.get((fin.year, fin.month), {}))
        donnees["stats"] = {"mois": f"{fin.year}-{fin.month:02d}", **{k: indicateurs[k] for k in ("vues", "appels", "itineraires", "site")}}
    except Exception as erreur:
        donnees["stats"] = {}
        erreurs.append(f"Statistiques Maps indisponibles : {str(erreur)[:100]}")
    try:
        avis = lire_avis(identifiants, travail["account_id"], travail["location_id"])
        avis["historique"] = _historique_avis(ancien_avis.get("historique"), avis)
        donnees["avis"] = avis
    except Exception as erreur:
        donnees["avis"] = ancien_avis                                       # on garde les derniers avis connus plutot que de tout effacer
        erreurs.append(f"Avis : {str(erreur)[:100]}")
    donnees["erreur"] = " ; ".join(erreurs)
    return donnees


# ---------- positions naturelles (grille autour de l'agence)

def mesurer_positions(donnees: dict, nom_agence: str, mots: list, taille: int = 3, ecart: float = 2.0, mesure=None) -> dict:
    """
    Position naturelle de l'agence pour chaque mot-cle + ville, en chaque point d'une grille taille x taille (ecart en km entre points) centree sur la
    fiche. Resultat : {"mots": {mot: [position ou None, ...]}} (points de la grille du sud-ouest au nord-est). Agences hors France : non mesurees.
    """
    mesure = mesure or rank_tracking.verifier_position
    base = {"maj": datetime.utcnow().isoformat(timespec="seconds"), "taille": taille, "ecart_km": ecart}
    if (donnees.get("pays") or "FR") != "FR":
        return {**base, "mots": {}, "non_applicable": True}
    ville, lat, lng = donnees.get("ville") or "", donnees.get("lat"), donnees.get("lng")
    if not ville:
        return {**base, "mots": {}, "erreur": "Ville introuvable sur la fiche Google."}
    if lat is None or lng is None:
        return {**base, "mots": {}, "erreur": "Adresse de la fiche non localisable (coordonnées introuvables)."}
    points = rank_tracking.generer_points_grille(lat, lng, taille, ecart)
    resultat = {**base, "mots": {}, "centre": [lat, lng]}
    for mot in mots:
        liste = []
        for latitude, longitude in points:
            try:
                position, _, _ = mesure(f"{mot} {ville}", latitude, longitude, nom_agence)
            except Exception as erreur:
                resultat["erreur"] = str(erreur)[:160]
                return resultat
            liste.append(position)
        resultat["mots"][mot] = liste
    return resultat


def _score_positions(mots: dict) -> dict:
    valeurs = [p for liste in mots.values() for p in liste]
    total = len(valeurs)
    top3 = sum(1 for p in valeurs if p is not None and p <= 3)
    top10 = sum(1 for p in valeurs if p is not None and p <= 10)
    return {"total": total, "top3": top3, "top10": top10, "pct3": round(100 * top3 / total) if total else 0, "pct10": round(100 * top10 / total) if total else 0}


def fusionner_positions(ancien: dict, nouveau: dict, aujourdhui: date = None) -> dict:
    """
    Garde l'historique mensuel (une ligne par mois, la plus recente) : une mesure complete s'ajoute, une mesure en erreur ou incomplete laisse
    l'ancienne en place avec le message d'erreur.
    """
    ancien = ancien or {}
    if nouveau.get("erreur") or nouveau.get("non_applicable") or not nouveau.get("mots"):
        return {**ancien, "erreur": nouveau.get("erreur", ""), "non_applicable": nouveau.get("non_applicable", False)} if ancien.get("mots") else nouveau
    mois = (aujourdhui or date.today()).strftime("%Y-%m")
    score = _score_positions(nouveau["mots"])
    historique = [h for h in ancien.get("historique", []) if h.get("mois") != mois] + [{"mois": mois, "pct3": score["pct3"], "pct10": score["pct10"], "top3": score["top3"], "top10": score["top10"], "total": score["total"]}]
    return {**nouveau, "historique": historique[-14:]}


def grille_nord_en_haut(positions: list, taille: int) -> list:
    """Lignes de la grille pour l'affichage : le nord en haut, l'ouest a gauche."""
    lignes = [positions[i * taille:(i + 1) * taille] for i in range(taille)]
    return lignes[::-1]


def donnees_carte(positions: dict, centre=None) -> dict:
    """Ce qu'il faut pour dessiner la mesure sur une carte : centre, points de la grille (meme ordre que les positions) et position par recherche ; {} sans centre connu."""
    centre = positions.get("centre") or centre
    mots = positions.get("mots") or {}
    taille = positions.get("taille") or 3
    if not centre or centre[0] is None or centre[1] is None or not mots:
        return {}
    points = rank_tracking.generer_points_grille(centre[0], centre[1], taille, positions.get("ecart_km") or 2.0)
    if any(len(liste) != len(points) for liste in mots.values()):
        return {}
    return {"centre": [round(centre[0], 6), round(centre[1], 6)], "points": [[round(la, 6), round(lo, 6)] for la, lo in points], "taille": taille,
            "ecart_km": positions.get("ecart_km") or 2.0, "mots": mots}


def resume_positions(positions: dict, centre=None) -> dict:
    """Visibilite d'une agence (part des points ou elle est dans le top 3 / top 10) et son evolution depuis le mois precedent ; {} si pas encore mesuree."""
    mots = (positions or {}).get("mots") or {}
    if not mots or not any(mots.values()):
        return {}
    taille = positions.get("taille") or 3
    score = _score_positions(mots)
    historique = positions.get("historique") or []
    mois = historique[-1]["mois"] if historique else None
    precedent = next((h for h in reversed(historique[:-1]) if mois and h.get("mois", "") < mois), None)
    par_mot = {}
    for mot, liste in mots.items():
        par_mot[mot] = {"top3": sum(1 for p in liste if p is not None and p <= 3), "top10": sum(1 for p in liste if p is not None and p <= 10), "total": len(liste),
                        "grille": grille_nord_en_haut(liste, taille) if len(liste) == taille * taille else [liste]}
    return {**score, "taille": taille, "ecart_km": positions.get("ecart_km"), "mois": mois, "par_mot": par_mot, "carte": donnees_carte(positions, centre),
            "evolution": score["pct3"] - precedent["pct3"] if precedent else None, "evolution10": score["pct10"] - precedent["pct10"] if precedent else None,
            "mois_precedent": precedent["mois"] if precedent else None, "maj": positions.get("maj", "")}


# ---------- points d'attention

def problemes(donnees: dict, groupe_attendu: str = "", positions: dict = None) -> list:
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
    avis = resume_avis(donnees.get("avis") or {})
    if avis.get("negatifs_sans_reponse"):
        liste.append("Avis négatif sans réponse")
    if (avis.get("sans_reponse") or 0) >= 3:
        liste.append(f"{avis['sans_reponse']} avis sans réponse")
    if avis.get("evolution_note") is not None and avis["evolution_note"] <= -0.1:
        liste.append("Note en baisse")
    visibilite = resume_positions(positions or {})
    if visibilite:
        if visibilite["evolution"] is not None and visibilite["evolution"] <= -SEUIL_BAISSE_VISIBILITE:
            liste.append(f"Visibilité en baisse ({visibilite['evolution']} pts)")
        if visibilite["top10"] == 0:
            liste.append("Absente du top 10")
    return liste


STATUTS_FICHE = [
    ("validee", "Fiche validée"), ("non_validee", "Fiche non validée"), ("non_reliee", "Fiche non reliée"),
    ("ouverte", "Ouverte"), ("fermee_temp", "Fermée temporairement"), ("fermee_def", "Fermée définitivement"), ("statut_non_renseigne", "Statut d'ouverture non renseigné"),
]


def statuts_fiche(donnees: dict) -> list:
    """Codes de statut d'une agence (voir STATUTS_FICHE) : validation de la fiche et statut d'ouverture ; vide si la fiche n'a pas pu etre lue."""
    if not donnees:
        return []
    fiche = donnees.get("fiche") or {}
    if not fiche.get("creee"):
        return ["non_reliee"]
    if not donnees.get("adresse"):
        return []
    statuts = ["validee" if fiche.get("validee") else "non_validee"]
    ouverture = (donnees.get("statut_ouverture") or "").lower()
    statuts.append("fermee_def" if "définitivement" in ouverture else "fermee_temp" if "temporairement" in ouverture else "ouverte" if ouverture.startswith("ouvert") else "statut_non_renseigne")
    return statuts


def libelle_date_ouverture(valeur: str) -> str:
    parties = (valeur or "").split("-")
    if len(parties) == 3:
        return f"{parties[2]}/{parties[1]}/{parties[0]}"
    if len(parties) == 2:
        return f"{parties[1]}/{parties[0]}"
    return ""


def lignes(db, parametre) -> list:
    """Une entree par agence suivie : {"client", "donnees", "positions", "pos", "avis", "maj_le", "problemes"}, triee par nom."""
    clients = clients_suivis(db, parametre)
    suivis = {s.client_id: s for s in db.query(models.SuiviAgence).filter(models.SuiviAgence.client_id.in_([c.id for c in clients] or [0])).all()}
    resultat = []
    for client in clients:
        suivi = suivis.get(client.id)
        donnees = json.loads(suivi.donnees_json) if suivi and suivi.donnees_json else {}
        positions = json.loads(suivi.positions_json) if suivi and suivi.positions_json else {}
        resultat.append({
            "client": client, "donnees": donnees, "positions": positions, "pos": resume_positions(positions, [donnees.get("lat"), donnees.get("lng")]), "avis": resume_avis(donnees.get("avis") or {}),
            "maj_le": suivi.maj_le if suivi else None, "statuts": statuts_fiche(donnees),
            "problemes": problemes(donnees, parametre.groupe_attendu or "", positions) if donnees else ["Pas encore lue"],
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
        "avis_sans_reponse": sum(1 for e in lues if (e["avis"].get("sans_reponse") or 0) > 0),
        "visibilite_en_baisse": sum(1 for e in lues if e["pos"] and e["pos"]["evolution"] is not None and e["pos"]["evolution"] <= -SEUIL_BAISSE_VISIBILITE),
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


def rafraichir(session_factory, avec_positions: bool = False, seulement_positions: bool = False, client_id: int = None) -> bool:
    """
    Relit les fiches et les avis (et mesure les positions si demande). Renvoie False si une mise a jour est deja en cours.
    client_id : ne traiter que cette agence (les autres gardent leurs donnees, et les dates de derniere mise a jour generales ne changent pas).
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
            mots, taille, ecart = mots_cles(parametre), taille_grille(parametre), ecart_km(parametre)
            clients = [c for c in clients_suivis(db, parametre) if client_id is None or c.id == client_id]
            comptes = {}
            existants = {s.client_id: s for s in db.query(models.SuiviAgence).all()}
            anciennes = {cid: json.loads(s.donnees_json) for cid, s in existants.items() if s.donnees_json}
            anciennes_positions = {cid: json.loads(s.positions_json) for cid, s in existants.items() if s.positions_json}
            travaux = []
            for client in clients:
                if client.compte_google_id not in comptes:
                    comptes[client.compte_google_id] = google_oauth.obtenir_identifiants(db, client.compte_google_id) if client.account_id else None
                travaux.append({"client_id": client.id, "nom": client.nom, "account_id": client.account_id, "location_id": client.location_id,
                                "compte": client.compte_google_id, "identifiants": comptes[client.compte_google_id],
                                "ville_secours": (client.localisation_ville or "").strip(), "lat_cache": client.latitude, "lng_cache": client.longitude,
                                "avis_ancien": (anciennes.get(client.id) or {}).get("avis") or {}})
        finally:
            db.close()
        total = len(travaux)
        if not total:
            if client_id is not None:
                _noter(session_factory, statut="ok", message="Agence introuvable parmi les agences suivies.")
                return True
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
                    positions[travail["client_id"]] = mesurer_positions(donnees_finales.get(travail["client_id"]) or {}, travail["nom"], mots, taille, ecart)
                    faits[0] += 1
                    if faits[0] % 5 == 0:
                        _noter(session_factory, message=f"Positions : {faits[0]}/{total} agences")

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
                    lue = resultats[travail["client_id"]]
                    if lue.get("lat") is not None and lue.get("lng") is not None and travail.get("lat_cache") is None:
                        client = db.get(models.Client, travail["client_id"])         # coordonnees gardees sur la fiche : plus de geocodage la fois suivante
                        if client is not None and client.latitude is None:
                            client.latitude, client.longitude = lue["lat"], lue["lng"]
                if travail["client_id"] in positions:
                    suivi.positions_json = json.dumps(fusionner_positions(anciennes_positions.get(travail["client_id"]), positions[travail["client_id"]]))
                    suivi.positions_maj_le = maintenant
            parametre = lire_parametre(db)
            illisibles = sum(1 for r in resultats.values() if r.get("erreur") and not r.get("adresse"))
            echecs_positions = sum(1 for p in positions.values() if p.get("erreur"))
            parametre.statut = "ok"
            if client_id is not None:
                parametre.message = f"{travaux[0]['nom']} mise à jour" + (" (fiche non lue)" if illisibles else "") + (f" (mesure en erreur : {next(iter(positions.values())).get('erreur')})" if echecs_positions else "")
            else:
                parametre.message = f"{total} agences" + (f", {illisibles} fiche(s) non lue(s)" if illisibles else "") + (f", {echecs_positions} mesure(s) de position en erreur" if echecs_positions else "")
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


def lancer(session_factory, avec_positions: bool = False, seulement_positions: bool = False, client_id: int = None) -> bool:
    """Demarre la mise a jour (de toutes les agences, ou d'une seule) dans un fil. False si une mise a jour est deja en cours."""
    db = session_factory()
    try:
        if est_en_cours(lire_parametre(db)):
            return False
    finally:
        db.close()
    threading.Thread(target=rafraichir, args=(session_factory, avec_positions, seulement_positions, client_id), daemon=True).start()
    return True


# ---------- export Excel

ENTETES_EXCEL = [
    "Agence", "Pays", "Ville", "Adresse", "Téléphone", "Horaires", "Statut d'ouverture", "Date d'ouverture", "Fermetures / horaires exceptionnels à venir",
    "Fiche Google reliée", "Fiche validée", "Groupe Google", "Site de la fiche", "Page agence : état",
    "Note moyenne", "Nombre d'avis", "Nouveaux avis (30 j)", "Avis sans réponse (60 j)", "Avis négatifs sans réponse (90 j)", "Évolution du nombre d'avis (vs mois dernier)", "Évolution de la note (vs mois dernier)",
    "Vues Maps (mois dernier)", "Appels", "Itinéraires", "Clics site", "Mois des statistiques",
    "Visibilité : points dans le top 3 (%)", "Visibilité : points dans le top 10 (%)", "Évolution top 3 (pts vs mois dernier)", "Mesure des positions",
    "Points d'attention", "Mise à jour",
]


def generer_excel(entrees: list, mots: list) -> bytes:
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Agences GoldUnion"
    feuille.append(ENTETES_EXCEL + [f"Top 3 « {m} » (points)" for m in mots])
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    libelles_site = {"ok": "OK", "ville_absente": "Ville absente de la page", "indeterminee": "À vérifier à la main (page dynamique)", "erreur": "En erreur", "absent": "Aucun site"}
    for e in entrees:
        d, fiche, avis, pos = e["donnees"], (e["donnees"].get("fiche") or {}), e["avis"], e["pos"]
        etat_site = d.get("site_etat") or {}
        stats = d.get("stats") or {}
        feuille.append([
            e["client"].nom, d.get("pays", ""), d.get("ville", ""), d.get("adresse", ""), d.get("telephone", ""), d.get("horaires", ""),
            d.get("statut_ouverture", ""), libelle_date_ouverture(d.get("date_ouverture", "")),
            " ; ".join(f"{c['date']} : {c['libelle']}" for c in d.get("conges", [])),
            "Oui" if fiche.get("creee") else "Non", {True: "Oui", False: "Non"}.get(fiche.get("validee"), ""), fiche.get("groupe", ""), d.get("site", ""),
            (libelles_site.get(etat_site.get("etat"), "") + (f" ({etat_site['detail']})" if etat_site.get("detail") else "")) if etat_site else "",
            avis.get("note", ""), avis.get("total", ""), avis.get("nouveaux_30j", ""), avis.get("sans_reponse", ""), avis.get("negatifs_sans_reponse", ""),
            "" if avis.get("evolution_total") is None else avis["evolution_total"], "" if avis.get("evolution_note") is None else avis["evolution_note"],
            stats.get("vues", ""), stats.get("appels", ""), stats.get("itineraires", ""), stats.get("site", ""), stats.get("mois", ""),
            pos.get("pct3", ""), pos.get("pct10", ""), "" if not pos or pos.get("evolution") is None else pos["evolution"],
            f"{pos['taille']}×{pos['taille']}, {pos['ecart_km']:g} km" if pos else "",
            " ; ".join(e["problemes"]), e["maj_le"].strftime("%d/%m/%Y %H:%M") if e["maj_le"] else "",
        ] + [f"{pos['par_mot'][m]['top3']}/{pos['par_mot'][m]['total']}" if pos and m in pos.get("par_mot", {}) else "" for m in mots])
    for i, largeur in enumerate([34, 6, 18, 40, 16, 40, 16, 12, 40, 10, 10, 22, 34, 26, 10, 10, 12, 12, 14, 16, 16, 12, 8, 10, 10, 10, 14, 14, 16, 16, 34, 16] + [14] * len(mots), start=1):
        feuille.column_dimensions[get_column_letter(i)].width = largeur
    feuille.freeze_panes = "B2"
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
