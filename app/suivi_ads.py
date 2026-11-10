"""
Pubs Google Ads des agences GoldUnion : lecture (en LECTURE SEULE) de la structure du compte et des chiffres des 30 derniers jours, par campagne, et
proposition de rattachement de chaque campagne a une agence.

Aucune ecriture n'est faite sur le compte Google Ads : seules des requetes de lecture (GAQL « SELECT ... ») sont envoyees. La connexion est celle de la
plateforme (models.ParametreGoogleAds : jeton developpeur, compte gestionnaire, autorisation Google) ; le compte lu est celui du client (compte_client).
"""

import json
import math
import re
from datetime import datetime

from . import google_ads_keywords, suivi_goldunion

COMPTE_ADS_DEFAUT = "7191050822"        # compte GoldUnion (dans le compte gestionnaire 669-130-6801)

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
            c["zones"].append({"type": "zone", "ressource": ressource, "nom": "", "exclue": exclue})
            if ressource:
                a_nommer.add(ressource)
    return a_nommer


def nommer_zones(campagnes: dict, lignes) -> None:
    noms = {ligne.geo_target_constant.resource_name: (ligne.geo_target_constant.canonical_name or ligne.geo_target_constant.name) for ligne in lignes}
    for c in campagnes.values():
        for z in c["zones"]:
            if z["type"] == "zone":
                z["nom"] = noms.get(z["ressource"], z["ressource"])


# ---------- lecture complete

def lire_structure(parametre_ads, compte_client: str, jours: int = 30, executer=None) -> dict:
    """
    Campagnes du compte, avec chiffres des `jours` derniers jours et zones ciblees. `executer(requete)` renvoie les lignes d'une requete GAQL : par defaut
    la vraie API (lecture seule) ; injecte dans les tests.
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
        nommer_zones(campagnes, executer(REQUETE_ZONES.format(noms=", ".join(f"'{n}'" for n in sorted(a_nommer)))))
    liste = sorted(campagnes.values(), key=lambda c: (c["statut"] != "ENABLED", -c["cout"], c["nom"].lower()))
    return {"compte": compte, "maj": datetime.utcnow().isoformat(timespec="seconds"), "jours": int(jours), "campagnes": liste,
            "total": {"campagnes": len(liste), "actives": sum(1 for c in liste if c["statut"] == "ENABLED"), "cout": round(sum(c["cout"] for c in liste), 2),
                      "clics": sum(c["clics"] for c in liste), "impressions": sum(c["impressions"] for c in liste), "conversions": round(sum(c["conversions"] for c in liste), 1)}}


# ---------- rattachement campagne -> agence

def distance_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _contient_mot(texte: str, mot: str) -> bool:
    mot = suivi_goldunion._forme_comparable(mot).strip()
    return len(mot) >= 3 and f" {mot} " in suivi_goldunion._forme_comparable(texte)


def proposer_agence(campagne: dict, agences: list):
    """
    Agence la plus probable pour une campagne : 1) un rayon autour de l'agence (distance), 2) sa ville dans le nom de la campagne, 3) sa ville dans une zone
    ciblee. agences : [{"id", "nom", "ville", "lat", "lng"}]. Renvoie {"id", "nom", "raison", "fiabilite"} ou None si aucune ou plusieurs agences possibles.
    """
    proches = []
    for z in campagne.get("zones", []):
        if z["type"] == "rayon" and not z.get("exclue") and z.get("lat") is not None:
            for a in agences:
                if a.get("lat") is not None:
                    d = distance_km(z["lat"], z["lng"], a["lat"], a["lng"])
                    if d <= max(z.get("rayon_km") or 0, 2):
                        proches.append((d, a, f"rayon de {z.get('rayon_km') or '?'} km autour de {z.get('ville') or 'l’adresse ciblée'}"))
    if proches:
        d, a, raison = min(proches, key=lambda x: x[0])
        return {"id": a["id"], "nom": a["nom"], "raison": raison, "fiabilite": "forte"}
    par_nom = [a for a in agences if a.get("ville") and _contient_mot(campagne.get("nom", ""), a["ville"])]
    if len(par_nom) == 1:
        return {"id": par_nom[0]["id"], "nom": par_nom[0]["nom"], "raison": f"« {par_nom[0]['ville']} » dans le nom de la campagne", "fiabilite": "forte"}
    if len(par_nom) > 1:
        return None
    par_zone = [a for a in agences if a.get("ville") and any(_contient_mot(z.get("nom", ""), a["ville"]) for z in campagne.get("zones", []) if z["type"] == "zone" and not z.get("exclue"))]
    if len(par_zone) == 1:
        return {"id": par_zone[0]["id"], "nom": par_zone[0]["nom"], "raison": f"zone ciblée contenant « {par_zone[0]['ville']} »", "fiabilite": "moyenne"}
    return None


def agences_pour_rattachement(entrees: list) -> list:
    """Agences suivies (lignes de suivi_goldunion.lignes) avec leur ville et leurs coordonnees."""
    return [{"id": e["client"].id, "nom": e["client"].nom, "ville": e["donnees"].get("ville", ""), "lat": e["donnees"].get("lat"), "lng": e["donnees"].get("lng")} for e in entrees]


def charger(parametre) -> dict:
    try:
        return json.loads(parametre.ads_json) if parametre.ads_json else {}
    except ValueError:
        return {}
