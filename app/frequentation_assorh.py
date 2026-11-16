"""
Frequentation du site assorh.fr pour la page de suivi de Franck (onglet « Fréquentation du site », voir suivi_assorh.py).

Deux sources, lues avec la connexion Search Console de l'agence (models.ParametreSearchConsole) :
- Google Analytics 4 (API Data, scope analytics.readonly, demande en meme temps que la Search Console depuis /search-console/connecter) :
  visites, pages vues, provenance, appareils, regions. Ne compte que les visiteurs qui ont accepte les cookies (bandeau Axeptio).
- Search Console (sc-domain:assorh.fr) : apparitions et clics dans Google, recherches tapees, position de la marque. Compte tout le
  monde, avec environ 3 jours de retard.

Les chiffres sont gardes en base (StatsSuiviAssoRH) et relus en arriere-plan au plus toutes les DUREE_CACHE : la page ne fait jamais
attendre Franck sur un appel Google.
"""

import json
import os
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as RequeteGoogle
from google.oauth2.credentials import Credentials
from sqlalchemy import Column, DateTime, Integer, Text

from . import google_oauth, search_console
from .database import Base, SessionLocal

SITE_SC = "sc-domain:assorh.fr"
PROPRIETE_GA4 = os.getenv("ASSORH_GA4_PROPRIETE", "564468213")  # identifiant numerique de la propriete GA4 « assorh.fr » (pas le G-…)
NB_JOURS = 28
DUREE_CACHE = timedelta(hours=6)
URL_GA4 = "https://analyticsdata.googleapis.com/v1beta/properties/{}:runReport"
DEBUT_SUIVI = date(2026, 11, 16)                                 # mise en ligne de la mesure (GTM + Axeptio) : rien avant

CANAUX = {
    "Organic Search": "Recherche Google ou Bing", "Direct": "Accès direct (adresse tapée, favori)", "Paid Search": "Publicité Google",
    "Organic Social": "Réseaux sociaux", "Paid Social": "Publicité sur les réseaux sociaux", "Referral": "Lien depuis un autre site",
    "Email": "E-mails", "Unassigned": "Provenance inconnue", "Cross-network": "Publicité Google (plusieurs réseaux)",
    "Organic Video": "Vidéos", "Paid Video": "Publicité vidéo", "Display": "Bannières publicitaires", "Paid Other": "Autre publicité",
    "SMS": "SMS", "Organic Shopping": "Comparateurs", "Paid Shopping": "Publicité shopping", "Mobile Push Notifications": "Notifications",
}
APPAREILS = {"mobile": "Téléphone", "desktop": "Ordinateur", "tablet": "Tablette", "smart tv": "Télévision"}
PAGES = {
    "/": "Accueil", "/tarifs": "Tarifs", "/demo": "Demander une démo", "/login": "Connexion", "/cgu": "Conditions d'utilisation",
    "/cgs": "Conditions de vente", "/mentions-legales": "Mentions légales", "/sport": "Page Sport", "/petite-enfance": "Page Petite enfance",
}

_verrou = threading.Lock()
_en_cours = threading.Event()


class StatsSuiviAssoRH(Base):
    """Derniere lecture des chiffres (une seule ligne)."""

    __tablename__ = "suivi_assorh_stats"

    id = Column(Integer, primary_key=True)
    donnees_json = Column(Text, default="")
    maj_le = Column(DateTime, nullable=True)


# ---------- connexions

def identifiants_analytics(db):
    """Identifiants Google Analytics (meme jeton que la Search Console), ou None si le scope analytics n'a pas ete accorde."""
    parametre = google_oauth.obtenir_parametre_search_console(db)
    if not parametre or not parametre.refresh_token:
        return None
    identifiants = Credentials(
        token=None, refresh_token=parametre.refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=google_oauth.CLIENT_ID, client_secret=google_oauth.CLIENT_SECRET, scopes=[google_oauth.SCOPE_ANALYTICS],
    )
    try:
        identifiants.refresh(RequeteGoogle())
    except RefreshError:
        return None
    return identifiants


