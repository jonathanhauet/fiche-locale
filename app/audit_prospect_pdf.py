"""
Mise en page PDF de l'audit prospect (voir audit_prospect.py) - reutilise les
memes classes/couleurs que rapport_pdf.py pour rester coherent visuellement
avec les autres documents produits par la plateforme.
"""

from datetime import date

from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _ligne_valeur, _nettoyer, _titre_section

COULEUR_BON = (22, 163, 74)
COULEUR_ATTENTION = (217, 138, 6)


def _couleur_selon_couverture(pourcentage: int):
    if pourcentage >= 50:
        return COULEUR_BON
    if pourcentage >= 20:
        return COULEUR_ATTENTION
    return (220, 38, 38)


def _tableau_concurrents(pdf: RapportPDF, concurrents: list):
    if not concurrents:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.cell(0, 6, "Aucun concurrent identifie sur ce mot-cle a proximite.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)
        return

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(240, 242, 247)
    pdf.cell(90, 7, "Entreprise", border=1, fill=True)
    pdf.cell(45, 7, "Position moyenne", border=1, fill=True, align="C")
    pdf.cell(0, 7, "Presence sur la zone", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    for concurrent in concurrents:
        pdf.cell(90, 7, _nettoyer(concurrent["nom"])[:48], border=1)
        pdf.cell(45, 7, f"#{concurrent['position_moyenne']}", border=1, align="C")
        pdf.cell(0, 7, f"{concurrent['presence']}%", border=1, align="C", new_x="LMARGIN", new_y="NEXT")


def _recommandations(resume: dict) -> list:
    """Constats generes par une regle simple sur les chiffres du releve - pas d'IA ici, pour ne rien ajouter au cout de l'audit."""
    constats = []
    pourcentage = resume["pourcentage_couverture"]
    if pourcentage < 20:
        constats.append(
            "La visibilite locale sur ce mot-cle est tres faible : sur la grande majorite de la zone testee, "
            "la fiche n'apparait dans aucun des premiers resultats Google Maps."
        )
    elif pourcentage < 50:
        constats.append(
            "La visibilite locale sur ce mot-cle est partielle : une partie significative de la zone testee "
            "ne fait pas remonter la fiche."
        )
    else:
        constats.append("La visibilite locale sur ce mot-cle est deja correcte sur la zone testee.")

    if resume["position_moyenne"] and resume["position_moyenne"] > 5:
        constats.append(
            f"Quand la fiche apparait, elle se situe en moyenne en position {resume['position_moyenne']} "
            "- au-dela des tout premiers resultats consultes par les internautes."
        )
    return constats


def generer_audit_prospect_pdf(nom_entreprise: str, ville: str, fiche: dict, releves: list) -> bytes:
    """
    fiche : voir audit_prospect.rechercher_fiche_publique()
    releves : liste de resultats audit_prospect.grille_positions_prospect() (un par mot-cle)
    """
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 12, "Audit de visibilite locale", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 9, _nettoyer(nom_entreprise), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 7, f"{_nettoyer(ville)} - audit genere le {date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)

    _titre_section(pdf, "Fiche Google")
    if fiche.get("trouve"):
        _ligne_valeur(pdf, "Nom de la fiche", _nettoyer(fiche.get("titre") or ""))
        note = fiche.get("note")
        nb_avis = fiche.get("nombre_avis")
        if note:
            _ligne_valeur(pdf, "Note moyenne", f"{note}/5" + (f" ({nb_avis} avis)" if nb_avis else ""))
        if fiche.get("categorie"):
            _ligne_valeur(pdf, "Categorie", _nettoyer(fiche["categorie"]))
        if fiche.get("adresse"):
            _ligne_valeur(pdf, "Adresse", _nettoyer(fiche["adresse"]))
        if fiche.get("telephone"):
            _ligne_valeur(pdf, "Telephone", fiche["telephone"])
        if fiche.get("site_web"):
            _ligne_valeur(pdf, "Site web", _nettoyer(fiche["site_web"]))
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, "Fiche non retrouvee automatiquement sur Google Maps avec ce nom et cette ville.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)

    for releve in releves:
        pdf.ln(2)
        _titre_section(pdf, f"Visibilite locale : \"{releve['mot_cle']}\"")
        resume = releve["resume"]
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(110, 8, "Points ou la fiche apparait")
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*_couleur_selon_couverture(resume["pourcentage_couverture"]))
        pdf.cell(0, 8, f"{resume['points_trouves']} / {resume['total_points']} ({resume['pourcentage_couverture']}%)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*COULEUR_TEXTE)
        if resume["position_moyenne"] is not None:
            _ligne_valeur(pdf, "Position moyenne quand elle apparait", f"#{resume['position_moyenne']}")

        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        for constat in _recommandations(resume):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, _nettoyer(f"- {constat}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Qui ressort devant elle sur ce mot-cle", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        _tableau_concurrents(pdf, releve["concurrents"])

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0, 5.5,
        _nettoyer(
            "Cet audit s'appuie sur des donnees publiques Google Maps (recherche geolocalisee), sans acces "
            "autorise a la fiche : certaines informations (photos, historique des publications) ne peuvent "
            "donc pas y figurer."
        ),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*COULEUR_TEXTE)

    return bytes(pdf.output())
