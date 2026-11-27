"""
API de lecture des statistiques Google des agences GoldUnion, pour que Claude prepare des analyses (evolution jour par jour, mots-cles de
recherche) sans passer par l'interface.

- GET /api/goldunion/stats?q=<nom ou ville>&debut=AAAA-MM-JJ&fin=AAAA-MM-JJ&mois=3, en-tete X-Cle.
- Lecture seule : aucune ecriture en base, aucune modification des fiches.
- Agences concernees : celles du suivi GoldUnion, et toute fiche dont le nom contient « GoldUnion » (les agences hors France n'ont pas
  toujours les etiquettes du suivi).

La cle n'est pas dans le code : seule son empreinte SHA-256 y figure. La valeur est gardee hors du depot
(CC Fiche Locale/credentials/cle-api-stats-goldunion.txt).
"""

import hashlib
import hmac
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import google_oauth, google_performance, models, suivi_goldunion
from .database import obtenir_session

EMPREINTE_CLE = "7dcdae9deb5d1eddd5e74c35bb46262da7d290e1101dcf6b274d9f7081d469d3"
ENTETES = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
MAX_JOURS = 540                 # l'historique Google remonte a 18 mois environ
MAX_AGENCES = 10
MAX_MOIS_MOTS_CLES = 6

routeur = APIRouter()


def _cle_valide(request: Request) -> bool:
    return hmac.compare_digest(hashlib.sha256((request.headers.get("x-cle") or "").encode()).hexdigest(), EMPREINTE_CLE)


def _date(valeur: str, defaut: date) -> date:
    try:
        return date.fromisoformat(valeur) if valeur else defaut
    except ValueError:
        return defaut


def _mois_precedents(fin: date, nombre: int) -> list:
    """Les `nombre` derniers mois (premier jour de chaque mois), du plus ancien au plus recent, en finissant par le mois de `fin`."""
    mois, courant = [], fin.replace(day=1)
    for _ in range(nombre):
        mois.append(courant)
        courant = (courant - timedelta(days=1)).replace(day=1)
    return list(reversed(mois))


def agences_correspondantes(db: Session, q: str) -> list:
    """Agences GoldUnion (suivi + noms contenant « goldunion ») dont le nom ou la ville contient q, sans tenir compte de la casse."""
    q = q.lower()
    suivies = {c.id: c for c in suivi_goldunion.clients_suivis(db, suivi_goldunion.lire_parametre(db))}
    for c in db.query(models.Client).filter(models.Client.nom.ilike("%goldunion%")).all():
        suivies.setdefault(c.id, c)
    trouvees = [c for c in suivies.values() if q in (c.nom or "").lower() or q in (c.localisation_ville or "").lower()]
    return sorted(trouvees, key=lambda c: c.nom or "")[:MAX_AGENCES]


@routeur.get("/api/goldunion/stats")
def api_stats(request: Request, q: str = "", debut: str = "", fin: str = "", mois: int = 3, db: Session = Depends(obtenir_session)):
    if not _cle_valide(request):
        return JSONResponse({"erreur": "introuvable"}, status_code=404, headers=ENTETES)
    q = (q or "").strip()
    if len(q) < 3:
        return JSONResponse({"erreur": "q attendu (3 caractères minimum) : nom ou ville de l'agence"}, status_code=400, headers=ENTETES)
    fin_d = min(_date(fin, date.today() - timedelta(days=1)), date.today())
    debut_d = max(_date(debut, fin_d - timedelta(days=120)), fin_d - timedelta(days=MAX_JOURS))
    if debut_d > fin_d:
        return JSONResponse({"erreur": "debut doit précéder fin"}, status_code=400, headers=ENTETES)
    nb_mois = max(0, min(int(mois or 0), MAX_MOIS_MOTS_CLES))

    identifiants_par_compte, resultats = {}, []
    for client in agences_correspondantes(db, q):
        agence = {"client_id": client.id, "nom": client.nom, "ville": client.localisation_ville or "", "location_id": client.location_id or "",
                  "jours": {}, "mots_cles": {}, "erreur": ""}
        resultats.append(agence)
        if not client.location_id or not client.compte_google_id:
            agence["erreur"] = "fiche Google non reliée"
            continue
        if client.compte_google_id not in identifiants_par_compte:
            identifiants_par_compte[client.compte_google_id] = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        identifiants = identifiants_par_compte[client.compte_google_id]
        if not identifiants:
            agence["erreur"] = "connexion Google expirée ou absente"
            continue
        try:
            par_jour = google_performance.recuperer_statistiques_par_jour(identifiants, client.location_id, debut_d, fin_d)
            agence["jours"] = {j.isoformat(): v for j, v in sorted(par_jour.items())}
            for premier in _mois_precedents(fin_d, nb_mois):
                agence["mots_cles"][premier.strftime("%Y-%m")] = google_performance.recuperer_mots_cles_recherche(identifiants, client.location_id, premier, premier, limite=30)
        except Exception as erreur:
            agence["erreur"] = str(erreur)[:300]
    return JSONResponse({"debut": debut_d.isoformat(), "fin": fin_d.isoformat(), "metriques": list(google_performance.LIBELLES.values()),
                         "agences": resultats}, headers=ENTETES)
