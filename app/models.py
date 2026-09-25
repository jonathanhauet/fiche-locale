"""
Modeles de la base de donnees. Remplacent progressivement les fichiers
texte utilises par les scripts en ligne de commande (clients/*.txt,
identifiants_fiches.json, posts_generes/*.txt, logs/journal_publications.csv).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Table, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base

client_etiquettes = Table(
    "client_etiquettes",
    Base.metadata,
    Column("client_id", Integer, ForeignKey("clients.id"), primary_key=True),
    Column("etiquette_id", Integer, ForeignKey("etiquettes.id"), primary_key=True),
)


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id = Column(Integer, primary_key=True)
    identifiant = Column(String, unique=True, nullable=False)
    mot_de_passe_hash = Column(String, nullable=False)
    # Cle secrete TOTP (double authentification) - vide tant que l'utilisateur
    # n'a pas termine la configuration (voir deux_facteurs.py).
    totp_secret = Column(String, nullable=True)

    codes_recuperation = relationship(
        "CodeRecuperation2FA", back_populates="utilisateur", cascade="all, delete-orphan"
    )


class CodeRecuperation2FA(Base):
    """
    Code de secours a usage unique permettant de se connecter sans code TOTP
    (telephone perdu/casse). Genere par lot au moment de l'activation de la
    2FA ou d'une regeneration manuelle (voir /parametres/securite).
    """

    __tablename__ = "codes_recuperation_2fa"

    id = Column(Integer, primary_key=True)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id"), nullable=False)
    code_hash = Column(String, nullable=False)
    utilise = Column(Boolean, default=False)
    cree_le = Column(DateTime, default=datetime.utcnow)

    utilisateur = relationship("Utilisateur", back_populates="codes_recuperation")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    nom = Column(String, nullable=False)
    contenu_site = Column(Text, default="")
    account_id = Column(String, default="")
    location_id = Column(String, default="")
    compte_google_id = Column(Integer, ForeignKey("comptes_google.id"), nullable=True)
    consignes_avis = Column(Text, default="")
    # Email et prenom du client (contact personnel, distinct de son nom
    # d'entreprise), utilises pour l'envoi du recap mensuel (voir recap_mensuel.py).
    email = Column(String, default="")
    prenom = Column(String, default="")
    # Permet de desactiver l'envoi automatique du recap mensuel pour ce client
    # sans effacer son email (voir page /recaps).
    recap_actif = Column(Boolean, default=True)
    # Coordonnees de la fiche, mises en cache depuis Google (voir google_location.py)
    # pour centrer la grille de la carte de positions.
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    # Zone geographique ciblee pour la generation de posts IA (voir
    # claude_generation.py) - distincte de latitude/longitude ci-dessus (adresse
    # reelle de la fiche) : une commune de reference choisie librement, avec un
    # rayon, pour que l'IA ne cite pas de localites trop eloignees dans les
    # posts. Optionnel - si desactive, la generation se base uniquement sur le
    # contenu de la fiche/site comme avant.
    localisation_active = Column(Boolean, default=False)
    localisation_ville = Column(String, default="")
    localisation_latitude = Column(Float, nullable=True)
    localisation_longitude = Column(Float, nullable=True)
    localisation_rayon_km = Column(Integer, default=15)
    # Protection de la fiche (voir google_location.valeurs_protegees et
    # planificateur.verifier_protection_fiches) : instantane de reference des
    # champs les plus sensibles aux modifications non sollicitees (nom,
    # telephone, categorie principale, statut ouvert/ferme), capture au moment
    # de l'activation. Compare quotidiennement au reel pour detecter un
    # changement non voulu (Google autorise n'importe qui a suggerer une
    # modification sur une fiche).
    protection_fiche_active = Column(Boolean, default=False)
    protection_titre_ref = Column(String, default="")
    protection_telephone_ref = Column(String, default="")
    protection_categorie_id_ref = Column(String, default="")
    protection_categorie_nom_ref = Column(String, default="")
    protection_statut_ref = Column(String, default="")
    protection_reference_maj_le = Column(DateTime, nullable=True)
    # Dernier statut de validation Google connu ("valide", "non_valide",
    # "inaccessible") - voir AlerteStatutFiche et
    # planificateur.verifier_statut_validation_fiches. NULL tant que la
    # fiche n'a jamais ete verifiee par cette tache (evite une fausse alerte
    # au premier passage apres l'ajout d'une fiche ou ce deploiement).
    dernier_statut_validation = Column(String, nullable=True)
    # Page Facebook (et compte Instagram lie, optionnel) associes a ce client
    # - voir meta_oauth.py. token_page_meta est le token de la Page elle-meme
    # (distinct du token systeme du compte Meta connecte), necessaire pour
    # publier/lire sur cette Page precisement.
    compte_meta_id = Column(Integer, ForeignKey("comptes_meta.id"), nullable=True)
    page_id_meta = Column(String, default="")
    page_nom_meta = Column(String, default="")
    token_page_meta = Column(String, default="")
    instagram_id_meta = Column(String, default="")
    instagram_nom_meta = Column(String, default="")
    # Compte Instagram lie - flux de connexion separe (voir instagram_oauth.py) :
    # token distinct du token de Page Facebook ci-dessus, car "Business Login
    # for Instagram" n'a rien a voir avec "Facebook Login for Business".
    compte_instagram_id = Column(Integer, ForeignKey("comptes_instagram.id"), nullable=True)
    token_instagram = Column(Text, default="")
    # Profil LinkedIn personnel lie (voir linkedin_oauth.py) - pas de gestion
    # de page entreprise possible pour l'instant (Community Management API en
    # attente cote LinkedIn), donc uniquement pertinent pour un client dont le
    # profil personnel EST la presence a publier (ex: Jonathan lui-meme).
    compte_linkedin_id = Column(Integer, ForeignKey("comptes_linkedin.id"), nullable=True)
    # Numero WhatsApp (format international, ex "33612345678") pour le mode
    # rapide vocal par WhatsApp - voir whatsapp_business.py. Optionnel, non
    # lie a une connexion OAuth (pas de "compte" a proprement parler cote
    # WhatsApp, juste un numero de destinataire).
    numero_whatsapp = Column(String, default="")
    # Jours d'envoi des questions hebdomadaires WhatsApp, cle "jour ISO" (0 =
    # lundi ... 6 = dimanche) separes par des virgules, ex "0,2,4" pour
    # lundi/mercredi/vendredi. Vide = pas d'envoi automatique.
    whatsapp_jours = Column(String, default="")
    # A cocher manuellement une fois que CE client (pas Jonathan) a donne son
    # accord pour recevoir les messages WhatsApp automatiques - obligatoire
    # avant tout envoi cote Meta (regles anti-spam sur les messages business
    # a l'initiative de l'entreprise) : sans opt-in confirme, le numero
    # WhatsApp business de l'agence risque d'etre signale/restreint. Non
    # coche par defaut, y compris pour la propre fiche de Jonathan.
    whatsapp_opt_in_confirme = Column(Boolean, default=False)
    # Hashtags "de marque" propres a ce client, toujours inclus en plus des
    # hashtags contextuels generes par l'IA lors de l'adaptation multi-reseaux
    # (voir claude_generation.adapter_post_multi_reseaux) - uniquement pour
    # les reseaux qui utilisent des hashtags (Instagram, LinkedIn).
    hashtags_fixes = Column(String, default="")
    # Portrait de la facon de parler/penser du client, deduit de ses reponses
    # vocales (voir claude_generation.analyser_voix_client) : injecte dans
    # tous les prompts de redaction (voir main._contexte_ia_client) pour que
    # les textes generes sonnent comme lui a l'ecrit.
    profil_voix = Column(Text, default="")
    # Blog WordPress du client (voir wordpress_publish.py) : adresse du site,
    # identifiant et "mot de passe d'application" WordPress (Utilisateurs >
    # Profil, distinct du vrai mot de passe et revocable a tout moment).
    wordpress_url = Column(String, default="")
    wordpress_utilisateur = Column(String, default="")
    wordpress_mot_de_passe = Column(String, default="")
    # Couleur d'accent du site (#rrggbb, voir wordpress_style) pour teinter les
    # blocs des articles, et lien du bouton d'appel a l'action ajoute en fin d'article.
    wordpress_couleur = Column(String, default="")
    wordpress_lien_cta = Column(String, default="")
    # Texte impose du bouton ; vide = l'IA choisit un texte adapte a l'entreprise ("Me contacter", "Nous contacter"...).
    wordpress_texte_cta = Column(String, default="")
    # Propriete Google Search Console du site web du client ("sc-domain:exemple.fr" ou
    # "https://www.exemple.fr/"), voir search_console.py.
    search_console_site = Column(String, default="")
    logo_url = Column(String, default="")  # logo affiche sur les carrousels (voir carrousel_visuel.py)
    # Client mis en avant en haut de la liste de choix de /publication-multi
    # (etoile cliquable) : evite de faire defiler la liste pour retrouver les
    # clients publiés le plus souvent.
    favori_publication_multi = Column(Boolean, default=False)
    cree_le = Column(DateTime, default=datetime.utcnow)

    posts = relationship("Post", back_populates="client", cascade="all, delete-orphan")
    photos = relationship("PhotoFiche", back_populates="client", cascade="all, delete-orphan")
    compte_google = relationship("CompteGoogle", back_populates="clients")
    compte_meta = relationship("CompteMeta", back_populates="clients")
    compte_instagram = relationship("CompteInstagram", back_populates="clients")
    etiquettes = relationship("Etiquette", secondary=client_etiquettes, back_populates="clients")
    mots_cles = relationship("MotCle", back_populates="client", cascade="all, delete-orphan")
    releves_position = relationship("ReleveDePosition", back_populates="client", cascade="all, delete-orphan")
    documents_connaissance = relationship("DocumentConnaissance", back_populates="client", cascade="all, delete-orphan")
    envois_recap = relationship("EnvoiRecap", back_populates="client", cascade="all, delete-orphan")
    requetes_visibilite_ia = relationship("RequeteVisibiliteIA", back_populates="client", cascade="all, delete-orphan")
    resultats_visibilite_ia = relationship("ResultatVisibiliteIA", back_populates="client", cascade="all, delete-orphan")
    avis_connus = relationship("AvisConnu", back_populates="client", cascade="all, delete-orphan")
    alertes_protection = relationship("AlerteProtectionFiche", back_populates="client", cascade="all, delete-orphan")
    alertes_statut = relationship("AlerteStatutFiche", back_populates="client", cascade="all, delete-orphan")
    posts_meta_programmes = relationship("PostMetaProgramme", back_populates="client", cascade="all, delete-orphan")
    posts_instagram_programmes = relationship("PostInstagramProgramme", back_populates="client", cascade="all, delete-orphan")
    suggestions_sujet_jour = relationship("SuggestionSujetJour", back_populates="client", cascade="all, delete-orphan")
    questions_whatsapp_posees = relationship("QuestionWhatsAppPosee", back_populates="client", cascade="all, delete-orphan")
    reponses_interview = relationship("ReponseInterviewClient", back_populates="client", cascade="all, delete-orphan")
    etats_conversation_whatsapp = relationship("EtatConversationWhatsApp", back_populates="client", cascade="all, delete-orphan")
    brouillons_whatsapp = relationship("BrouillonWhatsApp", back_populates="client", cascade="all, delete-orphan")
    photos_reference = relationship("PhotoReferenceClient", back_populates="client", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    titre = Column(String, default="")
    texte = Column(Text, default="")
    image_url = Column(String, default="")
    prompt_image = Column(Text, default="")
    # "", BOOK, CALL, LEARN_MORE, ORDER, SHOP, SIGN_UP (bouton "appel a l'action" Google)
    # CALL par defaut : Jonathan le laisse tel quel la plupart du temps et le
    # change manuellement au besoin plutot que de le selectionner a chaque post.
    type_appel_action = Column(String, default="CALL")
    url_appel_action = Column(String, default="")
    # STANDARD, EVENT, OFFER (format du post Google)
    type_post = Column(String, default="STANDARD")
    evenement_titre = Column(String, default="")
    evenement_date_debut = Column(Date, nullable=True)
    evenement_heure_debut = Column(String, nullable=True)  # "HH:MM"
    evenement_date_fin = Column(Date, nullable=True)
    evenement_heure_fin = Column(String, nullable=True)  # "HH:MM"
    offre_code = Column(String, default="")
    offre_url = Column(String, default="")
    offre_conditions = Column(Text, default="")
    # BROUILLON, A_PUBLIER, PUBLIE_LIVE, PUBLIE_REJECTED, ECHEC_PUBLICATION, IGNORE, SUPPRIME
    statut = Column(String, default="BROUILLON")
    date_prevue = Column(Date, nullable=True)
    heure_prevue = Column(String, nullable=True)  # "HH:MM", utilise avec date_prevue pour la programmation
    id_post_google = Column(String, default="")
    # Regroupe les posts crees ensemble pour un envoi sur plusieurs fiches (voir /posts).
    lot_id = Column(String, nullable=True, index=True)
    cree_le = Column(DateTime, default=datetime.utcnow)
    maj_le = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client", back_populates="posts")
    evenements = relationship(
        "EvenementPublication", back_populates="post", cascade="all, delete-orphan"
    )


class EvenementPublication(Base):
    __tablename__ = "evenements_publication"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    etat = Column(String, nullable=False)
    horodatage = Column(DateTime, default=datetime.utcnow)

    post = relationship("Post", back_populates="evenements")


class EnvoiRecap(Base):
    """Trace d'un envoi (reussi ou non) du recap mensuel a un client, voir recap_mensuel.py."""

    __tablename__ = "envois_recap"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    mois = Column(Integer, nullable=False)
    annee = Column(Integer, nullable=False)
    etat = Column(String, nullable=False)  # ENVOYE, ECHEC
    erreur = Column(Text, default="")
    horodatage = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="envois_recap")


