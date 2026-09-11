"""
Mise en page PDF de l'audit prospect (voir audit_prospect.py) - reutilise la
classe/couleurs de base de rapport_pdf.py, mais avec une mise en page plus
soignee (cartes arrondies, tuiles de statistiques, grille de positions
dessinee, marges genereuses) pour un document destine a etre envoye tel quel
a un prospect.
"""

import io
import os
from datetime import date

from PIL import Image

from . import audit_carte, audit_prospect
from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _nettoyer

COULEUR_BON = (22, 163, 74)
COULEUR_BON_CLAIR = (234, 250, 240)
COULEUR_ATTENTION = (217, 138, 6)
COULEUR_ATTENTION_CLAIR = (255, 246, 224)
COULEUR_DANGER = (220, 38, 38)
COULEUR_DANGER_CLAIR = (253, 236, 236)
COULEUR_GRILLE_ABSENT = (203, 210, 221)
COULEUR_FOND_CARTE = (247, 248, 251)
COULEUR_BORDURE_CARTE = (226, 230, 238)

# Couleurs et polices reprises du site jonathanhauet.com pour les pages de
# couverture/contact - la seule touche "personal branding" du rapport, le
# reste des pages garde la palette bleue/neutre habituelle du document.
COULEUR_NAVY = (15, 23, 42)
COULEUR_NAVY_TEXTE_ATTENUE = (180, 190, 213)
COULEUR_AMBRE = (245, 166, 35)

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
CHEMIN_PHOTO_JONATHAN = os.path.join(DOSSIER_APP, "assets", "jonathan_photo.jpg")
CHEMIN_POLICE_SIGNATURE = os.path.join(DOSSIER_APP, "fonts", "DancingScript-Bold.ttf")

COORDONNEES_JONATHAN = {
    "email": "hello@jonathanhauet.fr",
    "site": "https://www.jonathanhauet.com",
    "linkedin": "https://www.linkedin.com/in/jonathan-hauet/",
    "youtube": "https://www.youtube.com/@jonathanhauet",
}

# Calcule une seule fois au chargement du module plutot qu'a chaque PDF genere.
try:
    with Image.open(CHEMIN_PHOTO_JONATHAN) as _photo:
        RATIO_PHOTO_JONATHAN = _photo.height / _photo.width
except Exception:
    RATIO_PHOTO_JONATHAN = None

# Marges laterales generees plus larges que le defaut fpdf2 (10mm) pour aerer
# la mise en page - point explicitement demande apres relecture du rendu.
MARGE_LATERALE = 18
RAYON_CARTE = 3.5
RAYON_TUILE = 2.5


def _couleurs_selon_couverture(pourcentage: int):
    if pourcentage >= 50:
        return COULEUR_BON, COULEUR_BON_CLAIR
    if pourcentage >= 20:
        return COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR
    return COULEUR_DANGER, COULEUR_DANGER_CLAIR


def _couleur_point(position):
    if position is None:
        return COULEUR_GRILLE_ABSENT
    if position <= 3:
        return COULEUR_BON
    if position <= 10:
        return COULEUR_ATTENTION
    return COULEUR_DANGER


def _couleur_score(score):
    if score is None:
        return COULEUR_GRIS
    if score >= 80:
        return COULEUR_BON
    if score >= 50:
        return COULEUR_ATTENTION
    return COULEUR_DANGER


def _assurer_espace(pdf: RapportPDF, hauteur: float):
    """
    Ajoute une nouvelle page si le bloc a dessiner (hauteur donnee) ne rentre
    pas dans l'espace restant. Necessaire avant tout encadre colore (rect())
    suivi de texte : rect() ne declenche jamais de saut de page (contrairement
    a cell()/multi_cell()), donc sans cette verification un encadre peut se
    retrouver dessine juste avant la marge basse pendant que le texte qui le
    remplit est renvoye par le saut de page automatique sur la page suivante -
    le cadre colore se retrouve alors vide en bas d'une page, le texte en haut
    de la suivante sans son cadre.
    """
    if pdf.y + hauteur > pdf.page_break_trigger:
        pdf.add_page()


