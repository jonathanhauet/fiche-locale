"""
Verification periodique des posts programmes et publication automatique.
Remplace la tache planifiee Windows utilisee par les scripts en ligne de
commande : ici, une tache de fond integree au processus web (APScheduler).
"""

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import (
    claude_generation, google_business, google_location, google_oauth, google_publish,
    google_reviews, instagram_oauth, instagram_publish, linkedin_publish, meta_publish, models,
    notifications, rapport_donnees, veille_actualite, whatsapp_business,
)
from .database import SessionLocal


FUSEAU_LOCAL = ZoneInfo("Europe/Brussels")


def _maintenant_local() -> datetime:
    """
    Heure actuelle "murale" de Paris/Bruxelles (sans fuseau), a comparer aux
    dates/heures de programmation saisies par l'utilisateur, qui sont
    stockees telles quelles, sans fuseau. datetime.now() renvoie l'heure du
    serveur : sur Railway c'est l'UTC, soit 2h de retard sur Paris en ete -
    un post programme a 08h30 ne partait qu'a 10h30 (heure de Paris).
    """
    return datetime.now(FUSEAU_LOCAL).replace(tzinfo=None)


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

        maintenant = _maintenant_local()
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
    """Publie automatiquement toutes les photos 'A_PUBLIER' dont la date et l'heure prevues sont arrivees."""
    db = SessionLocal()
    try:
        if not google_oauth.google_est_connecte(db):
            return

        maintenant = _maintenant_local()
        photos_candidates = (
            db.query(models.PhotoFiche)
            .filter(models.PhotoFiche.statut == "A_PUBLIER")
            .filter(models.PhotoFiche.date_prevue.isnot(None))
            .filter(models.PhotoFiche.date_prevue <= maintenant.date())
            .all()
        )
        photos_a_publier = [
            p for p in photos_candidates if _heure_prevue_atteinte(p.date_prevue, p.heure_prevue, maintenant)
        ]
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


