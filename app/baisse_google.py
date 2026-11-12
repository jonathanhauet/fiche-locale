"""
Analyse de la baisse de visibilite Google des agences (France, Espagne, Italie) : tout ce qu'il faut pour repondre a la Direction.

1. Statistiques de la fiche (vues, clics site, appels, itineraires) : 13 dernieres semaines contre les 13 precedentes, date probable du debut de la baisse, agences les plus touchees par pays. Lues semaine par semaine chez Google sur les 6 derniers mois.
2. Etat de la fiche (suspendue, non validee, modifiee par Google, modifications en attente, doublon) et modifications constatees d'une lecture a l'autre
   (nom, adresse, telephone, site, categories, horaires, description). Rang de l'agence sur les requetes cles « rachat or / compro oro / compra oro + ville ».
3. Presence de l'agence dans les reponses de ChatGPT, Gemini et du Mode IA de Google (releve sur un echantillon d'agences), avec les sites cites par l'IA.
4. Presence sur les annuaires qui alimentent l'IA (Pages Jaunes / Paginas Amarillas / PagineGialle, Trustpilot, Bing Places, Apple Plans) et concordance du
   nom, de l'adresse et du telephone avec la fiche Google.

Les donnees sont gardees (models.SuiviBaisse) : la page n'appelle rien quand elle s'ouvre. Chaque releve est lance a la main (certains sont payants) ;
les statistiques et l'etat des fiches (gratuits) sont aussi relus chaque lundi.
"""

import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import urlparse

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import citations, google_location, google_oauth, google_performance, ia_visibilite, models, rank_tracking, reporting_mensuel, suivi_goldunion

# ---------- pays et requetes cles

PAYS = [("FR", "France"), ("ES", "Espagne"), ("IT", "Italie")]
NOMS_PAYS = dict(PAYS)
REQUETES_PAYS = {"FR": ["rachat or"], "ES": ["compro oro", "compra oro"], "IT": ["compro oro"]}
LANGUES = {"FR": "fr", "ES": "es", "IT": "it"}


def code_pays(donnees: dict) -> str:
    """Pays de l'agence d'apres sa fiche (FR si la fiche n'a pas encore ete lue)."""
    return ((donnees or {}).get("pays") or "FR").upper()


def requetes_pays(pays: str) -> list:
    return REQUETES_PAYS.get(pays, REQUETES_PAYS["FR"])


def langue_pays(pays: str) -> str:
    return LANGUES.get(pays, "fr")


def groupe_pays(pays: str) -> str:
    """FR / ES / IT, ou AUTRE pour les autres pays."""
    return pays if pays in NOMS_PAYS else "AUTRE"


def _json(texte, defaut):
    try:
        valeur = json.loads(texte) if texte else defaut
    except ValueError:
        return defaut
    return valeur if isinstance(valeur, type(defaut)) else defaut


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte or "") if unicodedata.category(c) != "Mn").lower()


def date_fr(iso: str) -> str:
    """« 2026-11-09T10:00:00 » -> « 09/11/2026 »."""
    try:
        return datetime.fromisoformat((iso or "")[:19]).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def marque_depuis_nom(nom: str) -> str:
    """« GoldUnion - Agen » -> « GoldUnion »."""
    return re.split(r"\s[-–—]\s", (nom or "").strip(), maxsplit=1)[0].strip()


# ---------- 1. statistiques de la fiche, semaine par semaine

NB_SEMAINES = 26                                  # 6 mois : les 13 dernieres semaines et les 13 precedentes
NB_SEMAINES_PERIODE = 13                          # « trois mois »
DELAI_STATS_JOURS = 4                             # Google consolide ses chiffres avec quelques jours de retard
INDICATEURS = ("vues", "appels", "itineraires", "site")
LIBELLES_INDICATEURS = {"vues": "Vues", "appels": "Appels", "itineraires": "Itinéraires", "site": "Clics site"}
SEUIL_BAISSE = 20                                 # % de vues perdues pour dire qu'une agence « chute »
VUES_MIN_PERIODE = 100                            # en dessous, une variation en % n'a pas de sens (trop peu de vues)


def dernier_dimanche_complet(aujourdhui: date = None) -> date:
    fin = (aujourdhui or date.today()) - timedelta(days=DELAI_STATS_JOURS)
    return fin - timedelta(days=(fin.weekday() + 1) % 7)


def plage_stats(aujourdhui: date = None) -> tuple:
    dimanche = dernier_dimanche_complet(aujourdhui)
    return dimanche - timedelta(days=7 * NB_SEMAINES - 1), dimanche


def semaines_depuis_jours(par_jour: dict, dimanche_fin: date, nb: int = NB_SEMAINES) -> list:
    """Totaux par semaine (du lundi au dimanche), de la plus ancienne a la plus recente : [{"debut": "2026-11-02", "vues": n, "appels": n, ...}]."""
    premier = dimanche_fin - timedelta(days=7 * nb - 1)
    semaines = []
    for i in range(nb):
        lundi = premier + timedelta(days=7 * i)
        cumul = {k: 0 for k in INDICATEURS}
        for j in range(7):
            jour = reporting_mensuel.indicateurs(par_jour.get(lundi + timedelta(days=j)) or {})
            for k in INDICATEURS:
                cumul[k] += jour[k]
        semaines.append({"debut": lundi.isoformat(), **cumul})
    return semaines


def lire_stats_agence(identifiants, location_id: str, aujourdhui: date = None, lecteur=None) -> dict:
    debut, fin = plage_stats(aujourdhui)
    lecteur = lecteur or google_performance.recuperer_statistiques_par_jour
    par_jour = lecteur(identifiants, location_id, debut, fin)
    return {"maj": datetime.utcnow().isoformat(timespec="seconds"), "semaines": semaines_depuis_jours(par_jour, fin)}


def totaux_semaines(semaines: list) -> dict:
    return {k: sum(s.get(k, 0) for s in semaines) for k in INDICATEURS}


def variation(actuel, precedent):
    """Variation en % (None si la periode de comparaison est vide)."""
    if not precedent:
        return None
    return round((actuel - precedent) / precedent * 100, 1)


def comparer(semaines: list, nb: int = NB_SEMAINES_PERIODE):
    """13 dernieres semaines contre les 13 precedentes. None si pas assez de recul."""
    if len(semaines) < 2 * nb:
        return None
    actuel, precedent = totaux_semaines(semaines[-nb:]), totaux_semaines(semaines[-2 * nb:-nb])
    return {
        "actuel": actuel, "precedent": precedent,
        "var": {k: variation(actuel[k], precedent[k]) for k in INDICATEURS},
        "du": semaines[-nb]["debut"], "au": (date.fromisoformat(semaines[-1]["debut"]) + timedelta(days=6)).isoformat(),
        "precedent_du": semaines[-2 * nb]["debut"], "precedent_au": (date.fromisoformat(semaines[-nb]["debut"]) - timedelta(days=1)).isoformat(),
    }


