"""
Point d'entree de la plateforme web (FastAPI).

Lancement en local : depuis le dossier plateforme_web/,
    uvicorn app.main:app --reload
puis ouvrir http://localhost:8000
"""

import calendar
import json
import os
import uuid
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import (
    brevo_email,
    claude_generation,
    documents,
    gemini_images,
    geocodage,
    google_business,
    google_location,
    google_oauth,
    google_performance,
    google_place_actions,
    google_publish,
    google_reviews,
    models,
    ovh_upload,
    rank_tracking,
    rapport_donnees,
    rapport_pdf,
    recap_mensuel,
)
from .database import Base, engine, obtenir_session
from .planificateur import (
    envoyer_recaps_mensuels,
    verifier_et_publier_photos_programmees,
    verifier_et_publier_posts_programmes,
)
from .security import verifier_mot_de_passe

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

Base.metadata.create_all(bind=engine)


def _migrer_vers_multi_comptes():
    """
    Migration legere pour les bases SQLite existantes (creees avant le support
    multi-comptes) : ajoute la colonne clients.compte_google_id si absente, et
    bascule l'ancienne table config_google (un seul compte) vers comptes_google.
    Base.metadata.create_all() cree les nouvelles tables mais n'altere jamais
    les tables existantes, d'ou ce complement fait a la main.
    """
    inspecteur = inspect(engine)
    if "clients" not in inspecteur.get_table_names():
        return  # base toute neuve : deja creee avec le bon schema, rien a faire

    colonnes_clients = [c["name"] for c in inspecteur.get_columns("clients")]

    with engine.begin() as connexion:
        if "compte_google_id" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN compte_google_id INTEGER"))
        if "consignes_avis" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN consignes_avis TEXT DEFAULT ''"))
        if "latitude" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN latitude REAL"))
        if "longitude" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN longitude REAL"))
        if "email" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN email TEXT DEFAULT ''"))

        if "photos_fiche" in inspecteur.get_table_names():
            colonnes_photos = [c["name"] for c in inspecteur.get_columns("photos_fiche")]
            if "legende" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN legende TEXT DEFAULT ''"))
            if "latitude" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN latitude REAL"))
            if "longitude" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN longitude REAL"))

        if "posts" in inspecteur.get_table_names():
            colonnes_posts = [c["name"] for c in inspecteur.get_columns("posts")]
            if "lot_id" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN lot_id TEXT"))
            if "heure_prevue" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN heure_prevue TEXT"))
            if "type_appel_action" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN type_appel_action TEXT DEFAULT ''"))
            if "url_appel_action" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN url_appel_action TEXT DEFAULT ''"))
            if "type_post" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN type_post TEXT DEFAULT 'STANDARD'"))
            if "evenement_titre" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN evenement_titre TEXT DEFAULT ''"))
            if "evenement_date_debut" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN evenement_date_debut DATE"))
            if "evenement_heure_debut" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN evenement_heure_debut TEXT"))
            if "evenement_date_fin" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN evenement_date_fin DATE"))
            if "evenement_heure_fin" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN evenement_heure_fin TEXT"))
            if "offre_code" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN offre_code TEXT DEFAULT ''"))
            if "offre_url" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN offre_url TEXT DEFAULT ''"))
            if "offre_conditions" not in colonnes_posts:
                connexion.execute(text("ALTER TABLE posts ADD COLUMN offre_conditions TEXT DEFAULT ''"))

        if "points_grille" in inspecteur.get_table_names():
            colonnes_points_grille = [c["name"] for c in inspecteur.get_columns("points_grille")]
            if "resultats_json" not in colonnes_points_grille:
                connexion.execute(text("ALTER TABLE points_grille ADD COLUMN resultats_json TEXT DEFAULT ''"))

        if "config_google" in inspecteur.get_table_names():
            ancien = connexion.execute(text("SELECT refresh_token FROM config_google LIMIT 1")).fetchone()
            if ancien and ancien[0]:
                compte_existant = connexion.execute(
                    text("SELECT id FROM comptes_google WHERE refresh_token = :rt"), {"rt": ancien[0]}
                ).fetchone()
                if compte_existant:
                    id_compte_migre = compte_existant[0]
                else:
                    resultat = connexion.execute(
                        text(
                            "INSERT INTO comptes_google (libelle, refresh_token, cree_le) "
                            "VALUES (:l, :rt, :d)"
                        ),
                        {"l": google_oauth.LIBELLE_COMPTE_MIGRE, "rt": ancien[0], "d": datetime.utcnow()},
                    )
                    id_compte_migre = resultat.lastrowid

                connexion.execute(
                    text("UPDATE clients SET compte_google_id = :id WHERE compte_google_id IS NULL"),
                    {"id": id_compte_migre},
                )
            connexion.execute(text("DROP TABLE config_google"))


_migrer_vers_multi_comptes()

app = FastAPI(title="Fiche Locale - Plateforme")

# Derriere un proxy inverse (Railway, Heroku...), la requete arrive en HTTP
# en interne meme si le visiteur est en HTTPS : sans ce middleware,
# request.url_for() (utilise pour construire l'URL de callback OAuth Google)
# genererait une URL en http:// qui ne correspondrait a aucune URI de
# redirection autorisee cote Google (redirect_uri_mismatch). On se base sur
# l'en-tete X-Forwarded-Proto envoye par le proxy pour connaitre le vrai
# protocole. Configure ici (plutot qu'en option de ligne de commande uvicorn)
# pour ne pas dependre de la facon dont la plateforme d'hebergement decoupe
# la commande du Procfile.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

CLE_SESSION = os.getenv("SECRET_KEY")
if not CLE_SESSION:
    raise RuntimeError(
        "SECRET_KEY manquant dans plateforme_web/.env. "
        "Ajoutez une longue chaine aleatoire (voir .env.example)."
    )
app.add_middleware(SessionMiddleware, secret_key=CLE_SESSION)