class PhotoFiche(Base):
    """
    Photo importee pour une fiche Google : reste en BROUILLON (simple zone de
    preparation, pas encore envoyee a Google) jusqu'a ce qu'elle soit publiee
    manuellement ou programmee (meme logique que les posts).
    """

    __tablename__ = "photos_fiche"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    url_image = Column(String, default="")
    categorie = Column(String, default="ADDITIONAL")
    legende = Column(Text, default="")
    # Geotag optionnel : coordonnees inscrites dans l'EXIF de la photo au moment de l'envoi.
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    # BROUILLON, A_PUBLIER, PUBLIE_LIVE, ECHEC_PUBLICATION
    statut = Column(String, default="BROUILLON")
    date_prevue = Column(Date, nullable=True)
    heure_prevue = Column(String, nullable=True)  # "HH:MM", utilise avec date_prevue pour la programmation
    id_media_google = Column(String, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)
    maj_le = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client", back_populates="photos")


class ComparatifAvis(Base):
    """
    Snapshot enregistre d'un comparatif d'avis multi-fiches (voir /avis/comparatif) :
    permet de retrouver plus tard un comparatif deja genere (les chiffres au
    moment de la generation, pas recalcules) et d'en telecharger le PDF, sans
    re-interroger Google.
    """

    __tablename__ = "comparatifs_avis"

    id = Column(Integer, primary_key=True)
    libelle = Column(String, default="")
    date_debut = Column(Date, nullable=False)
    date_fin = Column(Date, nullable=False)
    donnees_json = Column(Text, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)


