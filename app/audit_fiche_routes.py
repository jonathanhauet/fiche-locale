"""Routes de l'audit complet de fiche (voir audit_fiche.py) : liste et creation (connecte), resultat, PDF, lien public a partager avec le prospect."""

import json
import logging
import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import audit_fiche, audit_fiche_pdf, audit_prospect, models

journal = logging.getLogger("audit_fiche")
ENTETES_PRIVES = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}
CHAMPS_FICHE_TEXTE = ("titre", "adresse", "telephone", "categorie", "site_web", "lien_rdv", "lien_contact", "photo_principale", "domaine", "place_id", "ville_fiche")


def nettoyer_fiche(brut) -> dict:
    """La fiche choisie revient du navigateur : on ne garde que les champs attendus, avec leur type (rien n'est utilise tel quel)."""
    if not isinstance(brut, dict):
        raise ValueError("Fiche invalide.")
    fiche = {"trouve": True}
    for cle in CHAMPS_FICHE_TEXTE:
        valeur = brut.get(cle)
        fiche[cle] = str(valeur)[:300] if isinstance(valeur, (str, int, float)) else ""
    for cle in ("note",):
        v = brut.get(cle)
        fiche[cle] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 5 else None
    for cle in ("nombre_avis", "total_photos", "jours_ouverts"):
        v = brut.get(cle)
        fiche[cle] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10_000_000 else None
    for cle in ("latitude", "longitude"):
        v = brut.get(cle)
        fiche[cle] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and -180 <= v <= 180 else None
    for cle in ("a_horaires", "a_categorie_secondaire"):
        fiche[cle] = bool(brut.get(cle))
    revendiquee = brut.get("est_revendiquee")
    fiche["est_revendiquee"] = revendiquee if isinstance(revendiquee, bool) else None
    secondaires = brut.get("categories_secondaires")
    fiche["categories_secondaires"] = [str(c)[:80] for c in secondaires[:15] if isinstance(c, str)] if isinstance(secondaires, list) else []
    repartition = brut.get("repartition_avis")
    fiche["repartition_avis"] = {str(k): int(v) for k, v in repartition.items() if str(k) in "12345" and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0} if isinstance(repartition, dict) else {}
    if not fiche["titre"]:
        raise ValueError("Fiche sans nom.")
    return fiche


