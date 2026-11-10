"""
Pubs Google Ads des agences GoldUnion : lecture (en LECTURE SEULE) de la structure du compte et des chiffres des 30 derniers jours, par campagne, puis
calcul de ce que chaque campagne COUVRE comme agences (villes ciblees, rayon autour de l'agence, France entiere sauf exclusions, ville dans le nom de la campagne).

Aucune ecriture n'est faite sur le compte Google Ads : seules des requetes de lecture (GAQL « SELECT ... ») sont envoyees. La connexion est celle de la
plateforme (models.ParametreGoogleAds : jeton developpeur, compte gestionnaire, autorisation Google) ; le compte lu est celui du client (compte_client).
"""

import json
import math
import re
from datetime import datetime

from . import google_ads_keywords, suivi_goldunion

COMPTE_ADS_DEFAUT = "7191050822"        # compte GoldUnion (dans le compte gestionnaire 669-130-6801)
TAILLE_LOT_ZONES = 400                  # noms de zones demandes a Google par requete
NB_MAX_LIGNES_VILLES = 8000

REQUETE_CAMPAGNES = """
SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.start_date_time, campaign.end_date_time, campaign_budget.amount_micros
FROM campaign
WHERE campaign.status != 'REMOVED'
"""

REQUETE_RESULTATS = """
SELECT campaign.id, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM campaign
WHERE campaign.status != 'REMOVED' AND segments.date DURING LAST_{jours}_DAYS
"""

REQUETE_CIBLAGES = """
SELECT campaign.id, campaign_criterion.criterion_id, campaign_criterion.type, campaign_criterion.negative, campaign_criterion.location.geo_target_constant,
       campaign_criterion.proximity.radius, campaign_criterion.proximity.radius_units, campaign_criterion.proximity.address.city_name,
       campaign_criterion.proximity.address.street_address, campaign_criterion.proximity.geo_point.latitude_in_micro_degrees,
       campaign_criterion.proximity.geo_point.longitude_in_micro_degrees
FROM campaign_criterion
WHERE campaign_criterion.type IN ('LOCATION', 'PROXIMITY') AND campaign.status != 'REMOVED'
"""

REQUETE_ZONES = """
SELECT geo_target_constant.resource_name, geo_target_constant.name, geo_target_constant.canonical_name, geo_target_constant.target_type
FROM geo_target_constant
WHERE geo_target_constant.resource_name IN ({noms})
"""

# Resultats par ville de l'internaute (pas toujours disponible, notamment pour certains types de campagnes : une erreur ici n'empeche pas le reste)
REQUETE_VILLES = """
SELECT campaign.id, segments.geo_target_city, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM campaign
WHERE campaign.status != 'REMOVED' AND segments.date DURING LAST_{jours}_DAYS AND metrics.impressions > 0
"""


def normaliser_id(texte: str) -> str:
    """« 719-105-0822 » -> « 7191050822 » (l'API attend dix chiffres, sans tirets)."""
    return re.sub(r"\D", "", texte or "")


def formater_id(identifiant: str) -> str:
    chiffres = normaliser_id(identifiant)
    return f"{chiffres[:3]}-{chiffres[3:6]}-{chiffres[6:]}" if len(chiffres) == 10 else chiffres


def _micros(valeur) -> float:
    return round((valeur or 0) / 1_000_000, 2)


def _date(texte: str) -> str:
    return (texte or "")[:10]


# ---------- lecture des lignes renvoyees par Google (objets « GoogleAdsRow ») -> dictionnaires simples

def lire_campagnes(lignes) -> dict:
    campagnes = {}
    for ligne in lignes:
        c = ligne.campaign
        campagnes[int(c.id)] = {
            "id": int(c.id), "nom": c.name, "statut": c.status.name, "type": c.advertising_channel_type.name, "debut": _date(c.start_date_time), "fin": _date(c.end_date_time),
            "budget_jour": _micros(ligne.campaign_budget.amount_micros), "impressions": 0, "clics": 0, "cout": 0.0, "conversions": 0.0, "zones": [],
        }
    return campagnes


