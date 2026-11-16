"""
Audit complet d'une fiche Google d'entreprise (prospect) : score global et par theme, recommandations classees par priorite, visibilite dans les IA.

Tout vient de donnees publiques (aucun acces a la fiche du prospect) : la fiche Google Maps, une grille de positions autour de l'adresse, le site web
(technique, vitesse, referencement, lisibilite par les assistants IA), les annuaires (presence et coherence nom / adresse / telephone) et les reponses de
ChatGPT et Gemini a la question qu'un client leur poserait. Ce que l'on ne peut pas voir sans acces a la fiche (publications, reponses aux avis,
description, attributs, statistiques) est liste a part, jamais invente.

evaluer() est une fonction pure (donnees collectees -> score, themes, controles, priorites) : c'est elle qui est testee ; collecter() et executer() font
les appels externes, etape par etape, une etape en echec n'empechant jamais les autres (elle est simplement exclue du score).
"""

import json
import math
import re
import secrets
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from statistics import median
from urllib.parse import urlparse

from . import models

# ---------------------------------------------------------------------------------------------------------------- themes

THEMES = [
    ("fiche", "Fiche Google", 25, "📋"), ("photos", "Photos", 8, "📷"), ("avis", "Avis et réputation", 15, "⭐"), ("visibilite", "Visibilité locale", 17, "📍"),
    ("site", "Site web", 12, "🌐"), ("annuaires", "Annuaires et cohérence", 8, "🗂️"), ("ia", "Visibilité dans les IA", 15, "🤖"),
]
POIDS_THEME = {code: poids for code, _, poids, _ in THEMES}
NOMS_THEME = {code: nom for code, nom, _, _ in THEMES}
ICONES_THEME = {code: icone for code, _, _, icone in THEMES}

ETIQUETTES_SCORE = [(85, "Excellente", "#16a34a"), (70, "Bonne", "#65a30d"), (50, "À améliorer", "#d98a06"), (30, "Insuffisante", "#ea580c"), (0, "Critique", "#dc2626")]
IMPACTS = {3: "Élevé", 2: "Moyen", 1: "Faible"}
EFFORTS = {1: "Faible", 2: "Moyen", 3: "Élevé"}

NON_VERIFIABLE = [
    ("Publications Google", "Fréquence et contenu des publications de la fiche : seul le propriétaire y a accès."),
    ("Réponses aux avis", "Part des avis auxquels le propriétaire répond, et délai de réponse."),
    ("Description et attributs", "Description de l'entreprise, services, produits et attributs (accessibilité, paiements…) : non exposés publiquement."),
    ("Questions / réponses et messagerie", "Questions posées sur la fiche et messages reçus."),
    ("Statistiques de la fiche", "Vues, appels, demandes d'itinéraire et clics vers le site."),
    ("Cohérence interne de la fiche", "Zone de service, liens de réservation, liste des produits : à vérifier avec le propriétaire."),
]


def etiquette_score(score) -> tuple:
    """(libelle, couleur) d'un score 0-100."""
    for seuil, libelle, couleur in ETIQUETTES_SCORE:
        if score is not None and score >= seuil:
            return libelle, couleur
    return "Non évalué", "#6b7280"


# ---------------------------------------------------------------------------------------------------------------- activite

PROFILS = [
    ("restauration", ("restaurant", "pizz", "brasserie", "café", "cafe", "bar ", "boulang", "patiss", "traiteur", "snack", "crêperie", "creperie", "glacier", "fast")),
    ("batiment", ("plomb", "électric", "electric", "couvreur", "toiture", "maçon", "macon", "peintre", "menuis", "chauffag", "carrel", "serrur", "entreprise de", "rénovation", "renovation", "paysag", "façad", "facad", "isolation", "terrass", "vitrier", "dépann", "depann")),
    ("sante", ("médecin", "medecin", "dentiste", "kiné", "kine", "ostéo", "osteo", "pharmac", "infirm", "psycholog", "orthodon", "vétérin", "veterin", "optic", "clinique", "cabinet", "docteur", "sage-femme", "podolog")),
    ("beaute", ("coiff", "esthéti", "esthet", "beauté", "beaute", "barbier", "manucure", "ongle", "massage", "spa", "tatou", "institut", "maquill")),
    ("auto", ("garage", "automobile", "carrosserie", "pneu", "contrôle technique", "controle technique", "concession", "moto", "lavage")),
    ("services_pro", ("avocat", "notaire", "comptab", "assurance", "immobili", "banque", "conseil", "agence", "architecte", "huissier", "expert", "formation", "courtier")),
    ("commerce", ("magasin", "boutique", "vendeur", "marchand", "librairie", "fleuriste", "bijou", "or ", "rachat", "vêtement", "vetement", "supermarché", "épicerie", "epicerie", "antiqu", "quincaill", "jardinerie", "cave", "optique")),
]
PROFILS_RDV = {"sante", "beaute", "services_pro", "batiment", "auto"}          # activites ou un lien de prise de rendez-vous / devis est attendu

