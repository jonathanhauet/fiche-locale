"""
Generation du PDF pour un comparatif d'avis multi-fiches enregistre (voir
/avis/comparatif et models.ComparatifAvis). Reutilise les briques de
rapport_pdf.py (classe PDF, cellules, nettoyage des caracteres).
"""

from datetime import date

from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _ligne_valeur, _nettoyer, _titre_section

LARGEURS_COLONNES_CLASSEMENT = [62, 24, 24, 20, 20, 30]
ENTETES_CLASSEMENT = ["Fiche", "Avis (per.)", "Moyenne", "Positifs", "Negatifs", "Total"]


def generer_comparatif_pdf(libelle: str, date_debut: date, date_fin: date, donnees: dict) -> bytes:
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 12, "Comparatif d'avis", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(0, 9, _nettoyer(libelle or "Selection personnalisee"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(
        0, 7,
        f"Periode du {date_debut.strftime('%d/%m/%Y')} au {date_fin.strftime('%d/%m/%Y')} "
        f"- Genere le {date.today().strftime('%d/%m/%Y')}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*COULEUR_TEXTE)

    _titre_section(pdf, "Resume")
    _ligne_valeur(pdf, "Avis recus (periode)", donnees.get("total_periode", 0))
    moyenne_periode = donnees.get("moyenne_periode")
    _ligne_valeur(pdf, "Moyenne (periode)", f"{moyenne_periode}/5" if moyenne_periode else "N/A")
    _ligne_valeur(pdf, "Avis positifs (periode)", donnees.get("positifs_periode", 0))
    _ligne_valeur(pdf, "Avis negatifs (periode)", donnees.get("negatifs_periode", 0))
    _ligne_valeur(pdf, "Avis au total (tout historique)", donnees.get("total_historique", 0))
    moyenne_historique = donnees.get("moyenne_historique")
    _ligne_valeur(pdf, "Moyenne globale (tout historique)", f"{moyenne_historique}/5" if moyenne_historique else "N/A")

    classement = donnees.get("classement") or []
    if classement:
        _titre_section(pdf, "Classement par fiche")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(240, 240, 245)
        for largeur, entete in zip(LARGEURS_COLONNES_CLASSEMENT, ENTETES_CLASSEMENT):
            pdf.cell(largeur, 7, entete, border=1, align="C", fill=True)
        pdf.ln()

        pdf.set_font("Helvetica", "", 9)
        for ligne in classement:
            nom = ligne.get("client_nom", "")
            nom_affiche = _nettoyer(nom[:40] + "..." if len(nom) > 40 else nom)
            moyenne_ligne = ligne.get("moyenne_periode")
            valeurs = [
                nom_affiche,
                str(ligne.get("total_periode", 0)),
                f"{moyenne_ligne}/5" if moyenne_ligne else "-",
                str(ligne.get("positifs", 0)),
                str(ligne.get("negatifs", 0)),
                str(ligne.get("total_historique", 0)),
            ]
            for indice, (largeur, valeur) in enumerate(zip(LARGEURS_COLONNES_CLASSEMENT, valeurs)):
                pdf.cell(largeur, 6.5, valeur, border=1, align="L" if indice == 0 else "C")
            pdf.ln()

    return bytes(pdf.output())
