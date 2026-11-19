"""
Suivi AssoRH : le journal de bord de Jonathan pour Franck Laduche (client AssoRH), jour par jour.

- Page publique sans mot de passe, accessible par son lien secret (/assorh/<jeton>) : Franck y lit le journal et peut laisser
  une remarque sous chaque journee, ou une remarque generale.
- API protegee par une cle (en-tete X-Cle) : c'est par la que Claude ecrit les journees et lit les remarques de Franck.

Ni le jeton ni la cle ne sont dans le code : seules leurs empreintes SHA-256 y figurent. Les valeurs sont gardees hors du depot
(Desktop/AssoRH/suivi/acces-suivi.txt).
"""

import hashlib
import hmac
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from . import frequentation_assorh
from .database import Base, obtenir_session

EMPREINTE_JETON = "497273561af644db159d759ef7ae2371e2b76961cf27ba299431acc43c4dede5"
EMPREINTE_CLE = "075defcf40c8f0ba3ba7c6d65088fe459882c1069119de861bee293be8725823"

ENTETES = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
THEMES = ("Configuration", "Site", "Contenus", "Publicité", "Prospection", "Témoignages", "Resofit", "Organisation")
AUTEURS = ("Franck", "Jonathan")
TAILLE_REMARQUE = 3000
MAX_REMARQUES_PAR_HEURE = 30                   # garde-fou si le lien circulait : au-dela, les nouvelles remarques sont ignorees
MAX_ACTIONS = 8
FORMAT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
JOURS_SEMAINE = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")

routeur = APIRouter()
_templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))


class JourSuiviAssoRH(Base):
    """Une journee du journal : un resume et quelques actions (liste JSON de {theme, texte})."""

    __tablename__ = "suivi_assorh_jours"

    id = Column(Integer, primary_key=True)
    date = Column(String(10), unique=True, nullable=False)     # AAAA-MM-JJ
    resume = Column(Text, default="")
    actions_json = Column(Text, default="[]")
    maj_le = Column(DateTime, default=datetime.utcnow)


class RemarqueSuiviAssoRH(Base):
    """Remarque laissee sur la page (par Franck en general) : rattachee a une journee, ou generale si `jour` est vide."""

    __tablename__ = "suivi_assorh_remarques"

    id = Column(Integer, primary_key=True)
    jour = Column(String(10), default="")
    auteur = Column(String, default="Franck")
    texte = Column(Text, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)


class TacheSuiviAssoRH(Base):
    """Petite tache confiee a Franck (ajoutee par l'API a la demande de Jonathan) : Franck la coche sur la page quand c'est fait."""

    __tablename__ = "suivi_assorh_taches"

    id = Column(Integer, primary_key=True)
    jour = Column(String(10), default="")                      # journee a laquelle la demande a ete faite
    texte = Column(Text, nullable=False)
    faite = Column(Boolean, default=False)
    faite_le = Column(DateTime, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)


class AvisPubSuiviAssoRH(Base):
    """Onglet Publicités : l'avis de Franck sur une pub (« valide » ou « a-revoir »), une ligne par pub du catalogue."""
    __tablename__ = "suivi_assorh_avis_pubs"

    id = Column(Integer, primary_key=True)
    pub_id = Column(String, unique=True, nullable=False)
    avis = Column(String, default="")
    maj_le = Column(DateTime, default=datetime.utcnow)


class DemandePubSuiviAssoRH(Base):
    """Onglet Publicités : une demande de modification de Franck sur une pub (« changer tel mot »…), que Jonathan marque comme faite."""
    __tablename__ = "suivi_assorh_demandes_pubs"

    id = Column(Integer, primary_key=True)
    pub_id = Column(String, nullable=False, index=True)
    texte = Column(Text, nullable=False)
    faite = Column(Boolean, default=False)
    faite_le = Column(DateTime, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)


# ---------- outils

def _correspond(valeur: str, empreinte: str) -> bool:
    return hmac.compare_digest(hashlib.sha256((valeur or "").encode()).hexdigest(), empreinte)


def _date_valide(valeur: str) -> bool:
    if not valeur or not FORMAT_DATE.fullmatch(valeur):
        return False
    try:
        date.fromisoformat(valeur)
    except ValueError:
        return False
    return True