def creer_routeur(templates, utilisateur_connecte, rediriger_si_non_connecte, session_factory) -> APIRouter:
    routeur = APIRouter()

    def refuse(request: Request):
        return None if utilisateur_connecte(request) else JSONResponse({"erreur": "Session expirée : reconnectez-vous."}, status_code=401, headers=ENTETES_PRIVES)

    async def executer(fonction, *args):
        try:
            return await run_in_threadpool(fonction, *args)
        except ValueError as erreur:
            return JSONResponse({"erreur": str(erreur)}, status_code=400, headers=ENTETES_PRIVES)
        except RuntimeError as erreur:
            return JSONResponse({"erreur": str(erreur)[:300]}, status_code=400, headers=ENTETES_PRIVES)
        except Exception:
            journal.exception("Erreur imprevue dans l'audit de fiche")
            return JSONResponse({"erreur": "Le traitement a échoué : réessayez."}, status_code=500, headers=ENTETES_PRIVES)

    def contexte_audit(audit) -> dict:
        resultat = audit_fiche.charger_json(audit.resultat_json, {})
        donnees = audit_fiche.charger_json(audit.donnees_json, {})
        return {"audit": audit, "resultat": resultat, "donnees": donnees, "fiche": donnees.get("fiche") or {}, "af": audit_fiche, "date_audit": audit.cree_le.strftime("%d/%m/%Y") if audit.cree_le else ""}

    def lire(audit_id: int = None, token: str = None):
        db = session_factory()
        try:
            audit = db.get(models.AuditFiche, audit_id) if audit_id is not None else db.query(models.AuditFiche).filter_by(token=token).first()
            if audit:
                audit_fiche.verifier_abandon(db, audit)
                db.refresh(audit)
                db.expunge(audit)
            return audit
        finally:
            db.close()

    # ---------- espace connecte

    @routeur.get("/audits", response_class=HTMLResponse)
    def liste(request: Request, nom: str = "", ville: str = "", lead_id: int = None):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        db = session_factory()
        try:
            audits = db.query(models.AuditFiche).order_by(models.AuditFiche.cree_le.desc()).limit(200).all()
            for a in audits:
                audit_fiche.verifier_abandon(db, a)
            lignes = [{"id": a.id, "nom": a.nom, "ville": a.ville, "statut": a.statut, "score": a.score, "date": a.cree_le.strftime("%d/%m/%Y %H:%M") if a.cree_le else "", "etiquette": audit_fiche.etiquette_score(a.score)} for a in audits]
        finally:
            db.close()
        return templates.TemplateResponse(request, "audits.html", {"page_actuelle": "audits", "audits": lignes, "nom": nom[:120], "ville": ville[:80], "lead_id": lead_id}, headers=ENTETES_PRIVES)

    @routeur.post("/audits/rechercher")
    async def rechercher(request: Request):
        """Fiches Google candidates pour un nom et une ville (une requete DataForSEO) : on confirme la bonne avant de lancer l'analyse."""
        if (r := refuse(request)):
            return r
        try:
            corps = await request.json()
            nom, ville = str(corps.get("nom", "")).strip()[:120], str(corps.get("ville", "")).strip()[:80]
        except (ValueError, AttributeError):
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        if not nom or not ville:
            return JSONResponse({"erreur": "Indiquez le nom de l'entreprise et la ville."}, status_code=400)
        resultat = await executer(audit_prospect.rechercher_candidats_fiche, nom, ville)
        if isinstance(resultat, Response):
            return resultat
        return JSONResponse({"candidats": resultat, "mots_cles": {i: audit_fiche.mots_cles_par_defaut(c, ville) for i, c in enumerate(resultat)}}, headers=ENTETES_PRIVES)

    @routeur.post("/audits/lancer")
    async def lancer(request: Request):
        if (r := refuse(request)):
            return r
        try:
            corps = await request.json()
            fiche = nettoyer_fiche(corps.get("fiche"))
            ville = str(corps.get("ville", "")).strip()[:80]
        except (ValueError, AttributeError) as erreur:
            return JSONResponse({"erreur": str(erreur) or "Requête invalide."}, status_code=400)
        if not ville:
            return JSONResponse({"erreur": "Indiquez la ville."}, status_code=400)
        mots = [str(m).strip()[:80] for m in (corps.get("mots_cles") or [])[:2] if str(m).strip()] if isinstance(corps.get("mots_cles"), list) else []
        taille = corps.get("taille_grille") if corps.get("taille_grille") in (3, 5) else 3
        lead_id = corps.get("lead_id") if isinstance(corps.get("lead_id"), int) else None
        entree = {"fiche": fiche, "ville": ville, "mots_cles": mots or audit_fiche.mots_cles_par_defaut(fiche, ville), "taille_grille": taille, "ia": bool(corps.get("ia", True))}

        def creer_et_lancer():
            db = session_factory()
            try:
                audit = audit_fiche.creer(db, fiche["titre"], ville, entree, lead_id)
                ident = audit.id
            finally:
                db.close()
            audit_fiche.lancer(session_factory, ident)
            return {"id": ident}

        resultat = await executer(creer_et_lancer)
        return resultat if isinstance(resultat, Response) else JSONResponse(resultat, headers=ENTETES_PRIVES)

    @routeur.get("/audits/{audit_id}", response_class=HTMLResponse)
    def voir(audit_id: int, request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        audit = lire(audit_id)
        if not audit:
            return HTMLResponse("Audit introuvable.", status_code=404)
        lien = f"{request.headers.get('x-forwarded-proto') or request.url.scheme}://{request.headers.get('host') or request.url.netloc}/audit/{audit.token}"
        return templates.TemplateResponse(request, "audit_fiche.html", {"page_actuelle": "audits", "public": False, "lien_public": lien, **contexte_audit(audit)}, headers=ENTETES_PRIVES)

    @routeur.get("/audits/{audit_id}/etat")
    def etat(audit_id: int, request: Request):
        if (r := refuse(request)):
            return r
        audit = lire(audit_id)
        if not audit:
            return JSONResponse({"erreur": "Audit introuvable."}, status_code=404)
        return JSONResponse({"statut": audit.statut, "progression": audit.progression or 0, "message": audit.message or "", "score": audit.score}, headers=ENTETES_PRIVES)

    @routeur.get("/audits/{audit_id}/pdf")
    def pdf(audit_id: int, request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        audit = lire(audit_id)
        return _reponse_pdf(audit)

    def _reponse_pdf(audit):
        if not audit or audit.statut != "PRET":
            return HTMLResponse("Audit indisponible.", status_code=404)
        contexte = contexte_audit(audit)
        try:
            octets = audit_fiche_pdf.generer_audit_fiche_pdf(audit.nom, audit.ville, contexte["donnees"], contexte["resultat"])
        except Exception:
            journal.exception("PDF de l'audit")
            return HTMLResponse("Le PDF n'a pas pu être généré.", status_code=500)
        nom_fichier = "audit_" + re.sub(r"[^a-z0-9]+", "_", audit.nom.lower())[:50].strip("_") + ".pdf"
        return Response(octets, media_type="application/pdf", headers={**ENTETES_PRIVES, "Content-Disposition": f'attachment; filename="{nom_fichier}"'})

    @routeur.post("/audits/{audit_id}/relancer")
    async def relancer(audit_id: int, request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection

        def preparer():
            db = session_factory()
            try:
                audit = db.get(models.AuditFiche, audit_id)
                if not audit or audit.statut == "EN_COURS":
                    return False
                audit.statut, audit.message, audit.progression, audit.score, audit.maj_le = "EN_COURS", "En attente…", 0, None, datetime.utcnow()
                audit.donnees_json, audit.resultat_json = "", ""
                db.commit()
                return True
            finally:
                db.close()

        if await run_in_threadpool(preparer):
            audit_fiche.lancer(session_factory, audit_id)
        return RedirectResponse(f"/audits/{audit_id}", status_code=303)

    @routeur.post("/audits/{audit_id}/partage")
    async def partage(audit_id: int, request: Request):
        """Active, coupe ou renouvelle le lien public (un nouveau lien invalide l'ancien)."""
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        formulaire = await request.form()
        action = str(formulaire.get("action", ""))

        def modifier():
            db = session_factory()
            try:
                audit = db.get(models.AuditFiche, audit_id)
                if audit:
                    if action == "couper":
                        audit.partage_actif = False
                    elif action == "activer":
                        audit.partage_actif = True
                    elif action == "renouveler":
                        audit.token, audit.partage_actif = secrets.token_urlsafe(18), True
                    db.commit()
            finally:
                db.close()

        await run_in_threadpool(modifier)
        return RedirectResponse(f"/audits/{audit_id}", status_code=303)

    @routeur.post("/audits/{audit_id}/supprimer")
    async def supprimer(audit_id: int, request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection

        def effacer():
            db = session_factory()
            try:
                audit = db.get(models.AuditFiche, audit_id)
                if audit:
                    db.delete(audit)
                    db.commit()
            finally:
                db.close()

        await run_in_threadpool(effacer)
        return RedirectResponse("/audits", status_code=303)

    # ---------- page publique (lien secret, lecture seule)

    @routeur.get("/audit/{token}", response_class=HTMLResponse)
    def publique(token: str, request: Request):
        audit = lire(token=token) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or "") else None
        if not audit or not audit.partage_actif:
            return HTMLResponse("Page introuvable.", status_code=404, headers=ENTETES_PRIVES)
        return templates.TemplateResponse(request, "audit_fiche_public.html", {"public": True, "lien_public": "", **contexte_audit(audit)}, headers=ENTETES_PRIVES)

    @routeur.get("/audit/{token}/pdf")
    def publique_pdf(token: str):
        audit = lire(token=token) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or "") else None
        if not audit or not audit.partage_actif:
            return HTMLResponse("Page introuvable.", status_code=404, headers=ENTETES_PRIVES)
        return _reponse_pdf(audit)

    @routeur.get("/audit/{token}/etat")
    def publique_etat(token: str):
        audit = lire(token=token) if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or "") else None
        if not audit or not audit.partage_actif:
            return JSONResponse({"erreur": "Introuvable."}, status_code=404, headers=ENTETES_PRIVES)
        return JSONResponse({"statut": audit.statut, "progression": audit.progression or 0, "message": audit.message or ""}, headers=ENTETES_PRIVES)

    return routeur