def _ga4(identifiants, corps: dict) -> list:
    """Lignes d'un rapport GA4 : [(dimensions, metriques)], metriques en nombres."""
    reponse = requests.post(URL_GA4.format(PROPRIETE_GA4), headers={"Authorization": f"Bearer {identifiants.token}"}, json=corps, timeout=30)
    if reponse.status_code == 403:
        raise RuntimeError("Accès refusé par Google Analytics : activer « Google Analytics Data API » dans le projet Google Cloud, "
                           "ou donner au compte connecté l'accès à la propriété AssoRH.")
    if reponse.status_code != 200:
        raise RuntimeError(f"Échec de Google Analytics (code {reponse.status_code}) : {reponse.text[:300]}")
    lignes = []
    for ligne in reponse.json().get("rows", []) or []:
        dims = [d.get("value", "") for d in ligne.get("dimensionValues", [])]
        mets = [float(m.get("value") or 0) for m in ligne.get("metricValues", [])]
        lignes.append((dims, mets))
    return lignes


# ---------- outils

def _periodes(fin: date) -> tuple:
    debut = fin - timedelta(days=NB_JOURS - 1)
    fin_prec = debut - timedelta(days=1)
    return debut, fin, fin_prec - timedelta(days=NB_JOURS - 1), fin_prec


def _plage(debut: date, fin: date) -> dict:
    return {"startDate": debut.isoformat(), "endDate": fin.isoformat()}


def _nom_page(chemin: str) -> str:
    chemin = re.sub(r"^https?://[^/]+", "", chemin or "") or "/"
    chemin = chemin.split("?")[0].split("#")[0]
    if len(chemin) > 1:
        chemin = chemin.rstrip("/")
    return PAGES.get(chemin, chemin)


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


def est_recherche_marque(requete: str) -> bool:
    """« assorh », « asso rh », « asso-rh », « assorh tarif »… (pas « rh association » tout court)."""
    return "assorh" in re.sub(r"[\s\-_.']", "", _sans_accents(requete))


# ---------- lectures

def lire_ga4(identifiants, aujourd_hui: date) -> dict:
    fin = aujourd_hui - timedelta(days=1)
    debut, fin, debut_prec, fin_prec = _periodes(fin)
    metriques = [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}, {"name": "engagedSessions"}]

    def totaux(d, f):
        lignes = _ga4(identifiants, {"dateRanges": [_plage(d, f)], "metrics": metriques})
        m = lignes[0][1] if lignes else [0, 0, 0, 0]
        return {"visiteurs": int(m[0]), "visites": int(m[1]), "pages_vues": int(m[2]), "visites_engagees": int(m[3])}

    actuel, precedent = totaux(debut, fin), totaux(debut_prec, fin_prec)
    par_jour = {d[0]: int(m[0]) for d, m in _ga4(identifiants, {
        "dateRanges": [_plage(debut, fin)], "dimensions": [{"name": "date"}], "metrics": [{"name": "sessions"}]})}
    jours = []
    for i in range(NB_JOURS):
        jour = debut + timedelta(days=i)
        jours.append({"date": jour.isoformat(), "visites": par_jour.get(jour.strftime("%Y%m%d"), 0)})

    def classement(dimension, metrique, limite, nommer):
        lignes = _ga4(identifiants, {"dateRanges": [_plage(debut, fin)], "dimensions": [{"name": dimension}], "metrics": [{"name": metrique}],
                                     "orderBys": [{"metric": {"metricName": metrique}, "desc": True}], "limit": limite})
        total = sum(m[0] for _, m in lignes) or 1
        return [{"nom": nommer(d[0]), "valeur": int(m[0]), "part": round(100 * m[0] / total)} for d, m in lignes if m[0] > 0]

    canaux = classement("sessionDefaultChannelGroup", "sessions", 10, lambda v: CANAUX.get(v, v))
    sources = classement("sessionSourceMedium", "sessions", 10, lambda v: v.replace(" / ", " · ").replace("(direct) · (none)", "accès direct"))
    appareils = classement("deviceCategory", "activeUsers", 5, lambda v: APPAREILS.get(v.lower(), v))
    regions = classement("region", "activeUsers", 8, lambda v: v if v and v != "(not set)" else "Région inconnue")
    lignes_pages = _ga4(identifiants, {"dateRanges": [_plage(debut, fin)], "dimensions": [{"name": "pagePath"}],
                                       "metrics": [{"name": "screenPageViews"}, {"name": "activeUsers"}],
                                       "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}], "limit": 50})
    pages = {}
    for d, m in lignes_pages:
        nom = _nom_page(d[0])
        page = pages.setdefault(nom, {"nom": nom, "vues": 0, "visiteurs": 0})
        page["vues"] += int(m[0])
        page["visiteurs"] += int(m[1])
    pages = sorted(pages.values(), key=lambda p: p["vues"], reverse=True)
    actuel["vues_demo"] = next((p["vues"] for p in pages if p["nom"] == PAGES["/demo"]), 0)
    prec_demo = _ga4(identifiants, {"dateRanges": [_plage(debut_prec, fin_prec)], "dimensions": [{"name": "pagePath"}],
                                    "metrics": [{"name": "screenPageViews"}],
                                    "dimensionFilter": {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "BEGINS_WITH", "value": "/demo"}}}})
    precedent["vues_demo"] = int(sum(m[0] for _, m in prec_demo))
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()}, "actuel": actuel, "precedent": precedent, "jours": jours,
        "canaux": canaux, "sources": sources, "appareils": appareils, "regions": regions, "pages": pages[:8],
    }


