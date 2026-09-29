"""
Horaires de publication suggeres, par reseau et par jour de la semaine (0 = lundi ... 6 = dimanche, voir
date.weekday()) : des reperes generalistes couramment cites pour les reseaux sociaux professionnels, pas mesures
sur l'audience reelle de chaque client. A prendre comme un point de depart raisonnable, pas une certitude : le
meilleur moment reel depend du public de chaque fiche et peut evoluer avec le temps.

Chaque entree est (heure_HH:MM ou None, note courte). None = ce reseau/jour n'est pas recommande (ex. LinkedIn le
week-end) ; suggestion_horaire renvoie alors une note expliquant pourquoi, sans heure a proposer.
"""

HORAIRES_RECOMMANDES = {
    "google": {
        None: (
            "11:00",
            "Les posts Google Business Profile ne sont pas mis en avant par un algorithme : l'heure a peu d'effet "
            "sur leur portée. La fin de matinée correspond simplement aux pics de recherche locale.",
        ),
    },
    "facebook": {
        0: ("09:00", "Début de semaine : le matin capte l'attention avant que la journée ne se remplisse."),
        1: ("09:00", "Bon créneau en début de journée, avant les pics d'activité du milieu de semaine."),
        2: ("11:00", "Milieu de semaine, fin de matinée : créneau généralement actif sur Facebook."),
        3: ("13:00", "Jeudi midi : souvent cité comme l'un des meilleurs moments de la semaine."),
        4: ("11:00", "Vendredi matin, avant que l'attention ne se tourne vers le week-end."),
        5: ("11:00", "Week-end : plutôt en matinée, quand les gens consultent leur téléphone tranquillement."),
        6: ("12:00", "Dimanche midi : moment de consultation plus calme mais régulier."),
    },
    "instagram": {
        0: ("11:00", "Fin de matinée : créneau de consultation fréquent en début de semaine."),
        1: ("11:00", "Fin de matinée, l'un des moments les plus actifs sur Instagram."),
        2: ("11:00", "Milieu de semaine, fin de matinée : bon compromis portée/simplicité."),
        3: ("11:00", "Jeudi, même logique que le reste de la semaine."),
        4: ("13:00", "Vendredi : la pause de midi capte encore bien l'attention."),
        5: ("11:00", "Week-end, consultation un peu plus tardive dans la matinée."),
        6: ("11:00", "Dimanche, créneau calme mais fréquenté."),
    },
    "linkedin": {
        0: ("08:00", "Début de semaine, avant le début de la journée de travail : un moment classique sur LinkedIn."),
        1: ("09:00", "Mardi matin : l'un des jours les plus actifs sur ce réseau professionnel."),
        2: ("09:00", "Mercredi matin, même logique que le mardi."),
        3: ("08:00", "Jeudi, avant le début de journée."),
        4: ("09:00", "Vendredi matin, avant que l'attention professionnelle ne retombe."),
        5: (None, "LinkedIn est un réseau professionnel : l'audience y est nettement plus faible le week-end, mieux vaut viser un jour de semaine."),
        6: (None, "Même remarque que le samedi : privilégiez un jour de semaine pour LinkedIn."),
    },
    "wordpress": {
        None: (
            "09:00",
            "L'heure de publication d'un article de blog a peu d'effet direct : le matin laisse simplement le "
            "temps à Google de l'indexer dans la journée.",
        ),
    },
}


def suggestion_horaire(reseau: str, jour_semaine: int = None) -> tuple:
    """
    (heure_ou_None, note) : creneau indicatif pour ce reseau, au jour donne (0 = lundi ... 6 = dimanche). heure =
    None si le reseau/jour n'est pas recommande (la note explique pourquoi). ("", "") si le reseau est inconnu.
    """
    table = HORAIRES_RECOMMANDES.get(reseau)
    if not table:
        return None, ""
    if None in table:
        return table[None]
    return table.get(jour_semaine, (None, ""))