class AvisConnu(Base):
    """
    Dernier etat connu de chaque avis Google d'un client, releve par la tache
    de fond quotidienne verifier_avis_supprimes (voir planificateur.py).
    Google ne fournit aucun moyen direct de lister les avis supprimes ; c'est
    la comparaison entre ce qui est deja connu ici et le releve actuel qui
    permet de detecter une suppression (avis connu mais absent du releve ->
    supprime_le renseigne). Ne peut donc detecter que les suppressions
    survenues APRES la premiere execution de cette tache pour un client donne.
    """

    __tablename__ = "avis_connus"
    __table_args__ = (UniqueConstraint("client_id", "review_id", name="uq_avis_connu_client_review"),)

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    review_id = Column(String, nullable=False)
    auteur = Column(String, default="")
    note = Column(Integer, nullable=True)
    commentaire = Column(Text, default="")
    date_avis = Column(String, default="")  # createTime Google, ISO brut
    reponse = Column(Text, default="")
    premiere_detection_le = Column(DateTime, default=datetime.utcnow)
    derniere_confirmation_le = Column(DateTime, default=datetime.utcnow)
    supprime_le = Column(DateTime, nullable=True)

    client = relationship("Client", back_populates="avis_connus")


class AlerteProtectionFiche(Base):
    """
    Changement suspect detecte sur un champ protege d'une fiche (voir
    Client.protection_*_ref et planificateur.verifier_protection_fiches) :
    reste en attente (traite_le NULL) jusqu'a ce que Jonathan choisisse de
    restaurer la valeur de reference ou d'accepter le changement comme
    nouvelle reference, depuis la page /alertes.
    """

    __tablename__ = "alertes_protection_fiche"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    champ = Column(String, nullable=False)  # titre, telephone, categorie, statut
    libelle_champ = Column(String, default="")
    valeur_reference = Column(String, default="")  # texte affichable, pas necessairement la valeur brute API
    valeur_detectee = Column(String, default="")
    detecte_le = Column(DateTime, default=datetime.utcnow)
    traite_le = Column(DateTime, nullable=True)
    action = Column(String, nullable=True)  # RESTAURE, IGNORE, MASQUE

    client = relationship("Client", back_populates="alertes_protection")


