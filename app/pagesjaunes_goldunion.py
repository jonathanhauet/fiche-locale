"""
PagesJaunes GoldUnion : page publique ou les experts des agences GoldUnion declarent la fiche PagesJaunes qu'ils ont revendiquee
(ville de l'agence, adresse e-mail utilisee sur Solocal, prenom et nom), et recuperent le logo et les photos a mettre sur la fiche.

- /pagesjaunes-goldunion : page publique sans mot de passe (lien donne dans le tuto), formulaire + telechargements.
- /pagesjaunes-goldunion/photos.zip : les 17 photos en un seul fichier.
- /pagesjaunes-goldunion/declarations : liste des declarations, reservee a l'espace connecte.

Le routeur est cree par main.py, qui fournit les gabarits et la connexion. La table est creee ici (main.py cree les autres
tables avant d'inclure ce routeur).
"""

import io
import os
import re
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.orm import Session

from .database import Base, engine, obtenir_session

CHEMIN = "/pagesjaunes-goldunion"
DOSSIER_FICHIERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "goldunion-pagesjaunes")
URL_FICHIERS = "/static/goldunion-pagesjaunes"
DOMAINE_ATTENDU = "@goldunion.fr"
MAX_DECLARATIONS_PAR_HEURE = 60                 # garde-fou si le lien circulait : au-dela, les nouvelles declarations sont ignorees
FORMAT_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ENTETES_PUBLICS = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
ENTETES_PRIVES = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}


class DeclarationPagesJaunesGoldUnion(Base):
    """Une agence GoldUnion qui a revendique sa fiche PagesJaunes : avec quelle adresse, et qui l'a fait."""

    __tablename__ = "pagesjaunes_goldunion_declarations"

    id = Column(Integer, primary_key=True)
    ville = Column(String(80), nullable=False)
    email = Column(String(160), nullable=False)
    responsable = Column(String(120), nullable=False)          # prenom et nom de la personne qui a fait la manipulation
    cree_le = Column(DateTime, default=datetime.utcnow)


def photos() -> list:
    """Noms des photos a mettre sur la fiche, dans l'ordre."""
    if not os.path.isdir(DOSSIER_FICHIERS):
        return []
    return sorted(f for f in os.listdir(DOSSIER_FICHIERS) if f.startswith("goldunion-photo-") and f.lower().endswith(".jpg"))


_zip_cache = {"cle": None, "octets": b""}


def zip_photos() -> bytes:
    """Les photos dans un seul .zip (sans recompression : ce sont deja des JPG), garde en memoire tant que les fichiers ne changent pas."""
    noms = photos()
    cle = tuple((n, os.path.getmtime(os.path.join(DOSSIER_FICHIERS, n))) for n in noms)
    if _zip_cache["cle"] != cle:
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as archive:
            for n in noms:
                archive.write(os.path.join(DOSSIER_FICHIERS, n), n)
        _zip_cache.update(cle=cle, octets=tampon.getvalue())
    return _zip_cache["octets"]


def adresse_attendue(email: str) -> bool:
    return (email or "").strip().lower().endswith(DOMAINE_ATTENDU)


def _nettoyer(valeur: str, taille: int) -> str:
    return " ".join((valeur or "").split())[:taille]


def libelle_moment(quand: datetime) -> str:
    """Date et heure de Paris approximatives (UTC+1 l'hiver, UTC+2 l'ete)."""
    if not quand:
        return ""
    ete = 3 < quand.month < 11
    local = quand + timedelta(hours=2 if ete else 1)
    return local.strftime("%d/%m/%Y à %Hh%M")