app.mount("/static", StaticFiles(directory=os.path.join(DOSSIER_APP, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(DOSSIER_APP, "templates"))

# Casse le cache navigateur du CSS a chaque modification du fichier (evite de
# servir une feuille de style perimee apres une mise a jour de la plateforme).
templates.env.globals["version_css"] = int(
    os.path.getmtime(os.path.join(DOSSIER_APP, "static", "style.css"))
)

# Tache de fond : publie automatiquement les posts programmes dont la date
# est arrivee. Remplace la tache planifiee Windows des scripts en ligne de
# commande - tourne tant que ce processus est actif.
INTERVALLE_PLANIFICATEUR_MINUTES = int(os.getenv("PLANIFICATEUR_INTERVALLE_MINUTES", "15"))
planificateur = BackgroundScheduler()
planificateur.add_job(
    verifier_et_publier_posts_programmes,
    "interval",
    minutes=INTERVALLE_PLANIFICATEUR_MINUTES,
    id="publication_programmee",
)
planificateur.add_job(
    verifier_et_publier_photos_programmees,
    "interval",
    minutes=INTERVALLE_PLANIFICATEUR_MINUTES,
    id="publication_photos_programmee",
)
# Le recap mensuel n'a pas besoin d'un rythme aussi rapide : une verification
# quotidienne suffit (job idempotent via EnvoiRecap, cf. planificateur.py).
planificateur.add_job(
    envoyer_recaps_mensuels,
    "interval",
    hours=24,
    id="recap_mensuel",
)
planificateur.start()


def utilisateur_connecte(request: Request):
    return request.session.get("user_id")


def rediriger_si_non_connecte(request: Request):
    if not utilisateur_connecte(request):
        return RedirectResponse("/connexion")
    return None


# --- Connexion / deconnexion ---------------------------------------------


@app.get("/connexion", response_class=HTMLResponse)
def page_connexion(request: Request):
    if utilisateur_connecte(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html", {"erreur": None})


@app.post("/connexion")
def connexion(
    request: Request,
    identifiant: str = Form(...),
    mot_de_passe: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    utilisateur = db.query(models.Utilisateur).filter_by(identifiant=identifiant).first()
    if not utilisateur or not verifier_mot_de_passe(mot_de_passe, utilisateur.mot_de_passe_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"erreur": "Identifiant ou mot de passe incorrect."},
            status_code=401,
        )
    request.session["user_id"] = utilisateur.id
    return RedirectResponse("/", status_code=303)


@app.get("/deconnexion")
def deconnexion(request: Request):
    request.session.clear()
    return RedirectResponse("/connexion", status_code=303)


# --- Historique des publications --------------------------------------------


@app.get("/historique", response_class=HTMLResponse)
def historique(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    evenements = (
        db.query(models.EvenementPublication)
        .join(models.Post)
        .join(models.Client)
        .order_by(models.EvenementPublication.horodatage.desc())
        .all()
    )

    return templates.TemplateResponse(request, "historique.html", {"evenements": evenements})


@app.get("/clients/{client_id}/historique", response_class=HTMLResponse)
def historique_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    evenements = (
        db.query(models.EvenementPublication)
        .join(models.Post)
        .filter(models.Post.client_id == client_id)
        .order_by(models.EvenementPublication.horodatage.desc())
        .all()
    )

    posts_en_ligne = []
    erreur_posts_en_ligne = None
    if client.account_id and client.location_id:
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if identifiants:
            try:
                posts_en_ligne = google_business.lister_posts(identifiants, client.account_id, client.location_id)
            except Exception as erreur:
                erreur_posts_en_ligne = str(erreur)
        else:
            erreur_posts_en_ligne = "Compte Google non valide pour ce client (a reconnecter depuis Comptes Google)."

    return templates.TemplateResponse(
        request,
        "client_historique.html",
        {
            "client": client,
            "evenements": evenements,
            "posts_en_ligne": posts_en_ligne,
            "erreur_posts_en_ligne": erreur_posts_en_ligne,
        },
    )


LIBELLES_MOIS = {
    1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril", 5: "Mai", 6: "Juin",
    7: "Juillet", 8: "Août", 9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
}


def _donnees_calendrier(request: Request, db: Session, client: models.Client) -> dict:
    """Grille du mois (calendrier de contenu, affiche directement sur la fiche client)."""
    aujourdhui = date.today()
    try:
        annee = int(request.query_params.get("annee", aujourdhui.year))
        mois = int(request.query_params.get("mois", aujourdhui.month))
        date(annee, mois, 1)  # valide que annee/mois forment bien une date
    except (ValueError, TypeError):
        annee, mois = aujourdhui.year, aujourdhui.month

    semaines = calendar.Calendar(firstweekday=0).monthdatescalendar(annee, mois)
    premier_jour_grille = semaines[0][0]
    dernier_jour_grille = semaines[-1][-1]

    posts = (
        db.query(models.Post)
        .filter(
            models.Post.client_id == client.id,
            models.Post.date_prevue >= premier_jour_grille,
            models.Post.date_prevue <= dernier_jour_grille,
            models.Post.statut != "SUPPRIME",
        )
        .all()
    )
    photos = (
        db.query(models.PhotoFiche)
        .filter(
            models.PhotoFiche.client_id == client.id,
            models.PhotoFiche.date_prevue >= premier_jour_grille,
            models.PhotoFiche.date_prevue <= dernier_jour_grille,
            models.PhotoFiche.statut != "SUPPRIME",
        )
        .all()
    )

    evenements_par_jour = {}
    for post in posts:
        evenements_par_jour.setdefault(post.date_prevue, {"posts": [], "photos": []})["posts"].append(post)
    for photo in photos:
        evenements_par_jour.setdefault(photo.date_prevue, {"posts": [], "photos": []})["photos"].append(photo)

    mois_precedent = mois - 1 if mois > 1 else 12
    annee_mois_precedent = annee if mois > 1 else annee - 1
    mois_suivant = mois + 1 if mois < 12 else 1
    annee_mois_suivant = annee if mois < 12 else annee + 1

    return {
        "annee": annee,
        "mois": mois,
        "libelle_mois": LIBELLES_MOIS[mois],
        "semaines": semaines,
        "evenements_par_jour": evenements_par_jour,
        "aujourdhui": aujourdhui,
        "annee_mois_precedent": annee_mois_precedent,
        "mois_precedent": mois_precedent,
        "annee_mois_suivant": annee_mois_suivant,
        "mois_suivant": mois_suivant,
    }


# --- Gestion des avis --------------------------------------------------------
#
# La page /avis charge la liste des clients instantanement (aucun appel Google),
# puis le navigateur recupere les avis client par client via /avis/donnees/{id}
# pour pouvoir afficher une progression reelle et ne pas bloquer toute la page
# si le compte Google d'un seul client pose probleme.


@app.get("/avis", response_class=HTMLResponse)
def liste_avis(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    google_connecte = google_oauth.google_est_connecte(db)
    clients = []
    if google_connecte:
        clients = (
            db.query(models.Client)
            .filter(models.Client.account_id != "", models.Client.location_id != "")
            .order_by(models.Client.nom)
            .all()
        )

    clients_json = json.dumps([
        {"id": c.id, "nom": c.nom, "etiquettes": [e.id for e in c.etiquettes]} for c in clients
    ]).replace("</", "<\\/")

    etiquettes_disponibles = sorted(
        {etiquette for c in clients for etiquette in c.etiquettes}, key=lambda e: e.nom
    )

    return templates.TemplateResponse(
        request,
        "avis.html",
        {
            "clients": clients,
            "clients_json": clients_json,
            "etiquettes_disponibles": etiquettes_disponibles,
            "google_connecte": google_connecte,
        },
    )


@app.get("/avis/donnees/{client_id}")
def avis_donnees_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Renvoie en JSON les avis d'un seul client, pour le chargement progressif cote navigateur."""
    if not utilisateur_connecte(request):
        return JSONResponse({"avis": [], "erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client or not client.account_id or not client.location_id:
        return JSONResponse({"avis": [], "erreur": None})

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return JSONResponse({
            "avis": [],
            "erreur": f"{client.nom} : compte Google non valide (a reconnecter depuis Comptes Google).",
        })

    try:
        avis = google_reviews.lister_avis_multi_clients({client.id: identifiants}, [client])
        return JSONResponse({"avis": avis, "erreur": None})
    except Exception as erreur:
        return JSONResponse({"avis": [], "erreur": f"{client.nom} : {erreur}"})


@app.post("/avis/suggerer")
def suggerer_reponse_avis_route(
    request: Request,
    account_id: str = Form(...),
    location_id: str = Form(...),
    review_id: str = Form(...),
    commentaire: str = Form(""),
    note: int = Form(0),
    auteur: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.query(models.Client).filter_by(account_id=account_id, location_id=location_id).first()
    contenu_site = _contexte_ia_client(client) if client else ""
    consignes_avis = client.consignes_avis if client else ""

    try:
        texte_suggere = claude_generation.suggerer_reponse_avis(commentaire, note, contenu_site, consignes_avis, auteur)
    except Exception as erreur_ia:
        return JSONResponse({"erreur": f"Erreur lors de la suggestion : {erreur_ia}"}, status_code=500)

    return JSONResponse({"texte_suggere": texte_suggere})


@app.post("/avis/repondre")
def repondre_avis_route(
    request: Request,
    account_id: str = Form(...),
    location_id: str = Form(...),
    review_id: str = Form(...),
    texte_reponse: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.query(models.Client).filter_by(account_id=account_id, location_id=location_id).first()
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id if client else None)
    if not identifiants:
        return JSONResponse({"erreur": "Compte Google non valide pour ce client."}, status_code=400)

    try:
        reponse = google_reviews.repondre_avis(identifiants, account_id, location_id, review_id, texte_reponse)
    except Exception as erreur:
        return JSONResponse({"erreur": f"Erreur lors de l'envoi de la reponse : {erreur}"}, status_code=500)

    return JSONResponse({
        "reponse": reponse.get("comment", texte_reponse),
        "date_reponse": reponse.get("updateTime", ""),
    })


@app.post("/avis/supprimer_reponse")
def supprimer_reponse_avis_route(
    request: Request,
    account_id: str = Form(...),
    location_id: str = Form(...),
    review_id: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.query(models.Client).filter_by(account_id=account_id, location_id=location_id).first()
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id if client else None)
    if not identifiants:
        return JSONResponse({"erreur": "Compte Google non valide pour ce client."}, status_code=400)

    try:
        google_reviews.supprimer_reponse_avis(identifiants, account_id, location_id, review_id)
    except Exception as erreur:
        return JSONResponse({"erreur": f"Erreur lors de la suppression : {erreur}"}, status_code=500)

    return JSONResponse({"ok": True})


# --- Posts multi-fiches (publier/programmer un meme post sur plusieurs clients) --


HEURES_PREREGLEES = {"0830": "08:30", "1230": "12:30", "1830": "18:30"}


def _heure_depuis_formulaire(source, defaut: str = "08:30") -> str:
    """
    Lit le mode d'heure choisi (une des 3 heures prereglees, ou 'personnalise'
    avec heure_h/heure_m) et renvoie une chaine "HH:MM".
    """
    mode = source.get("heure_mode", "0830")
    if mode == "personnalise":
        try:
            heure = int(source.get("heure_h", 8))
            minute = int(source.get("heure_m", 30))
            return f"{heure:02d}:{minute:02d}"
        except ValueError:
            return defaut
    return HEURES_PREREGLEES.get(mode, defaut)


def _clients_json_avec_etiquettes(db: Session) -> str:
    clients = (
        db.query(models.Client)
        .filter(models.Client.account_id != "", models.Client.location_id != "")
        .order_by(models.Client.nom)
        .all()
    )
    return json.dumps([
        {"id": c.id, "nom": c.nom, "etiquette_ids": [e.id for e in c.etiquettes]} for c in clients
    ]).replace("</", "<\\/")


def _reponse_posts_multi(request: Request, db: Session, erreur: str = None, valeurs: dict = None, code: int = 200):
    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    clients_reference = db.query(models.Client).order_by(models.Client.nom).all()
    return templates.TemplateResponse(
        request,
        "posts_multi.html",
        {
            "etiquettes": etiquettes,
            "clients_json": _clients_json_avec_etiquettes(db),
            "clients_reference": clients_reference,
            "options_appel_action": google_publish.OPTIONS_APPEL_ACTION,
            "types_post": google_publish.TYPES_POST,
            "erreur": erreur,
            "valeurs": valeurs or {
                "titre": "", "texte": "", "prompt_image": "", "image_url": "",
                "type_appel_action": "", "url_appel_action": "",
                "type_post": "STANDARD", "evenement_titre": "",
                "evenement_date_debut": "", "evenement_heure_debut": "",
                "evenement_date_fin": "", "evenement_heure_fin": "",
                "offre_code": "", "offre_url": "", "offre_conditions": "",
            },
        },
        status_code=code,
    )


@app.get("/posts", response_class=HTMLResponse)
def posts_multi_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    return _reponse_posts_multi(request, db)


@app.post("/posts/generer_generique")
def generer_post_generique_route(
    request: Request,
    theme: str = Form(""),
    client_reference_id: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    contenu_reference = ""
    if client_reference_id.strip():
        client_reference = db.get(models.Client, int(client_reference_id))
        if client_reference:
            contenu_reference = _contexte_ia_client(client_reference)

    try:
        post_genere = claude_generation.generer_post_generique(theme, contenu_reference)
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=500)

    return JSONResponse(post_genere)


@app.post("/posts/creer_multi")
async def creer_posts_multi(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    titre = formulaire.get("titre", "").strip()
    texte = formulaire.get("texte", "").strip()
    prompt_image = formulaire.get("prompt_image", "")
    image_url = formulaire.get("image_url", "").strip()
    type_appel_action = formulaire.get("type_appel_action", "")
    url_appel_action = formulaire.get("url_appel_action", "").strip()
    type_post = formulaire.get("type_post", "STANDARD")
    evenement_titre = formulaire.get("evenement_titre", "").strip()
    evenement_date_debut_brut = formulaire.get("evenement_date_debut", "").strip()
    evenement_heure_debut = formulaire.get("evenement_heure_debut", "").strip()
    evenement_date_fin_brut = formulaire.get("evenement_date_fin", "").strip()
    evenement_heure_fin = formulaire.get("evenement_heure_fin", "").strip()
    offre_code = formulaire.get("offre_code", "").strip()
    offre_url = formulaire.get("offre_url", "").strip()
    offre_conditions = formulaire.get("offre_conditions", "")
    client_ids = [int(v) for v in formulaire.getlist("client_ids") if v.strip()]
    valeurs = {
        "titre": titre, "texte": texte, "prompt_image": prompt_image, "image_url": image_url,
        "type_appel_action": type_appel_action, "url_appel_action": url_appel_action,
        "type_post": type_post, "evenement_titre": evenement_titre,
        "evenement_date_debut": evenement_date_debut_brut, "evenement_heure_debut": evenement_heure_debut,
        "evenement_date_fin": evenement_date_fin_brut, "evenement_heure_fin": evenement_heure_fin,
        "offre_code": offre_code, "offre_url": offre_url, "offre_conditions": offre_conditions,
    }
    evenement_date_debut = date.fromisoformat(evenement_date_debut_brut) if evenement_date_debut_brut else None
    evenement_date_fin = date.fromisoformat(evenement_date_fin_brut) if evenement_date_fin_brut else None

    fichier_image = formulaire.get("fichier_image")
    if fichier_image is not None and getattr(fichier_image, "filename", ""):
        try:
            octets = await fichier_image.read()
            extension = os.path.splitext(fichier_image.filename)[1] or ".jpg"
            horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            image_url = ovh_upload.envoyer_octets(octets, f"posts_multi_{horodatage}{extension}")
            valeurs["image_url"] = image_url
        except Exception as erreur:
            return _reponse_posts_multi(
                request, db, erreur=f"Erreur lors du televersement de l'image : {erreur}", valeurs=valeurs
            )

    if not texte:
        return _reponse_posts_multi(request, db, erreur="Le texte du post est obligatoire.", valeurs=valeurs)
    if not client_ids:
        return _reponse_posts_multi(request, db, erreur="Selectionnez au moins un client.", valeurs=valeurs)

    lot_id = uuid.uuid4().hex[:12]
    for client_id in client_ids:
        client = db.get(models.Client, client_id)
        if not client:
            continue
        db.add(models.Post(
            client_id=client.id, titre=titre, texte=texte, prompt_image=prompt_image,
            image_url=image_url, type_appel_action=type_appel_action, url_appel_action=url_appel_action,
            type_post=type_post, evenement_titre=evenement_titre,
            evenement_date_debut=evenement_date_debut, evenement_heure_debut=evenement_heure_debut or None,
            evenement_date_fin=evenement_date_fin, evenement_heure_fin=evenement_heure_fin or None,
            offre_code=offre_code, offre_url=offre_url, offre_conditions=offre_conditions,
            statut="BROUILLON", lot_id=lot_id,
        ))
    db.commit()

    return RedirectResponse(f"/posts/lot/{lot_id}", status_code=303)


@app.get("/posts/lot/{lot_id}", response_class=HTMLResponse)
def posts_lot(lot_id: str, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    posts = (
        db.query(models.Post)
        .join(models.Client)
        .filter(models.Post.lot_id == lot_id)
        .order_by(models.Client.nom)
        .all()
    )
    if not posts:
        return HTMLResponse("Lot introuvable.", status_code=404)

    return templates.TemplateResponse(request, "posts_lot.html", {"lot_id": lot_id, "posts": posts})


@app.post("/posts/lot/{lot_id}/programmer")
def programmer_lot_posts(
    lot_id: str,
    request: Request,
    date_prevue: str = Form(...),
    heure_mode: str = Form("0830"),
    heure_h: str = Form(""),
    heure_m: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    try:
        date_programmee = date.fromisoformat(date_prevue)
    except ValueError:
        return HTMLResponse("Date invalide.", status_code=400)

    heure_programmee = _heure_depuis_formulaire({"heure_mode": heure_mode, "heure_h": heure_h, "heure_m": heure_m})

    db.query(models.Post).filter(
        models.Post.lot_id == lot_id, models.Post.statut == "BROUILLON"
    ).update({"statut": "A_PUBLIER", "date_prevue": date_programmee, "heure_prevue": heure_programmee})
    db.commit()

    return RedirectResponse(f"/posts/lot/{lot_id}", status_code=303)


# --- Liste et gestion des clients -----------------------------------------


@app.get("/", response_class=HTMLResponse)
def liste_clients(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    clients = db.query(models.Client).order_by(models.Client.nom).all()
    return templates.TemplateResponse(
        request,
        "clients_liste.html",
        {"clients": clients, "google_connecte": google_oauth.google_est_connecte(db)},
    )


@app.get("/clients/nouveau", response_class=HTMLResponse)
def nouveau_client_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    fiches = []
    google_connecte = google_oauth.google_est_connecte(db)
    if google_connecte:
        comptes_avec_identifiants = [
            (compte.id, compte.libelle, google_oauth.obtenir_identifiants(db, compte.id))
            for compte in google_oauth.lister_comptes(db)
        ]
        comptes_avec_identifiants = [c for c in comptes_avec_identifiants if c[2] is not None]
        fiches = google_business.lister_fiches_multi_comptes(comptes_avec_identifiants)

        # On ne repropose pas les fiches deja associees a un client existant.
        fiches_deja_liees = {
            (c.compte_google_id, c.account_id, c.location_id)
            for c in db.query(models.Client).filter(models.Client.location_id != "").all()
        }
        fiches = [
            f for f in fiches
            if (f["compte_google_id"], f["account_id"], f["location_id"]) not in fiches_deja_liees
        ]

    return templates.TemplateResponse(
        request,
        "client_nouveau.html",
        {"fiches": fiches, "google_connecte": google_connecte, "erreur": None},
    )


@app.post("/clients/nouveau")
def creer_client(
    request: Request,
    nom: str = Form(...),
    contenu_site: str = Form(""),
    consignes_avis: str = Form(""),
    fiche_google: str = Form(""),
    fichiers: list[UploadFile] = File(default=[]),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte_google_id, _, reste = fiche_google.partition("|")
    account_id, _, location_id = reste.partition("|")

    client = models.Client(
        nom=nom.strip(),
        contenu_site=contenu_site,
        consignes_avis=consignes_avis,
        account_id=account_id,
        location_id=location_id,
        compte_google_id=int(compte_google_id) if compte_google_id else None,
    )
    db.add(client)
    db.commit()

    # Base de connaissances renseignee des la creation : on cree quand meme le
    # client si un document echoue a l'extraction, l'erreur est juste affichee
    # sur sa fiche (comme un ajout de document classique).
    erreurs_documents = []
    for fichier in fichiers:
        if not fichier.filename:
            continue
        try:
            octets = fichier.file.read()
            texte_extrait = documents.extraire_texte(fichier.filename, octets)
        except Exception as erreur:
            erreurs_documents.append(f"{fichier.filename} : {erreur}")
            continue
        db.add(models.DocumentConnaissance(
            client_id=client.id, nom_fichier=fichier.filename, texte_extrait=texte_extrait,
        ))
    db.commit()

    if erreurs_documents:
        client = db.get(models.Client, client.id)
        return _reponse_detail_client(request, db, client, erreur_document=" ; ".join(erreurs_documents), code=200)

    return RedirectResponse(f"/clients/{client.id}", status_code=303)


def _contexte_ia_client(client: models.Client) -> str:
    """
    Contexte complet fourni a l'IA pour ce client : le champ libre
    Client.contenu_site, complete par le texte extrait de chaque document de
    la base de connaissances (voir app/documents.py).
    """
    morceaux = []
    if client.contenu_site and client.contenu_site.strip():
        morceaux.append(client.contenu_site.strip())
    for document in client.documents_connaissance:
        morceaux.append(f"--- Document : {document.nom_fichier} ---\n{document.texte_extrait}")
    return "\n\n".join(morceaux)


def _reponse_detail_client(
    request: Request, db: Session, client: models.Client, erreur_generation: str = None,
    erreur_photo: str = None, erreur_document: str = None, code: int = 200,
):
    posts = (
        db.query(models.Post)
        .filter_by(client_id=client.id)
        .order_by(models.Post.cree_le.desc())
        .all()
    )
    photos_en_preparation = (
        db.query(models.PhotoFiche)
        .filter(models.PhotoFiche.client_id == client.id, models.PhotoFiche.statut != "PUBLIE_LIVE")
        .order_by(models.PhotoFiche.cree_le.desc())
        .all()
    )
    toutes_etiquettes_json = json.dumps(
        [e.nom for e in db.query(models.Etiquette).order_by(models.Etiquette.nom).all()]
    ).replace("</", "<\\/")
    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "client": client,
            "posts": posts,
            "erreur_generation": erreur_generation,
            "photos": _photos_pour_client(db, client),
            "photos_en_preparation": photos_en_preparation,
            "categories_photo": [
                (valeur, google_business.LIBELLES_CATEGORIE_PHOTO.get(valeur, valeur))
                for valeur in google_business.CATEGORIES_PHOTO
            ],
            "erreur_photo": erreur_photo,
            "erreur_document": erreur_document,
            "toutes_etiquettes_json": toutes_etiquettes_json,
            **_donnees_calendrier(request, db, client),
        },
        status_code=code,
    )


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def detail_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    return _reponse_detail_client(request, db, client)


@app.post("/clients/{client_id}/generer")
def generer_posts_client(
    client_id: int,
    request: Request,
    nombre_posts: int = Form(7),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    try:
        posts_generes = claude_generation.generer_posts_pour_client(_contexte_ia_client(client), nombre_posts)
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_generation=str(erreur), code=500)

    for post_genere in posts_generes:
        db.add(models.Post(
            client_id=client.id,
            titre=post_genere["titre"],
            texte=post_genere["texte"],
            prompt_image=post_genere["prompt_image"],
            statut="BROUILLON",
        ))
    db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/documents")
def ajouter_document_client(
    client_id: int, request: Request, fichier: UploadFile = File(...), db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    try:
        octets = fichier.file.read()
        texte_extrait = documents.extraire_texte(fichier.filename or "", octets)
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_document=str(erreur), code=400)

    db.add(models.DocumentConnaissance(
        client_id=client.id, nom_fichier=fichier.filename or "document", texte_extrait=texte_extrait,
    ))
    db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/documents/{document_id}/supprimer")
def supprimer_document_client(client_id: int, document_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    document = db.get(models.DocumentConnaissance, document_id)
    if document and document.client_id == client_id:
        db.delete(document)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.get("/geocodage")
def geocodage_route(request: Request, q: str = ""):
    """Recherche de lieux (JSON) pour le champ de geotag des photos."""
    if not utilisateur_connecte(request):
        return JSONResponse({"resultats": []}, status_code=401)

    try:
        resultats = geocodage.rechercher_lieu(q)
    except Exception:
        resultats = []
    return JSONResponse({"resultats": resultats})


@app.get("/clients/{client_id}/categories/recherche")
def rechercher_categories_route(client_id: int, request: Request, q: str = "", db: Session = Depends(obtenir_session)):
    """Recherche de categories Google (JSON) pour le champ categorie de la fiche."""
    if not utilisateur_connecte(request):
        return JSONResponse({"resultats": []}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"resultats": []}, status_code=404)

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return JSONResponse({"resultats": []}, status_code=400)

    try:
        resultats = google_location.rechercher_categories(identifiants, q)
    except Exception:
        resultats = []
    return JSONResponse({"resultats": resultats})


@app.post("/clients/{client_id}/photos/importer")
def importer_photos_client(
    client_id: int,
    request: Request,
    categorie: str = Form("ADDITIONAL"),
    fichiers: list[UploadFile] = File(...),
    db: Session = Depends(obtenir_session),
):
    """
    Importe une ou plusieurs photos : envoi immediat vers l'hebergement OVH,
    mais mise en attente cote Google (statut BROUILLON) pour permettre une
    relecture (et un retrait des photos non voulues) avant envoi.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not client.account_id or not client.location_id:
        return _reponse_detail_client(request, db, client, erreur_photo="Ce client n'a pas de fiche Google associee.")

    erreurs = []
    for fichier in fichiers:
        if not fichier.filename:
            continue
        try:
            octets = fichier.file.read()
            extension = os.path.splitext(fichier.filename)[1] or ".jpg"
            horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            nom_fichier = f"fiche_{client.id}_{horodatage}{extension}"
            url_publique = ovh_upload.envoyer_octets(octets, nom_fichier)
        except Exception as erreur:
            erreurs.append(f"{fichier.filename} : {erreur}")
            continue

        db.add(models.PhotoFiche(
            client_id=client.id, url_image=url_publique, categorie=categorie, statut="BROUILLON",
        ))
    db.commit()

    if erreurs:
        return _reponse_detail_client(
            request, db, client, erreur_photo="Erreur lors de l'import : " + " / ".join(erreurs), code=500
        )
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/photos/{photo_id}/modifier")
def modifier_photo_client(
    client_id: int,
    photo_id: int,
    request: Request,
    legende: str = Form(""),
    categorie: str = Form("ADDITIONAL"),
    latitude: str = Form(""),
    longitude: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    """Modifie legende/categorie/geotag d'une photo tant qu'elle n'a pas encore ete envoyee a Google."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    photo = db.get(models.PhotoFiche, photo_id)
    if photo and photo.client_id == client_id and photo.statut != "PUBLIE_LIVE":
        photo.legende = legende.strip()
        photo.categorie = categorie
        try:
            photo.latitude = float(latitude.replace(",", ".")) if latitude.strip() else None
            photo.longitude = float(longitude.replace(",", ".")) if longitude.strip() else None
        except ValueError:
            return _reponse_detail_client(request, db, client=photo.client, erreur_photo="Coordonnees GPS invalides.")
        db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/photos/{photo_id}/supprimer")
def supprimer_photo_client(client_id: int, photo_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    photo = db.get(models.PhotoFiche, photo_id)
    if photo and photo.client_id == client_id:
        db.delete(photo)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/photos/{photo_id}/publier")
def publier_photo_client(client_id: int, photo_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Publie immediatement une seule photo en attente. Appelee en JS photo par photo
    (plutot qu'un endpoint qui publie tout d'un coup) pour permettre une barre de
    progression reelle cote navigateur.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    photo = db.get(models.PhotoFiche, photo_id)
    if not photo or photo.client_id != client_id:
        return JSONResponse({"erreur": "Photo introuvable."}, status_code=404)

    identifiants = google_oauth.obtenir_identifiants(db, photo.client.compte_google_id)
    if not identifiants:
        return JSONResponse(
            {"erreur": "Compte Google non valide pour ce client (a reconnecter depuis Comptes Google)."},
            status_code=400,
        )

    try:
        google_business.publier_photo_fiche(db, identifiants, photo)
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=500)

    return JSONResponse({"ok": True})


@app.post("/clients/{client_id}/photos/programmer")
def programmer_photos_client(
    client_id: int, request: Request, date_prevue: str = Form(...), db: Session = Depends(obtenir_session)
):
    """Programme l'envoi de toutes les photos actuellement en BROUILLON pour ce client."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    try:
        date_programmee = date.fromisoformat(date_prevue)
    except ValueError:
        return _reponse_detail_client(request, db, client, erreur_photo="Date invalide.")

    db.query(models.PhotoFiche).filter(
        models.PhotoFiche.client_id == client_id, models.PhotoFiche.statut == "BROUILLON"
    ).update({"statut": "A_PUBLIER", "date_prevue": date_programmee})
    db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


# --- Informations de base de la fiche (nom, telephone, adresse, horaires...) -


def _valeurs_formulaire_fiche(infos: dict) -> dict:
    if not infos:
        return {
            "titre": "", "telephone": "", "site_web": "", "description": "",
            "adresse_ligne1": "", "adresse_ligne2": "", "ville": "", "code_postal": "",
            "region": "", "pays": "FR",
        }
    adresse = infos.get("storefrontAddress") or {}
    lignes = adresse.get("addressLines") or []
    return {
        "titre": infos.get("title", ""),
        "telephone": (infos.get("phoneNumbers") or {}).get("primaryPhone", ""),
        "site_web": infos.get("websiteUri", ""),
        "description": (infos.get("profile") or {}).get("description", ""),
        "adresse_ligne1": lignes[0] if len(lignes) > 0 else "",
        "adresse_ligne2": lignes[1] if len(lignes) > 1 else "",
        "ville": adresse.get("locality", ""),
        "code_postal": adresse.get("postalCode", ""),
        "region": adresse.get("administrativeArea", ""),
        "pays": adresse.get("regionCode") or "FR",
    }


def _reponse_fiche_client(
    request: Request, client: models.Client, db: Session, infos: dict = None,
    erreur: str = None, succes: str = None, code: int = 200,
):
    horaires_par_jour, jours_verrouilles = google_location.horaires_par_jour(infos.get("regularHours") if infos else None)

    liens_action, erreur_liens_action = [], None
    if client.account_id and client.location_id:
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if identifiants:
            try:
                liens_action = google_place_actions.lister_liens(identifiants, client.location_id)
            except Exception as e:
                erreur_liens_action = str(e)

    return templates.TemplateResponse(
        request,
        "client_fiche.html",
        {
            "client": client,
            "infos": infos,
            "valeurs": _valeurs_formulaire_fiche(infos),
            "erreur": erreur,
            "succes": succes,
            "jours_semaine": google_location.JOURS_SEMAINE,
            "libelles_jour": google_location.LIBELLES_JOUR,
            "slug_par_jour": google_location.SLUG_PAR_JOUR,
            "jours_verrouilles": jours_verrouilles,
            "horaires_par_jour": horaires_par_jour,
            "types_action": google_place_actions.TYPES_ACTION,
            "liens_action": liens_action,
            "erreur_liens_action": erreur_liens_action,
        },
        status_code=code,
    )


@app.get("/clients/{client_id}/fiche", response_class=HTMLResponse)
def fiche_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not client.account_id or not client.location_id:
        return _reponse_fiche_client(request, client, db, erreur="Ce client n'a pas de fiche Google associee.")

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_fiche_client(
            request, client, db,
            erreur="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
    except Exception as erreur:
        return _reponse_fiche_client(request, client, db, erreur=str(erreur))

    return _reponse_fiche_client(request, client, db, infos=infos)


@app.post("/clients/{client_id}/fiche/modifier")
async def modifier_fiche_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_fiche_client(
            request, client, db,
            erreur="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    formulaire = await request.form()

    # On relit la fiche avant d'ecrire pour recuperer telles quelles les
    # plages horaires des jours "verrouilles" (a cheval sur un autre jour) : le
    # formulaire ne les gere pas, il ne faut donc jamais les ecraser.
    try:
        infos_actuelles = google_location.obtenir_infos_fiche(identifiants, client.location_id)
    except Exception as erreur:
        return _reponse_fiche_client(
            request, client, db, erreur=f"Impossible de relire la fiche avant enregistrement : {erreur}"
        )

    _, jours_verrouilles = google_location.horaires_par_jour(infos_actuelles.get("regularHours"))
    periodes = [
        p for p in infos_actuelles.get("regularHours", {}).get("periods", [])
        if p.get("openDay") in jours_verrouilles or p.get("closeDay") in jours_verrouilles
    ]
    for jour, slug in google_location.SLUG_PAR_JOUR.items():
        if jour in jours_verrouilles or formulaire.get(f"{slug}_ferme"):
            continue
        if formulaire.get(f"{slug}_24h"):
            periodes.append({
                "openDay": jour, "openTime": {"hours": 0, "minutes": 0},
                "closeDay": jour, "closeTime": {"hours": 24, "minutes": 0},
            })
            continue
        ouvertures = formulaire.getlist(f"{slug}_ouverture")
        fermetures = formulaire.getlist(f"{slug}_fermeture")
        for ouverture, fermeture in zip(ouvertures, fermetures):
            if not ouverture or not fermeture:
                continue
            h_o, m_o = (int(x) for x in ouverture.split(":"))
            h_f, m_f = (int(x) for x in fermeture.split(":"))
            periodes.append({
                "openDay": jour, "openTime": {"hours": h_o, "minutes": m_o},
                "closeDay": jour, "closeTime": {"hours": h_f, "minutes": m_f},
            })

    lignes_adresse = [
        ligne.strip() for ligne in [formulaire.get("adresse_ligne1", ""), formulaire.get("adresse_ligne2", "")]
        if ligne.strip()
    ]

    donnees = {
        "title": formulaire.get("titre", "").strip(),
        "phoneNumbers": {"primaryPhone": formulaire.get("telephone", "").strip()},
        "websiteUri": formulaire.get("site_web", "").strip(),
        "storefrontAddress": {
            "regionCode": (formulaire.get("pays", "FR") or "FR").strip().upper(),
            "postalCode": formulaire.get("code_postal", "").strip(),
            "administrativeArea": formulaire.get("region", "").strip(),
            "locality": formulaire.get("ville", "").strip(),
            "addressLines": lignes_adresse,
        },
        "regularHours": {"periods": periodes},
        "profile": {"description": formulaire.get("description", "").strip()},
    }
    champs = ["title", "phoneNumbers", "websiteUri", "storefrontAddress", "regularHours", "profile.description"]

    # Categorie principale obligatoire cote Google : on ne touche aux
    # categories que si elle est presente (jamais d'ecriture vide/partielle -
    # primaryCategory et additionalCategories doivent toujours etre envoyes
    # ensemble).
    categorie_principale_id = formulaire.get("categorie_principale_id", "").strip()
    if categorie_principale_id:
        categories_complementaires_id = [
            c for c in formulaire.getlist("categories_complementaires_id") if c.strip()
        ]
        donnees["categories"] = {
            "primaryCategory": {"name": categorie_principale_id},
            "additionalCategories": [{"name": cid} for cid in categories_complementaires_id],
        }
        champs.append("categories")

    try:
        google_location.mettre_a_jour_fiche(identifiants, client.location_id, donnees, champs)
    except Exception as erreur:
        return _reponse_fiche_client(request, client, db, erreur=f"Erreur lors de l'enregistrement : {erreur}")

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
    except Exception:
        infos = None
    return _reponse_fiche_client(request, client, db, infos=infos, succes="Fiche mise a jour avec succes.")


@app.post("/clients/{client_id}/fiche/liens_action")
async def creer_lien_action_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_fiche_client(
            request, client, db,
            erreur="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    formulaire = await request.form()
    type_action = formulaire.get("type_action", "")
    uri = formulaire.get("uri", "").strip()
    est_prefere = bool(formulaire.get("est_prefere"))

    if not type_action or not uri:
        return _reponse_fiche_client(request, client, db, erreur="Type d'action et URL obligatoires.")

    try:
        google_place_actions.creer_lien(identifiants, client.location_id, type_action, uri, est_prefere)
    except Exception as erreur:
        return _reponse_fiche_client(request, client, db, erreur=f"Erreur lors de la creation du lien : {erreur}")

    return _reponse_fiche_client(request, client, db, succes="Lien d'action ajoute avec succes.")


@app.post("/clients/{client_id}/fiche/liens_action/{lien_id}/supprimer")
def supprimer_lien_action_client(client_id: int, lien_id: str, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_fiche_client(
            request, client, db,
            erreur="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    try:
        google_place_actions.supprimer_lien(identifiants, client.location_id, lien_id)
    except Exception as erreur:
        return _reponse_fiche_client(request, client, db, erreur=f"Erreur lors de la suppression du lien : {erreur}")

    return _reponse_fiche_client(request, client, db, succes="Lien d'action supprime.")


# --- Carte de positions par mots-cles (grille geographique, type Localo) ----


def _obtenir_coordonnees_client(db: Session, client: models.Client):
    """Renvoie (latitude, longitude) de la fiche, en les mettant en cache sur le client si absentes."""
    if client.latitude is not None and client.longitude is not None:
        return client.latitude, client.longitude

    if not client.account_id or not client.location_id:
        return None, None

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return None, None

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
    except Exception:
        return None, None

    latitude, longitude = google_location.coordonnees(infos)

    # latlng n'est renseigne par Google que si des coordonnees ont ete definies
    # manuellement (rare) : on tente de geocoder l'adresse de la fiche en repli.
    if latitude is None or longitude is None:
        adresse = google_location.adresse_texte(infos)
        if adresse:
            try:
                resultats = geocodage.rechercher_lieu(adresse)
            except Exception:
                resultats = []
            if resultats:
                latitude, longitude = resultats[0]["latitude"], resultats[0]["longitude"]

    if latitude is not None and longitude is not None:
        client.latitude = latitude
        client.longitude = longitude
        db.commit()
    return latitude, longitude


@app.post("/clients/{client_id}/positions/coordonnees")
def definir_coordonnees_client(
    client_id: int, request: Request, latitude: float = Form(...), longitude: float = Form(...),
    db: Session = Depends(obtenir_session),
):
    """Definit manuellement les coordonnees de centrage (repli quand Google/le geocodage n'en fournissent pas)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    client.latitude = latitude
    client.longitude = longitude
    db.commit()

    return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)


@app.get("/clients/{client_id}/positions", response_class=HTMLResponse)
def positions_client(request: Request, client_id: int, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    latitude, longitude = _obtenir_coordonnees_client(db, client)

    releves = (
        db.query(models.ReleveDePosition)
        .filter_by(client_id=client_id)
        .order_by(models.ReleveDePosition.cree_le.desc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "client_positions.html",
        {
            "client": client,
            "latitude": latitude,
            "longitude": longitude,
            "releves": releves,
            "dataforseo_configure": rank_tracking.identifiants_configures(),
            "erreur": None,
        },
    )


@app.post("/clients/{client_id}/positions/mots_cles")
def ajouter_mot_cle(client_id: int, request: Request, texte: str = Form(...), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    if texte.strip():
        db.add(models.MotCle(client_id=client_id, texte=texte.strip()))
        db.commit()

    return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)


@app.post("/clients/{client_id}/positions/mots_cles/{mot_cle_id}/supprimer")
def supprimer_mot_cle(client_id: int, mot_cle_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    mot_cle = db.get(models.MotCle, mot_cle_id)
    if mot_cle and mot_cle.client_id == client_id:
        db.delete(mot_cle)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)


@app.post("/clients/{client_id}/positions/releve/{releve_id}/supprimer")
def supprimer_releve_position(client_id: int, releve_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    releve = db.get(models.ReleveDePosition, releve_id)
    if releve and releve.client_id == client_id:
        db.delete(releve)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)


@app.post("/clients/{client_id}/positions/verifier/demarrer")
def demarrer_releve_position(
    client_id: int,
    request: Request,
    mot_cle_texte: str = Form(...),
    taille_grille: int = Form(5),
    rayon_km: float = Form(2.0),
    db: Session = Depends(obtenir_session),
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    if not rank_tracking.identifiants_configures():
        return JSONResponse(
            {"erreur": "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD manquants dans plateforme_web/.env."},
            status_code=400,
        )

    latitude, longitude = _obtenir_coordonnees_client(db, client)
    if latitude is None or longitude is None:
        return JSONResponse(
            {"erreur": "Coordonnees introuvables pour cette fiche (verifiez la connexion Google)."}, status_code=400
        )

    releve = models.ReleveDePosition(
        client_id=client_id, mot_cle_texte=mot_cle_texte.strip(), taille_grille=taille_grille,
        rayon_km=rayon_km, latitude_centre=latitude, longitude_centre=longitude, statut="EN_COURS",
    )
    db.add(releve)
    db.commit()
    db.refresh(releve)

    points_coords = rank_tracking.generer_points_grille(latitude, longitude, taille_grille, rayon_km)
    points = []
    for lat, lng in points_coords:
        point = models.PointDeGrille(releve_id=releve.id, latitude=lat, longitude=lng)
        db.add(point)
        points.append(point)
    db.commit()

    return JSONResponse({
        "releve_id": releve.id,
        "latitude_centre": latitude,
        "longitude_centre": longitude,
        "points": [{"id": p.id, "latitude": p.latitude, "longitude": p.longitude} for p in points],
    })


@app.post("/clients/{client_id}/positions/verifier/point/{point_id}")
def verifier_point_grille(
    client_id: int, point_id: int, request: Request, db: Session = Depends(obtenir_session)
):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    point = db.get(models.PointDeGrille, point_id)
    if not point or point.releve.client_id != client_id:
        return JSONResponse({"erreur": "Point introuvable."}, status_code=404)

    client = point.releve.client
    try:
        position, nom_correspondance, classement = rank_tracking.verifier_position(
            point.releve.mot_cle_texte, point.latitude, point.longitude, client.nom
        )
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=500)

    point.position = position
    point.nom_correspondance = nom_correspondance
    point.resultats_json = json.dumps(classement)
    point.verifie = True
    db.commit()

    tous_verifies = all(p.verifie for p in point.releve.points)
    if tous_verifies:
        point.releve.statut = "TERMINE"
        db.commit()

    return JSONResponse({"position": position, "nom_correspondance": nom_correspondance, "classement": classement})


@app.get("/clients/{client_id}/positions/releve/{releve_id}")
def obtenir_releve_position(client_id: int, releve_id: int, request: Request, db: Session = Depends(obtenir_session)):
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    releve = db.get(models.ReleveDePosition, releve_id)
    if not releve or releve.client_id != client_id:
        return JSONResponse({"erreur": "Releve introuvable."}, status_code=404)

    # Auto-guerison : avec des verifications de points concurrentes (requetes
    # paralleles), le dernier point a se terminer peut ne pas voir les autres
    # points comme deja "verifie" au moment de son propre commit (course entre
    # requetes), et le statut reste alors bloque sur EN_COURS malgre un releve
    # en realite complet. On corrige ici a la lecture.
    if releve.statut == "EN_COURS" and releve.points and all(p.verifie for p in releve.points):
        releve.statut = "TERMINE"
        db.commit()

    return JSONResponse({
        "id": releve.id,
        "mot_cle_texte": releve.mot_cle_texte,
        "statut": releve.statut,
        "latitude_centre": releve.latitude_centre,
        "longitude_centre": releve.longitude_centre,
        "resume": rank_tracking.resumer_releve(releve.points),
        "points": [
            {
                "id": p.id, "latitude": p.latitude, "longitude": p.longitude,
                "position": p.position, "verifie": p.verifie, "nom_correspondance": p.nom_correspondance,
                "classement": json.loads(p.resultats_json) if p.resultats_json else [],
            }
            for p in releve.points
        ],
    })


# --- Statistiques et rapport ------------------------------------------------


def _periode_depuis_requete(request: Request):
    aujourdhui = date.today()
    try:
        debut = date.fromisoformat(request.query_params.get("debut", ""))
    except ValueError:
        debut = aujourdhui - timedelta(days=30)
    try:
        fin = date.fromisoformat(request.query_params.get("fin", ""))
    except ValueError:
        fin = aujourdhui
    return debut, fin


@app.get("/clients/{client_id}/stats", response_class=HTMLResponse)
def stats_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    debut, fin = _periode_depuis_requete(request)

    if not client.account_id or not client.location_id:
        return templates.TemplateResponse(
            request, "client_stats.html",
            {"client": client, "debut": debut, "fin": fin, "erreur": "Ce client n'a pas de fiche Google associee.",
             **rapport_donnees.donnees_rapport_vides()},
        )

    try:
        donnees = rapport_donnees.rassembler_donnees_rapport(db, client, debut, fin)
        erreur = None
    except Exception as e:
        donnees = rapport_donnees.donnees_rapport_vides()
        erreur = str(e)

    return templates.TemplateResponse(
        request, "client_stats.html",
        {"client": client, "debut": debut, "fin": fin, "erreur": erreur, **donnees},
    )


@app.get("/clients/{client_id}/stats/pdf")
def stats_client_pdf(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client or not client.account_id or not client.location_id:
        return HTMLResponse("Client introuvable ou sans fiche Google associee.", status_code=404)

    debut, fin = _periode_depuis_requete(request)
    sections = set(request.query_params.getlist("sections")) & rapport_pdf.SECTIONS_DISPONIBLES
    if not sections:
        sections = rapport_pdf.SECTIONS_DISPONIBLES

    try:
        donnees = rapport_donnees.rassembler_donnees_rapport(db, client, debut, fin)
    except Exception as erreur:
        return HTMLResponse(f"Impossible de generer le rapport : {erreur}", status_code=500)

    octets_pdf = rapport_pdf.generer_rapport_pdf(
        client.nom, debut, fin, donnees["statistiques"], donnees["resume_avis"], donnees["posts_publies"],
        mots_cles=donnees["mots_cles"], comparatif_visibilite=donnees["comparatif_visibilite"],
        evolution_avis=donnees["evolution_avis"], sections=sections,
    )

    nom_fichier = f"rapport_{client.nom.replace(' ', '_')}_{debut.isoformat()}_{fin.isoformat()}.pdf"
    return Response(
        content=octets_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


# --- Recap mensuel (email) --------------------------------------------------


@app.get("/recaps", response_class=HTMLResponse)
def recaps(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    clients = (
        db.query(models.Client)
        .filter(models.Client.account_id != "", models.Client.location_id != "")
        .order_by(models.Client.nom)
        .all()
    )
    mois, annee = rapport_donnees.mois_precedent(date.today())

    lignes = []
    for client in clients:
        dernier_envoi = (
            db.query(models.EnvoiRecap)
            .filter_by(client_id=client.id)
            .order_by(models.EnvoiRecap.horodatage.desc())
            .first()
        )
        lignes.append({"client": client, "dernier_envoi": dernier_envoi})

    return templates.TemplateResponse(
        request, "recaps.html",
        {
            "lignes": lignes,
            "mois_cible": recap_mensuel.LIBELLES_MOIS[mois],
            "annee_cible": annee,
            "brevo_configure": brevo_email.identifiants_configures(),
        },
    )


@app.get("/clients/{client_id}/recap/apercu", response_class=HTMLResponse)
def apercu_recap(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client or not client.account_id or not client.location_id:
        return HTMLResponse("Client introuvable ou sans fiche Google associee.", status_code=404)

    mois, annee = rapport_donnees.mois_precedent(date.today())
    debut = date(annee, mois, 1)
    fin = date(annee, mois, calendar.monthrange(annee, mois)[1])

    try:
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            raise RuntimeError("Le compte Google associe a ce client n'est plus valide.")
        donnees = rapport_donnees.rassembler_donnees_rapport(db, client, debut, fin)
        avis_recents = google_reviews.avis_cinq_etoiles_recents(
            identifiants, client.account_id, client.location_id, debut, fin
        )
    except Exception as erreur:
        return HTMLResponse(f"Impossible de generer l'apercu : {erreur}", status_code=500)

    html = recap_mensuel.construire_email(client, donnees, mois, annee, avis_recents)
    return HTMLResponse(html)


@app.post("/clients/{client_id}/recap/envoyer")
def envoyer_recap_manuel(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    mois, annee = rapport_donnees.mois_precedent(date.today())
    rapport_donnees.envoyer_recap_client(db, client, mois, annee)
    return RedirectResponse("/recaps", status_code=303)


def _obtenir_ou_creer_etiquettes(db: Session, noms_etiquettes) -> list:
    """Convertit une liste de noms en liste d'objets Etiquette, en creant celles qui n'existent pas encore."""
    noms = {n.strip() for n in noms_etiquettes if n.strip()}
    etiquettes = []
    for nom in noms:
        etiquette = db.query(models.Etiquette).filter_by(nom=nom).first()
        if not etiquette:
            etiquette = models.Etiquette(nom=nom)
            db.add(etiquette)
            db.flush()
        etiquettes.append(etiquette)
    return etiquettes


@app.post("/clients/{client_id}/modifier")
def modifier_client(
    client_id: int,
    request: Request,
    nom: str = Form(...),
    contenu_site: str = Form(""),
    account_id: str = Form(""),
    location_id: str = Form(""),
    consignes_avis: str = Form(""),
    email: str = Form(""),
    etiquettes: list[str] = Form(default=[]),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    client.nom = nom.strip()
    client.contenu_site = contenu_site
    client.account_id = account_id.strip()
    client.location_id = location_id.strip()
    client.consignes_avis = consignes_avis
    client.email = email.strip()
    client.etiquettes = _obtenir_ou_creer_etiquettes(db, etiquettes)
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/supprimer")
def supprimer_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if client:
        db.delete(client)
        db.commit()
    return RedirectResponse("/", status_code=303)


# --- Connexion Google (OAuth) ---------------------------------------------


@app.get("/google/connecter")
def google_connecter(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    redirect_uri = str(request.url_for("google_callback"))
    flow = google_oauth.construire_flow(redirect_uri)
    url_autorisation, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = flow.code_verifier
    return RedirectResponse(url_autorisation)


@app.get("/google/callback", name="google_callback")
def google_callback(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    state_attendu = request.session.get("oauth_state")
    if state_attendu and request.query_params.get("state") != state_attendu:
        return HTMLResponse("Etat OAuth invalide, merci de reessayer depuis /google/connecter.", status_code=400)

    redirect_uri = str(request.url_for("google_callback"))
    code_verifier = request.session.get("oauth_code_verifier")
    flow = google_oauth.construire_flow(redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=str(request.url))

    google_oauth.enregistrer_refresh_token(db, flow.credentials.refresh_token)
    return RedirectResponse("/google/comptes", status_code=303)


@app.get("/google/comptes", response_class=HTMLResponse)
def google_comptes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comptes = google_oauth.lister_comptes(db)
    return templates.TemplateResponse(request, "google_comptes.html", {"comptes": comptes})


@app.post("/google/comptes/{compte_id}/deconnecter")
def google_deconnecter_compte(compte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteGoogle, compte_id)
    if compte:
        clients_lies = db.query(models.Client).filter_by(compte_google_id=compte_id).count()
        if clients_lies:
            comptes = google_oauth.lister_comptes(db)
            return templates.TemplateResponse(
                request,
                "google_comptes.html",
                {
                    "comptes": comptes,
                    "erreur": (
                        f"Impossible de deconnecter ce compte : {clients_lies} client(s) y sont "
                        "encore rattaches. Reassignez-les d'abord a un autre compte."
                    ),
                },
                status_code=400,
            )
        db.delete(compte)
        db.commit()

    return RedirectResponse("/google/comptes", status_code=303)


# --- Etiquettes --------------------------------------------------------------


@app.get("/etiquettes", response_class=HTMLResponse)
def liste_etiquettes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    return templates.TemplateResponse(request, "etiquettes.html", {"etiquettes": etiquettes, "erreur": None})


@app.post("/etiquettes")
def creer_etiquette(request: Request, nom: str = Form(...), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    nom = nom.strip()
    erreur = None
    if not nom:
        erreur = "Le nom de l'étiquette ne peut pas être vide."
    elif db.query(models.Etiquette).filter_by(nom=nom).first():
        erreur = f"L'étiquette « {nom} » existe déjà."
    else:
        db.add(models.Etiquette(nom=nom))
        db.commit()

    if erreur:
        etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
        return templates.TemplateResponse(
            request, "etiquettes.html", {"etiquettes": etiquettes, "erreur": erreur}, status_code=400
        )
    return RedirectResponse("/etiquettes", status_code=303)


@app.post("/etiquettes/{etiquette_id}/supprimer")
def supprimer_etiquette(etiquette_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquette = db.get(models.Etiquette, etiquette_id)
    if etiquette:
        etiquette.clients = []
        db.delete(etiquette)
        db.commit()
    return RedirectResponse("/etiquettes", status_code=303)


# --- Relecture d'un post ----------------------------------------------------


def _photos_pour_client(db: Session, client: models.Client):
    if client.account_id and client.location_id:
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if identifiants:
            return google_business.lister_photos(identifiants, client.account_id, client.location_id)
    return []


def _photos_pour_post(db: Session, post: models.Post):
    return _photos_pour_client(db, post.client)


def _reponse_post_detail(request: Request, db: Session, post: models.Post, erreur: str = None, code: int = 200):
    return templates.TemplateResponse(
        request,
        "post_detail.html",
        {
            "post": post,
            "photos": _photos_pour_post(db, post),
            "options_appel_action": google_publish.OPTIONS_APPEL_ACTION,
            "types_post": google_publish.TYPES_POST,
            "erreur": erreur,
        },
        status_code=code,
    )


@app.get("/posts/{post_id}", response_class=HTMLResponse)
def detail_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    return _reponse_post_detail(request, db, post)


@app.post("/posts/{post_id}/modifier")
async def modifier_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    formulaire = await request.form()

    post.titre = formulaire.get("titre", "").strip()
    post.texte = formulaire.get("texte", "")
    post.statut = formulaire.get("statut", post.statut)
    post.image_url = formulaire.get("image_url", "").strip()
    post.prompt_image = formulaire.get("prompt_image", "")
    post.type_appel_action = formulaire.get("type_appel_action", "")
    post.url_appel_action = formulaire.get("url_appel_action", "").strip()

    date_prevue = formulaire.get("date_prevue", "")
    post.date_prevue = date.fromisoformat(date_prevue) if date_prevue.strip() else None
    post.heure_prevue = _heure_depuis_formulaire(formulaire)

    post.type_post = formulaire.get("type_post", "STANDARD")
    post.evenement_titre = formulaire.get("evenement_titre", "").strip()
    evenement_date_debut = formulaire.get("evenement_date_debut", "")
    post.evenement_date_debut = date.fromisoformat(evenement_date_debut) if evenement_date_debut.strip() else None
    post.evenement_heure_debut = formulaire.get("evenement_heure_debut", "").strip() or None
    evenement_date_fin = formulaire.get("evenement_date_fin", "")
    post.evenement_date_fin = date.fromisoformat(evenement_date_fin) if evenement_date_fin.strip() else None
    post.evenement_heure_fin = formulaire.get("evenement_heure_fin", "").strip() or None
    post.offre_code = formulaire.get("offre_code", "").strip()
    post.offre_url = formulaire.get("offre_url", "").strip()
    post.offre_conditions = formulaire.get("offre_conditions", "")

    db.commit()

    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/statut_rapide")
async def modifier_statut_rapide_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Validation/rejet rapide depuis la liste des posts d'une fiche (sans passer
    par la page de detail) : ne touche qu'au statut et, si valide, a la date de
    publication - laisse tous les autres champs du post intacts.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    formulaire = await request.form()
    statut = formulaire.get("statut", "")
    if statut not in ("A_PUBLIER", "IGNORE"):
        return HTMLResponse("Statut invalide.", status_code=400)

    post.statut = statut
    if statut == "A_PUBLIER":
        date_prevue = formulaire.get("date_prevue", "")
        post.date_prevue = date.fromisoformat(date_prevue) if date_prevue.strip() else None
        post.heure_prevue = _heure_depuis_formulaire(formulaire)
    db.commit()

    return RedirectResponse(f"/clients/{post.client_id}#post-{post_id}", status_code=303)


@app.post("/posts/{post_id}/generer_image")
def generer_image_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    if not post.prompt_image.strip():
        return _reponse_post_detail(request, db, post, erreur="Aucun prompt image renseigne pour ce post.", code=400)

    try:
        octets_image = gemini_images.generer_image(post.prompt_image)
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        nom_fichier = f"post_{post.id}_{horodatage}.png"
        url_publique = ovh_upload.envoyer_octets(octets_image, nom_fichier)
    except Exception as erreur:
        return _reponse_post_detail(
            request, db, post, erreur=f"Erreur lors de la generation de l'image : {erreur}", code=500
        )

    post.image_url = url_publique
    db.commit()

    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/televerser_image")
def televerser_image_post(
    post_id: int, request: Request, fichier: UploadFile = File(...), db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    try:
        octets = fichier.file.read()
        extension = os.path.splitext(fichier.filename or "")[1] or ".jpg"
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        nom_fichier = f"post_{post.id}_{horodatage}{extension}"
        url_publique = ovh_upload.envoyer_octets(octets, nom_fichier)
    except Exception as erreur:
        return _reponse_post_detail(request, db, post, erreur=f"Erreur lors du televersement : {erreur}", code=500)

    post.image_url = url_publique
    db.commit()

    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/publier")
def publier_post_route(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    if not post.client.account_id or not post.client.location_id:
        return _reponse_post_detail(request, db, post, erreur="Ce client n'a pas de fiche Google associee.", code=400)

    identifiants = google_oauth.obtenir_identifiants(db, post.client.compte_google_id)
    if not identifiants:
        return _reponse_post_detail(request, db, post, erreur="Google n'est pas connecte.", code=400)

    try:
        google_publish.publier_et_verifier(db, identifiants, post)
    except Exception as erreur:
        return _reponse_post_detail(request, db, post, erreur=f"Erreur lors de la publication : {erreur}", code=500)

    return RedirectResponse(f"/posts/{post_id}", status_code=303)