class AlerteStatutFiche(Base):
    """
    Changement du statut de validation Google (voir Client.dernier_statut_validation
    et planificateur.verifier_statut_validation_fiches) : "non_valide" (fiche
    en attente de verification Google), "valide" (Voice of Merchant confirme)
    ou "inaccessible" (la fiche ne repond plus - signe probable d'une
    suspension, sans certitude absolue car Google n'expose pas d'etat
    "suspendu" explicite dans l'API). Purement informatif (rien a restaurer
    sur Google) : Jonathan marque juste l'alerte comme vue depuis /alertes.
    """

    __tablename__ = "alertes_statut_fiche"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    statut_avant = Column(String, nullable=False)
    statut_apres = Column(String, nullable=False)
    detecte_le = Column(DateTime, default=datetime.utcnow)
    traite_le = Column(DateTime, nullable=True)

    client = relationship("Client", back_populates="alertes_statut")


class LeadAudit(Base):
    """
    Contact capture sur la page publique d'audit gratuit (/audit-gratuit) -
    coordonnees completes demandees avant tout traitement. Ne declenche plus
    aucun appel DataForSEO a la soumission (protege le solde et filtre le
    spam) : Jonathan lance lui-meme l'audit depuis /leads quand il le juge
    pertinent (voir /prospection, pre-rempli avec ces coordonnees), puis
    envoie le PDF genere par email via Brevo quand il est pret.

    score/details_json : vestiges de l'ancien flux (score calcule
    automatiquement a la soumission) - plus alimentes, conserves pour les
    leads deja enregistres avant ce changement.
    """

    __tablename__ = "leads_audit"

    id = Column(Integer, primary_key=True)
    prenom = Column(String, default="")
    nom = Column(String, default="")
    email = Column(String, default="")
    telephone = Column(String, default="")
    entreprise_nom = Column(String, default="")
    ville = Column(String, default="")
    score = Column(Integer, nullable=True)
    details_json = Column(Text, default="")
    pdf_audit_base64 = Column(Text, nullable=True)
    audite_le = Column(DateTime, nullable=True)
    envoye_le = Column(DateTime, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)


