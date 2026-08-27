"""
Modeles de la base de donnees. Remplacent progressivement les fichiers
texte utilises par les scripts en ligne de commande (clients/*.txt,
identifiants_fiches.json, posts_generes/*.txt, logs/journal_publications.csv).
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, Text
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
    cree_le = Column(DateTime, default=datetime.utcnow)

    posts = relationship("Post", back_populates="client", cascade="all, delete-orphan")
    photos = relationship("PhotoFiche", back_populates="client", cascade="all, delete-orphan")
    compte_google = relationship("CompteGoogle", back_populates="clients")
    etiquettes = relationship("Etiquette", secondary=client_etiquettes, back_populates="clients")
    mots_cles = relationship("MotCle", back_populates="client", cascade="all, delete-orphan")
    releves_position = relationship("ReleveDePosition", back_populates="client", cascade="all, delete-orphan")
    documents_connaissance = relationship("DocumentConnaissance", back_populates="client", cascade="all, delete-orphan")
    envois_recap = relationship("EnvoiRecap", back_populates="client", cascade="all, delete-orphan")
    requetes_visibilite_ia = relationship("RequeteVisibiliteIA", back_populates="client", cascade="all, delete-orphan")
    resultats_visibilite_ia = relationship("ResultatVisibiliteIA", back_populates="client", cascade="all, delete-orphan")


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