def date_debut_baisse(semaines: list, cle: str = "vues", seuil: int = SEUIL_BAISSE, fenetre: int = 26, volume_min: float = 20):
    """
    Semaine ou la baisse commence probablement : le point de rupture des `fenetre` dernieres semaines qui separe le mieux un « avant » d'un « apres » plus
    bas. Renvoie {"date": lundi, "chute": % perdus entre la moyenne d'avant et celle d'apres} ou None (pas de chute d'au moins `seuil` %, ou trop peu de vues).
    C'est une estimation : a confirmer sur la courbe.
    """
    serie = [s.get(cle, 0) for s in semaines[-fenetre:]]
    debuts = [s["debut"] for s in semaines[-fenetre:]]
    meilleur = None
    for k in range(4, len(serie) - 2):
        avant, apres = sum(serie[:k]) / k, sum(serie[k:]) / (len(serie) - k)
        if avant < volume_min:
            continue
        chute = (avant - apres) / avant * 100
        if meilleur is None or chute > meilleur[0]:
            meilleur = (chute, k)
    if meilleur is None or meilleur[0] < seuil:
        return None
    return {"date": debuts[meilleur[1]], "chute": round(meilleur[0], 1)}


def sommer_series(listes: list) -> list:
    """Somme semaine par semaine de plusieurs agences, alignees sur les dates de debut de semaine ; seules les semaines connues d'au moins 80 % des agences comptent."""
    listes = [l for l in listes if l]
    if not listes:
        return []
    cumul = {}
    for l in listes:
        for s in l:
            c = cumul.setdefault(s["debut"], {"n": 0, **{k: 0 for k in INDICATEURS}})
            c["n"] += 1
            for k in INDICATEURS:
                c[k] += s.get(k, 0)
    return [{"debut": d, **{k: c[k] for k in INDICATEURS}} for d, c in sorted(cumul.items()) if c["n"] >= 0.8 * len(listes)]


# ---------- 2. etat de la fiche et modifications

CHAMPS_ETENDUS = google_location.CHAMPS_LECTURE + ",metadata.hasGoogleUpdated,metadata.hasPendingEdits,metadata.duplicateLocation"
URL_ETAT_MARCHAND = "https://mybusinessverifications.googleapis.com/v1/locations/{id}/VoiceOfMerchantState"
CHAMPS_SURVEILLES = [
    ("nom", "Nom"), ("adresse", "Adresse"), ("telephone", "Téléphone"), ("site", "Site web"), ("categorie", "Catégorie principale"),
    ("categories_secondaires", "Catégories secondaires"), ("horaires", "Horaires"), ("description", "Description"), ("ouverture", "Statut d'ouverture"),
]
MAX_CHANGEMENTS = 40


def lire_infos_fiche(identifiants, location_id: str) -> dict:
    """Fiche avec les indicateurs « modifiee par Google / modifications en attente / doublon ». Si Google refuse ces champs, repli sur la lecture habituelle."""
    reponse = requests.get(
        f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}",
        headers={"Authorization": f"Bearer {identifiants.token}"}, params={"readMask": CHAMPS_ETENDUS}, timeout=30,
    )
    if reponse.status_code == 200:
        return reponse.json()
    return google_location.obtenir_infos_fiche(identifiants, location_id)


def lire_etat_marchand(identifiants, location_id: str):
    """Etat de validation chez Google (API de verification). None si Google ne le donne pas : l'analyse continue sans."""
    try:
        reponse = requests.get(URL_ETAT_MARCHAND.format(id=location_id), headers={"Authorization": f"Bearer {identifiants.token}"}, timeout=30)
        return reponse.json() if reponse.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def instantane_fiche(infos: dict, etat_marchand=None) -> dict:
    """Photographie des champs que l'on surveille, plus les indicateurs d'alerte de Google."""
    metadata = infos.get("metadata") or {}
    categories = infos.get("categories") or {}
    secondaires = sorted((c.get("displayName") or c.get("name") or "") for c in (categories.get("additionalCategories") or []))
    description = ((infos.get("profile") or {}).get("description") or "").strip()
    etat = etat_marchand or {}
    return {
        "nom": infos.get("title", ""), "adresse": suivi_goldunion._adresse(infos), "telephone": (infos.get("phoneNumbers") or {}).get("primaryPhone", ""),
        "site": infos.get("websiteUri", ""), "categorie": (categories.get("primaryCategory") or {}).get("displayName", ""),
        "categories_secondaires": " | ".join(secondaires), "horaires": suivi_goldunion.resumer_horaires(infos.get("regularHours")),
        "description": description, "ouverture": (infos.get("openInfo") or {}).get("status", ""),
        "valide": bool(google_location.fiche_validee(infos)), "modifiee_par_google": bool(metadata.get("hasGoogleUpdated")),
        "modifications_en_attente": bool(metadata.get("hasPendingEdits")), "doublon": bool(metadata.get("duplicateLocation")),
        "suspendue": (True if etat.get("complyWithGuidelines") else False) if etat_marchand is not None else None,
    }


def detecter_changements(ancien: dict, nouveau: dict, maintenant: datetime = None) -> list:
    """Champs surveilles qui ont change entre deux lectures : [{"date", "champ", "avant", "apres"}]."""
    quand = (maintenant or datetime.utcnow()).isoformat(timespec="seconds")
    resultat = []
    for cle, libelle in CHAMPS_SURVEILLES:
        avant, apres = (ancien or {}).get(cle, ""), (nouveau or {}).get(cle, "")
        if (avant or "") != (apres or ""):
            if cle == "description":
                avant, apres = f"{len(avant or '')} caractères", f"{len(apres or '')} caractères"
            resultat.append({"date": quand, "champ": libelle, "avant": avant or "(vide)", "apres": apres or "(vide)"})
    return resultat


def alertes_fiche(instantane: dict) -> list:
    """Points qui meritent une action, du plus grave au moins grave."""
    if not instantane:
        return []
    alertes = []
    if instantane.get("suspendue"):
        alertes.append(("grave", "Fiche suspendue (non conforme aux règles de Google)"))
    if not instantane.get("valide"):
        alertes.append(("grave", "Fiche non validée (pas de contrôle du propriétaire)"))
    if "CLOSED_PERMANENTLY" in (instantane.get("ouverture") or ""):
        alertes.append(("grave", "Fiche marquée définitivement fermée"))
    if instantane.get("doublon"):
        alertes.append(("attention", "Doublon signalé par Google"))
    if instantane.get("modifiee_par_google"):
        alertes.append(("attention", "Informations modifiées par Google"))
    if instantane.get("modifications_en_attente"):
        alertes.append(("attention", "Modifications en attente de validation"))
    if "CLOSED_TEMPORARILY" in (instantane.get("ouverture") or ""):
        alertes.append(("attention", "Fiche fermée temporairement"))
    return alertes


