"""
Archive des creations par client : videos (motion design, montages, videos IA), visuels, carrousels. Chaque creation garde l'adresse de son fichier,
heberge sur OVH (voir ovh_upload.py), son titre, sa date et ses reglages ; la page /creations permet de la retrouver, la telecharger, la republier.
Les scripts video sont dans leur propre table (models.ScriptVideo) et sont simplement rattaches a un client.
"""

import json
from datetime import datetime, timedelta

from . import models, ovh_upload

GENRES = {"video": "Vidéo", "visuel": "Visuel", "carrousel": "Carrousel"}


def _urls_valides(urls) -> list:
    return [u for u in (urls or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]


def enregistrer(db, client_id, genre: str, titre: str, urls: list, format_video: str = "", duree: float = 0.0, meta: dict = None) -> "models.Creation":
    """
    Garde une creation. Une creation deja archivee avec la meme premiere adresse n'est pas dupliquee (un clic sur « Publier » peut se repeter).
    Ne leve jamais d'erreur de donnees : une liste d'adresses vide n'enregistre rien et renvoie None.
    """
    urls = _urls_valides(urls)
    if not urls:
        return None
    for existante in db.query(models.Creation).order_by(models.Creation.id.desc()).limit(400).all():
        if urls[0] in json.loads(existante.urls_json or "[]"):
            if client_id and not existante.client_id:
                existante.client_id = client_id              # une creation archivee « sans client » est rattachee des qu'on sait pour qui elle est
                db.commit()
            return existante
    ligne = models.Creation(
        client_id=client_id or None, genre=genre if genre in GENRES else "visuel", titre=" ".join(str(titre or "").split())[:200] or GENRES.get(genre, "Création"),
        urls_json=json.dumps(urls, ensure_ascii=False), format=format_video or "", duree=float(duree or 0.0),
        meta_json=json.dumps(meta or {}, ensure_ascii=False, default=str), cree_le=datetime.utcnow(),
    )
    db.add(ligne)
    db.commit()
    return ligne


def nom_distant(url: str) -> str:
    """Nom du fichier sur l'hebergement OVH si l'adresse est bien celle de NOTRE hebergement, sinon chaine vide (on ne supprime jamais ailleurs)."""
    base = (ovh_upload.URL_PUBLIQUE_BASE or "").rstrip("/") + "/"
    if len(base) <= 1 or not (url or "").startswith(base):
        return ""
    nom = url[len(base):].split("?", 1)[0]
    return nom if nom and "/" not in nom else ""


def supprimer(db, creation) -> int:
    """Supprime la creation et ses fichiers sur OVH (au mieux : un echec de suppression distante n'empeche pas de la retirer). Renvoie le nombre de fichiers retires."""
    retires = 0
    for url in json.loads(creation.urls_json or "[]"):
        nom = nom_distant(url)
        if nom:
            try:
                ovh_upload.supprimer_fichier(nom)
                retires += 1
            except Exception:
                pass
    db.delete(creation)
    db.commit()
    return retires


def en_dict(creation) -> dict:
    try:
        meta = json.loads(creation.meta_json or "{}")
    except ValueError:
        meta = {}
    return {"id": creation.id, "client_id": creation.client_id, "epingle": bool(creation.epingle), "genre": creation.genre, "genre_libelle": GENRES.get(creation.genre, "Création"), "titre": creation.titre,
            "urls": json.loads(creation.urls_json or "[]"), "format": creation.format, "duree": creation.duree, "meta": meta,
            "date": creation.cree_le.strftime("%d/%m/%Y %H:%M") if creation.cree_le else ""}


# --------------------------------------------------------------------------- suppression automatique des anciennes creations

CHOIX_CONSERVATION = (0, 1, 3, 6, 12)          # en mois ; 0 = ne jamais supprimer
MOIS_CONSERVATION_DEFAUT = 3


def mois_conservation(db) -> int:
    ligne = db.query(models.ParametreArchive).first()
    return ligne.mois_conservation if ligne is not None and ligne.mois_conservation in CHOIX_CONSERVATION else (MOIS_CONSERVATION_DEFAUT if ligne is None else 0)


def enregistrer_reglage(db, mois) -> int:
    """Duree de conservation (0, 1, 3, 6 ou 12 mois) ; toute autre valeur est refusee (ValueError)."""
    try:
        mois = int(mois)
    except (TypeError, ValueError):
        raise ValueError("Durée invalide.")
    if mois not in CHOIX_CONSERVATION:
        raise ValueError("Durée invalide.")
    ligne = db.query(models.ParametreArchive).first() or models.ParametreArchive()
    ligne.mois_conservation, ligne.maj_le = mois, datetime.utcnow()
    db.add(ligne)
    db.commit()
    return mois


def _texte_references(db, maintenant: datetime) -> str:
    """Tout le texte des publications NON terminees (programmees, brouillons, a reessayer) : un fichier cite la-dedans est encore necessaire, on ne le supprime jamais."""
    morceaux = []
    for modele in (models.PostMetaProgramme, models.PostInstagramProgramme):
        for p in db.query(modele).filter(modele.etat.in_(("EN_ATTENTE", "ECHEC"))).all():
            morceaux += [p.image_url or "", p.video_url or ""]
    for p in db.query(models.Post).filter(models.Post.statut.in_(("BROUILLON", "A_PUBLIER"))).all():
        morceaux.append(p.image_url or "")
    return "\n".join(morceaux)


def a_purger(db, mois: int, maintenant: datetime = None) -> tuple:
    """(creations a supprimer, nombre de creations protegees) : plus anciennes que `mois` mois, non epinglees, et pas utilisees par une publication a venir."""
    if not mois:
        return [], 0
    maintenant = maintenant or datetime.utcnow()
    limite = maintenant - timedelta(days=30 * mois)
    references = _texte_references(db, maintenant)
    candidates, protegees = [], 0
    for c in db.query(models.Creation).filter(models.Creation.cree_le < limite).filter((models.Creation.epingle == False) | (models.Creation.epingle.is_(None))).all():  # noqa: E712
        if any(u in references for u in json.loads(c.urls_json or "[]")):
            protegees += 1
        else:
            candidates.append(c)
    return candidates, protegees


def purger(db, mois: int, maintenant: datetime = None) -> dict:
    """Supprime les anciennes creations (fichiers OVH compris) et leurs entrees de la bibliotheque des videos. Renvoie {"supprimees", "protegees", "fichiers"}."""
    if not mois:
        return {"supprimees": 0, "protegees": 0, "fichiers": 0}
    candidates, protegees = a_purger(db, mois, maintenant)
    fichiers = 0
    for c in candidates:
        urls = json.loads(c.urls_json or "[]")
        fichiers += supprimer(db, c)
        if urls:
            db.query(models.VideoMontee).filter(models.VideoMontee.url.in_(urls)).delete(synchronize_session=False)
            db.commit()
    ligne = db.query(models.ParametreArchive).first() or models.ParametreArchive(mois_conservation=mois)
    ligne.derniere_purge, ligne.derniere_purge_nombre = datetime.utcnow(), len(candidates)
    db.add(ligne)
    db.commit()
    return {"supprimees": len(candidates), "protegees": protegees, "fichiers": fichiers}
