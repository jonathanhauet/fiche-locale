"""
Script a lancer une seule fois (en local, ou plus tard sur Railway via sa
console) pour creer ou reinitialiser le compte de connexion a la plateforme.

Usage : depuis le dossier plateforme_web/,
    python creer_admin.py
"""

import getpass
import sys

from app import models
from app.database import Base, SessionLocal, engine
from app.security import hacher_mot_de_passe

Base.metadata.create_all(bind=engine)


def main():
    identifiant = input("Identifiant souhaite : ").strip()
    if not identifiant:
        print("ERREUR : l'identifiant ne peut pas etre vide.")
        sys.exit(1)

    mot_de_passe = getpass.getpass("Mot de passe : ")
    confirmation = getpass.getpass("Confirmez le mot de passe : ")

    if not mot_de_passe:
        print("ERREUR : le mot de passe ne peut pas etre vide.")
        sys.exit(1)
    if mot_de_passe != confirmation:
        print("ERREUR : les deux mots de passe ne correspondent pas.")
        sys.exit(1)

    db = SessionLocal()
    try:
        utilisateur = db.query(models.Utilisateur).filter_by(identifiant=identifiant).first()
        if utilisateur:
            utilisateur.mot_de_passe_hash = hacher_mot_de_passe(mot_de_passe)
            print(f"Mot de passe mis a jour pour '{identifiant}'.")
        else:
            utilisateur = models.Utilisateur(
                identifiant=identifiant,
                mot_de_passe_hash=hacher_mot_de_passe(mot_de_passe),
            )
            db.add(utilisateur)
            print(f"Compte '{identifiant}' cree.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