def libelle_jour(iso: str) -> str:
    """« Mardi 10 novembre » (l'annee seulement si ce n'est pas l'annee en cours)."""
    d = date.fromisoformat(iso)
    texte = f"{JOURS_SEMAINE[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]}"
    if d.year != date.today().year:
        texte += f" {d.year}"
    return texte[0].upper() + texte[1:]


def libelle_moment(quand: datetime) -> str:
    """Heure de Paris approximative (UTC+1 l'hiver, UTC+2 l'ete) : suffisant pour dater une remarque."""
    ete = 3 < quand.month < 11
    local = quand + timedelta(hours=2 if ete else 1)
    return f"{local.day} {MOIS[local.month - 1]} à {local.hour}h{local.minute:02d}"


def _actions(jour: JourSuiviAssoRH) -> list:
    try:
        valeur = json.loads(jour.actions_json or "[]")
    except ValueError:
        return []
    return [a for a in valeur if isinstance(a, dict) and a.get("texte")]


def _remarque_json(r: RemarqueSuiviAssoRH) -> dict:
    return {"id": r.id, "jour": r.jour or "", "auteur": r.auteur, "texte": r.texte, "cree_le": r.cree_le.isoformat() if r.cree_le else ""}


def _tache_json(t: TacheSuiviAssoRH) -> dict:
    return {"id": t.id, "jour": t.jour or "", "texte": t.texte, "faite": bool(t.faite),
            "faite_le": t.faite_le.isoformat() if t.faite_le else "", "cree_le": t.cree_le.isoformat() if t.cree_le else ""}


def taches_pour_page(db: Session) -> list:
    """Taches a faire d'abord (les plus anciennes en premier), puis les 10 dernieres faites."""
    a_faire = db.query(TacheSuiviAssoRH).filter(TacheSuiviAssoRH.faite.isnot(True)).order_by(TacheSuiviAssoRH.cree_le, TacheSuiviAssoRH.id).all()
    faites = db.query(TacheSuiviAssoRH).filter(TacheSuiviAssoRH.faite.is_(True)).order_by(TacheSuiviAssoRH.faite_le.desc(), TacheSuiviAssoRH.id.desc()).limit(10).all()
    return [{
        "id": t.id, "texte": t.texte, "faite": bool(t.faite),
        "libelle": f"Demandé le {libelle_jour(t.jour).lower()}" if _date_valide(t.jour or "") else "",
        "faite_quand": libelle_moment(t.faite_le) if t.faite_le else "",
    } for t in a_faire + faites]


def _introuvable():
    return HTMLResponse("Page introuvable.", status_code=404, headers=ENTETES)


# ---------- page de Franck

@routeur.get("/assorh/{jeton}", response_class=HTMLResponse)
def page_suivi(jeton: str, request: Request, db: Session = Depends(obtenir_session)):
    """Journal jour par jour, du plus recent au plus ancien, avec les remarques sous chaque journee."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    remarques = defaultdict(list)
    for r in db.query(RemarqueSuiviAssoRH).order_by(RemarqueSuiviAssoRH.cree_le, RemarqueSuiviAssoRH.id).all():
        remarques[r.jour or ""].append({"auteur": r.auteur, "texte": r.texte, "quand": libelle_moment(r.cree_le) if r.cree_le else ""})
    jours = [{
        "date": j.date, "libelle": libelle_jour(j.date), "resume": j.resume or "", "actions": _actions(j), "remarques": remarques.get(j.date, []),
    } for j in db.query(JourSuiviAssoRH).order_by(JourSuiviAssoRH.date.desc()).all()]
    taches = taches_pour_page(db)
    return _templates.TemplateResponse(request, "suivi_assorh.html", {
        "jeton": jeton, "jours": jours, "generales": remarques.get("", []), "auteurs": AUTEURS,
        "maj": jours[0]["libelle"] if jours else "", "taches": taches, "nb_a_faire": sum(1 for t in taches if not t["faite"]),
    }, headers=ENTETES)


@routeur.get("/assorh/{jeton}/frequentation", response_class=HTMLResponse)
def page_frequentation(jeton: str, request: Request, db: Session = Depends(obtenir_session)):
    """Onglet « Fréquentation du site » : chiffres GA4 et Search Console gardés en base, relus en arrière-plan s'ils sont périmés."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    donnees, _ = frequentation_assorh.donnees_en_cache(db)
    relance = frequentation_assorh.relancer_si_perime(db)
    return _templates.TemplateResponse(request, "suivi_assorh_frequentation.html", {
        "jeton": jeton, "onglet": "frequentation", "v": frequentation_assorh.vue(donnees), "chargement": relance and not donnees,
    }, headers=ENTETES)


