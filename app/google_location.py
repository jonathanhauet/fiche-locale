"""
Lecture/ecriture des informations de base d'une fiche Google Business Profile
(nom, telephone, site web, adresse, horaires, description). Aucune donnee
stockee en local : Google reste la source de verite, comme pour les photos
deja presentes sur la fiche (voir google_business.lister_photos).
"""

import requests

CHAMPS_LECTURE = (
    "title,phoneNumbers,websiteUri,storefrontAddress,regularHours,specialHours,"
    "profile,latlng,categories,metadata.mapsUri"
)

JOURS_SEMAINE = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
LIBELLES_JOUR = {
    "MONDAY": "Lundi",
    "TUESDAY": "Mardi",
    "WEDNESDAY": "Mercredi",
    "THURSDAY": "Jeudi",
    "FRIDAY": "Vendredi",
    "SATURDAY": "Samedi",
    "SUNDAY": "Dimanche",
}
SLUG_PAR_JOUR = {
    "MONDAY": "lundi",
    "TUESDAY": "mardi",
    "WEDNESDAY": "mercredi",
    "THURSDAY": "jeudi",
    "FRIDAY": "vendredi",
    "SATURDAY": "samedi",
    "SUNDAY": "dimanche",
}


def obtenir_infos_fiche(identifiants, location_id: str) -> dict:
    url = f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}"
    reponse = requests.get(
        url,
        headers={"Authorization": f"Bearer {identifiants.token}"},
        params={"readMask": CHAMPS_LECTURE},
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la lecture de la fiche (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def mettre_a_jour_fiche(identifiants, location_id: str, donnees: dict, champs: list[str]) -> dict:
    """
    donnees : sous-ensemble du corps Location a envoyer (uniquement les champs modifies).
    champs : liste des noms de champs a inclure dans updateMask (doit correspondre aux cles de donnees).
    """
    url = f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}"
    reponse = requests.patch(
        url,
        headers={"Authorization": f"Bearer {identifiants.token}"},
        params={"updateMask": ",".join(champs)},
        json=donnees,
    )
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la mise a jour de la fiche (code {reponse.status_code}) : {reponse.text}")
    return reponse.json()


def rechercher_categories(identifiants, terme: str, langue: str = "fr", region: str = "FR") -> list[dict]:
    """
    Recherche dans le vocabulaire ferme de categories Google (ce n'est pas du
    texte libre : chaque categorie a un identifiant stable du type
    "categories/gcid:locksmith"). Renvoie [{"name": ..., "displayName": ...}, ...].

    Syntaxe du parametre "filter" non documentee clairement par Google et
    verifiee empiriquement (appels reels) : "displayName=mot" SANS guillemets
    fait une recherche par sous-chaine (insensible a la casse) - avec
    guillemets, le filtre est silencieusement ignore (renvoie tout le
    catalogue, ~4000 entrees). Un espace dans la valeur non quotee provoque
    une erreur 400 : on ne garde donc que le premier mot du terme tape.
    """
    premier_mot = terme.strip().split()[0] if terme.strip() else ""
    if not premier_mot:
        return []
    url = "https://mybusinessbusinessinformation.googleapis.com/v1/categories"
    params = {
        "regionCode": region,
        "languageCode": langue,
        "view": "BASIC",
        "filter": f"displayName={premier_mot}",
    }
    reponse = requests.get(url, headers={"Authorization": f"Bearer {identifiants.token}"}, params=params)
    if reponse.status_code != 200:
        raise RuntimeError(f"Echec de la recherche de categories (code {reponse.status_code}) : {reponse.text}")
    return reponse.json().get("categories", [])


def coordonnees(infos: dict):
    """
    Renvoie (latitude, longitude) depuis les infos de la fiche, ou (None, None)
    si absentes. Le champ latlng de Google n'est renseigne que si des
    coordonnees ont ete definies manuellement (rare) : la plupart des fiches
    n'en ont pas, meme avec une adresse complete.
    """
    latlng = (infos or {}).get("latlng") or {}
    return latlng.get("latitude"), latlng.get("longitude")


def adresse_texte(infos: dict) -> str:
    """Construit une adresse lisible a partir de storefrontAddress, utilisable pour un geocodage (Nominatim)."""
    adresse = (infos or {}).get("storefrontAddress") or {}
    morceaux = list(adresse.get("addressLines") or [])
    for cle in ("locality", "postalCode", "administrativeArea", "regionCode"):
        valeur = adresse.get(cle)
        if valeur:
            morceaux.append(valeur)
    return ", ".join(morceaux)


def construire_periode_exceptionnelle(date_iso: str, ferme: bool, ouverture: str = "", fermeture: str = "") -> dict:
    """
    Construit un SpecialHourPeriod Google pour une seule date (ex. jour ferie,
    fermeture ponctuelle ou horaires reduits). date_iso : "AAAA-MM-JJ".
    """
    annee, mois, jour = (int(x) for x in date_iso.split("-"))
    periode = {"startDate": {"year": annee, "month": mois, "day": jour}, "closed": ferme}
    if not ferme and ouverture and fermeture:
        h_o, m_o = (int(x) for x in ouverture.split(":"))
        h_f, m_f = (int(x) for x in fermeture.split(":"))
        periode["openTime"] = {"hours": h_o, "minutes": m_o}
        periode["closeTime"] = {"hours": h_f, "minutes": m_f}
    return periode


def fusionner_horaires_exceptionnels(special_hours_actuelles: dict, nouvelle_periode: dict) -> list[dict]:
    """
    L'API Google remplace l'integralite de specialHours.specialHourPeriods a
    chaque ecriture (pas de fusion cote serveur) : il faut donc reconstruire
    nous-memes la liste complete. On retire une eventuelle periode deja
    definie pour la meme date (ecrasement volontaire, ex. mise a jour d'un
    jour ferie deja programme) et on garde toutes les autres dates telles quelles.
    """
    date_cible = nouvelle_periode["startDate"]
    conservees = [
        p for p in (special_hours_actuelles or {}).get("specialHourPeriods", [])
        if p.get("startDate") != date_cible
    ]
    conservees.append(nouvelle_periode)
    return conservees


CRITERES_COMPLETUDE = [
    ("telephone", "Téléphone"),
    ("site_web", "Site web"),
    ("description", "Description"),
    ("horaires", "Horaires"),
    ("categorie_secondaire", "Catégorie secondaire"),
]


def score_completude(infos: dict) -> dict:
    """
    Score simple base uniquement sur la lecture deja faite via
    obtenir_infos_fiche (aucun appel API supplementaire, donc utilisable sur
    beaucoup de fiches sans ralentir). Les photos ne sont volontairement pas
    incluses ici (necessiterait un appel media a part par fiche) - elles
    restent visibles individuellement sur la page de chaque client.
    """
    infos = infos or {}
    presents = {
        "telephone": bool((infos.get("phoneNumbers") or {}).get("primaryPhone")),
        "site_web": bool(infos.get("websiteUri")),
        "description": bool((infos.get("profile") or {}).get("description")),
        "horaires": bool((infos.get("regularHours") or {}).get("periods")),
        "categorie_secondaire": bool((infos.get("categories") or {}).get("additionalCategories")),
    }
    manquants = [libelle for cle, libelle in CRITERES_COMPLETUDE if not presents[cle]]
    return {
        "score": len(CRITERES_COMPLETUDE) - len(manquants),
        "total": len(CRITERES_COMPLETUDE),
        "manquants": manquants,
    }


def horaires_par_jour(regular_hours: dict):
    """
    Regroupe les periodes Google par jour, editables individuellement dans le
    formulaire (plusieurs periodes par jour = coupures, ex. fermeture le midi).

    Seules les periodes contenues dans une seule journee (openDay == closeDay)
    sont editables ici. Les jours ayant au moins une periode qui deborde sur
    un autre jour (ex. ouvert jusqu'a 2h du matin) sont laisses de cote dans
    jours_verrouilles : impossible de les representer sans gerer un changement
    de jour, donc on les laisse en lecture seule (a modifier depuis l'app Google).

    Cas 24h/24 : Google le represente avec openTime=00:00 et closeTime a
    hours=24 le meme jour (cf. doc officielle TimePeriod). Un <input type="time">
    HTML ne peut pas contenir "24:00" (max 23:59), d'ou un champ signale a part
    plutot que forme dans une chaine "HH:MM".

    Renvoie (horaires, jours_verrouilles) :
    - horaires : {jour: {"vingt_quatre_heures": bool, "periodes": [{"ouverture": "HH:MM", "fermeture": "HH:MM"}, ...]}}
      (periodes vide si vingt_quatre_heures ; jour absent = ferme)
    - jours_verrouilles : set des jours a horaires "a cheval sur minuit", non editables ici
    """
    periodes_par_jour = {}
    jours_verrouilles = set()
    for periode in (regular_hours or {}).get("periods", []):
        jour_ouverture = periode.get("openDay")
        jour_fermeture = periode.get("closeDay")
        if jour_ouverture != jour_fermeture:
            jours_verrouilles.add(jour_ouverture)
            jours_verrouilles.add(jour_fermeture)
            continue
        periodes_par_jour.setdefault(jour_ouverture, []).append(periode)

    horaires = {}
    for jour, periodes in periodes_par_jour.items():
        if jour in jours_verrouilles:
            continue
        premiere = periodes[0]
        vingt_quatre_heures = (
            len(periodes) == 1
            and premiere.get("openTime", {}).get("hours", 0) == 0
            and premiere.get("openTime", {}).get("minutes", 0) == 0
            and premiere.get("closeTime", {}).get("hours") == 24
        )
        if vingt_quatre_heures:
            horaires[jour] = {"vingt_quatre_heures": True, "periodes": []}
            continue
        periodes_triees = sorted(
            periodes,
            key=lambda p: (p.get("openTime", {}).get("hours", 0), p.get("openTime", {}).get("minutes", 0)),
        )
        horaires[jour] = {
            "vingt_quatre_heures": False,
            "periodes": [
                {
                    "ouverture": f"{p.get('openTime', {}).get('hours', 0):02d}:{p.get('openTime', {}).get('minutes', 0):02d}",
                    "fermeture": f"{p.get('closeTime', {}).get('hours', 0):02d}:{p.get('closeTime', {}).get('minutes', 0):02d}",
                }
                for p in periodes_triees
            ],
        }
    return horaires, jours_verrouilles