def _titre_section(pdf: RapportPDF, texte: str, sous_titre: str = None):
    """Titre de section uniforme - fixe le rythme vertical entre les blocs du rapport."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 12.5)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, _nettoyer(texte), new_x="LMARGIN", new_y="NEXT")
    if sous_titre:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.multi_cell(0, 5, _nettoyer(sous_titre), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(2.5)


def _ligne_puce(pdf: RapportPDF, libelle: str, ok: bool):
    """Ligne a puce ronde coloree (remplace un caractere "+"/"!" par un vrai point visuel)."""
    couleur = COULEUR_BON if ok else COULEUR_ATTENTION
    x, y = pdf.l_margin, pdf.y
    pdf.set_fill_color(*couleur)
    pdf.ellipse(x + 1, y + 2, 3.2, 3.2, style="F")
    pdf.set_xy(x + 8, y)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 8, 6, _nettoyer(libelle), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _rangee_statistiques(pdf: RapportPDF, cases: list):
    """
    cases : [(libelle, valeur, couleur), ...] - rangee de tuiles de
    statistiques a fond arrondi, chiffre en avant, legende discrete en
    dessous. Reutilisee par la section technique du site et l'autorite du
    site (memes proportions, nombre de tuiles different).
    """
    largeur_totale = pdf.w - pdf.l_margin - pdf.r_margin
    marge_tuile = 3.5
    largeur_case = largeur_totale / len(cases)
    hauteur = 24

    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y

    for indice, (libelle, valeur, couleur) in enumerate(cases):
        x = pdf.l_margin + indice * largeur_case
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(
            x + marge_tuile / 2, y_debut, largeur_case - marge_tuile, hauteur,
            style="F", round_corners=True, corner_radius=RAYON_TUILE,
        )
        pdf.set_xy(x, y_debut + 5)
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(*couleur)
        pdf.cell(largeur_case, 8, _nettoyer(valeur), align="C")
        pdf.set_xy(x, y_debut + 15)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(largeur_case, 5, _nettoyer(libelle), align="C")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 8)


def _bandeau_titre(pdf: RapportPDF, nom_entreprise: str, ville: str):
    """
    En-tete sans bloc de couleur plein (remplace par une etiquette "eyebrow"
    en petites capitales espacees + une regle fine) - plus actuel qu'un
    bandeau colore epais, et laisse plus d'air en haut de page.
    """
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 6, " ".join("AUDIT DE VISIBILITE LOCALE"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 23)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 13, _nettoyer(nom_entreprise), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 6, f"{_nettoyer(ville)} - genere le {date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_draw_color(*COULEUR_ACCENT)
    pdf.set_line_width(0.7)
    pdf.line(pdf.l_margin, pdf.y, pdf.w - pdf.r_margin, pdf.y)
    pdf.set_line_width(0.2)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(9)


def _couleurs_score_global(score: int):
    if score >= 70:
        return COULEUR_BON, COULEUR_BON_CLAIR
    if score >= 40:
        return COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR
    return COULEUR_DANGER, COULEUR_DANGER_CLAIR


def _libelle_score_global(score: int) -> str:
    if score >= 70:
        return "Bonne visibilite locale d'ensemble"
    if score >= 40:
        return "Visibilite locale correcte, des points a ameliorer"
    return "Visibilite locale a renforcer en priorite"


def _bandeau_score_global(pdf: RapportPDF, score: int):
    if score is None:
        return

    couleur, couleur_claire = _couleurs_score_global(score)
    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    hauteur = 26
    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y
    pdf.set_fill_color(*couleur_claire)
    pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="F", round_corners=True, corner_radius=RAYON_CARTE)

    pdf.set_xy(pdf.l_margin + 8, y_debut + 5)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 6, "Score global de visibilite locale", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 8)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*couleur)
    texte_score = f"{score}/100"
    pdf.cell(pdf.get_string_width(texte_score) + 5, 11, texte_score)

    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 11, "  " + _nettoyer(_libelle_score_global(score)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 9)


def _carte_fiche_google(pdf: RapportPDF, fiche: dict):
    lignes = []
    if fiche.get("trouve"):
        note = fiche.get("note")
        nb_avis = fiche.get("nombre_avis")
        lignes.append(("Nom de la fiche", fiche.get("titre") or ""))
        if note:
            lignes.append(("Note moyenne", f"{note}/5" + (f" ({nb_avis} avis)" if nb_avis else "")))
        if fiche.get("categorie"):
            lignes.append(("Categorie", fiche["categorie"]))
        if fiche.get("adresse"):
            lignes.append(("Adresse", fiche["adresse"]))
        if fiche.get("telephone"):
            lignes.append(("Telephone", fiche["telephone"]))
        if fiche.get("site_web"):
            lignes.append(("Site web", fiche["site_web"]))
    else:
        lignes.append((None, "Fiche non retrouvee automatiquement sur Google Maps avec ce nom et cette ville."))

    hauteur = 13 + len(lignes) * 8
    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y
    pdf.set_fill_color(*COULEUR_FOND_CARTE)
    pdf.rect(
        pdf.l_margin, y_debut, pdf.w - pdf.l_margin - pdf.r_margin, hauteur,
        style="F", round_corners=True, corner_radius=RAYON_CARTE,
    )

    pdf.set_xy(pdf.l_margin + 8, y_debut + 6)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 7, "Fiche Google", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    for libelle, valeur in lignes:
        pdf.set_x(pdf.l_margin + 8)
        if libelle:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.cell(50, 8, libelle)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*COULEUR_TEXTE)
            pdf.cell(0, 8, _nettoyer(str(valeur))[:70], new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 16, 6, _nettoyer(valeur), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 9)


def _tableau_completude(pdf: RapportPDF, items: list):
    if not items:
        return

    _titre_section(pdf, "Completude de la fiche")

    largeur_totale = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_puce = 34
    hauteur_ligne = 11

    for item in items:
        couleur, couleur_claire = (COULEUR_BON, COULEUR_BON_CLAIR) if item["complet"] else (COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR)
        libelle_puce = "Complet" if item["complet"] else "A ameliorer"

        _assurer_espace(pdf, hauteur_ligne + 2)
        y_debut = pdf.y
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(pdf.l_margin, y_debut, largeur_totale, hauteur_ligne, style="F", round_corners=True, corner_radius=RAYON_TUILE)

        pdf.set_xy(pdf.l_margin + 6, y_debut + 2.3)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(50, 6.5, _nettoyer(item["libelle"]))

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(largeur_totale - 50 - largeur_puce - 10, 6.5, _nettoyer(item["detail"])[:72])

        pdf.set_fill_color(*couleur_claire)
        pdf.set_xy(pdf.l_margin + largeur_totale - largeur_puce - 5, y_debut + 2.3)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*couleur)
        pdf.cell(largeur_puce, 6.5, libelle_puce, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.set_y(y_debut + hauteur_ligne + 2)

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(6)


def _carte_site_technique(pdf: RapportPDF, resultat: dict):
    _titre_section(pdf, "Votre site face a Google", resultat["url"])

    cases = [
        ("Performance", f"{resultat['score_performance']}/100" if resultat["score_performance"] is not None else "-", _couleur_score(resultat["score_performance"])),
        ("SEO", f"{resultat['score_seo']}/100" if resultat["score_seo"] is not None else "-", _couleur_score(resultat["score_seo"])),
        ("1er affichage", resultat["premier_affichage"] or "-", COULEUR_TEXTE),
        ("Affichage complet", resultat["affichage_complet"] or "-", COULEUR_TEXTE),
    ]
    _rangee_statistiques(pdf, cases)

    if resultat["points_bloquants"]:
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Ce qui freine votre site", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        for point in resultat["points_bloquants"]:
            _ligne_puce(pdf, point["libelle"], ok=False)
        pdf.ln(2)

    if resultat["points_positifs"]:
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Ce qui fonctionne deja", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        for point in resultat["points_positifs"]:
            _ligne_puce(pdf, point["libelle"], ok=True)

    pdf.ln(5)


def _carte_autorite_site(pdf: RapportPDF, resultat: dict):
    _titre_section(pdf, "Autorite du site")

    cases = [
        ("Score d'autorite", f"{resultat['rang']}/100", _couleur_score(resultat["rang"])),
        ("Backlinks", str(resultat["backlinks"]), COULEUR_TEXTE),
        ("Domaines referents", str(resultat["domaines_referents"]), COULEUR_TEXTE),
    ]
    _rangee_statistiques(pdf, cases)
    pdf.ln(3)


def _carte_citations(pdf: RapportPDF, resultats: list):
    resultats_valides = [r for r in resultats if r.get("erreur") is None]
    if not resultats_valides:
        return

    _titre_section(pdf, "Presence sur les annuaires locaux")

    for resultat in resultats_valides:
        libelle = f"{resultat['nom']} : " + ("fiche trouvee" if resultat["trouve"] else "aucune fiche trouvee")
        _ligne_puce(pdf, libelle, ok=resultat["trouve"])

    pdf.ln(4)


def _tableau_opportunites_mots_cles(pdf: RapportPDF, idees: list):
    if not idees:
        return

    _titre_section(pdf, "Opportunites de mots-cles a exploiter", "Recherches Google reelles, non testees dans cet audit.")

    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_volume = 55

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_x(pdf.l_margin)
    pdf.cell(largeur - largeur_volume, 9, "   Mot-cle", border=0, fill=True)
    pdf.cell(largeur_volume, 9, "Volume mensuel estime", border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COULEUR_TEXTE)
    for indice, idee in enumerate(idees):
        pdf.set_x(pdf.l_margin)
        fill = bool(indice % 2)
        if fill:
            pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.cell(largeur - largeur_volume, 9, "   " + _nettoyer(idee["mot_cle"])[:58], border=0, fill=fill)
        pdf.cell(
            largeur_volume, 9, f"{idee['volume_moyen_mensuel']}/mois",
            border=0, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT",
        )

    pdf.ln(7)


def _carte_plan_action(pdf: RapportPDF, priorites: list):
    if not priorites:
        return

    _titre_section(pdf, "Plan d'action : vos 3 priorites")

    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_texte = largeur - 24

    for indice, priorite in enumerate(priorites, start=1):
        pdf.set_font("Helvetica", "", 9.5)
        lignes = pdf.multi_cell(largeur_texte, 5.8, _nettoyer(priorite["description"]), dry_run=True, output="LINES")
        hauteur = 14 + len(lignes) * 5.8 + 6

        _assurer_espace(pdf, hauteur)
        y_debut = pdf.y
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="F", round_corners=True, corner_radius=RAYON_CARTE)

        pdf.set_fill_color(*COULEUR_ACCENT)
        pdf.ellipse(pdf.l_margin + 7, y_debut + 6, 8.5, 8.5, style="F")
        pdf.set_xy(pdf.l_margin + 7, y_debut + 6)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(8.5, 8.5, str(indice), align="C")

        pdf.set_xy(pdf.l_margin + 20, y_debut + 6)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(largeur_texte, 6, _nettoyer(priorite["titre"]), new_x="LMARGIN", new_y="NEXT")

        pdf.set_xy(pdf.l_margin + 20, pdf.y + 1)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.multi_cell(largeur_texte, 5.8, _nettoyer(priorite["description"]), new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.set_y(y_debut + hauteur + 6)

    pdf.ln(2)


def _encadre_verdict(pdf: RapportPDF, mot_cle: str, resume: dict):
    couleur, couleur_claire = _couleurs_selon_couverture(resume["pourcentage_couverture"])
    if resume["pourcentage_couverture"] >= 50:
        verdict = "Bonne visibilite sur ce mot-cle"
    elif resume["pourcentage_couverture"] >= 20:
        verdict = "Visibilite partielle, il y a de la marge"
    else:
        verdict = "Visibilite tres faible sur ce mot-cle"

    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    hauteur = 28
    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y
    pdf.set_fill_color(*couleur_claire)
    pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="F", round_corners=True, corner_radius=RAYON_CARTE)

    pdf.set_xy(pdf.l_margin + 8, y_debut + 5)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 6, _nettoyer(f'Visibilite locale : "{mot_cle}"'), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 8)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*couleur)
    texte_pct = f"{resume['pourcentage_couverture']}%"
    pdf.cell(pdf.get_string_width(texte_pct) + 5, 11, texte_pct)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 11, "  " + _nettoyer(
        f"{verdict} ({resume['points_trouves']}/{resume['total_points']} points de la zone testee)"
    ), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 8)


def _legende_positions(pdf: RapportPDF):
    legende = [
        (COULEUR_BON, "Top 3"), (COULEUR_ATTENTION, "Top 4-10"),
        (COULEUR_DANGER, "Au-dela / non classee"), (COULEUR_GRILLE_ABSENT, "Non trouvee"),
    ]
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 8)
    for couleur, libelle in legende:
        pdf.set_fill_color(*couleur)
        x, y = pdf.get_x(), pdf.get_y()
        pdf.rect(x, y + 1, 3, 3, style="F", round_corners=True, corner_radius=0.7)
        pdf.set_x(x + 5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(pdf.get_string_width(libelle) + 7, 5, libelle)
    pdf.ln(9)
    pdf.set_text_color(*COULEUR_TEXTE)


def _carte_reelle_positions(pdf: RapportPDF, points: list) -> bool:
    """
    Tente d'inserer une vraie carte (fond OpenStreetMap, voir audit_carte.py)
    avec un marqueur colore par point de la grille - beaucoup plus parlant
    qu'une grille abstraite pour comprendre quelle zone geographique reelle a
    ete testee. Renvoie False sans rien dessiner si le rendu a echoue (ex.
    tuiles injoignables), pour laisser l'appelant retomber sur la grille
    abstraite : jamais d'echec bloquant pour la generation du PDF.
    """
    try:
        octets_image = audit_carte.generer_image_carte(points)
    except Exception:
        return False

    taille_mm = min(pdf.w - pdf.l_margin - pdf.r_margin, 115)
    _assurer_espace(pdf, taille_mm + 14)
    x = pdf.l_margin + (pdf.w - pdf.l_margin - pdf.r_margin - taille_mm) / 2
    y = pdf.y
    pdf.image(io.BytesIO(octets_image), x=x, y=y, w=taille_mm, h=taille_mm)
    pdf.set_y(y + taille_mm + 5)
    return True


def _grille_visuelle(pdf: RapportPDF, points: list):
    """Repli abstrait (carres colores) quand la vraie carte n'a pas pu etre generee."""
    taille = int(round(len(points) ** 0.5))
    if taille * taille != len(points) or taille == 0:
        return  # forme inattendue, on saute plutot que d'afficher une grille fausse

    cote = 9.5
    marge_case = 1.8
    largeur_totale = taille * (cote + marge_case) - marge_case
    # +14 pour la legende dessinee juste apres (voir _section_carte_positions).
    _assurer_espace(pdf, largeur_totale + 14)
    x_debut = pdf.l_margin + (pdf.w - pdf.l_margin - pdf.r_margin - largeur_totale) / 2
    y_debut = pdf.y

    for indice, point in enumerate(points):
        ligne, colonne = divmod(indice, taille)
        x = x_debut + colonne * (cote + marge_case)
        y = y_debut + ligne * (cote + marge_case)
        pdf.set_fill_color(*_couleur_point(point.get("position")))
        pdf.rect(x, y, cote, cote, style="F", round_corners=True, corner_radius=1.8)
        if point.get("position") and point["position"] <= 10:
            pdf.set_xy(x, y + 1.9)
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(cote, 6, str(point["position"]), align="C")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + taille * (cote + marge_case) + 4)


