"""
Connexion a la base de donnees.

En local, utilise un fichier SQLite (donnees.db) par defaut.
En production (Railway), DATABASE_URL pointera vers la base PostgreSQL
fournie automatiquement.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DOSSIER_PLATEFORME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{os.path.join(DOSSIER_PLATEFORME, 'donnees.db')}"

arguments_connexion = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=arguments_connexion)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def obtenir_session():
    """Dependance FastAPI : fournit une session de base de donnees par requete."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