class Etiquette(Base):
    """Etiquette libre posee sur un ou plusieurs clients, pour les regrouper (ex. envoi multi-fiches)."""

    __tablename__ = "etiquettes"

    id = Column(Integer, primary_key=True)
    nom = Column(String, unique=True, nullable=False)
    # Si vrai, les clients de cette etiquette sont exclus des vues generales
    # (accueil, avis, alertes) et geres a part dans un "espace" dedie -
    # voir /espaces et les parametres ?etiquette=... des routes concernees.
    isolee = Column(Boolean, default=False)

    clients = relationship("Client", secondary=client_etiquettes, back_populates="etiquettes")


class MotCle(Base):
    """Mot-cle suivi pour un client, reutilise a chaque releve de position (voir rank_tracking.py)."""

    __tablename__ = "mots_cles"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    texte = Column(String, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="mots_cles")


class RequeteVisibiliteIA(Base):
    """
    Question suivie pour verifier si le client est cite par les IA
    generatives (ChatGPT, Gemini) - equivalent de MotCle mais pour le
    suivi de visibilite IA ("GEO") plutot que le classement Google
    classique (voir rank_tracking.py).
    """

    __tablename__ = "requetes_visibilite_ia"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    texte = Column(String, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="requetes_visibilite_ia")


class ResultatVisibiliteIA(Base):
    """
    Un releve de visibilite IA pour une requete donnee, sur un modele donne
    (chatgpt/gemini), a une date donnee. La requete est copiee ici (comme
    ReleveDePosition.mot_cle_texte) pour garder l'historique meme si la
    requete suivie est supprimee ensuite.
    """

    __tablename__ = "resultats_visibilite_ia"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    requete_texte = Column(String, nullable=False)
    modele = Column(String, nullable=False)  # "chatgpt" ou "gemini"
    client_cite = Column(Boolean, default=False)
    position = Column(Integer, nullable=True)  # rang approximatif si cite (1 = premier mentionne)
    concurrents_cites = Column(Text, default="")  # JSON: liste de noms
    suggestion = Column(Text, default="")
    reponse_brute = Column(Text, default="")
    erreur = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="resultats_visibilite_ia")