def lire_fiche_agence(identifiants, location_id: str, ancien: dict = None, maintenant: datetime = None, lecteur=None, lecteur_etat=None) -> dict:
    maintenant = maintenant or datetime.utcnow()
    infos = (lecteur or lire_infos_fiche)(identifiants, location_id)
    actuel = instantane_fiche(infos, (lecteur_etat or lire_etat_marchand)(identifiants, location_id))
    ancien = ancien or {}
    changements = list(ancien.get("changements") or [])
    if ancien.get("actuel"):
        changements = (detecter_changements(ancien["actuel"], actuel, maintenant) + changements)[:MAX_CHANGEMENTS]
    return {
        "actuel": actuel, "lu_le": maintenant.isoformat(timespec="seconds"), "reference_le": ancien.get("reference_le") or maintenant.isoformat(timespec="seconds"),
        "changements": changements,
    }


# ---------- 2 bis. rang sur les requetes cles

NB_RELEVES_RANG = 26


def mesurer_rang_agence(donnees: dict, nom_agence: str, mesure=None, maintenant: datetime = None) -> dict:
    """
    Rang de l'agence dans Google Maps, a son adresse, pour chaque requete cle de son pays + sa ville. Resultat :
    {"date", "pays", "requetes": {requete: {"position": rang ou None, "top3": bool, "devant": [3 fiches classees devant elle]}}, "erreur"}
    """
    pays = code_pays(donnees)
    resultat = {"date": (maintenant or datetime.utcnow()).isoformat(timespec="seconds"), "pays": pays, "requetes": {}, "erreur": ""}
    ville, lat, lng = (donnees or {}).get("ville") or "", (donnees or {}).get("lat"), (donnees or {}).get("lng")
    if not donnees or not donnees.get("adresse"):
        resultat["erreur"] = "Fiche pas encore lue : relisez les fiches d'abord."
        return resultat
    if not ville or lat is None or lng is None:
        resultat["erreur"] = "Ville ou coordonnées introuvables sur la fiche."
        return resultat
    langue = langue_pays(pays)
    mesure = mesure or (lambda requete, la, lo, nom: rank_tracking.verifier_position(requete, la, lo, nom, profondeur=suivi_goldunion.PROFONDEUR_CONCURRENTS, langue=langue))
    for mot in requetes_pays(pays):
        requete = f"{mot} {ville}"
        try:
            position, _, classement = suivi_goldunion._mesure_avec_reprises(mesure, requete, lat, lng, nom_agence)
        except Exception as erreur:
            resultat["erreur"] = str(erreur)[:160]
            return resultat
        devant = [c["n"] for c in suivi_goldunion.concurrents_devant(position, classement)[:3]]
        resultat["requetes"][requete] = {"position": position, "top3": position is not None and position <= 3, "devant": devant}
    return resultat


def ajouter_releve_rang(ancien: dict, releve: dict) -> dict:
    releves = list((ancien or {}).get("releves") or [])
    releves.append(releve)
    return {"releves": releves[-NB_RELEVES_RANG:]}


def evolution_rang(rang: dict) -> list:
    """Lignes (une par requete) du dernier releve avec la position du releve precedent : [{"requete", "position", "avant", "top3", "avant_top3", "devant"}]."""
    releves = (rang or {}).get("releves") or []
    if not releves:
        return []
    dernier, precedent = releves[-1], (releves[-2] if len(releves) > 1 else None)
    lignes = []
    for requete, r in (dernier.get("requetes") or {}).items():
        p = ((precedent or {}).get("requetes") or {}).get(requete)
        lignes.append({"requete": requete, "position": r["position"], "top3": r["top3"], "devant": r.get("devant") or [], "date": dernier["date"],
                       "a_precedent": p is not None, "avant": p["position"] if p else None, "avant_top3": p["top3"] if p else None})
    return lignes


# ---------- 3. presence dans les reponses des IA

MOTEURS_IA = {"chatgpt": "ChatGPT", "gemini": "Gemini", "mode_ia": "Mode IA de Google"}
URL_MODE_IA = "https://api.dataforseo.com/v3/serp/google/ai_mode/live/advanced"
NB_RELEVES_IA = 6
NOMS_DATAFORSEO = {"FR": "France", "ES": "Spain", "IT": "Italy"}
# Une IA ne comprend pas toujours une requete de mots-cles (« rachat or Lens ») : on pose aussi la question comme un client la formulerait.
PHRASES_IA = {"FR": ["entreprise de rachat d'or à {ville}"], "ES": ["empresa de compra de oro en {ville}"], "IT": ["azienda che compra oro a {ville}"]}


def requetes_ia(pays: str, ville: str) -> list:
    """Requetes posees aux IA : les requetes cles + ville, puis la question en langage naturel."""
    return [f"{mot} {ville}" for mot in requetes_pays(pays)] + [p.format(ville=ville) for p in PHRASES_IA.get(pays, PHRASES_IA["FR"])]


def interroger_mode_ia(requete: str, pays: str) -> tuple:
    """(texte, sources) du Mode IA de Google, via DataForSEO (payant, quelques centimes par requete)."""
    if not rank_tracking.identifiants_configures():
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env.")
    corps = [{"keyword": requete, "location_name": NOMS_DATAFORSEO.get(pays, "France"), "language_code": langue_pays(pays), "device": "desktop"}]
    try:
        reponse = requests.post(URL_MODE_IA, auth=(rank_tracking.DATAFORSEO_LOGIN, rank_tracking.DATAFORSEO_PASSWORD), json=corps, timeout=90)
    except requests.RequestException as erreur:
        raise RuntimeError(f"Erreur réseau vers DataForSEO : {erreur}") from erreur
    if reponse.status_code != 200:
        raise RuntimeError(f"Mode IA : échec de l'appel (code {reponse.status_code})")
    taches = reponse.json().get("tasks") or []
    if not taches or taches[0].get("status_code") != 20000:
        raise RuntimeError("Mode IA : " + str((taches[0].get("status_message") if taches else "réponse vide"))[:150])
    return extraire_mode_ia((taches[0].get("result") or [{}])[0].get("items") or [])


def extraire_mode_ia(items: list) -> tuple:
    """Texte de la reponse et sources citees, depuis les elements « ai_overview » renvoyes par DataForSEO (lecture defensive : le format n'a pas pu etre verifie sur un compte reel)."""
    texte, sources = [], []

    def refs(liste):
        for ref in liste or []:
            if isinstance(ref, dict) and (ref.get("url") or ref.get("domain")):
                ia_visibilite._ajouter_source(sources, ref.get("title") or ref.get("source") or "", ref.get("url") or "")

    for item in items:
        if not str(item.get("type", "")).startswith("ai_overview"):
            continue
        morceau = item.get("markdown") or item.get("text") or ""
        if morceau:
            texte.append(morceau)
        refs(item.get("references"))
        for element in item.get("items") or []:
            if not morceau and (element.get("markdown") or element.get("text")):
                texte.append(element.get("markdown") or element.get("text"))
            refs(element.get("references"))
    return "\n".join(texte), sources


