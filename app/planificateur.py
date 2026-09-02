"""
Verification periodique des posts programmes et publication automatique.
Remplace la tache planifiee Windows utilisee par les scripts en ligne de
commande : ici, une tache de fond integree au processus web (APScheduler).
"""

from datetime import date, datetime, time

from . import google_business, google_location, google_oauth, google_publish, google_reviews, models, rapport_donnees
from .database import SessionLocal


def _heure_prevue_atteinte(date_prevue: date, heure_prevue: str, maintenant: datetime) -> bool:
    """Compare date_prevue+heure_prevue ('HH:MM', minuit si absente) au moment actuel."""
    try:
        heure, minute = (int(x) for x in (heure_prevue or "00:00").split(":"))
    except ValueError:
        heure, minute = 0, 0
    return datetime.combine(date_prevue, time(hour=heure, minute=minute)) <= maintenant


def verifier_et_publier_posts_programmes():
    """Publie automatiquement tous les posts 'A_PUBLIER' dont la date et l'heure prevues sont arrivees."""
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        maintenant = datetime.now()
        posts_candidats = (
            db.query(models.Post)
            .filter(models.Post.statut == "A_PUBLIER")
            .filter(models.Post.date_prevue.isnot(None))
            .filter(models.Post.date_prevue <= maintenant.date())
            .all()
        )
        posts_a_publier = [
            p for p in posts_candidats if _heure_prevue_atteinte(p.date_prevue, p.heure_prevue, maintenant)
        ]
        if not posts_a_publier:
            return

        identifiants_par_compte = {}

        for post in posts_a_publier:
            if not post.client.account_id or not post.client.location_id:
                continue

            compte_id = post.client.compte_google_id
            if compte_id not in identifiants_par_compte:
                identifiants_par_compte[compte_id] = google_oauth.obtenir_identifiants(db, compte_id)
            identifiants = identifiants_par_compte[compte_id]
            if not identifiants:
                continue

            try:
                google_publish.publier_et_verifier(db, identifiants, post)
            except Exception:
                # Deja journalise (ECHEC_PUBLICATION) dans publier_et_verifier.
                # On continue avec les posts suivants plutot que d'interrompre la tache.
                continue
    finally:
        db.close()


def verifier_et_publier_photos_programmees():
    """Publie automatiquement toutes les photos 'A_PUBLIER' dont la date prevue est arrivee."""
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        aujourdhui = date.today()
        photos_a_publier = (
            db.query(models.PhotoFiche)
            .filter(models.PhotoFiche.statut == "A_PUBLIER")
            .filter(models.PhotoFiche.date_prevue.isnot(None))
            .filter(models.PhotoFiche.date_prevue <= aujourdhui)
            .all()
        )
        if not photos_a_publier:
            return

        identifiants_par_compte = {}

        for photo in photos_a_publier:
            if not photo.client.account_id or not photo.client.location_id:
                continue

            compte_id = photo.client.compte_google_id
            if compte_id not in identifiants_par_compte:
                identifiants_par_compte[compte_id] = google_oauth.obtenir_identifiants(db, compte_id)
            identifiants = identifiants_par_compte[compte_id]
            if not identifiants:
                continue

            try:
                google_business.publier_photo_fiche(db, identifiants, photo)
            except Exception:
                continue
    finally:
        db.close()


def envoyer_recaps_mensuels():
    """
    Envoie le recap mensuel (voir recap_mensuel.py) aux clients eligibles pour
    le mois qui vient de se terminer. Tourne quotidiennement : sans effet la
    plupart des jours grace a la verification "deja envoye" (EnvoiRecap) faite
    par rapport_donnees.envoyer_recap_client - un echec (ex. token Google
    expire) est simplement retente le lendemain.
    """
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        mois, annee = rapport_donnees.mois_precedent(date.today())
        clients_eligibles = (
            db.query(models.Client)
            .filter(models.Client.email != "")
            .filter(models.Client.account_id != "", models.Client.location_id != "")
            .filter(models.Client.recap_actif)
            .all()
        )

        # Partage entre tous les envois de ce lot : une fiche appartenant a une
        # etiquette de plusieurs dizaines de fiches ne fait recalculer le total
        # du groupe qu'une fois pour tout le lot, pas une fois par fiche membre
        # (voir rapport_donnees.resume_groupes_etiquette).
        cache_groupes_etiquette = {}
        for client in clients_eligibles:
            rapport_donnees.envoyer_recap_client(db, client, mois, annee, cache_groupes_etiquette)
    finally:
        db.close()