def ajouter_resultats(campagnes: dict, lignes) -> None:
    for ligne in lignes:
        c = campagnes.get(int(ligne.campaign.id))
        if not c:
            continue
        m = ligne.metrics
        c["impressions"] += int(m.impressions)
        c["clics"] += int(m.clicks)
        c["cout"] = round(c["cout"] + _micros(m.cost_micros), 2)
        c["conversions"] = round(c["conversions"] + float(m.conversions), 1)


def ajouter_ciblages(campagnes: dict, lignes) -> set:
    """Zones et rayons cibles par chaque campagne ; renvoie les identifiants de zones nommees a resoudre (« geoTargetConstants/1006094 »)."""
    a_nommer = set()
    for ligne in lignes:
        c = campagnes.get(int(ligne.campaign.id))
        if not c:
            continue
        critere = ligne.campaign_criterion
        exclue = bool(critere.negative)
        if critere.type_.name == "PROXIMITY":
            p = critere.proximity
            unite = p.radius_units.name
            rayon_km = round(p.radius * (1.609344 if unite == "MILES" else 1), 1) if p.radius else None
            lat, lng = p.geo_point.latitude_in_micro_degrees / 1_000_000, p.geo_point.longitude_in_micro_degrees / 1_000_000
            c["zones"].append({"type": "rayon", "ville": p.address.city_name, "adresse": p.address.street_address, "rayon_km": rayon_km, "exclue": exclue,
                               "lat": lat if (lat or lng) else None, "lng": lng if (lat or lng) else None})
        elif critere.type_.name == "LOCATION":
            ressource = critere.location.geo_target_constant
            c["zones"].append({"type": "zone", "ressource": ressource, "nom": "", "niveau": "", "exclue": exclue})
            if ressource:
                a_nommer.add(ressource)
    return a_nommer


def lire_noms_zones(lignes) -> dict:
    """{« geoTargetConstants/1006094 »: {"nom": « Carpentras,Provence-Alpes-Cote d'Azur,France », "niveau": « City »}}"""
    return {ligne.geo_target_constant.resource_name: {"nom": ligne.geo_target_constant.canonical_name or ligne.geo_target_constant.name, "niveau": ligne.geo_target_constant.target_type}
            for ligne in lignes}


def nommer_zones(campagnes: dict, noms: dict) -> None:
    for c in campagnes.values():
        for z in c["zones"]:
            if z["type"] == "zone":
                info = noms.get(z["ressource"]) or {}
                z["nom"], z["niveau"] = info.get("nom") or z["ressource"], info.get("niveau", "")


def _noms_par_lots(executer, ressources) -> dict:
    noms, ressources = {}, sorted(ressources)
    for i in range(0, len(ressources), TAILLE_LOT_ZONES):
        lot = ressources[i:i + TAILLE_LOT_ZONES]
        noms.update(lire_noms_zones(executer(REQUETE_ZONES.format(noms=", ".join(f"'{n}'" for n in lot)))))
    return noms


def lire_villes(executer, jours: int) -> list:
    """Resultats des `jours` derniers jours par campagne ET par ville de l'internaute : [{"campagne", "zone", "cout", "clics", "conversions"}] (les plus depensiers d'abord)."""
    cumul = {}
    for ligne in executer(REQUETE_VILLES.format(jours=int(jours))):
        ressource = ligne.segments.geo_target_city
        if not ressource:
            continue
        m = ligne.metrics
        c = cumul.setdefault((int(ligne.campaign.id), ressource), [0.0, 0, 0.0])
        c[0] += _micros(m.cost_micros); c[1] += int(m.clicks); c[2] += float(m.conversions)
    lignes = sorted(cumul.items(), key=lambda x: -x[1][0])[:NB_MAX_LIGNES_VILLES]
    noms = _noms_par_lots(executer, {ressource for (_, ressource), _ in lignes})
    return [{"campagne": cid, "zone": (noms.get(ressource) or {}).get("nom") or ressource, "cout": round(v[0], 2), "clics": v[1], "conversions": round(v[2], 1)} for (cid, ressource), v in lignes]


# ---------- lecture complete