def analyser_citation(texte: str, marque: str, donnees: dict, sources: list = None) -> dict:
    """La marque est-elle citee dans le texte de la reponse ? L'agence elle-meme l'est-elle (marque a cote de la ville, ou telephone de l'agence) ? Les sources consultees sont gardees a part."""
    brut = _sans_accents(texte)
    cle = _sans_accents(marque)
    marque_citee = bool(cle) and cle in brut
    ville = _sans_accents((donnees or {}).get("ville", ""))
    agence = False
    if marque_citee and ville:
        for m in re.finditer(re.escape(cle), brut):
            if ville in brut[max(0, m.start() - 250): m.end() + 250]:
                agence = True
                break
    chiffres = re.sub(r"\D", "", (donnees or {}).get("telephone", ""))[-9:]
    if not agence and len(chiffres) == 9 and re.search(r"\D*".join(chiffres), texte or ""):
        agence = True
    return {"marque_citee": marque_citee, "agence_citee": agence}


def interroger_moteur(moteur: str, requete: str, pays: str) -> tuple:
    if moteur == "mode_ia":
        return interroger_mode_ia(requete, pays)
    return ia_visibilite.INTERROGATEURS[moteur](requete)


def releve_ia_agence(donnees: dict, nom_client: str, moteurs: list, interrogateur=None, maintenant: datetime = None) -> dict:
    """Interroge chaque IA sur chaque requete cle de l'agence. Resultat {"date", "marque", "resultats": {« moteur|requete »: {...}}} ; une IA en panne n'arrete pas les autres."""
    interrogateur = interrogateur or interroger_moteur
    pays, marque = code_pays(donnees), marque_depuis_nom(nom_client)
    ville = (donnees or {}).get("ville") or ""
    releve = {"date": (maintenant or datetime.utcnow()).isoformat(timespec="seconds"), "marque": marque, "resultats": {}}
    if not ville:
        releve["erreur"] = "Ville introuvable : fiche pas encore lue."
        return releve
    for requete in requetes_ia(pays, ville):
        for moteur in moteurs:
            cle = f"{moteur}|{requete}"
            try:
                texte, sources = interrogateur(moteur, requete, pays)
            except Exception as erreur:
                releve["resultats"][cle] = {"moteur": moteur, "requete": requete, "erreur": str(erreur)[:200]}
                continue
            releve["resultats"][cle] = {
                "moteur": moteur, "requete": requete, **analyser_citation(texte, marque, donnees, sources), "erreur": "", "manuel": False,
                "sources": [{"domaine": s["domaine"], "url": s["url"], "titre": s["titre"][:120]} for s in (sources or [])[:15]], "extrait": (texte or "")[:600],
            }
    return releve


def ajouter_releve_ia(ancien: dict, releve: dict) -> dict:
    releves = list((ancien or {}).get("releves") or [])
    releves.append(releve)
    return {"releves": releves[-NB_RELEVES_IA:]}


def choisir_echantillon(entrees: list, par_pays: int = 5) -> list:
    """Ids des agences retenues pour l'echantillon IA : `par_pays` par pays (les premieres dont la fiche est lue, pour avoir la ville), reparties dans l'ordre alphabetique."""
    retenus, compte = [], {}
    for e in entrees:
        if not e["ville"]:
            continue
        g = groupe_pays(e["pays"])
        if compte.get(g, 0) < par_pays:
            retenus.append(e["client"].id)
            compte[g] = compte.get(g, 0) + 1
    return retenus


def domaines_cites(entrees: list) -> list:
    """Sites cites dans les reponses des IA (dernier releve), du plus cite au moins cite : [{"domaine", "nb", "annuaire": bool}]."""
    compte = {}
    for e in entrees:
        releves = (e["ia"] or {}).get("releves") or []
        for r in (releves[-1].get("resultats") or {}).values() if releves else []:
            for domaine in {s["domaine"] for s in r.get("sources") or []}:
                compte[domaine] = compte.get(domaine, 0) + 1
    annuaires = tuple(a["domaine"] for a in citations.ANNUAIRES_PAR_ID.values() if a.get("domaine"))
    return [{"domaine": d, "nb": n, "annuaire": any(d.endswith(a) for a in annuaires)} for d, n in sorted(compte.items(), key=lambda x: (-x[1], x[0]))]


# ---------- 4. annuaires et concordance du nom, de l'adresse et du telephone

ANNUAIRES_PAYS = {
    "FR": ["pages_jaunes", "trustpilot", "bing_places", "apple_plans"],
    "ES": ["paginas_amarillas", "trustpilot", "bing_places", "apple_plans"],
    "IT": ["pagine_gialle", "trustpilot", "bing_places", "apple_plans"],
}
ANNUAIRES_MARQUE = {"trustpilot"}                 # une page pour la marque, pas une par agence : verifiee une fois par pays
MOTS_RUE = {"rue", "avenue", "boulevard", "place", "chemin", "route", "allee", "impasse", "calle", "avenida", "plaza", "paseo", "carrer", "via", "viale", "piazza", "corso", "largo", "vicolo", "quai", "cours", "de", "la", "le", "les", "del", "della", "dei", "des", "du"}
ETATS_ANNUAIRE = {"conforme": "Conforme", "ecart": "Écart", "illisible": "À vérifier à la main", "marque": "Page de la marque", "absent": "Absent", "inconnu": "Non vérifié"}
STATUTS_MANUELS = [("conforme", "Conforme (vérifié à la main)"), ("ecart", "Écart constaté"), ("absent", "Absent")]


def annuaires_pays(pays: str) -> list:
    ids = ANNUAIRES_PAYS.get(pays, ANNUAIRES_PAYS["FR"])
    return [citations.ANNUAIRES_PAR_ID[i] for i in ids]


def lire_page(url: str) -> tuple:
    """(code HTTP, texte visible) ; les annuaires bloquent souvent les robots : (0, '') si la page n'est pas lisible."""
    try:
        reponse = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "fr,es,it;q=0.8"})
    except requests.RequestException:
        return 0, ""
    return reponse.status_code, suivi_goldunion._texte_visible(reponse.text) if reponse.status_code == 200 else ""


