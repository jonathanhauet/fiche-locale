"""
Mise en page PDF de l'audit prospect (voir audit_prospect.py) - reutilise la
classe/couleurs de base de rapport_pdf.py, mais avec une mise en page plus
soignee (bandeau de couverture, encadres de verdict, grille de positions
dessinee) pour un document destine a etre envoye tel quel a un prospect.
"""

from datetime import date

from . import audit_prospect
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


def _bandeau_titre(pdf: RapportPDF, nom_entreprise: str, ville: str):
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.rect(0, 0, pdf.w, 40, style="F")

    pdf.set_xy(pdf.l_margin, 10)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, "AUDIT DE VISIBILITE LOCALE", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 19)
    pdf.cell(0, 10, _nettoyer(nom_entreprise), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(230, 233, 250)
    pdf.cell(0, 6, f"{_nettoyer(ville)} - genere le {date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(48)


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
    y_debut = pdf.y
    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    hauteur = 22
    pdf.set_fill_color(*couleur_claire)
    pdf.set_draw_color(*couleur)
    pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="DF")

    pdf.set_xy(pdf.l_margin + 6, y_debut + 4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 6, "Score global de visibilite locale", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*couleur)
    texte_score = f"{score}/100"
    pdf.cell(pdf.get_string_width(texte_score) + 4, 10, texte_score)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 10, "  " + _nettoyer(_libelle_score_global(score)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 6)


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

    hauteur = 10 + len(lignes) * 7 + 4
    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y
    pdf.set_draw_color(*COULEUR_BORDURE_CARTE)
    pdf.set_fill_color(*COULEUR_FOND_CARTE)
    pdf.rect(pdf.l_margin, y_debut, pdf.w - pdf.l_margin - pdf.r_margin, hauteur, style="DF")

    pdf.set_xy(pdf.l_margin + 6, y_debut + 4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 7, "Fiche Google", new_x="LMARGIN", new_y="NEXT")

    for libelle, valeur in lignes:
        pdf.set_x(pdf.l_margin + 6)
        if libelle:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.cell(48, 7, libelle)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*COULEUR_TEXTE)
            pdf.cell(0, 7, _nettoyer(str(valeur))[:70], new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 12, 6, _nettoyer(valeur), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 6)


def _tableau_completude(pdf: RapportPDF, items: list):
    if not items:
        return

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, "Completude de la fiche", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    largeur_totale = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_puce = 32

    for indice, item in enumerate(items):
        couleur, couleur_claire = (COULEUR_BON, COULEUR_BON_CLAIR) if item["complet"] else (COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR)
        libelle_puce = "Complet" if item["complet"] else "A ameliorer"

        _assurer_espace(pdf, 9)
        y_debut = pdf.y
        pdf.set_draw_color(*COULEUR_BORDURE_CARTE)
        pdf.set_fill_color(*COULEUR_FOND_CARTE if indice % 2 else (255, 255, 255))
        pdf.rect(pdf.l_margin, y_debut, largeur_totale, 9, style="F")

        pdf.set_xy(pdf.l_margin + 3, y_debut + 1.3)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(50, 6.5, _nettoyer(item["libelle"]))

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(largeur_totale - 50 - largeur_puce - 6, 6.5, _nettoyer(item["detail"])[:75])

        pdf.set_fill_color(*couleur_claire)
        pdf.set_xy(pdf.l_margin + largeur_totale - largeur_puce, y_debut + 1.3)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*couleur)
        pdf.cell(largeur_puce, 6.5, libelle_puce, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

        pdf.set_y(y_debut + 9)

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(6)


def _couleur_score(score):
    if score is None:
        return COULEUR_GRIS
    if score >= 80:
        return COULEUR_BON
    if score >= 50:
        return COULEUR_ATTENTION
    return COULEUR_DANGER


def _ligne_point_technique(pdf: RapportPDF, libelle: str, ok: bool):
    couleur = COULEUR_BON if ok else COULEUR_ATTENTION
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*couleur)
    pdf.cell(6, 6, "+" if ok else "!")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 6, 6, _nettoyer(libelle), new_x="LMARGIN", new_y="NEXT")


