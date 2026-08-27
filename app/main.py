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
from types import SimpleNamespace
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import (
    bilan_pdf,
    brevo_email,
    claude_generation,
    comparatif_avis_pdf,
    deux_facteurs,
    documents,
    export_clients_excel,
    gemini_images,
    geocodage,
    google_ads_keywords,
    google_autocomplete,
    google_business,
    google_location,
    google_oauth,
    google_performance,
    google_place_actions,
    google_publish,
    google_reviews,
    ia_visibilite,
    models,
    ovh_upload,
    rank_tracking,
    rapport_donnees,
    rapport_pdf,
    recap_mensuel,
    soldes_api,
)
from .database import Base, SessionLocal, engine, obtenir_session
from .planificateur import (
    envoyer_recaps_mensuels,
    verifier_et_publier_photos_programmees,
    verifier_et_publier_posts_programmes,
)
from .security import hacher_mot_de_passe, verifier_mot_de_passe

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
        if "prenom" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN prenom TEXT DEFAULT ''"))
        if "recap_actif" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN recap_actif BOOLEAN DEFAULT TRUE"))

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

        if "utilisateurs" in inspecteur.get_table_names():
            colonnes_utilisateurs = [c["name"] for c in inspecteur.get_columns("utilisateurs")]
            if "totp_secret" not in colonnes_utilisateurs:
                connexion.execute(text("ALTER TABLE utilisateurs ADD COLUMN totp_secret TEXT"))

        if "etiquettes" in inspecteur.get_table_names():
            colonnes_etiquettes = [c["name"] for c in inspecteur.get_columns("etiquettes")]
            if "isolee" not in colonnes_etiquettes:
                connexion.execute(text("ALTER TABLE etiquettes ADD COLUMN isolee BOOLEAN DEFAULT FALSE"))

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
# Fonctions appelables directement depuis les templates (barre laterale,
# affichee sur toutes les pages) : voir soldes_api.py pour le detail du cache.
templates.env.globals["solde_dataforseo"] = soldes_api.solde_dataforseo
templates.env.globals["liens_plateformes_paiement"] = soldes_api.LIENS_PLATEFORMES_PAIEMENT


def _clients_json_recherche_globale() -> str:
    """
    Liste {id, nom} de tous les clients, pour la recherche rapide de la barre
    laterale (voir base.html) - utilise une session dediee (pas celle de la
    requete en cours) car appelee comme fonction globale Jinja, sans acces a
    la dependance Depends(obtenir_session) de la route affichee.
    """
    db = SessionLocal()
    try:
        clients = db.query(models.Client).order_by(models.Client.nom).all()
        return json.dumps([{"id": c.id, "nom": c.nom} for c in clients]).replace("</", "<\\/")
    finally:
        db.close()