def controle_nap(texte: str, donnees: dict, marque: str) -> dict:
    """Le nom, le telephone et l'adresse de la fiche Google se retrouvent-ils dans le texte de la page de l'annuaire ? (les horaires ne sont pas controles automatiquement)"""
    brut = _sans_accents(texte)
    chiffres = re.sub(r"\D", "", (donnees or {}).get("telephone", ""))[-9:]
    telephone = None if len(chiffres) < 9 else bool(re.search(r"\D*".join(chiffres), texte))
    adresse = (donnees or {}).get("adresse", "")
    postal = re.search(r"\b\d{4,5}\b", adresse)
    rue = adresse.split(",")[0]
    mots = [m for m in re.findall(r"[a-zà-ÿ'-]{4,}", _sans_accents(rue)) if m not in MOTS_RUE]
    numero = re.match(r"\s*(\d+)", rue)
    if not adresse:
        adresse_ok = None
    else:
        rue_ok = (not mots) or max(mots, key=len) in brut
        adresse_ok = rue_ok and (postal is None or postal.group() in brut) and (numero is None or re.search(rf"\b{numero.group(1)}\b", brut) is not None)
    nom = bool(marque) and _sans_accents(marque) in brut
    manque = [libelle for libelle, ok in (("nom", nom), ("téléphone", telephone), ("adresse", adresse_ok)) if ok is False]
    return {"nom": nom, "telephone": telephone, "adresse": adresse_ok, "manque": manque, "etat": "conforme" if not manque else "ecart"}


def verifier_annuaires_agence(donnees: dict, nom_client: str, verifier=None, page=None, cache_marque: dict = None) -> dict:
    """Presence de l'agence sur chaque annuaire de son pays, et concordance nom / adresse / telephone quand la page est lisible."""
    verifier = verifier or citations.verifier_citations
    page = page or lire_page
    cache_marque = cache_marque if cache_marque is not None else {}
    pays, marque, ville = code_pays(donnees), marque_depuis_nom(nom_client), (donnees or {}).get("ville") or ""
    resultat = {"maj": datetime.utcnow().isoformat(timespec="seconds"), "pays": pays, "resultats": {}, "erreur": ""}
    if not ville:
        resultat["erreur"] = "Ville introuvable : fiche pas encore lue."
        return resultat
    for annuaire in annuaires_pays(pays):
        ident = annuaire["id"]
        niveau_marque = ident in ANNUAIRES_MARQUE
        cle_cache = (pays, ident)
        if niveau_marque and cle_cache in cache_marque:
            verif = cache_marque[cle_cache]
        else:
            try:
                verif = verifier(marque, "" if niveau_marque else ville, pays, [ident])[0]
            except Exception as erreur:
                verif = {"id": ident, "trouve": None, "url": None, "erreur": str(erreur)[:150]}
            if niveau_marque and not verif.get("erreur"):
                cache_marque[cle_cache] = verif
        entree = {"nom": annuaire["nom"], "trouve": verif.get("trouve"), "url": verif.get("url") or "", "erreur": verif.get("erreur") or "", "niveau": "marque" if niveau_marque else "agence"}
        if entree["erreur"]:
            entree["etat"] = "inconnu"
        elif not entree["trouve"]:
            entree["etat"] = "absent"
        elif niveau_marque:
            entree["etat"] = "marque"
        elif entree["url"]:
            code, texte = page(entree["url"])
            if texte:
                nap = controle_nap(texte, donnees, marque)
                entree.update({"etat": nap["etat"], "manque": nap["manque"]})
            else:
                entree["etat"] = "illisible"
                entree["detail"] = f"page non lisible (code {code})" if code else "page non lisible"
        else:
            entree["etat"] = "illisible"
            entree["detail"] = "annuaire sans adresse de page (Bing / Apple) : à vérifier à la main"
        resultat["resultats"][ident] = entree
    return resultat


def etat_annuaire(annuaires: dict, ident: str) -> dict:
    """Etat retenu pour un annuaire : la saisie manuelle l'emporte sur la detection automatique."""
    manuel = ((annuaires or {}).get("manuel") or {}).get(ident)
    auto = ((annuaires or {}).get("resultats") or {}).get(ident) or {}
    if manuel and manuel.get("statut") in ETATS_ANNUAIRE:
        return {"etat": manuel["statut"], "url": manuel.get("url") or auto.get("url", ""), "note": manuel.get("note", ""), "manuel": True, "manque": []}
    return {"etat": auto.get("etat", "inconnu"), "url": auto.get("url", ""), "note": auto.get("detail", "") or auto.get("erreur", ""), "manuel": False, "manque": auto.get("manque") or []}


# ---------- vue d'ensemble (pour la page et l'export)

def lignes_baisse(db, parametre) -> list:
    """Une entree par agence suivie, avec tout ce qui a ete releve."""
    clients = suivi_goldunion.clients_suivis(db, parametre)
    ids = [c.id for c in clients] or [0]
    suivis = {s.client_id: s for s in db.query(models.SuiviAgence).filter(models.SuiviAgence.client_id.in_(ids)).all()}
    baisses = {b.client_id: b for b in db.query(models.SuiviBaisse).filter(models.SuiviBaisse.client_id.in_(ids)).all()}
    entrees = []
    for client in clients:
        suivi, b = suivis.get(client.id), baisses.get(client.id)
        donnees = _json(suivi.donnees_json, {}) if suivi else {}
        stats = _json(b.stats_json, {}) if b else {}
        semaines = (stats.get("semaines") or [])[-NB_SEMAINES:]          # les releves plus anciens (76 semaines) sont ecartes a l'affichage
        entrees.append({
            "client": client, "donnees": donnees, "pays": code_pays(donnees), "ville": donnees.get("ville", ""),
            "stats": stats, "semaines": semaines, "comparaison": comparer(semaines), "debut": date_debut_baisse(semaines) if semaines else None,
            "fiche": _json(b.fiche_json, {}) if b else {}, "rang": _json(b.rang_json, {}) if b else {}, "ia": _json(b.ia_json, {}) if b else {},
            "annuaires": _json(b.annuaires_json, {}) if b else {}, "echantillon": bool(b.echantillon_ia) if b else False,
            "maj": {c: getattr(b, f"{c}_maj_le", None) if b else None for c in ("stats", "fiche", "rang", "ia", "annuaires")},
        })
    return entrees


def synthese_stats(entrees: list) -> list:
    """Par pays : totaux des deux periodes, variations, nombre d'agences qui chutent, date probable du debut de la baisse, courbe du reseau."""
    resultat = []
    for code, nom in PAYS + [("AUTRE", "Autres pays")]:
        du_pays = [e for e in entrees if groupe_pays(e["pays"]) == code]
        if not du_pays:
            continue
        avec = [e for e in du_pays if e["comparaison"]]
        serie = sommer_series([e["semaines"] for e in du_pays if e["semaines"]])
        comp = comparer(serie) if serie else None
        touchees = [e for e in avec if e["comparaison"]["precedent"]["vues"] >= VUES_MIN_PERIODE and (e["comparaison"]["var"]["vues"] or 0) <= -SEUIL_BAISSE]
        resultat.append({
            "code": code, "nom": nom, "nb": len(du_pays), "nb_lues": len(avec), "comparaison": comp, "serie": serie,
            "nb_touchees": len(touchees), "debut": date_debut_baisse(serie) if serie else None,
        })
    return resultat