def verifier_avis_supprimes():
    """
    Compare les avis actuellement presents sur chaque fiche a ceux deja connus
    (models.AvisConnu) pour detecter les suppressions - Google ne fournit
    aucun moyen direct de lister les avis supprimes, la seule facon de les
    detecter est de comparer un releve actuel a un releve precedent. Tourne
    une fois par jour. Un avis connu qui reapparait (rare, mais possible si
    Google le restaure) est "reactive" (supprime_le remis a None).
    """
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        clients = (
            db.query(models.Client)
            .filter(models.Client.account_id != "", models.Client.location_id != "")
            .all()
        )
        maintenant = datetime.now()
        identifiants_par_compte = {}

        for client in clients:
            compte_id = client.compte_google_id
            if compte_id not in identifiants_par_compte:
                identifiants_par_compte[compte_id] = google_oauth.obtenir_identifiants(db, compte_id)
            identifiants = identifiants_par_compte[compte_id]
            if not identifiants:
                continue

            try:
                avis_actuels = google_reviews.lister_avis_complet_client(identifiants, client)
            except Exception:
                continue

            ids_actuels = {a["review_id"] for a in avis_actuels}
            avis_connus = {
                a.review_id: a
                for a in db.query(models.AvisConnu).filter(models.AvisConnu.client_id == client.id).all()
            }

            for avis in avis_actuels:
                connu = avis_connus.get(avis["review_id"])
                if connu:
                    connu.derniere_confirmation_le = maintenant
                    connu.supprime_le = None
                    connu.note = avis["note"]
                    connu.commentaire = avis["commentaire"]
                    connu.reponse = avis["reponse"] or ""
                else:
                    db.add(models.AvisConnu(
                        client_id=client.id,
                        review_id=avis["review_id"],
                        auteur=avis["auteur"],
                        note=avis["note"],
                        commentaire=avis["commentaire"],
                        date_avis=avis["date_avis"],
                        reponse=avis["reponse"] or "",
                        premiere_detection_le=maintenant,
                        derniere_confirmation_le=maintenant,
                    ))

            for review_id, connu in avis_connus.items():
                if review_id not in ids_actuels and connu.supprime_le is None:
                    connu.supprime_le = maintenant

            db.commit()
    finally:
        db.close()


def verifier_protection_fiches():
    """
    Compare, pour chaque fiche ayant active la protection, le nom, le
    telephone, la categorie principale et le statut ouvert/ferme actuels a la
    reference enregistree au moment de l'activation (voir
    Client.protection_*_ref). Un ecart cree une AlerteProtectionFiche en
    attente (visible sur /alertes) - aucune ecriture automatique sur la fiche
    Google, Jonathan valide ensuite la restauration ou l'acceptation depuis
    l'alerte. Tourne une fois par jour.
    """
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        clients = (
            db.query(models.Client)
            .filter(models.Client.protection_fiche_active == True)  # noqa: E712
            .filter(models.Client.account_id != "", models.Client.location_id != "")
            .all()
        )
        if not clients:
            return

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
            actuel = google_location.valeurs_protegees(infos)

            verifications = [
                {
                    "champ": "titre", "libelle": "Nom de la fiche",
                    "reference": client.protection_titre_ref, "detecte": actuel["titre"],
                    "reference_affichee": client.protection_titre_ref, "detecte_affiche": actuel["titre"],
                },
                {
                    "champ": "telephone", "libelle": "Téléphone",
                    "reference": client.protection_telephone_ref, "detecte": actuel["telephone"],
                    "reference_affichee": client.protection_telephone_ref, "detecte_affiche": actuel["telephone"],
                },
                {
                    "champ": "categorie", "libelle": "Catégorie principale",
                    "reference": client.protection_categorie_id_ref, "detecte": actuel["categorie_id"],
                    "reference_affichee": client.protection_categorie_nom_ref, "detecte_affiche": actuel["categorie_nom"],
                },
                {
                    "champ": "statut", "libelle": "Statut ouvert/fermé",
                    "reference": client.protection_statut_ref, "detecte": actuel["statut_ouvert"],
                    "reference_affichee": google_location.LIBELLES_STATUT_OUVERTURE.get(
                        client.protection_statut_ref, client.protection_statut_ref
                    ),
                    "detecte_affiche": google_location.LIBELLES_STATUT_OUVERTURE.get(
                        actuel["statut_ouvert"], actuel["statut_ouvert"]
                    ),
                },
            ]

            for verif in verifications:
                if not verif["reference"] or verif["reference"] == verif["detecte"]:
                    continue
                alerte_en_attente = (
                    db.query(models.AlerteProtectionFiche)
                    .filter(
                        models.AlerteProtectionFiche.client_id == client.id,
                        models.AlerteProtectionFiche.champ == verif["champ"],
                        models.AlerteProtectionFiche.traite_le.is_(None),
                    )
                    .first()
                )
                if alerte_en_attente:
                    # Deja signale et toujours pas traite : on rafraichit juste la
                    # valeur detectee au cas ou elle aurait encore change entre-temps.
                    alerte_en_attente.valeur_detectee = verif["detecte_affiche"]
                    continue
                db.add(models.AlerteProtectionFiche(
                    client_id=client.id,
                    champ=verif["champ"],
                    libelle_champ=verif["libelle"],
                    valeur_reference=verif["reference_affichee"],
                    valeur_detectee=verif["detecte_affiche"],
                ))

            db.commit()
    finally:
        db.close()