templates.env.globals["clients_json_recherche_globale"] = _clients_json_recherche_globale

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
# Limite aux 5 premiers jours du mois (pas tout le mois) : le job reste
# idempotent (EnvoiRecap) et tourne chaque jour dans cette fenetre, donc un
# echec un jour donne (token expire, etc.) est retente le lendemain sans
# intervention manuelle - mais un client devenant eligible APRES cette
# fenetre (email ajoute le 15, par exemple) n'a pas ete rattrape et recoit un
# recap du mois precedent avec deux semaines de retard, sujet+contenu ne
# mentionnant pas ce delai. Borner la fenetre evite ce cas : ce client
# recevra son premier recap au debut du mois suivant, comme les autres.
planificateur.add_job(
    envoyer_recaps_mensuels,
    "cron",
    day="1-5",
    hour=8,
    timezone="Europe/Brussels",
    id="recap_mensuel",
)
# Solde DataForSEO affiche dans la barre laterale : rafraichi peu apres le
# demarrage (next_run_time proche mais pas immediat, pour ne pas retarder le
# tout premier chargement de page) puis toutes les 6h - jamais a la volee au
# chargement d'une page (voir soldes_api.py).
planificateur.add_job(
    soldes_api.rafraichir_solde_dataforseo,
    "interval",
    hours=6,
    id="solde_dataforseo",
    next_run_time=datetime.now() + timedelta(seconds=5),
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

    request.session["utilisateur_en_attente_2fa"] = utilisateur.id
    if utilisateur.totp_secret:
        return RedirectResponse("/connexion/code", status_code=303)

    request.session["secret_2fa_configuration"] = deux_facteurs.generer_secret()
    return RedirectResponse("/connexion/configurer-2fa", status_code=303)


def _utilisateur_en_attente_2fa(request: Request, db: Session):
    id_utilisateur = request.session.get("utilisateur_en_attente_2fa")
    if not id_utilisateur:
        return None
    return db.query(models.Utilisateur).filter_by(id=id_utilisateur).first()


def _regenerer_codes_recuperation(db: Session, utilisateur) -> list[str]:
    """Invalide les anciens codes de secours et en genere un nouveau lot."""
    db.query(models.CodeRecuperation2FA).filter_by(utilisateur_id=utilisateur.id).delete()
    codes = deux_facteurs.generer_codes_recuperation()
    for code in codes:
        db.add(models.CodeRecuperation2FA(utilisateur_id=utilisateur.id, code_hash=hacher_mot_de_passe(code)))
    db.commit()
    return codes


@app.get("/connexion/configurer-2fa", response_class=HTMLResponse)
def page_configurer_2fa(request: Request, db: Session = Depends(obtenir_session)):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    secret = request.session.get("secret_2fa_configuration")
    if not utilisateur or not secret:
        return RedirectResponse("/connexion", status_code=303)
    uri = deux_facteurs.uri_provisionnement(secret, utilisateur.identifiant)
    return templates.TemplateResponse(
        request,
        "connexion_2fa_configurer.html",
        {
            "erreur": None,
            "secret": secret,
            "qr_code_data_uri": deux_facteurs.qr_code_data_uri(uri),
        },
    )


@app.post("/connexion/configurer-2fa")
def valider_configuration_2fa(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    secret = request.session.get("secret_2fa_configuration")
    if not utilisateur or not secret:
        return RedirectResponse("/connexion", status_code=303)

    if not deux_facteurs.code_valide(secret, code):
        uri = deux_facteurs.uri_provisionnement(secret, utilisateur.identifiant)
        return templates.TemplateResponse(
            request,
            "connexion_2fa_configurer.html",
            {
                "erreur": "Code incorrect, reessaie.",
                "secret": secret,
                "qr_code_data_uri": deux_facteurs.qr_code_data_uri(uri),
            },
            status_code=401,
        )

    utilisateur.totp_secret = secret
    codes_recuperation = _regenerer_codes_recuperation(db, utilisateur)
    request.session.pop("secret_2fa_configuration", None)
    request.session.pop("utilisateur_en_attente_2fa", None)
    request.session["user_id"] = utilisateur.id
    request.session["codes_recuperation_a_afficher"] = codes_recuperation
    return RedirectResponse("/connexion/codes-recuperation", status_code=303)


@app.get("/connexion/codes-recuperation", response_class=HTMLResponse)
def page_codes_recuperation(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    codes = request.session.pop("codes_recuperation_a_afficher", None)
    if not codes:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "connexion_2fa_codes_recuperation.html", {"codes": codes})


@app.get("/connexion/code", response_class=HTMLResponse)
def page_code_2fa(request: Request, db: Session = Depends(obtenir_session)):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    if not utilisateur or not utilisateur.totp_secret:
        return RedirectResponse("/connexion", status_code=303)
    return templates.TemplateResponse(request, "connexion_2fa_code.html", {"erreur": None})


@app.post("/connexion/code")
def valider_code_2fa(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    if not utilisateur or not utilisateur.totp_secret:
        return RedirectResponse("/connexion", status_code=303)

    if not deux_facteurs.code_valide(utilisateur.totp_secret, code):
        return templates.TemplateResponse(
            request,
            "connexion_2fa_code.html",
            {"erreur": "Code incorrect, reessaie."},
            status_code=401,
        )

    request.session.pop("utilisateur_en_attente_2fa", None)
    request.session["user_id"] = utilisateur.id
    return RedirectResponse("/", status_code=303)


@app.get("/connexion/recuperation", response_class=HTMLResponse)
def page_recuperation_2fa(request: Request, db: Session = Depends(obtenir_session)):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    if not utilisateur or not utilisateur.totp_secret:
        return RedirectResponse("/connexion", status_code=303)
    return templates.TemplateResponse(request, "connexion_2fa_recuperation.html", {"erreur": None})


@app.post("/connexion/recuperation")
def valider_recuperation_2fa(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    utilisateur = _utilisateur_en_attente_2fa(request, db)
    if not utilisateur or not utilisateur.totp_secret:
        return RedirectResponse("/connexion", status_code=303)

    code_normalise = deux_facteurs.normaliser_code_recuperation(code)
    correspondance = None
    for ligne in db.query(models.CodeRecuperation2FA).filter_by(utilisateur_id=utilisateur.id, utilise=False):
        if verifier_mot_de_passe(code_normalise, ligne.code_hash):
            correspondance = ligne
            break

    if not correspondance:
        return templates.TemplateResponse(
            request,
            "connexion_2fa_recuperation.html",
            {"erreur": "Code invalide ou déjà utilisé."},
            status_code=401,
        )

    correspondance.utilise = True
    db.commit()
    request.session.pop("utilisateur_en_attente_2fa", None)
    request.session["user_id"] = utilisateur.id
    return RedirectResponse("/", status_code=303)


@app.get("/parametres/securite", response_class=HTMLResponse)
def page_securite(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    utilisateur = db.query(models.Utilisateur).filter_by(id=utilisateur_connecte(request)).first()
    nb_codes_restants = (
        db.query(models.CodeRecuperation2FA)
        .filter_by(utilisateur_id=utilisateur.id, utilise=False)
        .count()
    )
    return templates.TemplateResponse(
        request,
        "securite.html",
        {
            "page_actuelle": "securite",
            "nb_codes_restants": nb_codes_restants,
        },
    )


@app.post("/parametres/securite/regenerer-codes")
def regenerer_codes_recuperation(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    utilisateur = db.query(models.Utilisateur).filter_by(id=utilisateur_connecte(request)).first()
    request.session["codes_recuperation_a_afficher"] = _regenerer_codes_recuperation(db, utilisateur)
    return RedirectResponse("/connexion/codes-recuperation", status_code=303)


@app.post("/parametres/securite/reinitialiser-2fa")
def reinitialiser_2fa(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    utilisateur = db.query(models.Utilisateur).filter_by(id=utilisateur_connecte(request)).first()
    utilisateur.totp_secret = None
    db.query(models.CodeRecuperation2FA).filter_by(utilisateur_id=utilisateur.id).delete()
    db.commit()
    return RedirectResponse("/parametres/securite", status_code=303)


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


def _parser_date_iso_calendrier(chaine: str):
    if not chaine:
        return None
    try:
        return datetime.fromisoformat(chaine.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _donnees_calendrier(request: Request, db: Session, client: models.Client, posts_en_ligne: list = None) -> dict:
    """
    Grille du mois (calendrier de contenu, affiche directement sur la fiche
    client). posts_en_ligne (voir _posts_en_ligne_pour_client, sans limite) :
    complete la grille avec les posts publies directement sur Google (hors
    plateforme), pour ne pas les rendre invisibles ici - Google ne les garde
    accessibles par l'API qu'environ 7 jours, donc uniquement pertinent pour
    le mois courant/recent.
    """
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

    if posts_en_ligne:
        ids_deja_suivis = {
            id_google for (id_google,) in db.query(models.Post.id_post_google)
            .filter(models.Post.client_id == client.id, models.Post.id_post_google.isnot(None))
            .all()
            if id_google
        }
        for post_google in posts_en_ligne:
            if post_google.get("id_post_google") in ids_deja_suivis:
                continue
            jour = _parser_date_iso_calendrier(post_google.get("date_creation_brute", ""))
            if not jour or not (premier_jour_grille <= jour <= dernier_jour_grille):
                continue
            posts.append(SimpleNamespace(
                id=None,
                titre=(post_google.get("texte", "").strip()[:80] or "(sans titre)"),
                type_post="STANDARD",
                statut="PUBLIE_LIVE",
                date_prevue=jour,
                url_recherche=post_google.get("url_recherche", ""),
                hors_plateforme=True,
            ))

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
def liste_avis(request: Request, etiquette_id: int = None, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    espace = db.get(models.Etiquette, etiquette_id) if etiquette_id else None

    google_connecte = google_oauth.google_est_connecte(db)
    clients = []
    if google_connecte:
        base = db.query(models.Client).filter(
            models.Client.account_id != "", models.Client.location_id != ""
        )
        if espace:
            base = base.filter(models.Client.etiquettes.any(models.Etiquette.id == espace.id))
        else:
            base = base.filter(~models.Client.etiquettes.any(models.Etiquette.isolee == True))  # noqa: E712
        clients = base.order_by(models.Client.nom).all()

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
            "espace": espace,
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


@app.get("/avis/comparatif", response_class=HTMLResponse)
def avis_comparatif_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    """
    Statistiques d'avis comparees sur plusieurs fiches (typiquement toutes les
    fiches d'un meme client final, regroupees par etiquette) : total, moyenne
    globale, classement par fiche, evolution jour par jour sur une periode.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    debut, fin = _periode_depuis_requete(request)
    return templates.TemplateResponse(
        request,
        "avis_comparatif.html",
        {
            "etiquettes": etiquettes,
            "clients_json": _clients_json_avec_etiquettes(db),
            "debut": debut,
            "fin": fin,
        },
    )


@app.get("/avis/comparatif/donnees/{client_id}")
def avis_comparatif_donnees_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Renvoie l'historique COMPLET des avis d'un client (toutes les pages, pas
    seulement les 50 premiers comme /avis/donnees) - necessaire pour un total
    et une moyenne globale exacts sur les statistiques comparatives.
    """
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
        avis = google_reviews.lister_avis_complet_client(identifiants, client)
        return JSONResponse({"avis": avis, "erreur": None})
    except Exception as erreur:
        return JSONResponse({"avis": [], "erreur": f"{client.nom} : {erreur}"})


@app.post("/avis/comparatif/enregistrer")
async def enregistrer_comparatif_avis(request: Request, db: Session = Depends(obtenir_session)):
    """
    Enregistre en base le resultat (deja calcule cote navigateur, voir
    avis_comparatif.html) d'un comparatif genere - snapshot fige, pas
    recalcule a la consultation - pour alimenter /avis/comparatif/historique
    et permettre le telechargement du PDF sans re-interroger Google.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    corps = await request.json()
    try:
        date_debut = date.fromisoformat(corps["date_debut"])
        date_fin = date.fromisoformat(corps["date_fin"])
    except (KeyError, ValueError, TypeError):
        return JSONResponse({"erreur": "Dates invalides."}, status_code=400)

    comparatif = models.ComparatifAvis(
        libelle=(corps.get("libelle") or "").strip()[:200],
        date_debut=date_debut,
        date_fin=date_fin,
        donnees_json=json.dumps(corps.get("donnees") or {}),
    )
    db.add(comparatif)
    db.commit()
    return JSONResponse({"id": comparatif.id})


@app.get("/avis/comparatif/historique", response_class=HTMLResponse)
def historique_comparatifs_avis(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comparatifs = db.query(models.ComparatifAvis).order_by(models.ComparatifAvis.cree_le.desc()).all()
    lignes = []
    for c in comparatifs:
        donnees = json.loads(c.donnees_json)
        lignes.append({
            "id": c.id,
            "libelle": c.libelle or "Sans nom",
            "date_debut": c.date_debut,
            "date_fin": c.date_fin,
            "cree_le": c.cree_le,
            "total_periode": donnees.get("total_periode", 0),
            "moyenne_periode": donnees.get("moyenne_periode"),
        })
    return templates.TemplateResponse(request, "avis_comparatif_historique.html", {"comparatifs": lignes})


@app.get("/avis/comparatif/{comparatif_id}", response_class=HTMLResponse)
def voir_comparatif_avis(comparatif_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comparatif = db.get(models.ComparatifAvis, comparatif_id)
    if not comparatif:
        return HTMLResponse("Comparatif introuvable.", status_code=404)

    donnees = json.loads(comparatif.donnees_json)
    return templates.TemplateResponse(
        request,
        "avis_comparatif_detail.html",
        {
            "comparatif": comparatif,
            "donnees": donnees,
            "donnees_json": json.dumps(donnees).replace("</", "<\\/"),
        },
    )


@app.get("/avis/comparatif/{comparatif_id}/pdf")
def telecharger_comparatif_avis_pdf(comparatif_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comparatif = db.get(models.ComparatifAvis, comparatif_id)
    if not comparatif:
        return HTMLResponse("Comparatif introuvable.", status_code=404)

    octets_pdf = comparatif_avis_pdf.generer_comparatif_pdf(
        comparatif.libelle, comparatif.date_debut, comparatif.date_fin, json.loads(comparatif.donnees_json)
    )
    nom_fichier = f"comparatif_avis_{comparatif.date_debut}_{comparatif.date_fin}.pdf"
    return Response(
        content=octets_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


@app.post("/avis/comparatif/{comparatif_id}/supprimer")
def supprimer_comparatif_avis(comparatif_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comparatif = db.get(models.ComparatifAvis, comparatif_id)
    if comparatif:
        db.delete(comparatif)
        db.commit()
    return RedirectResponse("/avis/comparatif/historique", status_code=303)


@app.get("/completude/donnees/{client_id}")
def completude_donnees_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Renvoie en JSON le score de completude d'un seul client, pour le chargement progressif cote navigateur."""
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client or not client.account_id or not client.location_id:
        return JSONResponse({"resultat": None, "erreur": None})

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return JSONResponse({
            "resultat": None,
            "erreur": f"{client.nom} : compte Google non valide (a reconnecter depuis Comptes Google).",
        })

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
        resultat = google_location.score_completude(infos)
        resultat["client_id"] = client.id
        resultat["client_nom"] = client.nom
        resultat["fiche_validee"] = google_location.fiche_validee(infos)
        return JSONResponse({"resultat": resultat, "erreur": None})
    except Exception as erreur:
        return JSONResponse({"resultat": None, "erreur": f"{client.nom} : {erreur}"})


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
                "type_appel_action": "CALL", "url_appel_action": "",
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


@app.post("/posts/generer_generique_masse")
def generer_posts_generiques_route(
    request: Request,
    theme: str = Form(""),
    client_reference_id: str = Form(""),
    nombre_posts: int = Form(5),
    db: Session = Depends(obtenir_session),
):
    """Variante en lot de /posts/generer_generique : plusieurs propositions parmi lesquelles choisir."""
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    contenu_reference = ""
    if client_reference_id.strip():
        client_reference = db.get(models.Client, int(client_reference_id))
        if client_reference:
            contenu_reference = _contexte_ia_client(client_reference)

    try:
        posts_generes = claude_generation.generer_posts_generiques(theme, contenu_reference, nombre_posts)
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=500)

    return JSONResponse({"posts": posts_generes})


@app.post("/posts/generer_masse_et_creer_lots")
async def generer_masse_et_creer_lots(request: Request, db: Session = Depends(obtenir_session)):
    """
    Genere plusieurs posts differents avec l'IA et cree un lot (un exemplaire
    par client selectionne, voir creer_posts_multi) pour CHACUN - contrairement
    a /posts/generer_generique_masse (une seule proposition choisie parmi
    plusieurs), ici tous les posts generes sont bien crees et programmables.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    theme = formulaire.get("theme", "").strip()
    client_reference_id = formulaire.get("client_reference_id", "").strip()
    try:
        nombre_posts = int(formulaire.get("nombre_posts", "5"))
    except ValueError:
        nombre_posts = 5
    client_ids = [int(v) for v in formulaire.getlist("client_ids") if v.strip()]

    if not client_ids:
        return _reponse_posts_multi(request, db, erreur="Selectionnez au moins un client.")

    contenu_reference = ""
    if client_reference_id:
        client_reference = db.get(models.Client, int(client_reference_id))
        if client_reference:
            contenu_reference = _contexte_ia_client(client_reference)

    try:
        posts_generes = claude_generation.generer_posts_generiques(theme, contenu_reference, nombre_posts)
    except Exception as erreur:
        return _reponse_posts_multi(request, db, erreur=str(erreur))

    clients_valides = [db.get(models.Client, cid) for cid in client_ids]
    clients_valides = [c for c in clients_valides if c]

    lot_ids = []
    for post_genere in posts_generes:
        lot_id = uuid.uuid4().hex[:12]
        for client in clients_valides:
            db.add(models.Post(
                client_id=client.id,
                titre=post_genere.get("titre", ""),
                texte=post_genere.get("texte", ""),
                prompt_image=post_genere.get("prompt_image", ""),
                statut="BROUILLON",
                lot_id=lot_id,
            ))
        lot_ids.append(lot_id)
    db.commit()

    return RedirectResponse(f"/posts/lots-generes?lots={','.join(lot_ids)}", status_code=303)


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


@app.get("/posts/lots-generes", response_class=HTMLResponse)
def lots_generes(request: Request, lots: str = "", db: Session = Depends(obtenir_session)):
    """
    Page de relecture apres /posts/generer_masse_et_creer_lots : un lot par
    post genere, chacun avec sa propre image/date/heure/CTA a valider
    independamment (les autres exemplaires du meme lot, un par client
    selectionne, suivent automatiquement une fois le lot valide).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    lot_ids = [l.strip() for l in lots.split(",") if l.strip()]
    groupes = []
    ids_clients_du_groupe = set()
    for lot_id in lot_ids:
        posts = db.query(models.Post).filter(models.Post.lot_id == lot_id).all()
        posts_brouillon = [p for p in posts if p.statut == "BROUILLON"]
        if not posts:
            continue
        premier = posts[0]
        groupes.append({
            "lot_id": lot_id,
            "titre": premier.titre,
            "texte": premier.texte,
            "prompt_image": premier.prompt_image,
            "image_url": premier.image_url,
            "statut": premier.statut,
            "nb_fiches": len(posts),
            "en_attente": len(posts_brouillon) > 0,
        })
        ids_clients_du_groupe.update(p.client_id for p in posts)

    clients_du_groupe = (
        db.query(models.Client)
        .filter(models.Client.id.in_(ids_clients_du_groupe))
        .order_by(models.Client.nom)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "posts_lots_generes.html",
        {
            "lots_param": lots,
            "groupes": groupes,
            "clients_du_groupe": clients_du_groupe,
            "options_appel_action": google_publish.OPTIONS_APPEL_ACTION,
        },
    )


def _tous_posts_du_lot(db: Session, lot_id: str) -> list:
    return db.query(models.Post).filter(models.Post.lot_id == lot_id).all()


def _redirection_apres_action_lot(lot_id: str, lots_param: str) -> RedirectResponse:
    parametres = lots_param if lots_param else lot_id
    return RedirectResponse(f"/posts/lots-generes?lots={parametres}#lot-{lot_id}", status_code=303)


@app.post("/posts/lot/{lot_id}/generer_image_masse")
def generer_image_lot(
    lot_id: str, request: Request, lots_param: str = Form(""), db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    posts = _tous_posts_du_lot(db, lot_id)
    if not posts:
        return HTMLResponse("Lot introuvable.", status_code=404)
    if not posts[0].prompt_image.strip():
        return HTMLResponse("Aucun prompt image renseigne pour ce lot.", status_code=400)

    try:
        octets_image = gemini_images.generer_image(posts[0].prompt_image)
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        nom_fichier = f"lot_{lot_id}_{horodatage}.png"
        url_publique = ovh_upload.envoyer_octets(octets_image, nom_fichier)
    except Exception as erreur:
        return HTMLResponse(f"Erreur lors de la generation de l'image : {erreur}", status_code=500)

    for post in posts:
        post.image_url = url_publique
    db.commit()

    return _redirection_apres_action_lot(lot_id, lots_param)


@app.post("/posts/lot/{lot_id}/televerser_image_masse")
async def televerser_image_lot(
    lot_id: str, request: Request, db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    lots_param = formulaire.get("lots_param", "")
    fichier = formulaire.get("fichier")

    posts = _tous_posts_du_lot(db, lot_id)
    if not posts:
        return HTMLResponse("Lot introuvable.", status_code=404)
    if fichier is None or not getattr(fichier, "filename", ""):
        return HTMLResponse("Aucun fichier fourni.", status_code=400)

    try:
        octets = await fichier.read()
        extension = os.path.splitext(fichier.filename)[1] or ".jpg"
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        url_publique = ovh_upload.envoyer_octets(octets, f"lot_{lot_id}_{horodatage}{extension}")
    except Exception as erreur:
        return HTMLResponse(f"Erreur lors du televersement : {erreur}", status_code=500)

    for post in posts:
        post.image_url = url_publique
    db.commit()

    return _redirection_apres_action_lot(lot_id, lots_param)


@app.post("/posts/lot/{lot_id}/choisir_image_fiche_masse")
def choisir_image_fiche_lot(
    lot_id: str, request: Request, image_url: str = Form(...), lots_param: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    posts = _tous_posts_du_lot(db, lot_id)
    if not posts:
        return HTMLResponse("Lot introuvable.", status_code=404)

    for post in posts:
        post.image_url = image_url
    db.commit()

    return _redirection_apres_action_lot(lot_id, lots_param)


@app.get("/posts/lots-generes/photos-client/{client_id}")
def photos_client_pour_lot(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Chargement a la demande des photos d'UN client du groupe (JSON), pour eviter d'interroger l'API Google pour chaque client du groupe au chargement de la page."""
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    try:
        photos = _photos_pour_client(db, client)
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=500)

    return JSONResponse({"photos": photos})


@app.post("/posts/lot/{lot_id}/statut_rapide_masse")
async def statut_rapide_lot(lot_id: str, request: Request, db: Session = Depends(obtenir_session)):
    """
    Equivalent, pour un lot genere en masse, de /posts/{post_id}/statut_rapide :
    valide (avec date/heure/CTA) ou rejette d'un coup tous les exemplaires du
    lot (un par client selectionne), sans passer par chaque post individuellement.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    posts = _tous_posts_du_lot(db, lot_id)
    if not posts:
        return HTMLResponse("Lot introuvable.", status_code=404)

    formulaire = await request.form()
    statut = formulaire.get("statut", "")
    if statut not in ("A_PUBLIER", "IGNORE"):
        return HTMLResponse("Statut invalide.", status_code=400)

    for post in posts:
        post.statut = statut
        if statut == "A_PUBLIER":
            date_prevue = formulaire.get("date_prevue", "")
            post.date_prevue = date.fromisoformat(date_prevue) if date_prevue.strip() else None
            post.heure_prevue = _heure_depuis_formulaire(formulaire)
            type_appel_action = formulaire.get("type_appel_action", "")
            post.type_appel_action = type_appel_action
            post.url_appel_action = (
                formulaire.get("url_appel_action", "").strip()
                if type_appel_action and type_appel_action != "CALL"
                else ""
            )
    db.commit()

    return _redirection_apres_action_lot(lot_id, formulaire.get("lots_param", ""))


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
def liste_clients(request: Request, etiquette_id: int = None, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    espace, clients = _resoudre_espace_et_clients(db, etiquette_id)

    ids_avec_connaissance = {
        client_id
        for (client_id,) in db.query(models.DocumentConnaissance.client_id).distinct().all()
    }
    return templates.TemplateResponse(
        request,
        "clients_liste.html",
        {
            "clients": clients,
            "google_connecte": google_oauth.google_est_connecte(db),
            "ids_avec_connaissance": ids_avec_connaissance,
            "nb_sans_email": sum(1 for c in clients if not c.email),
            "nb_sans_prenom": sum(1 for c in clients if not c.prenom),
            "nb_sans_connaissance": sum(1 for c in clients if c.id not in ids_avec_connaissance),
            "espace": espace,
        },
    )


@app.get("/clients/export.xlsx")
def exporter_clients_excel(request: Request, etiquette_id: int = None, db: Session = Depends(obtenir_session)):
    """
    Export Excel des clients (accueil, ou un seul espace si etiquette_id est
    fourni) avec les donnees disponibles gratuitement via les API Google deja
    utilisees ailleurs dans la plateforme - jamais de donnee payante
    (positions/DataForSEO). Peut prendre un moment sur beaucoup de fiches (3
    appels Google par fiche), comme le rapport PDF ou le bilan.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    espace, clients = _resoudre_espace_et_clients(db, etiquette_id)

    octets = export_clients_excel.generer_export(db, clients)
    suffixe = espace.nom.replace(" ", "_") if espace else "tous"
    nom_fichier = f"clients_{suffixe}_{date.today().isoformat()}.xlsx"
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


# Seuil d'inactivite (en jours) au-dela duquel une fiche sans nouveau post
# publie (via cette plateforme) remonte dans les alertes.
SEUIL_INACTIVITE_POSTS_JOURS = 30


@app.get("/alertes", response_class=HTMLResponse)
def alertes(request: Request, etiquette_id: int = None, db: Session = Depends(obtenir_session)):
    """
    Tableau de bord regroupant deux signaux gratuits (pas d'appel DataForSEO
    payant ici) : fiches sans post recent publie via la plateforme, et avis
    negatifs sans reponse (ces derniers sont charges cote navigateur, comme
    sur la page Avis, pour ne pas bloquer le chargement de la page le temps
    d'interroger l'API Google pour chaque client).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    espace = db.get(models.Etiquette, etiquette_id) if etiquette_id else None
    if espace:
        base = db.query(models.Client).filter(models.Client.etiquettes.any(models.Etiquette.id == espace.id))
    else:
        base = _query_clients_non_isoles(db)
    clients_avec_fiche = [
        c for c in base.order_by(models.Client.nom).all()
        if c.account_id and c.location_id
    ]

    derniers_posts = dict(
        db.query(models.Post.client_id, func.max(models.EvenementPublication.horodatage))
        .join(models.EvenementPublication, models.EvenementPublication.post_id == models.Post.id)
        .filter(models.EvenementPublication.etat == "LIVE")
        .group_by(models.Post.client_id)
        .all()
    )

    seuil = datetime.utcnow() - timedelta(days=SEUIL_INACTIVITE_POSTS_JOURS)
    clients_inactifs = []
    for client in clients_avec_fiche:
        dernier = derniers_posts.get(client.id)
        if not dernier or dernier < seuil:
            clients_inactifs.append({
                "client": client,
                "nb_jours": (datetime.utcnow() - dernier).days if dernier else None,
            })
    # Les plus preoccupants en premier : jamais publie, puis les plus anciens.
    clients_inactifs.sort(key=lambda c: c["nb_jours"] if c["nb_jours"] is not None else float("inf"), reverse=True)

    return templates.TemplateResponse(
        request,
        "alertes.html",
        {
            "clients_json": json.dumps(
                [{"id": c.id, "nom": c.nom} for c in clients_avec_fiche]
            ).replace("</", "<\\/"),
            "clients_inactifs": clients_inactifs,
            "seuil_inactivite_jours": SEUIL_INACTIVITE_POSTS_JOURS,
            "espace": espace,
        },
    )


@app.get("/clients/nouveau", response_class=HTMLResponse)
def nouveau_client_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    google_connecte = google_oauth.google_est_connecte(db)
    fiches = _fiches_google_non_liees(db) if google_connecte else []

    return templates.TemplateResponse(
        request,
        "client_nouveau.html",
        {
            "fiches": fiches,
            "google_connecte": google_connecte,
            "erreur": None,
            "toutes_etiquettes_json": _toutes_etiquettes_json(db),
        },
    )


@app.post("/clients/nouveau")
def creer_client(
    request: Request,
    nom: str = Form(...),
    contenu_site: str = Form(""),
    consignes_avis: str = Form(""),
    fiche_google: str = Form(""),
    etiquettes: list[str] = Form(default=[]),
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
    client.etiquettes = _obtenir_ou_creer_etiquettes(db, etiquettes)
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


def _fiches_google_non_liees(db: Session) -> list[dict]:
    """Fiches Google (tous comptes connectes confondus) pas encore associees a un client."""
    comptes_avec_identifiants = [
        (compte.id, compte.libelle, google_oauth.obtenir_identifiants(db, compte.id))
        for compte in google_oauth.lister_comptes(db)
    ]
    comptes_avec_identifiants = [c for c in comptes_avec_identifiants if c[2] is not None]
    fiches = google_business.lister_fiches_multi_comptes(comptes_avec_identifiants)

    fiches_deja_liees = {
        (c.compte_google_id, c.account_id, c.location_id)
        for c in db.query(models.Client).filter(models.Client.location_id != "").all()
    }
    fiches = [
        f for f in fiches
        if (f["compte_google_id"], f["account_id"], f["location_id"]) not in fiches_deja_liees
    ]
    fiches.sort(key=lambda f: f["nom_fiche"].lower())
    return fiches


def _query_clients_non_isoles(db: Session):
    """
    Clients n'appartenant a aucune etiquette marquee "isolee" (espace separe,
    voir /espaces) - c'est la base des vues generales (accueil, avis,
    alertes) quand aucun ?etiquette=... n'est demande explicitement.
    """
    return db.query(models.Client).filter(~models.Client.etiquettes.any(models.Etiquette.isolee == True))  # noqa: E712


def _resoudre_espace_et_clients(db: Session, etiquette_id: int):
    """
    Utilise par les vues qui existent en version globale (clients non isoles)
    et en version scopee a un espace (?etiquette_id=...) : accueil, avis,
    alertes, export Excel. Renvoie (espace|None, clients tries par nom).
    """
    espace = db.get(models.Etiquette, etiquette_id) if etiquette_id else None
    if espace:
        clients = (
            db.query(models.Client)
            .filter(models.Client.etiquettes.any(models.Etiquette.id == espace.id))
            .order_by(models.Client.nom)
            .all()
        )
    else:
        clients = _query_clients_non_isoles(db).order_by(models.Client.nom).all()
    return espace, clients


def _toutes_etiquettes_json(db: Session) -> str:
    return json.dumps(
        [e.nom for e in db.query(models.Etiquette).order_by(models.Etiquette.nom).all()]
    ).replace("</", "<\\/")


@app.get("/clients/import-masse", response_class=HTMLResponse)
def import_masse_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    google_connecte = google_oauth.google_est_connecte(db)
    fiches = _fiches_google_non_liees(db) if google_connecte else []

    return templates.TemplateResponse(
        request,
        "clients_import_masse.html",
        {
            "fiches": fiches,
            "google_connecte": google_connecte,
            "resultats": None,
            "toutes_etiquettes_json": _toutes_etiquettes_json(db),
        },
    )


@app.post("/clients/import-masse")
async def creer_clients_masse(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    contenu_site = formulaire.get("contenu_site", "")
    consignes_avis = formulaire.get("consignes_avis", "")
    selection = [v.strip() for v in formulaire.getlist("selection") if v.strip()]
    # Memes etiquettes appliquees a tous les clients crees dans ce lot (voir
    # _obtenir_ou_creer_etiquettes, deja utilise par la modification d'un
    # client) : resolues une seule fois plutot qu'une fois par client.
    etiquettes_lot = _obtenir_ou_creer_etiquettes(db, formulaire.getlist("etiquettes"))

    # Le meme document est joint a chaque client cree : on extrait son texte
    # une seule fois plutot que de refaire l'extraction pour chacun.
    documents_extraits = []
    erreurs_documents = []
    for fichier in formulaire.getlist("fichiers"):
        if not getattr(fichier, "filename", ""):
            continue
        try:
            octets = await fichier.read()
            texte_extrait = documents.extraire_texte(fichier.filename, octets)
            documents_extraits.append((fichier.filename, texte_extrait))
        except Exception as erreur:
            erreurs_documents.append(f"{fichier.filename} : {erreur}")

    fiches_deja_liees = {
        (c.compte_google_id, c.account_id, c.location_id)
        for c in db.query(models.Client).filter(models.Client.location_id != "").all()
    }

    details_ignores = list(erreurs_documents)
    nb_crees = 0
    for cle in selection:
        compte_google_id_brut, _, reste = cle.partition("|")
        account_id, _, location_id = reste.partition("|")
        compte_google_id = int(compte_google_id_brut) if compte_google_id_brut else None

        if (compte_google_id, account_id, location_id) in fiches_deja_liees:
            details_ignores.append(f"{formulaire.get(f'nom__{cle}', cle)} : déjà associée à un client.")
            continue

        nom = formulaire.get(f"nom__{cle}", "").strip()
        if not nom:
            details_ignores.append(f"{cle} : nom vide.")
            continue

        client = models.Client(
            nom=nom, contenu_site=contenu_site, consignes_avis=consignes_avis,
            account_id=account_id, location_id=location_id, compte_google_id=compte_google_id,
        )
        client.etiquettes = etiquettes_lot
        db.add(client)
        db.flush()

        for nom_fichier, texte_extrait in documents_extraits:
            db.add(models.DocumentConnaissance(
                client_id=client.id, nom_fichier=nom_fichier, texte_extrait=texte_extrait,
            ))

        fiches_deja_liees.add((compte_google_id, account_id, location_id))
        nb_crees += 1

    db.commit()

    google_connecte = google_oauth.google_est_connecte(db)
    fiches = _fiches_google_non_liees(db) if google_connecte else []

    return templates.TemplateResponse(
        request,
        "clients_import_masse.html",
        {
            "fiches": fiches,
            "google_connecte": google_connecte,
            "resultats": {"crees": nb_crees, "details_ignores": details_ignores},
            "toutes_etiquettes_json": _toutes_etiquettes_json(db),
        },
    )


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


def _jours_occupes_client(db: Session, client_id: int, posts_en_ligne: list = None) -> str:
    """
    Toutes les dates (posts + photos) deja programmees ou publiees pour ce
    client, en JSON (liste de "AAAA-MM-JJ") - utilise par le calendrier
    personnalise (voir static/calendrier_champ.js) pour signaler visuellement
    les jours deja occupes avant de choisir une nouvelle date. posts_en_ligne
    (voir _posts_en_ligne_pour_client) : inclut aussi les posts publies
    directement sur Google, hors plateforme - sinon invisibles ici.
    """
    dates_posts = (
        db.query(models.Post.date_prevue)
        .filter(models.Post.client_id == client_id, models.Post.date_prevue.isnot(None), models.Post.statut != "SUPPRIME")
        .distinct()
        .all()
    )
    dates_photos = (
        db.query(models.PhotoFiche.date_prevue)
        .filter(models.PhotoFiche.client_id == client_id, models.PhotoFiche.date_prevue.isnot(None), models.PhotoFiche.statut != "SUPPRIME")
        .distinct()
        .all()
    )
    toutes_dates = {d.isoformat() for (d,) in dates_posts} | {d.isoformat() for (d,) in dates_photos}
    for post_google in posts_en_ligne or []:
        jour = _parser_date_iso_calendrier(post_google.get("date_creation_brute", ""))
        if jour:
            toutes_dates.add(jour.isoformat())
    return json.dumps(sorted(toutes_dates)).replace("</", "<\\/")


def _reponse_detail_client(
    request: Request, db: Session, client: models.Client, erreur_generation: str = None,
    erreur_photo: str = None, erreur_document: str = None, erreur_post_manuel: str = None, code: int = 200,
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
    toutes_etiquettes_json = _toutes_etiquettes_json(db)
    tous_posts_en_ligne = _posts_en_ligne_pour_client(db, client)
    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "client": client,
            "posts": posts,
            "posts_en_ligne": tous_posts_en_ligne[:5],
            "erreur_generation": erreur_generation,
            "photos": _photos_pour_client(db, client),
            "photos_en_preparation": photos_en_preparation,
            "categories_photo": [
                (valeur, google_business.LIBELLES_CATEGORIE_PHOTO.get(valeur, valeur))
                for valeur in google_business.CATEGORIES_PHOTO
            ],
            "erreur_photo": erreur_photo,
            "erreur_document": erreur_document,
            "erreur_post_manuel": erreur_post_manuel,
            "toutes_etiquettes_json": toutes_etiquettes_json,
            "jours_occupes_json": _jours_occupes_client(db, client.id, posts_en_ligne=tous_posts_en_ligne),
            "options_appel_action": google_publish.OPTIONS_APPEL_ACTION,
            **_donnees_calendrier(request, db, client, posts_en_ligne=tous_posts_en_ligne),
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


@app.post("/clients/{client_id}/posts/brouillons/programmer_masse")
def programmer_brouillons_masse(
    client_id: int,
    request: Request,
    date_debut: str = Form(...),
    intervalle_jours: int = Form(3),
    heure_mode: str = Form("0830"),
    heure_h: str = Form(""),
    heure_m: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    """
    Programme tous les brouillons d'un client en une seule action, avec des
    dates espacees automatiquement (ex : tous les 3 jours a partir de la date
    de depart) - evite d'ouvrir chaque post un par un pour lui donner sa date.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    try:
        premiere_date = date.fromisoformat(date_debut)
    except ValueError:
        return HTMLResponse("Date invalide.", status_code=400)

    intervalle_jours = max(1, intervalle_jours)
    heure_programmee = _heure_depuis_formulaire({"heure_mode": heure_mode, "heure_h": heure_h, "heure_m": heure_m})

    brouillons = (
        db.query(models.Post)
        .filter(models.Post.client_id == client_id, models.Post.statut == "BROUILLON")
        .order_by(models.Post.id)
        .all()
    )
    for index, post in enumerate(brouillons):
        post.date_prevue = premiere_date + timedelta(days=index * intervalle_jours)
        post.heure_prevue = heure_programmee
        post.statut = "A_PUBLIER"
    db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/posts/creer")
def creer_post_manuel(
    client_id: int, request: Request, titre: str = Form(""), texte: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    """Cree un post directement, sans passer par la generation IA."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not texte.strip():
        return _reponse_detail_client(
            request, db, client, erreur_post_manuel="Le texte du post ne peut pas etre vide.", code=400
        )

    db.add(models.Post(client_id=client.id, titre=titre.strip(), texte=texte, statut="BROUILLON"))
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


@app.post("/clients/{client_id}/photos/appliquer_masse")
def appliquer_masse_photos_client(
    client_id: int, request: Request,
    legende: str = Form(""), latitude: str = Form(""), longitude: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    """
    Applique la meme legende et/ou le meme geotag a toutes les photos
    actuellement en preparation (BROUILLON) pour ce client - evite de
    ressaisir photo par photo quand un import en masse partage les memes
    informations (ex : 28 photos d'une meme intervention). Un champ laisse
    vide n'ecrase pas ce qui existe deja sur chaque photo.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    valeurs = {}
    if legende.strip():
        valeurs["legende"] = legende.strip()
    if latitude.strip() and longitude.strip():
        try:
            valeurs["latitude"] = float(latitude.replace(",", "."))
            valeurs["longitude"] = float(longitude.replace(",", "."))
        except ValueError:
            return _reponse_detail_client(request, db, client, erreur_photo="Coordonnees GPS invalides.")

    if valeurs:
        db.query(models.PhotoFiche).filter(
            models.PhotoFiche.client_id == client_id, models.PhotoFiche.statut == "BROUILLON"
        ).update(valeurs)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/photos_google/supprimer")
async def supprimer_photo_fiche_google_route(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Supprime une photo directement sur la fiche Google (galerie "Deja sur la
    fiche", lue en direct - pas un PhotoFiche local). A la difference de
    /photos/{id}/supprimer ci-dessous, ceci retire vraiment la photo de la
    fiche publique, pas seulement du suivi local.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    formulaire = await request.form()
    nom_media = formulaire.get("nom_media", "").strip()
    if not nom_media:
        return JSONResponse({"erreur": "Photo introuvable."}, status_code=400)

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return JSONResponse(
            {"erreur": "Compte Google non valide pour ce client (a reconnecter depuis Comptes Google)."},
            status_code=400,
        )

    try:
        google_business.supprimer_photo_fiche_google(identifiants, nom_media)
    except Exception as erreur:
        return JSONResponse({"erreur": f"Echec de la suppression : {erreur}"}, status_code=500)

    return JSONResponse({"ok": True})


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
    client_id: int, request: Request,
    date_prevue: str = Form(...),
    taille_lot: int = Form(...),
    intervalle_jours: int = Form(1),
    db: Session = Depends(obtenir_session),
):
    """
    Programme l'envoi de toutes les photos actuellement en BROUILLON pour ce
    client, par lots espaces (ex : 10 photos tous les 3 jours) plutot que
    toutes a la meme date - utile pour un import en masse (ex : une
    trentaine de photos d'un coup) qu'on ne veut pas voir arriver toutes le
    meme jour sur la fiche Google.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    try:
        date_debut = date.fromisoformat(date_prevue)
    except ValueError:
        return _reponse_detail_client(request, db, client, erreur_photo="Date invalide.")

    taille_lot = max(1, taille_lot)
    intervalle_jours = max(1, intervalle_jours)

    photos = (
        db.query(models.PhotoFiche)
        .filter(models.PhotoFiche.client_id == client_id, models.PhotoFiche.statut == "BROUILLON")
        .order_by(models.PhotoFiche.id)
        .all()
    )
    for index, photo in enumerate(photos):
        groupe = index // taille_lot
        photo.date_prevue = date_debut + timedelta(days=groupe * intervalle_jours)
        photo.statut = "A_PUBLIER"
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
            "fiche_validee": google_location.fiche_validee(infos) if infos else None,
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


@app.get("/horaires-exceptionnelles", response_class=HTMLResponse)
def horaires_exceptionnelles_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    return templates.TemplateResponse(
        request,
        "horaires_exceptionnelles.html",
        {"etiquettes": etiquettes, "clients_json": _clients_json_avec_etiquettes(db), "resultats": None, "erreur": None},
    )


@app.post("/horaires-exceptionnelles/appliquer")
async def appliquer_horaires_exceptionnelles(request: Request, db: Session = Depends(obtenir_session)):
    """
    Applique une meme date exceptionnelle (fermeture ou horaires reduits) a
    plusieurs fiches d'un coup - utile pour les jours feries. Traite chaque
    fiche independamment (l'echec d'une fiche ne bloque pas les autres) et
    affiche un resultat detaille par fiche, puisqu'il s'agit d'une ecriture
    reelle sur des fiches Google en production.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    clients_json = _clients_json_avec_etiquettes(db)

    client_ids = [int(v) for v in formulaire.getlist("client_ids") if v.strip()]
    date_iso = formulaire.get("date", "").strip()
    ferme = bool(formulaire.get("ferme"))
    ouverture = formulaire.get("ouverture", "").strip()
    fermeture = formulaire.get("fermeture", "").strip()

    if not client_ids:
        return templates.TemplateResponse(
            request, "horaires_exceptionnelles.html",
            {"etiquettes": etiquettes, "clients_json": clients_json, "resultats": None,
             "erreur": "Selectionnez au moins une fiche."},
            status_code=400,
        )
    if not date_iso:
        return templates.TemplateResponse(
            request, "horaires_exceptionnelles.html",
            {"etiquettes": etiquettes, "clients_json": clients_json, "resultats": None,
             "erreur": "Choisissez une date."},
            status_code=400,
        )
    if not ferme and not (ouverture and fermeture):
        return templates.TemplateResponse(
            request, "horaires_exceptionnelles.html",
            {"etiquettes": etiquettes, "clients_json": clients_json, "resultats": None,
             "erreur": "Indiquez une heure d'ouverture et de fermeture, ou cochez «Fermé toute la journée»."},
            status_code=400,
        )

    nouvelle_periode = google_location.construire_periode_exceptionnelle(date_iso, ferme, ouverture, fermeture)

    resultats = []
    for client_id in client_ids:
        client = db.get(models.Client, client_id)
        if not client:
            continue
        entree = {"client": client, "succes": False, "erreur": None}
        if not client.account_id or not client.location_id:
            entree["erreur"] = "Pas de fiche Google associee."
            resultats.append(entree)
            continue
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            entree["erreur"] = "Compte Google non valide (a reconnecter depuis Comptes Google)."
            resultats.append(entree)
            continue
        try:
            infos_actuelles = google_location.obtenir_infos_fiche(identifiants, client.location_id)
            periodes = google_location.fusionner_horaires_exceptionnels(
                infos_actuelles.get("specialHours"), nouvelle_periode
            )
            google_location.mettre_a_jour_fiche(
                identifiants, client.location_id,
                {"specialHours": {"specialHourPeriods": periodes}}, ["specialHours"],
            )
            entree["succes"] = True
        except Exception as erreur:
            entree["erreur"] = str(erreur)
        resultats.append(entree)

    return templates.TemplateResponse(
        request,
        "horaires_exceptionnelles.html",
        {
            "etiquettes": etiquettes,
            "clients_json": clients_json,
            "resultats": resultats,
            "date_appliquee": date_iso,
            "erreur": None,
        },
    )


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


@app.get("/mots-cles", response_class=HTMLResponse)
def recherche_mots_cles(request: Request, q: str = "", db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    resultats, erreur = None, None
    idees_volume, erreur_volume = None, None
    ads_configure = google_oauth.ads_configure(db)
    if q.strip():
        try:
            resultats = google_autocomplete.rechercher(q)
        except Exception as e:
            erreur = f"Impossible de recuperer des suggestions pour le moment : {e}"

        if ads_configure:
            try:
                idees_volume = sorted(
                    google_ads_keywords.idees_mots_cles(google_oauth.obtenir_parametre_ads(db), [q]),
                    key=lambda i: i["volume_moyen_mensuel"] or 0,
                    reverse=True,
                )
            except Exception as e:
                erreur_volume = f"Impossible de recuperer les volumes Google Ads pour le moment : {e}"

    clients = db.query(models.Client).order_by(models.Client.nom).all()

    return templates.TemplateResponse(
        request,
        "recherche_mots_cles.html",
        {
            "q": q, "resultats": resultats, "erreur": erreur, "clients": clients,
            "ads_configure": ads_configure, "idees_volume": idees_volume, "erreur_volume": erreur_volume,
        },
    )


@app.post("/mots-cles/envoyer-positions")
def envoyer_mots_cles_positions(
    request: Request,
    client_id: int = Form(...),
    q: str = Form(""),
    mots_cles: list[str] = Form(default=[]),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    deja_suivis = {m.texte.strip().lower() for m in client.mots_cles}
    for texte in mots_cles:
        texte = texte.strip()
        if texte and texte.lower() not in deja_suivis:
            db.add(models.MotCle(client_id=client_id, texte=texte))
            deja_suivis.add(texte.lower())
    db.commit()

    return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)


def _derniers_resultats_visibilite_ia(db: Session, client_id: int) -> list:
    """Un seul resultat par (requete, modele) - le plus recent - pour l'affichage synthese."""
    tous = (
        db.query(models.ResultatVisibiliteIA)
        .filter_by(client_id=client_id)
        .order_by(models.ResultatVisibiliteIA.cree_le.desc())
        .all()
    )
    derniers_par_cle = {}
    for resultat in tous:
        cle = (resultat.requete_texte, resultat.modele)
        derniers_par_cle.setdefault(cle, resultat)
    return list(derniers_par_cle.values())


@app.get("/clients/{client_id}/visibilite-ia", response_class=HTMLResponse)
def visibilite_ia_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    derniers_resultats = []
    for resultat in _derniers_resultats_visibilite_ia(db, client_id):
        derniers_resultats.append({
            "requete_texte": resultat.requete_texte,
            "modele": resultat.modele,
            "client_cite": resultat.client_cite,
            "position": resultat.position,
            "concurrents_cites": json.loads(resultat.concurrents_cites or "[]"),
            "suggestion": resultat.suggestion,
            "erreur": resultat.erreur,
            "cree_le": resultat.cree_le.strftime("%d/%m/%Y %H:%M"),
        })

    return templates.TemplateResponse(
        request,
        "client_visibilite_ia.html",
        {
            "client": client,
            "requetes": client.requetes_visibilite_ia,
            "requetes_json": json.dumps([{"id": r.id, "texte": r.texte} for r in client.requetes_visibilite_ia]).replace("</", "<\\/"),
            "derniers_resultats_json": json.dumps(derniers_resultats).replace("</", "<\\/"),
            "modeles_disponibles": ia_visibilite.MODELES_DISPONIBLES,
            "modeles_disponibles_json": json.dumps(ia_visibilite.MODELES_DISPONIBLES).replace("</", "<\\/"),
            "openai_configure": bool(ia_visibilite.CLE_OPENAI),
            "gemini_configure": bool(ia_visibilite.CLE_GEMINI),
        },
    )


@app.post("/clients/{client_id}/visibilite-ia/requetes")
def ajouter_requete_visibilite_ia(
    client_id: int, request: Request, texte: str = Form(...), db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    if texte.strip():
        db.add(models.RequeteVisibiliteIA(client_id=client_id, texte=texte.strip()))
        db.commit()

    return RedirectResponse(f"/clients/{client_id}/visibilite-ia", status_code=303)


@app.post("/clients/{client_id}/visibilite-ia/requetes/{requete_id}/supprimer")
def supprimer_requete_visibilite_ia(
    client_id: int, requete_id: int, request: Request, db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    requete = db.get(models.RequeteVisibiliteIA, requete_id)
    if requete and requete.client_id == client_id:
        db.delete(requete)
        db.commit()

    return RedirectResponse(f"/clients/{client_id}/visibilite-ia", status_code=303)


@app.post("/clients/{client_id}/visibilite-ia/verifier-une")
def verifier_une_visibilite_ia(
    client_id: int, request: Request, requete_id: int = Form(...), modele: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    """
    Verifie UNE requete sur UN modele et enregistre le resultat - appelee en
    JS en boucle (une requete x deux modeles a la fois) pour permettre une
    barre de progression reelle, comme /avis/suggerer en boucle sur la page Avis.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    requete = db.get(models.RequeteVisibiliteIA, requete_id)
    if not client or not requete or requete.client_id != client_id or modele not in ia_visibilite.MODELES_DISPONIBLES:
        return JSONResponse({"erreur": "Requete invalide."}, status_code=400)

    resultat = ia_visibilite.verifier_une_requete(client.nom, modele, requete.texte)

    ligne = models.ResultatVisibiliteIA(
        client_id=client_id,
        requete_texte=requete.texte,
        modele=modele,
        client_cite=resultat["client_cite"],
        position=resultat["position"],
        concurrents_cites=json.dumps(resultat["concurrents_cites"]),
        suggestion=resultat["suggestion"],
        reponse_brute=resultat["reponse_brute"],
        erreur=resultat["erreur"],
    )
    db.add(ligne)
    db.commit()

    return JSONResponse({
        "resultat": {
            "requete_texte": ligne.requete_texte,
            "modele": ligne.modele,
            "client_cite": ligne.client_cite,
            "position": ligne.position,
            "concurrents_cites": resultat["concurrents_cites"],
            "suggestion": ligne.suggestion,
            "erreur": ligne.erreur,
            "cree_le": ligne.cree_le.strftime("%d/%m/%Y %H:%M"),
        }
    })


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


@app.get("/clients/{client_id}/avis/historique-mensuel")
def avis_historique_mensuel_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Historique des avis mois par mois (13 derniers mois, avec repartition par
    etoile) pour le graphique de la page Statistiques. Charge cote navigateur
    (voir client_stats.html) plutot qu'au rendu de la page : necessite de
    relire tout l'historique de la fiche (toutes_les_pages=True), potentiellement
    long sur une fiche tres commentee.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"historique": [], "erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client or not client.account_id or not client.location_id:
        return JSONResponse({"historique": [], "erreur": None})

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return JSONResponse({
            "historique": [],
            "erreur": "Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        })

    try:
        avis = google_reviews.lister_avis_complet_client(identifiants, client)
        historique = google_reviews.historique_mensuel(avis)
        return JSONResponse({"historique": historique, "erreur": None})
    except Exception as erreur:
        return JSONResponse({"historique": [], "erreur": str(erreur)})


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
        evolution_avis=donnees["evolution_avis"], sections=sections, fiche_validee=donnees["fiche_validee"],
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
    try:
        _sujet, html = rapport_donnees.construire_contenu_recap(db, client, mois, annee)
    except Exception as erreur:
        return HTMLResponse(f"Impossible de generer l'apercu : {erreur}", status_code=500)

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


@app.post("/clients/{client_id}/recap/basculer")
def basculer_recap_actif(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Active/desactive l'envoi automatique du recap mensuel pour ce client, sans toucher a son email."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if client:
        client.recap_actif = not client.recap_actif
        db.commit()
    return RedirectResponse("/recaps", status_code=303)


# --- Acces rapide aux positions ----------------------------------------------


@app.get("/positions", response_class=HTMLResponse)
def positions_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    """Raccourci menu vers l'onglet Positions d'une fiche, sans devoir d'abord ouvrir sa page client."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    clients = (
        db.query(models.Client)
        .filter(models.Client.account_id != "", models.Client.location_id != "")
        .order_by(models.Client.nom)
        .all()
    )
    return templates.TemplateResponse(request, "positions_index.html", {"clients": clients})


# --- Bilan ponctuel (PDF multi-fiches) ---------------------------------------


@app.get("/bilan", response_class=HTMLResponse)
def bilan_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    debut, fin = _periode_depuis_requete(request)
    return templates.TemplateResponse(
        request,
        "bilan.html",
        {"etiquettes": etiquettes, "clients_json": _clients_json_avec_etiquettes(db), "debut": debut, "fin": fin},
    )


@app.get("/bilan/pdf")
def telecharger_bilan_pdf(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client_ids = [int(v) for v in request.query_params.getlist("client_ids") if v.strip()]
    if not client_ids:
        return HTMLResponse("Selectionnez au moins une fiche.", status_code=400)

    debut, fin = _periode_depuis_requete(request)

    sections_clients = []
    for client_id in client_ids:
        client = db.get(models.Client, client_id)
        if not client or not client.account_id or not client.location_id:
            continue

        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            continue

        try:
            donnees = rapport_donnees.rassembler_donnees_rapport(db, client, debut, fin)
        except Exception:
            # Fiche en erreur (token expire, etc.) : on l'ignore plutot que
            # de faire echouer tout le bilan pour les autres fiches valides.
            continue

        avis_positifs = google_reviews.avis_positifs_periode(
            identifiants, client.account_id, client.location_id, debut, fin
        )
        resume_avis_texte = None
        if len(avis_positifs) > 1:
            try:
                resume_avis_texte = claude_generation.resumer_avis_positifs(avis_positifs)
            except Exception:
                resume_avis_texte = None

        sections_clients.append({
            "nom": client.nom,
            "donnees": donnees,
            "avis_positifs": avis_positifs,
            "resume_avis_texte": resume_avis_texte,
        })

    if not sections_clients:
        return HTMLResponse("Aucune fiche valide parmi la selection (fiche Google associee et compte valide requis).", status_code=400)

    octets_pdf = bilan_pdf.generer_bilan_pdf(sections_clients, debut, fin)
    nom_fichier = f"bilan_{debut.isoformat()}_{fin.isoformat()}.pdf"
    return Response(
        content=octets_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


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
    prenom: str = Form(""),
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
    client.prenom = prenom.strip()
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


@app.post("/clients/supprimer-masse")
def supprimer_clients_masse(
    request: Request, client_ids: list[int] = Form(default=[]), db: Session = Depends(obtenir_session)
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    # Suppression via l'ORM (pas une requete DELETE en masse) : necessaire
    # pour declencher les cascades (posts, photos, documents...) definies sur
    # les relations de Client, comme pour la suppression d'un seul client.
    for client in db.query(models.Client).filter(models.Client.id.in_(client_ids)).all():
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
    return templates.TemplateResponse(
        request, "google_comptes.html",
        {"comptes": comptes, "parametre_ads": google_oauth.obtenir_parametre_ads(db)},
    )


@app.post("/google-ads/parametres")
def enregistrer_parametres_ads(
    request: Request, developer_token: str = Form(...), customer_id: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    google_oauth.enregistrer_identifiants_ads(db, developer_token, customer_id)
    return RedirectResponse("/google/comptes", status_code=303)


@app.get("/google-ads/connecter")
def google_ads_connecter(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    redirect_uri = str(request.url_for("google_ads_callback"))
    flow = google_oauth.construire_flow_ads(redirect_uri)
    url_autorisation, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session["oauth_ads_state"] = state
    request.session["oauth_ads_code_verifier"] = flow.code_verifier
    return RedirectResponse(url_autorisation)


@app.get("/google-ads/callback", name="google_ads_callback")
def google_ads_callback(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    state_attendu = request.session.get("oauth_ads_state")
    if state_attendu and request.query_params.get("state") != state_attendu:
        return HTMLResponse("Etat OAuth invalide, merci de reessayer depuis /google-ads/connecter.", status_code=400)

    redirect_uri = str(request.url_for("google_ads_callback"))
    code_verifier = request.session.get("oauth_ads_code_verifier")
    flow = google_oauth.construire_flow_ads(redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=str(request.url))

    google_oauth.enregistrer_refresh_token_ads(db, flow.credentials.refresh_token)
    return RedirectResponse("/google/comptes", status_code=303)


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


# --- Espaces (etiquettes isolees des vues generales) ------------------------


@app.get("/espaces", response_class=HTMLResponse)
def liste_espaces(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes_isolees = (
        db.query(models.Etiquette).filter_by(isolee=True).order_by(models.Etiquette.nom).all()
    )
    espaces = [{"etiquette": e, "nb_clients": len(e.clients)} for e in etiquettes_isolees]

    return templates.TemplateResponse(request, "espaces.html", {"espaces": espaces})


# --- Etiquettes --------------------------------------------------------------


@app.get("/etiquettes", response_class=HTMLResponse)
def liste_etiquettes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    return templates.TemplateResponse(request, "etiquettes.html", {"etiquettes": etiquettes, "erreur": None})


@app.post("/etiquettes")
def creer_etiquette(
    request: Request, nom: str = Form(...), isolee: bool = Form(False), db: Session = Depends(obtenir_session)
):
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
        db.add(models.Etiquette(nom=nom, isolee=isolee))
        db.commit()

    if erreur:
        etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
        return templates.TemplateResponse(
            request, "etiquettes.html", {"etiquettes": etiquettes, "erreur": erreur}, status_code=400
        )
    return RedirectResponse("/etiquettes", status_code=303)


@app.post("/etiquettes/{etiquette_id}/isoler")
def basculer_isolement_etiquette(etiquette_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Active/desactive l'isolement d'une etiquette (voir _query_clients_non_isoles
    et /espaces) : ses clients sortent des vues generales (accueil, avis,
    alertes) pour n'apparaitre que dans leur propre espace.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquette = db.get(models.Etiquette, etiquette_id)
    if etiquette:
        etiquette.isolee = not etiquette.isolee
        db.commit()
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
    if not client.account_id or not client.location_id:
        return []
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return []

    photos = google_business.lister_photos(identifiants, client.account_id, client.location_id)
    for photo in photos:
        try:
            photo["date_publication_affichee"] = (
                datetime.fromisoformat(photo["date_publication"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
            )
        except (ValueError, KeyError):
            photo["date_publication_affichee"] = ""
    return photos


def _posts_en_ligne_pour_client(db: Session, client: models.Client, limite: int = None) -> list:
    """
    Les posts reellement presents sur la fiche Google (lecture directe, pas
    seulement ceux publies via cette plateforme - voir google_business.lister_posts).
    Google fait expirer ces posts de son API au bout d'environ 7 jours : cette
    liste ne remonte donc pas plus loin, meme sans limite explicite.
    """
    if not client.account_id or not client.location_id:
        return []
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return []
    try:
        posts = google_business.lister_posts(identifiants, client.account_id, client.location_id)
    except Exception:
        return []
    posts.sort(key=lambda p: p.get("date_creation_brute", ""), reverse=True)
    return posts[:limite] if limite else posts


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
            "jours_occupes_json": _jours_occupes_client(db, post.client_id, posts_en_ligne=_posts_en_ligne_pour_client(db, post.client)),
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


def _appliquer_formulaire_post(post: models.Post, formulaire) -> None:
    """
    Applique au post les champs du formulaire de relecture (post_detail.html).
    Partage entre modifier_post et publier_post_route pour que "Publier
    maintenant" tienne compte des modifications non explicitement enregistrees
    (ex. une photo choisie sur la fiche) plutot que de les perdre.
    """
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


@app.post("/posts/{post_id}/modifier")
async def modifier_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    formulaire = await request.form()
    _appliquer_formulaire_post(post, formulaire)
    db.commit()

    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/statut_rapide")
async def modifier_statut_rapide_post(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Validation/rejet rapide depuis la liste des posts d'une fiche (sans passer
    par la page de detail) : ne touche qu'au statut, et si valide, a la date
    de publication et au bouton d'appel a l'action - laisse tous les autres
    champs du post (texte, image...) intacts.
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
        type_appel_action = formulaire.get("type_appel_action", "")
        post.type_appel_action = type_appel_action
        post.url_appel_action = (
            formulaire.get("url_appel_action", "").strip()
            if type_appel_action and type_appel_action != "CALL"
            else ""
        )
    db.commit()

    return RedirectResponse(f"/clients/{post.client_id}#post-{post_id}", status_code=303)


HOTES_IMAGES_AUTORISES_PROXY = {"lh3.googleusercontent.com"}
_hote_ovh = urlparse(ovh_upload.URL_PUBLIQUE_BASE).hostname if ovh_upload.URL_PUBLIQUE_BASE else None
if _hote_ovh:
    HOTES_IMAGES_AUTORISES_PROXY.add(_hote_ovh)


@app.get("/image_proxy")
def image_proxy(request: Request, url: str):
    """
    Relaie une image (fiche Google ou stockage OVH) en meme origine que la
    plateforme, pour permettre son dessin sur un <canvas> (outil de recadrage)
    sans etre bloque par les CORS du domaine d'origine.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    url_analysee = urlparse(url)
    if url_analysee.scheme not in ("http", "https") or url_analysee.hostname not in HOTES_IMAGES_AUTORISES_PROXY:
        return Response(status_code=400)

    try:
        reponse = requests.get(url, timeout=10)
        reponse.raise_for_status()
    except Exception:
        return Response(status_code=404)

    return Response(content=reponse.content, media_type=reponse.headers.get("Content-Type", "image/jpeg"))


def _redirection_apres_image_post(post: "models.Post", retour: str) -> RedirectResponse:
    """retour="client" : reste sur la fiche client (workflow de generation en masse) plutot que d'ouvrir le post."""
    if retour == "client":
        return RedirectResponse(f"/clients/{post.client_id}#post-{post.id}", status_code=303)
    return RedirectResponse(f"/posts/{post.id}", status_code=303)


@app.post("/posts/{post_id}/choisir_image_fiche")
def choisir_image_fiche_post(
    post_id: int, request: Request, image_url: str = Form(...), retour: str = Form("post"),
    db: Session = Depends(obtenir_session),
):
    """Reprend directement une photo deja presente sur la fiche Google du client, sans televersement ni generation IA."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    post.image_url = image_url
    db.commit()

    return _redirection_apres_image_post(post, retour)


@app.post("/posts/{post_id}/generer_image")
def generer_image_post(post_id: int, request: Request, retour: str = Form("post"), db: Session = Depends(obtenir_session)):
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

    return _redirection_apres_image_post(post, retour)


@app.post("/posts/{post_id}/televerser_image")
def televerser_image_post(
    post_id: int, request: Request, fichier: UploadFile = File(...), retour: str = Form("post"),
    db: Session = Depends(obtenir_session),
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

    return _redirection_apres_image_post(post, retour)


@app.post("/posts/{post_id}/publier")
async def publier_post_route(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    # Le bouton "Publier maintenant" fait partie du meme formulaire que
    # "Enregistrer les modifications" (voir post_detail.html) : on applique
    # d'abord les champs du formulaire (ex. une photo choisie sur la fiche
    # mais pas encore enregistree) pour ne jamais publier un etat perime.
    formulaire = await request.form()
    _appliquer_formulaire_post(post, formulaire)
    db.commit()

    if not post.client.account_id or not post.client.location_id:
        return _reponse_post_detail(request, db, post, erreur="Ce client n'a pas de fiche Google associee.", code=400)

    identifiants = google_oauth.obtenir_identifiants(db, post.client.compte_google_id)
    if not identifiants:
        return _reponse_post_detail(request, db, post, erreur="Google n'est pas connecte.", code=400)

    try:
        google_publish.publier_et_verifier(db, identifiants, post)
    except Exception as erreur:
        return _reponse_post_detail(request, db, post, erreur=f"Erreur lors de la publication : {erreur}", code=500)

    return RedirectResponse(f"/clients/{post.client_id}#post-{post_id}", status_code=303)


@app.post("/posts/{post_id}/programmer")
async def programmer_post_route(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Enregistre le formulaire de relecture et programme le post (statut
    A_PUBLIER) pour la date/heure choisies - aucun appel a Google ici, c'est
    la tache planifiee qui publiera reellement au moment venu (voir
    planificateur.verifier_et_publier_posts_programmes). Evite l'etape
    supplementaire de "valider" a nouveau depuis la liste des posts.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    formulaire = await request.form()
    _appliquer_formulaire_post(post, formulaire)

    if not post.date_prevue:
        return _reponse_post_detail(
            request, db, post, erreur="Choisissez une date pour programmer ce post.", code=400
        )

    post.statut = "A_PUBLIER"
    db.commit()

    return RedirectResponse(f"/clients/{post.client_id}#post-{post_id}", status_code=303)
