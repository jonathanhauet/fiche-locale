"""
Assemblage des donnees de rapport (stats, avis, posts) pour un client sur une
periode donnee. Extrait de main.py pour etre reutilisable depuis
planificateur.py sans creer d'import circulaire (planificateur est importe
par main.py, donc ne peut pas importer main.py en retour).
"""

import calendar
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from . import (
    brevo_email,
    claude_generation,
    google_business,
    google_location,
    google_oauth,
    google_performance,
    google_reviews,
    models,
    recap_mensuel,
)


def _date_n1(jour: date) -> date:
    """Meme date un an plus tot (repli au 28 fevrier si jour = 29 fevrier)."""
    try:
        return jour.replace(year=jour.year - 1)
    except ValueError:
        return jour.replace(year=jour.year - 1, day=28)


def _tronquer_proprement(texte: str, longueur: int = 100) -> str:
    """Tronque au dernier espace avant `longueur` (pas au milieu d'un mot) et ajoute une ellipse."""
    texte = texte.strip()
    if len(texte) <= longueur:
        return texte
    tronque = texte[:longueur].rsplit(" ", 1)[0]
    return (tronque or texte[:longueur]) + "…"


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
        "photos_publiees": 0,
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
            "titre": _tronquer_proprement(post_google.get("texte", "")) or "(post sans titre)",
            "date": jour.strftime("%d/%m/%Y"),
            "source": "google",
        }))

    posts_publies_tries.sort(key=lambda item: item[0], reverse=True)
    posts_publies = [item[1] for item in posts_publies_tries]

    photos_publiees = (
        db.query(models.PhotoFiche)
        .filter(models.PhotoFiche.client_id == client.id)
        .filter(models.PhotoFiche.statut == "PUBLIE_LIVE")
        .filter(models.PhotoFiche.date_prevue >= debut, models.PhotoFiche.date_prevue <= fin)
        .count()
    )

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
        "photos_publiees": photos_publiees,
    }


def resume_groupes_etiquette(
    db: Session, client: models.Client, date_debut: date, date_fin: date, cache: dict = None,
) -> list[dict]:
    """
    Pour chaque etiquette du client comptant au moins 2 fiches, le total
    d'avis et la moyenne de TOUT le groupe sur la periode - pense pour un
    client final gere via de nombreuses fiches regroupees sous une meme
    etiquette (ex. une enseigne a plusieurs dizaines d'etablissements), qui
    veut un chiffre global en plus de celui de cette fiche precise.

    cache (optionnel, un dict partage entre plusieurs appels d'un meme lot
    d'envoi - voir planificateur.envoyer_recaps_mensuels) evite de relire les
    avis de tout le groupe a chaque fiche membre : sans lui, un groupe de 100
    fiches ferait relire les avis des 100 fiches... 100 fois (une fois par
    email envoye a ce groupe le meme jour).
    """
    if cache is None:
        cache = {}

    resultats = []
    for etiquette in client.etiquettes:
        if len(etiquette.clients) < 2:
            continue  # groupe d'une seule fiche (elle-meme) : rien a additionner

        cle_cache = (etiquette.id, date_debut, date_fin)
        if cle_cache not in cache:
            total_avis = 0
            notes = []
            for membre in etiquette.clients:
                if not membre.account_id or not membre.location_id:
                    continue
                identifiants = google_oauth.obtenir_identifiants(db, membre.compte_google_id)
                if not identifiants:
                    continue
                try:
                    avis_membre = google_reviews.lister_avis_dune_fiche(
                        identifiants, membre.account_id, membre.location_id, toutes_les_pages=True
                    )
                except Exception:
                    continue
                for avis in avis_membre:
                    try:
                        date_creation = datetime.fromisoformat(
                            avis.get("createTime", "").replace("Z", "+00:00")
                        ).date()
                    except ValueError:
                        continue
                    if date_debut <= date_creation <= date_fin:
                        total_avis += 1
                        note = google_reviews.NOTES.get(avis.get("starRating"), 0)
                        if note:
                            notes.append(note)
            cache[cle_cache] = {
                "nb_fiches": len(etiquette.clients),
                "total_avis": total_avis,
                "moyenne": round(sum(notes) / len(notes), 1) if notes else None,
            }

        resultats.append({"etiquette_nom": etiquette.nom, **cache[cle_cache]})

    return resultats