CONSEILS_ACTIVITE = {
    "restauration": [
        ("Menu et plats en photos", "Ajoutez le menu sur la fiche et des photos récentes des plats : ce sont les images les plus consultées pour un restaurant."),
        ("Liens de réservation et de commande", "Reliez la réservation en ligne ou la commande à emporter à la fiche : l'internaute passe à l'action sans quitter Google."),
        ("Horaires exceptionnels", "Renseignez les jours fériés et les fermetures : un horaire faux est la première cause d'avis négatifs pour un restaurant."),
    ],
    "batiment": [
        ("Réalisations avant / après", "Publiez régulièrement des photos de chantiers terminés, avec la ville : elles rassurent et nourrissent les recherches locales."),
        ("Zone d'intervention précise", "Indiquez les communes desservies sur la fiche et sur le site, une page par ville principale si possible."),
        ("Devis en ligne et rappel rapide", "Un bouton « demander un devis » relié à la fiche transforme les consultations en contacts, surtout en urgence (dépannage)."),
    ],
    "sante": [
        ("Prise de rendez-vous en ligne", "Reliez un lien de prise de rendez-vous : c'est l'action n°1 attendue sur la fiche d'un praticien."),
        ("Informations pratiques", "Accès, accessibilité, moyens de paiement et langues parlées rassurent avant la première consultation."),
        ("Avis avec réponse mesurée", "Répondez aux avis avec sobriété, sans jamais citer de détail de soin, pour rester dans le cadre déontologique."),
    ],
    "beaute": [
        ("Réservation en ligne", "Reliez la prise de rendez-vous à la fiche : c'est ce qui convertit le plus pour un salon ou un institut."),
        ("Photos de réalisations", "Avant / après, coiffures, soins : des photos fraîches et régulières font la différence face aux concurrents proches."),
        ("Liste de prestations et tarifs", "Détaillez les prestations (et un prix « à partir de ») : l'internaute compare avant d'appeler."),
    ],
    "auto": [
        ("Services et marques traitées", "Listez les prestations (révision, pneus, climatisation…) et les marques : ce sont des recherches très fréquentes."),
        ("Prise de rendez-vous atelier", "Un lien de prise de rendez-vous évite l'appel manqué quand l'atelier est occupé."),
        ("Photos de l'atelier et de l'équipe", "Elles rassurent sur le sérieux de l'établissement."),
    ],
    "services_pro": [
        ("Spécialités et zone couverte", "Précisez les domaines d'intervention et la zone sur la fiche et sur le site : on cherche un spécialiste, pas un généraliste."),
        ("Prise de rendez-vous ou formulaire", "Un lien de contact clair raccourcit le chemin entre la fiche et le premier échange."),
        ("Preuves de confiance", "Avis, ancienneté, certifications : à mettre en avant dans la description et sur le site."),
    ],
    "commerce": [
        ("Produits et services en photos", "Ajoutez des photos de la vitrine, de l'intérieur et des produits phares : c'est ce que regardent les passants avant de venir."),
        ("Horaires à jour, jours fériés compris", "Un magasin trouvé fermé alors qu'il est ouvert (ou l'inverse) génère des avis négatifs."),
        ("Publications régulières", "Nouveautés, promotions, événements : une publication par semaine entretient la fiche."),
    ],
    "generique": [
        ("Photos récentes et variées", "Façade, intérieur, équipe, réalisations : Google valorise les fiches vivantes."),
        ("Publications régulières", "Une publication par semaine (actualité, offre, réalisation) montre que l'entreprise est active."),
        ("Réponse à tous les avis", "Répondre aux avis, positifs comme négatifs, améliore l'image et la confiance."),
    ],
}


def profil_activite(categorie: str) -> str:
    """Famille d'activite d'apres la categorie Google (ex. « Plombier » -> batiment) : sert a adapter les conseils."""
    c = (categorie or "").lower()
    for code, mots in PROFILS:
        if any(m in c for m in mots):
            return code
    return "generique"


# ---------------------------------------------------------------------------------------------------------------- recommandations
# check -> (titre, pourquoi, comment, impact 1-3, effort 1-3). Le gain en points est calcule d'apres l'ecart au maximum du controle.