def lire_structure(parametre_ads, compte_client: str, jours: int = 30, executer=None) -> dict:
    """
    Campagnes du compte, avec chiffres des `jours` derniers jours, zones ciblees et resultats par ville. `executer(requete)` renvoie les lignes d'une requete
    GAQL : par defaut la vraie API (lecture seule) ; injecte dans les tests.
    """
    compte = normaliser_id(compte_client)
    if len(compte) != 10:
        raise ValueError("L'identifiant du compte Google Ads doit comporter 10 chiffres (par exemple 719-105-0822).")
    if executer is None:
        client = google_ads_keywords.construire_client(parametre_ads)
        service = client.get_service("GoogleAdsService")
        executer = lambda requete: list(service.search(customer_id=compte, query=requete))
    campagnes = lire_campagnes(executer(REQUETE_CAMPAGNES))
    ajouter_resultats(campagnes, executer(REQUETE_RESULTATS.format(jours=int(jours))))
    a_nommer = ajouter_ciblages(campagnes, executer(REQUETE_CIBLAGES))
    if a_nommer:
        nommer_zones(campagnes, _noms_par_lots(executer, a_nommer))
    villes, erreur_villes = [], ""
    try:
        villes = lire_villes(executer, jours)
    except Exception as erreur:                                   # decoupage par ville indisponible : le reste de la lecture reste valable
        erreur_villes = str(erreur)[:300]
    liste = sorted(campagnes.values(), key=lambda c: (c["statut"] != "ENABLED", -c["cout"], c["nom"].lower()))
    return {"compte": compte, "maj": datetime.utcnow().isoformat(timespec="seconds"), "jours": int(jours), "campagnes": liste, "villes": villes, "villes_erreur": erreur_villes,
            "total": {"campagnes": len(liste), "actives": sum(1 for c in liste if c["statut"] == "ENABLED"), "cout": round(sum(c["cout"] for c in liste), 2),
                      "clics": sum(c["clics"] for c in liste), "impressions": sum(c["impressions"] for c in liste), "conversions": round(sum(c["conversions"] for c in liste), 1)}}


# ---------- ce que chaque campagne couvre comme agences

def distance_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _jetons(texte: str) -> set:
    return {m for m in suivi_goldunion._forme_comparable(texte).split() if m not in suivi_goldunion.MOTS_VILLE_BANALS and len(m) >= 3}


def meme_ville(ville: str, nom_zone: str) -> bool:
    """
    La zone Google (« Agen,Nouvelle-Aquitaine,France », « Saint-Denis,Reunion,France ») est-elle la ville de l'agence ? Le premier element de la zone doit etre la ville
    (« Villeneuve-d'Agen » n'est pas « Agen ») ; la region sert seulement quand le nom de la fiche la contient (« Saint-Denis Reunion »).
    """
    morceaux = (nom_zone or "").split(",")
    ville_j, premier = _jetons(ville), _jetons(morceaux[0])
    if not ville_j or not premier:
        return False
    if ville_j == premier:
        return True
    return premier <= ville_j and ville_j <= (premier | _jetons(morceaux[1] if len(morceaux) > 1 else ""))


def _contient_mot(texte: str, mot: str) -> bool:
    mot = suivi_goldunion._forme_comparable(mot).strip()
    return len(mot) >= 3 and f" {mot} " in suivi_goldunion._forme_comparable(texte)


def _est_pays(zone: dict) -> bool:
    return zone["type"] == "zone" and (zone.get("niveau") == "Country" or suivi_goldunion._forme_comparable(zone.get("nom", "")).strip() == "france")


def _rayon_couvre(zone: dict, agence: dict) -> bool:
    if zone["type"] != "rayon" or zone.get("lat") is None or agence.get("lat") is None:
        return False
    return distance_km(zone["lat"], zone["lng"], agence["lat"], agence["lng"]) <= max(zone.get("rayon_km") or 0, 2)


