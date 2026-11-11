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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

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
    return _templates.TemplateResponse(request, "suivi_assorh.html", {
        "jeton": jeton, "jours": jours, "generales": remarques.get("", []), "auteurs": AUTEURS,
        "maj": jours[0]["libelle"] if jours else "",
    }, headers=ENTETES)


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
    return JSONResponse({"jours": jours, "remarques": remarques}, headers=ENTETES)


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