class DocumentConnaissance(Base):
    """
    Document (PDF/Word/texte) fournissant du contexte supplementaire a l'IA
    pour ce client, en complement du champ libre Client.contenu_site. Seul
    le texte extrait est conserve, pas le fichier d'origine.
    """

    __tablename__ = "documents_connaissance"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    nom_fichier = Column(String, default="")
    texte_extrait = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="documents_connaissance")


class ReleveDePosition(Base):
    """
    Une verification de classement sur une grille de points geographiques pour
    un mot-cle donne (equivalent d'un "scan" Localo). Le mot-cle est copie ici
    (independant de MotCle) pour garder l'historique meme si le mot-cle est
    supprime plus tard.
    """

    __tablename__ = "releves_position"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    mot_cle_texte = Column(String, nullable=False)
    taille_grille = Column(Integer, default=5)
    rayon_km = Column(Float, default=2.0)
    latitude_centre = Column(Float, nullable=False)
    longitude_centre = Column(Float, nullable=False)
    # EN_COURS, TERMINE, ECHEC
    statut = Column(String, default="EN_COURS")
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="releves_position")
    points = relationship("PointDeGrille", back_populates="releve", cascade="all, delete-orphan")


class PointDeGrille(Base):
    """Un point de la grille d'un releve, avec le classement trouve a cet endroit (voir rank_tracking.py)."""

    __tablename__ = "points_grille"

    id = Column(Integer, primary_key=True)
    releve_id = Column(Integer, ForeignKey("releves_position.id"), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    # None tant que le point n'a pas ete verifie ; reste None si l'entreprise
    # n'apparait pas dans les resultats renvoyes (hors classement visible).
    position = Column(Integer, nullable=True)
    verifie = Column(Boolean, default=False)
    nom_correspondance = Column(String, default="")
    # Top 10 des resultats a ce point (JSON : [{"position": int, "nom": str}, ...]),
    # conserve pour reafficher le classement sans refaire d'appel API.
    resultats_json = Column(Text, default="")

    releve = relationship("ReleveDePosition", back_populates="points")


class CompteGoogle(Base):
    """
    Un compte Google connecte a la plateforme. Plusieurs comptes peuvent etre
    connectes (Jonathan gere des fiches reparties sur plusieurs comptes Google).
    """

    __tablename__ = "comptes_google"

    id = Column(Integer, primary_key=True)
    libelle = Column(String, default="")  # adresse e-mail du compte Google, si recuperee
    refresh_token = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)

    clients = relationship("Client", back_populates="compte_google")


class CompteMeta(Base):
    """
    Un compte Meta (Facebook/Instagram) connecte a la plateforme via
    "Facebook Login for Business" (voir meta_oauth.py) - token d'acces
    utilisateur systeme, n'expire jamais (choix fait a la creation de la
    Configuration cote tableau de bord developpeur Meta).
    """

    __tablename__ = "comptes_meta"

    id = Column(Integer, primary_key=True)
    libelle = Column(String, default="")
    access_token = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)

    clients = relationship("Client", back_populates="compte_meta")


class CompteInstagram(Base):
    """
    Un compte Instagram connecte via "Business Login for Instagram" (voir
    instagram_oauth.py) - flux distinct de CompteMeta. Le token expire (60
    jours) et doit etre rafraichi avant expiration, contrairement au token
    systeme Meta.
    """

    __tablename__ = "comptes_instagram"

    id = Column(Integer, primary_key=True)
    libelle = Column(String, default="")
    identifiant_instagram = Column(String, default="")
    access_token = Column(Text, default="")
    expire_le = Column(DateTime, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)

    clients = relationship("Client", back_populates="compte_instagram")


class CompteLinkedIn(Base):
    """
    Un profil LinkedIn personnel connecte via OAuth (voir linkedin_oauth.py -
    produits "Sign In with LinkedIn using OpenID Connect" + "Share on
    LinkedIn"). Le token expire (~60 jours) et LinkedIn ne fournit pas de
    refresh token sans produit supplementaire approuve : au-dela de
    l'expiration, il faut reconnecter le compte manuellement.

    Pas encore de lien vers Client : la gestion des pages entreprise des
    clients necessite le produit "Community Management API", en attente de
    validation par LinkedIn au moment de l'ecriture de ce modele.
    """

    __tablename__ = "comptes_linkedin"

    id = Column(Integer, primary_key=True)
    libelle = Column(String, default="")
    identifiant_membre = Column(String, default="")
    access_token = Column(Text, default="")
    expire_le = Column(DateTime, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)


