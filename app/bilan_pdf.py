"""
Generation du bilan PDF ponctuel, multi-fiches : une section par fiche
selectionnee dans le meme fichier. Meme ton "toujours positif" que le recap
mensuel par email (recap_mensuel.py) - les chiffres reels sont toujours
affiches, une evolution N-1 n'est mise en avant que si elle est favorable -
mais rendu en PDF plutot qu'en HTML, en reutilisant les briques de
rapport_pdf.py (classe RapportPDF, cellules, nettoyage des caracteres).
"""

from datetime import date

from . import recap_mensuel
from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _ligne_valeur, _nettoyer, _titre_section


def _ligne_evolution_positive(pdf: RapportPDF, evolution: float) -> None:
    """N'est appelee que lorsque l'evolution est deja confirmee positive par l'appelant."""
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(0, 140, 60)
    pdf.cell(0, 6, f"   +{evolution:.1f}% vs l'an dernier", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)


def _bloc_citation(pdf: RapportPDF, texte: str, auteur: str = "") -> None:
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    contenu = f'"{texte}"' + (f" - {auteur}" if auteur else "")
    pdf.multi_cell(0, 6, _nettoyer(contenu))
    pdf.ln(1)


def _section_fiche(pdf: RapportPDF, section: dict) -> None:
    donnees = section["donnees"]
    avis_positifs = section.get("avis_positifs") or []
    resume_avis_texte = section.get("resume_avis_texte")

    statistiques = donnees.get("statistiques") or {}
    resume_avis = donnees.get("resume_avis") or {}
    comparatif_visibilite = donnees.get("comparatif_visibilite")
    posts_publies = donnees.get("posts_publies") or []
    mots_cles = donnees.get("mots_cles") or []
    photos_publiees = donnees.get("photos_publiees") or 0

    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 10, _nettoyer(section["nom"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)

    # --- Avis ---
    # Le titre de section s'affiche des qu'il y a des stats OU une citation a
    # montrer (les deux sont independants, comme dans recap_mensuel.py : un
    # avis positif peut exister sans qu'il y ait de "nouveaux avis" chiffres
    # a afficher au-dessus, ex. periode sans stat mais avec un avis notable).
    nombre_avis = resume_avis.get("nombre_avis_periode") or 0
    note_globale = resume_avis.get("note_moyenne_globale")
    if nombre_avis or note_globale or resume_avis_texte or avis_positifs:
        _titre_section(pdf, "Avis clients")
        if nombre_avis:
            _ligne_valeur(pdf, "Nouveaux avis sur la periode", nombre_avis)
            evolution_avis = donnees.get("evolution_avis")
            if evolution_avis is not None and evolution_avis > 0:
                _ligne_evolution_positive(pdf, evolution_avis)
        if note_globale:
            _ligne_valeur(
                pdf, "Note moyenne actuelle",
                f"{note_globale}/5 ({resume_avis.get('total_avis_global', 0)} avis au total)",
            )

        if resume_avis_texte:
            pdf.ln(1)
            _bloc_citation(pdf, resume_avis_texte)
        elif len(avis_positifs) == 1:
            pdf.ln(1)
            _bloc_citation(pdf, avis_positifs[0]["commentaire"], avis_positifs[0]["auteur"])
        elif len(avis_positifs) > 1:
            pdf.ln(1)
            for a in avis_positifs[:2]:
                _bloc_citation(pdf, a["commentaire"], a["auteur"])

    # --- Visibilite ---
    total_vues, evolution_vues = recap_mensuel.total_et_evolution(
        statistiques, comparatif_visibilite, recap_mensuel.CLES_VUES
    )
    autres_metriques = [
        (libelle, statistiques.get(libelle))
        for libelle in ("Clics sur \"Appeler\"", "Clics vers le site web", "Demandes d'itinéraire", "Messages reçus")
        if statistiques.get(libelle)
    ]
    if total_vues or autres_metriques:
        _titre_section(pdf, "Visibilité sur Google")
        if total_vues:
            _ligne_valeur(pdf, "Vues de la fiche sur Google", total_vues)
            if evolution_vues is not None and evolution_vues > 0:
                _ligne_evolution_positive(pdf, evolution_vues)
        for libelle, valeur in autres_metriques:
            _ligne_valeur(pdf, libelle, valeur)
            evolution = (comparatif_visibilite or {}).get(libelle, {}).get("evolution")
            if evolution is not None and evolution > 0:
                _ligne_evolution_positive(pdf, evolution)

    # --- Mots-cles de recherche ---
    if mots_cles:
        _titre_section(pdf, "Mots-clés de recherche")
        for entree in mots_cles[:5]:
            prefixe = "< " if entree.get("est_seuil") else ""
            _ligne_valeur(pdf, _nettoyer(entree.get("mot_cle", "")), f"{prefixe}{entree.get('impressions', 0)}")

    # --- Travail realise (posts + photos) ---
    if posts_publies or photos_publiees:
        _titre_section(pdf, "Travail réalisé sur la fiche")
        if posts_publies:
            _ligne_valeur(pdf, "Posts publiés", len(posts_publies))
            pdf.ln(1)
            for post in posts_publies:
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(*COULEUR_GRIS)
                pdf.cell(0, 5, post["date"], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                pdf.set_text_color(*COULEUR_TEXTE)
                pdf.multi_cell(0, 6, _nettoyer(post["titre"]))
                pdf.ln(1)
        if photos_publiees:
            _ligne_valeur(pdf, "Photos ajoutées", photos_publiees)


def generer_bilan_pdf(sections_clients: list[dict], date_debut: date, date_fin: date) -> bytes:
    """
    sections_clients : [{"nom": str, "donnees": dict, "avis_positifs": list,
    "resume_avis_texte": str|None}, ...] - une entree par fiche, dans l'ordre
    d'affichage souhaite. "donnees" a la meme forme que
    rapport_donnees.rassembler_donnees_rapport().
    """
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 12, "Bilan", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(
        0, 7,
        f"Periode du {date_debut.strftime('%d/%m/%Y')} au {date_fin.strftime('%d/%m/%Y')} "
        f"- Bilan genere le {date.today().strftime('%d/%m/%Y')}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*COULEUR_TEXTE)

    for index, section in enumerate(sections_clients):
        if index > 0:
            pdf.add_page()
        else:
            pdf.ln(4)
        _section_fiche(pdf, section)

    return bytes(pdf.output())
