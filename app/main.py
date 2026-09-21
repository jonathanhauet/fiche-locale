"""
Point d'entree de la plateforme web (FastAPI).

Lancement en local : depuis le dossier plateforme_web/,
    uvicorn app.main:app --reload
puis ouvrir http://localhost:8000
"""

import base64
import calendar
import io
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
from PIL import Image, ImageOps
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import (
    acces_masse,
    audit_backlinks,
    audit_prospect,
    audit_prospect_pdf,
    audit_site_technique,
    bilan_pdf,
    brevo_email,
    citations,
    claude_generation,
    comparatif_avis_pdf,
    deux_facteurs,
    documents,
    export_acces_excel,
    export_clients_excel,
    gemini_images,
    geocodage,
    google_admins,
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
    instagram_engagement,
    instagram_oauth,
    instagram_publish,
    linkedin_oauth,
    linkedin_publish,
    meta_engagement,
    meta_oauth,
    meta_publish,
    models,
    notifications,
    ovh_upload,
    rank_tracking,
    rapport_donnees,
    rapport_pdf,
    recap_mensuel,
    soldes_api,
    veille_actualite,
    whatsapp_business,
)
from .database import Base, SessionLocal, engine, obtenir_session
from .planificateur import (
    envoyer_questions_whatsapp_pour_client,
    envoyer_questions_whatsapp_si_prevu,
    envoyer_recaps_mensuels,
    generer_suggestions_quotidiennes,
    notifier_expirations_linkedin,
    publier_posts_instagram_programmes,
    publier_posts_linkedin_programmes,
    publier_posts_meta_programmes,
    rafraichir_tokens_instagram,
    verifier_avis_supprimes,
    verifier_et_publier_photos_programmees,
    verifier_et_publier_posts_programmes,
    verifier_protection_fiches,
    verifier_statut_validation_fiches,
)
from .security import hacher_mot_de_passe, verifier_mot_de_passe

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

# Destinataire de la notification "nouvelle demande d'audit gratuit" (voir /audit-gratuit).
EMAIL_NOTIFICATION_LEADS = os.getenv("EMAIL_NOTIFICATION_LEADS", "")

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
        if "localisation_active" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN localisation_active BOOLEAN DEFAULT FALSE"))
        if "localisation_ville" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN localisation_ville TEXT DEFAULT ''"))
        if "localisation_latitude" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN localisation_latitude REAL"))
        if "localisation_longitude" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN localisation_longitude REAL"))
        if "localisation_rayon_km" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN localisation_rayon_km INTEGER DEFAULT 15"))
        if "protection_fiche_active" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_fiche_active BOOLEAN DEFAULT FALSE"))
        if "protection_titre_ref" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_titre_ref TEXT DEFAULT ''"))
        if "protection_telephone_ref" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_telephone_ref TEXT DEFAULT ''"))
        if "protection_categorie_id_ref" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_categorie_id_ref TEXT DEFAULT ''"))
        if "protection_categorie_nom_ref" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_categorie_nom_ref TEXT DEFAULT ''"))
        if "protection_statut_ref" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_statut_ref TEXT DEFAULT ''"))
        if "protection_reference_maj_le" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN protection_reference_maj_le TIMESTAMP"))
        if "dernier_statut_validation" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN dernier_statut_validation TEXT"))
        if "compte_meta_id" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN compte_meta_id INTEGER"))
        if "page_id_meta" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN page_id_meta TEXT DEFAULT ''"))
        if "page_nom_meta" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN page_nom_meta TEXT DEFAULT ''"))
        if "token_page_meta" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN token_page_meta TEXT DEFAULT ''"))
        if "instagram_id_meta" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN instagram_id_meta TEXT DEFAULT ''"))
        if "instagram_nom_meta" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN instagram_nom_meta TEXT DEFAULT ''"))
        if "compte_instagram_id" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN compte_instagram_id INTEGER"))
        if "token_instagram" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN token_instagram TEXT DEFAULT ''"))
        if "compte_linkedin_id" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN compte_linkedin_id INTEGER"))
        if "numero_whatsapp" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN numero_whatsapp TEXT DEFAULT ''"))
        if "whatsapp_jours" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN whatsapp_jours TEXT DEFAULT ''"))
        if "whatsapp_opt_in_confirme" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN whatsapp_opt_in_confirme BOOLEAN DEFAULT FALSE"))
        if "hashtags_fixes" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN hashtags_fixes TEXT DEFAULT ''"))
        if "favori_publication_multi" not in colonnes_clients:
            connexion.execute(text("ALTER TABLE clients ADD COLUMN favori_publication_multi BOOLEAN DEFAULT FALSE"))

        if "leads_audit" in inspecteur.get_table_names():
            colonnes_leads = [c["name"] for c in inspecteur.get_columns("leads_audit")]
            if "pdf_audit_base64" not in colonnes_leads:
                connexion.execute(text("ALTER TABLE leads_audit ADD COLUMN pdf_audit_base64 TEXT"))
            if "audite_le" not in colonnes_leads:
                connexion.execute(text("ALTER TABLE leads_audit ADD COLUMN audite_le TIMESTAMP"))
            if "envoye_le" not in colonnes_leads:
                connexion.execute(text("ALTER TABLE leads_audit ADD COLUMN envoye_le TIMESTAMP"))

        if "photos_fiche" in inspecteur.get_table_names():
            colonnes_photos = [c["name"] for c in inspecteur.get_columns("photos_fiche")]
            if "legende" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN legende TEXT DEFAULT ''"))
            if "latitude" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN latitude REAL"))
            if "longitude" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN longitude REAL"))
            if "heure_prevue" not in colonnes_photos:
                connexion.execute(text("ALTER TABLE photos_fiche ADD COLUMN heure_prevue TEXT"))

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

