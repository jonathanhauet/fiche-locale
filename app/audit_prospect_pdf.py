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


def _carte_fiche_google(pdf: RapportPDF, fiche: dict):
    y_debut = pdf.y
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


def _encadre_verdict(pdf: RapportPDF, mot_cle: str, resume: dict):
    couleur, couleur_claire = _couleurs_selon_couverture(resume["pourcentage_couverture"])
    if resume["pourcentage_couverture"] >= 50:
        verdict = "Bonne visibilite sur ce mot-cle"
    elif resume["pourcentage_couverture"] >= 20:
        verdict = "Visibilite partielle, il y a de la marge"
    else:
        verdict = "Visibilite tres faible sur ce mot-cle"

    y_debut = pdf.y
    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    hauteur = 26
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


def _tableau_concurrents(pdf: RapportPDF, concurrents: list):
    if not concurrents:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Aucun concurrent identifie sur ce mot-cle a proximite.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)
        return

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_x(pdf.l_margin)
    pdf.cell(90, 8, "  Entreprise", border=0, fill=True)
    pdf.cell(45, 8, "Position moyenne", border=0, fill=True, align="C")
    pdf.cell(0, 8, "Presence sur la zone", border=0, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COULEUR_TEXTE)
    for indice, concurrent in enumerate(concurrents):
        pdf.set_x(pdf.l_margin)
        if indice % 2:
            pdf.set_fill_color(*COULEUR_FOND_CARTE)
            fill = True
        else:
            fill = False
        pdf.cell(90, 7.5, "  " + _nettoyer(concurrent["nom"])[:46], border=0, fill=fill)
        pdf.cell(45, 7.5, f"#{concurrent['position_moyenne']}", border=0, fill=fill, align="C")
        pdf.cell(0, 7.5, f"{concurrent['presence']}%", border=0, fill=fill, align="C", new_x="LMARGIN", new_y="NEXT")


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


def generer_audit_prospect_pdf(nom_entreprise: str, ville: str, fiche: dict, releves: list, analyse_site: dict = None) -> bytes:
    """
    fiche : voir audit_prospect.rechercher_fiche_publique()
    releves : liste de resultats audit_prospect.grille_positions_prospect() (un par mot-cle)
    analyse_site : voir audit_site_technique.analyser_site(), optionnel (absent si pas de site
    web renseigne ou si l'analyse a echoue - section simplement omise).
    """
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    _bandeau_titre(pdf, nom_entreprise, ville)
    _carte_fiche_google(pdf, fiche)
    _tableau_completude(pdf, audit_prospect.evaluer_completude_fiche(fiche))

    if analyse_site:
        _carte_site_technique(pdf, analyse_site)

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
        _tableau_concurrents(pdf, releve["concurrents"])
        pdf.ln(6)

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