# ---------- onglet « Publicités » : les pubs preparees pour Franck (fichiers exportes par AssoRH/pubs/exporter_pour_suivi.py)

DOSSIER_PUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assorh_pubs")
AVIS_PUBS = {"valide": "Validée", "a-revoir": "À revoir"}
TAILLE_DEMANDE = 2000
MAX_DEMANDES_PAR_HEURE = 30
_cache_catalogue = {"mtime": None, "valeur": None}


def catalogue_pubs() -> dict:
    """Catalogue des pubs (series -> pubs -> formats), relu seulement si le fichier a change."""
    chemin = os.path.join(DOSSIER_PUBS, "catalogue.json")
    try:
        mtime = os.path.getmtime(chemin)
    except OSError:
        return {"series": []}
    if _cache_catalogue["mtime"] != mtime:
        with open(chemin, encoding="utf-8") as f:
            _cache_catalogue.update(mtime=mtime, valeur=json.load(f))
    return _cache_catalogue["valeur"]


def _pubs(catalogue: dict) -> list:
    return [p for s in catalogue.get("series", []) for p in s.get("pubs", [])]


def _fichiers_pubs(catalogue: dict) -> set:
    """Seuls les fichiers cites par le catalogue sont servis."""
    fichiers = set()
    for p in _pubs(catalogue):
        for f in p.get("formats", []):
            fichiers.update(x for x in (f.get("fichier"), f.get("affiche"), f.get("pdf")) if x)
            fichiers.update(f.get("pages", []))
    return fichiers


def _demande_json(d: DemandePubSuiviAssoRH) -> dict:
    return {"id": d.id, "pub_id": d.pub_id, "texte": d.texte, "faite": bool(d.faite),
            "faite_le": d.faite_le.isoformat() if d.faite_le else "", "cree_le": d.cree_le.isoformat() if d.cree_le else ""}