def agences_touchees(entrees: list, code: str, nombre: int = 10) -> list:
    """Les agences d'un pays qui perdent des vues (celles qui avaient assez de vues pour que le % ait un sens), de la plus touchee a la moins touchee."""
    candidates = [e for e in entrees if groupe_pays(e["pays"]) == code and e["comparaison"] and e["comparaison"]["precedent"]["vues"] >= VUES_MIN_PERIODE and e["comparaison"]["var"]["vues"] is not None and e["comparaison"]["var"]["vues"] < 0]
    return sorted(candidates, key=lambda e: e["comparaison"]["var"]["vues"])[:nombre]


def synthese_fiches(entrees: list) -> dict:
    lues = [e for e in entrees if (e["fiche"] or {}).get("actuel")]
    avec_alerte = [(e, alertes_fiche(e["fiche"]["actuel"])) for e in lues]
    avec_alerte = [(e, a) for e, a in avec_alerte if a]
    avec_changement = [e for e in lues if e["fiche"].get("changements")]
    return {"lues": len(lues), "alertes": sorted(avec_alerte, key=lambda x: (0 if any(g == "grave" for g, _ in x[1]) else 1, x[0]["client"].nom)),
            "suspendues": sum(1 for e in lues if e["fiche"]["actuel"].get("suspendue")), "avec_changement": avec_changement}


def synthese_rang(entrees: list) -> list:
    """Par pays : agences mesurees, dans le top 3 maintenant, et passees hors du top 3 depuis le releve precedent."""
    resultat = []
    for code, nom in PAYS + [("AUTRE", "Autres pays")]:
        lignes = []
        for e in entrees:
            if groupe_pays(e["pays"]) != code:
                continue
            for l in evolution_rang(e["rang"]):
                lignes.append({**l, "entree": e})
        if not [e for e in entrees if groupe_pays(e["pays"]) == code]:
            continue
        sorties = [l for l in lignes if l["a_precedent"] and l["avant_top3"] and not l["top3"]]
        resultat.append({"code": code, "nom": nom, "lignes": sorted(lignes, key=lambda l: (l["top3"], l["position"] if l["position"] is not None else 999, l["entree"]["client"].nom), reverse=False),
                         "nb_top3": sum(1 for l in lignes if l["top3"]), "nb_hors_top3": sum(1 for l in lignes if not l["top3"]), "sorties": sorties})
    return resultat


def synthese_ia(entrees: list) -> dict:
    """Taux de presence par moteur et par pays dans le dernier releve."""
    par = {}
    for e in entrees:
        releves = (e["ia"] or {}).get("releves") or []
        if not releves:
            continue
        for r in releves[-1].get("resultats", {}).values():
            if r.get("erreur"):
                continue
            cle = (groupe_pays(e["pays"]), r["moteur"])
            p = par.setdefault(cle, {"n": 0, "marque": 0, "agence": 0})
            p["n"] += 1
            p["marque"] += 1 if r.get("marque_citee") else 0
            p["agence"] += 1 if r.get("agence_citee") else 0
    return {"par": par, "domaines": domaines_cites(entrees)}


def synthese_annuaires(entrees: list) -> list:
    """Par annuaire : nombre d'agences conformes, avec un ecart, absentes, a verifier."""
    compte = {}
    for e in entrees:
        pays = e["pays"]
        for annuaire in annuaires_pays(pays):
            etat = etat_annuaire(e["annuaires"], annuaire["id"])["etat"] if e["annuaires"] else "inconnu"
            c = compte.setdefault(annuaire["id"], {"nom": annuaire["nom"], "pays": set(), "etats": {}})
            c["pays"].add(groupe_pays(pays))
            c["etats"][etat] = c["etats"].get(etat, 0) + 1
    return [{"id": i, "nom": c["nom"], "pays": sorted(c["pays"]), "etats": c["etats"]} for i, c in compte.items()]


def donnees_evolution(entrees: list) -> dict:
    """
    Donnees de l'onglet « Evolution » de la page des responsables : pour chaque agence, ses 4 courbes hebdomadaires (vues, appels, itineraires, clics site),
    la comparaison 13 / 13 semaines, la date probable du debut de la baisse, ses rangs sur les requetes cles et les alertes de sa fiche.
    """
    agences = []
    for e in entrees:
        semaines = e["semaines"]
        if not semaines:
            continue
        agences.append({
            "id": e["client"].id, "nom": e["client"].nom, "ville": e["ville"], "pays": e["pays"], "d0": semaines[0]["debut"],
            "v": [s["vues"] for s in semaines], "a": [s["appels"] for s in semaines], "i": [s["itineraires"] for s in semaines], "s": [s["site"] for s in semaines],
            "c": e["comparaison"], "debut": e["debut"], "maj": date_fr(((e["stats"] or {}).get("maj")) or ""),
            "rang": [{"r": l["requete"], "p": l["position"], "av": l["avant"], "ap": l["a_precedent"], "t3": l["top3"]} for l in evolution_rang(e["rang"])],
            "alertes": [[g, texte] for g, texte in alertes_fiche((e["fiche"] or {}).get("actuel"))],
            "chg": [{"d": date_fr(c["date"]), "c": c["champ"], "av": c["avant"], "ap": c["apres"]} for c in ((e["fiche"] or {}).get("changements") or [])[:5]],
        })
    return {"agences": agences, "nb_total": len(entrees), "seuil": SEUIL_BAISSE, "pays": NOMS_PAYS}


# ---------- releves en tache de fond

ETAT = {"en_cours": False, "tache": "", "message": "", "fait": 0, "total": 0, "debut": None, "fin": None, "erreurs": []}
_verrou_etat = threading.Lock()
_verrou_ecriture = threading.Lock()
LIBELLES_TACHES = {"stats": "Statistiques des fiches", "fiches": "État des fiches", "rang": "Rang sur les requêtes clés", "ia": "Réponses des IA", "annuaires": "Annuaires"}
COUT_PAR_REQUETE_CENTIMES = {"rang": 0.2, "annuaires": 0.2, "ia_mode": 0.4}


def etat() -> dict:
    return dict(ETAT, erreurs=list(ETAT["erreurs"]), debut=ETAT["debut"].isoformat() if ETAT["debut"] else "", fin=ETAT["fin"].isoformat() if ETAT["fin"] else "")


