"""Page « Pipette a couleurs » : tout se passe dans le navigateur (aucune donnee envoyee au serveur)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


def creer_routeur(templates, rediriger_si_non_connecte) -> APIRouter:
    routeur = APIRouter()

    @routeur.get("/pipette", response_class=HTMLResponse)
    def page(request: Request):
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection
        return templates.TemplateResponse(request, "pipette_couleur.html", {"page_actuelle": "pipette"}, headers={"Cache-Control": "no-store"})

    return routeur