def _carte_site_technique(pdf: RapportPDF, resultat: dict):
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, "Votre site face a Google", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 5, _nettoyer(resultat["url"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    largeur_totale = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_case = largeur_totale / 4
    y_debut = pdf.y
    cases = [
        ("Performance", f"{resultat['score_performance']}/100" if resultat["score_performance"] is not None else "-", _couleur_score(resultat["score_performance"])),
        ("SEO", f"{resultat['score_seo']}/100" if resultat["score_seo"] is not None else "-", _couleur_score(resultat["score_seo"])),
        ("1er affichage", resultat["premier_affichage"] or "-", COULEUR_TEXTE),
        ("Affichage complet", resultat["affichage_complet"] or "-", COULEUR_TEXTE),
    ]
    for indice, (libelle, valeur, couleur) in enumerate(cases):
        x = pdf.l_margin + indice * largeur_case
        pdf.set_xy(x, y_debut)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*couleur)
        pdf.cell(largeur_case, 8, _nettoyer(valeur), align="C", new_x="LMARGIN", new_y="TOP")
        pdf.set_xy(x, y_debut + 8)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(largeur_case, 5, _nettoyer(libelle), align="C")
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + 16)

    if resultat["points_bloquants"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Ce qui freine votre site", new_x="LMARGIN", new_y="NEXT")
        for point in resultat["points_bloquants"]:
            _ligne_point_technique(pdf, point["libelle"], ok=False)

    if resultat["points_positifs"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Ce qui fonctionne deja", new_x="LMARGIN", new_y="NEXT")
        for point in resultat["points_positifs"]:
            _ligne_point_technique(pdf, point["libelle"], ok=True)

    pdf.ln(4)


def _carte_autorite_site(pdf: RapportPDF, resultat: dict):
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, "Autorite du site", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    largeur_totale = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_case = largeur_totale / 3
    y_debut = pdf.y
    cases = [
        ("Score d'autorite", f"{resultat['rang']}/100", _couleur_score(resultat["rang"])),
        ("Backlinks", str(resultat["backlinks"]), COULEUR_TEXTE),
        ("Domaines referents", str(resultat["domaines_referents"]), COULEUR_TEXTE),
    ]
    for indice, (libelle, valeur, couleur) in enumerate(cases):
        x = pdf.l_margin + indice * largeur_case
        pdf.set_xy(x, y_debut)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*couleur)
        pdf.cell(largeur_case, 8, _nettoyer(valeur), align="C", new_x="LMARGIN", new_y="TOP")
        pdf.set_xy(x, y_debut + 8)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(largeur_case, 5, _nettoyer(libelle), align="C")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + 16)
    pdf.ln(4)


def _carte_citations(pdf: RapportPDF, resultats: list):
    resultats_valides = [r for r in resultats if r.get("erreur") is None]
    if not resultats_valides:
        return

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, "Presence sur les annuaires locaux", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    for resultat in resultats_valides:
        libelle = f"{resultat['nom']} : " + ("fiche trouvee" if resultat["trouve"] else "aucune fiche trouvee")
        _ligne_point_technique(pdf, libelle, ok=resultat["trouve"])

    pdf.ln(4)


def _tableau_opportunites_mots_cles(pdf: RapportPDF, idees: list):
    if not idees:
        return

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 8, "Opportunites de mots-cles a exploiter", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 5, _nettoyer("Recherches Google reelles, non testees dans cet audit."), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_volume = 55

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_x(pdf.l_margin)
    pdf.cell(largeur - largeur_volume, 8, "  Mot-cle", border=0, fill=True)
    pdf.cell(largeur_volume, 8, "Volume mensuel estime", border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COULEUR_TEXTE)
    for indice, idee in enumerate(idees):
        pdf.set_x(pdf.l_margin)
        fill = bool(indice % 2)
        if fill:
            pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.cell(largeur - largeur_volume, 7.5, "  " + _nettoyer(idee["mot_cle"])[:60], border=0, fill=fill)
        pdf.cell(
            largeur_volume, 7.5, f"{idee['volume_moyen_mensuel']}/mois",
            border=0, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT",
        )

    pdf.ln(6)