RECOS = {
    "revendiquee": ("Revendiquer la fiche Google", "Une fiche non revendiquée ne peut être ni mise à jour ni protégée : n'importe qui peut proposer des modifications.",
                    "Le propriétaire clique sur « Gérer cette fiche » sur Google Maps et suit la vérification (courrier, appel ou vidéo).", 3, 1),
    "categorie": ("Choisir une catégorie principale", "La catégorie principale est le premier critère de classement local.", "Dans la fiche, choisir la catégorie qui décrit le mieux l'activité principale.", 3, 1),
    "categories_secondaires": ("Ajouter des catégories secondaires", "Chaque catégorie supplémentaire ouvre un jeu de recherches où la fiche peut apparaître.",
                               "Ajouter 2 à 5 catégories secondaires pertinentes (activités annexes), sans en inventer.", 2, 1),
    "horaires": ("Renseigner les horaires d'ouverture", "Sans horaires, Google n'affiche pas « Ouvert maintenant » : on perd les recherches faites au moment de se déplacer.",
                 "Saisir les horaires de chaque jour, puis les horaires exceptionnels (jours fériés, vacances).", 3, 1),
    "telephone": ("Ajouter un numéro de téléphone", "Le bouton « Appeler » est l'action la plus utilisée depuis un mobile.", "Renseigner le numéro principal, idéalement un numéro direct.", 3, 1),
    "site_web": ("Relier le site web à la fiche", "Le lien vers le site est un signal de confiance et une source de visites.", "Ajouter l'adresse du site dans la fiche (page d'accueil ou page locale).", 2, 1),
    "adresse": ("Compléter l'adresse", "L'adresse sert de point de départ au classement local.", "Renseigner l'adresse complète, ou la zone de service si l'entreprise se déplace.", 3, 1),
    "lien_rdv": ("Ajouter un lien de rendez-vous ou de devis", "Pour cette activité, l'internaute veut agir tout de suite : un lien direct raccourcit le chemin vers le contact.",
                 "Ajouter un lien de réservation ou de demande de devis dans la fiche (section « Liens de réservation »).", 2, 1),
    "photo_principale": ("Choisir une photo principale", "La photo de couverture est la première image vue dans les résultats.", "Importer une photo de qualité de la façade ou de l'activité, puis la définir comme couverture.", 2, 1),
    "nombre_photos": ("Ajouter davantage de photos", "Les fiches riches en photos reçoivent plus de visites et d'appels, et inspirent confiance.",
                      "Viser au moins 30 photos : façade, intérieur, équipe, réalisations, produits. En ajouter 2 ou 3 par semaine.", 2, 1),
    "note": ("Améliorer la note moyenne", "La note est l'un des premiers critères de choix, et elle pèse sur le classement.",
             "Traiter les causes des avis négatifs récurrents et répondre à chacun, puis demander un avis aux clients satisfaits.", 3, 3),
    "volume_avis": ("Obtenir plus d'avis", "Le nombre d'avis rassure et pèse sur le classement : on le compare directement à celui des concurrents.",
                    "Demander un avis après chaque prestation avec un lien court ou un QR code, et en faire un geste quotidien.", 3, 2),
    "mauvaises_notes": ("Traiter les avis négatifs", "Une part élevée d'avis à 1 ou 2 étoiles freine les clients, surtout s'ils restent sans réponse.",
                        "Répondre poliment à chaque avis négatif, proposer une solution hors ligne, et équilibrer en sollicitant les clients satisfaits.", 2, 2),
    "site_present": ("Créer un site web", "Sans site, la fiche perd en confiance, en pages qui peuvent se positionner, et en visibilité dans les IA.",
                     "Créer un site simple : page d'accueil, services, zone d'intervention, contact, avis.", 3, 3),
    "https": ("Passer le site en HTTPS", "Les navigateurs signalent les sites non sécurisés comme dangereux, ce qui fait fuir les visiteurs.", "Activer le certificat SSL chez l'hébergeur et rediriger tout le site vers https.", 3, 1),
    "vitesse": ("Accélérer le site sur mobile", "La majorité des recherches locales se font sur mobile : un site lent perd des visiteurs avant même l'affichage.",
                "Compresser les images, réduire les extensions et activer le cache ; tester avec PageSpeed Insights.", 2, 2),
    "balises": ("Corriger titre, description et titre H1", "Ce sont les éléments que Google et les assistants IA lisent en premier pour comprendre la page.",
                "Un titre de 10 à 70 caractères avec l'activité et la ville, une description de 50 à 160 caractères, un seul titre H1.", 2, 1),
    "texte_visible": ("Rendre le texte lisible sans JavaScript", "Les robots (Google, ChatGPT…) lisent surtout le texte déjà présent dans la page : un site tout en JavaScript leur paraît vide.",
                      "Faire afficher le contenu côté serveur (ou utiliser un site classique) pour que les services, la zone et le contact soient lisibles directement.", 2, 3),
    "nap_site": ("Afficher nom, adresse et téléphone sur le site", "Les coordonnées de la page doivent correspondre exactement à celles de la fiche : c'est un signal de cohérence majeur.",
                 "Ajouter (ou corriger) l'adresse et le téléphone en haut ou en bas de chaque page, identiques à la fiche Google.", 2, 1),
    "reseaux": ("Relier les réseaux sociaux au site", "Des réseaux actifs, reliés au site, renforcent la confiance et créent des signaux cohérents pour les IA.", "Ajouter les liens Facebook / Instagram / LinkedIn du site et publier régulièrement.", 1, 1),
    "sitemap": ("Publier un plan du site", "Le sitemap aide Google et les IA à découvrir toutes les pages utiles.", "Activer le sitemap (extension SEO) et le déclarer dans robots.txt.", 1, 1),
    "autorite": ("Gagner des liens depuis d'autres sites", "Les liens entrants (annuaires, presse locale, partenaires) font monter l'autorité du site, donc de la fiche.",
                 "Obtenir des liens depuis les annuaires de qualité, la CCI, les partenaires et la presse locale.", 1, 3),
    "annuaire_pages_jaunes": ("Créer la fiche Pages Jaunes", "Les assistants IA et les moteurs s'appuient beaucoup sur les annuaires pour confirmer une entreprise.", "Créer ou revendiquer la fiche avec exactement le même nom, adresse et téléphone que Google.", 2, 1),
    "annuaire_bing_places": ("Créer la fiche Bing Places", "ChatGPT s'appuie en partie sur Bing : sans fiche Bing, l'entreprise est plus rarement citée.", "Importer la fiche Google dans Bing Places (gratuit, quelques minutes).", 2, 1),
    "annuaire_apple_plans": ("Créer la fiche Apple Plans", "Apple Plans est l'application de cartes par défaut sur iPhone.", "Créer la fiche sur Apple Business Connect (gratuit).", 2, 1),
    "annuaire_yelp": ("Créer la fiche Yelp", "Yelp reste une source de confirmation citée par certaines IA.", "Créer ou revendiquer la fiche sur Yelp avec les mêmes coordonnées.", 1, 1),
    "coherence_nap": ("Harmoniser nom, adresse et téléphone partout", "Des coordonnées différentes d'un annuaire à l'autre brouillent Google et les IA.", "Corriger chaque annuaire pour qu'il reprenne exactement les coordonnées de la fiche Google.", 3, 2),
    "cite_chatgpt": ("Se faire recommander par ChatGPT", "De plus en plus de clients demandent directement à ChatGPT. L'entreprise n'est pas citée quand on lui demande ce service dans sa ville.",
                     "Renforcer les sources que l'IA consulte : fiche Bing, annuaires, avis, site clair avec la ville et les services (voir les sites cités ci-dessous).", 3, 2),
    "cite_gemini": ("Se faire recommander par Gemini", "Gemini s'appuie sur Google : fiche complète, avis nombreux et site lisible sont décisifs.", "Compléter la fiche, obtenir des avis et publier des pages de services précises avec la ville.", 3, 2),
    "robots_ia": ("Autoriser les robots des assistants IA", "Un robots.txt qui bloque OAI-SearchBot, PerplexityBot ou Googlebot empêche les IA de lire le site.", "Retirer les blocages correspondants dans robots.txt (et vérifier le pare-feu de l'hébergeur).", 3, 1),
    "donnees_structurees": ("Ajouter les données structurées LocalBusiness", "Elles donnent aux IA et à Google l'adresse, les horaires et les services sans avoir à les deviner.", "Ajouter le bloc JSON-LD LocalBusiness à la page d'accueil (la plateforme sait le générer).", 2, 2),
    "llms_txt": ("Publier un fichier llms.txt", "Ce petit fichier résume l'activité et les pages importantes pour les assistants IA.", "Déposer un fichier llms.txt à la racine du site (la plateforme sait le générer).", 1, 1),
}


# ---------------------------------------------------------------------------------------------------------------- evaluation (pure)

def _controle(code, theme, poids, libelle, fraction, detail, statut=None, valeur=None):
    """Un controle. fraction : 0..1, ou None si non evaluable (donnee absente : exclu du score)."""
    if fraction is None:
        statut = "inconnu"
    elif statut is None:
        statut = "ok" if fraction >= 0.85 else "attention" if fraction >= 0.4 else "probleme"
    return {"code": code, "theme": theme, "poids": poids, "libelle": libelle, "fraction": None if fraction is None else round(fraction, 3), "statut": statut, "detail": detail, "valeur": valeur}


def _paliers(valeur, paliers, defaut=0.0):
    """paliers : [(seuil, fraction)] du plus haut au plus bas ; renvoie la fraction du premier seuil atteint."""
    for seuil, fraction in paliers:
        if valeur >= seuil:
            return fraction
    return defaut


