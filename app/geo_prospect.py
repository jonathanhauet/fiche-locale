"""
Test GEO d'un prospect (entreprise non cliente) : 5 questions locales posees aux assistants IA, controle technique du site,
puis un rapport interne avec les 3 actions prioritaires. Rien n'est enregistre : c'est un outil de rendez-vous commercial.
"""

import json
from types import SimpleNamespace

from . import geo_score

LIBELLES_ACTION_SOURCE = {
    "annuaire": "Compléter ou créer la fiche sur {domaine} (mêmes nom, adresse et téléphone partout) : les IA s'appuient sur ce site.",
    "avis": "Obtenir des avis récents sur {domaine} : les IA s'en servent pour recommander.",
    "reseau_social": "Publier régulièrement sur {domaine} avec le nom de l'entreprise et la ville : les IA le consultent.",
    "autre": "Obtenir une mention de l'entreprise sur {domaine} (article, partenariat, annuaire local) : les IA le citent.",
}


def resultats_depuis_dicts(resultats: list) -> list:
    """Dictionnaires venus du navigateur -> objets compatibles avec geo_score.synthese (memes attributs que ResultatVisibiliteIA)."""
    objets = []
    for r in resultats or []:
        if not isinstance(r, dict):
            continue
        objets.append(SimpleNamespace(
            requete_texte=str(r.get("requete_texte", ""))[:300], modele=str(r.get("modele", ""))[:30], erreur=str(r.get("erreur") or ""),
            client_cite=bool(r.get("client_cite")), position=r.get("position") if isinstance(r.get("position"), int) else None,
            concurrents_cites=json.dumps([str(c)[:120] for c in (r.get("concurrents_cites") or [])][:15], ensure_ascii=False),
            sources=json.dumps([
                {"domaine": str(s.get("domaine", ""))[:120], "url": str(s.get("url", ""))[:500]}
                for s in (r.get("sources") or []) if isinstance(s, dict)
            ][:20], ensure_ascii=False),
        ))
    return objets


def _sans_renvoi(texte: str) -> str:
    """Les correctifs du controle technique renvoient aux generateurs « ci-dessous » d'une fiche cliente : absents de ce rapport."""
    return texte.replace(" ci-dessous", " (generateurs disponibles une fois l'entreprise cliente)").replace("(generateurs", "(générateurs")


def actions_prioritaires(nom: str, synthese: dict, controle: dict = None) -> list:
    """Jusqu'a 3 actions concretes, par ordre d'impact : technique bloquant, contenu, sources a travailler."""
    actions = []
    if controle:
        a_traiter = [c for c in controle["controles"] if c["statut"] == "probleme"] or [c for c in controle["controles"] if c["statut"] == "avertissement"]
        if a_traiter:
            premier = a_traiter[0]
            actions.append(f"Corriger le site : {premier['titre'].lower()}. {_sans_renvoi(premier['correctif'] or premier['detail'])}")
    perdues = synthese.get("questions_perdues") or []
    total_questions = len(perdues) + len(synthese.get("questions_gagnees") or [])
    if perdues:
        actions.append(
            f"Publier des contenus (posts, FAQ, page du site) qui répondent aux questions où {nom} n'est pas cité "
            f"({len(perdues)} sur {total_questions}), par exemple « {perdues[0]['texte']} »."
        )
    for source in synthese.get("sources_top") or []:
        if source["categorie"] != "votre_site":
            actions.append(LIBELLES_ACTION_SOURCE[source["categorie"]].format(domaine=source["domaine"]))
            break
    if controle and len(actions) < 3:
        suivants = [c for c in controle["controles"] if c["statut"] != "ok"][1:]
        for c in suivants[: 3 - len(actions)]:
            actions.append(f"Corriger le site : {c['titre'].lower()}. {_sans_renvoi(c['correctif'] or c['detail'])}")
    return actions[:3]


def _questions_detaillees(objets: list) -> list:
    """Une ligne par question (gagnee ou perdue) pour le tableau du rapport."""
    par_question = {}
    for r in objets:
        if r.erreur:
            continue
        info = par_question.setdefault(r.requete_texte, {"texte": r.requete_texte, "cite_sur": [], "concurrents": []})
        if r.client_cite:
            info["cite_sur"].append(r.modele)
        for concurrent in json.loads(r.concurrents_cites):
            if concurrent not in info["concurrents"]:
                info["concurrents"].append(concurrent)
    for info in par_question.values():
        info["concurrents"] = info["concurrents"][:4]
    return list(par_question.values())


def construire_rapport(nom: str, resultats: list, site: str = "", controle: dict = None) -> dict:
    objets = resultats_depuis_dicts(resultats)
    synthese = geo_score.synthese(objets, site)
    return {
        "synthese": synthese, "controle": controle, "actions": actions_prioritaires(nom, synthese, controle),
        "questions": _questions_detaillees(objets),
    }