def _carte_plan_action(pdf: RapportPDF, priorites: list):
    if not priorites:
        return

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 9, "Plan d'action : vos 3 priorites", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    largeur_texte = largeur - 20

    for indice, priorite in enumerate(priorites, start=1):
        pdf.set_font("Helvetica", "", 9.5)
        lignes = pdf.multi_cell(largeur_texte, 5.5, _nettoyer(priorite["description"]), dry_run=True, output="LINES")
        hauteur = 12 + len(lignes) * 5.5 + 5

        _assurer_espace(pdf, hauteur)
        y_debut = pdf.y
        pdf.set_draw_color(*COULEUR_BORDURE_CARTE)
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="DF")

        pdf.set_fill_color(*COULEUR_ACCENT)
        pdf.ellipse(pdf.l_margin + 5, y_debut + 4, 8, 8, style="F")
        pdf.set_xy(pdf.l_margin + 5, y_debut + 4)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(8, 8, str(indice), align="C")

        pdf.set_xy(pdf.l_margin + 17, y_debut + 4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(largeur_texte, 6, _nettoyer(priorite["titre"]), new_x="LMARGIN", new_y="NEXT")

        pdf.set_xy(pdf.l_margin + 17, pdf.y)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.multi_cell(largeur_texte, 5.5, _nettoyer(priorite["description"]), new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.set_y(y_debut + hauteur + 5)

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
    hauteur = 26
    _assurer_espace(pdf, hauteur)
    y_debut = pdf.y
    pdf.set_fill_color(*couleur_claire)
    pdf.set_draw_color(*couleur)
    pdf.rect(pdf.l_margin, y_debut, largeur, hauteur, style="DF")

    pdf.set_xy(pdf.l_margin + 6, y_debut + 4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 6, _nettoyer(f'Visibilite locale : "{mot_cle}"'), new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*couleur)
    texte_pct = f"{resume['pourcentage_couverture']}%"
    pdf.cell(pdf.get_string_width(texte_pct) + 4, 10, texte_pct)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 10, _nettoyer(
        f"  {verdict} ({resume['points_trouves']}/{resume['total_points']} points de la zone testee)"
    ), new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + hauteur + 5)


def _grille_visuelle(pdf: RapportPDF, points: list):
    taille = int(round(len(points) ** 0.5))
    if taille * taille != len(points) or taille == 0:
        return  # forme inattendue, on saute plutot que d'afficher une grille fausse

    cote = 9.0
    marge_case = 1.2
    largeur_totale = taille * (cote + marge_case) - marge_case
    # +13 pour la legende dessinee juste en dessous (voir plus bas dans cette fonction).
    _assurer_espace(pdf, largeur_totale + 13)
    x_debut = pdf.l_margin + (pdf.w - pdf.l_margin - pdf.r_margin - largeur_totale) / 2
    y_debut = pdf.y

    for indice, point in enumerate(points):
        ligne, colonne = divmod(indice, taille)
        x = x_debut + colonne * (cote + marge_case)
        y = y_debut + ligne * (cote + marge_case)
        pdf.set_fill_color(*_couleur_point(point.get("position")))
        pdf.rect(x, y, cote, cote, style="F")
        if point.get("position") and point["position"] <= 10:
            pdf.set_xy(x, y + 1.6)
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(cote, 6, str(point["position"]), align="C")

    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y_debut + taille * (cote + marge_case) + 3)

    # Legende
    legende = [
        (COULEUR_BON, "Top 3"), (COULEUR_ATTENTION, "Top 4-10"),
        (COULEUR_DANGER, "Au-dela / non classee"), (COULEUR_GRILLE_ABSENT, "Non trouvee"),
    ]
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 8)
    for couleur, libelle in legende:
        pdf.set_fill_color(*couleur)
        x, y = pdf.get_x(), pdf.get_y()
        pdf.rect(x, y + 1, 3, 3, style="F")
        pdf.set_x(x + 4.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(pdf.get_string_width(libelle) + 6, 5, libelle)
    pdf.ln(8)
    pdf.set_text_color(*COULEUR_TEXTE)


def _tableau_concurrents(pdf: RapportPDF, concurrents: list, fiche: dict = None, resume: dict = None):
    fiche_comparable = bool(fiche and fiche.get("trouve") and resume)
    if not concurrents and not fiche_comparable:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Aucun concurrent identifie sur ce mot-cle a proximite.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)
        return

    largeur_nom, largeur_note, largeur_avis, largeur_position = 55, 22, 22, 33

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_x(pdf.l_margin)
    pdf.cell(largeur_nom, 8, "  Entreprise", border=0, fill=True)
    pdf.cell(largeur_note, 8, "Note", border=0, fill=True, align="C")
    pdf.cell(largeur_avis, 8, "Avis", border=0, fill=True, align="C")
    pdf.cell(largeur_position, 8, "Position moy.", border=0, fill=True, align="C")
    pdf.cell(0, 8, "Presence sur la zone", border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*COULEUR_TEXTE)
    indice = 0

    if fiche_comparable:
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(*COULEUR_BON_CLAIR)
        pdf.set_font("Helvetica", "B", 9)
        nom_vous = _nettoyer(fiche.get("titre") or "Votre entreprise")[:32] + " (vous)"
        pdf.cell(largeur_nom, 7.5, "  " + nom_vous, border=0, fill=True)
        pdf.cell(largeur_note, 7.5, f"{fiche['note']}/5" if fiche.get("note") else "-", border=0, fill=True, align="C")
        pdf.cell(largeur_avis, 7.5, str(fiche["nombre_avis"]) if fiche.get("nombre_avis") else "-", border=0, fill=True, align="C")
        pdf.cell(
            largeur_position, 7.5, f"#{resume['position_moyenne']}" if resume.get("position_moyenne") else "-",
            border=0, fill=True, align="C",
        )
        pdf.cell(
            0, 7.5, f"{resume['pourcentage_couverture']}%",
            border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT",
        )
        indice = 1

    pdf.set_font("Helvetica", "", 9)
    for concurrent in concurrents:
        pdf.set_x(pdf.l_margin)
        fill = bool(indice % 2)
        if fill:
            pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.cell(largeur_nom, 7.5, "  " + _nettoyer(concurrent["nom"])[:32], border=0, fill=fill)
        pdf.cell(largeur_note, 7.5, f"{concurrent['note']}/5" if concurrent.get("note") else "-", border=0, fill=fill, align="C")
        pdf.cell(
            largeur_avis, 7.5, str(concurrent["nombre_avis"]) if concurrent.get("nombre_avis") else "-",
            border=0, fill=fill, align="C",
        )
        pdf.cell(largeur_position, 7.5, f"#{concurrent['position_moyenne']}", border=0, fill=fill, align="C")
        pdf.cell(0, 7.5, f"{concurrent['presence']}%", border=0, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT")
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


def generer_audit_prospect_pdf(
    nom_entreprise: str, ville: str, fiche: dict, releves: list,
    analyse_site: dict = None, plan_action: list = None,
    citations_resultats: list = None, opportunites_mots_cles: list = None,
    autorite_site: dict = None,
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
    """
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
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
        pdf.cell(0, 6, "Carte de positionnement (zone testee autour de la fiche)", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        _grille_visuelle(pdf, releve["points"])

        pdf.set_font("Helvetica", "", 9.5)
        for constat in _recommandations(resume):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 5.5, _nettoyer(f"- {constat}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, "Qui ressort devant elle sur ce mot-cle", new_x="LMARGIN", new_y="NEXT")
        _tableau_concurrents(pdf, releve["concurrents"], fiche, resume)
        pdf.ln(6)

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

    return bytes(pdf.output())