def mois_precedent(aujourdhui: date) -> tuple[int, int]:
    """Renvoie (mois, annee) du mois calendaire qui vient de se terminer."""
    premier_du_mois = aujourdhui.replace(day=1)
    dernier_jour_precedent = premier_du_mois - timedelta(days=1)
    return dernier_jour_precedent.month, dernier_jour_precedent.year


def construire_contenu_recap(
    db: Session, client: models.Client, mois: int, annee: int, cache_groupes: dict = None,
) -> tuple[str, str]:
    """
    Assemble le sujet et le HTML du recap pour ce client/periode, sans rien
    envoyer. Utilise a la fois par l'apercu (main.py) et par l'envoi reel
    (envoyer_recap_client ci-dessous), pour que les deux restent identiques.

    cache_groupes : voir resume_groupes_etiquette - a fournir (un dict partage)
    depuis un envoi en lot pour eviter de recalculer le meme groupe a chaque
    fiche membre ; laisse a None pour un appel isole (apercu, envoi manuel).
    """
    identifiants = google_oauth.obtenir_identifiants(db, client.compte_google_id)
    if not identifiants:
        raise RuntimeError("Le compte Google associe a ce client n'est plus valide.")

    debut = date(annee, mois, 1)
    fin = date(annee, mois, calendar.monthrange(annee, mois)[1])

    donnees = rassembler_donnees_rapport(db, client, debut, fin)
    avis_positifs = google_reviews.avis_positifs_periode(
        identifiants, client.account_id, client.location_id, debut, fin
    )

    # Un seul avis positif : la citation directe suffit (voir recap_mensuel).
    # Plusieurs : on demande un resume a l'IA pour ne pas juxtaposer trop de
    # citations - si l'IA echoue (credits epuises, etc.), on se rabat
    # simplement sur des citations individuelles, l'email part quand meme.
    resume_avis_texte = None
    if len(avis_positifs) > 1:
        try:
            resume_avis_texte = claude_generation.resumer_avis_positifs(avis_positifs)
        except Exception:
            resume_avis_texte = None

    # Lien "Voir ma fiche sur Google" : facultatif, on n'echoue jamais le
    # recap pour ca (fiche pas encore visible sur Maps, erreur API, etc.).
    try:
        infos_fiche = google_location.obtenir_infos_fiche(identifiants, client.location_id)
        lien_fiche_google = (infos_fiche.get("metadata") or {}).get("mapsUri", "")
    except Exception:
        lien_fiche_google = ""

    groupes_etiquette = resume_groupes_etiquette(db, client, debut, fin, cache_groupes)

    sujet = recap_mensuel.construire_sujet(client, mois, annee)
    html = recap_mensuel.construire_email(
        client, donnees, mois, annee, avis_positifs, resume_avis_texte, lien_fiche_google, groupes_etiquette
    )
    return sujet, html


def envoyer_recap_client(
    db: Session, client: models.Client, mois: int, annee: int, cache_groupes: dict = None,
) -> tuple[str, str]:
    """
    Envoie le recap mensuel a un client pour la periode mois/annee, puis
    enregistre le resultat (EnvoiRecap). N'envoie rien si un recap a deja ete
    envoye avec succes pour cette periode (evite les doublons, y compris
    depuis un declenchement manuel). Renvoie (etat, erreur).

    cache_groupes : voir construire_contenu_recap / resume_groupes_etiquette.
    """
    deja_envoye = (
        db.query(models.EnvoiRecap)
        .filter_by(client_id=client.id, mois=mois, annee=annee, etat="ENVOYE")
        .first()
    )
    if deja_envoye:
        return "ENVOYE", ""

    try:
        if not client.email:
            raise RuntimeError("Aucun email renseigne pour ce client.")
        sujet, html = construire_contenu_recap(db, client, mois, annee, cache_groupes)
        brevo_email.envoyer_email(client.email, client.prenom or client.nom, sujet, html)
        etat, erreur = "ENVOYE", ""
    except Exception as e:
        etat, erreur = "ECHEC", str(e)

    db.add(models.EnvoiRecap(client_id=client.id, mois=mois, annee=annee, etat=etat, erreur=erreur))
    db.commit()
    return etat, erreur