def _section_carte_positions(pdf: RapportPDF, points: list):
    if not _carte_reelle_positions(pdf, points):
        _grille_visuelle(pdf, points)
    _legende_positions(pdf)


def _tableau_concurrents(pdf: RapportPDF, concurrents: list, fiche: dict = None, resume: dict = None):
    fiche_comparable = bool(fiche and fiche.get("trouve") and resume)
    if not concurrents and not fiche_comparable:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Aucun concurrent identifie sur ce mot-cle a proximite.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)
        return

    largeur_nom, largeur_note, largeur_avis, largeur_position = 58, 22, 22, 30

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_x(pdf.l_margin)
    pdf.cell(largeur_nom, 9, "   Entreprise", border=0, fill=True)
    pdf.cell(largeur_note, 9, "Note", border=0, fill=True, align="C")
    pdf.cell(largeur_avis, 9, "Avis", border=0, fill=True, align="C")
    pdf.cell(largeur_position, 9, "Position moy.", border=0, fill=True, align="C")
    pdf.cell(0, 9, "Presence sur la zone", border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    indice = 0

    if fiche_comparable:
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(*COULEUR_BON_CLAIR)
        pdf.set_font("Helvetica", "B", 9)
        nom_vous = _nettoyer(fiche.get("titre") or "Votre entreprise")[:26] + " (vous)"
        pdf.cell(largeur_nom, 9, "   " + nom_vous, border=0, fill=True)
        pdf.cell(largeur_note, 9, f"{fiche['note']}/5" if fiche.get("note") else "-", border=0, fill=True, align="C")
        pdf.cell(largeur_avis, 9, str(fiche["nombre_avis"]) if fiche.get("nombre_avis") else "-", border=0, fill=True, align="C")
        pdf.cell(
            largeur_position, 9, f"#{resume['position_moyenne']}" if resume.get("position_moyenne") else "-",
            border=0, fill=True, align="C",
        )
        pdf.cell(
            0, 9, f"{resume['pourcentage_couverture']}%",
            border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT",
        )
        indice = 1

    pdf.set_font("Helvetica", "", 9)
    for concurrent in concurrents:
        pdf.set_x(pdf.l_margin)
        fill = bool(indice % 2)
        if fill:
            pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.cell(largeur_nom, 9, "   " + _nettoyer(concurrent["nom"])[:32], border=0, fill=fill)
        pdf.cell(largeur_note, 9, f"{concurrent['note']}/5" if concurrent.get("note") else "-", border=0, fill=fill, align="C")
        pdf.cell(
            largeur_avis, 9, str(concurrent["nombre_avis"]) if concurrent.get("nombre_avis") else "-",
            border=0, fill=fill, align="C",
        )
        pdf.cell(largeur_position, 9, f"#{concurrent['position_moyenne']}", border=0, fill=fill, align="C")
        pdf.cell(0, 9, f"{concurrent['presence']}%", border=0, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT")
        indice += 1


def _recommandations(resume: dict) -> list:
    """Constats generes par une regle simple sur les chiffres du releve - pas d'IA ici, pour ne rien ajouter au cout de l'audit."""
    constats = []
    pourcentage = resume["pourcentage_couverture"]
    if pourcentage < 20:
        constats.append(
            "La grande majorite de la zone testee ne fait pas remonter la fiche dans les premiers "
            "resultats Google Maps sur ce mot-cle."
        )
    elif pourcentage < 50:
        constats.append("Une partie significative de la zone testee ne fait pas remonter la fiche.")
    else:
        constats.append("La visibilite est deja correcte sur la zone testee.")

    if resume["position_moyenne"] and resume["position_moyenne"] > 5:
        constats.append(
            f"Quand la fiche apparait, elle se situe en moyenne en position {resume['position_moyenne']} "
            "- au-dela des tout premiers resultats consultes par les internautes."
        )
    return constats


def _activer_police_signature(pdf: RapportPDF) -> bool:
    """
    Enregistre la police script (signature personnelle) une fois pour tout le
    document. Renvoie False si le fichier est indisponible - les pages
    couverture/contact retombent alors sur Helvetica italique.
    """
    try:
        pdf.add_font("DancingScript", "", CHEMIN_POLICE_SIGNATURE)
        return True
    except Exception:
        return False


def _photo_jonathan(pdf: RapportPDF, largeur: float):
    """Colle la photo de Jonathan (fond navy) en bas a droite de la page courante, bord a bord."""
    if RATIO_PHOTO_JONATHAN is None:
        return
    try:
        hauteur = largeur * RATIO_PHOTO_JONATHAN
        pdf.image(CHEMIN_PHOTO_JONATHAN, x=pdf.w - largeur, y=pdf.h - hauteur, w=largeur)
    except Exception:
        pass  # photo omise si le fichier est illisible, ne bloque jamais la generation du PDF


def _page_couverture(pdf: RapportPDF, nom_entreprise: str, ville: str, avis_jonathan: dict, police_signature_ok: bool):
    """
    Page de garde personnalisee (fond navy, palette reprise du site
    jonathanhauet.com) - met en avant que l'audit est realise par Jonathan
    lui-meme, avec sa photo et, quand disponible, la note de sa propre fiche
    Google comme preuve sociale ("il applique ce qu'il recommande").
    """
    pdf.add_page()
    pdf.set_fill_color(*COULEUR_NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, style="F")

    largeur_photo = 100
    _photo_jonathan(pdf, largeur_photo)
    largeur_texte = pdf.w - largeur_photo - MARGE_LATERALE - 10

    pdf.set_xy(MARGE_LATERALE, 34)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 6, " ".join("AUDIT DE VISIBILITE LOCALE"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_x(MARGE_LATERALE)
    pdf.set_font("Helvetica", "B", 27)
    pdf.set_text_color(255, 255, 255)
    pdf.multi_cell(largeur_texte, 13, _nettoyer(nom_entreprise), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(MARGE_LATERALE)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*COULEUR_NAVY_TEXTE_ATTENUE)
    pdf.cell(0, 7, _nettoyer(f"{ville} - genere le {date.today().strftime('%d/%m/%Y')}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(MARGE_LATERALE, pdf.h - 96)
    if police_signature_ok:
        pdf.set_font("DancingScript", "", 27)
    else:
        pdf.set_font("Helvetica", "I", 15)
    pdf.set_text_color(*COULEUR_AMBRE)
    # multi_cell (pas cell) : la police script est large, le texte doit
    # pouvoir passer sur 2 lignes sans deborder sur la photo a droite.
    pdf.multi_cell(largeur_texte, 12, "Un audit realise par Jonathan Hauet", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(MARGE_LATERALE)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*COULEUR_NAVY_TEXTE_ATTENUE)
    pdf.multi_cell(
        largeur_texte, 5.5, _nettoyer("Expert Produit Google et specialiste du referencement local"),
        new_x="LMARGIN", new_y="NEXT",
    )

    if avis_jonathan and avis_jonathan.get("note") is not None:
        pdf.ln(6)
        note_texte = f"{avis_jonathan['note']:.1f}".replace(".", ",")
        texte = f"Sa fiche Google : {note_texte}/5 ({avis_jonathan['nombre_avis']} avis)"
        pdf.set_font("Helvetica", "B", 9.5)
        largeur_badge = min(pdf.get_string_width(_nettoyer(texte)) + 14, largeur_texte)
        y_badge = pdf.y
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(MARGE_LATERALE, y_badge, largeur_badge, 10, style="F", round_corners=True, corner_radius=5)
        pdf.set_xy(MARGE_LATERALE, y_badge + 2.2)
        pdf.set_text_color(*COULEUR_NAVY)
        pdf.cell(largeur_badge, 6, _nettoyer(texte), align="C")

    pdf.set_text_color(*COULEUR_TEXTE)


def _page_contact(pdf: RapportPDF, police_signature_ok: bool):
    """Derniere page (fond navy) : invite au contact avec les coordonnees de Jonathan, liens cliquables."""
    pdf.add_page()
    pdf.set_fill_color(*COULEUR_NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, style="F")

    largeur_photo = 78
    _photo_jonathan(pdf, largeur_photo)
    largeur_texte = pdf.w - largeur_photo - MARGE_LATERALE - 10

    pdf.set_xy(MARGE_LATERALE, 40)
    if police_signature_ok:
        pdf.set_font("DancingScript", "", 30)
    else:
        pdf.set_font("Helvetica", "I", 19)
    pdf.set_text_color(*COULEUR_AMBRE)
    pdf.cell(largeur_texte, 14, "Envie d'en discuter ?", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_x(MARGE_LATERALE)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*COULEUR_NAVY_TEXTE_ATTENUE)
    pdf.multi_cell(
        largeur_texte, 6,
        _nettoyer(
            "Cet audit n'est qu'un point de depart. Si vous voulez qu'on regarde ensemble comment "
            "ameliorer concretement votre visibilite locale, contactez-moi."
        ),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(10)

    liens = [
        ("Email", COORDONNEES_JONATHAN["email"], f"mailto:{COORDONNEES_JONATHAN['email']}"),
        ("Site web", "jonathanhauet.com", COORDONNEES_JONATHAN["site"]),
        ("LinkedIn", "linkedin.com/in/jonathan-hauet", COORDONNEES_JONATHAN["linkedin"]),
        ("YouTube", "youtube.com/@jonathanhauet", COORDONNEES_JONATHAN["youtube"]),
    ]
    for libelle, texte_affiche, url in liens:
        pdf.set_x(MARGE_LATERALE)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COULEUR_AMBRE)
        pdf.cell(26, 8, libelle)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(largeur_texte - 26, 8, texte_affiche, link=url, new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)


def generer_audit_prospect_pdf(
    nom_entreprise: str, ville: str, fiche: dict, releves: list,
    analyse_site: dict = None, plan_action: list = None,
    citations_resultats: list = None, opportunites_mots_cles: list = None,
    autorite_site: dict = None, avis_jonathan: dict = None,
) -> bytes:
    """
    fiche : voir audit_prospect.rechercher_fiche_publique()
    releves : liste de resultats audit_prospect.grille_positions_prospect() (un par mot-cle)
    analyse_site : voir audit_site_technique.analyser_site(), optionnel (absent si pas de site
    web renseigne ou si l'analyse a echoue - section simplement omise).
    plan_action : voir claude_generation.generer_plan_action_audit(), optionnel (absent si la
    generation IA a echoue - section simplement omise).
    citations_resultats : voir citations.verifier_citations(), optionnel (absent si non verifie
    ou si l'appel a echoue - section simplement omise).
    opportunites_mots_cles : voir google_ads_keywords.idees_mots_cles(), optionnel (absent si
    Google Ads non configure ou si l'appel a echoue - section simplement omise).
    autorite_site : voir audit_backlinks.analyser_autorite(), optionnel (absent si pas de site
    web renseigne ou si l'appel a echoue - section simplement omise).
    avis_jonathan : voir main._avis_jonathan(), optionnel (absent si la fiche de Jonathan n'est
    pas configuree ou si l'appel a echoue - le badge de preuve sociale est simplement omis).
    """
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_margins(MARGE_LATERALE, 16, MARGE_LATERALE)
    pdf.set_auto_page_break(auto=True, margin=24)

    police_signature_ok = _activer_police_signature(pdf)
    _page_couverture(pdf, nom_entreprise, ville, avis_jonathan, police_signature_ok)

    pdf.add_page()

    items_completude = audit_prospect.evaluer_completude_fiche(fiche)
    score_global = audit_prospect.calculer_score_global(items_completude, analyse_site, releves, autorite_site)

    _bandeau_titre(pdf, nom_entreprise, ville)
    _bandeau_score_global(pdf, score_global)
    _carte_fiche_google(pdf, fiche)
    _tableau_completude(pdf, items_completude)

    if analyse_site:
        _carte_site_technique(pdf, analyse_site)

    if autorite_site:
        _carte_autorite_site(pdf, autorite_site)

    if citations_resultats:
        _carte_citations(pdf, citations_resultats)

    for releve in releves:
        resume = releve["resume"]
        _encadre_verdict(pdf, releve["mot_cle"], resume)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Carte de positionnement", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.cell(
            0, 5, f"Zone testee : rayon d'environ {audit_prospect.RAYON_KM_DEFAUT:g} km autour de la fiche",
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.ln(3)
        _section_carte_positions(pdf, releve["points"])

        pdf.set_font("Helvetica", "", 9.5)
        for constat in _recommandations(resume):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 5.5, _nettoyer(f"- {constat}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, "Qui ressort devant elle sur ce mot-cle", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        _tableau_concurrents(pdf, releve["concurrents"], fiche, resume)
        pdf.ln(8)

    if opportunites_mots_cles:
        _tableau_opportunites_mots_cles(pdf, opportunites_mots_cles)

    if plan_action:
        _carte_plan_action(pdf, plan_action)

    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0, 5,
        _nettoyer(
            "Cet audit s'appuie sur des donnees publiques Google Maps (recherche geolocalisee), sans acces "
            "autorise a la fiche : certaines informations (photos, historique des publications) ne peuvent "
            "donc pas y figurer."
        ),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*COULEUR_TEXTE)

    _page_contact(pdf, police_signature_ok)

    return bytes(pdf.output())