def creer_routeur(templates, utilisateur_connecte, rediriger_si_non_connecte) -> APIRouter:
    Base.metadata.create_all(bind=engine, tables=[DeclarationPagesJaunesGoldUnion.__table__])
    routeur = APIRouter(prefix=CHEMIN)

    @routeur.get("", response_class=HTMLResponse)
    def page_publique(request: Request, ok: str = "", erreur: str = ""):
        return templates.TemplateResponse(request, "pagesjaunes_goldunion.html", {
            "chemin": CHEMIN, "url_fichiers": URL_FICHIERS, "photos": photos(), "ok": ok, "erreur": erreur,
            "adresse_perso": ok == "perso",
        }, headers=ENTETES_PUBLICS)

    @routeur.post("")
    def declarer(ville: str = Form(""), email: str = Form(""), responsable: str = Form(""), site_web: str = Form(""),
                 db: Session = Depends(obtenir_session)):
        """Enregistre la declaration. `site_web` est un champ cache : rempli, c'est un robot, on ne garde rien."""
        ville, email, responsable = _nettoyer(ville, 80), _nettoyer(email, 160).lower(), _nettoyer(responsable, 120)
        if site_web:
            return RedirectResponse(f"{CHEMIN}?ok=1#declarer", status_code=303, headers=ENTETES_PUBLICS)
        if not ville or not responsable or not FORMAT_EMAIL.fullmatch(email):
            return RedirectResponse(f"{CHEMIN}?erreur=1#declarer", status_code=303, headers=ENTETES_PUBLICS)
        recentes = db.query(DeclarationPagesJaunesGoldUnion).filter(DeclarationPagesJaunesGoldUnion.cree_le >= datetime.utcnow() - timedelta(hours=1)).count()
        if recentes < MAX_DECLARATIONS_PAR_HEURE:
            db.add(DeclarationPagesJaunesGoldUnion(ville=ville, email=email, responsable=responsable))
            db.commit()
        etat = "1" if adresse_attendue(email) else "perso"
        return RedirectResponse(f"{CHEMIN}?ok={etat}#declarer", status_code=303, headers=ENTETES_PUBLICS)

    @routeur.get("/photos.zip")
    def telecharger_photos():
        return Response(zip_photos(), media_type="application/zip",
                        headers={**ENTETES_PUBLICS, "Content-Disposition": 'attachment; filename="goldunion-photos.zip"'})

    @routeur.get("/logo.png")
    def telecharger_logo():
        with open(os.path.join(DOSSIER_FICHIERS, "logo-goldunion.png"), "rb") as fichier:
            octets = fichier.read()
        return Response(octets, media_type="image/png",
                        headers={**ENTETES_PUBLICS, "Content-Disposition": 'attachment; filename="logo-goldunion.png"'})

    @routeur.get("/declarations", response_class=HTMLResponse)
    def liste(request: Request, message: str = "", db: Session = Depends(obtenir_session)):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        lignes = db.query(DeclarationPagesJaunesGoldUnion).order_by(func.lower(DeclarationPagesJaunesGoldUnion.ville), DeclarationPagesJaunesGoldUnion.cree_le.desc()).all()
        declarations = [{
            "id": d.id, "ville": d.ville, "email": d.email, "responsable": d.responsable, "quand": libelle_moment(d.cree_le),
            "attendue": adresse_attendue(d.email),
        } for d in lignes]
        return templates.TemplateResponse(request, "pagesjaunes_goldunion_liste.html", {
            "page_actuelle": "suivi_goldunion", "chemin": CHEMIN, "declarations": declarations, "message": message,
            "nb_agences": len({d["ville"].lower() for d in declarations}), "nb_perso": sum(1 for d in declarations if not d["attendue"]),
        }, headers=ENTETES_PRIVES)

    @routeur.post("/declarations/{declaration_id}/supprimer")
    def supprimer(declaration_id: int, request: Request, db: Session = Depends(obtenir_session)):
        if not utilisateur_connecte(request):
            return RedirectResponse("/connexion", status_code=303)
        db.query(DeclarationPagesJaunesGoldUnion).filter_by(id=declaration_id).delete()
        db.commit()
        return RedirectResponse(f"{CHEMIN}/declarations?message=Déclaration supprimée.", status_code=303, headers=ENTETES_PRIVES)

    return routeur
