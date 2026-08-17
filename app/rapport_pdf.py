"""Generation du rapport PDF de performance a fournir a un client."""

from datetime import date

from fpdf import FPDF

COULEUR_ACCENT = (37, 99, 235)
COULEUR_TEXTE = (28, 30, 33)
COULEUR_GRIS = (110, 118, 129)
COULEUR_DANGER = (220, 38, 38)
COULEUR_DANGER_CLAIR = (253, 236, 236)

REMPLACEMENTS_CARACTERES = {
    "–": "-",  # tiret demi-cadratin (–)
    "—": "-",  # tiret cadratin (—)
    "‘": "'", "’": "'",  # apostrophes typographiques
    "“": '"', "”": '"',  # guillemets typographiques
    "…": "...",  # points de suspension
}


def _nettoyer(texte: str) -> str:
    """
    Remplace les caracteres typographiques courants (tirets, guillemets...)
    generes par l'IA mais non supportes par la police de base du PDF, puis
    filtre tout caractere restant hors du jeu latin-1 par securite.
    """
    for original, remplacement in REMPLACEMENTS_CARACTERES.items():
        texte = texte.replace(original, remplacement)
    return texte.encode("latin-1", errors="replace").decode("latin-1")


class RapportPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*COULEUR_GRIS)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _titre_section(pdf: RapportPDF, texte: str):
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 9, texte, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)


def _ligne_valeur(pdf: RapportPDF, libelle: str, valeur: str):
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(110, 8, libelle)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, str(valeur), new_x="LMARGIN", new_y="NEXT")


def _bandeau_fiche_non_validee(pdf: RapportPDF):
    """Encadre rouge signalant qu'une fiche n'est pas validee (verifiee) par Google."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_draw_color(*COULEUR_DANGER)
    pdf.set_fill_color(*COULEUR_DANGER_CLAIR)
    pdf.set_text_color(*COULEUR_DANGER)
    pdf.multi_cell(
        0, 6.5,
        _nettoyer(
            "Fiche non validee par Google : tant qu'elle n'est pas verifiee, la reponse aux avis, "
            "la modification des informations et certaines statistiques peuvent etre limitees."
        ),
        border=1, fill=True,
    )
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(3)


def _ligne_evolution(pdf: RapportPDF, valeur_n1, evolution):
    """Petite ligne grise italique sous une metrique, pour le comparatif N-1."""
    texte_evolution = f" ({evolution:+.1f}%)" if evolution is not None else ""
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 6, f"   {valeur_n1} l'an dernier{texte_evolution}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)


SECTIONS_DISPONIBLES = {"visibilite", "comparatif", "avis", "posts", "mots_cles"}


def generer_rapport_pdf(
    client_nom: str,
    date_debut: date,
    date_fin: date,
    statistiques: dict,
    resume_avis: dict,
    posts_publies: list,
    mots_cles: list = None,
    comparatif_visibilite: dict = None,
    evolution_avis: float = None,
    sections: set = None,
    fiche_validee: bool = None,
) -> bytes:
    """
    Construit le rapport PDF et renvoie ses octets.
    statistiques : {libelle: total} (voir google_performance.py)
    resume_avis : voir google_reviews.resumer_avis()
    posts_publies : liste de dicts {"titre": str, "date": str}
    mots_cles : voir google_performance.recuperer_mots_cles_recherche()
    comparatif_visibilite, evolution_avis : voir main._rassembler_donnees_rapport()
    sections : sous-ensemble de SECTIONS_DISPONIBLES a inclure (None = toutes).
               "comparatif" n'a d'effet qu'a l'interieur de "visibilite"/"avis".
    """
    if sections is None:
        sections = SECTIONS_DISPONIBLES
    mots_cles = mots_cles or []
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 12, "Rapport de performance", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 9, _nettoyer(client_nom), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(
        0, 7,
        f"Periode du {date_debut.strftime('%d/%m/%Y')} au {date_fin.strftime('%d/%m/%Y')} "
        f"- Rapport genere le {date.today().strftime('%d/%m/%Y')}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*COULEUR_TEXTE)

    if fiche_validee is False:
        pdf.ln(2)
        _bandeau_fiche_non_validee(pdf)

    inclure_comparatif = "comparatif" in sections

    # --- Visibilite Google ---
    if "visibilite" in sections:
        _titre_section(pdf, "Visibilité sur Google")
        vues = (
            statistiques.get("Vues sur Maps (ordinateur)", 0)
            + statistiques.get("Vues sur Recherche Google (ordinateur)", 0)
            + statistiques.get("Vues sur Maps (mobile)", 0)
            + statistiques.get("Vues sur Recherche Google (mobile)", 0)
        )
        _ligne_valeur(pdf, "Vues totales de la fiche (Maps + Recherche)", vues)
        for libelle in ["Clics vers le site web", "Clics sur \"Appeler\"", "Demandes d'itinéraire", "Messages reçus"]:
            _ligne_valeur(pdf, libelle, statistiques.get(libelle, 0))
            if inclure_comparatif and comparatif_visibilite and libelle in comparatif_visibilite:
                comp = comparatif_visibilite[libelle]
                _ligne_evolution(pdf, comp["n1"], comp["evolution"])

    # --- Avis clients ---
    if "avis" in sections:
        _titre_section(pdf, "Avis clients")
        note_globale = resume_avis.get("note_moyenne_globale")
        _ligne_valeur(pdf, "Note moyenne actuelle", f"{note_globale}/5" if note_globale else "N/A")
        _ligne_valeur(pdf, "Nombre total d'avis", resume_avis.get("total_avis_global", 0))
        _ligne_valeur(pdf, "Nouveaux avis sur la période", resume_avis.get("nombre_avis_periode", 0))
        if inclure_comparatif and resume_avis.get("nombre_avis_periode_n1") is not None:
            _ligne_evolution(pdf, resume_avis["nombre_avis_periode_n1"], evolution_avis)
        note_periode = resume_avis.get("note_moyenne_periode")
        if note_periode:
            _ligne_valeur(pdf, "Note moyenne des nouveaux avis", f"{note_periode}/5")

    # --- Posts publies ---
    if "posts" in sections:
        _titre_section(pdf, "Publications Google (posts)")
        _ligne_valeur(pdf, "Posts publiés sur la période", len(posts_publies))
        if posts_publies:
            pdf.ln(2)
            for post in posts_publies:
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(*COULEUR_GRIS)
                pdf.cell(0, 5, post["date"], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*COULEUR_TEXTE)
                titre = post["titre"] + (" (hors plateforme)" if post.get("source") == "google" else "")
                pdf.multi_cell(0, 6, _nettoyer(titre))
                pdf.ln(1)

    # --- Mots-cles de recherche ---
    if "mots_cles" in sections and mots_cles:
        _titre_section(pdf, "Mots-clés de recherche")
        for entree in mots_cles:
            prefixe = "< " if entree.get("est_seuil") else ""
            _ligne_valeur(pdf, _nettoyer(entree.get("mot_cle", "")), f"{prefixe}{entree.get('impressions', 0)}")

    return bytes(pdf.output())
