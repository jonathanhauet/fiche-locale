"""Routes de la page « Compresseur photos et vidéos » (voir compression_media.py). Le routeur est cree par main.py, qui fournit la connexion et les gabarits."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import compression_media as cm

journal = logging.getLogger("compression_media")
ENTETES_PRIVES = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def creer_routeur(templates, utilisateur_connecte, rediriger_si_non_connecte) -> APIRouter:
    routeur = APIRouter(prefix="/compresseur-media")

    def refuse(request: Request):
        return None if utilisateur_connecte(request) else JSONResponse({"erreur": "Session expirée : reconnectez-vous."}, status_code=401, headers=ENTETES_PRIVES)

    async def executer(fonction, *args, **kwargs):
        """Traitement hors de la boucle d'evenements ; une erreur prevue est renvoyee telle quelle, une erreur imprevue en message generique."""
        try:
            return await run_in_threadpool(fonction, *args, **kwargs)
        except cm.ErreurMedia as erreur:
            return JSONResponse({"erreur": str(erreur)}, status_code=400, headers=ENTETES_PRIVES)
        except Exception:
            journal.exception("Erreur imprevue dans le compresseur")
            return JSONResponse({"erreur": "Le traitement a échoué : essayez avec un autre fichier."}, status_code=500, headers=ENTETES_PRIVES)

    def json_ok(valeur):
        return valeur if isinstance(valeur, Response) else JSONResponse(valeur, headers=ENTETES_PRIVES)

    @routeur.get("", response_class=HTMLResponse)
    def page(request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        return templates.TemplateResponse(request, "compresseur_media.html", {
            "page_actuelle": "compresseur_media", "formats_photo": {k: v[2] for k, v in cm.formats_photo_disponibles().items()},
            "formats_video": {k: v[2] for k, v in cm.formats_video_disponibles().items()}, "heic": cm.HEIC_POSSIBLE,
            "taille_max_photo": cm.TAILLE_MAX_PHOTO // 1048576, "taille_max_video": cm.TAILLE_MAX_VIDEO // 1048576, "max_fichiers": cm.MAX_FICHIERS,
            "duree_heures": cm.DUREE_VIE_SECONDES // 3600, "cotes": cm.COTES_PHOTO[1:], "hauteurs": cm.HAUTEURS_VIDEO[1:], "max_lot": cm.TAILLE_MAX_LOT // 1073741824,
        }, headers=ENTETES_PRIVES)

    @routeur.post("/lot")
    async def nouveau_lot(request: Request):
        if (r := refuse(request)):
            return r
        resultat = await executer(cm.creer_lot)
        return resultat if isinstance(resultat, Response) else JSONResponse({"id": resultat}, headers=ENTETES_PRIVES)

    @routeur.post("/{ident}/fichier")
    async def ajouter(ident: str, request: Request):
        """Recoit UN fichier (le navigateur les envoie un par un pour montrer l'avancement)."""
        if (r := refuse(request)):
            return r
        formulaire = await request.form()
        fichier = formulaire.get("fichier")
        if not hasattr(fichier, "read") or not getattr(fichier, "filename", ""):
            return JSONResponse({"erreur": "Aucun fichier reçu."}, status_code=400, headers=ENTETES_PRIVES)
        resultat = await executer(cm.ajouter_fichier, ident, fichier.filename, fichier.file)
        return json_ok(resultat)

    @routeur.post("/{ident}/retirer/{numero}")
    async def retirer(ident: str, numero: int, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(cm.retirer_fichier, ident, numero))

    @routeur.post("/{ident}/lancer")
    async def lancer(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        try:
            corps = await request.json()
        except ValueError:
            corps = {}
        return json_ok(await executer(cm.lancer, ident, corps))

    @routeur.post("/{ident}/annuler")
    async def annuler(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(cm.annuler, ident))

    @routeur.get("/{ident}/etat")
    async def etat(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(cm.etat, ident))

    @routeur.get("/{ident}/fichier/{numero}")
    async def telecharger(ident: str, numero: int, request: Request):
        if (r := refuse(request)):
            return r
        resultat = await executer(cm.chemin_sortie, ident, numero)
        if isinstance(resultat, Response):
            return resultat
        chemin, nom = resultat
        return FileResponse(chemin, filename=nom, headers=ENTETES_PRIVES)

    @routeur.get("/{ident}/zip")
    async def telecharger_zip(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        resultat = await executer(cm.archive_zip, ident)
        if isinstance(resultat, Response):
            return resultat
        chemin, nom = resultat
        return FileResponse(chemin, filename=nom, media_type="application/zip", headers=ENTETES_PRIVES)

    @routeur.get("/{ident}/miniature/{numero}.jpg")
    async def miniature(ident: str, numero: int, request: Request, source: int = 0):
        if (r := refuse(request)):
            return r
        resultat = await executer(cm.miniature, ident, numero, 160, not source)
        return resultat if isinstance(resultat, Response) else Response(resultat, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600", "X-Robots-Tag": "noindex"})

    @routeur.post("/{ident}/fermer")
    async def fermer(ident: str, request: Request):
        if (r := refuse(request)):
            return r
        return json_ok(await executer(lambda: (cm.fermer(ident), {"ok": True})[1]))

    return routeur
