"""
Performance des articles WordPress publies depuis la plateforme : ce que Google en dit (indexation, impressions, clics, position, requetes,
Search Console) et ce que les IA en font (le client est-il cite quand on pose la question visee par l'article, et l'article ou le site
est-il dans les sources ?), mesure au lancement puis environ 30 jours plus tard. Vue INTERNE : les chiffres y sont complets, y compris
les baisses (contrairement au recap envoye au client).
"""

import json
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from . import geo_score, google_oauth, ia_visibilite, models, search_console

DELAI_MESURE_APRES_JOURS = 30
FENETRE_AVANT_MAX_JOURS = 14          # une mesure « au lancement » reste possible tant que l'article a moins de 14 jours
AGE_MAX_SUIVI_AUTO_JOURS = 120        # on ne rattrape pas automatiquement les vieux articles
MAX_MESURES_IA_PAR_PASSAGE = 8
MAX_CREATIONS_PAR_PASSAGE = 25
TROP_TOT_JOURS = 4                    # Google a 2 a 3 jours de retard : avant ca, aucun chiffre n'a de sens
INDEXATION_ATTENDUE_JOURS = 14
PERIODE_MAX_JOURS = 90


# --------------------------------------------------------------------------- utilitaires

def _normaliser_url(url: str) -> str:
    """Adresse comparable : sans protocole, sans « www. », sans parametres ni barre finale."""
    analyse = urlparse((url or "").strip())
    hote = (analyse.netloc or "").lower()
    if hote.startswith("www."):
        hote = hote[4:]
    return f"{hote}{analyse.path.rstrip('/')}"


def _hote(url: str) -> str:
    return _normaliser_url(url).split("/", 1)[0]


def _json(valeur):
    try:
        return json.loads(valeur) if (valeur or "").strip() else None
    except ValueError:
        return None


def date_publication(post) -> date:
    return post.publier_le.date()


def age_jours(post, aujourdhui: date = None) -> int:
    return ((aujourdhui or date.today()) - date_publication(post)).days


def est_publie(post, maintenant: datetime = None) -> bool:
    """Un article programme n'est mesurable qu'une fois sa date passee."""
    return bool(post.lien) and post.publier_le <= (maintenant or datetime.now())


# --------------------------------------------------------------------------- IA

def analyser_ia(resultat: dict, client, lien_article: str) -> dict:
    """Resume d'un releve d'assistant : client cite (et a quelle place), site du client et article parmi les sources."""
    sources = resultat.get("sources") or []
    hote_site = _hote(client.wordpress_url or "")
    article = _normaliser_url(lien_article)
    urls = [_normaliser_url(s.get("url") or "") for s in sources]
    return {
        "client_cite": bool(resultat.get("client_cite")),
        "position": resultat.get("position"),
        "site_en_source": bool(hote_site) and any(u.split("/", 1)[0] == hote_site for u in urls),
        "article_en_source": bool(article) and any(u == article for u in urls),
        "nb_sources": len(sources),
        "erreur": resultat.get("erreur") or "",
    }


def mesurer_ia(db, client, post, question: str) -> dict:
    """Pose la question visee a chaque assistant actif (les releves sont aussi gardes dans l'historique GEO) ; renvoie le resume horodate."""
    modeles = []
    for modele in geo_score.modeles_actifs():
        ligne, resultat = geo_score.verifier_et_enregistrer(db, client, question, modele)
        resume = analyser_ia({**resultat, "sources": json.loads(ligne.sources or "[]")}, client, post.lien)
        resume["modele"] = modele
        resume["libelle"] = ia_visibilite.MODELES_DISPONIBLES.get(modele, modele).split(" (")[0]
        modeles.append(resume)
    return {"date": date.today().isoformat(), "question": question, "modeles": modeles}


