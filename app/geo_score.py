"""
Score GEO (visibilite dans les reponses des assistants IA) : synthese a partir des derniers resultats par
(question, assistant), instantane mensuel (ScoreGEO) pour suivre l'evolution, et suivi automatique.
"""

import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ia_visibilite, models

# Points d'une reponse ou le client est cite, selon son rang dans la reponse (non cite = 0).
POINTS_PAR_POSITION = {1: 100, 2: 85, 3: 70}
POINTS_POSITION_AUTRE = 55
POINTS_POSITION_INCONNUE = 70
MAX_QUESTIONS_SUIVI_AUTO = 10

RESEAUX_SOCIAUX = {"facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "youtube.com", "x.com", "twitter.com", "pinterest.com"}
PLATEFORMES_AVIS = {
    "trustpilot.com", "tripadvisor.com", "tripadvisor.fr", "yelp.com", "yelp.fr", "nosavis.be", "avis-verifies.com",
    "google.com", "maps.google.com", "trustedshops.fr", "ledenicheur.fr", "meilleursagents.com", "doctolib.fr",
}
ANNUAIRES = {
    "pagesjaunes.fr", "goldenpages.be", "pagesdor.be", "cylex.be", "cylex.fr", "hotfrog.fr", "europages.fr", "kompass.com",
    "infobel.com", "118000.fr", "118218.fr", "mappy.com", "foursquare.com", "bing.com", "apple.com", "justacote.com",
    "local.fr", "yellowpages.com", "societe.com", "pappers.fr", "bottin.be", "companyweb.be", "trouver-un-artisan.be",
}


def mois_courant() -> str:
    return datetime.now(ZoneInfo("Europe/Brussels")).strftime("%Y-%m")


def categorie_domaine(domaine: str, site_client: str = "") -> str:
    """Type de source : votre_site, reseau_social, avis, annuaire, autre (pour savoir ou agir)."""
    domaine = ia_visibilite.domaine_depuis(domaine)
    site = ia_visibilite.domaine_depuis(site_client)
    if site and (domaine == site or domaine.endswith("." + site)):
        return "votre_site"
    for ensemble, nom in ((RESEAUX_SOCIAUX, "reseau_social"), (PLATEFORMES_AVIS, "avis"), (ANNUAIRES, "annuaire")):
        if domaine in ensemble or any(domaine.endswith("." + d) for d in ensemble):
            return nom
    return "autre"


def _json_liste(valeur) -> list:
    try:
        resultat = json.loads(valeur or "[]")
        return resultat if isinstance(resultat, list) else []
    except ValueError:
        return []


def points_resultat(client_cite: bool, position) -> int:
    if not client_cite:
        return 0
    if not position:
        return POINTS_POSITION_INCONNUE
    return POINTS_PAR_POSITION.get(position, POINTS_POSITION_AUTRE)


def derniers_resultats(db, client_id: int) -> list:
    """Le releve le plus recent par (question, assistant), uniquement pour les questions encore suivies."""
    suivies = {r.texte for r in db.query(models.RequeteVisibiliteIA).filter_by(client_id=client_id).all()}
    tous = (
        db.query(models.ResultatVisibiliteIA).filter_by(client_id=client_id)
        .order_by(models.ResultatVisibiliteIA.cree_le.desc(), models.ResultatVisibiliteIA.id.desc()).all()
    )
    derniers = {}
    for resultat in tous:
        if resultat.requete_texte in suivies:
            derniers.setdefault((resultat.requete_texte, resultat.modele), resultat)
    return list(derniers.values())


def synthese(resultats: list, site_client: str = "") -> dict:
    """
    Indicateurs a partir d'une liste de ResultatVisibiliteIA (les plus recents par question x assistant).
    Les reponses en erreur sont ignorees : une panne d'API ne doit pas faire chuter le score.
    """
    valides = [r for r in resultats if not r.erreur]
    cites = [r for r in valides if r.client_cite]
    positions = [r.position for r in cites if r.position]

    concurrents = Counter()
    noms_affiches = {}
    for r in valides:
        for nom in _json_liste(r.concurrents_cites):
            cle = str(nom).strip().lower()
            if cle:
                concurrents[cle] += 1
                noms_affiches.setdefault(cle, str(nom).strip())
    total_mentions_concurrents = sum(concurrents.values())

    sources = Counter()
    exemples = {}
    for r in valides:
        domaines_de_cette_reponse = set()
        for source in _json_liste(r.sources):
            domaine = ia_visibilite.domaine_depuis(source.get("domaine") or source.get("url") or "")
            if domaine:
                domaines_de_cette_reponse.add(domaine)
                exemples.setdefault(domaine, source.get("url", ""))
        sources.update(domaines_de_cette_reponse)   # un domaine compte une fois par reponse

    par_modele = {}
    for r in valides:
        stats = par_modele.setdefault(r.modele, {"total": 0, "cites": 0})
        stats["total"] += 1
        stats["cites"] += 1 if r.client_cite else 0

    par_question = {}
    for r in valides:
        info = par_question.setdefault(r.requete_texte, {"texte": r.requete_texte, "cite_sur": [], "non_cite_sur": [], "concurrents": Counter()})
        (info["cite_sur"] if r.client_cite else info["non_cite_sur"]).append(r.modele)
        for nom in _json_liste(r.concurrents_cites):
            info["concurrents"][str(nom).strip()] += 1
    questions_perdues = [
        {"texte": i["texte"], "modeles": i["non_cite_sur"], "concurrents": [n for n, _ in i["concurrents"].most_common(3)]}
        for i in par_question.values() if not i["cite_sur"]
    ]
    questions_gagnees = [i["texte"] for i in par_question.values() if i["cite_sur"]]

    score = round(sum(points_resultat(r.client_cite, r.position) for r in valides) / len(valides)) if valides else None
    mentions_client = len(cites)
    return {
        "nb_reponses": len(valides),
        "nb_cites": mentions_client,
        "taux_citation": round(100 * mentions_client / len(valides)) if valides else None,
        "position_moyenne": round(sum(positions) / len(positions), 1) if positions else None,
        "score": score,
        "part_de_voix": round(100 * mentions_client / (mentions_client + total_mentions_concurrents))
        if (mentions_client + total_mentions_concurrents) else None,
        "concurrents_top": [{"nom": noms_affiches[c], "mentions": n} for c, n in concurrents.most_common(5)],
        "sources_top": [
            {"domaine": d, "nb": n, "url": exemples.get(d, ""), "categorie": categorie_domaine(d, site_client)}
            for d, n in sources.most_common(10)
        ],
        "par_modele": par_modele,
        "questions_perdues": questions_perdues,
        "questions_gagnees": questions_gagnees,
        "nb_erreurs": len(resultats) - len(valides),
    }


def synthese_client(db, client) -> dict:
    return synthese(derniers_resultats(db, client.id), client.site_web or client.wordpress_url or "")


def enregistrer_instantane(db, client, auto: bool = False):
    """Photographie du mois courant (remplace celle du mois si elle existe). None s'il n'y a aucune reponse exploitable."""
    donnees = synthese_client(db, client)
    if not donnees["nb_reponses"]:
        return None
    mois = mois_courant()
    ligne = db.query(models.ScoreGEO).filter_by(client_id=client.id, mois=mois).first()
    if not ligne:
        ligne = models.ScoreGEO(client_id=client.id, mois=mois)
        db.add(ligne)
    ligne.score = donnees["score"]
    ligne.nb_questions = len(donnees["questions_gagnees"]) + len(donnees["questions_perdues"])
    ligne.nb_reponses = donnees["nb_reponses"]
    ligne.nb_cites = donnees["nb_cites"]
    ligne.position_moyenne = donnees["position_moyenne"]
    ligne.part_de_voix = donnees["part_de_voix"]
    ligne.details = json.dumps({
        "concurrents_top": donnees["concurrents_top"], "sources_top": donnees["sources_top"], "par_modele": donnees["par_modele"],
        "questions_gagnees": donnees["questions_gagnees"][:5],
    }, ensure_ascii=False)
    ligne.auto = auto or ligne.auto
    ligne.cree_le = datetime.utcnow()
    db.commit()
    return ligne


def historique(db, client_id: int, limite: int = 12) -> list:
    """Instantanes mensuels, du plus ancien au plus recent, avec l'evolution (points) par rapport au mois precedent."""
    lignes = (
        db.query(models.ScoreGEO).filter_by(client_id=client_id).order_by(models.ScoreGEO.mois.desc()).limit(limite).all()
    )[::-1]
    resultat = []
    precedent = None
    for ligne in lignes:
        resultat.append({
            "mois": ligne.mois, "score": ligne.score, "nb_cites": ligne.nb_cites, "nb_reponses": ligne.nb_reponses,
            "part_de_voix": ligne.part_de_voix, "position_moyenne": ligne.position_moyenne,
            "evolution": (ligne.score - precedent) if (precedent is not None and ligne.score is not None) else None,
        })
        precedent = ligne.score if ligne.score is not None else precedent
    return resultat


def modeles_actifs() -> list:
    """Assistants dont la cle API est configuree."""
    cles = {"chatgpt": ia_visibilite.CLE_OPENAI, "gemini": ia_visibilite.CLE_GEMINI, "perplexity": ia_visibilite.CLE_PERPLEXITY}
    return [m for m in ia_visibilite.MODELES_DISPONIBLES if cles.get(m)]


def verifier_et_enregistrer(db, client, requete_texte: str, modele: str):
    """Interroge un assistant pour une question, enregistre le releve. Retourne (ligne, dict du resultat)."""
    resultat = ia_visibilite.verifier_une_requete(client.nom, modele, requete_texte)
    ligne = models.ResultatVisibiliteIA(
        client_id=client.id, requete_texte=requete_texte, modele=modele,
        client_cite=resultat["client_cite"], position=resultat["position"],
        concurrents_cites=json.dumps(resultat["concurrents_cites"]), suggestion=resultat["suggestion"],
        reponse_brute=resultat["reponse_brute"], sources=json.dumps(resultat.get("sources", []), ensure_ascii=False),
        erreur=resultat["erreur"],
    )
    db.add(ligne)
    db.commit()
    return ligne, resultat


def relancer_suivi(db, client, auto: bool = True):
    """Verifie toutes les questions suivies (10 max) sur tous les assistants actifs puis enregistre l'instantane du mois."""
    requetes = db.query(models.RequeteVisibiliteIA).filter_by(client_id=client.id).order_by(models.RequeteVisibiliteIA.id).limit(MAX_QUESTIONS_SUIVI_AUTO).all()
    for requete in requetes:
        for modele in modeles_actifs():
            verifier_et_enregistrer(db, client, requete.texte, modele)
    return enregistrer_instantane(db, client, auto=auto)


SEUIL_TAUX_RECAP = 0.2


def donnees_recap(db, client_id: int, mois: int, annee: int):
    """
    Bloc GEO du recap mensuel envoye au client : uniquement de bonnes nouvelles. None (bloc absent) si le client n'a pas de
    releve ce mois-la, s'il n'a jamais ete cite, ou si sa presence est faible ET ne progresse pas.
    """
    ligne = db.query(models.ScoreGEO).filter_by(client_id=client_id, mois=f"{annee}-{mois:02d}").first()
    if not ligne or not ligne.nb_cites or not ligne.nb_reponses:
        return None
    mois_prec, annee_prec = (mois - 1, annee) if mois > 1 else (12, annee - 1)
    precedent = db.query(models.ScoreGEO).filter_by(client_id=client_id, mois=f"{annee_prec}-{mois_prec:02d}").first()
    evolution = None
    if precedent and precedent.score is not None and ligne.score is not None and ligne.score > precedent.score:
        evolution = ligne.score - precedent.score
    if ligne.nb_cites / ligne.nb_reponses < SEUIL_TAUX_RECAP and evolution is None:
        return None
    try:
        questions = json.loads(ligne.details or "{}").get("questions_gagnees", [])
    except ValueError:
        questions = []
    return {"nb_cites": ligne.nb_cites, "nb_reponses": ligne.nb_reponses, "score": ligne.score, "evolution": evolution, "questions": questions[:3]}


def donnees_recap_periode(db, client_id: int, debut, fin):
    """Comme donnees_recap, pour une periode libre (bilan PDF) : on prend le releve le plus recent des mois couverts."""
    ligne = (
        db.query(models.ScoreGEO)
        .filter(models.ScoreGEO.client_id == client_id, models.ScoreGEO.mois >= debut.strftime("%Y-%m"), models.ScoreGEO.mois <= fin.strftime("%Y-%m"))
        .order_by(models.ScoreGEO.mois.desc()).first()
    )
    if not ligne:
        return None
    annee, mois = (int(x) for x in ligne.mois.split("-"))
    return donnees_recap(db, client_id, mois, annee)