def _controles_fiche(d: dict) -> list:
    f = d.get("fiche") or {}
    profil = profil_activite(f.get("categorie", ""))
    cs = []
    if f.get("est_revendiquee") is None:
        cs.append(_controle("revendiquee", "fiche", 4, "Fiche revendiquée par son propriétaire", None, "Information non disponible."))
    else:
        cs.append(_controle("revendiquee", "fiche", 4, "Fiche revendiquée par son propriétaire", 1.0 if f["est_revendiquee"] else 0.0,
                            "La fiche est revendiquée." if f["est_revendiquee"] else "La fiche n'est pas revendiquée : personne ne la gère officiellement."))
    cs.append(_controle("categorie", "fiche", 2, "Catégorie principale", 1.0 if f.get("categorie") else 0.0, f.get("categorie") or "Aucune catégorie."))
    n = len(f.get("categories_secondaires") or [])
    cs.append(_controle("categories_secondaires", "fiche", 3, "Catégories secondaires", _paliers(n, [(3, 1.0), (2, 0.8), (1, 0.5)]),
                        f"{n} catégorie(s) secondaire(s)" + (" : " + ", ".join((f.get("categories_secondaires") or [])[:4]) if n else ".")))
    jours = f.get("jours_ouverts")
    if not f.get("a_horaires"):
        cs.append(_controle("horaires", "fiche", 3, "Horaires d'ouverture", 0.0, "Aucun horaire renseigné."))
    else:
        cs.append(_controle("horaires", "fiche", 3, "Horaires d'ouverture", 1.0 if (jours is None or jours >= 5) else 0.8, "Horaires renseignés" + (f" ({jours} jour(s) d'ouverture)." if jours is not None else ".")))
    cs.append(_controle("telephone", "fiche", 2, "Téléphone", 1.0 if f.get("telephone") else 0.0, f.get("telephone") or "Aucun numéro."))
    cs.append(_controle("site_web", "fiche", 3, "Site web relié", 1.0 if f.get("site_web") else 0.0, f.get("site_web") or "Aucun site relié à la fiche."))
    cs.append(_controle("adresse", "fiche", 1, "Adresse", 1.0 if f.get("adresse") else 0.0, f.get("adresse") or "Aucune adresse affichée (zone de service ?)."))
    if profil in PROFILS_RDV:
        lien = f.get("lien_rdv") or f.get("lien_contact")
        cs.append(_controle("lien_rdv", "fiche", 2, "Lien de rendez-vous ou de contact", 1.0 if f.get("lien_rdv") else 0.6 if lien else 0.0,
                            f.get("lien_rdv") or (f"Lien de contact : {lien}" if lien else "Aucun lien de réservation ni de contact sur la fiche."), valeur=lien))
    cs.append(_controle("photo_principale", "fiche", 1, "Photo principale", 1.0 if f.get("photo_principale") else 0.0, "Photo de couverture présente." if f.get("photo_principale") else "Pas de photo principale."))
    return cs


def _controles_photos(d: dict) -> list:
    f = d.get("fiche") or {}
    n = f.get("total_photos")
    if n is None:
        return [_controle("nombre_photos", "photos", 6, "Nombre de photos", None, "Information non disponible.")]
    return [_controle("nombre_photos", "photos", 6, "Nombre de photos", _paliers(n, [(30, 1.0), (20, 0.85), (10, 0.55), (5, 0.3), (1, 0.12)]),
                      f"{n} photo(s) sur la fiche (objectif : 30 ou plus).")]


def _avis_concurrents(d: dict) -> list:
    nombres = []
    for releve in (d.get("positions") or {}).get("releves") or []:
        nombres += [c["nombre_avis"] for c in releve.get("concurrents", []) if c.get("nombre_avis")]
    return nombres


def _controles_avis(d: dict) -> list:
    f = d.get("fiche") or {}
    note, n = f.get("note"), f.get("nombre_avis") or 0
    cs = []
    if note is None or not n:
        cs.append(_controle("note", "avis", 6, "Note moyenne", 0.0, "Aucun avis pour l'instant.", valeur=None))
        cs.append(_controle("volume_avis", "avis", 5, "Nombre d'avis", 0.0, "Aucun avis : la fiche n'inspire pas confiance."))
        return cs
    cs.append(_controle("note", "avis", 6, "Note moyenne", _paliers(note, [(4.7, 1.0), (4.5, 0.9), (4.2, 0.75), (4.0, 0.6), (3.5, 0.35)], 0.1), f"{str(note).replace('.', ',')} / 5 sur {n} avis."))
    paliers = _paliers(n, [(150, 1.0), (80, 0.9), (40, 0.75), (20, 0.55), (10, 0.35), (1, 0.15)])
    concurrents = _avis_concurrents(d)
    detail = f"{n} avis."
    fraction = paliers
    if len(concurrents) >= 3:
        med = median(concurrents)
        fraction = max(paliers, min(1.0, n / med)) if med else paliers
        detail += f" Les concurrents les mieux placés en ont {int(med)} en médiane" + (f" : il en manque environ {int(med - n)} pour les rattraper." if n < med else " : la fiche est au niveau.")
    cs.append(_controle("volume_avis", "avis", 5, "Nombre d'avis", fraction, detail))
    repartition = f.get("repartition_avis") or {}
    total = sum(int(v or 0) for v in repartition.values())
    if total >= 10:
        mauvais = (int(repartition.get("1") or 0) + int(repartition.get("2") or 0)) / total
        cs.append(_controle("mauvaises_notes", "avis", 2, "Part d'avis négatifs (1 et 2 étoiles)", _paliers(-mauvais, [(-0.05, 1.0), (-0.10, 0.7), (-0.20, 0.4)], 0.1), f"{round(mauvais * 100)} % d'avis à 1 ou 2 étoiles."))
    return cs


def _controles_visibilite(d: dict) -> list:
    p = d.get("positions") or {}
    releves = p.get("releves") or []
    if not releves:
        return [_controle("visibilite", "visibilite", 8, "Visibilité sur la recherche locale", None, p.get("erreur") or "Positions non mesurées.")]
    cs = []
    for i, r in enumerate(releves):
        points = r.get("points") or []
        total = len(points) or 1
        top3 = sum(1 for x in points if x.get("position") is not None and x["position"] <= 3)
        top10 = sum(1 for x in points if x.get("position") is not None and x["position"] <= 10)
        part = top3 / total
        fraction = min(1.0, 0.75 * part / 0.6 + 0.25 * (top10 / total)) if total else 0.0
        cs.append(_controle(f"visibilite_{i}", "visibilite", 8, f"Visibilité sur « {r['mot_cle']} »", fraction,
                            f"Dans le top 3 sur {round(part * 100)} % de la zone testée (top 10 : {round(top10 / total * 100)} %)." + (f" Position moyenne : {r['resume']['position_moyenne']}." if (r.get('resume') or {}).get('position_moyenne') else " Non classée en moyenne."), valeur=round(part * 100)))
    return cs