def _ecrire(session_factory, client_id: int, champ: str, valeur, extra: dict = None) -> None:
    with _verrou_ecriture:
        db = session_factory()
        try:
            ligne = db.query(models.SuiviBaisse).filter_by(client_id=client_id).first()
            if not ligne:
                ligne = models.SuiviBaisse(client_id=client_id)
                db.add(ligne)
            setattr(ligne, f"{champ}_json", json.dumps(valeur, ensure_ascii=False))
            setattr(ligne, f"{champ}_maj_le", datetime.utcnow())
            for cle, v in (extra or {}).items():
                setattr(ligne, cle, v)
            db.commit()
        finally:
            db.close()


def fixer_echantillon(session_factory, ids: list) -> None:
    ids = set(ids)
    with _verrou_ecriture:
        db = session_factory()
        try:
            existantes = {b.client_id: b for b in db.query(models.SuiviBaisse).all()}
            for cid in ids:
                if cid not in existantes:
                    existantes[cid] = models.SuiviBaisse(client_id=cid)
                    db.add(existantes[cid])
            for cid, b in existantes.items():
                b.echantillon_ia = cid in ids
            db.commit()
        finally:
            db.close()


def _travaux(session_factory) -> list:
    db = session_factory()
    try:
        parametre = suivi_goldunion.lire_parametre(db)
        entrees = lignes_baisse(db, parametre)
        comptes, travaux = {}, []
        for e in entrees:
            c = e["client"]
            if c.compte_google_id not in comptes:
                comptes[c.compte_google_id] = google_oauth.obtenir_identifiants(db, c.compte_google_id) if c.account_id else None
            travaux.append({"client_id": c.id, "nom": c.nom, "location_id": c.location_id, "identifiants": comptes[c.compte_google_id], "donnees": e["donnees"],
                            "pays": e["pays"], "echantillon": e["echantillon"], "stats": e["stats"], "fiche": e["fiche"], "rang": e["rang"], "ia": e["ia"], "annuaires": e["annuaires"]})
        return travaux
    finally:
        db.close()


def _noter(**champs) -> None:
    ETAT.update(champs)


def _executer(session_factory, tache: str, options: dict) -> None:
    try:
        travaux = _travaux(session_factory)
        if tache == "ia":
            if not any(t["echantillon"] for t in travaux):
                db = session_factory()
                try:
                    entrees = lignes_baisse(db, suivi_goldunion.lire_parametre(db))
                finally:
                    db.close()
                fixer_echantillon(session_factory, choisir_echantillon(entrees))
                travaux = _travaux(session_factory)
            travaux = [t for t in travaux if t["echantillon"]]
        cible = options.get("client_id")
        if cible:
            travaux = [t for t in travaux if t["client_id"] == cible]
        _noter(total=len(travaux), fait=0, message=f"{LIBELLES_TACHES[tache]} : 0/{len(travaux)}")
        cache_marque = {}

        def faire(t):
            try:
                if tache in ("stats", "fiches") and (not t["identifiants"] or not t["location_id"]):
                    raise RuntimeError("fiche Google non reliée ou compte à reconnecter")
                if tache == "stats":
                    _ecrire(session_factory, t["client_id"], "stats", lire_stats_agence(t["identifiants"], t["location_id"]))
                elif tache == "fiches":
                    _ecrire(session_factory, t["client_id"], "fiche", lire_fiche_agence(t["identifiants"], t["location_id"], t["fiche"]))
                elif tache == "rang":
                    releve = mesurer_rang_agence(t["donnees"], t["nom"])
                    _ecrire(session_factory, t["client_id"], "rang", ajouter_releve_rang(t["rang"], releve) if not (releve["erreur"] and not releve["requetes"]) else {**(t["rang"] or {}), "erreur": releve["erreur"]})
                elif tache == "ia":
                    releve = releve_ia_agence(t["donnees"], t["nom"], options.get("moteurs") or ["chatgpt", "gemini"])
                    _ecrire(session_factory, t["client_id"], "ia", ajouter_releve_ia(t["ia"], releve))
                elif tache == "annuaires":
                    nouveau = verifier_annuaires_agence(t["donnees"], t["nom"], cache_marque=cache_marque)
                    nouveau["manuel"] = (t["annuaires"] or {}).get("manuel") or {}          # les saisies manuelles survivent a une nouvelle verification
                    _ecrire(session_factory, t["client_id"], "annuaires", nouveau)
            except Exception as erreur:
                ETAT["erreurs"].append(f"{t['nom']} : {str(erreur)[:120]}")
            ETAT["fait"] += 1
            if ETAT["fait"] % 5 == 0 or ETAT["fait"] == ETAT["total"]:
                _noter(message=f"{LIBELLES_TACHES[tache]} : {ETAT['fait']}/{ETAT['total']}")

        travailleurs = {"stats": 6, "fiches": 6, "rang": 8, "ia": 4, "annuaires": 1}[tache]      # annuaires : un seul fil, le cache des pages « marque » doit servir a tous
        with ThreadPoolExecutor(max_workers=travailleurs) as pool:
            list(pool.map(faire, travaux))
        erreurs = len(ETAT["erreurs"])
        _noter(message=f"{LIBELLES_TACHES[tache]} terminé : {len(travaux) - erreurs} agence(s) traitée(s)" + (f", {erreurs} en erreur" if erreurs else ""))
    except Exception as erreur:
        _noter(message=f"Échec : {str(erreur)[:200]}")
    finally:
        _noter(en_cours=False, fin=datetime.utcnow())
        _verrou_etat.release()


def lancer(session_factory, tache: str, options: dict = None) -> bool:
    """Demarre un releve dans un fil. False si un releve est deja en cours."""
    if tache not in LIBELLES_TACHES or not _verrou_etat.acquire(blocking=False):
        return False
    ETAT.update(en_cours=True, tache=tache, message="Préparation…", fait=0, total=0, debut=datetime.utcnow(), fin=None, erreurs=[])
    threading.Thread(target=_executer, args=(session_factory, tache, options or {}), daemon=True).start()
    return True


def releve_hebdomadaire(session_factory) -> None:
    """Tache du lundi matin : statistiques puis etat des fiches (gratuits)."""
    for tache in ("stats", "fiches"):
        if not lancer(session_factory, tache):
            return
        while ETAT["en_cours"]:
            threading.Event().wait(5)


# ---------- saisies manuelles