def agence_couverte(campagne: dict, agence: dict, agences: list):
    """
    Cette campagne atteint-elle cette agence ? Renvoie {"mode", "raison"} ou None. Modes : « rayon » (rayon autour de l'agence), « ville » (ville ciblee),
    « nom » (ville dans le nom d'une campagne ciblant la France), « france » (France entiere hors villes exclues). Une ville exclue n'est jamais couverte.
    """
    ville = agence.get("ville") or ""
    zones = campagne.get("zones", [])
    positives, negatives = [z for z in zones if not z.get("exclue")], [z for z in zones if z.get("exclue")]
    if any((z["type"] == "zone" and meme_ville(ville, z.get("nom", ""))) or _rayon_couvre(z, agence) for z in negatives):
        return None
    for z in positives:
        if _rayon_couvre(z, agence):
            return {"mode": "rayon", "raison": f"rayon de {z.get('rayon_km') or '?'} km autour de {z.get('ville') or 'l’adresse ciblée'}"}
    if any(z["type"] == "zone" and not _est_pays(z) and meme_ville(ville, z.get("nom", "")) for z in positives):
        return {"mode": "ville", "raison": "ville ciblée"}
    if any(_est_pays(z) for z in positives):
        nommees = [a for a in agences if a.get("ville") and _contient_mot(campagne.get("nom", ""), a["ville"])]
        if nommees:
            return {"mode": "nom", "raison": f"« {ville} » dans le nom de la campagne"} if any(a["id"] == agence["id"] for a in nommees) else None
        return {"mode": "france", "raison": "France entière (hors villes exclues)"}
    return None


def couverture_campagnes(campagnes: list, agences: list) -> list:
    """Chaque campagne avec `agences` : [{"id", "nom", "mode", "raison"}] et `dediee` (identifiant de l'agence si la campagne n'en couvre qu'une seule)."""
    resultat = []
    for c in campagnes:
        couvertes = []
        for a in agences:
            cv = agence_couverte(c, a, agences)
            if cv:
                couvertes.append({"id": a["id"], "nom": a["nom"], **cv})
        resultat.append({**c, "agences": couvertes, "dediee": couvertes[0]["id"] if len(couvertes) == 1 else None})
    return resultat


def vue_par_agence(campagnes_couvertes: list, agences: list, villes: list = None) -> list:
    """
    Pour chaque agence : campagnes qui la couvrent (actives, en pause), depense des campagnes DEDIEES (une seule agence couverte) et, quand Google le fournit,
    depense des campagnes PARTAGEES dans la ville de l'agence.
    """
    par_agence = {a["id"]: {"agence": a, "campagnes": [], "depense_dediee": 0.0, "conversions_dediees": 0.0, "depense_ville": 0.0, "conversions_ville": 0.0} for a in agences}
    partagees = {}
    for c in campagnes_couvertes:
        for cv in c["agences"]:
            ligne = par_agence[cv["id"]]
            ligne["campagnes"].append({"id": c["id"], "nom": c["nom"], "statut": c["statut"], "mode": cv["mode"], "raison": cv["raison"], "cout": c["cout"], "dediee": c["dediee"] == cv["id"]})
            if c["dediee"] == cv["id"]:
                ligne["depense_dediee"] = round(ligne["depense_dediee"] + c["cout"], 2)
                ligne["conversions_dediees"] = round(ligne["conversions_dediees"] + c["conversions"], 1)
            else:
                partagees[(c["id"], cv["id"])] = True
    for v in villes or []:
        for a in agences:
            if partagees.get((v["campagne"], a["id"])) and meme_ville(a.get("ville", ""), v["zone"]):
                ligne = par_agence[a["id"]]
                ligne["depense_ville"] = round(ligne["depense_ville"] + v["cout"], 2)
                ligne["conversions_ville"] = round(ligne["conversions_ville"] + v["conversions"], 1)
    for ligne in par_agence.values():
        actives = [c for c in ligne["campagnes"] if c["statut"] == "ENABLED"]
        ligne["nb_actives"], ligne["nb_pause"] = len(actives), len(ligne["campagnes"]) - len(actives)
        ligne["statut"] = "active" if actives else ("pause" if ligne["campagnes"] else "aucune")
        ligne["dediees_actives"] = sum(1 for c in actives if c["dediee"])
    return sorted(par_agence.values(), key=lambda l: ({"aucune": 0, "pause": 1, "active": 2}[l["statut"]], l["agence"]["nom"].lower()))


def agences_pour_rattachement(entrees: list) -> list:
    """Agences suivies (lignes de suivi_goldunion.lignes) avec leur ville et leurs coordonnees."""
    return [{"id": e["client"].id, "nom": e["client"].nom, "ville": e["donnees"].get("ville", ""), "lat": e["donnees"].get("lat"), "lng": e["donnees"].get("lng")} for e in entrees]


def charger(parametre) -> dict:
    try:
        return json.loads(parametre.ads_json) if parametre.ads_json else {}
    except ValueError:
        return {}
