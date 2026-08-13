"""
Assemblage des donnees de rapport (stats, avis, posts) pour un client sur une
periode donnee. Extrait de main.py pour etre reutilisable depuis
planificateur.py sans creer d'import circulaire (planificateur est importe
par main.py, donc ne peut pas importer main.py en retour).
"""

import calendar
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from . import brevo_email, google_business, google_oauth, google_performance, google_reviews, models, recap_mensuel


def _date_n1(jour: date) -> date:
    """Meme date un an plus tot (repli au 28 fevrier si jour = 29 fevrier)."""
    try:
        return jour.replace(year=jour.year - 1)
    except ValueError:
        return jour.replace(year=jour.year - 1, day=28)


def _parser_date_iso(chaine: str):
    if not chaine:
        return None
    try:
        return datetime.fromisoformat(chaine.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def donnees_rapport_vides() -> dict:
    return {
        "statistiques": {}, "resume_avis": {}, "posts_publies": [],
        "mots_cles": [], "erreur_mots_cles": None,
        "comparatif_visibilite": None, "evolution_avis": None,
    }


def rassembler_donnees_rapport(db: Session, client: models.Client, debut: date, fin: date) -> dict:
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        raise RuntimeError(
            "Le compte Google associe a ce client n'est plus valide. "
            "Reconnectez-le depuis la page Comptes Google."
        )

    debut_n1, fin_n1 = _date_n1(debut), _date_n1(fin)

    statistiques = google_performance.recuperer_statistiques(identifiants, client.location_id, debut, fin)
    resume_avis = google_reviews.resumer_avis(
        identifiants, client.account_id, client.location_id, debut, fin, debut_n1, fin_n1
    )

    evenements = (
        db.query(models.EvenementPublication)
        .join(models.Post)
        .filter(models.Post.client_id == client.id)
        .filter(models.EvenementPublication.etat == "LIVE")
        .filter(models.EvenementPublication.horodatage >= datetime.combine(debut, datetime.min.time()))
        .filter(models.EvenementPublication.horodatage <= datetime.combine(fin, datetime.max.time()))
        .order_by(models.EvenementPublication.horodatage.desc())
        .all()
    )
    posts_publies_tries = [
        (e.horodatage.date(), {"titre": e.post.titre, "date": e.horodatage.strftime("%d/%m/%Y"), "source": "plateforme"})
        for e in evenements
    ]

    # Complete avec les posts reellement en ligne sur la fiche Google, pour
    # compter aussi ceux publies par un autre moyen que cette plateforme. On
    # exclut ceux deja comptes ci-dessus (meme id_post_google) pour ne pas les
    # compter deux fois. Limite connue : l'API Google (localPosts.list) ne
    # renvoie que les posts actuellement actifs sur la fiche, pas un historique
    # complet - un post externe deja expire cote Google au moment de la
    # consultation ne pourra donc pas etre comptabilise ici.
    ids_deja_comptes = {e.post.id_post_google for e in evenements if e.post.id_post_google}
    try:
        posts_en_ligne = google_business.lister_posts(identifiants, client.account_id, client.location_id)
    except Exception:
        posts_en_ligne = []

    for post_google in posts_en_ligne:
        if post_google.get("id_post_google") and post_google["id_post_google"] in ids_deja_comptes:
            continue
        jour = _parser_date_iso(post_google.get("date_creation_brute", ""))
        if not jour or not (debut <= jour <= fin):
            continue
        posts_publies_tries.append((jour, {
            "titre": post_google.get("texte", "")[:80] or "(post sans titre)",
            "date": jour.strftime("%d/%m/%Y"),
            "source": "google",
        }))

    posts_publies_tries.sort(key=lambda item: item[0], reverse=True)
    posts_publies = [item[1] for item in posts_publies_tries]

    try:
        mots_cles = google_performance.recuperer_mots_cles_recherche(identifiants, client.location_id, debut, fin)
        erreur_mots_cles = None
    except Exception as erreur:
        mots_cles = []
        erreur_mots_cles = str(erreur)

    # Comparatif N-1 : requiert un second appel Performance API sur la meme
    # periode l'annee precedente. Si Google n'a pas d'historique aussi loin
    # (fiche trop recente, periode hors plage disponible), on desactive
    # simplement le comparatif plutot que de casser la page.
    try:
        statistiques_n1 = google_performance.recuperer_statistiques(identifiants, client.location_id, debut_n1, fin_n1)
    except Exception:
        statistiques_n1 = None

    comparatif_visibilite = None
    if statistiques_n1 is not None:
        comparatif_visibilite = {}
        for libelle, valeur in statistiques.items():
            valeur_n1 = statistiques_n1.get(libelle, 0)
            evolution = round((valeur - valeur_n1) / valeur_n1 * 100, 1) if valeur_n1 else None
            comparatif_visibilite[libelle] = {"n1": valeur_n1, "evolution": evolution}

    evolution_avis = None
    nombre_avis_periode_n1 = resume_avis.get("nombre_avis_periode_n1")
    if nombre_avis_periode_n1:
        evolution_avis = round(
            (resume_avis["nombre_avis_periode"] - nombre_avis_periode_n1) / nombre_avis_periode_n1 * 100, 1
        )

    return {
        "statistiques": statistiques,
        "resume_avis": resume_avis,
        "posts_publies": posts_publies,
        "mots_cles": mots_cles,
        "erreur_mots_cles": erreur_mots_cles,
        "comparatif_visibilite": comparatif_visibilite,
        "evolution_avis": evolution_avis,
    }


def mois_precedent(aujourdhui: date) -> tuple[int, int]:
    """Renvoie (mois, annee) du mois calendaire qui vient de se terminer."""
    premier_du_mois = aujourdhui.replace(day=1)
    dernier_jour_precedent = premier_du_mois - timedelta(days=1)
    return dernier_jour_precedent.month, dernier_jour_precedent.year


def envoyer_recap_client(db: Session, client: models.Client, mois: int, annee: int) -> tuple[str, str]:
    """
    Assemble et envoie le recap mensuel a un client pour la periode mois/annee,
    puis enregistre le resultat (EnvoiRecap). N'envoie rien si un recap a deja
    ete envoye avec succes pour cette periode (evite les doublons, y compris
    depuis un declenchement manuel). Renvoie (etat, erreur).
    """
    deja_envoye = (
        db.query(models.EnvoiRecap)
        .filter_by(client_id=client.id, mois=mois, annee=annee, etat="ENVOYE")
        .first()
    )
    if deja_envoye:
        return "ENVOYE", ""

    debut = date(annee, mois, 1)
    fin = date(annee, mois, calendar.monthrange(annee, mois)[1])

    try:
        if not client.email:
            raise RuntimeError("Aucun email renseigne pour ce client.")
        identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
        if not identifiants:
            raise RuntimeError("Le compte Google associe a ce client n'est plus valide.")

        donnees = rassembler_donnees_rapport(db, client, debut, fin)
        avis_recents = google_reviews.avis_cinq_etoiles_recents(
            identifiants, client.account_id, client.location_id, debut, fin
        )
        sujet = recap_mensuel.construire_sujet(client, mois, annee)
        html = recap_mensuel.construire_email(client, donnees, mois, annee, avis_recents)
        brevo_email.envoyer_email(client.email, client.nom, sujet, html)
        etat, erreur = "ENVOYE", ""
    except Exception as e:
        etat, erreur = "ECHEC", str(e)

    db.add(models.EnvoiRecap(client_id=client.id, mois=mois, annee=annee, etat=etat, erreur=erreur))
    db.commit()
    return etat, erreur