def lire_search_console(identifiants, aujourd_hui: date) -> dict:
    fin = aujourd_hui - timedelta(days=search_console.RETARD_DONNEES_JOURS)
    debut, fin, debut_prec, fin_prec = _periodes(fin)

    def totaux(d, f):
        lignes = search_console._requete(identifiants, SITE_SC, d, f)
        l = lignes[0] if lignes else {}
        return {"clics": int(l.get("clicks", 0)), "impressions": int(l.get("impressions", 0)), "position": round(float(l.get("position", 0)), 1)}

    actuel, precedent = totaux(debut, fin), totaux(debut_prec, fin_prec)
    requetes = [{"requete": l["keys"][0], "clics": int(l.get("clicks", 0)), "impressions": int(l.get("impressions", 0)),
                 "position": round(float(l.get("position", 0)), 1)}
                for l in search_console._requete(identifiants, SITE_SC, debut, fin, ["query"], limite=250)]
    marque = [r for r in requetes if est_recherche_marque(r["requete"])]
    impressions_marque = sum(r["impressions"] for r in marque)
    position_marque = round(sum(r["position"] * r["impressions"] for r in marque) / impressions_marque, 1) if impressions_marque else None
    pages = [{"nom": _nom_page(l["keys"][0]), "clics": int(l.get("clicks", 0)), "impressions": int(l.get("impressions", 0))}
             for l in search_console._requete(identifiants, SITE_SC, debut, fin, ["page"], limite=10)]
    requetes.sort(key=lambda r: (r["clics"], r["impressions"]), reverse=True)
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()}, "actuel": actuel, "precedent": precedent,
        "requetes": requetes[:12], "nb_requetes": len(requetes),
        "marque": {"clics": sum(r["clics"] for r in marque), "impressions": impressions_marque, "position": position_marque,
                   "requetes": [r["requete"] for r in sorted(marque, key=lambda r: r["impressions"], reverse=True)[:5]]},
        "pages": sorted(pages, key=lambda p: (p["clics"], p["impressions"]), reverse=True)[:8],
    }


def rafraichir(db, aujourd_hui: date = None) -> dict:
    """Relit GA4 et la Search Console, garde le resultat en base. Une source en erreur n'empeche pas l'autre."""
    aujourd_hui = aujourd_hui or date.today()
    with _verrou:
        _en_cours.set()
        try:
            donnees = {"maj": datetime.utcnow().isoformat(timespec="seconds")}
            try:
                if not PROPRIETE_GA4:
                    raise RuntimeError("Propriété GA4 non renseignée (variable ASSORH_GA4_PROPRIETE).")
                identifiants = identifiants_analytics(db)
                if not identifiants:
                    raise RuntimeError("Google Analytics non autorisé : reconnecter la Search Console depuis /search-console/connecter "
                                       "(le consentement demande maintenant aussi Google Analytics).")
                donnees["ga4"] = lire_ga4(identifiants, aujourd_hui)
            except Exception as erreur:                                  # noqa: BLE001 - erreur affichee telle quelle a Jonathan via l'API
                donnees["ga4"] = {"erreur": str(erreur)}
            try:
                identifiants_sc = google_oauth.obtenir_identifiants_search_console(db)
                if not identifiants_sc:
                    raise RuntimeError("Search Console non connectée (ou accès révoqué) : reconnecter depuis /search-console/connecter.")
                donnees["sc"] = lire_search_console(identifiants_sc, aujourd_hui)
            except Exception as erreur:                                  # noqa: BLE001
                donnees["sc"] = {"erreur": str(erreur)}
            ligne = db.query(StatsSuiviAssoRH).first() or StatsSuiviAssoRH()
            ligne.donnees_json = json.dumps(donnees, ensure_ascii=False)
            ligne.maj_le = datetime.utcnow()
            db.add(ligne)
            db.commit()
            return donnees
        finally:
            _en_cours.clear()