class PostLinkedInProgramme(Base):
    """
    Post LinkedIn en attente de publication a une date/heure future (voir
    linkedin_publish.py + planificateur.publier_posts_linkedin_programmes).
    L'image est stockee directement en base (contrairement aux posts Google,
    qui referencent une image_url hebergee ailleurs) : LinkedIn accepte un
    televersement direct des octets au moment de la publication, pas besoin
    d'hebergement intermediaire.
    """

    __tablename__ = "posts_linkedin_programmes"

    id = Column(Integer, primary_key=True)
    compte_linkedin_id = Column(Integer, ForeignKey("comptes_linkedin.id"), nullable=False)
    texte = Column(Text, default="")
    image_donnees = Column(LargeBinary, nullable=True)
    # Video au lieu d'une image (mutuellement exclusif) : memes octets
    # directement en base, LinkedIn accepte aussi un televersement direct au
    # moment de la publication (voir linkedin_publish.publier_post).
    video_donnees = Column(LargeBinary, nullable=True)
    # Carrousel : PDF publie comme "document" LinkedIn (image_donnees garde la 1re slide, en repli).
    document_donnees = Column(LargeBinary, nullable=True)
    document_titre = Column(String, default="")
    publier_le = Column(DateTime, nullable=False)
    etat = Column(String, default="EN_ATTENTE")  # EN_ATTENTE, PUBLIE, ECHEC
    erreur = Column(Text, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)

    compte = relationship("CompteLinkedIn")


class PostMetaProgramme(Base):
    """
    Post Facebook (Page du client) en attente de publication a une date/heure
    future (voir meta_publish.py + planificateur.publier_posts_meta_programmes).
    image_url : contrairement a LinkedIn, l'API Graph attend une URL
    publique, pas des octets - hebergee sur OVH au moment de la composition
    (voir ovh_upload.py), pas au moment de la publication.
    """

    __tablename__ = "posts_meta_programmes"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    texte = Column(Text, default="")
    image_url = Column(String, nullable=True)
    # Video au lieu d'image(s) (mutuellement exclusif) : URL hebergee sur OVH,
    # Facebook la televerse lui-meme depuis cette URL (voir meta_publish.publier_video_page).
    video_url = Column(String, nullable=True)
    publier_le = Column(DateTime, nullable=False)
    etat = Column(String, default="EN_ATTENTE")  # EN_ATTENTE, PUBLIE, ECHEC
    erreur = Column(Text, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="posts_meta_programmes")


class PostInstagramProgramme(Base):
    """Post Instagram (compte du client) en attente de publication - voir PostMetaProgramme, meme logique."""

    __tablename__ = "posts_instagram_programmes"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    texte = Column(Text, default="")
    image_url = Column(String, nullable=True)
    # Video au lieu d'image(s) (mutuellement exclusif) : publiee en Reel (voir instagram_publish.publier_reel).
    video_url = Column(String, nullable=True)
    publier_le = Column(DateTime, nullable=False)
    etat = Column(String, default="EN_ATTENTE")  # EN_ATTENTE, PUBLIE, ECHEC
    erreur = Column(Text, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="posts_instagram_programmes")


class ParametreGoogleAds(Base):
    """
    Configuration Google Ads (Keyword Planner) - un seul compte pour toute
    l'agence (pas un par client comme CompteGoogle), une seule ligne attendue
    en base. refresh_token reste vide tant que la connexion OAuth (scope
    adwords, distinct du scope Business Profile) n'a pas ete faite.
    """

    __tablename__ = "parametre_google_ads"

    id = Column(Integer, primary_key=True)
    developer_token = Column(String, default="")
    customer_id = Column(String, default="")  # 10 chiffres, sans tirets
    refresh_token = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)