def enregistrer_manuel_annuaire(session_factory, client_id: int, annuaire_id: str, statut: str, url: str = "", note: str = "") -> bool:
    if annuaire_id not in citations.ANNUAIRES_PAR_ID or statut not in dict(STATUTS_MANUELS) and statut != "":
        return False
    with _verrou_ecriture:
        db = session_factory()
        try:
            ligne = db.query(models.SuiviBaisse).filter_by(client_id=client_id).first()
            if not ligne:
                ligne = models.SuiviBaisse(client_id=client_id)
                db.add(ligne)
            valeur = _json(ligne.annuaires_json, {})
            manuel = valeur.setdefault("manuel", {})
            if statut:
                manuel[annuaire_id] = {"statut": statut, "url": url.strip()[:300], "note": note.strip()[:200], "le": datetime.utcnow().isoformat(timespec="seconds")}
            else:
                manuel.pop(annuaire_id, None)
            ligne.annuaires_json = json.dumps(valeur, ensure_ascii=False)
            ligne.annuaires_maj_le = ligne.annuaires_maj_le or datetime.utcnow()
            db.commit()
            return True
        finally:
            db.close()


def enregistrer_manuel_ia(session_factory, client_id: int, requete: str, cite: bool, moteur: str = "mode_ia") -> bool:
    """Observation faite a la main (le Mode IA n'a pas d'interface officielle) : ajoutee au dernier releve de l'agence."""
    if moteur not in MOTEURS_IA:
        return False
    with _verrou_ecriture:
        db = session_factory()
        try:
            ligne = db.query(models.SuiviBaisse).filter_by(client_id=client_id).first()
            if not ligne:
                ligne = models.SuiviBaisse(client_id=client_id)
                db.add(ligne)
            valeur = _json(ligne.ia_json, {})
            releves = valeur.setdefault("releves", [])
            if not releves:
                releves.append({"date": datetime.utcnow().isoformat(timespec="seconds"), "marque": "", "resultats": {}})
            releves[-1].setdefault("resultats", {})[f"{moteur}|{requete}"] = {
                "moteur": moteur, "requete": requete, "marque_citee": cite, "agence_citee": cite, "erreur": "", "manuel": True, "sources": [], "extrait": "",
            }
            ligne.ia_json = json.dumps(valeur, ensure_ascii=False)
            ligne.ia_maj_le = datetime.utcnow()
            db.commit()
            return True
        finally:
            db.close()


# ---------- export Excel

def _pct(valeur) -> str:
    return "" if valeur is None else f"{valeur:+.1f} %".replace(".", ",")


def generer_excel(entrees: list) -> bytes:
    classeur = Workbook()

    def feuille(titre, entetes, premiere=False):
        f = classeur.active if premiere else classeur.create_sheet()
        f.title = titre
        f.append(entetes)
        for c in f[1]:
            c.font = Font(bold=True)
        f.freeze_panes = "B2"
        return f

    f = feuille("Statistiques", ["Agence", "Pays", "Ville", "Vues (13 dernières sem.)", "Vues (13 sem. précédentes)", "Évolution vues", "Appels", "Évolution appels", "Itinéraires", "Évolution itinéraires", "Clics site", "Évolution clics", "Début probable de la baisse", "Perte au point de rupture"], premiere=True)
    for e in sorted(entrees, key=lambda e: (e["pays"], e["client"].nom)):
        c = e["comparaison"]
        if not c:
            f.append([e["client"].nom, e["pays"], e["ville"], "Pas encore lu"])
            continue
        f.append([e["client"].nom, e["pays"], e["ville"], c["actuel"]["vues"], c["precedent"]["vues"], _pct(c["var"]["vues"]), c["actuel"]["appels"], _pct(c["var"]["appels"]),
                  c["actuel"]["itineraires"], _pct(c["var"]["itineraires"]), c["actuel"]["site"], _pct(c["var"]["site"]), date_fr(e["debut"]["date"]) if e["debut"] else "", _pct(-e["debut"]["chute"]) if e["debut"] else ""])
    f = feuille("Fiches", ["Agence", "Pays", "Suspendue", "Validée", "Modifiée par Google", "Modifications en attente", "Doublon", "Statut d'ouverture", "Dernière lecture", "Changements constatés"])
    oui = lambda v: {True: "Oui", False: "Non"}.get(v, "")
    for e in sorted(entrees, key=lambda e: (e["pays"], e["client"].nom)):
        a = (e["fiche"] or {}).get("actuel")
        if not a:
            f.append([e["client"].nom, e["pays"], "Pas encore lue"])
            continue
        f.append([e["client"].nom, e["pays"], oui(a.get("suspendue")), oui(a.get("valide")), oui(a.get("modifiee_par_google")), oui(a.get("modifications_en_attente")), oui(a.get("doublon")), a.get("ouverture", ""),
                  date_fr(e["fiche"].get("lu_le", "")), " ; ".join(f"{date_fr(x['date'])} {x['champ']} : {x['avant']} → {x['apres']}" for x in e["fiche"].get("changements", [])[:8])])
    f = feuille("Rangs", ["Agence", "Pays", "Requête", "Rang actuel", "Rang précédent", "Top 3", "Devant elle", "Date du relevé"])
    for e in sorted(entrees, key=lambda e: (e["pays"], e["client"].nom)):
        for l in evolution_rang(e["rang"]):
            f.append([e["client"].nom, e["pays"], l["requete"], l["position"] if l["position"] is not None else "Hors des résultats", (l["avant"] if l["avant"] is not None else "Hors des résultats") if l["a_precedent"] else "", "Oui" if l["top3"] else "Non", " ; ".join(l["devant"]), date_fr(l["date"])])
    f = feuille("Réponses des IA", ["Agence", "Pays", "IA", "Requête", "Marque citée", "Agence citée", "Saisie à la main", "Sites cités", "Erreur"])
    for e in sorted(entrees, key=lambda e: (e["pays"], e["client"].nom)):
        releves = (e["ia"] or {}).get("releves") or []
        for r in (releves[-1].get("resultats") or {}).values() if releves else []:
            f.append([e["client"].nom, e["pays"], MOTEURS_IA.get(r["moteur"], r["moteur"]), r["requete"], "" if r.get("erreur") else oui(r.get("marque_citee")), "" if r.get("erreur") else oui(r.get("agence_citee")),
                      "Oui" if r.get("manuel") else "", ", ".join(sorted({s["domaine"] for s in r.get("sources", [])})), r.get("erreur", "")])
    f = feuille("Annuaires", ["Agence", "Pays", "Annuaire", "État", "Écarts", "Adresse de la page", "Note"])
    for e in sorted(entrees, key=lambda e: (e["pays"], e["client"].nom)):
        for annuaire in annuaires_pays(e["pays"]):
            s = etat_annuaire(e["annuaires"], annuaire["id"])
            f.append([e["client"].nom, e["pays"], annuaire["nom"], ETATS_ANNUAIRE.get(s["etat"], s["etat"]) + (" (saisi à la main)" if s["manuel"] else ""), ", ".join(s["manque"]), s["url"], s["note"]])
    for feuille_ in classeur.worksheets:
        for i in range(1, feuille_.max_column + 1):
            feuille_.column_dimensions[get_column_letter(i)].width = 30 if i == 1 else 22
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
