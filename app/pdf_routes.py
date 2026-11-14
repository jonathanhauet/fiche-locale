"""Routes de la page « Outils PDF » (voir pdf_outils.py). Le routeur est cree par main.py, qui fournit la connexion et les gabarits."""

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import pdf_outils as po

journal = logging.getLogger("outils_pdf")
ENTETES_IMAGE = {"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"}
ENTETES_PRIVES = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
EXTENSIONS_IMAGES = (".jpg", ".jpeg", ".png")


def creer_routeur(templates, utilisateur_connecte, rediriger_si_non_connecte) -> APIRouter:
    routeur = APIRouter(prefix="/outils-pdf")

    def refuse(request: Request):
        return None if utilisateur_connecte(request) else JSONResponse({"erreur": "Session expirée : reconnectez-vous."}, status_code=401, headers=ENTETES_PRIVES)

    async def executer(fonction, *args, **kwargs):
        """Lance un traitement PDF hors de la boucle d'evenements ; une erreur prevue est renvoyee telle quelle, une erreur imprevue en message generique."""
        try:
            return await run_in_threadpool(fonction, *args, **kwargs)
        except po.ErreurPDF as erreur:
            return JSONResponse({"erreur": str(erreur)}, status_code=400, headers=ENTETES_PRIVES)
        except Exception:
            journal.exception("Erreur imprevue dans les outils PDF")
            return JSONResponse({"erreur": "Le traitement du PDF a échoué : essayez avec un autre fichier."}, status_code=500, headers=ENTETES_PRIVES)

    def json_ok(valeur):
        return valeur if isinstance(valeur, Response) else JSONResponse(valeur, headers=ENTETES_PRIVES)

    async def lire(fichier: UploadFile) -> bytes:
        octets = await fichier.read(po.TAILLE_MAX_OCTETS + 1)
        if len(octets) > po.TAILLE_MAX_OCTETS:
            raise po.ErreurPDF(f"« {fichier.filename} » est trop lourd (maximum {po.TAILLE_MAX_OCTETS // 1048576} Mo).")
        return octets

    @routeur.get("", response_class=HTMLResponse)
    def page(request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        return templates.TemplateResponse(request, "outils_pdf.html", {
            "page_actuelle": "outils_pdf", "polices": po.LIBELLES_POLICES, "niveaux": list(po.NIVEAUX_COMPRESSION), "taille_max": po.TAILLE_MAX_OCTETS // 1048576,
            "pages_max": po.PAGES_MAX, "duree_heures": po.DUREE_VIE_SECONDES // 3600,
        }, headers=ENTETES_PRIVES)

    @routeur.post("/ouvrir")
    async def ouvrir(request: Request):
        """Ouvre un ou plusieurs PDF (fusionnes dans l'ordre), ou des images (une page par image)."""
        if (r := refuse(request)):
            return r
        formulaire = await request.form()
        fichiers = [f for f in formulaire.getlist("fichiers") if hasattr(f, "filename") and f.filename]
        mot_de_passe = str(formulaire.get("mot_de_passe", ""))
        if not fichiers:
            return JSONResponse({"erreur": "Aucun fichier."}, status_code=400)
        if len(fichiers) > 40:
            return JSONResponse({"erreur": "Trop de fichiers à la fois (maximum 40)."}, status_code=400)
        images = [f for f in fichiers if f.filename.lower().endswith(EXTENSIONS_IMAGES)]
        if images and len(images) != len(fichiers):
            return JSONResponse({"erreur": "Choisissez des PDF ou des images, pas les deux à la fois."}, status_code=400)

        def ouvrir_tout(contenus):
            if images:
                return po.session_depuis_images([(f.filename, c) for f, c in contenus])
            ident = po.creer_session(contenus[0][1], contenus[0][0].filename, mot_de_passe)
            try:
                for f, c in contenus[1:]:
                    po.appliquer(ident, "fusionner", {"mot_de_passe_autre": mot_de_passe}, c)
            except Exception:
                po.fermer(ident)
                raise
            return ident

        try:
            contenus = [(f, await lire(f)) for f in fichiers]
        except po.ErreurPDF as erreur:
            return JSONResponse({"erreur": str(erreur)}, status_code=400)
        resultat = await executer(ouvrir_tout, contenus)
        if isinstance(resultat, Response):
            return resultat
        return json_ok(await executer(po.etat, resultat))

    @routeur.get("/{ident}/etat")
    async def etat(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(po.etat, ident))

    @routeur.get("/{ident}/page/{numero}.jpg")
    async def page_image(ident: str, numero: int, request: Request, zoom: float = 1.5, v: int = None):
        if (r := refuse(request)):
            return r
        octets = await executer(po.rendre_page, ident, numero, zoom, v)
        return octets if isinstance(octets, Response) else Response(octets, media_type="image/jpeg", headers=ENTETES_IMAGE)

    @routeur.get("/{ident}/vignette/{numero}.jpg")
    async def vignette(ident: str, numero: int, request: Request, v: int = None):
        if (r := refuse(request)):
            return r
        octets = await executer(po.rendre_page, ident, numero, 0.32, v)
        return octets if isinstance(octets, Response) else Response(octets, media_type="image/jpeg", headers=ENTETES_IMAGE)

    @routeur.get("/{ident}/texte/{numero}")
    async def texte(ident: str, numero: int, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(po.lignes_texte, ident, numero))

    @routeur.post("/{ident}/action")
    async def action(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        try:
            corps = await request.json()
        except ValueError:
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        if not isinstance(corps, dict) or not isinstance(corps.get("op"), str):
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        operation = corps.pop("op")
        if operation in po.OPERATIONS_AVEC_FICHIER:
            return JSONResponse({"erreur": "Cette opération demande un fichier."}, status_code=400)
        return json_ok(await executer(po.appliquer, ident, operation, corps))

    @routeur.post("/{ident}/action-fichier")
    async def action_fichier(ident: str, request: Request):
        """Operations avec un fichier : image a placer sur une page, PDF a ajouter."""
        if (r := refuse(request)):
            return r
        formulaire = await request.form()
        fichier = formulaire.get("fichier")
        operation = str(formulaire.get("op", ""))
        if not hasattr(fichier, "read") or operation not in po.OPERATIONS_AVEC_FICHIER:
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        try:
            params = json.loads(str(formulaire.get("params") or "{}"))
            octets = await lire(fichier)
        except po.ErreurPDF as erreur:
            return JSONResponse({"erreur": str(erreur)}, status_code=400)
        except ValueError:
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        if not isinstance(params, dict):
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        return json_ok(await executer(po.appliquer, ident, operation, params, octets))

    @routeur.post("/{ident}/annuler")
    async def annuler(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(po.annuler, ident))

    @routeur.post("/{ident}/telecharger")
    async def telecharger(ident: str, request: Request):
        """Export : PDF (entier ou pages choisies, protege ou non), images en ZIP, ou decoupe en plusieurs PDF. Les numeros de pages recus commencent a 1."""
        if (r := refuse(request)):
            return r
        try:
            corps = await request.json()
            if not isinstance(corps, dict):
                raise ValueError
        except ValueError:
            return JSONResponse({"erreur": "Requête invalide."}, status_code=400)
        mode = corps.get("mode", "pdf")
        pages = corps.get("pages")
        if pages is not None:
            if not isinstance(pages, list) or any(not isinstance(i, int) for i in pages):
                return JSONResponse({"erreur": "Pages invalides."}, status_code=400)
            pages = [i - 1 for i in pages]

        def produire():
            if mode == "pdf":
                return po.exporter_pdf(ident, pages or None, str(corps.get("mot_de_passe") or ""))
            if mode == "images":
                return po.exporter_images(ident, pages or None, str(corps.get("format", "png")), int(corps.get("dpi") or 150))
            if mode == "decoupe":
                return po.decouper(ident, str(corps.get("plages", "")))
            raise po.ErreurPDF("Export inconnu.")

        resultat = await executer(produire)
        if isinstance(resultat, Response):
            return resultat
        octets, nom = resultat
        type_mime = "application/pdf" if nom.endswith(".pdf") else "application/zip"
        return Response(octets, media_type=type_mime, headers={**ENTETES_PRIVES, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nom)}", "X-Nom-Fichier": quote(nom)})

    @routeur.post("/{ident}/fermer")
    async def fermer(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(lambda: (po.fermer(_valide(ident)), {"ok": True})[1]))

    def _valide(ident: str) -> str:
        return po._verifier_id(ident)

    return routeur
