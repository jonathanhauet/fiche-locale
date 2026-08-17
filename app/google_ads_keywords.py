"""
Volumes de recherche reels via Google Ads Keyword Planner (KeywordPlanIdeaService)
- complement au module google_autocomplete.py (suggestions gratuites mais sans
volume). Un seul compte Google Ads pour toute l'agence (voir
models.ParametreGoogleAds), pas un par client comme les comptes Business
Profile (google_oauth.py / models.CompteGoogle).
"""

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from . import google_oauth

GEO_TARGET_FRANCE = "geoTargetConstants/2250"
LANGUE_FRANCAIS = "languageConstants/1003"

# L'API refuse au-dela d'un certain nombre de mots-cles de depart par appel.
MAX_MOTS_CLES_SEMENCE = 10


def construire_client(parametre: "models.ParametreGoogleAds") -> GoogleAdsClient:
    configuration = {
        "developer_token": parametre.developer_token,
        "refresh_token": parametre.refresh_token,
        "client_id": google_oauth.CLIENT_ID,
        "client_secret": google_oauth.CLIENT_SECRET,
        "login_customer_id": parametre.customer_id,
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(configuration, version="v25")


def idees_mots_cles(parametre: "models.ParametreGoogleAds", mots_cles_semence: list[str]) -> list[dict]:
    """
    Renvoie [{"mot_cle": str, "volume_moyen_mensuel": int|None, "concurrence": str}, ...]
    pour les idees generees par Google a partir des mots-cles de depart
    (l'API en renvoie generalement bien plus que les semences fournies).
    """
    mots_cles_semence = [m.strip() for m in mots_cles_semence if m.strip()][:MAX_MOTS_CLES_SEMENCE]
    if not mots_cles_semence:
        return []

    client = construire_client(parametre)
    service = client.get_service("KeywordPlanIdeaService")

    requete = client.get_type("GenerateKeywordIdeasRequest")
    requete.customer_id = parametre.customer_id
    requete.language = LANGUE_FRANCAIS
    requete.geo_target_constants.append(GEO_TARGET_FRANCE)
    requete.include_adult_keywords = False
    requete.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
    requete.keyword_seed.keywords.extend(mots_cles_semence)

    reponse = service.generate_keyword_ideas(request=requete)

    niveau_concurrence = client.enums.KeywordPlanCompetitionLevelEnum

    resultats = []
    for idee in reponse:
        metriques = idee.keyword_idea_metrics
        resultats.append({
            "mot_cle": idee.text,
            "volume_moyen_mensuel": metriques.avg_monthly_searches or None,
            "concurrence": niveau_concurrence(metriques.competition).name if metriques.competition else "INCONNUE",
        })
    return resultats


def message_erreur_lisible(erreur: GoogleAdsException) -> str:
    details = "; ".join(e.message for e in erreur.failure.errors)
    return f"Google Ads a refuse la requete : {details}"