def _controles_site(d: dict) -> list:
    f = d.get("fiche") or {}
    s = d.get("site") or {}
    if not f.get("site_web"):
        return [_controle("site_present", "site", 4, "Site web", 0.0, "Aucun site web n'est relié à la fiche.")]
    cs = [_controle("site_present", "site", 4, "Site web", 1.0 if s.get("accessible", True) else 0.3, f"{f['site_web']}" + ("" if s.get("accessible", True) else " : site inaccessible lors du test."))]
    if s.get("erreur") and not s.get("controle"):
        return cs
    controles = {c["code"]: c for c in ((s.get("controle") or {}).get("controles") or [])}
    if s.get("controle"):
        https = controles.get("https")
        if https:
            cs.append(_controle("https", "site", 2, "Site sécurisé (HTTPS)", 1.0 if https["statut"] == "ok" else 0.0, https["detail"]))
        if controles.get("balises"):
            c = controles["balises"]
            cs.append(_controle("balises", "site", 2, "Titre, description et H1", {"ok": 1.0, "avertissement": 0.5}.get(c["statut"], 0.0), c["detail"]))
        if controles.get("texte_sans_js"):
            c = controles["texte_sans_js"]
            cs.append(_controle("texte_visible", "site", 1, "Texte lisible sans JavaScript", {"ok": 1.0, "avertissement": 0.5}.get(c["statut"], 0.0), c["detail"]))
        if controles.get("sitemap"):
            cs.append(_controle("sitemap", "site", 1, "Plan du site (sitemap)", 1.0 if controles["sitemap"]["statut"] == "ok" else 0.0, controles["sitemap"]["detail"]))
    v = s.get("vitesse")
    if v and v.get("score_performance") is not None:
        cs.append(_controle("vitesse", "site", 3, "Rapidité sur mobile", _paliers(v["score_performance"], [(90, 1.0), (70, 0.75), (50, 0.5), (30, 0.25)]), f"Score de performance mobile : {v['score_performance']} / 100" + (f" (affichage complet en {v['affichage_complet']})." if v.get("affichage_complet") else ".")))
    nap = s.get("nap")
    if nap and (nap.get("telephone") is not None or nap.get("adresse") is not None):
        valeurs = [x for x in (nap.get("telephone"), nap.get("adresse")) if x is not None]
        manques = [lib for lib, x in (("le téléphone", nap.get("telephone")), ("l'adresse", nap.get("adresse"))) if x is False]
        cs.append(_controle("nap_site", "site", 2, "Coordonnées du site identiques à la fiche", sum(1 for x in valeurs if x) / len(valeurs), "Téléphone et adresse de la fiche retrouvés sur la page." if not manques else "Non retrouvé(s) sur la page : " + " et ".join(manques) + "."))
    if s.get("reseaux") is not None:
        cs.append(_controle("reseaux", "site", 1, "Réseaux sociaux reliés au site", 1.0 if s["reseaux"] else 0.0, ("Liens trouvés : " + ", ".join(s["reseaux"]) + ".") if s["reseaux"] else "Aucun lien vers un réseau social sur la page."))
    a = s.get("autorite")
    if a and a.get("rang") is not None:
        cs.append(_controle("autorite", "site", 1, "Autorité du site (liens entrants)", _paliers(a["rang"], [(40, 1.0), (25, 0.75), (10, 0.5), (3, 0.25)], 0.1), f"Score d'autorité {a['rang']} / 100 : {a.get('domaines_referents', 0)} site(s) qui renvoient vers lui."))
    return cs


def _controles_annuaires(d: dict) -> list:
    a = d.get("annuaires")
    if not a or not a.get("resultats"):
        return [_controle("annuaires", "annuaires", 8, "Annuaires", None, (a or {}).get("erreur") or "Annuaires non vérifiés.")]
    poids = {"pages_jaunes": 3, "bing_places": 2, "apple_plans": 2, "yelp": 1}
    cs = []
    for r in a["resultats"]:
        if r.get("erreur") or r.get("trouve") is None:
            continue
        cs.append(_controle(f"annuaire_{r['id']}", "annuaires", poids.get(r["id"], 1), r["nom"], 1.0 if r["trouve"] else 0.0,
                            "Présente." if r["trouve"] else "Non retrouvée par la recherche automatique (à confirmer à la main).", valeur=r.get("url") or None))
    nap = a.get("nap")
    if nap and nap.get("etat") in ("conforme", "ecart"):
        cs.append(_controle("coherence_nap", "annuaires", 3, "Coordonnées identiques aux annuaires", 1.0 if nap["etat"] == "conforme" else 0.25,
                            "Téléphone et adresse concordent avec Pages Jaunes." if nap["etat"] == "conforme" else "Écart avec Pages Jaunes : " + ", ".join(nap.get("manque") or []) + "."))
    return cs or [_controle("annuaires", "annuaires", 8, "Annuaires", None, "Annuaires non vérifiés.")]


def _controles_ia(d: dict) -> list:
    cs = []
    ia = d.get("ia") or {}
    for moteur, libelle, poids in (("chatgpt", "ChatGPT", 5), ("gemini", "Gemini", 4)):
        reponses = [q["moteurs"][moteur] for q in ia.get("questions") or [] if moteur in q.get("moteurs", {}) and not q["moteurs"][moteur].get("erreur")]
        if not reponses:
            continue
        citees = sum(1 for r in reponses if r.get("citee"))
        fraction = citees / len(reponses)
        cs.append(_controle(f"cite_{moteur}", "ia", poids, f"Cité par {libelle}", fraction,
                            (f"Citée dans {citees} réponse(s) sur {len(reponses)}." if citees else (f"Absente de la réponse de {libelle} à la question d'un client." if len(reponses) == 1 else f"Absente des {len(reponses)} réponses de {libelle} à la question d'un client.")), valeur=f"{citees}/{len(reponses)}"))
    s = d.get("site") or {}
    controles = {c["code"]: c for c in ((s.get("controle") or {}).get("controles") or [])}
    if controles:
        indispensables = [c for code, c in controles.items() if code.startswith("robots_") and code.split("_", 1)[1] in ("OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Googlebot")]
        if indispensables:
            bloques = [c["titre"].replace("robots.txt : ", "") for c in indispensables if c["statut"] == "probleme"]
            cs.append(_controle("robots_ia", "ia", 3, "Site ouvert aux robots des IA", 0.0 if bloques else 1.0, ("Bloqués par robots.txt : " + ", ".join(bloques) + ".") if bloques else "Les robots de ChatGPT, Perplexity et Google peuvent lire le site."))
        if controles.get("json_ld_local"):
            c = controles["json_ld_local"]
            cs.append(_controle("donnees_structurees", "ia", 2, "Données structurées de l'entreprise (LocalBusiness)", {"ok": 1.0, "avertissement": 0.4}.get(c["statut"], 0.0), c["detail"]))
        if controles.get("llms_txt"):
            cs.append(_controle("llms_txt", "ia", 1, "Fichier llms.txt", 1.0 if controles["llms_txt"]["statut"] == "ok" else 0.3, controles["llms_txt"]["detail"]))
    return cs


FONCTIONS_THEMES = {"fiche": _controles_fiche, "photos": _controles_photos, "avis": _controles_avis, "visibilite": _controles_visibilite, "site": _controles_site, "annuaires": _controles_annuaires, "ia": _controles_ia}


