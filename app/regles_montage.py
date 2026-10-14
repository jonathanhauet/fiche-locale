"""
Memoire de style du montage video : les preferences que l'utilisateur veut retrouver a chaque montage (« moins de zooms », « sous-titres plus bas »,
« toujours une carte pour les chiffres »...) et le modele de style choisi par defaut. Les preferences sont ajoutees aux consignes des IA
(accroche et mots-cles, cartes, commandes de l'editeur, notes de relecture) ; le style par defaut preselectionne les reglages d'un nouveau montage.
"""

from . import models

NB_REGLES_MAX = 30
LONGUEUR_MAX = 240


def lister(db) -> list:
    return [{"id": r.id, "texte": r.texte} for r in db.query(models.RegleMontage).order_by(models.RegleMontage.cree_le, models.RegleMontage.id).all()]


def textes(db) -> list:
    return [r["texte"] for r in lister(db)]


def ajouter(db, texte: str):
    """Retient une preference (sans doublon) ; ValueError si elle est vide ou si la liste est pleine."""
    texte = " ".join(str(texte or "").split())[:LONGUEUR_MAX]
    if not texte:
        raise ValueError("Écrivez la préférence à retenir.")
    existante = [r for r in db.query(models.RegleMontage).all() if r.texte.lower() == texte.lower()]
    if existante:
        return existante[0]
    if db.query(models.RegleMontage).count() >= NB_REGLES_MAX:
        raise ValueError(f"{NB_REGLES_MAX} préférences au maximum : supprimez-en d'abord.")
    regle = models.RegleMontage(texte=texte)
    db.add(regle)
    db.commit()
    return regle


def supprimer(db, identifiant: int) -> bool:
    regle = db.get(models.RegleMontage, identifiant)
    if not regle:
        return False
    db.delete(regle)
    db.commit()
    return True


def style_par_defaut(db) -> str:
    ligne = db.query(models.ParametreMontage).first()
    return ligne.style_defaut if ligne else ""


def definir_style_par_defaut(db, identifiant: str) -> None:
    ligne = db.query(models.ParametreMontage).first() or models.ParametreMontage()
    ligne.style_defaut = identifiant or ""
    db.add(ligne)
    db.commit()