def donnees_en_cache(db) -> tuple:
    """(donnees, maj_le) de la derniere lecture, ou ({}, None)."""
    ligne = db.query(StatsSuiviAssoRH).first()
    if not ligne or not ligne.donnees_json:
        return {}, None
    try:
        return json.loads(ligne.donnees_json), ligne.maj_le
    except ValueError:
        return {}, ligne.maj_le


def relancer_si_perime(db) -> bool:
    """Lance une relecture en arriere-plan si les chiffres ont plus de DUREE_CACHE (ou n'existent pas). True si lancee."""
    _, maj_le = donnees_en_cache(db)
    if (maj_le and datetime.utcnow() - maj_le < DUREE_CACHE) or _en_cours.is_set():
        return False
    _en_cours.set()
    threading.Thread(target=_rafraichir_silencieux, daemon=True).start()
    return True


def _rafraichir_silencieux():
    db = SessionLocal()
    try:
        rafraichir(db)
    except Exception:                                                    # noqa: BLE001 - tache de fond : la page affiche l'ancien cache
        _en_cours.clear()
    finally:
        db.close()


# ---------- mise en forme pour la page

MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
MOIS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def evolution(actuel, precedent, plus_bas_est_mieux: bool = False):
    """Texte, sens et lecture (bon / mauvais) de l'evolution ; None si la periode precedente est vide (debut du suivi)."""
    if not precedent or actuel is None:
        return None
    ecart = round(100 * (actuel - precedent) / precedent)
    if ecart == 0:
        return {"texte": "stable", "sens": "stable", "lecture": "neutre"}
    hausse = ecart > 0
    return {"texte": f"{'+' if hausse else '−'}{abs(ecart)} %", "sens": "hausse" if hausse else "baisse",
            "lecture": "bon" if hausse != plus_bas_est_mieux else "mauvais"}


def nombre(valeur) -> str:
    """1 234 (espace fine insecable) ; 3,4 pour une decimale."""
    if valeur is None:
        return "—"
    if isinstance(valeur, float) and not valeur.is_integer():
        return f"{valeur:.1f}".replace(".", ",")
    return f"{int(valeur):,}".replace(",", " ")


def _jour_long(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]}"


def _jour_court(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MOIS_COURTS[d.month - 1]}"


def _periode(p: dict) -> str:
    return f"du {_jour_long(p['debut'])} au {_jour_long(p['fin'])}" if p else ""


def _barres(lignes: list, cle: str = "valeur") -> list:
    """Ajoute la largeur relative (en %) de chaque barre horizontale."""
    maxi = max((l[cle] for l in lignes), default=0) or 1
    return [dict(l, largeur=max(2, round(100 * l[cle] / maxi))) for l in lignes]