def evaluer(donnees: dict) -> dict:
    """
    Donnees collectees -> {"score", "libelle", "couleur", "themes": [...], "priorites": [...], "points_forts": [...], "a_verifier": [...], "activite", "conseils"}.
    Un theme sans controle evaluable est exclu et les autres themes sont ramenes sur 100 : une donnee manquante ne penalise jamais l'entreprise.
    """
    themes = []
    for code, nom, poids, icone in THEMES:
        try:
            controles = FONCTIONS_THEMES[code](donnees)
        except Exception:
            controles = []
        evaluables = [c for c in controles if c["fraction"] is not None]
        total_poids = sum(c["poids"] for c in evaluables)
        fraction_theme = sum(c["poids"] * c["fraction"] for c in evaluables) / total_poids if total_poids else None
        themes.append({"code": code, "nom": nom, "icone": icone, "poids": poids, "controles": controles, "fraction": fraction_theme, "total_poids": total_poids,
                       "score": None if fraction_theme is None else round(fraction_theme * 100)})
    poids_actifs = sum(t["poids"] for t in themes if t["fraction"] is not None)
    score = round(sum(t["poids"] * t["fraction"] for t in themes if t["fraction"] is not None) / poids_actifs * 100) if poids_actifs else None
    for t in themes:
        t["points_max"] = round(100 * t["poids"] / poids_actifs, 1) if (poids_actifs and t["fraction"] is not None) else 0
        t["points"] = round(t["points_max"] * t["fraction"], 1) if t["fraction"] is not None else 0
        for c in t["controles"]:
            if c["fraction"] is None or not t["total_poids"]:
                c["points_max"], c["points"] = 0, 0
            else:
                c["points_max"] = round(t["points_max"] * c["poids"] / t["total_poids"], 2)
                c["points"] = round(c["points_max"] * c["fraction"], 2)
    profil = profil_activite((donnees.get("fiche") or {}).get("categorie", ""))
    priorites = []
    for t in themes:
        for c in t["controles"]:
            reco = RECOS.get(c["code"]) or RECOS.get(re.sub(r"_\d+$", "", c["code"]))
            if c["fraction"] is None or c["fraction"] >= 0.85 or not reco:
                continue
            gain = round(c["points_max"] - c["points"], 1)
            if gain < 0.2:
                continue
            titre, pourquoi, comment, impact, effort = reco
            priorites.append({"code": c["code"], "theme": t["code"], "titre": titre, "pourquoi": pourquoi, "comment": comment, "constat": c["detail"], "gain": gain, "impact": impact, "effort": effort,
                              "statut": c["statut"], "priorite": round(gain * impact / effort, 2)})
    priorites.sort(key=lambda p: -p["priorite"])
    libelle, couleur = etiquette_score(score)
    forts = sorted((c for t in themes for c in t["controles"] if c["fraction"] is not None and c["fraction"] >= 0.85 and c["code"] != "site_present"), key=lambda c: -c["points_max"])[:5]
    return {"score": score, "libelle": libelle, "couleur": couleur, "themes": themes, "priorites": priorites, "points_forts": [{"libelle": c["libelle"], "detail": c["detail"], "theme": c["theme"]} for c in forts],
            "a_verifier": [{"titre": a, "detail": b} for a, b in NON_VERIFIABLE], "activite": profil, "conseils": [{"titre": a, "texte": b} for a, b in CONSEILS_ACTIVITE[profil]]}


# ---------------------------------------------------------------------------------------------------------------- collecte

def _compact(texte: str) -> str:
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in unicodedata.normalize("NFD", texte or "") if unicodedata.category(c) != "Mn").lower())


def variantes_nom(fiche: dict) -> list:
    """Noms sous lesquels l'entreprise peut etre citee : marque du domaine, premier segment du titre de la fiche."""
    variantes = []
    domaine = urlparse(fiche.get("site_web") or "").netloc or fiche.get("domaine") or ""
    marque = domaine.lower().removeprefix("www.").split(".")[0] if domaine else ""
    if len(_compact(marque)) >= 4 and marque not in ("facebook", "instagram", "wix", "wordpress", "business", "linktr"):
        variantes.append(_compact(marque))
    premier = re.split(r"\s[-–—|:]\s", fiche.get("titre") or "", maxsplit=1)[0]
    if len(_compact(premier)) >= 4:
        variantes.append(_compact(premier))
    titre = _compact(fiche.get("titre") or "")
    if 4 <= len(titre) <= 40:
        variantes.append(titre)
    return list(dict.fromkeys(variantes))


def entreprise_citee(texte: str, fiche: dict) -> bool:
    brut = _compact(texte)
    return any(v in brut for v in variantes_nom(fiche))


def questions_ia(fiche: dict, ville: str) -> list:
    """Les questions qu'un client poserait a une IA (langage naturel, avec la ville)."""
    activite = (fiche.get("categorie") or "").strip().lower() or "entreprise"
    return [f"{activite} à {ville}", f"Quel est le meilleur {activite} à {ville} ?"]


def collecter_ia(fiche: dict, ville: str, outils: dict) -> dict:
    """Pose les questions a ChatGPT et Gemini ; l'entreprise est-elle citee, et qui l'IA recommande-t-elle a sa place ?"""
    questions = questions_ia(fiche, ville)
    taches = [(q, m) for q in questions for m in ("chatgpt", "gemini")]

    def une(tache):
        question, moteur = tache
        try:
            texte, sources = outils["interroger_ia"](moteur, question, "FR", ville)
        except Exception as erreur:
            return question, moteur, {"erreur": str(erreur)[:160]}
        reponse = {"citee": entreprise_citee(texte, fiche), "extrait": (texte or "")[:900], "sources": [{"domaine": s["domaine"], "url": s["url"], "titre": s["titre"][:100]} for s in (sources or [])[:12]], "concurrents": [], "erreur": ""}
        try:
            analyse = outils["analyser_reponse"](fiche.get("titre") or "", texte)
            reponse["concurrents"] = [c for c in (analyse.get("concurrents_cites") or []) if isinstance(c, str)][:8]
            reponse["position"] = analyse.get("position")
        except Exception:
            pass
        return question, moteur, reponse

    with ThreadPoolExecutor(max_workers=4) as pool:
        resultats = list(pool.map(une, taches))
    par_question = {q: {"question": q, "moteurs": {}} for q in questions}
    for question, moteur, reponse in resultats:
        par_question[question]["moteurs"][moteur] = reponse
    domaines = {}
    for _, _, r in resultats:
        for s in r.get("sources", []):
            domaines[s["domaine"]] = domaines.get(s["domaine"], 0) + 1
    concurrents = {}
    for _, _, r in resultats:
        for c in r.get("concurrents", []):
            concurrents[c] = concurrents.get(c, 0) + 1
    return {"questions": list(par_question.values()), "domaines": [{"domaine": d, "nb": n} for d, n in sorted(domaines.items(), key=lambda x: -x[1])[:12]],
            "concurrents_cites": [{"nom": n, "nb": c} for n, c in sorted(concurrents.items(), key=lambda x: -x[1])[:8]]}


def geo_url(url: str) -> str:
    return url if "://" in (url or "") else "https://" + (url or "")