def resume_ia(avant: dict, apres: dict) -> str:
    """Phrase de lecture : ce qui a change entre la mesure au lancement et la derniere mesure."""
    derniere = apres or avant
    if not derniere or not derniere.get("modeles"):
        return "Aucun test IA pour l'instant : enregistrez la question visée puis testez-la."
    valides = [m for m in derniere["modeles"] if not m.get("erreur")]
    if not valides:
        return "Le test IA a échoué (assistants indisponibles) : réessayez plus tard."
    if any(m["article_en_source"] for m in valides):
        return "✅ L'article lui-même est utilisé comme source par au moins un assistant."
    citees = [m for m in valides if m["client_cite"]]
    if apres and avant:
        avant_cite = any(m["client_cite"] for m in avant.get("modeles", []) if not m.get("erreur"))
        if citees and not avant_cite:
            return "📈 Le client est cité par les IA alors qu'il ne l'était pas au lancement."
        if not citees and avant_cite:
            return "📉 Le client n'est plus cité par les IA sur cette question."
    if citees:
        return "Le client est cité par les IA sur cette question."
    if any(m["site_en_source"] for m in valides):
        return "Le site du client apparaît dans les sources des IA, mais le client n'est pas cité dans la réponse."
    return "Pas encore cité par les IA sur cette question : c'est normal les premières semaines."


# --------------------------------------------------------------------------- Google