def _sujets_deja_traites(db, client_id: int, limite: int = 15) -> list[str]:
    """
    Meme logique que main._sujets_deja_traites_client, dupliquee ici : main
    importe planificateur (pas l'inverse), un import direct creerait un
    cycle. Fonction volontairement petite pour que la duplication reste sans
    risque de divergence significative.
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
    """Meme logique que main._contexte_ia_client, dupliquee ici pour la meme raison que _sujets_deja_traites (pas d'import circulaire possible)."""
    morceaux = []
    if client.contenu_site and client.contenu_site.strip():
        morceaux.append(client.contenu_site.strip())
    for document in client.documents_connaissance:
        morceaux.append(f"--- Document : {document.nom_fichier} ---\n{document.texte_extrait}")
    return "\n\n".join(morceaux)


def _contexte_positionnement_client(db, client_id: int, limite: int = 8) -> str:
    """
    Resume des reponses vocales precedentes du client (voir
    ReponseInterviewClient, alimente par main._traiter_message_whatsapp),
    ajoute au contexte envoye a l'IA pour la generation des nouvelles
    questions - permet de creuser des angles pas encore abordes plutot que
    de reposer des variantes de ce qui est deja connu.
    """
    reponses = (
        db.query(models.ReponseInterviewClient)
        .filter(models.ReponseInterviewClient.client_id == client_id)
        .order_by(models.ReponseInterviewClient.cree_le.desc())
        .limit(limite)
        .all()
    )
    if not reponses:
        return ""
    morceaux = [f"Q: {r.question}\nR: {r.reponse_transcrite[:400].strip()}" for r in reponses]
    return "Reponses precedentes du client, deja connues (ne pas reposer une question sur les memes elements) :\n" + "\n\n".join(morceaux)


def envoyer_questions_whatsapp_pour_client(db, client) -> str | None:
    """
    Genere et envoie les 5 questions de la semaine a un client donne (voir
    envoyer_questions_whatsapp_si_prevu, qui appelle cette fonction pour
    chaque client eligible du jour et ignore la valeur de retour - un echec
    ne doit pas interrompre les autres clients du lot). Aussi utilisee pour
    un envoi manuel immediat depuis la fiche client (bouton "Envoyer
    maintenant"), ou le message d'erreur est lui affiche a Jonathan pour
    diagnostiquer (token expire, numero pas autorise sur le numero de test
    Meta, modele introuvable...). Renvoie None si tout s'est bien passe,
    sinon un message d'erreur.
    """
    # Les questions doivent toujours etre renouvelees : on exclut a la fois
    # les questions deja envoyees recemment (QuestionWhatsAppPosee) et on
    # enrichit le contexte avec les reponses deja obtenues, pour creuser de
    # nouveaux angles plutot que de tourner en rond.
    questions_recentes = (
        db.query(models.QuestionWhatsAppPosee)
        .filter(models.QuestionWhatsAppPosee.client_id == client.id)
        .order_by(models.QuestionWhatsAppPosee.posee_le.desc())
        .limit(20)
        .all()
    )
    contexte = _contexte_ia_client(client)
    contexte_positionnement = _contexte_positionnement_client(db, client.id)
    if contexte_positionnement:
        contexte = f"{contexte}\n\n{contexte_positionnement}" if contexte else contexte_positionnement

    try:
        questions = claude_generation.generer_questions_interview(
            contexte, sujets_deja_traites=[q.question for q in questions_recentes], nombre=5,
        )
    except Exception as erreur:
        return f"Echec de la generation des questions : {erreur}"
    if not questions:
        return "Aucune question generee."

    etat = db.query(models.EtatConversationWhatsApp).filter_by(numero=client.numero_whatsapp).first()
    if not etat:
        etat = models.EtatConversationWhatsApp(client_id=client.id, numero=client.numero_whatsapp)
        db.add(etat)
    etat.questions_json = json.dumps(questions)
    etat.question_choisie = None
    etat.image_url = None
    etat.maj_le = datetime.utcnow()
    for question in questions:
        db.add(models.QuestionWhatsAppPosee(client_id=client.id, question=question))
    db.commit()

    # Une variable de modele WhatsApp ne peut pas contenir de saut de ligne
    # (retours a la ligne/tabulations refuses par l'API, code 132018) : les 5
    # questions sont donc separees par " | " plutot qu'un saut de ligne par
    # question.
    corps = " | ".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    try:
        whatsapp_business.envoyer_message_template(
            client.numero_whatsapp, whatsapp_business.NOM_TEMPLATE_QUESTIONS_HEBDO, "fr",
            {"questions_semaine": corps},
        )
    except Exception as erreur:
        return f"Echec de l'envoi WhatsApp : {erreur}"
    return None


def envoyer_questions_whatsapp_si_prevu():
    """
    Envoie les questions du mode rapide vocal par WhatsApp (voir
    whatsapp_business.py) a chaque client eligible, aux jours qu'il a
    choisis (Client.whatsapp_jours, "0" = lundi ... "6" = dimanche), via le
    modele approuve WHATSAPP_TEMPLATE_QUESTIONS. Tourne quotidiennement (voir
    main.py) et ne fait rien pour un client les jours non choisis.

    Un client n'est eligible que si whatsapp_opt_in_confirme est coche : les
    messages "template" envoyes ici sont a l'initiative de l'entreprise (pas
    une reponse a un message recu), ce qui declenche les regles anti-spam de
    Meta si le destinataire n'a pas explicitement donne son accord - sans
    cette securite, le numero WhatsApp Business de l'agence risquerait d'etre
    signale/restreint des le premier client non consentant.
    """
    db = SessionLocal()
    try:
        if not whatsapp_business.identifiants_configures():
            return

        jour_aujourdhui = str(date.today().weekday())
        clients_eligibles = (
            db.query(models.Client)
            .filter(models.Client.numero_whatsapp != "", models.Client.whatsapp_jours != "")
            .filter(models.Client.whatsapp_opt_in_confirme.is_(True))
            .all()
        )
        for client in clients_eligibles:
            if jour_aujourdhui not in client.whatsapp_jours.split(","):
                continue
            envoyer_questions_whatsapp_pour_client(db, client)
    finally:
        db.close()


JOURS_PREAVIS_EXPIRATION_LINKEDIN = 3


def notifier_expirations_linkedin():
    """
    Previent (ntfy) qu'un profil LinkedIn arrive a expiration dans 3 jours
    ou moins (jusqu'a 1 jour apres, pour ne pas laisser passer un profil
    expire dans la nuit) : LinkedIn ne renouvelle pas le token tout seul
    (voir linkedin_oauth.py), il faut reconnecter le profil a la main. Tourne
    chaque jour, donc un meme profil declenche jusqu'a 3-4 rappels
    successifs plutot qu'un seul, sans avoir besoin d'un champ "deja notifie".
    Un profil deja reconnecte depuis (une ligne plus recente avec le meme
    identifiant_membre) est ignore : la reconnexion cree une nouvelle ligne
    sans supprimer l'ancienne, qui continuerait sinon a alerter a tort.
    """
    db = SessionLocal()
    try:
        maintenant = datetime.utcnow()
        comptes = db.query(models.CompteLinkedIn).filter(models.CompteLinkedIn.expire_le.isnot(None)).all()
        for compte in comptes:
            reste = compte.expire_le - maintenant
            if not (timedelta(days=-1) <= reste <= timedelta(days=JOURS_PREAVIS_EXPIRATION_LINKEDIN)):
                continue
            deja_reconnecte = any(
                autre.id != compte.id
                and autre.identifiant_membre == compte.identifiant_membre
                and autre.expire_le
                and autre.expire_le > compte.expire_le
                for autre in comptes
            )
            if deja_reconnecte:
                continue

            if reste.total_seconds() <= 0:
                message = f"Le profil LinkedIn {compte.libelle} a expiré : reconnectez-le pour reprendre les publications."
            else:
                jours = max(1, -(-int(reste.total_seconds()) // 86400))
                message = (
                    f"Le profil LinkedIn {compte.libelle} expire dans {jours} jour{'s' if jours > 1 else ''} : "
                    "pensez à le reconnecter."
                )
            notifications.notifier(
                "LinkedIn : reconnexion a prevoir", message,
                url="https://web-production-bf59a.up.railway.app/linkedin/comptes",
            )
    finally:
        db.close()


def generer_suggestions_quotidiennes():
    """
    Prepare chaque matin les sujets tendance de la fiche de Jonathan (voir
    veille_actualite.py + claude_generation.suggerer_sujets_actualite), pour
    que /publication-multi les affiche deja prets a l'ouverture plutot que
    d'attendre un appel IA en direct sur place - pas d'email, Jonathan
    prefere consulter la plateforme lui-meme. Se limite a la fiche "Jonathan
    Hauet Marketing" : generer_post_expert (voix a la premiere personne,
    prise de position) n'a de sens que pour une fiche dont le proprietaire
    EST l'auteur, pas pour une fiche cliente classique. Le lot du jour
    remplace celui de la veille (voir le delete avant l'ajout) plutot que de
    s'accumuler indefiniment.
    """
    db = SessionLocal()
    try:
        client = db.query(models.Client).filter(models.Client.nom == "Jonathan Hauet Marketing").first()
        if not client:
            return

        try:
            articles = veille_actualite.rechercher_actualites()
            # Les sujets deja publies (_sujets_deja_traites) ne suffisent pas a
            # eviter une repetition d'un jour sur l'autre : si Jonathan regarde
            # les suggestions sans en publier une, rien n'indique a l'IA
            # qu'elles ont deja ete montrees - le lot de la veille (sur le
            # point d'etre remplace juste en dessous) sert donc aussi de liste
            # a eviter, en plus des sujets reellement publies.
            suggestions_veille = (
                db.query(models.SuggestionSujetJour).filter_by(client_id=client.id).all()
            )
            sujets_a_eviter = _sujets_deja_traites(db, client.id) + [s.sujet for s in suggestions_veille]
            suggestions = claude_generation.suggerer_sujets_actualite(
                articles, nombre=5, sujets_deja_traites=sujets_a_eviter,
            )
        except Exception:
            return

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


def verifier_statut_validation_fiches():
    """
    Compare, pour toutes les fiches liees a Google, le statut de validation
    actuel (voir google_location.fiche_validee) au dernier statut connu
    (Client.dernier_statut_validation). Un changement - dans un sens comme
    dans l'autre - cree une AlerteStatutFiche en attente, visible sur
    /alertes. Tourne une fois par jour, meme creneau que les autres
    verifications legeres.

    "inaccessible" (echec de lecture avec un code d'erreur explicite,
    typiquement 403/404) est traite comme un statut a part entiere : Google
    n'expose aucun champ "suspendu" officiel dans cette API, donc une fiche
    qui devient brutalement inaccessible apres avoir ete lisible est le
    signal le plus proche d'une suspension qu'on puisse detecter. Une erreur
    reseau/transitoire (pas de code HTTP net) ne fait pas changer le statut
    enregistre, pour eviter une fausse alerte.
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
                nouveau_statut = "valide" if google_location.fiche_validee(infos) else "non_valide"
            except RuntimeError:
                # Echec avec code HTTP explicite (voir google_location.obtenir_infos_fiche) -
                # traite comme un signal d'inaccessibilite, pas ignore.
                nouveau_statut = "inaccessible"
            except Exception:
                # Erreur transitoire (reseau, timeout...) : on ne change rien,
                # on reessaiera au prochain passage.
                continue

            ancien_statut = client.dernier_statut_validation
            if ancien_statut is None:
                # Premiere verification pour cette fiche : on enregistre la
                # reference sans alerter (rien a comparer).
                client.dernier_statut_validation = nouveau_statut
                db.commit()
                continue

            if nouveau_statut == ancien_statut:
                continue

            db.add(models.AlerteStatutFiche(
                client_id=client.id, statut_avant=ancien_statut, statut_apres=nouveau_statut,
            ))
            client.dernier_statut_validation = nouveau_statut
            db.commit()
    finally:
        db.close()


SEUIL_RAFRAICHISSEMENT_INSTAGRAM_JOURS = 10


def rafraichir_tokens_instagram():
    """
    Le token Instagram (voir instagram_oauth.py) expire au bout de 60 jours -
    contrairement au token systeme Meta qui n'expire jamais. Rafraichit tout
    compte dont l'expiration approche, et propage le nouveau token aux
    clients qui y sont rattaches (une copie a ete faite sur Client au moment
    de la liaison, pas une reference live). Tourne une fois par jour.
    """
    db = SessionLocal()
    try:
        seuil = datetime.utcnow() + timedelta(days=SEUIL_RAFRAICHISSEMENT_INSTAGRAM_JOURS)
        comptes = (
            db.query(models.CompteInstagram)
            .filter(models.CompteInstagram.expire_le.isnot(None), models.CompteInstagram.expire_le <= seuil)
            .all()
        )
        for compte in comptes:
            try:
                nouveau_token, duree_secondes = instagram_oauth.rafraichir_token(compte.access_token)
            except Exception:
                continue

            compte.access_token = nouveau_token
            compte.expire_le = datetime.utcnow() + timedelta(seconds=duree_secondes)

            for client in db.query(models.Client).filter_by(compte_instagram_id=compte.id).all():
                client.token_instagram = nouveau_token

            db.commit()
    finally:
        db.close()


def publier_posts_linkedin_programmes():
    """
    Publie automatiquement tous les posts LinkedIn 'EN_ATTENTE' dont la
    date/heure prevue est arrivee. Meme convention que
    verifier_et_publier_posts_programmes (Google) : comparaison a
    _maintenant_local() (heure de Paris, pas celle du serveur).
    """
    db = SessionLocal()
    try:
        maintenant = _maintenant_local()
        posts_a_publier = (
            db.query(models.PostLinkedInProgramme)
            .filter(models.PostLinkedInProgramme.etat == "EN_ATTENTE")
            .filter(models.PostLinkedInProgramme.publier_le <= maintenant)
            .all()
        )
        for post in posts_a_publier:
            try:
                linkedin_publish.publier_post(
                    post.compte.access_token, post.compte.identifiant_membre, post.texte,
                    octets_image=post.image_donnees, octets_video=post.video_donnees,
                )
                post.etat = "PUBLIE"
            except Exception as erreur:
                post.etat = "ECHEC"
                post.erreur = str(erreur)
            db.commit()
    finally:
        db.close()


def publier_posts_meta_programmes():
    """Publie automatiquement tous les posts Facebook 'EN_ATTENTE' dont la date/heure prevue est arrivee."""
    db = SessionLocal()
    try:
        maintenant = _maintenant_local()
        posts_a_publier = (
            db.query(models.PostMetaProgramme)
            .filter(models.PostMetaProgramme.etat == "EN_ATTENTE")
            .filter(models.PostMetaProgramme.publier_le <= maintenant)
            .all()
        )
        for post in posts_a_publier:
            try:
                if not post.client.page_id_meta or not post.client.token_page_meta:
                    raise RuntimeError("Ce client n'a plus de Page Facebook associee.")
                if post.video_url:
                    meta_publish.publier_video_page(post.client.token_page_meta, post.client.page_id_meta, post.video_url, post.texte)
                else:
                    meta_publish.publier_post_page(
                        post.client.token_page_meta, post.client.page_id_meta, post.texte,
                        meta_publish.urls_depuis_champ(post.image_url),
                    )
                post.etat = "PUBLIE"
            except Exception as erreur:
                post.etat = "ECHEC"
                post.erreur = str(erreur)
            db.commit()
    finally:
        db.close()


def publier_posts_instagram_programmes():
    """Publie automatiquement tous les posts Instagram 'EN_ATTENTE' dont la date/heure prevue est arrivee."""
    db = SessionLocal()
    try:
        maintenant = _maintenant_local()
        posts_a_publier = (
            db.query(models.PostInstagramProgramme)
            .filter(models.PostInstagramProgramme.etat == "EN_ATTENTE")
            .filter(models.PostInstagramProgramme.publier_le <= maintenant)
            .all()
        )
        for post in posts_a_publier:
            try:
                if not post.client.instagram_id_meta or not post.client.token_instagram:
                    raise RuntimeError("Ce client n'a plus de compte Instagram associe.")
                if post.video_url:
                    instagram_publish.publier_reel(post.client.token_instagram, post.client.instagram_id_meta, post.video_url, post.texte)
                elif post.image_url:
                    instagram_publish.publier_medias(
                        post.client.token_instagram, post.client.instagram_id_meta,
                        meta_publish.urls_depuis_champ(post.image_url), post.texte,
                    )
                else:
                    raise RuntimeError("Instagram necessite une image ou une video.")
                post.etat = "PUBLIE"
            except Exception as erreur:
                post.etat = "ECHEC"
                post.erreur = str(erreur)
            db.commit()
    finally:
        db.close()