def collecter_site(fiche: dict, outils: dict) -> dict:
    """Controle technique du site, vitesse, autorite, coordonnees et reseaux sociaux de la page."""
    url = fiche.get("site_web") or ""
    resultat = {"accessible": True, "erreur": "", "controle": None, "vitesse": None, "autorite": None, "nap": None, "reseaux": None}
    try:
        resultat["controle"] = outils["controler_site"](url)
    except Exception as erreur:
        resultat["accessible"] = False
        resultat["erreur"] = str(erreur)[:200]
    if resultat["accessible"]:
        try:
            html = outils["lire_html"](url)
            resultat["reseaux"] = sorted({m.group(1).lower() for m in re.finditer(r"https?://(?:www\.|m\.|fr-fr\.)?(facebook|instagram|linkedin|youtube|tiktok|x|twitter|pinterest)\.com/[^\s\"'<>]+", html or "", re.I)})
            texte = outils["texte_visible"](html or "")
            nap = outils["controle_nap"](texte, {"telephone": fiche.get("telephone", ""), "adresse": fiche.get("adresse", "")}, "")
            resultat["nap"] = {"telephone": nap.get("telephone"), "adresse": nap.get("adresse")}
        except Exception:
            pass
    domaine = (urlparse(geo_url(url)).netloc or "").lower().removeprefix("www.")
    for cle, nom, cible in (("vitesse", "pagespeed", url), ("autorite", "autorite", domaine)):         # l'autorite se mesure sur le domaine entier, pas sur la page de la fiche
        try:
            resultat[cle] = outils[nom](cible)
        except Exception:
            resultat[cle] = None
    return resultat


def noms_recherche(fiche: dict) -> list:
    """Noms a chercher dans les annuaires : le premier segment du titre de la fiche, puis la marque du site (sauf si c'est un reseau social)."""
    titre = (fiche.get("titre") or "").strip()
    premier = re.split(r"\s[-–—|:]\s", titre, maxsplit=1)[0].strip()
    domaine = (urlparse(fiche.get("site_web") or "").netloc or "").lower().removeprefix("www.").split(".")[0]
    noms = [premier or titre]
    if len(domaine) >= 4 and domaine not in ("facebook", "instagram", "wix", "wordpress", "business", "linktr", "google") and _compact(domaine) != _compact(noms[0]):
        noms.append(domaine)
    return noms[:2]


def collecter_annuaires(fiche: dict, ville: str, outils: dict) -> dict:
    """Presence sur les annuaires et coherence des coordonnees sur la page Pages Jaunes si elle est lisible."""
    resultats, derniere_erreur = {}, ""
    for nom in noms_recherche(fiche):                      # un titre « Achat Or N°1 GoldUnion - Lens - La référence… » ne se retrouve nulle part : on essaie aussi la marque du site
        try:
            for r in outils["verifier_annuaires"](nom, ville):
                actuel = resultats.get(r["id"])
                if not actuel or (r.get("trouve") and not actuel.get("trouve")) or (actuel.get("erreur") and not r.get("erreur")):
                    resultats[r["id"]] = r
        except Exception as erreur:
            derniere_erreur = str(erreur)[:160]
    if not resultats:
        return {"resultats": [], "erreur": derniere_erreur or "Annuaires non vérifiés."}
    resultats = list(resultats.values())
    sortie = {"resultats": resultats, "nap": None, "erreur": ""}
    pj = next((r for r in resultats if r["id"] == "pages_jaunes" and r.get("trouve") and r.get("url")), None)
    if pj:
        try:
            code, texte = outils["lire_page"](pj["url"])
            if texte:
                nap = outils["controle_nap"](texte, {"telephone": fiche.get("telephone", ""), "adresse": fiche.get("adresse", "")}, "")
                manque = [m for m in nap.get("manque", []) if m != "nom"]
                sortie["nap"] = {"etat": "ecart" if manque else "conforme", "manque": manque, "url": pj["url"]}
        except Exception:
            pass
    return sortie


def collecter_positions(fiche: dict, mots_cles: list, taille_grille: int, outils: dict) -> dict:
    """Grille de positions autour de l'adresse pour chaque mot-cle, avec les concurrents les mieux places."""
    if fiche.get("latitude") is None or fiche.get("longitude") is None:
        return {"releves": [], "erreur": "Adresse non localisable : positions non mesurées."}
    releves = []
    erreurs = []

    def un(mot):
        try:
            return outils["grille"](fiche["titre"], mot, fiche["latitude"], fiche["longitude"], taille_grille)
        except Exception as erreur:
            erreurs.append(str(erreur)[:160])
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(un, mots_cles):
            if r:
                releves.append(r)
    return {"releves": releves, "erreur": erreurs[0] if erreurs and not releves else ""}


def outils_reels() -> dict:
    """Les appels externes reels (remplaces par des faux dans les tests)."""
    from . import audit_backlinks, audit_prospect, audit_site_technique, baisse_google, citations, geo_technique, ia_visibilite, suivi_goldunion

    def pagespeed(url):
        if not audit_site_technique.identifiants_configures():
            raise RuntimeError("PageSpeed non configuré")
        return audit_site_technique.analyser_site(url)

    def verifier_annuaires(nom, ville):
        return citations.verifier_citations(nom, ville, "FR", [a["id"] for a in citations.ANNUAIRES])

    def lire_html(url):
        code, html, _, _ = geo_technique._get(geo_technique.normaliser_url(url))
        return html if code == 200 else ""

    return {
        "controler_site": geo_technique.controler_site, "pagespeed": pagespeed, "autorite": audit_backlinks.analyser_autorite, "lire_html": lire_html,
        "texte_visible": suivi_goldunion._texte_visible, "controle_nap": baisse_google.controle_nap, "verifier_annuaires": verifier_annuaires, "lire_page": baisse_google.lire_page,
        "grille": lambda nom, mot, lat, lng, taille: audit_prospect.grille_positions_prospect(nom, mot, lat, lng, taille_grille=taille),
        "interroger_ia": baisse_google.interroger_moteur, "analyser_reponse": ia_visibilite.analyser_reponse, "synthese_ia": synthese_ia,
    }


def mots_cles_par_defaut(fiche: dict, ville: str) -> list:
    activite = (fiche.get("categorie") or "").strip().lower()
    return [f"{activite} {ville.lower()}".strip()] if activite else []


