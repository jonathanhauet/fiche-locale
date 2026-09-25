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

# Force le pilote installe (psycopg2-binary) : les versions recentes de SQLAlchemy prennent "psycopg" (v3)
# par defaut pour "postgresql://", pilote absent d'ici, et la plateforme ne demarrait plus.
for _prefixe in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefixe):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(_prefixe):]

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