@routeur.get("/assorh/{jeton}/pubs", response_class=HTMLResponse)
def page_pubs(jeton: str, request: Request, db: Session = Depends(obtenir_session)):
    """Onglet « Publicités » : toutes les pubs, avec pour chacune l'avis de Franck et ses demandes de modification."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    catalogue = catalogue_pubs()
    avis = {a.pub_id: a.avis for a in db.query(AvisPubSuiviAssoRH).all()}
    demandes = defaultdict(list)
    for d in db.query(DemandePubSuiviAssoRH).order_by(DemandePubSuiviAssoRH.cree_le, DemandePubSuiviAssoRH.id).all():
        demandes[d.pub_id].append({"texte": d.texte, "faite": bool(d.faite), "quand": libelle_moment(d.cree_le) if d.cree_le else "",
                                   "faite_quand": libelle_moment(d.faite_le) if d.faite_le else ""})
    series = [dict(s, pubs=[dict(p, avis=avis.get(p["id"], ""), demandes=demandes.get(p["id"], [])) for p in s.get("pubs", [])])
              for s in catalogue.get("series", [])]
    toutes = [p for s in series for p in s["pubs"]]
    return _templates.TemplateResponse(request, "suivi_assorh_pubs.html", {
        "jeton": jeton, "onglet": "pubs", "series": series, "libelles_avis": AVIS_PUBS, "total": len(toutes),
        "nb_valides": sum(1 for p in toutes if p["avis"] == "valide"), "nb_a_revoir": sum(1 for p in toutes if p["avis"] == "a-revoir"),
        "nb_demandes": sum(1 for p in toutes for d in p["demandes"] if not d["faite"]),
    }, headers=ENTETES)


@routeur.get("/assorh/{jeton}/pubs/f/{chemin:path}")
def fichier_pub(jeton: str, chemin: str):
    """Image, video ou PDF d'une pub (derriere le lien secret, comme la page)."""
    if not _correspond(jeton, EMPREINTE_JETON) or chemin not in _fichiers_pubs(catalogue_pubs()):
        return _introuvable()
    complet = os.path.realpath(os.path.join(DOSSIER_PUBS, chemin))
    if not complet.startswith(os.path.realpath(DOSSIER_PUBS) + os.sep) or not os.path.isfile(complet):
        return _introuvable()
    return FileResponse(complet, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, max-age=86400", "Referrer-Policy": "no-referrer"})


@routeur.post("/assorh/{jeton}/pubs/avis")
def donner_avis_pub(jeton: str, pub_id: str = Form(""), avis: str = Form(""), db: Session = Depends(obtenir_session)):
    """Franck valide une pub, la met « à revoir », ou retire son choix (avis vide)."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    if pub_id in {p["id"] for p in _pubs(catalogue_pubs())}:
        ligne = db.query(AvisPubSuiviAssoRH).filter_by(pub_id=pub_id).first()
        if avis in AVIS_PUBS:
            if not ligne:
                ligne = AvisPubSuiviAssoRH(pub_id=pub_id)
                db.add(ligne)
            ligne.avis, ligne.maj_le = avis, datetime.utcnow()
        elif ligne:
            db.delete(ligne)
        db.commit()
    return RedirectResponse(f"/assorh/{jeton}/pubs#pub-{pub_id}", status_code=303, headers=ENTETES)


@routeur.post("/assorh/{jeton}/pubs/demande")
def demander_modif_pub(jeton: str, pub_id: str = Form(""), texte: str = Form(""), db: Session = Depends(obtenir_session)):
    """Franck demande une modification sur une pub (« remplacer tel mot par… »)."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    texte = (texte or "").strip()[:TAILLE_DEMANDE]
    recentes = db.query(DemandePubSuiviAssoRH).filter(DemandePubSuiviAssoRH.cree_le >= datetime.utcnow() - timedelta(hours=1)).count()
    if texte and pub_id in {p["id"] for p in _pubs(catalogue_pubs())} and recentes < MAX_DEMANDES_PAR_HEURE:
        db.add(DemandePubSuiviAssoRH(pub_id=pub_id, texte=texte))
        db.commit()
    return RedirectResponse(f"/assorh/{jeton}/pubs#pub-{pub_id}", status_code=303, headers=ENTETES)


@routeur.post("/assorh/{jeton}/tache/{tache_id}")
def cocher_tache(jeton: str, tache_id: int, db: Session = Depends(obtenir_session)):
    """Franck coche (ou decoche) une tache."""
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    tache = db.get(TacheSuiviAssoRH, tache_id)
    if tache:
        tache.faite = not tache.faite
        tache.faite_le = datetime.utcnow() if tache.faite else None
        db.commit()
    return RedirectResponse(f"/assorh/{jeton}#a-faire", status_code=303, headers=ENTETES)


@routeur.post("/assorh/{jeton}/remarque")
def ajouter_remarque(jeton: str, texte: str = Form(""), jour: str = Form(""), auteur: str = Form("Franck"), db: Session = Depends(obtenir_session)):
    if not _correspond(jeton, EMPREINTE_JETON):
        return _introuvable()
    texte = (texte or "").strip()[:TAILLE_REMARQUE]
    jour = jour if _date_valide(jour) else ""
    auteur = auteur if auteur in AUTEURS else "Franck"
    recentes = db.query(RemarqueSuiviAssoRH).filter(RemarqueSuiviAssoRH.cree_le >= datetime.utcnow() - timedelta(hours=1)).count()
    if texte and recentes < MAX_REMARQUES_PAR_HEURE:
        db.add(RemarqueSuiviAssoRH(jour=jour, auteur=auteur, texte=texte))
        db.commit()
    ancre = f"#jour-{jour}" if jour else "#remarques"
    return RedirectResponse(f"/assorh/{jeton}{ancre}", status_code=303, headers=ENTETES)


# ---------- API (Claude)

def _cle_valide(request: Request) -> bool:
    return _correspond(request.headers.get("x-cle", ""), EMPREINTE_CLE)


def _refus():
    return JSONResponse({"erreur": "introuvable"}, status_code=404, headers=ENTETES)


@routeur.get("/api/assorh/journal")
def api_journal(request: Request, db: Session = Depends(obtenir_session)):
    """Tout le journal et toutes les remarques (pour relire ce que Franck a ecrit)."""
    if not _cle_valide(request):
        return _refus()
    jours = [{"date": j.date, "resume": j.resume or "", "actions": _actions(j)} for j in db.query(JourSuiviAssoRH).order_by(JourSuiviAssoRH.date.desc()).all()]
    remarques = [_remarque_json(r) for r in db.query(RemarqueSuiviAssoRH).order_by(RemarqueSuiviAssoRH.cree_le, RemarqueSuiviAssoRH.id).all()]
    taches = [_tache_json(t) for t in db.query(TacheSuiviAssoRH).order_by(TacheSuiviAssoRH.cree_le, TacheSuiviAssoRH.id).all()]
    return JSONResponse({"jours": jours, "remarques": remarques, "taches": taches}, headers=ENTETES)


@routeur.get("/api/assorh/frequentation")
def api_frequentation(request: Request, db: Session = Depends(obtenir_session)):
    """Derniers chiffres gardés (avec le détail des erreurs éventuelles, que la page de Franck n'affiche pas)."""
    if not _cle_valide(request):
        return _refus()
    donnees, maj_le = frequentation_assorh.donnees_en_cache(db)
    return JSONResponse({"maj_le": maj_le.isoformat() if maj_le else "", "donnees": donnees}, headers=ENTETES)


@routeur.get("/api/assorh/pubs")
def api_pubs(request: Request, db: Session = Depends(obtenir_session)):
    """Avis de Franck et demandes de modification, pub par pub (avec le titre de chaque pub)."""
    if not _cle_valide(request):
        return _refus()
    titres = {p["id"]: p.get("titre", "") for p in _pubs(catalogue_pubs())}
    avis = {a.pub_id: {"avis": a.avis, "maj_le": a.maj_le.isoformat() if a.maj_le else ""} for a in db.query(AvisPubSuiviAssoRH).all()}
    demandes = [dict(_demande_json(d), titre=titres.get(d.pub_id, ""))
                for d in db.query(DemandePubSuiviAssoRH).order_by(DemandePubSuiviAssoRH.cree_le, DemandePubSuiviAssoRH.id).all()]
    pubs = [{"id": i, "titre": t, **avis.get(i, {"avis": "", "maj_le": ""})} for i, t in titres.items()]
    return JSONResponse({"pubs": pubs, "demandes": demandes}, headers=ENTETES)


@routeur.post("/api/assorh/pubs/demandes/{demande_id}")
async def api_marquer_demande(demande_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Jonathan marque une demande de modification comme faite : {"faite": true} (false pour la rouvrir)."""
    if not _cle_valide(request):
        return _refus()
    try:
        corps = await request.json()
    except ValueError:
        corps = {}
    demande = db.get(DemandePubSuiviAssoRH, demande_id)
    if not demande:
        return JSONResponse({"erreur": "demande introuvable"}, status_code=404, headers=ENTETES)
    demande.faite = bool((corps or {}).get("faite", True)) if isinstance(corps, dict) else True
    demande.faite_le = datetime.utcnow() if demande.faite else None
    db.commit()
    return JSONResponse({"ok": True, "demande": _demande_json(demande)}, headers=ENTETES)


@routeur.post("/api/assorh/frequentation/rafraichir")
def api_rafraichir_frequentation(request: Request, db: Session = Depends(obtenir_session)):
    """Relit tout de suite Google Analytics et la Search Console."""
    if not _cle_valide(request):
        return _refus()
    return JSONResponse(frequentation_assorh.rafraichir(db), headers=ENTETES)


@routeur.post("/api/assorh/taches")
async def api_ajouter_tache(request: Request, db: Session = Depends(obtenir_session)):
    """Tache pour Franck : {"texte": "...", "jour": "AAAA-MM-JJ"} (jour de la demande, aujourd'hui par defaut)."""
    if not _cle_valide(request):
        return _refus()
    try:
        corps = await request.json()
    except ValueError:
        return JSONResponse({"erreur": "corps JSON attendu"}, status_code=400)
    texte = str((corps or {}).get("texte") or "").strip()[:600] if isinstance(corps, dict) else ""
    if not texte:
        return JSONResponse({"erreur": "texte vide"}, status_code=400)
    jour = corps.get("jour") if _date_valide(corps.get("jour") or "") else date.today().isoformat()
    tache = TacheSuiviAssoRH(jour=jour, texte=texte)
    db.add(tache)
    db.commit()
    return JSONResponse({"ok": True, "tache": _tache_json(tache)}, headers=ENTETES)


@routeur.delete("/api/assorh/taches/{tache_id}")
def api_supprimer_tache(tache_id: int, request: Request, db: Session = Depends(obtenir_session)):
    if not _cle_valide(request):
        return _refus()
    nb = db.query(TacheSuiviAssoRH).filter_by(id=tache_id).delete()
    db.commit()
    return JSONResponse({"ok": True, "supprimes": nb}, headers=ENTETES)


@routeur.put("/api/assorh/jours/{date_jour}")
async def api_ecrire_jour(date_jour: str, request: Request, db: Session = Depends(obtenir_session)):
    """Cree ou remplace la journee : {"resume": "...", "actions": [{"theme": "...", "texte": "..."}]}."""
    if not _cle_valide(request):
        return _refus()
    if not _date_valide(date_jour):
        return JSONResponse({"erreur": "date attendue au format AAAA-MM-JJ"}, status_code=400)
    try:
        corps = await request.json()
    except ValueError:
        return JSONResponse({"erreur": "corps JSON attendu"}, status_code=400)
    if not isinstance(corps, dict):
        return JSONResponse({"erreur": "corps JSON attendu"}, status_code=400)
    actions = []
    for a in (corps.get("actions") or [])[:MAX_ACTIONS]:
        if isinstance(a, dict) and str(a.get("texte") or "").strip():
            theme = a.get("theme") if a.get("theme") in THEMES else "Organisation"
            actions.append({"theme": theme, "texte": str(a["texte"]).strip()[:600]})
    jour = db.query(JourSuiviAssoRH).filter_by(date=date_jour).first() or JourSuiviAssoRH(date=date_jour)
    jour.resume = str(corps.get("resume") or "").strip()[:1200]
    jour.actions_json = json.dumps(actions, ensure_ascii=False)
    jour.maj_le = datetime.utcnow()
    db.add(jour)
    db.commit()
    return JSONResponse({"ok": True, "date": date_jour, "actions": len(actions)}, headers=ENTETES)


@routeur.delete("/api/assorh/jours/{date_jour}")
def api_supprimer_jour(date_jour: str, request: Request, db: Session = Depends(obtenir_session)):
    if not _cle_valide(request):
        return _refus()
    nb = db.query(JourSuiviAssoRH).filter_by(date=date_jour).delete()
    db.commit()
    return JSONResponse({"ok": True, "supprimes": nb}, headers=ENTETES)


@routeur.post("/api/assorh/remarques")
async def api_ajouter_remarque(request: Request, db: Session = Depends(obtenir_session)):
    """Reponse de Jonathan sur la page : {"texte": "...", "jour": "AAAA-MM-JJ" ou "", "auteur": "Jonathan"}."""
    if not _cle_valide(request):
        return _refus()
    try:
        corps = await request.json()
    except ValueError:
        return JSONResponse({"erreur": "corps JSON attendu"}, status_code=400)
    texte = str((corps or {}).get("texte") or "").strip()[:TAILLE_REMARQUE] if isinstance(corps, dict) else ""
    if not texte:
        return JSONResponse({"erreur": "texte vide"}, status_code=400)
    jour = corps.get("jour") if _date_valide(corps.get("jour") or "") else ""
    auteur = corps.get("auteur") if corps.get("auteur") in AUTEURS else "Jonathan"
    remarque = RemarqueSuiviAssoRH(jour=jour, auteur=auteur, texte=texte)
    db.add(remarque)
    db.commit()
    return JSONResponse({"ok": True, "remarque": _remarque_json(remarque)}, headers=ENTETES)
