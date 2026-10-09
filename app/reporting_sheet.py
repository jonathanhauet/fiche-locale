"""
Reporting mensuel dans un Google Sheet unique, mis a jour automatiquement chaque mois (et a la demande) : onglet Synthese par pays
et par mois, un onglet par pays. L'historique grandit mois apres mois a partir du mois de debut choisi (au plus MOIS_MAX_SHEET
derniers mois, l'API Google ne conservant qu'environ 18 mois de statistiques).
"""

from datetime import date, datetime, timedelta

from . import google_oauth, google_sheets, models, notifications, reporting_mensuel
from .database import SessionLocal

MOIS_MAX_SHEET = 18
DUREE_MAX_MAJ = timedelta(minutes=30)          # une mise a jour « en cours » plus ancienne est consideree comme interrompue


def mois_precedent(aujourdhui: date) -> str:
    """AAAA-MM du dernier mois termine."""
    dernier = aujourdhui.replace(day=1) - timedelta(days=1)
    return dernier.strftime("%Y-%m")


def mois_a_afficher(mois_debut: str, aujourdhui: date) -> list:
    """[(annee, mois), ...] du mois de debut (AAAA-MM) au dernier mois termine, borne aux MOIS_MAX_SHEET derniers mois."""
    fin = date.fromisoformat(mois_precedent(aujourdhui) + "-01")
    tous = reporting_mensuel.derniers_mois(fin, MOIS_MAX_SHEET)
    try:
        annee, mois = (int(x) for x in mois_debut.split("-"))
    except (ValueError, AttributeError):
        return tous[-6:]
    return [m for m in tous if m >= (annee, mois)] or tous[-1:]


def clients_du_reporting(db, parametre) -> list:
    etiquette = db.get(models.Etiquette, parametre.etiquette_id) if parametre.etiquette_id else None
    if not etiquette:
        return []
    return sorted([c for c in etiquette.clients if c.account_id and c.location_id], key=lambda c: (c.nom or "").lower())


def mettre_a_jour(db, parametre, aujourdhui: date = None) -> dict:
    """Lit les statistiques, puis ecrit le Sheet. Leve une exception avec un message lisible en cas de probleme."""
    aujourdhui = aujourdhui or date.today()
    identifiants = google_oauth.obtenir_identifiants_sheets(db)
    if not identifiants:
        raise RuntimeError("Google Sheets n'est pas connecté (ou l'accès a été révoqué) : reconnectez-le.")
    clients = clients_du_reporting(db, parametre)
    if not clients:
        raise RuntimeError("Aucune agence : choisissez une étiquette qui contient des fiches Google.")
    mois = mois_a_afficher(parametre.mois_debut, aujourdhui)
    resultats = reporting_mensuel.collecter(db, clients, mois, avec_pays=True)
    if not any(r["mois"] for r in resultats):
        raise RuntimeError("Aucune donnée lue sur les fiches : " + " ; ".join(f"{r['client'].nom} : {r['erreur']}" for r in resultats)[:300])
    tableaux = reporting_mensuel.tableaux_par_pays(resultats, mois)
    spreadsheet_id, adresse = google_sheets.ecrire(identifiants, parametre.spreadsheet_id, parametre.nom_fichier or "Reporting mensuel", tableaux)
    return {"spreadsheet_id": spreadsheet_id, "url": adresse, "nb_agences": sum(1 for r in resultats if r["mois"]), "nb_non_lues": sum(1 for r in resultats if not r["mois"]),
            "mois": len(mois), "dernier_mois": mois_precedent(aujourdhui)}


def demarrer(db, parametre) -> bool:
    """Marque la mise a jour « en cours » ; False si une autre est deja en cours (et recente)."""
    if parametre.statut == "en_cours" and parametre.maj_demarree_le and datetime.utcnow() - parametre.maj_demarree_le < DUREE_MAX_MAJ:
        return False
    parametre.statut, parametre.message, parametre.maj_demarree_le = "en_cours", "", datetime.utcnow()
    db.commit()
    return True


def executer(parametre_id: int, automatique: bool = False):
    """Travail complet (a lancer dans un thread ou depuis le planificateur) avec sa propre session ; garde le resultat en base."""
    db = SessionLocal()
    try:
        parametre = db.get(models.ParametreGoogleSheets, parametre_id)
        if not parametre:
            return
        try:
            resultat = mettre_a_jour(db, parametre)
        except Exception as erreur:
            db.rollback()
            parametre = db.get(models.ParametreGoogleSheets, parametre_id)
            parametre.statut, parametre.message = "erreur", str(erreur)[:500]
            db.commit()
            if automatique:
                notifications.notifier("Echec du reporting Google Sheet", parametre.message[:300])
            return
        parametre.spreadsheet_id, parametre.spreadsheet_url = resultat["spreadsheet_id"], resultat["url"]
        parametre.statut, parametre.derniere_maj, parametre.dernier_mois = "ok", datetime.utcnow(), resultat["dernier_mois"]
        parametre.message = f"{resultat['nb_agences']} agences sur {resultat['mois']} mois" + (f" ; {resultat['nb_non_lues']} fiche(s) non lue(s), voir l'onglet « Fiches non lues »" if resultat["nb_non_lues"] else "")
        db.commit()
    finally:
        db.close()


def mise_a_jour_mensuelle():
    """
    Tache du planificateur : du 3 au 10 de chaque mois, une fois par jour, met a jour le Sheet si ce n'est pas deja fait pour le mois
    qui vient de se terminer (un echec est donc retente le lendemain, et l'alerte n'est envoyee qu'en cas d'echec).
    """
    db = SessionLocal()
    try:
        parametre = db.query(models.ParametreGoogleSheets).first()
        if not parametre or not parametre.actif or not parametre.refresh_token:
            return
        if parametre.dernier_mois == mois_precedent(date.today()) or not demarrer(db, parametre):
            return
        identifiant = parametre.id
    finally:
        db.close()
    executer(identifiant, automatique=True)