class ParametreSearchConsole(Base):
    """
    Connexion Google Search Console (scope webmasters.readonly, distinct de celui
    des fiches Business Profile) - un seul compte pour toute l'agence, comme
    ParametreGoogleAds, a ajouter comme utilisateur dans la Search Console de
    chaque site client. Une seule ligne attendue.
    """

    __tablename__ = "parametre_search_console"

    id = Column(Integer, primary_key=True)
    refresh_token = Column(Text, default="")
    libelle = Column(String, default="")  # adresse e-mail du compte connecte
    cree_le = Column(DateTime, default=datetime.utcnow)


class PromptImageGenere(Base):
    """
    Historique des prompts d'images IA generes avec l'option "Varier automatiquement" :
    les derniers d'un client sont passes a l'IA pour qu'elle ne refasse pas la meme scene.
    """

    __tablename__ = "prompt_image_genere"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, index=True, nullable=False)
    prompt = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)


class SuggestionSujetJour(Base):
    """
    Sujets tendance generes automatiquement chaque matin (voir
    planificateur.generer_suggestions_quotidiennes) pour une fiche, a partir
    de veille_actualite + claude_generation.suggerer_sujets_actualite -
    permet au composeur multi-reseaux de les afficher deja prets a
    l'ouverture, sans attendre un appel IA en direct. Le lot du jour remplace
    celui de la veille (voir la tache qui les genere) plutot que de
    s'accumuler indefiniment.
    """

    __tablename__ = "suggestions_sujet_jour"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    sujet = Column(Text, default="")
    titre_article = Column(Text, default="")
    source = Column(String, default="")
    url = Column(Text, default="")
    extrait = Column(Text, default="")
    genere_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="suggestions_sujet_jour")


class EtatConversationWhatsApp(Base):
    """
    Suivi d'une conversation WhatsApp en cours (voir whatsapp_business.py et
    le webhook /whatsapp/webhook) : questions envoyees, question choisie par
    numero (reponse "1" a "5"), photo eventuellement recue avant le vocal -
    necessaire car le webhook n'a pas de session navigateur pour garder cet
    etat entre deux messages recus. Supprime des qu'un BrouillonWhatsApp est
    genere avec succes.
    """

    __tablename__ = "etat_conversation_whatsapp"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    numero = Column(String, nullable=False, unique=True)
    questions_json = Column(Text, default="[]")
    question_choisie = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    maj_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="etats_conversation_whatsapp")


class QuestionWhatsAppPosee(Base):
    """
    Historique des questions deja envoyees par WhatsApp a un client (voir
    planificateur.envoyer_questions_whatsapp_si_prevu) : sert a exclure les
    questions trop proches lors de la generation suivante, puisque chaque
    envoi doit proposer des questions renouvelees.
    """

    __tablename__ = "questions_whatsapp_posees"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    question = Column(Text, default="")
    posee_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="questions_whatsapp_posees")


class ReponseInterviewClient(Base):
    """
    Reponse vocale (transcrite) donnee par un client a une question
    d'interview WhatsApp, conservee durablement sur la fiche (contrairement a
    BrouillonWhatsApp qui est supprime une fois charge dans le composeur) :
    sert de memoire du positionnement du client pour affiner les questions
    suivantes (voir planificateur.envoyer_questions_whatsapp_si_prevu).
    """

    __tablename__ = "reponses_interview_client"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    question = Column(Text, default="")
    reponse_transcrite = Column(Text, default="")
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="reponses_interview")


class BrouillonWhatsApp(Base):
    """
    Post pret, genere a partir d'une reponse vocale recue par WhatsApp (voir
    claude_generation.generer_post_depuis_reponse). La generation se fait au
    moment de la reception du vocal (dans le webhook, sans session
    navigateur) : le resultat attend ici que Jonathan ouvre la plateforme
    pour le charger dans le composeur multi-reseaux (voir /publication-multi
    et le bandeau "brouillon WhatsApp pret").
    """

    __tablename__ = "brouillons_whatsapp"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    texte = Column(Text, default="")
    prompt_image = Column(Text, default="")
    image_url = Column(String, nullable=True)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="brouillons_whatsapp")


class PhotoReferenceClient(Base):
    """
    Photo de reference du client (visage, sous differents angles), fournie
    volontairement par le client pour apparaitre comme sujet des images
    generees par l'IA - voir gemini_images.generer_image (parametre
    images_reference) et la case "M'inclure dans l'image" du composeur
    multi-reseaux. Limitee a quelques photos par client (voir la route
    d'upload), tres en dessous des 14 images de reference max de Gemini.
    """

    __tablename__ = "photos_reference_client"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    image_url = Column(String, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="photos_reference")