def vue(donnees: dict) -> dict:
    """Tout ce que le gabarit affiche, deja formate (rien a calculer dans le HTML)."""
    resultat = {"ga": None, "sc": None, "ga_indisponible": False, "sc_indisponible": False}
    ga = donnees.get("ga4") or {}
    if ga and not ga.get("erreur"):
        a, p = ga["actuel"], ga["precedent"]
        jours = ga.get("jours", [])
        maxi = max((j["visites"] for j in jours), default=0)
        largeur = 560 / max(len(jours), 1)
        graphe = [{"x": round(i * largeur + 1, 1), "l": round(largeur - 2, 1),
                   "h": round(130 * j["visites"] / maxi, 1) if maxi else 0, "visites": j["visites"], "jour": _jour_court(j["date"])}
                  for i, j in enumerate(jours)]
        for barre in graphe:
            barre["y"] = round(140 - barre["h"], 1)
        resultat["ga"] = {
            "periode": _periode(ga.get("periode")),
            "vide": not any(a.get(k) for k in ("visiteurs", "visites", "pages_vues")),
            "tuiles": [
                {"titre": "Visiteurs", "valeur": nombre(a["visiteurs"]), "evo": evolution(a["visiteurs"], p["visiteurs"]),
                 "aide": "Personnes différentes venues sur le site"},
                {"titre": "Visites", "valeur": nombre(a["visites"]), "evo": evolution(a["visites"], p["visites"]),
                 "aide": "Une même personne peut venir plusieurs fois"},
                {"titre": "Pages vues", "valeur": nombre(a["pages_vues"]), "evo": evolution(a["pages_vues"], p["pages_vues"]),
                 "aide": "Nombre total de pages ouvertes"},
                {"titre": "Page « Demander une démo »", "valeur": nombre(a.get("vues_demo", 0)), "evo": evolution(a.get("vues_demo", 0), p.get("vues_demo", 0)),
                 "aide": "Nombre de fois où la page démo a été ouverte"},
            ],
            "graphe": graphe, "graphe_max": nombre(maxi),
            "graphe_debut": graphe[0]["jour"] if graphe else "", "graphe_fin": graphe[-1]["jour"] if graphe else "",
            "canaux": _barres(ga.get("canaux", [])), "appareils": _barres(ga.get("appareils", [])),
            "regions": _barres(ga.get("regions", [])), "sources": ga.get("sources", []),
            "pages": [dict(pg, vues_txt=nombre(pg["vues"]), visiteurs_txt=nombre(pg["visiteurs"])) for pg in ga.get("pages", [])],
        }
    elif ga:
        resultat["ga_indisponible"] = True
    sc = donnees.get("sc") or {}
    if sc and not sc.get("erreur"):
        a, p = sc["actuel"], sc["precedent"]
        taux = round(100 * a["clics"] / a["impressions"], 1) if a["impressions"] else None
        taux_prec = round(100 * p["clics"] / p["impressions"], 1) if p["impressions"] else None
        marque = sc.get("marque") or {}
        resultat["sc"] = {
            "periode": _periode(sc.get("periode")),
            "vide": not a["impressions"],
            "tuiles": [
                {"titre": "Apparitions dans Google", "valeur": nombre(a["impressions"]), "evo": evolution(a["impressions"], p["impressions"]),
                 "aide": "Nombre de fois où le site s'est affiché dans les résultats"},
                {"titre": "Clics vers le site", "valeur": nombre(a["clics"]), "evo": evolution(a["clics"], p["clics"]),
                 "aide": "Personnes qui ont cliqué sur le site depuis Google"},
                {"titre": "Position moyenne", "valeur": nombre(a["position"]) if a["impressions"] else "—",
                 "evo": evolution(a["position"], p["position"], plus_bas_est_mieux=True) if a["impressions"] and p["impressions"] else None,
                 "aide": "1 = tout en haut de la première page de Google"},
                {"titre": "Taux de clic", "valeur": f"{nombre(taux)} %" if taux is not None else "—", "evo": evolution(taux, taux_prec),
                 "aide": "Part des apparitions qui donnent un clic"},
            ],
            "marque": {"impressions": nombre(marque.get("impressions", 0)), "clics": nombre(marque.get("clics", 0)),
                       "position": nombre(marque.get("position")) if marque.get("position") is not None else None,
                       "premier": marque.get("position") is not None and marque["position"] < 1.5,
                       "requetes": marque.get("requetes", [])},
            "requetes": [dict(r, impressions_txt=nombre(r["impressions"]), clics_txt=nombre(r["clics"]), position_txt=nombre(r["position"]))
                         for r in sc.get("requetes", [])],
            "nb_requetes": sc.get("nb_requetes", 0),
            "pages": [dict(pg, impressions_txt=nombre(pg["impressions"]), clics_txt=nombre(pg["clics"])) for pg in sc.get("pages", [])],
        }
    elif sc:
        resultat["sc_indisponible"] = True
    if donnees.get("maj"):
        d = datetime.fromisoformat(donnees["maj"])
        d += timedelta(hours=2 if 3 < d.month < 11 else 1)                        # heure de Paris approximative
        resultat["maj"] = f"{_jour_long(d.date().isoformat())} à {d.hour}h{d.minute:02d}"
    return resultat