def mesures_google(db, post, client, aujourdhui: date = None) -> dict:
    """Chiffres Search Console de l'article depuis sa publication (90 jours au plus) + indexation + evolution du site autour de la publication."""
    aujourdhui = aujourdhui or date.today()
    if not client.search_console_site:
        return {"etat": "non_rattache", "message": "La Search Console n'est pas rattachée à ce client (fiche client, bloc « Search Console »)."}
    identifiants = google_oauth.obtenir_identifiants_search_console(db)
    if not identifiants:
        return {"etat": "non_connecte", "message": "La Search Console n'est pas connectée (Comptes Google)."}
    age = age_jours(post, aujourdhui)
    if age < TROP_TOT_JOURS:
        return {"etat": "trop_tot", "age": age, "message": "Trop tôt pour juger : Google publie ses chiffres avec 2 à 3 jours de retard."}

    site, lien = client.search_console_site, post.lien
    fin = aujourdhui - timedelta(days=search_console.RETARD_DONNEES_JOURS)
    debut = max(date_publication(post), fin - timedelta(days=PERIODE_MAX_JOURS - 1))
    resultat = {"etat": "ok", "age": age, "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()}}
    try:
        resultat["page"] = search_console.performance_page(identifiants, site, lien, debut, fin)
    except Exception as erreur:
        return {"etat": "erreur", "message": str(erreur)}
    try:
        resultat["inspection"] = search_console.inspecter_url(identifiants, site, lien)
    except Exception as erreur:
        resultat["inspection"] = {"inconnue": True, "message": str(erreur)}
    # Le site gagne-t-il des clics apres la publication ? (28 jours apres contre 28 jours avant, une fois assez de recul)
    pub = date_publication(post)
    if pub + timedelta(days=28 + search_console.RETARD_DONNEES_JOURS) <= aujourdhui:
        try:
            apres = search_console._requete(identifiants, site, pub, pub + timedelta(days=27))
            avant = search_console._requete(identifiants, site, pub - timedelta(days=28), pub - timedelta(days=1))
            resultat["site"] = {"clics_apres": int(apres[0]["clicks"]) if apres else 0, "clics_avant": int(avant[0]["clicks"]) if avant else 0}
        except Exception:
            pass
    resultat["verdict"] = verdict_google(resultat)
    return resultat


def verdict_google(mesures: dict) -> str:
    if mesures.get("etat") != "ok":
        return mesures.get("message", "")
    age, page, inspection = mesures["age"], mesures.get("page") or {}, mesures.get("inspection") or {}
    indexee = inspection.get("indexee")
    if indexee is False and age >= INDEXATION_ATTENDUE_JOURS:
        return "⚠ Google n'a pas indexé cet article après " + str(age) + " jours : demandez l'indexation dans la Search Console (Inspection d'URL), et vérifiez qu'il est lié depuis le site."
    if page.get("impressions", 0) == 0:
        return "Pas encore visible sur Google (aucune impression)." + (" L'article est indexé : il faut souvent 3 à 6 semaines pour apparaître." if indexee else "")
    pos = page.get("position")
    base = f"Visible sur Google : {page['impressions']} impressions, {page['clics']} clic(s)" + (f", position moyenne {pos:.1f}" if pos else "") + "."
    if pos and pos <= 10 and page["clics"] == 0:
        return base + " Il apparaît en première page mais n'est pas cliqué : retravaillez le titre et la description."
    if pos and pos > 20:
        return base + " Position encore lointaine : renforcez l'article (liens internes, contenu plus précis sur la ville)."
    return base


# --------------------------------------------------------------------------- suivi automatique

def creer_suivi(db, post, question: str = "", mot_cle: str = ""):
    suivi = db.query(models.SuiviArticle).filter_by(post_id=post.id).first()
    if not suivi:
        suivi = models.SuiviArticle(post_id=post.id, client_id=post.client_id)
        db.add(suivi)
    if question:
        suivi.question = question.strip()[:300]
    if mot_cle:
        suivi.mot_cle = mot_cle.strip()[:120]
    db.commit()
    return suivi


def suivi_quotidien(session_factory, proposer, maintenant: datetime = None, mesurer=None) -> dict:
    """
    Passage automatique (une fois par jour) : cree le suivi des articles publies depuis moins de 120 jours (question visee proposee par
    l'IA), fait la mesure IA « au lancement » (article de moins de 14 jours) puis la mesure « apres » (30 jours et plus).
    proposer(post, client) -> {"question", "mot_cle"} ; mesurer(db, client, post, question) -> dict (par defaut : mesurer_ia).
    """
    mesurer = mesurer or mesurer_ia
    maintenant = maintenant or datetime.now()
    bilan = {"crees": 0, "avant": 0, "apres": 0}
    if not geo_score.modeles_actifs():
        return bilan
    db = session_factory()
    try:
        recents = (db.query(models.PostWordPress).filter(models.PostWordPress.publier_le <= maintenant, models.PostWordPress.lien != "")
                   .order_by(models.PostWordPress.publier_le.desc()).limit(400).all())
        mesures_faites = 0
        for post in recents:
            if not est_publie(post, maintenant):
                continue
            age = (maintenant.date() - date_publication(post)).days
            if age > AGE_MAX_SUIVI_AUTO_JOURS:
                continue
            client = db.get(models.Client, post.client_id)
            if not client:
                continue
            suivi = db.query(models.SuiviArticle).filter_by(post_id=post.id).first()
            if not suivi:
                if bilan["crees"] >= MAX_CREATIONS_PAR_PASSAGE:
                    continue
                try:
                    proposition = proposer(post, client) or {}
                except Exception:
                    proposition = {}
                suivi = creer_suivi(db, post, proposition.get("question") or post.titre, proposition.get("mot_cle") or "")
                bilan["crees"] += 1
            if not suivi.question or mesures_faites >= MAX_MESURES_IA_PAR_PASSAGE:
                continue
            phase = None
            if not suivi.mesure_avant_json and age <= FENETRE_AVANT_MAX_JOURS:
                phase = "avant"
            elif not suivi.mesure_apres_json and age >= DELAI_MESURE_APRES_JOURS:
                phase = "apres"
            if phase:
                try:
                    mesure = mesurer(db, client, post, suivi.question)
                except Exception:
                    db.rollback()
                    continue
                setattr(suivi, f"mesure_{phase}_json", json.dumps(mesure, ensure_ascii=False))
                db.commit()
                bilan[phase] += 1
                mesures_faites += 1
    finally:
        db.close()
    return bilan