# Casse le cache navigateur du CSS/JS statique a chaque modification du
# fichier (evite de servir une version perimee apres une mise a jour de la
# plateforme - calendrier_champ.js notamment, sans quoi un onglet deja
# ouvert peut continuer a executer une ancienne version indefiniment, meme
# apres un rechargement simple de la page).
templates.env.globals["version_css"] = int(
    os.path.getmtime(os.path.join(DOSSIER_APP, "static", "style.css"))
)
templates.env.globals["version_calendrier_js"] = int(
    os.path.getmtime(os.path.join(DOSSIER_APP, "static", "calendrier_champ.js"))
)
templates.env.globals["version_calendrier_multi_js"] = int(
    os.path.getmtime(os.path.join(DOSSIER_APP, "static", "calendrier_multi.js"))
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


def _nb_changements_suspects() -> int:
    """
    Nombre d'alertes pas encore traitees (changements suspects sur les
    fiches protegees + changements de statut de validation Google), toutes
    fiches confondues - affiche en pastille sur le lien "Alertes" de la
    barre laterale (voir base.html). Session dediee, comme
    _clients_json_recherche_globale ci-dessus (fonction globale Jinja, pas de
    Depends(obtenir_session) disponible ici).
    """
    db = SessionLocal()
    try:
        return (
            db.query(models.AlerteProtectionFiche).filter(models.AlerteProtectionFiche.traite_le.is_(None)).count()
            + db.query(models.AlerteStatutFiche).filter(models.AlerteStatutFiche.traite_le.is_(None)).count()
        )
    finally:
        db.close()


templates.env.globals["nb_changements_suspects"] = _nb_changements_suspects

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
# Sujets tendance du jour (voir planificateur.generer_suggestions_quotidiennes) :
# avant le recap mensuel a 8h, pour que la publication multi-reseaux ait deja
# ses suggestions pretes des le debut de journee.
planificateur.add_job(
    generer_suggestions_quotidiennes,
    "cron",
    hour=7,
    timezone="Europe/Brussels",
    id="suggestions_sujet_jour",
)
# Questions du mode rapide vocal par WhatsApp (voir
# planificateur.envoyer_questions_whatsapp_si_prevu) : verifie chaque matin si
# le jour courant fait partie des jours choisis par le client (Client.whatsapp_jours).
planificateur.add_job(
    envoyer_questions_whatsapp_si_prevu,
    "cron",
    hour=9,
    timezone="Europe/Brussels",
    id="questions_whatsapp_hebdomadaire",
)
# Rappel ntfy avant l'expiration d'un profil LinkedIn (voir
# planificateur.notifier_expirations_linkedin) : une fois par jour.
planificateur.add_job(
    notifier_expirations_linkedin,
    "cron",
    hour=9,
    minute=15,
    timezone="Europe/Brussels",
    id="expirations_linkedin",
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
# Detection des avis supprimes (voir planificateur.verifier_avis_supprimes) :
# une fois par jour suffit (Google ne fournit de toute facon aucune notification
# temps reel), tot le matin pour ne pas concurrencer le recap mensuel a 8h.
planificateur.add_job(
    verifier_avis_supprimes,
    "cron",
    hour=5,
    timezone="Europe/Brussels",
    id="verification_avis_supprimes",
)
# Protection de fiche (voir planificateur.verifier_protection_fiches) : une
# fois par jour, meme creneau matinal que les autres verifications legeres.
planificateur.add_job(
    verifier_protection_fiches,
    "cron",
    hour=6,
    timezone="Europe/Brussels",
    id="verification_protection_fiches",
)
# Changement de statut de validation Google (voir
# planificateur.verifier_statut_validation_fiches) : meme creneau matinal.
planificateur.add_job(
    verifier_statut_validation_fiches,
    "cron",
    hour=6,
    minute=30,
    timezone="Europe/Brussels",
    id="verification_statut_validation_fiches",
)
# Rafraichissement des tokens Instagram (voir planificateur.rafraichir_tokens_instagram)
# - contrairement au token systeme Meta, celui-ci expire (60 jours).
planificateur.add_job(
    rafraichir_tokens_instagram,
    "cron",
    hour=7,
    timezone="Europe/Brussels",
    id="rafraichissement_tokens_instagram",
)
# Publication programmee LinkedIn (voir planificateur.publier_posts_linkedin_programmes) :
# meme frequence que la publication programmee Google.
planificateur.add_job(
    publier_posts_linkedin_programmes,
    "interval",
    minutes=INTERVALLE_PLANIFICATEUR_MINUTES,
    id="publication_linkedin_programmee",
)
# Publication programmee Facebook/Instagram (voir publication-multi) : meme frequence.
planificateur.add_job(
    publier_posts_meta_programmes,
    "interval",
    minutes=INTERVALLE_PLANIFICATEUR_MINUTES,
    id="publication_meta_programmee",
)
planificateur.add_job(
    publier_posts_instagram_programmes,
    "interval",
    minutes=INTERVALLE_PLANIFICATEUR_MINUTES,
    id="publication_instagram_programmee",
)
planificateur.start()


def utilisateur_connecte(request: Request):
    return request.session.get("user_id")


def rediriger_si_non_connecte(request: Request):
    if not utilisateur_connecte(request):
        return RedirectResponse("/connexion")
    return None


# --- Connexion / deconnexion ---------------------------------------------


@app.get("/confidentialite", response_class=HTMLResponse)
def page_confidentialite(request: Request):
    """
    Page publique (pas d'authentification requise) - exigee par Google pour la
    validation du branding de l'ecran de consentement OAuth (voir demande
    d'acces Google Ads API).
    """
    return templates.TemplateResponse(request, "confidentialite.html", {})


@app.get("/audit-gratuit", response_class=HTMLResponse)
def page_audit_gratuit(request: Request):
    """
    Page publique (pas d'authentification requise) de capture de leads. Ne
    declenche plus aucun appel DataForSEO a la soumission (voir
    soumettre_audit_gratuit) - Jonathan lance lui-meme l'audit depuis /leads.
    Destinee a etre exposee sous un sous-domaine du site vitrine de
    l'agence, pas sous le nom "Fiche Locale".
    """
    return templates.TemplateResponse(request, "audit_gratuit.html", {"erreur": None, "envoye": False, "valeurs": {}})


@app.post("/audit-gratuit")
async def soumettre_audit_gratuit(request: Request, db: Session = Depends(obtenir_session)):
    """Capture le lead (coordonnees completes) et notifie Jonathan par email (Brevo) - aucun appel DataForSEO ici."""
    formulaire = await request.form()

    # Piege a robots : champ cache que seul un script automatise remplirait -
    # on repond normalement en apparence, sans rien enregistrer.
    if (formulaire.get("site_web_perso") or "").strip():
        return templates.TemplateResponse(request, "audit_gratuit.html", {"erreur": None, "envoye": True, "valeurs": {}})

    prenom = (formulaire.get("prenom") or "").strip()
    nom = (formulaire.get("nom") or "").strip()
    email = (formulaire.get("email") or "").strip()
    telephone = (formulaire.get("telephone") or "").strip()
    entreprise_nom = (formulaire.get("entreprise_nom") or "").strip()
    ville = (formulaire.get("ville") or "").strip()

    if not all([prenom, nom, email, telephone, entreprise_nom, ville]):
        return templates.TemplateResponse(
            request, "audit_gratuit.html",
            {"erreur": "Merci de remplir tous les champs.", "envoye": False, "valeurs": formulaire},
            status_code=400,
        )

    lead = models.LeadAudit(
        prenom=prenom, nom=nom, email=email, telephone=telephone,
        entreprise_nom=entreprise_nom, ville=ville,
    )
    db.add(lead)
    db.commit()

    if EMAIL_NOTIFICATION_LEADS and brevo_email.identifiants_configures():
        try:
            brevo_email.envoyer_email(
                EMAIL_NOTIFICATION_LEADS, "Fiche Locale",
                f"Nouvelle demande d'audit gratuit : {entreprise_nom}",
                (
                    f"<p>{prenom} {nom} ({email}, {telephone}) demande un audit gratuit pour "
                    f"<strong>{entreprise_nom}</strong> ({ville}).</p>"
                    "<p><a href='https://web-production-bf59a.up.railway.app/leads'>Voir la demande sur /leads</a></p>"
                ),
            )
        except Exception:
            pass  # ne bloque jamais la confirmation au visiteur si la notification echoue

    return templates.TemplateResponse(request, "audit_gratuit.html", {"erreur": None, "envoye": True, "valeurs": {}})


@app.get("/leads", response_class=HTMLResponse)
def liste_leads(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    leads = db.query(models.LeadAudit).order_by(models.LeadAudit.cree_le.desc()).all()
    return templates.TemplateResponse(request, "leads.html", {"leads": leads})


@app.get("/leads/{lead_id}/pdf")
def telecharger_audit_lead(lead_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    lead = db.get(models.LeadAudit, lead_id)
    if not lead or not lead.pdf_audit_base64:
        return RedirectResponse("/leads", status_code=303)

    nom_fichier = f"audit_{lead.entreprise_nom.lower().replace(' ', '_')}.pdf"
    return Response(
        content=base64.b64decode(lead.pdf_audit_base64),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


@app.post("/leads/{lead_id}/envoyer")
def envoyer_audit_lead(lead_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Envoie par email (Brevo) le PDF d'audit deja genere pour ce lead (voir /prospection/audit) - declenche manuellement."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    lead = db.get(models.LeadAudit, lead_id)
    if not lead or not lead.pdf_audit_base64:
        return RedirectResponse("/leads", status_code=303)

    nom_fichier = f"audit_{lead.entreprise_nom.lower().replace(' ', '_')}.pdf"
    try:
        brevo_email.envoyer_email(
            lead.email, f"{lead.prenom} {lead.nom}".strip(),
            f"Votre audit gratuit de fiche Google — {lead.entreprise_nom}",
            (
                f"<p>Bonjour {lead.prenom},</p>"
                f"<p>Voici l'audit gratuit de la fiche Google de <strong>{lead.entreprise_nom}</strong>, "
                "en pièce jointe.</p>"
            ),
            pieces_jointes=[(nom_fichier, base64.b64decode(lead.pdf_audit_base64))],
        )
    except Exception as erreur:
        return HTMLResponse(f"Echec de l'envoi de l'audit : {erreur}", status_code=400)

    lead.envoye_le = datetime.utcnow()
    db.commit()
    return RedirectResponse("/leads", status_code=303)


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


@app.post("/parametres/notifications/tester")
def tester_notification(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    notifications.notifier("Test de notification", "Si vous voyez ceci, tout fonctionne !")
    return RedirectResponse("/parametres/securite?notification_testee=1", status_code=303)


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


STATUTS_POST_GOOGLE_RESUME = {
    "PUBLIE_LIVE": "Publié",
    "A_PUBLIER": "Programmé",
    "PUBLIE_REJECTED": "Rejeté",
    "ECHEC_PUBLICATION": "Échec",
}
LIBELLES_ETAT_RESEAU_RESUME = {"PUBLIE": "Publié", "EN_ATTENTE": "Programmé", "ECHEC": "Échec"}


def _extrait_court(texte: str, longueur: int = 60) -> str:
    texte = (texte or "").strip()
    return texte[:longueur] + ("…" if len(texte) > longueur else "")


NB_MAX_IMAGES_PUBLICATION = 10  # limite d'un carrousel Instagram
COTE_MAX_IMAGE_PUBLICATION = 2048


def _jpeg_normalise(octets: bytes, cote_max: int = COTE_MAX_IMAGE_PUBLICATION) -> bytes:
    """Image quelconque -> JPEG RGB, orientation EXIF appliquee, cote max reduit. ValueError si illisible."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(octets)))
    except Exception:
        raise ValueError("image illisible ou format non pris en charge (utilisez un fichier JPG ou PNG).")
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        fond = Image.new("RGB", image.size, "white")
        fond.paste(image, mask=image.split()[-1])
        image = fond
    else:
        image = image.convert("RGB")
    image.thumbnail((cote_max, cote_max))
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=88, optimize=True)
    return tampon.getvalue()


def _televerser_image_publication(octets: bytes, prefixe: str) -> str:
    """
    Normalise une image choisie par l'utilisateur puis l'heberge sur OVH, sous
    un nom neutre. Deux raisons : (1) Facebook/Instagram telechargent l'image
    depuis son URL, et un nom d'origine avec espaces, parentheses ou accents
    (photos de telephone, WhatsApp : "IMG 2026 (1).jpg") donne une URL qu'ils
    rejettent (erreur 324 "Missing or invalid image file") ; (2) les photos de
    telephone de plusieurs Mo depassent parfois leurs limites de taille.
    Renvoie l'URL publique ; ValueError si le fichier n'est pas une image lisible.
    """
    return ovh_upload.envoyer_octets(_jpeg_normalise(octets), f"{prefixe}-{uuid.uuid4().hex[:12]}.jpg")


FUSEAU_PARIS = ZoneInfo("Europe/Brussels")
DUREE_CACHE_PUBLICATIONS_EXTERNES = 300  # secondes
_cache_publications_externes: dict = {}


def _maintenant_paris() -> datetime:
    """Heure murale de Paris sans fuseau : meme convention que les dates de programmation (voir planificateur._maintenant_local)."""
    return datetime.now(FUSEAU_PARIS).replace(tzinfo=None)


def _datetime_paris(iso: str):
    """Convertit un horodatage ISO d'une API (UTC, "Z" ou "+0000") en heure de Paris sans fuseau ; None si illisible."""
    if not iso:
        return None
    texte = iso.strip().replace("Z", "+0000")
    for format_ in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(texte.replace("+00:00", "+0000"), format_).astimezone(FUSEAU_PARIS).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _cle_texte(texte: str) -> str:
    """Debut du texte normalise : sert a reconnaitre un meme post entre la base locale et la lecture en direct."""
    return " ".join((texte or "").lower().split())[:40]


def _publications_externes(client: "models.Client", posts_google_en_ligne: list, ids_google_connus: set) -> list:
    """
    Publications lues en direct chez les reseaux, y compris celles faites hors
    plateforme : Google (posts_google_en_ligne, deja lus par l'appelant, ~7
    derniers jours seulement cote API Google), Facebook et Instagram (les 10
    derniers). LinkedIn n'en fait pas partie : son API ne permet pas de relire
    les posts d'un profil (scope r_member_social reserve) - voir
    _journaliser_publication_linkedin. Une erreur d'un reseau est ignoree : le
    resume doit s'afficher meme si un jeton est expire. Cache de quelques
    minutes pour ne pas refaire ces appels a chaque affichage de la fiche.
    """
    lignes = []
    for post in posts_google_en_ligne or []:
        if post.get("id_post_google") in ids_google_connus or post.get("etat") not in ("LIVE", "REJECTED"):
            continue
        moment = _datetime_paris(post.get("date_creation_brute", ""))
        if not moment:
            continue
        rejete = post["etat"] == "REJECTED"
        lignes.append({
            "reseau": "google", "titre": _extrait_court(post.get("texte", "")), "image_url": post.get("url_image", ""),
            "date": moment.date(), "heure": moment.strftime("%H:%M"), "url": post.get("url_recherche", ""),
            "statut_brut": "PUBLIE_REJECTED" if rejete else "PUBLIE_LIVE", "statut_libelle": "Rejeté" if rejete else "Publié",
            "externe": True,
        })

    maintenant = monotonic()
    en_cache = _cache_publications_externes.get(client.id)
    if en_cache and maintenant - en_cache[0] < DUREE_CACHE_PUBLICATIONS_EXTERNES:
        reseaux_lus = en_cache[1]
    else:
        def lire_facebook():
            if not (client.page_id_meta and client.token_page_meta):
                return []
            return meta_engagement.lister_posts_publies(client.token_page_meta, client.page_id_meta)

        def lire_instagram():
            if not (client.instagram_id_meta and client.token_instagram):
                return []
            return instagram_engagement.lister_medias_recents(client.token_instagram, client.instagram_id_meta)

        def proteger(lecture):
            try:
                return lecture()
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=2) as pool:
            futur_facebook = pool.submit(proteger, lire_facebook)
            futur_instagram = pool.submit(proteger, lire_instagram)
            reseaux_lus = {"facebook": futur_facebook.result(), "instagram": futur_instagram.result()}
        _cache_publications_externes[client.id] = (maintenant, reseaux_lus)

    for reseau, posts in reseaux_lus.items():
        for post in posts:
            moment = _datetime_paris(post.get("cree_le", ""))
            if not moment:
                continue
            lignes.append({
                "reseau": reseau, "titre": _extrait_court(post.get("texte", "")), "image_url": post.get("image_url", ""),
                "date": moment.date(), "heure": moment.strftime("%H:%M"), "url": post.get("url", ""),
                "statut_brut": "PUBLIE", "statut_libelle": "Publié", "externe": True,
            })
    return lignes


def _journaliser_publication_linkedin(db: Session, compte_linkedin_id: int, texte: str) -> None:
    """
    LinkedIn ne permet pas de relire les posts d'un profil : on garde donc une
    trace de ceux publies immediatement via la plateforme, en reutilisant
    PostLinkedInProgramme (etat PUBLIE, sans image : inutile, et lourde a
    stocker) pour qu'ils apparaissent dans le resume multi-reseaux.
    """
    db.add(models.PostLinkedInProgramme(
        compte_linkedin_id=compte_linkedin_id, texte=texte, image_donnees=None,
        publier_le=_maintenant_paris(), etat="PUBLIE",
    ))
    db.commit()


def _publications_multi_reseaux(
    db: Session, client: "models.Client", limite: int = 30, posts_google_en_ligne: list = None,
) -> list:
    """
    Vue unifiee, tous reseaux confondus, des publications d'un client
    (programmees, publiees ou en echec) : ce que la plateforme suit en base
    (Post pour Google, PostMetaProgramme, PostInstagramProgramme,
    PostLinkedInProgramme) completee par la lecture en direct des publications
    faites hors plateforme (voir _publications_externes), sans doublon.
    Triee par date et heure decroissantes.
    """
    lignes = []

    for post in (
        db.query(models.Post)
        .filter(models.Post.client_id == client.id, models.Post.statut.in_(STATUTS_POST_GOOGLE_RESUME.keys()))
        .all()
    ):
        lignes.append({
            "reseau": "google",
            "titre": _extrait_court(post.titre or post.texte),
            "image_url": post.image_url,
            "date": post.date_prevue or post.cree_le.date(),
            "heure": post.heure_prevue or "",
            "id": post.id,
            "a_venir": post.statut == "A_PUBLIER",
            "statut_brut": post.statut,
            "statut_libelle": STATUTS_POST_GOOGLE_RESUME[post.statut],
        })

    for reseau, modele, filtre in [
        ("facebook", models.PostMetaProgramme, {"client_id": client.id}),
        ("instagram", models.PostInstagramProgramme, {"client_id": client.id}),
    ]:
        for post in db.query(modele).filter_by(**filtre).all():
            lignes.append({
                "reseau": reseau,
                "titre": post.texte[:60] + ("…" if len(post.texte) > 60 else ""),
                "image_url": (meta_publish.urls_depuis_champ(post.image_url) or [None])[0],
                "date": post.publier_le.date(),
                "heure": post.publier_le.strftime("%H:%M"),
                "id": post.id,
                "a_venir": post.etat == "EN_ATTENTE",
                "statut_brut": post.etat,
                "statut_libelle": LIBELLES_ETAT_RESEAU_RESUME.get(post.etat, post.etat),
            })

    if client.compte_linkedin_id:
        for post in db.query(models.PostLinkedInProgramme).filter_by(compte_linkedin_id=client.compte_linkedin_id).all():
            lignes.append({
                "reseau": "linkedin",
                "titre": post.texte[:60] + ("…" if len(post.texte) > 60 else ""),
                "image_url": None,  # stockee en octets, pas d'URL directe (voir PostLinkedInProgramme)
                "date": post.publier_le.date(),
                "heure": post.publier_le.strftime("%H:%M"),
                "id": post.id,
                "a_venir": post.etat == "EN_ATTENTE",
                "statut_brut": post.etat,
                "statut_libelle": LIBELLES_ETAT_RESEAU_RESUME.get(post.etat, post.etat),
            })

    ids_google_connus = {
        id_post for (id_post,) in db.query(models.Post.id_post_google).filter(
            models.Post.client_id == client.id, models.Post.id_post_google != "",
        ).all()
    }
    cles_deja_suivies = {(l["reseau"], _cle_texte(l["titre"])) for l in lignes if l["reseau"] != "google"}
    # Les titres locaux sont tronques a 60 caracteres, la cle en garde 40 : comparables.
    for externe in _publications_externes(client, posts_google_en_ligne, ids_google_connus):
        if externe["reseau"] != "google" and (externe["reseau"], _cle_texte(externe["titre"])) in cles_deja_suivies:
            continue
        lignes.append(externe)

    lignes.sort(key=lambda l: (l["date"], l.get("heure") or "00:00"), reverse=True)
    return lignes[:limite]


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
            "publications_multi_reseaux": _publications_multi_reseaux(db, client, posts_google_en_ligne=posts_en_ligne),
        },
    )


LIBELLES_MOIS = {
    1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril", 5: "Mai", 6: "Juin",
    7: "Juillet", 8: "Août", 9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
}


# Statuts de post a exclure de tout affichage lie au calendrier (grille de
# contenu et jours "deja occupes" du widget de date) : un post rejete
# (IGNORE, ou PUBLIE_REJECTED par Google) ne va plus etre publie, au meme
# titre qu'un post SUPPRIME - le laisser apparaitre donnerait l'impression
# trompeuse que quelque chose est encore programme ce jour-la.
STATUTS_POST_EXCLUS_CALENDRIER = ["SUPPRIME", "IGNORE", "PUBLIE_REJECTED"]


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
            models.Post.statut.notin_(STATUTS_POST_EXCLUS_CALENDRIER),
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


@app.get("/avis/supprimes/{client_id}")
def avis_supprimes_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Avis detectes comme supprimes pour un client (voir models.AvisConnu et
    planificateur.verifier_avis_supprimes) - simple lecture en base, aucun
    appel a Google ici. Ne peut remonter que les suppressions survenues
    depuis que la tache de fond tourne pour ce client.
    """
    if not utilisateur_connecte(request):
        return JSONResponse({"avis": [], "erreur": "Non connecte."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"avis": [], "erreur": None})

    avis_supprimes = (
        db.query(models.AvisConnu)
        .filter(models.AvisConnu.client_id == client_id, models.AvisConnu.supprime_le.isnot(None))
        .order_by(models.AvisConnu.supprime_le.desc())
        .all()
    )

    return JSONResponse({
        "avis": [
            {
                "client_id": client.id,
                "client_nom": client.nom,
                "review_id": a.review_id,
                "auteur": a.auteur,
                "note": a.note,
                "commentaire": a.commentaire,
                "date_avis": a.date_avis,
                "reponse": a.reponse or None,
                "supprime_le": a.supprime_le.isoformat(),
                "premiere_detection_le": a.premiere_detection_le.isoformat() if a.premiere_detection_le else None,
            }
            for a in avis_supprimes
        ],
        "erreur": None,
    })


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

    # Dates optionnelles preselectionnees (une par post genere, dans l'ordre) :
    # prereplit le champ de date de TOUS les exemplaires du lot correspondant
    # (un par client selectionne), mais chaque lot reste en BROUILLON - juste
    # un gain de temps a la relecture, pas une programmation automatique.
    dates_brutes = [d for d in formulaire.getlist("dates_prevues")[:nombre_posts]]

    try:
        posts_generes = claude_generation.generer_posts_generiques(theme, contenu_reference, nombre_posts)
    except Exception as erreur:
        return _reponse_posts_multi(request, db, erreur=str(erreur))

    clients_valides = [db.get(models.Client, cid) for cid in client_ids]
    clients_valides = [c for c in clients_valides if c]

    lot_ids = []
    for index, post_genere in enumerate(posts_generes):
        date_prevue = None
        if index < len(dates_brutes) and dates_brutes[index].strip():
            try:
                date_prevue = date.fromisoformat(dates_brutes[index].strip())
            except ValueError:
                date_prevue = None
        lot_id = uuid.uuid4().hex[:12]
        for client in clients_valides:
            db.add(models.Post(
                client_id=client.id,
                titre=post_genere.get("titre", ""),
                texte=post_genere.get("texte", ""),
                prompt_image=post_genere.get("prompt_image", ""),
                statut="BROUILLON",
                lot_id=lot_id,
                date_prevue=date_prevue,
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
            "date_prevue": premier.date_prevue,
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


def _plage_mois_prochain() -> tuple[date, date]:
    aujourdhui = date.today()
    if aujourdhui.month == 12:
        annee, mois = aujourdhui.year + 1, 1
    else:
        annee, mois = aujourdhui.year, aujourdhui.month + 1
    premier_jour = date(annee, mois, 1)
    dernier_jour = date(annee, mois, calendar.monthrange(annee, mois)[1])
    return premier_jour, dernier_jour


@app.get("/", response_class=HTMLResponse)
def liste_clients(request: Request, etiquette_id: int = None, db: Session = Depends(obtenir_session)):
    if not utilisateur_connecte(request):
        # Page d'accueil publique (sans connexion) decrivant l'outil - requise
        # par la validation du branding Google (ecran de consentement OAuth) :
        # la page d'accueil doit rester consultable sans se connecter et
        # expliquer l'objet de l'application.
        return templates.TemplateResponse(request, "accueil.html", {})

    espace, clients = _resoudre_espace_et_clients(db, etiquette_id)

    ids_avec_connaissance = {
        client_id
        for (client_id,) in db.query(models.DocumentConnaissance.client_id).distinct().all()
    }

    # Fiches sans aucun post programme (A_PUBLIER) sur le mois qui suit le
    # mois en cours - seules les fiches reellement liees a Google sont
    # concernees, une fiche non associee ne pouvant de toute facon rien
    # publier.
    debut_mois_prochain, fin_mois_prochain = _plage_mois_prochain()
    ids_clients_avec_fiche = {c.id for c in clients if c.account_id and c.location_id}
    ids_avec_post_mois_prochain = {
        client_id
        for (client_id,) in db.query(models.Post.client_id).filter(
            models.Post.client_id.in_(ids_clients_avec_fiche),
            models.Post.statut == "A_PUBLIER",
            models.Post.date_prevue >= debut_mois_prochain,
            models.Post.date_prevue <= fin_mois_prochain,
        ).distinct().all()
    }
    ids_sans_post_mois_prochain = ids_clients_avec_fiche - ids_avec_post_mois_prochain

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
            "ids_sans_post_mois_prochain": ids_sans_post_mois_prochain,
            "nb_sans_post_mois_prochain": len(ids_sans_post_mois_prochain),
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

LIBELLES_STATUT_VALIDATION = {
    "valide": "Validée",
    "non_valide": "Non validée (en attente Google)",
    "inaccessible": "Inaccessible (suspension probable)",
}


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

    # Le badge global (barre laterale) compte les alertes de tous les
    # clients, y compris ceux d'un espace isole (exclus de cette page - voir
    # _query_clients_non_isoles). Sur la vue globale, on pointe explicitement
    # vers ces espaces plutot que de laisser un ecart badge/page inexplique.
    alertes_espaces_isoles = []
    if not espace:
        for etiquette_isolee in db.query(models.Etiquette).filter(models.Etiquette.isolee == True).all():  # noqa: E712
            ids_clients_espace = [
                c.id for c in db.query(models.Client.id)
                .filter(models.Client.etiquettes.any(models.Etiquette.id == etiquette_isolee.id))
                .all()
            ]
            if not ids_clients_espace:
                continue
            nb = (
                db.query(models.AlerteProtectionFiche)
                .filter(models.AlerteProtectionFiche.client_id.in_(ids_clients_espace), models.AlerteProtectionFiche.traite_le.is_(None))
                .count()
                + db.query(models.AlerteStatutFiche)
                .filter(models.AlerteStatutFiche.client_id.in_(ids_clients_espace), models.AlerteStatutFiche.traite_le.is_(None))
                .count()
            )
            if nb:
                alertes_espaces_isoles.append({"etiquette": etiquette_isolee, "nb": nb})
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

    changements_suspects = (
        db.query(models.AlerteProtectionFiche)
        .join(models.Client, models.AlerteProtectionFiche.client_id == models.Client.id)
        .filter(
            models.AlerteProtectionFiche.client_id.in_([c.id for c in clients_avec_fiche]),
            models.AlerteProtectionFiche.traite_le.is_(None),
        )
        .order_by(models.AlerteProtectionFiche.detecte_le.desc())
        .all()
    )
    nb_sans_protection = sum(1 for c in clients_avec_fiche if not c.protection_fiche_active)

    changements_statut = (
        db.query(models.AlerteStatutFiche)
        .join(models.Client, models.AlerteStatutFiche.client_id == models.Client.id)
        .filter(
            models.AlerteStatutFiche.client_id.in_([c.id for c in clients_avec_fiche]),
            models.AlerteStatutFiche.traite_le.is_(None),
        )
        .order_by(models.AlerteStatutFiche.detecte_le.desc())
        .all()
    )

    return templates.TemplateResponse(
        request,
        "alertes.html",
        {
            "clients_json": json.dumps(
                [{"id": c.id, "nom": c.nom} for c in clients_avec_fiche]
            ).replace("</", "<\\/"),
            "clients_inactifs": clients_inactifs,
            "seuil_inactivite_jours": SEUIL_INACTIVITE_POSTS_JOURS,
            "nb_sans_protection": nb_sans_protection,
            "espace": espace,
            "changements_suspects": changements_suspects,
            "changements_statut": changements_statut,
            "libelles_statut_validation": LIBELLES_STATUT_VALIDATION,
            "alertes_espaces_isoles": alertes_espaces_isoles,
        },
    )


@app.post("/alertes/statut/{alerte_id}/vu")
def marquer_alerte_statut_vue(alerte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Marque une alerte de changement de statut comme vue - purement informatif, aucune ecriture sur Google."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    alerte = db.get(models.AlerteStatutFiche, alerte_id)
    if alerte and alerte.traite_le is None:
        alerte.traite_le = datetime.utcnow()
        db.commit()
    return RedirectResponse("/alertes", status_code=303)


@app.post("/alertes/protection/{alerte_id}/restaurer")
def restaurer_alerte_protection(alerte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Remet le champ concerne a la valeur de reference sur la fiche Google (ecriture reelle)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    alerte = db.get(models.AlerteProtectionFiche, alerte_id)
    if not alerte or alerte.traite_le is not None:
        return RedirectResponse("/alertes", status_code=303)

    client = alerte.client
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id) if client else None
    if not client or not identifiants:
        return RedirectResponse("/alertes", status_code=303)

    valeur_reference = {
        "titre": client.protection_titre_ref,
        "telephone": client.protection_telephone_ref,
        "categorie": client.protection_categorie_id_ref,
        "statut": client.protection_statut_ref,
    }.get(alerte.champ)

    try:
        google_location.restaurer_champ_protege(
            identifiants, client.location_id, alerte.champ,
            valeur_reference, client.protection_categorie_nom_ref,
        )
    except Exception:
        # L'alerte reste en attente (non marquee traitee) : Jonathan peut reessayer.
        return RedirectResponse("/alertes", status_code=303)

    alerte.traite_le = datetime.utcnow()
    alerte.action = "RESTAURE"
    db.commit()
    return RedirectResponse("/alertes", status_code=303)


@app.post("/alertes/protection/{alerte_id}/ignorer")
def ignorer_alerte_protection(alerte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Accepte le changement detecte comme nouvelle reference (relit la fiche pour l'etat le plus a jour)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    alerte = db.get(models.AlerteProtectionFiche, alerte_id)
    if not alerte or alerte.traite_le is not None:
        return RedirectResponse("/alertes", status_code=303)

    client = alerte.client
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id) if client else None
    if client and identifiants:
        try:
            infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
            valeurs = google_location.valeurs_protegees(infos)
            if alerte.champ == "titre":
                client.protection_titre_ref = valeurs["titre"]
            elif alerte.champ == "telephone":
                client.protection_telephone_ref = valeurs["telephone"]
            elif alerte.champ == "categorie":
                client.protection_categorie_id_ref = valeurs["categorie_id"]
                client.protection_categorie_nom_ref = valeurs["categorie_nom"]
            elif alerte.champ == "statut":
                client.protection_statut_ref = valeurs["statut_ouvert"]
        except Exception:
            pass  # la reference n'est pas mise a jour, mais l'alerte est quand meme classee

    alerte.traite_le = datetime.utcnow()
    alerte.action = "IGNORE"
    db.commit()
    return RedirectResponse("/alertes", status_code=303)


@app.post("/alertes/protection/{alerte_id}/masquer")
def masquer_alerte_protection(alerte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Retire l'alerte de la liste sans rien changer (ni ecriture sur Google, ni
    mise a jour de la reference) - pour un cas que Jonathan ne veut pas
    traiter maintenant. La reference restant inchangee, si l'ecart persiste
    encore le lendemain, verifier_protection_fiches la re-signalera.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    alerte = db.get(models.AlerteProtectionFiche, alerte_id)
    if alerte and alerte.traite_le is None:
        alerte.traite_le = datetime.utcnow()
        alerte.action = "MASQUE"
        db.commit()
    return RedirectResponse("/alertes", status_code=303)


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


def _sujets_deja_traites_client(db: Session, client_id: int, limite: int = 40) -> list[str]:
    """
    Titre + debut du texte des posts deja generes pour ce client (tous statuts
    sauf SUPPRIME), les plus recents d'abord - fourni a l'IA pour qu'elle
    evite de reprendre les memes sujets d'un mois sur l'autre (voir
    generer_posts_pour_client). Limite a 40 pour ne pas alourdir le prompt sur
    un client avec un long historique.
    """
    posts = (
        db.query(models.Post)
        .filter(models.Post.client_id == client_id, models.Post.statut != "SUPPRIME")
        .order_by(models.Post.cree_le.desc())
        .limit(limite)
        .all()
    )
    return [f"{post.titre} — {post.texte[:150].strip()}" for post in posts if post.texte.strip()]


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
        .filter(
            models.Post.client_id == client_id,
            models.Post.date_prevue.isnot(None),
            models.Post.statut.notin_(STATUTS_POST_EXCLUS_CALENDRIER),
        )
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
    erreur_photo: str = None, erreur_document: str = None, erreur_post_manuel: str = None,
    resultats_citations: list = None, erreur_citations: str = None, erreur_protection: str = None,
    code: int = 200,
):
    posts = (
        db.query(models.Post)
        .filter(models.Post.client_id == client.id, models.Post.statut != "SUPPRIME")
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
            "publications_multi_reseaux": _publications_multi_reseaux(db, client, posts_google_en_ligne=tous_posts_en_ligne),
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
            "annuaires_citations": citations.ANNUAIRES,
            "resultats_citations": resultats_citations,
            "erreur_citations": erreur_citations,
            "erreur_protection": erreur_protection,
            "libelles_statut_ouverture": google_location.LIBELLES_STATUT_OUVERTURE,
            "intervalle_planificateur_minutes": INTERVALLE_PLANIFICATEUR_MINUTES,
            "reponses_interview": (
                db.query(models.ReponseInterviewClient)
                .filter(models.ReponseInterviewClient.client_id == client.id)
                .order_by(models.ReponseInterviewClient.cree_le.desc())
                .limit(20)
                .all()
            ),
            "erreur_whatsapp_test": request.session.pop("erreur_whatsapp_test", None),
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


@app.post("/clients/{client_id}/citations/verifier")
async def verifier_citations_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Verifie a la demande la presence de la fiche sur quelques annuaires tiers
    (voir citations.py) - jamais automatique, chaque annuaire coche consomme
    une requete DataForSEO facturee a l'usage.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    formulaire = await request.form()
    annuaires_ids = [v for v in formulaire.getlist("annuaires") if v.strip()]
    if not annuaires_ids:
        return _reponse_detail_client(request, db, client, erreur_citations="Cochez au moins un annuaire a verifier.")

    if not client.account_id or not client.location_id:
        return _reponse_detail_client(
            request, db, client, erreur_citations="Ce client n'a pas de fiche Google associee."
        )

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_detail_client(
            request, db, client,
            erreur_citations="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
        adresse = infos.get("storefrontAddress") or {}
        ville = adresse.get("locality", "")
        code_pays = adresse.get("regionCode", "")
    except Exception as erreur:
        return _reponse_detail_client(
            request, db, client, erreur_citations=f"Impossible de lire l'adresse de la fiche : {erreur}"
        )

    try:
        resultats = citations.verifier_citations(client.nom, ville, code_pays, annuaires_ids)
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_citations=str(erreur))

    return _reponse_detail_client(request, db, client, resultats_citations=resultats)


def _capturer_reference_protection(client: models.Client, infos: dict) -> None:
    """Enregistre l'etat actuel des champs surveilles comme reference et active la protection."""
    valeurs = google_location.valeurs_protegees(infos)
    client.protection_titre_ref = valeurs["titre"]
    client.protection_telephone_ref = valeurs["telephone"]
    client.protection_categorie_id_ref = valeurs["categorie_id"]
    client.protection_categorie_nom_ref = valeurs["categorie_nom"]
    client.protection_statut_ref = valeurs["statut_ouvert"]
    client.protection_reference_maj_le = datetime.utcnow()
    client.protection_fiche_active = True


@app.post("/clients/{client_id}/protection/activer")
def activer_protection_fiche(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Capture l'etat actuel des champs surveilles (nom, telephone, categorie
    principale, statut) comme reference, puis active la protection - voir
    planificateur.verifier_protection_fiches pour la verification quotidienne.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not client.account_id or not client.location_id:
        return _reponse_detail_client(
            request, db, client, erreur_protection="Ce client n'a pas de fiche Google associee."
        )

    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return _reponse_detail_client(
            request, db, client,
            erreur_protection="Compte Google non valide pour ce client (a reconnecter depuis Comptes Google).",
        )

    try:
        infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_protection=f"Impossible de lire la fiche : {erreur}")

    _capturer_reference_protection(client, infos)
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/protection/activer-toutes")
def activer_protection_toutes_fiches(request: Request, db: Session = Depends(obtenir_session)):
    """
    Active la protection en une fois sur toutes les fiches qui ne l'ont pas
    encore (voir _capturer_reference_protection) - pratique pour l'activer sur
    l'ensemble des clients existants plutot que fiche par fiche. Une fiche en
    erreur (token expire, fiche non lisible...) est simplement ignoree, les
    autres sont quand meme activees.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    clients = (
        db.query(models.Client)
        .filter(models.Client.account_id != "", models.Client.location_id != "")
        .filter(models.Client.protection_fiche_active.is_(False))
        .all()
    )

    identifiants_par_compte = {}
    for client in clients:
        compte_id = client.compte_google_id
        if compte_id not in identifiants_par_compte:
            identifiants_par_compte[compte_id] = google_oauth.obtenir_identifiants(db, compte_id)
        identifiants = identifiants_par_compte[compte_id]
        if not identifiants:
            continue

        try:
            infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
        except Exception:
            continue

        _capturer_reference_protection(client, infos)
        db.commit()

    return RedirectResponse("/alertes", status_code=303)


@app.post("/clients/{client_id}/protection/desactiver")
def desactiver_protection_fiche(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    client.protection_fiche_active = False
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/generer")
async def generer_posts_client(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    formulaire = await request.form()
    try:
        nombre_posts = int(formulaire.get("nombre_posts", "7"))
    except ValueError:
        nombre_posts = 7

    # Dates optionnelles preselectionnees (une par post, dans l'ordre) : le
    # champ de date de chaque post est prerempli avec, mais le post reste en
    # BROUILLON - juste un gain de temps a la relecture, pas une programmation
    # automatique (voir aussi programmer_brouillons_masse, qui lui programme
    # reellement avec des dates espacees automatiquement).
    dates_brutes = [d for d in formulaire.getlist("dates_prevues")[:nombre_posts]]

    localisation = None
    if client.localisation_active and client.localisation_latitude is not None:
        localisation = {
            "ville": client.localisation_ville,
            "latitude": client.localisation_latitude,
            "longitude": client.localisation_longitude,
            "rayon_km": client.localisation_rayon_km,
        }

    try:
        posts_generes = claude_generation.generer_posts_pour_client(
            _contexte_ia_client(client), nombre_posts, _sujets_deja_traites_client(db, client.id), localisation
        )
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_generation=str(erreur), code=500)

    for index, post_genere in enumerate(posts_generes):
        date_prevue = None
        if index < len(dates_brutes) and dates_brutes[index].strip():
            try:
                date_prevue = date.fromisoformat(dates_brutes[index].strip())
            except ValueError:
                date_prevue = None
        db.add(models.Post(
            client_id=client.id,
            titre=post_genere["titre"],
            texte=post_genere["texte"],
            prompt_image=post_genere["prompt_image"],
            statut="BROUILLON",
            date_prevue=date_prevue,
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


NB_MAX_PHOTOS_REFERENCE = 5


@app.post("/clients/{client_id}/photos_reference")
def ajouter_photo_reference_client(
    client_id: int, request: Request, fichier: UploadFile = File(...), db: Session = Depends(obtenir_session)
):
    """Ajoute une photo de reference du client (voir models.PhotoReferenceClient), utilisee pour l'inclure comme sujet des images IA."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if len(client.photos_reference) >= NB_MAX_PHOTOS_REFERENCE:
        return _reponse_detail_client(request, db, client, erreur_document=f"Maximum {NB_MAX_PHOTOS_REFERENCE} photos de reference.", code=400)

    try:
        octets = fichier.file.read()
        extension = ".png" if "png" in (fichier.content_type or "") else ".jpg"
        nom_fichier = f"reference_{client.id}_{uuid.uuid4().hex[:10]}{extension}"
        url_image = ovh_upload.envoyer_octets(octets, nom_fichier)
    except Exception as erreur:
        return _reponse_detail_client(request, db, client, erreur_document=str(erreur), code=400)

    db.add(models.PhotoReferenceClient(client_id=client.id, image_url=url_image))
    db.commit()

    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/photos_reference/{photo_id}/supprimer")
def supprimer_photo_reference_client(client_id: int, photo_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    photo = db.get(models.PhotoReferenceClient, photo_id)
    if photo and photo.client_id == client_id:
        db.delete(photo)
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


DUREES_INTERVALLE_PHOTOS = {"minutes": "minutes", "heures": "hours", "jours": "days"}


@app.post("/clients/{client_id}/photos/programmer")
def programmer_photos_client(
    client_id: int, request: Request,
    date_prevue: str = Form(...),
    taille_lot: int = Form(...),
    intervalle_valeur: int = Form(1),
    intervalle_unite: str = Form("jours"),
    heure_mode: str = Form("0830"),
    heure_h: int = Form(8),
    heure_m: int = Form(30),
    db: Session = Depends(obtenir_session),
):
    """
    Programme l'envoi de toutes les photos actuellement en BROUILLON pour ce
    client, par lots espaces (ex : 10 photos tous les 3 jours, ou toutes les
    30 minutes) plutot que toutes a la meme date - utile pour un import en
    masse (ex : une centaine de photos d'un coup) qu'on ne veut pas voir
    arriver toutes le meme jour sur la fiche Google (Google peut bloquer les
    envois trop massifs en une seule fois).
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

    heure_debut = _heure_depuis_formulaire({"heure_mode": heure_mode, "heure_h": heure_h, "heure_m": heure_m})
    heure_debut_h, heure_debut_m = (int(x) for x in heure_debut.split(":"))
    datetime_debut = datetime.combine(date_debut, time(hour=heure_debut_h, minute=heure_debut_m))

    taille_lot = max(1, taille_lot)
    intervalle_valeur = max(1, intervalle_valeur)
    if intervalle_unite == "minutes" and intervalle_valeur < INTERVALLE_PLANIFICATEUR_MINUTES:
        return _reponse_detail_client(
            request, db, client,
            erreur_photo=(
                f"L'espacement minimum est de {INTERVALLE_PLANIFICATEUR_MINUTES} minutes "
                "(fréquence de vérification du planificateur) : en dessous, plusieurs lots "
                "partiraient regroupés au même passage."
            ),
        )
    unite_timedelta = DUREES_INTERVALLE_PHOTOS.get(intervalle_unite, "days")
    pas = timedelta(**{unite_timedelta: intervalle_valeur})

    photos = (
        db.query(models.PhotoFiche)
        .filter(models.PhotoFiche.client_id == client_id, models.PhotoFiche.statut == "BROUILLON")
        .order_by(models.PhotoFiche.id)
        .all()
    )
    for index, photo in enumerate(photos):
        groupe = index // taille_lot
        moment_prevu = datetime_debut + pas * groupe
        photo.date_prevue = moment_prevu.date()
        photo.heure_prevue = moment_prevu.strftime("%H:%M")
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


def _categorie_ids_fiche(infos: dict) -> list[str]:
    categories = (infos or {}).get("categories") or {}
    ids = []
    if categories.get("primaryCategory"):
        ids.append(categories["primaryCategory"]["name"])
    ids += [c["name"] for c in categories.get("additionalCategories", [])]
    return ids


def _services_fiche(infos: dict, libelles_types: dict = None) -> list[dict]:
    services = []
    for item in (infos or {}).get("serviceItems", []):
        services.append({
            "libelle": google_location.etiquette_service(item, libelles_types),
            "description": google_location.description_service(item),
            "prix": google_location.prix_service(item),
            "json": json.dumps(item),
        })
    return services


def _reponse_fiche_client(
    request: Request, client: models.Client, db: Session, infos: dict = None,
    erreur: str = None, succes: str = None, code: int = 200,
):
    horaires_par_jour, jours_verrouilles = google_location.horaires_par_jour(infos.get("regularHours") if infos else None)

    identifiants = None
    if client.account_id and client.location_id:
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)

    liens_action, erreur_liens_action = [], None
    if identifiants:
        try:
            liens_action = google_place_actions.lister_liens(identifiants, client.location_id)
        except Exception as e:
            erreur_liens_action = str(e)

    # Types de service standard (vocabulaire ferme) pour les categories de la
    # fiche : utilise a la fois pour etiqueter les structuredServiceItem deja
    # presents (sinon affiches avec leur id technique brut) et pour proposer
    # un type standard a l'ajout d'un nouveau service.
    categorie_ids = _categorie_ids_fiche(infos)
    libelles_types_service, types_service_disponibles = {}, []
    if identifiants and categorie_ids:
        try:
            types_par_categorie = google_location.types_service_categories(identifiants, categorie_ids)
            for types in types_par_categorie.values():
                for t in types:
                    libelles_types_service[t["id"]] = t["libelle"]
            types_service_disponibles = types_par_categorie.get(categorie_ids[0], [])
        except Exception:
            pass

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
            "services": _services_fiche(infos, libelles_types_service),
            "types_service_disponibles": types_service_disponibles,
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

    # Un service invalide (JSON corrompu cote client, ne devrait pas arriver
    # via l'UI normale) ne doit pas faire echouer tout l'enregistrement de la
    # fiche - on l'ignore silencieusement plutot que de bloquer le reste.
    services = []
    for brut in formulaire.getlist("services_json"):
        try:
            services.append(json.loads(brut))
        except (json.JSONDecodeError, TypeError):
            continue
    donnees["serviceItems"] = services
    champs.append("serviceItems")

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


@app.get("/clients/{client_id}/positions/suggestions", response_class=HTMLResponse)
def suggestions_mots_cles(request: Request, client_id: int, db: Session = Depends(obtenir_session)):
    """
    Propose des mots-cles pertinents pour ce client precis, a partir de sa
    categorie Google, sa ville et le contenu de son site (voir
    claude_generation.suggerer_semences_mots_cles), avec leur volume de
    recherche reel (voir google_ads_keywords.idees_mots_cles).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not google_oauth.ads_configure(db):
        return RedirectResponse(f"/clients/{client_id}/positions", status_code=303)

    categorie, ville = "", client.localisation_ville or ""
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if identifiants and client.location_id:
        try:
            infos = google_location.obtenir_infos_fiche(identifiants, client.location_id)
            categorie = google_location.valeurs_protegees(infos).get("categorie_nom", "")
            if not ville:
                ville = (infos.get("storefrontAddress") or {}).get("locality", "")
        except Exception:
            pass

    erreur, idees = None, None
    try:
        semences = claude_generation.suggerer_semences_mots_cles(client.contenu_site, categorie, ville)
        idees = sorted(
            google_ads_keywords.idees_mots_cles(google_oauth.obtenir_parametre_ads(db), semences),
            key=lambda i: i["volume_moyen_mensuel"] or 0,
            reverse=True,
        )
    except Exception as e:
        erreur = f"Impossible de generer des suggestions pour le moment : {e}"

    return templates.TemplateResponse(
        request, "suggestions_mots_cles.html",
        {"client": client, "categorie": categorie, "ville": ville, "idees": idees, "erreur": erreur},
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
                    google_ads_keywords.idees_mots_cles(
                        google_oauth.obtenir_parametre_ads(db), google_ads_keywords.variantes_locales(q)
                    ),
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


# --- Gestion des acces (administrateurs de fiche) ----------------------------


@app.get("/acces", response_class=HTMLResponse)
def acces_formulaire(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    return templates.TemplateResponse(
        request, "acces.html",
        {
            "etiquettes": etiquettes, "clients_json": _clients_json_avec_etiquettes(db),
            "comptes": google_oauth.lister_comptes(db), "resultats": None, "erreur": None, "message_compte": None,
        },
    )


@app.get("/acces/export")
def telecharger_acces_excel(request: Request, db: Session = Depends(obtenir_session)):
    """
    Export en lecture seule (une ligne par administrateur trouve sur chaque
    fiche selectionnee) - sert a la fois d'audit des acces actuels et de base
    pour preparer le fichier de remplacement (voir /acces/remplacer).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client_ids = [int(v) for v in request.query_params.getlist("client_ids") if v.strip()]
    if not client_ids:
        return HTMLResponse("Selectionnez au moins une fiche.", status_code=400)

    clients = db.query(models.Client).filter(models.Client.id.in_(client_ids)).order_by(models.Client.nom).all()
    octets = export_acces_excel.generer_export(db, clients)
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="acces_fiches_{date.today().isoformat()}.xlsx"'},
    )


@app.post("/acces/remplacer", response_class=HTMLResponse)
async def remplacer_acces(request: Request, fichier: UploadFile = File(...), db: Session = Depends(obtenir_session)):
    """
    Importe un fichier (colonnes : ID client, Ancien email a retirer, Nouvel
    email a inviter, Role) et execute chaque remplacement - voir
    acces_masse.py. Chaque invitation reste en attente jusqu'a acceptation
    par l'adresse invitee elle-meme (aucun moyen de forcer l'acces par API).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    contexte_base = {
        "etiquettes": etiquettes, "clients_json": _clients_json_avec_etiquettes(db),
        "comptes": google_oauth.lister_comptes(db), "resultats": None, "message_compte": None,
    }

    try:
        octets = fichier.file.read()
        lignes = acces_masse.lire_fichier_remplacement(octets)
    except Exception as erreur:
        return templates.TemplateResponse(request, "acces.html", {**contexte_base, "erreur": f"Fichier illisible : {erreur}"}, status_code=400)

    if not lignes:
        return templates.TemplateResponse(
            request, "acces.html",
            {**contexte_base, "erreur": "Aucune ligne exploitable dans ce fichier (colonnes attendues : ID client, Ancien email à retirer, Nouvel email à inviter, Rôle)."},
            status_code=400,
        )

    resultats = acces_masse.executer_remplacements(db, lignes)
    return templates.TemplateResponse(request, "acces.html", {**contexte_base, "erreur": None, "resultats": resultats})


@app.post("/acces/changer-compte-google", response_class=HTMLResponse)
def changer_compte_google_masse(
    request: Request, client_ids: list[int] = Form(default=[]),
    compte_google_id: int = Form(...), db: Session = Depends(obtenir_session),
):
    """
    Change quel compte Google connecte (voir /google/comptes) la plateforme
    utilise pour gerer les fiches selectionnees. Ne touche ni location_id ni
    account_id (l'identifiant de la fiche Google elle-meme, valable quel que
    soit le compte qui la consulte) : aucune donnee locale (historique,
    posts, positions...) n'est perdue, contrairement a une suppression +
    recreation de la fiche. Utile par ex. quand le compte initialement
    connecte n'a qu'un role Gestionnaire et ne peut pas inviter/retirer des
    administrateurs (reserve aux comptes Proprietaire par Google - voir
    google_admins.py) alors qu'un autre compte connecte l'est.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    etiquettes = db.query(models.Etiquette).order_by(models.Etiquette.nom).all()
    comptes = google_oauth.lister_comptes(db)
    contexte_base = {"etiquettes": etiquettes, "clients_json": _clients_json_avec_etiquettes(db), "comptes": comptes, "resultats": None}

    compte = db.get(models.CompteGoogle, compte_google_id)
    if not compte:
        return templates.TemplateResponse(request, "acces.html", {**contexte_base, "erreur": "Compte Google introuvable.", "message_compte": None}, status_code=400)
    if not client_ids:
        return templates.TemplateResponse(request, "acces.html", {**contexte_base, "erreur": "Sélectionnez au moins une fiche.", "message_compte": None}, status_code=400)

    clients = db.query(models.Client).filter(models.Client.id.in_(client_ids)).all()
    for client in clients:
        client.compte_google_id = compte.id
    db.commit()

    return templates.TemplateResponse(
        request, "acces.html",
        {**contexte_base, "erreur": None, "message_compte": f"{len(clients)} fiche(s) basculée(s) sur le compte « {compte.libelle} »."},
    )


# --- Audit prospect (demarchage a froid) -------------------------------------


@app.get("/prospection", response_class=HTMLResponse)
def prospection_formulaire(request: Request, nom_entreprise: str = "", ville: str = "", lead_id: int = None):
    """
    nom_entreprise/ville/lead_id : pre-remplissage optionnel depuis une
    demande d'audit gratuit (/leads, bouton "Lancer l'audit") - lead_id est
    reporte jusqu'a la generation du PDF pour le rattacher a cette demande.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection
    return templates.TemplateResponse(
        request, "prospection.html",
        {"erreur": None, "nom_entreprise": nom_entreprise, "ville": ville, "lead_id": lead_id},
    )


@app.post("/prospection/rechercher")
async def rechercher_candidats_prospect(request: Request):
    """
    Recherche a la demande (1 seule requete DataForSEO) les fiches candidates
    correspondant au nom/ville saisis, pour confirmer visuellement la bonne
    entreprise avant de lancer l'audit complet - bien plus couteux (grille de
    positions sur plusieurs points).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    nom_entreprise = (formulaire.get("nom_entreprise") or "").strip()
    ville = (formulaire.get("ville") or "").strip()
    if not nom_entreprise or not ville:
        return JSONResponse({"erreur": "Renseignez le nom de l'entreprise et la ville."}, status_code=400)

    try:
        candidats = audit_prospect.rechercher_candidats_fiche(nom_entreprise, ville)
    except Exception as erreur:
        return JSONResponse({"erreur": str(erreur)}, status_code=400)

    return JSONResponse({"candidats": candidats})


def _avis_jonathan(db: Session) -> dict:
    """
    Note et nombre d'avis de la propre fiche de Jonathan (client "Jonathan
    Hauet Marketing" dans la plateforme) - utilises comme preuve sociale sur
    la page de couverture de l'audit prospect PDF ("il applique ce qu'il
    recommande"). Best-effort : renvoie None si le client/compte n'est pas
    configure ou si l'appel echoue, la section est alors simplement omise.
    """
    client = db.query(models.Client).filter(models.Client.nom == "Jonathan Hauet Marketing").first()
    if not client:
        return None
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        return None
    aujourdhui = date.today()
    resume = google_reviews.resumer_avis(identifiants, client.account_id, client.location_id, aujourdhui, aujourdhui)
    if resume.get("note_moyenne_globale") is None:
        return None
    return {"note": resume["note_moyenne_globale"], "nombre_avis": resume["total_avis_global"]}


@app.post("/prospection/audit")
async def generer_audit_prospect(request: Request, db: Session = Depends(obtenir_session)):
    """
    Genere un audit PDF pour une entreprise qui n'est PAS cliente (prospection),
    a partir de donnees Google Maps publiques uniquement (voir audit_prospect.py -
    aucun acces authentifie a la fiche, donc pas d'historique de posts/photos).
    Chaque mot-cle teste consomme des requetes DataForSEO facturees a l'usage.
    Si issu d'une demande d'audit gratuit (champ cache lead_id), le PDF est
    rattache a cette demande pour envoi ulterieur depuis /leads.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    formulaire = await request.form()
    nom_entreprise = (formulaire.get("nom_entreprise") or "").strip()
    ville = (formulaire.get("ville") or "").strip()
    mots_cles = [m.strip() for m in [formulaire.get("mot_cle_1"), formulaire.get("mot_cle_2")] if m and m.strip()]
    try:
        taille_grille = int(formulaire.get("taille_grille") or audit_prospect.TAILLE_GRILLE_DEFAUT)
    except ValueError:
        taille_grille = audit_prospect.TAILLE_GRILLE_DEFAUT

    if not nom_entreprise or not ville or not mots_cles:
        return templates.TemplateResponse(
            request, "prospection.html",
            {"erreur": "Renseignez le nom de l'entreprise, la ville, et au moins un mot-cle."}, status_code=400,
        )

    # Si une fiche candidate a deja ete confirmee via /prospection/rechercher
    # (champs caches du formulaire), on evite une deuxieme requete DataForSEO
    # identique pour la retrouver.
    if (formulaire.get("fiche_confirmee") or "") == "1" and formulaire.get("fiche_latitude"):
        fiche = {
            "trouve": True,
            "titre": formulaire.get("fiche_titre") or "",
            "note": float(formulaire["fiche_note"]) if formulaire.get("fiche_note") else None,
            "nombre_avis": int(formulaire["fiche_nombre_avis"]) if formulaire.get("fiche_nombre_avis") else None,
            "adresse": formulaire.get("fiche_adresse") or "",
            "telephone": formulaire.get("fiche_telephone") or "",
            "categorie": formulaire.get("fiche_categorie") or "",
            "site_web": formulaire.get("fiche_site_web") or "",
            "latitude": float(formulaire["fiche_latitude"]),
            "longitude": float(formulaire["fiche_longitude"]),
            "total_photos": int(formulaire["fiche_total_photos"]) if formulaire.get("fiche_total_photos") else 0,
            "a_horaires": (formulaire.get("fiche_a_horaires") or "") == "1",
            "a_categorie_secondaire": (formulaire.get("fiche_a_categorie_secondaire") or "") == "1",
        }
    else:
        try:
            fiche = audit_prospect.rechercher_fiche_publique(nom_entreprise, ville)
        except Exception as erreur:
            return templates.TemplateResponse(request, "prospection.html", {"erreur": str(erreur)}, status_code=400)

    latitude = fiche.get("latitude") if fiche.get("trouve") else None
    longitude = fiche.get("longitude") if fiche.get("trouve") else None
    if latitude is None or longitude is None:
        try:
            lieux = geocodage.rechercher_lieu(f"{ville}, France")
        except Exception:
            lieux = []
        if not lieux:
            return templates.TemplateResponse(
                request, "prospection.html",
                {"erreur": f"Impossible de localiser la ville '{ville}' pour centrer la grille de positions."},
                status_code=400,
            )
        latitude, longitude = lieux[0]["latitude"], lieux[0]["longitude"]

    releves = []
    try:
        for mot_cle in mots_cles:
            releves.append(audit_prospect.grille_positions_prospect(
                nom_entreprise, mot_cle, latitude, longitude, taille_grille=taille_grille,
            ))
    except Exception as erreur:
        return templates.TemplateResponse(request, "prospection.html", {"erreur": str(erreur)}, status_code=400)

    analyse_site = None
    if fiche.get("site_web") and audit_site_technique.identifiants_configures():
        try:
            analyse_site = audit_site_technique.analyser_site(fiche["site_web"])
        except Exception:
            pass  # section omise si l'analyse echoue, ne bloque jamais la generation du PDF

    autorite_site = None
    if fiche.get("site_web"):
        try:
            autorite_site = audit_backlinks.analyser_autorite(fiche["site_web"])
        except Exception:
            pass  # section omise si l'appel echoue, ne bloque jamais la generation du PDF

    citations_resultats = None
    try:
        citations_resultats = citations.verifier_citations(
            nom_entreprise, ville, "FR", [annuaire["id"] for annuaire in citations.ANNUAIRES],
        )
    except Exception:
        pass  # section omise si la verification echoue, ne bloque jamais la generation du PDF

    opportunites_mots_cles = None
    if google_oauth.ads_configure(db):
        try:
            # Variantes reparties equitablement entre les mots-cles testes (jusqu'a 2), pour
            # ne pas laisser le premier accaparer tout le quota MAX_MOTS_CLES_SEMENCE.
            par_mot_cle = google_ads_keywords.MAX_MOTS_CLES_SEMENCE // max(len(mots_cles), 1)
            semences = []
            for mot_cle in mots_cles:
                semences.extend(google_ads_keywords.variantes_locales(mot_cle)[:par_mot_cle])
            semences = semences[:google_ads_keywords.MAX_MOTS_CLES_SEMENCE]
            idees = google_ads_keywords.idees_mots_cles(google_oauth.obtenir_parametre_ads(db), semences)
            deja_testes = {m.strip().lower() for m in mots_cles}
            opportunites_mots_cles = sorted(
                (i for i in idees if i["mot_cle"].strip().lower() not in deja_testes and i["volume_moyen_mensuel"]),
                key=lambda i: i["volume_moyen_mensuel"],
                reverse=True,
            )[:6]
        except Exception:
            pass  # section omise si l'appel echoue, ne bloque jamais la generation du PDF

    plan_action = None
    try:
        plan_action = claude_generation.generer_plan_action_audit(
            nom_entreprise, audit_prospect.evaluer_completude_fiche(fiche), analyse_site, releves,
            citations_resultats, opportunites_mots_cles, autorite_site,
        )
    except Exception:
        pass  # section omise si la generation IA echoue, ne bloque jamais la generation du PDF

    try:
        avis_jonathan = _avis_jonathan(db)
    except Exception:
        avis_jonathan = None  # section omise si l'appel echoue, ne bloque jamais la generation du PDF

    octets_pdf = audit_prospect_pdf.generer_audit_prospect_pdf(
        nom_entreprise, ville, fiche, releves, analyse_site, plan_action,
        citations_resultats, opportunites_mots_cles, autorite_site, avis_jonathan,
    )

    lead_id = (formulaire.get("lead_id") or "").strip()
    if lead_id:
        lead = db.get(models.LeadAudit, int(lead_id))
        if lead:
            lead.pdf_audit_base64 = base64.b64encode(octets_pdf).decode()
            lead.audite_le = datetime.utcnow()
            db.commit()

    nom_fichier = f"audit_{nom_entreprise.lower().replace(' ', '_')}.pdf"
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
    numero_whatsapp: str = Form(""),
    whatsapp_jours: list[str] = Form(default=[]),
    whatsapp_opt_in_confirme: bool = Form(False),
    hashtags_fixes: str = Form(""),
    etiquettes: list[str] = Form(default=[]),
    localisation_active: bool = Form(False),
    localisation_ville: str = Form(""),
    localisation_latitude: str = Form(""),
    localisation_longitude: str = Form(""),
    localisation_rayon_km: int = Form(15),
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
    client.numero_whatsapp = numero_whatsapp.strip().replace(" ", "").replace("+", "")
    client.whatsapp_jours = ",".join(sorted(set(whatsapp_jours), key=int)[:3])
    client.whatsapp_opt_in_confirme = whatsapp_opt_in_confirme
    client.hashtags_fixes = hashtags_fixes.strip()
    client.etiquettes = _obtenir_ou_creer_etiquettes(db, etiquettes)
    client.localisation_active = localisation_active
    client.localisation_ville = localisation_ville.strip()
    client.localisation_latitude = float(localisation_latitude) if localisation_latitude.strip() else None
    client.localisation_longitude = float(localisation_longitude) if localisation_longitude.strip() else None
    client.localisation_rayon_km = max(1, localisation_rayon_km)
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/whatsapp/envoyer-maintenant")
def envoyer_questions_whatsapp_maintenant(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Envoi manuel immediat des 5 questions WhatsApp (voir planificateur.envoyer_questions_whatsapp_pour_client) : pour tester sans attendre le jour programme."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return HTMLResponse("Client introuvable.", status_code=404)

    if not (client.numero_whatsapp and client.whatsapp_opt_in_confirme and whatsapp_business.identifiants_configures()):
        request.session["erreur_whatsapp_test"] = (
            "Numéro WhatsApp, case d'accord ou identifiants WhatsApp (Railway) manquants."
        )
        return RedirectResponse(f"/clients/{client_id}", status_code=303)

    erreur = envoyer_questions_whatsapp_pour_client(db, client)
    if erreur:
        request.session["erreur_whatsapp_test"] = erreur
        return RedirectResponse(f"/clients/{client_id}", status_code=303)
    return RedirectResponse(f"/clients/{client_id}?whatsapp_envoye=1", status_code=303)


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


# --- Connexion Meta (Facebook/Instagram, OAuth) ----------------------------


@app.get("/meta/connecter")
def meta_connecter(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    redirect_uri = str(request.url_for("meta_callback"))
    state = uuid.uuid4().hex
    request.session["meta_oauth_state"] = state
    return RedirectResponse(meta_oauth.construire_url_autorisation(redirect_uri, state))


@app.get("/meta/callback", name="meta_callback")
def meta_callback(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    if request.query_params.get("error"):
        return HTMLResponse(
            f"Connexion Meta annulée ou refusée : {request.query_params.get('error_description', '')}",
            status_code=400,
        )

    state_attendu = request.session.get("meta_oauth_state")
    if state_attendu and request.query_params.get("state") != state_attendu:
        return HTMLResponse("Etat OAuth invalide, merci de reessayer depuis /meta/connecter.", status_code=400)

    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("Code d'autorisation manquant.", status_code=400)

    redirect_uri = str(request.url_for("meta_callback"))
    try:
        access_token = meta_oauth.echanger_code(code, redirect_uri)
    except Exception as erreur:
        return HTMLResponse(f"Echec de la connexion Meta : {erreur}", status_code=400)

    meta_oauth.enregistrer_compte(db, access_token)
    return RedirectResponse("/meta/comptes", status_code=303)


@app.post("/meta/comptes/connecter_token")
def meta_connecter_token(request: Request, access_token: str = Form(...), db: Session = Depends(obtenir_session)):
    """
    Connexion alternative avec un jeton d'utilisateur systeme colle directement
    (genere depuis Business Settings > Utilisateurs systeme > Generer un jeton).
    Utile quand la fenetre de connexion habituelle (/meta/connecter) refuse de
    partager une Page appartenant au meme portefeuille business que l'app
    elle-meme - Meta bloque ce partage "vers soi-meme" dans cette fenetre,
    mais un jeton systeme genere manuellement dans Business Settings n'est pas
    concerne par cette limite.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    access_token = access_token.strip()
    try:
        meta_oauth.lister_pages(access_token)
    except Exception as erreur:
        comptes = meta_oauth.lister_comptes(db)
        return templates.TemplateResponse(
            request, "meta_comptes.html",
            {"comptes": comptes, "erreur": f"Jeton invalide ou permissions insuffisantes : {erreur}"},
            status_code=400,
        )

    meta_oauth.enregistrer_compte(db, access_token)
    return RedirectResponse("/meta/comptes", status_code=303)


@app.get("/meta/comptes", response_class=HTMLResponse)
def meta_comptes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comptes = meta_oauth.lister_comptes(db)
    return templates.TemplateResponse(request, "meta_comptes.html", {"comptes": comptes, "erreur": None})


@app.get("/meta/comptes/{compte_id}/pages", response_class=HTMLResponse)
def meta_pages(compte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteMeta, compte_id)
    if not compte:
        return RedirectResponse("/meta/comptes", status_code=303)

    clients = db.query(models.Client).order_by(models.Client.nom).all()
    contexte = {"compte": compte, "clients": clients}

    try:
        pages = meta_oauth.lister_pages(compte.access_token)
    except Exception as erreur:
        return templates.TemplateResponse(
            request, "meta_pages.html", {**contexte, "pages": [], "clients_par_page_id": {}, "erreur": str(erreur)},
        )

    clients_par_page_id = {
        c.page_id_meta: c for c in clients if c.page_id_meta
    }
    return templates.TemplateResponse(
        request, "meta_pages.html", {**contexte, "pages": pages, "clients_par_page_id": clients_par_page_id, "erreur": None},
    )


@app.post("/meta/comptes/{compte_id}/pages/lier")
def meta_lier_page(
    compte_id: int, request: Request, client_id: int = Form(...),
    page_id: str = Form(...), page_nom: str = Form(...), token_page: str = Form(...),
    instagram_id: str = Form(""), instagram_nom: str = Form(""),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if client:
        client.compte_meta_id = compte_id
        client.page_id_meta = page_id
        client.page_nom_meta = page_nom
        client.token_page_meta = token_page
        client.instagram_id_meta = instagram_id
        client.instagram_nom_meta = instagram_nom
        db.commit()

    return RedirectResponse(f"/meta/comptes/{compte_id}/pages", status_code=303)


@app.post("/meta/comptes/{compte_id}/pages/delier")
def meta_delier_page(compte_id: int, request: Request, client_id: int = Form(...), db: Session = Depends(obtenir_session)):
    """
    Detache la Page Facebook (et l'Instagram lie via cette Page) d'UN client
    precis, sans toucher au compte Meta partage (utilise par les autres
    clients) ni a un eventuel compte Instagram connecte separement (voir
    instagram_oauth.py, non concerne par ce lien Page-Facebook).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if client and client.compte_meta_id == compte_id:
        client.compte_meta_id = None
        client.page_id_meta = ""
        client.page_nom_meta = ""
        client.token_page_meta = ""
        client.instagram_id_meta = ""
        client.instagram_nom_meta = ""
        db.commit()

    return RedirectResponse(f"/meta/comptes/{compte_id}/pages", status_code=303)


@app.post("/meta/comptes/{compte_id}/deconnecter")
def meta_deconnecter_compte(compte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteMeta, compte_id)
    if compte:
        clients_lies = db.query(models.Client).filter_by(compte_meta_id=compte_id).count()
        if clients_lies:
            comptes = meta_oauth.lister_comptes(db)
            return templates.TemplateResponse(
                request, "meta_comptes.html",
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

    return RedirectResponse("/meta/comptes", status_code=303)


# --- Connexion LinkedIn (OAuth, profils personnels) ------------------------


@app.get("/linkedin/connecter")
def linkedin_connecter(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    redirect_uri = str(request.url_for("linkedin_callback"))
    state = uuid.uuid4().hex
    request.session["linkedin_oauth_state"] = state
    return RedirectResponse(linkedin_oauth.construire_url_autorisation(redirect_uri, state))


@app.get("/linkedin/callback", name="linkedin_callback")
def linkedin_callback(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    if request.query_params.get("error"):
        return HTMLResponse(
            f"Connexion LinkedIn annulée ou refusée : {request.query_params.get('error_description', '')}",
            status_code=400,
        )

    state_attendu = request.session.get("linkedin_oauth_state")
    if state_attendu and request.query_params.get("state") != state_attendu:
        return HTMLResponse("Etat OAuth invalide, merci de reessayer depuis /linkedin/connecter.", status_code=400)

    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("Code d'autorisation manquant.", status_code=400)

    redirect_uri = str(request.url_for("linkedin_callback"))
    try:
        jeton = linkedin_oauth.echanger_code(code, redirect_uri)
        userinfo = linkedin_oauth.obtenir_userinfo(jeton["access_token"])
    except Exception as erreur:
        return HTMLResponse(f"Echec de la connexion LinkedIn : {erreur}", status_code=400)

    linkedin_oauth.enregistrer_compte(db, jeton["access_token"], jeton["expire_le"], userinfo)
    return RedirectResponse("/linkedin/comptes", status_code=303)


def _posts_linkedin_programmes(db: Session):
    return (
        db.query(models.PostLinkedInProgramme)
        .filter(models.PostLinkedInProgramme.etat == "EN_ATTENTE")
        .order_by(models.PostLinkedInProgramme.publier_le)
        .all()
    )


@app.get("/linkedin/comptes", response_class=HTMLResponse)
def linkedin_comptes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comptes = linkedin_oauth.lister_comptes(db)
    clients = db.query(models.Client).order_by(models.Client.nom).all()
    clients_par_compte_id = {c.compte_linkedin_id: c for c in clients if c.compte_linkedin_id}
    return templates.TemplateResponse(
        request, "linkedin_comptes.html",
        {
            "comptes": comptes, "erreur": None, "resultat_publication": None,
            "posts_programmes": _posts_linkedin_programmes(db),
            "clients": clients, "clients_par_compte_id": clients_par_compte_id,
        },
    )


@app.post("/linkedin/comptes/{compte_id}/lier")
def linkedin_lier_compte(compte_id: int, request: Request, client_id: int = Form(...), db: Session = Depends(obtenir_session)):
    """
    Associe un profil LinkedIn personnel a un client, pour le rendre
    selectionnable dans le composeur multi-reseaux (/publication-multi) -
    pertinent quand le profil personnel du client EST la presence a publier
    (ex : Jonathan lui-meme), pas pour une page entreprise (voir
    Community Management API, toujours en attente cote LinkedIn).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteLinkedIn, compte_id)
    client = db.get(models.Client, client_id)
    if compte and client:
        client.compte_linkedin_id = compte.id
        db.commit()

    return RedirectResponse("/linkedin/comptes", status_code=303)


@app.post("/linkedin/comptes/{compte_id}/publier")
async def linkedin_publier(
    compte_id: int, request: Request, texte: str = Form(...),
    publier_date: str = Form(""), publier_heure: str = Form(""),
    image: UploadFile = File(None), db: Session = Depends(obtenir_session),
):
    """
    Sans publier_date renseignee : publication immediate (comportement
    d'origine). Avec une date (et une heure optionnelle) future : le post
    est enregistre en attente, publie automatiquement par
    planificateur.publier_posts_linkedin_programmes.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comptes = linkedin_oauth.lister_comptes(db)
    compte = db.get(models.CompteLinkedIn, compte_id)
    if not compte:
        return RedirectResponse("/linkedin/comptes", status_code=303)

    erreur, resultat_publication = None, None
    octets_image = await image.read() if image and image.filename else None

    if publier_date.strip():
        try:
            heure = publier_heure.strip() or "00:00"
            publier_le = datetime.strptime(f"{publier_date.strip()} {heure}", "%Y-%m-%d %H:%M")
        except ValueError:
            erreur = "Date ou heure de publication invalide."
        else:
            db.add(models.PostLinkedInProgramme(
                compte_linkedin_id=compte.id, texte=texte, image_donnees=octets_image, publier_le=publier_le,
            ))
            db.commit()
            resultat_publication = f"programmé pour le {publier_le.strftime('%d/%m/%Y à %H:%M')}"
    else:
        try:
            linkedin_publish.publier_post(compte.access_token, compte.identifiant_membre, texte, octets_image)
            _journaliser_publication_linkedin(db, compte.id, texte)
            resultat_publication = compte.libelle
        except Exception as e:
            erreur = f"Echec de la publication : {e}"

    clients = db.query(models.Client).order_by(models.Client.nom).all()
    return templates.TemplateResponse(
        request, "linkedin_comptes.html",
        {
            "comptes": comptes, "erreur": erreur, "resultat_publication": resultat_publication,
            "posts_programmes": _posts_linkedin_programmes(db),
            "clients": clients, "clients_par_compte_id": {c.compte_linkedin_id: c for c in clients if c.compte_linkedin_id},
        },
    )


@app.post("/linkedin/posts-programmes/{post_id}/annuler")
def linkedin_annuler_post_programme(post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.PostLinkedInProgramme, post_id)
    if post and post.etat == "EN_ATTENTE":
        db.delete(post)
        db.commit()

    return RedirectResponse("/linkedin/comptes", status_code=303)


@app.post("/linkedin/comptes/{compte_id}/deconnecter")
def linkedin_deconnecter_compte(compte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteLinkedIn, compte_id)
    if compte:
        clients = db.query(models.Client).order_by(models.Client.nom).all()
        clients_par_compte_id = {c.compte_linkedin_id: c for c in clients if c.compte_linkedin_id}
        posts_en_attente = (
            db.query(models.PostLinkedInProgramme)
            .filter_by(compte_linkedin_id=compte_id, etat="EN_ATTENTE")
            .count()
        )
        clients_lies = clients_par_compte_id.get(compte_id)
        if posts_en_attente or clients_lies:
            comptes = linkedin_oauth.lister_comptes(db)
            if posts_en_attente:
                message = (
                    f"Impossible de deconnecter ce compte : {posts_en_attente} post(s) programme(s) "
                    "en attente. Annulez-les d'abord."
                )
            else:
                message = (
                    f"Impossible de deconnecter ce compte : le client {clients_lies.nom} y est encore "
                    "rattache. Reliez-le a un autre compte d'abord (ou retirez le lien)."
                )
            return templates.TemplateResponse(
                request, "linkedin_comptes.html",
                {
                    "comptes": comptes, "resultat_publication": None,
                    "posts_programmes": _posts_linkedin_programmes(db),
                    "clients": clients, "clients_par_compte_id": clients_par_compte_id,
                    "erreur": message,
                },
                status_code=400,
            )
        db.delete(compte)
        db.commit()

    return RedirectResponse("/linkedin/comptes", status_code=303)


# --- Publication multi-reseaux (Google + Facebook + Instagram) ---


def _suggestions_du_jour_json(db: Session, client_id: int) -> str:
    """
    Sujets tendance deja generes ce matin par le planificateur (voir
    planificateur.generer_suggestions_quotidiennes), en JSON - permet au
    composeur de les afficher directement a l'ouverture, sans appel IA en
    direct via le bouton "Sujets tendance du jour" (qui reste disponible en
    repli si le lot du jour est vide ou perime). Meme forme que la reponse
    de /publication-multi/{client_id}/sujets_tendance pour reutiliser le
    meme code JS de rendu.
    """
    suggestions = (
        db.query(models.SuggestionSujetJour)
        .filter_by(client_id=client_id)
        .order_by(models.SuggestionSujetJour.id)
        .all()
    )
    return json.dumps([
        {
            "sujet": s.sujet, "titre_article": s.titre_article,
            "source": s.source, "url": s.url, "extrait": s.extrait,
        }
        for s in suggestions
    ])


def _reseaux_disponibles_client(client: "models.Client") -> dict:
    """
    LinkedIn : uniquement le profil personnel (voir /linkedin/comptes), lie
    au client via compte_linkedin_id - pas de gestion de page entreprise
    possible pour l'instant (produit "Community Management API" toujours en
    attente de validation cote LinkedIn).
    """
    return {
        "google": bool(client.account_id and client.location_id),
        "facebook": bool(client.page_id_meta and client.token_page_meta),
        "instagram": bool(client.instagram_id_meta and client.token_instagram),
        "linkedin": bool(client.compte_linkedin_id),
    }


def _posts_multi_programmes(db: Session, client: "models.Client") -> dict:
    return {
        "google": (
            db.query(models.Post)
            .filter_by(client_id=client.id, statut="A_PUBLIER")
            .order_by(models.Post.date_prevue)
            .all()
        ),
        "facebook": (
            db.query(models.PostMetaProgramme)
            .filter_by(client_id=client.id, etat="EN_ATTENTE")
            .order_by(models.PostMetaProgramme.publier_le)
            .all()
        ),
        "instagram": (
            db.query(models.PostInstagramProgramme)
            .filter_by(client_id=client.id, etat="EN_ATTENTE")
            .order_by(models.PostInstagramProgramme.publier_le)
            .all()
        ),
        "linkedin": (
            db.query(models.PostLinkedInProgramme)
            .filter_by(compte_linkedin_id=client.compte_linkedin_id, etat="EN_ATTENTE")
            .order_by(models.PostLinkedInProgramme.publier_le)
            .all()
            if client.compte_linkedin_id else []
        ),
    }


def _contexte_publication_multi(
    db: Session, client: "models.Client", texte_base: str = "", reseaux_coches: list = None,
    variantes: dict = None, erreur: str = None, resultat: str = None,
    prompt_image_initial: str = "", origine_vocale: bool = False, image_url_initial: str = "",
) -> dict:
    return {
        "client": client,
        "reseaux_disponibles": _reseaux_disponibles_client(client),
        "texte_base": texte_base,
        "reseaux_coches": reseaux_coches or [],
        "variantes": variantes or {},
        "erreur": erreur,
        "resultat": resultat,
        "posts_programmes": _posts_multi_programmes(db, client),
        "suggestions_du_jour_json": _suggestions_du_jour_json(db, client.id),
        "prompt_image_initial": prompt_image_initial,
        "options_appel_action": google_publish.OPTIONS_APPEL_ACTION,
        "origine_vocale": origine_vocale,
        "image_url_initial": image_url_initial,
        "brouillons_whatsapp_en_attente": (
            db.query(models.BrouillonWhatsApp).filter_by(client_id=client.id).order_by(models.BrouillonWhatsApp.id).all()
        ),
    }


@app.post("/publication-multi/{client_id}/favori/basculer")
def publication_multi_basculer_favori(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Bascule le statut favori (etoile) d'un client sur la liste de choix de /publication-multi."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if client:
        client.favori_publication_multi = not client.favori_publication_multi
        db.commit()

    return RedirectResponse("/publication-multi", status_code=303)


@app.get("/publication-multi", response_class=HTMLResponse)
def publication_multi_choix_client(request: Request, client_id: int = None, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    if not client_id:
        clients = (
            db.query(models.Client)
            .order_by(models.Client.favori_publication_multi.desc(), models.Client.nom)
            .all()
        )
        return templates.TemplateResponse(request, "publication_multi_choix.html", {"clients": clients})

    client = db.get(models.Client, client_id)
    if not client:
        return RedirectResponse("/publication-multi", status_code=303)

    # Brouillon issu de la capture vocale (voir /publication-multi/{id}/vocal) :
    # laisse dans la session le temps d'une redirection plutot que via un
    # query param (texte potentiellement long) ou une table dediee.
    brouillon_vocal = request.session.pop("brouillon_vocal", None)
    if brouillon_vocal and brouillon_vocal.get("client_id") == client.id:
        return templates.TemplateResponse(
            request, "publication_multi.html",
            _contexte_publication_multi(
                db, client, texte_base=brouillon_vocal.get("texte", ""),
                prompt_image_initial=brouillon_vocal.get("prompt_image", ""), origine_vocale=True,
                image_url_initial=brouillon_vocal.get("image_url") or "",
            ),
        )

    return templates.TemplateResponse(
        request, "publication_multi.html", _contexte_publication_multi(db, client),
    )


@app.post("/publication-multi/{client_id}/adapter")
async def publication_multi_adapter(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Appelee en JS (fetch) depuis publication_multi.html, pas en navigation
    complete : un envoi de formulaire classique reinitialiserait le champ
    fichier image deja selectionne (les navigateurs ne permettent pas de le
    re-remplir apres un rechargement de page), ce qui obligerait a le
    rejoindre une seconde fois avant de publier. Renvoie du JSON, pas du HTML.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    donnees = await request.json()
    texte_base = (donnees.get("texte_base") or "").strip()
    reseaux = donnees.get("reseaux") or []

    if not texte_base:
        return JSONResponse({"erreur": "Le texte de base est obligatoire."}, status_code=400)
    if not reseaux:
        return JSONResponse({"erreur": "Selectionnez au moins un reseau."}, status_code=400)

    try:
        variantes = claude_generation.adapter_post_multi_reseaux(texte_base, reseaux, client.contenu_site, client.hashtags_fixes)
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de l'adaptation IA : {e}"}, status_code=500)

    return JSONResponse({"variantes": variantes})


@app.post("/publication-multi/{client_id}/generer_texte")
async def publication_multi_generer_texte(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Genere le texte de base via claude_generation.generer_post_expert (voix
    a la premiere personne, prise de position affirmee) plutot que le
    generateur generique utilise sur /posts (volontairement neutre, pense
    pour un post publiable tel quel sur n'importe quelle fiche cliente) -
    pertinent ici car la fiche EST l'auteur (ex : Jonathan sur sa propre
    fiche), pas un post generique a dupliquer sur d'autres clients.
    contenu_article (optionnel) : extrait reel transmis par le navigateur
    quand le theme vient d'une suggestion de /sujets_tendance, pour ancrer
    la redaction sur les faits reels plutot que de laisser l'IA deviner a
    partir du seul titre (voir generer_post_expert).
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    donnees = await request.json()
    theme = (donnees.get("theme") or "").strip()
    contenu_article = (donnees.get("contenu_article") or "").strip()

    try:
        post_genere = claude_generation.generer_post_expert(theme, _contexte_ia_client(client), contenu_article)
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de la generation : {e}"}, status_code=500)

    return JSONResponse(post_genere)


@app.post("/publication-multi/{client_id}/sujets_tendance")
def publication_multi_sujets_tendance(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Propose 5 sujets a commenter, bases sur une veille d'actualite gratuite
    (flux RSS Google Actualites, voir veille_actualite.py) reformulee par
    l'IA en angles de post concrets (voir claude_generation.
    suggerer_sujets_actualite) - pas de Google Trends (pas d'API officielle
    gratuite, et les tendances generiques du jour n'ont de toute facon aucun
    rapport avec le SEO local). Tient compte des sujets deja traites sur
    cette fiche (voir _sujets_deja_traites_client) ET du lot actuellement
    affiche (pour qu'un "Actualiser" repete ne reproduise pas les memes
    suggestions), et remplace ce lot par le nouveau une fois genere, pour
    qu'un rechargement de page reste coherent avec ce qui vient d'etre
    affiche plutot que de revenir au lot du matin.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    try:
        articles = veille_actualite.rechercher_actualites()
        suggestions_actuelles = db.query(models.SuggestionSujetJour).filter_by(client_id=client.id).all()
        sujets_deja_traites = (
            _sujets_deja_traites_client(db, client.id, limite=15)
            + [s.sujet for s in suggestions_actuelles]
        )
        suggestions = claude_generation.suggerer_sujets_actualite(articles, nombre=5, sujets_deja_traites=sujets_deja_traites)
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de la veille : {e}"}, status_code=500)

    db.query(models.SuggestionSujetJour).filter_by(client_id=client.id).delete()
    for suggestion in suggestions:
        db.add(models.SuggestionSujetJour(
            client_id=client.id,
            sujet=suggestion["sujet"],
            titre_article=suggestion["titre_article"],
            source=suggestion["source"],
            url=suggestion["url"],
            extrait=suggestion["extrait"],
        ))
    db.commit()

    return JSONResponse({"suggestions": suggestions})


@app.post("/publication-multi/{client_id}/sujets_evergreen")
def publication_multi_sujets_evergreen(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Propose 5 sujets independants de l'actualite du jour (voir
    claude_generation.suggerer_sujets_evergreen) - complement aux sujets
    tendance quand la veille manque de matiere fraiche, pour garder un
    rythme de publication regulier. Pas de persistance (contrairement aux
    sujets tendance) : ces sujets ne perimeent pas d'un jour sur l'autre,
    pas besoin de les precalculer chaque matin.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    try:
        sujets_deja_traites = _sujets_deja_traites_client(db, client.id, limite=15)
        suggestions = claude_generation.suggerer_sujets_evergreen(
            _contexte_ia_client(client), sujets_deja_traites, nombre=5,
        )
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de la generation : {e}"}, status_code=500)

    return JSONResponse({"suggestions": suggestions})


@app.get("/publication-multi/{client_id}/vocal", response_class=HTMLResponse)
def publication_multi_vocal(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Capture rapide au telephone : 5 questions -> reponse dictee a la voix ->
    post genere automatiquement (voir claude_generation.
    generer_post_depuis_reponse), avant de rejoindre le composeur classique
    (reseaux, image, programmation) deja pret a publier. Page volontairement
    separee et minimaliste plutot qu'integree au composeur complet, pense
    mobile en premier.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return RedirectResponse("/publication-multi", status_code=303)

    return templates.TemplateResponse(request, "capture_vocale.html", {"client": client})


@app.post("/publication-multi/{client_id}/questions_interview")
def publication_multi_questions_interview(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Propose 5 questions pensees pour etre repondues a l'oral (voir claude_generation.generer_questions_interview)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    try:
        sujets_deja_traites = _sujets_deja_traites_client(db, client.id, limite=15)
        questions = claude_generation.generer_questions_interview(
            _contexte_ia_client(client), sujets_deja_traites, nombre=5,
        )
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de la generation : {e}"}, status_code=500)

    return JSONResponse({"questions": questions})


@app.post("/publication-multi/{client_id}/generer_depuis_reponse")
async def publication_multi_generer_depuis_reponse(
    client_id: int, request: Request, question: str = Form(...), reponse: str = Form(...),
    db: Session = Depends(obtenir_session),
):
    """
    Transforme la reponse dictee (voir claude_generation.
    generer_post_depuis_reponse) en post, puis redirige vers le composeur
    complet avec le texte deja pret (voir le brouillon_vocal en session,
    consomme par publication_multi_choix_client) - reutilise tout le
    parcours existant (reseaux, image, programmation) plutot que de
    dupliquer cette logique ici.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return RedirectResponse("/publication-multi", status_code=303)

    try:
        post = claude_generation.generer_post_depuis_reponse(question, reponse, _contexte_ia_client(client))
    except Exception as e:
        return templates.TemplateResponse(
            request, "capture_vocale.html",
            {"client": client, "erreur": f"Echec de la generation : {e}", "question": question, "reponse": reponse},
            status_code=500,
        )

    request.session["brouillon_vocal"] = {
        "client_id": client.id, "texte": post["texte"], "prompt_image": post.get("prompt_image", ""),
    }
    return RedirectResponse(f"/publication-multi?client_id={client.id}", status_code=303)


# --- Mode rapide vocal par WhatsApp -----------------------------------------


@app.get("/whatsapp/webhook")
def whatsapp_webhook_verification(request: Request):
    """Poignee de main exigee par Meta au moment de configurer le webhook (voir Business Settings)."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and whatsapp_business.VERIFY_TOKEN and token == whatsapp_business.VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification token invalide.", status_code=403)


def _traiter_message_whatsapp(db: Session, message: dict) -> None:
    """
    Machine a etats minimale (voir models.EtatConversationWhatsApp) pour une
    conversation WhatsApp : texte "1" a "5" -> choisit la question, photo ->
    memorisee, vocal -> transcrit et poste genere (voir
    claude_generation.generer_post_depuis_reponse). Chaque erreur est
    avalee : un webhook qui repond une erreur HTTP a Meta declenche des
    reessais automatiques repetes, pire que de simplement ignorer un
    message qu'on n'a pas su traiter.
    """
    numero = message.get("from", "")
    type_message = message.get("type")
    if not numero or not type_message:
        return

    client = db.query(models.Client).filter(models.Client.numero_whatsapp == numero).first()
    if not client:
        return

    etat = db.query(models.EtatConversationWhatsApp).filter_by(numero=numero).first()
    if not etat:
        etat = models.EtatConversationWhatsApp(client_id=client.id, numero=numero)
        db.add(etat)
        db.commit()
        db.refresh(etat)

    if type_message == "text":
        texte = (message.get("text", {}).get("body") or "").strip()
        if texte in {"1", "2", "3", "4", "5"}:
            questions = json.loads(etat.questions_json or "[]")
            index = int(texte) - 1
            if 0 <= index < len(questions):
                etat.question_choisie = questions[index]
                etat.maj_le = datetime.utcnow()
                db.commit()
                try:
                    whatsapp_business.envoyer_message_texte(
                        numero,
                        "Noté ! Envoyez votre réponse vocale (et une photo si vous voulez) quand vous êtes prêt.",
                    )
                except Exception:
                    pass
        return

    if type_message == "image":
        try:
            media_id = message["image"]["id"]
            octets, mime = whatsapp_business.telecharger_media(media_id)
            extension = ".png" if "png" in mime else ".jpg"
            nom_fichier = f"whatsapp_{client.id}_{uuid.uuid4().hex[:10]}{extension}"
            url_image = ovh_upload.envoyer_octets(octets, nom_fichier)
            etat.image_url = url_image
            etat.maj_le = datetime.utcnow()
            # Photo envoyee APRES le vocal (l'etat a deja ete supprime puis
            # recree vide par le vocal) : on la rattache au dernier brouillon
            # WhatsApp de ce client pas encore charge dans le composeur,
            # sinon elle serait perdue (stockee sur un etat orphelin que plus
            # rien ne relit).
            if not etat.question_choisie:
                dernier_brouillon = (
                    db.query(models.BrouillonWhatsApp)
                    .filter(models.BrouillonWhatsApp.client_id == client.id, models.BrouillonWhatsApp.image_url.is_(None))
                    .order_by(models.BrouillonWhatsApp.cree_le.desc())
                    .first()
                )
                if dernier_brouillon:
                    dernier_brouillon.image_url = url_image
            db.commit()
            try:
                whatsapp_business.envoyer_message_texte(
                    numero, "Photo bien reçue 📸 (si vous en aviez déjà envoyé une, celle-ci la remplace).",
                )
            except Exception:
                pass
        except Exception as erreur:
            notifications.notifier("Echec photo WhatsApp", f"Photo de {numero} non enregistree : {erreur}")
        return

    if type_message == "audio":
        if not etat.question_choisie:
            try:
                whatsapp_business.envoyer_message_texte(
                    numero, "Répondez d'abord avec le numéro (1 à 5) de la question choisie, puis renvoyez votre vocal.",
                )
            except Exception:
                pass
            return
        try:
            media_id = message["audio"]["id"]
            octets, mime = whatsapp_business.telecharger_media(media_id)
            transcription = whatsapp_business.transcrire_audio(octets, mime)
            post = claude_generation.generer_post_depuis_reponse(
                etat.question_choisie, transcription, _contexte_ia_client(client),
            )
            db.add(models.BrouillonWhatsApp(
                client_id=client.id, texte=post["texte"], prompt_image=post.get("prompt_image", ""),
                image_url=etat.image_url,
            ))
            db.add(models.ReponseInterviewClient(
                client_id=client.id, question=etat.question_choisie, reponse_transcrite=transcription,
            ))
            db.delete(etat)
            db.commit()
            # Le lien vers le composeur (acces reserve a la plateforme) part
            # uniquement via la notification push, jamais par WhatsApp : le
            # numero qui repond ici peut etre celui d'un client, qui ne doit
            # pas recevoir de lien d'administration.
            whatsapp_business.envoyer_message_texte(numero, "Merci, c'est bien reçu ! Votre contenu est en cours de préparation.")
            lien_composeur = f"https://web-production-bf59a.up.railway.app/publication-multi/{client.id}"
            notifications.notifier(
                "Post pret a valider",
                f"{client.nom} : un post genere depuis WhatsApp attend votre relecture.",
                url=lien_composeur,
            )
        except Exception:
            try:
                whatsapp_business.envoyer_message_texte(numero, "Un souci est survenu pendant la génération, réessayez dans un instant.")
            except Exception:
                pass
        return


def _traiter_statut_whatsapp(statut: dict) -> None:
    """
    Notifie l'evolution de statut d'un message business-initie (envoye,
    distribue, echec...) - Meta renvoie ces mises a jour sur le meme webhook
    que les messages recus, mais elles etaient jusque-la ignorees. Utile
    pour diagnostiquer un envoi qui n'arrive pas sans avoir a se fier a
    l'historique d'activite du Gestionnaire WhatsApp (qui ne liste que les
    actions de configuration, pas les statuts de livraison).
    """
    etat = statut.get("status")
    destinataire = statut.get("recipient_id", "")
    if etat == "failed":
        erreurs = statut.get("errors", [])
        detail = "; ".join(e.get("title", "erreur inconnue") for e in erreurs) or "erreur inconnue"
        notifications.notifier("Echec envoi WhatsApp", f"Message vers {destinataire} : {detail}")
    elif etat in {"sent", "delivered"}:
        notifications.notifier("Statut message WhatsApp", f"{etat} - vers {destinataire}")


@app.post("/whatsapp/webhook")
async def whatsapp_webhook_reception(request: Request, db: Session = Depends(obtenir_session)):
    donnees = await request.json()
    try:
        for entree in donnees.get("entry", []):
            for changement in entree.get("changes", []):
                valeur = changement.get("value", {})
                for message in valeur.get("messages", []):
                    _traiter_message_whatsapp(db, message)
                for statut in valeur.get("statuses", []):
                    _traiter_statut_whatsapp(statut)
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@app.post("/publication-multi/{client_id}/charger_brouillon_whatsapp/{brouillon_id}")
def publication_multi_charger_brouillon_whatsapp(
    client_id: int, brouillon_id: int, request: Request, db: Session = Depends(obtenir_session),
):
    """Charge un brouillon genere depuis une reponse WhatsApp (voir models.BrouillonWhatsApp) dans le composeur, puis le supprime (usage unique)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    brouillon = db.get(models.BrouillonWhatsApp, brouillon_id)
    if brouillon and brouillon.client_id == client_id:
        request.session["brouillon_vocal"] = {
            "client_id": client_id, "texte": brouillon.texte, "prompt_image": brouillon.prompt_image,
            "image_url": brouillon.image_url,
        }
        db.delete(brouillon)
        db.commit()

    return RedirectResponse(f"/publication-multi?client_id={client_id}", status_code=303)


@app.post("/publication-multi/{client_id}/generer_image")
async def publication_multi_generer_image(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Genere une image via Gemini a partir d'un prompt (voir gemini_images.py -
    meme mecanisme que la generation d'image sur un post deja cree, mais ici
    rien n'est encore enregistre en base : la composition multi-reseaux n'est
    qu'un formulaire tant qu'elle n'est pas publiee/programmee). Renvoie
    l'URL hebergee sur OVH, prete a servir d'image partagee du formulaire.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return JSONResponse({"erreur": "Session expiree, merci de recharger la page."}, status_code=401)

    client = db.get(models.Client, client_id)
    if not client:
        return JSONResponse({"erreur": "Client introuvable."}, status_code=404)

    donnees = await request.json()
    prompt_image = (donnees.get("prompt_image") or "").strip()
    inclure_reference = bool(donnees.get("inclure_reference"))
    if not prompt_image:
        return JSONResponse({"erreur": "Aucun prompt image fourni."}, status_code=400)

    images_reference = None
    if inclure_reference and client.photos_reference:
        images_reference = []
        for photo in client.photos_reference:
            try:
                # JPEG reduit : alleger l'envoi a Gemini et garantir un format lisible
                # (les photos de reference sont stockees telles que televersees).
                images_reference.append(_jpeg_normalise(requests.get(photo.image_url, timeout=20).content, 1536))
            except Exception:
                continue
        if not images_reference:
            return JSONResponse({"erreur": "Impossible de recuperer les photos de reference."}, status_code=500)

        # Les prompts de post decrivent des scenes sans personne : on les reecrit
        # pour que l'auteur soit le sujet (sinon Gemini ignore les photos).
        try:
            prompt_image = claude_generation.prompt_image_avec_auteur(prompt_image, (donnees.get("texte_post") or "").strip())
        except Exception:
            pass  # repli : prompt d'origine, complete par la consigne de gemini_images

    try:
        # Carre plutot que paysage : reste correct sur Google/Facebook/LinkedIn
        # et evite le format mal adapte a Instagram (bandes noires) qui
        # resulterait du format paysage par defaut.
        octets_image = gemini_images.generer_image(prompt_image, aspect_ratio="1:1", images_reference=images_reference)
        nom_fichier = f"multi-ia-{uuid.uuid4().hex[:10]}.png"
        url_image = ovh_upload.envoyer_octets(octets_image, nom_fichier)
    except Exception as e:
        return JSONResponse({"erreur": f"Echec de la generation de l'image : {e}"}, status_code=500)

    return JSONResponse({"url": url_image, "prompt_utilise": prompt_image if images_reference else ""})


@app.post("/publication-multi/{client_id}/publier", response_class=HTMLResponse)
async def publication_multi_publier(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """
    Sans publier_date renseignee : publication immediate sur chaque reseau
    coche. Avec une date (et une heure optionnelle) future : chaque reseau
    coche est mis en attente (statut/etat propre a son modele) et publie
    automatiquement par le planificateur correspondant.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client:
        return RedirectResponse("/publication-multi", status_code=303)

    formulaire = await request.form()
    reseaux = formulaire.getlist("reseaux")
    texte_base = (formulaire.get("texte_base") or "").strip()
    variantes_soumises = {r: (formulaire.get(f"texte_{r}") or "").strip() for r in ("google", "facebook", "instagram", "linkedin")}

    def _erreur(message, code=400):
        return templates.TemplateResponse(
            request, "publication_multi.html",
            _contexte_publication_multi(db, client, texte_base, reseaux, variantes_soumises, message),
            status_code=code,
        )

    if not reseaux:
        return _erreur("Selectionnez au moins un reseau a publier.")

    reseaux_disponibles = _reseaux_disponibles_client(client)
    indisponibles = [r for r in reseaux if not reseaux_disponibles.get(r)]
    if indisponibles:
        noms = ", ".join(claude_generation.NOMS_RESEAUX.get(r, r) for r in indisponibles)
        return _erreur(f"Ce client n'a pas de compte connecte pour : {noms}.")

    publier_le = None
    publier_date = (formulaire.get("publier_date") or "").strip()
    if publier_date:
        try:
            heure = (formulaire.get("publier_heure") or "").strip() or "00:00"
            publier_le = datetime.strptime(f"{publier_date} {heure}", "%Y-%m-%d %H:%M")
        except ValueError:
            return _erreur("Date ou heure de publication invalide.")

    # Image partagee (optionnelle), televersee une seule fois sur OVH et
    # reutilisee pour chaque reseau sans image dediee. Un fichier choisi a la
    # main est prioritaire ; sinon on reprend l'image deja generee par IA
    # (deja hebergee sur OVH via /generer_image, pas besoin de la retraiter).
    # Bouton d'appel a l'action Google (voir OPTIONS_APPEL_ACTION) : "Appeler
    # maintenant" par defaut (comme sur /posts), URL obligatoire pour les
    # autres boutons. Valide avant toute publication pour ne rien envoyer a
    # moitie sur les autres reseaux.
    valeur_cta_google = formulaire.get("type_appel_action_google")
    type_cta_google = "CALL" if valeur_cta_google is None else valeur_cta_google.strip()  # "" = aucun bouton, choix explicite
    url_cta_google = (formulaire.get("url_appel_action_google") or "").strip()
    if type_cta_google not in {valeur for valeur, _ in google_publish.OPTIONS_APPEL_ACTION}:
        type_cta_google = "CALL"
    if type_cta_google in ("", "CALL"):
        url_cta_google = ""
    elif "google" in reseaux and not url_cta_google:
        return _erreur("Google : l'adresse (URL) du bouton d'appel à l'action est obligatoire pour ce type de bouton.")

    # Plusieurs images possibles par televersement (carrousel Instagram, post
    # multi-photos Facebook) ; l'image generee par IA reste unique. Google et
    # LinkedIn n'utilisent que la premiere. Une image dediee a un reseau
    # (case "image differente pour ce reseau") reste unique et prioritaire.
    fichiers_partages = [f for f in formulaire.getlist("image") if getattr(f, "filename", "")]
    if len(fichiers_partages) > NB_MAX_IMAGES_PUBLICATION:
        return _erreur(f"{NB_MAX_IMAGES_PUBLICATION} images maximum par publication.")

    urls_partagees = []
    urls_par_reseau = {}
    try:
        if fichiers_partages:
            for fichier in fichiers_partages:
                urls_partagees.append(_televerser_image_publication(await fichier.read(), "multi"))
        else:
            url_ia = (formulaire.get("image_url_ia") or "").strip()
            urls_partagees = [url_ia] if url_ia else []

        for reseau in reseaux:
            fichier_reseau = formulaire.get(f"image_{reseau}")
            if fichier_reseau is not None and getattr(fichier_reseau, "filename", ""):
                urls_par_reseau[reseau] = [_televerser_image_publication(await fichier_reseau.read(), f"multi-{reseau}")]
            else:
                urls_par_reseau[reseau] = list(urls_partagees)
    except ValueError as erreur_image:
        return _erreur(f"Image refusée : {erreur_image}")

    if "instagram" in reseaux and not urls_par_reseau.get("instagram"):
        return _erreur("Instagram necessite une image (partagee ou dediee).")

    echecs = []
    for reseau in reseaux:
        texte = variantes_soumises.get(reseau) or texte_base
        if not texte:
            echecs.append(f"{claude_generation.NOMS_RESEAUX.get(reseau, reseau)} : texte manquant.")
            continue
        try:
            if reseau == "google":
                post = models.Post(
                    client_id=client.id, titre=texte[:60], texte=texte, image_url=(urls_par_reseau.get("google") or [""])[0],
                    type_appel_action=type_cta_google, url_appel_action=url_cta_google,
                    statut="A_PUBLIER" if publier_le else "BROUILLON",
                    date_prevue=publier_le.date() if publier_le else None,
                    heure_prevue=publier_le.strftime("%H:%M") if publier_le else None,
                )
                db.add(post)
                db.commit()
                db.refresh(post)
                if not publier_le:
                    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
                    if not identifiants:
                        raise RuntimeError("Google n'est pas connecte pour ce client.")
                    google_publish.publier_et_verifier(db, identifiants, post)

            elif reseau == "facebook":
                if publier_le:
                    db.add(models.PostMetaProgramme(
                        client_id=client.id, texte=texte, image_url=meta_publish.champ_depuis_urls(urls_par_reseau.get("facebook")),
                        publier_le=publier_le,
                    ))
                    db.commit()
                else:
                    meta_publish.publier_post_page(
                        client.token_page_meta, client.page_id_meta, texte, urls_par_reseau.get("facebook"),
                    )

            elif reseau == "instagram":
                if publier_le:
                    db.add(models.PostInstagramProgramme(
                        client_id=client.id, texte=texte, image_url=meta_publish.champ_depuis_urls(urls_par_reseau.get("instagram")),
                        publier_le=publier_le,
                    ))
                    db.commit()
                else:
                    instagram_publish.publier_medias(
                        client.token_instagram, client.instagram_id_meta, urls_par_reseau["instagram"], texte,
                    )

            elif reseau == "linkedin":
                compte_linkedin = db.get(models.CompteLinkedIn, client.compte_linkedin_id)
                if not compte_linkedin:
                    raise RuntimeError("LinkedIn n'est pas connecte pour ce client.")
                # linkedin_publish attend les octets de l'image (televersement direct
                # a l'API LinkedIn), contrairement aux autres reseaux qui n'ont besoin
                # que d'une URL publique - on retelecharge donc depuis l'hebergement
                # OVH ou l'image vient d'etre envoyee.
                urls_linkedin = urls_par_reseau.get("linkedin") or []
                octets_image = requests.get(urls_linkedin[0], timeout=30).content if urls_linkedin else None
                if publier_le:
                    db.add(models.PostLinkedInProgramme(
                        compte_linkedin_id=compte_linkedin.id, texte=texte, image_donnees=octets_image, publier_le=publier_le,
                    ))
                    db.commit()
                else:
                    linkedin_publish.publier_post(
                        compte_linkedin.access_token, compte_linkedin.identifiant_membre, texte, octets_image,
                    )
                    _journaliser_publication_linkedin(db, compte_linkedin.id, texte)
        except Exception as e:
            echecs.append(f"{claude_generation.NOMS_RESEAUX.get(reseau, reseau)} : {e}")

    if echecs:
        return templates.TemplateResponse(
            request, "publication_multi.html",
            _contexte_publication_multi(db, client, texte_base, reseaux, variantes_soumises, " / ".join(echecs)),
            status_code=207,
        )

    return templates.TemplateResponse(
        request, "publication_multi.html",
        _contexte_publication_multi(
            db, client, resultat=("programmé" if publier_le else "publié"),
        ),
    )


def _retour_apres_annulation(retour: str, client_id: int) -> str:
    """Depuis le resume de la fiche client (retour="client") on revient sur la fiche ; depuis le composeur, sur le composeur."""
    return f"/clients/{client_id}" if retour == "client" else f"/publication-multi?client_id={client_id}"


MODELES_POSTS_PROGRAMMES = {
    "facebook": models.PostMetaProgramme,
    "instagram": models.PostInstagramProgramme,
    "linkedin": models.PostLinkedInProgramme,
}
NOMS_RESEAUX_PROGRAMMES = {"facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn"}


def _post_programme_modifiable(db: Session, reseau: str, post_id: int):
    """Renvoie (post, client) d'un post programme encore en attente, sinon (None, None)."""
    modele = MODELES_POSTS_PROGRAMMES.get(reseau)
    post = db.get(modele, post_id) if modele else None
    if not post or post.etat != "EN_ATTENTE":
        return None, None
    if reseau == "linkedin":
        client = db.query(models.Client).filter_by(compte_linkedin_id=post.compte_linkedin_id).first()
    else:
        client = db.get(models.Client, post.client_id)
    return post, client


def _reponse_modifier_post_programme(request: Request, reseau: str, post, client, erreur: str = None, code: int = 200):
    images = meta_publish.urls_depuis_champ(getattr(post, "image_url", "")) if reseau != "linkedin" else []
    return templates.TemplateResponse(
        request, "publication_programmee_modifier.html",
        {
            "reseau": reseau, "nom_reseau": NOMS_RESEAUX_PROGRAMMES[reseau], "post": post, "client": client,
            "date_iso": post.publier_le.strftime("%Y-%m-%d"), "heure": post.publier_le.strftime("%H:%M"),
            "images": images, "a_une_image_linkedin": reseau == "linkedin" and bool(post.image_donnees),
            "erreur": erreur,
        },
        status_code=code,
    )


@app.get("/publication-multi/{reseau}/{post_id}/modifier", response_class=HTMLResponse)
def publication_multi_modifier_formulaire(reseau: str, post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post, client = _post_programme_modifiable(db, reseau, post_id)
    if not post or not client:
        return RedirectResponse("/publication-multi", status_code=303)
    return _reponse_modifier_post_programme(request, reseau, post, client)


@app.post("/publication-multi/{reseau}/{post_id}/modifier")
async def publication_multi_modifier(reseau: str, post_id: int, request: Request, db: Session = Depends(obtenir_session)):
    """Modifie le texte et la date/heure d'un post programme (pas ses images : il faut alors le supprimer et le reprogrammer)."""
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post, client = _post_programme_modifiable(db, reseau, post_id)
    if not post or not client:
        return RedirectResponse("/publication-multi", status_code=303)

    formulaire = await request.form()
    texte = (formulaire.get("texte") or "").strip()
    if not texte:
        return _reponse_modifier_post_programme(request, reseau, post, client, erreur="Le texte du post est obligatoire.", code=400)
    try:
        publier_le = datetime.strptime(
            f"{(formulaire.get('publier_date') or '').strip()} {(formulaire.get('publier_heure') or '').strip() or '00:00'}",
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        return _reponse_modifier_post_programme(request, reseau, post, client, erreur="Date ou heure de publication invalide.", code=400)

    post.texte = texte
    post.publier_le = publier_le
    db.commit()
    return RedirectResponse(f"/clients/{client.id}", status_code=303)


@app.post("/publication-multi/facebook/{post_id}/annuler")
def publication_multi_annuler_facebook(post_id: int, request: Request, retour: str = Form(""), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.PostMetaProgramme, post_id)
    if post and post.etat == "EN_ATTENTE":
        client_id = post.client_id
        db.delete(post)
        db.commit()
        return RedirectResponse(_retour_apres_annulation(retour, client_id), status_code=303)

    return RedirectResponse("/publication-multi", status_code=303)


@app.post("/publication-multi/instagram/{post_id}/annuler")
def publication_multi_annuler_instagram(post_id: int, request: Request, retour: str = Form(""), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.PostInstagramProgramme, post_id)
    if post and post.etat == "EN_ATTENTE":
        client_id = post.client_id
        db.delete(post)
        db.commit()
        return RedirectResponse(_retour_apres_annulation(retour, client_id), status_code=303)

    return RedirectResponse("/publication-multi", status_code=303)


@app.post("/publication-multi/linkedin/{post_id}/annuler")
def publication_multi_annuler_linkedin(post_id: int, request: Request, retour: str = Form(""), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.PostLinkedInProgramme, post_id)
    if post and post.etat == "EN_ATTENTE":
        client = db.query(models.Client).filter_by(compte_linkedin_id=post.compte_linkedin_id).first()
        db.delete(post)
        db.commit()
        if client:
            return RedirectResponse(_retour_apres_annulation(retour, client.id), status_code=303)

    return RedirectResponse("/publication-multi", status_code=303)


# --- Connexion Instagram (Business Login for Instagram, OAuth separe) -----


def _contexte_demo_meta(access_token: str, identifiant_instagram: str, **supplement) -> dict:
    contexte = {
        "access_token": access_token, "instagram_id": identifiant_instagram,
        "nom_utilisateur": instagram_oauth._recuperer_libelle(access_token),
        "erreur": None, "erreur_publication": None, "resultat_publication": None,
        **supplement,
    }
    try:
        contexte["medias"] = instagram_engagement.lister_medias_avec_commentaires(access_token, identifiant_instagram, limite=5)
    except Exception as erreur:
        contexte["medias"] = []
        contexte["erreur_medias"] = str(erreur)
    try:
        contexte["insights"] = instagram_engagement.obtenir_insights(access_token, identifiant_instagram)
    except Exception as erreur:
        contexte["insights"] = []
        contexte["erreur_insights"] = str(erreur)
    return contexte


@app.post("/demo-meta/instagram/publier", response_class=HTMLResponse)
async def demo_meta_instagram_publier(
    request: Request, access_token: str = Form(...), instagram_id: str = Form(...),
    legende: str = Form(""), image: UploadFile = File(...),
):
    contexte = _contexte_demo_meta(access_token, instagram_id)
    try:
        octets = await image.read()
        extension = os.path.splitext(image.filename or "")[1] or ".jpg"
        url_image = ovh_upload.envoyer_octets(octets, f"demo-meta-{uuid.uuid4().hex}{extension}")
        contexte["resultat_publication"] = instagram_publish.publier_photo(access_token, instagram_id, url_image, legende)
    except Exception as erreur:
        contexte["erreur_publication"] = str(erreur)

    return templates.TemplateResponse(request, "demo_meta_resultat.html", contexte)


@app.get("/instagram/connecter")
def instagram_connecter(request: Request):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    redirect_uri = str(request.url_for("instagram_callback"))
    state = uuid.uuid4().hex
    request.session["instagram_oauth_state"] = state
    request.session.pop("instagram_demo", None)
    return RedirectResponse(instagram_oauth.construire_url_autorisation(redirect_uri, state))


@app.get("/demo-meta", response_class=HTMLResponse)
def demo_meta(request: Request):
    """
    Page publique et isolee (aucune donnee client, aucune connexion a Fiche
    Locale requise) destinee aux revieweurs Meta pour tester en autonomie le
    flux "Business Login for Instagram" - voir instagram_callback ci-dessous
    pour la branche "demo" qui n'enregistre rien en base.
    """
    return templates.TemplateResponse(request, "demo_meta.html", {})


@app.get("/demo-meta/instagram/connecter")
def demo_meta_instagram_connecter(request: Request):
    redirect_uri = str(request.url_for("instagram_callback"))
    state = uuid.uuid4().hex
    request.session["instagram_oauth_state"] = state
    request.session["instagram_demo"] = "1"
    return RedirectResponse(instagram_oauth.construire_url_autorisation(redirect_uri, state))


@app.get("/instagram/callback", name="instagram_callback")
def instagram_callback(request: Request, db: Session = Depends(obtenir_session)):
    mode_demo = bool(request.session.pop("instagram_demo", None))

    if not mode_demo:
        redirection = rediriger_si_non_connecte(request)
        if redirection:
            return redirection

    if request.query_params.get("error"):
        message_erreur = f"Connexion Instagram annulée ou refusée : {request.query_params.get('error_description', '')}"
        if mode_demo:
            return templates.TemplateResponse(request, "demo_meta.html", {"erreur": message_erreur}, status_code=400)
        return HTMLResponse(message_erreur, status_code=400)

    state_attendu = request.session.get("instagram_oauth_state")
    if state_attendu and request.query_params.get("state") != state_attendu:
        message_erreur = "Etat OAuth invalide, merci de reessayer depuis le debut."
        if mode_demo:
            return templates.TemplateResponse(request, "demo_meta.html", {"erreur": message_erreur}, status_code=400)
        return HTMLResponse(message_erreur, status_code=400)

    code = request.query_params.get("code")
    if not code:
        message_erreur = "Code d'autorisation manquant."
        if mode_demo:
            return templates.TemplateResponse(request, "demo_meta.html", {"erreur": message_erreur}, status_code=400)
        return HTMLResponse(message_erreur, status_code=400)

    redirect_uri = str(request.url_for("instagram_callback"))
    try:
        access_token, identifiant_instagram = instagram_oauth.echanger_code(code, redirect_uri)
    except Exception as erreur:
        if mode_demo:
            return templates.TemplateResponse(
                request, "demo_meta.html", {"erreur": f"Echec de la connexion Instagram : {erreur}"}, status_code=400,
            )
        return HTMLResponse(f"Echec de la connexion Instagram : {erreur}", status_code=400)

    if mode_demo:
        # Rien n'est enregistre en base : le token n'est utilise que pour cet
        # affichage, puis oublie - la demo ne doit laisser aucune trace liee
        # au compte Instagram du revieweur.
        return templates.TemplateResponse(request, "demo_meta_resultat.html", _contexte_demo_meta(access_token, identifiant_instagram))

    instagram_oauth.enregistrer_compte(db, access_token, identifiant_instagram)
    return RedirectResponse("/instagram/comptes", status_code=303)


@app.get("/instagram/comptes", response_class=HTMLResponse)
def instagram_comptes(request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    comptes = instagram_oauth.lister_comptes(db)
    clients = db.query(models.Client).order_by(models.Client.nom).all()
    clients_par_compte_id = {c.compte_instagram_id: c for c in clients if c.compte_instagram_id}
    return templates.TemplateResponse(
        request, "instagram_comptes.html",
        {"comptes": comptes, "clients": clients, "clients_par_compte_id": clients_par_compte_id, "erreur": None},
    )


@app.post("/instagram/comptes/{compte_id}/lier")
def instagram_lier_compte(compte_id: int, request: Request, client_id: int = Form(...), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteInstagram, compte_id)
    client = db.get(models.Client, client_id)
    if compte and client:
        client.compte_instagram_id = compte.id
        client.instagram_id_meta = compte.identifiant_instagram
        client.instagram_nom_meta = compte.libelle
        client.token_instagram = compte.access_token
        db.commit()

    return RedirectResponse("/instagram/comptes", status_code=303)


@app.post("/instagram/comptes/{compte_id}/deconnecter")
def instagram_deconnecter_compte(compte_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    compte = db.get(models.CompteInstagram, compte_id)
    if compte:
        clients_lies = db.query(models.Client).filter_by(compte_instagram_id=compte_id).count()
        if clients_lies:
            comptes = instagram_oauth.lister_comptes(db)
            clients = db.query(models.Client).order_by(models.Client.nom).all()
            clients_par_compte_id = {c.compte_instagram_id: c for c in clients if c.compte_instagram_id}
            return templates.TemplateResponse(
                request, "instagram_comptes.html",
                {
                    "comptes": comptes, "clients": clients, "clients_par_compte_id": clients_par_compte_id,
                    "erreur": (
                        f"Impossible de deconnecter ce compte : {clients_lies} client(s) y sont "
                        "encore rattaches. Reassignez-les d'abord a un autre compte."
                    ),
                },
                status_code=400,
            )
        db.delete(compte)
        db.commit()

    return RedirectResponse("/instagram/comptes", status_code=303)


def _contexte_meta_test(client) -> dict:
    contexte = {
        "posts": [], "erreur_posts": None, "insights": [], "erreur_insights": None,
        "medias_instagram": [], "erreur_medias_instagram": None, "insights_instagram": [], "erreur_insights_instagram": None,
    }
    try:
        contexte["posts"] = meta_engagement.lister_posts_avec_commentaires(client.token_page_meta, client.page_id_meta)
    except Exception as erreur:
        contexte["erreur_posts"] = str(erreur)
    try:
        contexte["insights"] = meta_engagement.obtenir_insights_page(client.token_page_meta, client.page_id_meta)
    except Exception as erreur:
        contexte["erreur_insights"] = str(erreur)

    if client.instagram_id_meta and client.token_instagram:
        try:
            contexte["medias_instagram"] = instagram_engagement.lister_medias_avec_commentaires(
                client.token_instagram, client.instagram_id_meta
            )
        except Exception as erreur:
            contexte["erreur_medias_instagram"] = str(erreur)
        try:
            contexte["insights_instagram"] = instagram_engagement.obtenir_insights(client.token_instagram, client.instagram_id_meta)
        except Exception as erreur:
            contexte["erreur_insights_instagram"] = str(erreur)

    return contexte


@app.get("/clients/{client_id}/meta/publier", response_class=HTMLResponse)
def meta_publier_formulaire(client_id: int, request: Request, db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client or not client.page_id_meta:
        return RedirectResponse(f"/clients/{client_id}", status_code=303)

    return templates.TemplateResponse(
        request, "meta_publier_test.html", {"client": client, "erreur": None, "resultat": None, **_contexte_meta_test(client)},
    )


@app.post("/clients/{client_id}/meta/publier", response_class=HTMLResponse)
def meta_publier(client_id: int, request: Request, message: str = Form(...), db: Session = Depends(obtenir_session)):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client or not client.page_id_meta:
        return RedirectResponse(f"/clients/{client_id}", status_code=303)

    try:
        resultat = meta_publish.publier_post_page(client.token_page_meta, client.page_id_meta, message)
    except Exception as erreur:
        return templates.TemplateResponse(
            request, "meta_publier_test.html",
            {"client": client, "erreur": str(erreur), "resultat": None, **_contexte_meta_test(client)}, status_code=400,
        )

    return templates.TemplateResponse(
        request, "meta_publier_test.html", {"client": client, "erreur": None, "resultat": resultat, **_contexte_meta_test(client)},
    )


@app.post("/clients/{client_id}/meta/publier-instagram", response_class=HTMLResponse)
async def meta_publier_instagram(
    client_id: int, request: Request, legende: str = Form(""), image: UploadFile = File(...),
    db: Session = Depends(obtenir_session),
):
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    client = db.get(models.Client, client_id)
    if not client or not client.instagram_id_meta or not client.token_instagram:
        return RedirectResponse(f"/clients/{client_id}", status_code=303)

    try:
        octets = await image.read()
        extension = os.path.splitext(image.filename or "")[1] or ".jpg"
        url_image = ovh_upload.envoyer_octets(octets, f"meta-test-{uuid.uuid4().hex}{extension}")
        resultat_instagram = instagram_publish.publier_photo(client.token_instagram, client.instagram_id_meta, url_image, legende)
    except Exception as erreur:
        return templates.TemplateResponse(
            request, "meta_publier_test.html",
            {"client": client, "erreur": str(erreur), "resultat": None, **_contexte_meta_test(client)}, status_code=400,
        )

    return templates.TemplateResponse(
        request, "meta_publier_test.html",
        {"client": client, "erreur": None, "resultat": resultat_instagram, **_contexte_meta_test(client)},
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
    Validation/rejet/suppression rapide depuis la liste des posts d'une fiche
    (sans passer par la page de detail) : ne touche qu'au statut, et si
    valide, a la date de publication et au bouton d'appel a l'action - laisse
    tous les autres champs du post (texte, image...) intacts. SUPPRIME est un
    retrait logique (le post reste en base mais disparait de partout ou ca
    compte - calendrier, contexte IA...), jamais un post deja PUBLIE_LIVE.
    """
    redirection = rediriger_si_non_connecte(request)
    if redirection:
        return redirection

    post = db.get(models.Post, post_id)
    if not post:
        return HTMLResponse("Post introuvable.", status_code=404)

    formulaire = await request.form()
    statut = formulaire.get("statut", "")
    if statut not in ("A_PUBLIER", "IGNORE", "SUPPRIME"):
        return HTMLResponse("Statut invalide.", status_code=400)
    if statut == "SUPPRIME" and post.statut == "PUBLIE_LIVE":
        return HTMLResponse("Un post deja publie ne peut pas etre supprime.", status_code=400)

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