def collecter(entree: dict, outils: dict = None, progression=lambda pct, message: None) -> dict:
    """
    entree : {"fiche": dict, "ville": str, "mots_cles": [...], "taille_grille": 3, "ia": bool}. Renvoie les donnees collectees ; chaque etape qui echoue est notee
    dans « etapes » et simplement exclue du score.
    """
    outils = outils or outils_reels()
    fiche, ville = entree["fiche"], entree["ville"]
    donnees = {"fiche": fiche, "ville": ville, "mots_cles": entree.get("mots_cles") or [], "etapes": {}}
    avancement = {"fait": 0}
    verrou = threading.Lock()
    etapes = [("positions", "Position sur la carte"), ("site", "Site web"), ("annuaires", "Annuaires")] + ([("ia", "Réponses de ChatGPT et Gemini")] if entree.get("ia", True) else [])

    def executer_etape(nom_etape):
        nom, libelle = nom_etape
        try:
            if nom == "positions":
                valeur = collecter_positions(fiche, donnees["mots_cles"], entree.get("taille_grille") or 3, outils) if donnees["mots_cles"] else {"releves": [], "erreur": "Aucun mot-clé."}
            elif nom == "site":
                valeur = collecter_site(fiche, outils) if fiche.get("site_web") else None
            elif nom == "annuaires":
                valeur = collecter_annuaires(fiche, ville, outils)
            else:
                valeur = collecter_ia(fiche, ville, outils)
            donnees[nom] = valeur
            donnees["etapes"][nom] = {"statut": "ok"}
        except Exception as erreur:
            donnees[nom] = None
            donnees["etapes"][nom] = {"statut": "erreur", "message": str(erreur)[:160]}
        with verrou:
            avancement["fait"] += 1
            progression(10 + int(80 * avancement["fait"] / len(etapes)), f"{libelle} analysé")

    progression(8, "Analyse en cours…")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(executer_etape, etapes))
    return donnees


# ---------------------------------------------------------------------------------------------------------------- synthese IA (facultative)

def synthese_ia(donnees: dict, resultat: dict) -> dict:
    """Synthese en quelques phrases et 3 opportunites propres a l'activite, redigees par Claude a partir des seuls constats chiffres (rien d'invente)."""
    import anthropic
    from . import ia_visibilite

    if not ia_visibilite.CLE_ANTHROPIC:
        raise RuntimeError("ANTHROPIC_API_KEY manquant.")
    f = donnees.get("fiche") or {}
    constats = {
        "entreprise": f.get("titre"), "ville": donnees.get("ville"), "activite": f.get("categorie"), "score_global": resultat["score"],
        "themes": {t["nom"]: t["score"] for t in resultat["themes"] if t["score"] is not None},
        "principaux_manques": [{"titre": p["titre"], "constat": p["constat"]} for p in resultat["priorites"][:6]],
        "points_forts": [p["libelle"] for p in resultat["points_forts"]],
        "ia_recommande_a_sa_place": [c["nom"] for c in ((donnees.get("ia") or {}).get("concurrents_cites") or [])[:5]],
    }
    schema = {"type": "object", "properties": {"resume": {"type": "string"}, "opportunites": {"type": "array", "items": {"type": "object", "properties": {"titre": {"type": "string"}, "texte": {"type": "string"}}, "required": ["titre", "texte"], "additionalProperties": False}}},
              "required": ["resume", "opportunites"], "additionalProperties": False}
    consigne = ("Tu rédiges la synthèse d'un audit de visibilité locale destinée au dirigeant de l'entreprise. À partir UNIQUEMENT des constats ci-dessous (n'invente aucun chiffre, aucun fait), écris : "
                "1) « resume » : 3 à 4 phrases claires en français, ton direct et bienveillant, qui disent où en est l'entreprise et la première chose à faire ; "
                "2) « opportunites » : exactement 3 idées concrètes, propres à cette activité et à cette ville, chacune avec un titre court et une explication de 1 à 2 phrases.\n\n"
                + json.dumps(constats, ensure_ascii=False))
    client = anthropic.Anthropic(api_key=ia_visibilite.CLE_ANTHROPIC)
    reponse = client.messages.create(model=ia_visibilite.MODELE_CLAUDE, max_tokens=1200, thinking={"type": "disabled"}, output_config={"format": {"type": "json_schema", "schema": schema}},
                                     messages=[{"role": "user", "content": consigne}])
    texte = next((b.text for b in reponse.content if b.type == "text"), None)
    if not texte:
        raise RuntimeError("Synthèse vide.")
    sortie = json.loads(texte)
    sortie["opportunites"] = sortie.get("opportunites", [])[:3]
    return sortie


# ---------------------------------------------------------------------------------------------------------------- persistance et execution

DELAI_ABANDON = timedelta(minutes=25)


def creer(db, nom: str, ville: str, entree: dict, lead_id: int = None) -> "models.AuditFiche":
    audit = models.AuditFiche(token=secrets.token_urlsafe(18), nom=nom, ville=ville, statut="EN_COURS", message="En attente…", progression=0, options_json=json.dumps(entree, ensure_ascii=False), lead_id=lead_id)
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def charger_json(texte: str, defaut):
    try:
        valeur = json.loads(texte) if texte else defaut
    except ValueError:
        return defaut
    return valeur if isinstance(valeur, type(defaut)) else defaut


def verifier_abandon(db, audit) -> None:
    """Un audit « en cours » depuis trop longtemps (serveur redemarre pendant l'analyse) est declare interrompu."""
    if audit.statut == "EN_COURS" and audit.maj_le and datetime.utcnow() - audit.maj_le > DELAI_ABANDON:
        audit.statut, audit.message = "ERREUR", "Analyse interrompue (redémarrage du serveur) : relancez l'audit."
        db.commit()


def executer(session_factory, audit_id: int, outils: dict = None) -> None:
    """Collecte, evalue et enregistre un audit (a lancer dans un fil)."""
    def noter(**champs):
        db = session_factory()
        try:
            audit = db.get(models.AuditFiche, audit_id)
            for cle, valeur in champs.items():
                setattr(audit, cle, valeur)
            audit.maj_le = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    try:
        db = session_factory()
        try:
            entree = charger_json(db.get(models.AuditFiche, audit_id).options_json, {})
        finally:
            db.close()
        outils = outils or outils_reels()
        donnees = collecter(entree, outils, lambda pct, message: noter(progression=pct, message=message))
        noter(progression=92, message="Calcul du score…")
        resultat = evaluer(donnees)
        if entree.get("ia", True) and outils.get("synthese_ia"):
            try:
                resultat["synthese"] = outils["synthese_ia"](donnees, resultat)
            except Exception:
                resultat["synthese"] = None
        noter(statut="PRET", progression=100, message="Audit terminé", donnees_json=json.dumps(donnees, ensure_ascii=False), resultat_json=json.dumps(resultat, ensure_ascii=False), score=resultat["score"])
    except Exception as erreur:
        try:
            noter(statut="ERREUR", message=f"L'analyse a échoué : {str(erreur)[:200]}")
        except Exception:
            pass


def lancer(session_factory, audit_id: int, outils: dict = None) -> None:
    threading.Thread(target=executer, args=(session_factory, audit_id, outils), daemon=True).start()
