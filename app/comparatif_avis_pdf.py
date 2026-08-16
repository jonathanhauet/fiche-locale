"""
Generation du PDF pour un comparatif d'avis multi-fiches enregistre (voir
/avis/comparatif et models.ComparatifAvis). Reutilise les briques de
rapport_pdf.py (classe PDF, cellules, nettoyage des caracteres).
"""

from datetime import date

from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _nettoyer, _titre_section

COULEUR_SUCCES = (22, 163, 74)
COULEUR_ATTENTION = (180, 95, 6)

LARGEURS_COLONNES_CLASSEMENT = [58, 22, 22, 20, 20, 28]
ENTETES_CLASSEMENT = ["Fiche", "Avis (per.)", "Moyenne", "Positifs", "Negatifs", "Total"]

MARGE = 10
LARGEUR_PAGE_UTILE = 190


def _boite_stat(pdf, x, y, largeur, hauteur, valeur, libelle):
    pdf.set_draw_color(220, 222, 228)
    pdf.rect(x, y, largeur, hauteur, style="D")
    pdf.set_xy(x, y + 3)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.cell(largeur, 8, _nettoyer(str(valeur)), align="C")
    pdf.set_xy(x + 1, y + 11)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.multi_cell(largeur - 2, 3.6, _nettoyer(libelle), align="C")


def _ligne_insight_titree(pdf, titre, texte, couleur):
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*couleur)
    pdf.cell(0, 5.5, _nettoyer(titre.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.multi_cell(0, 6, _nettoyer(texte))
    pdf.ln(1.5)


def generer_comparatif_pdf(libelle: str, date_debut: date, date_fin: date, donnees: dict) -> bytes:
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # --- Bandeau d'entete colore ---
    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.rect(0, 0, 210, 30, style="F")
    pdf.set_xy(MARGE, 7)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, "Comparatif d'avis", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGE)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _nettoyer(libelle or "Selection personnalisee"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGE)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0, 5,
        f"Periode du {date_debut.strftime('%d/%m/%Y')} au {date_fin.strftime('%d/%m/%Y')} "
        f"- Genere le {date.today().strftime('%d/%m/%Y')}",
    )
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(38)

    # --- Resume, en cartes plutot qu'en lignes texte ---
    moyenne_periode = donnees.get("moyenne_periode")
    moyenne_historique = donnees.get("moyenne_historique")
    stats = [
        (donnees.get("total_periode", 0), "Avis recus (periode)"),
        (f"{moyenne_periode}/5" if moyenne_periode else "N/A", "Moyenne (periode)"),
        (donnees.get("positifs_periode", 0), "Avis positifs (periode)"),
        (donnees.get("negatifs_periode", 0), "Avis negatifs (periode)"),
        (donnees.get("total_historique", 0), "Total (tout historique)"),
        (f"{moyenne_historique}/5" if moyenne_historique else "N/A", "Moyenne globale (historique)"),
    ]
    gap = 4
    largeur_boite = (LARGEUR_PAGE_UTILE - 2 * gap) / 3
    hauteur_boite = 20
    y_depart = pdf.get_y()
    for indice, (valeur, libelle_stat) in enumerate(stats):
        col, ligne = indice % 3, indice // 3
        x = MARGE + col * (largeur_boite + gap)
        y = y_depart + ligne * (hauteur_boite + gap)
        _boite_stat(pdf, x, y, largeur_boite, hauteur_boite, valeur, libelle_stat)
    pdf.set_y(y_depart + 2 * (hauteur_boite + gap) + 2)

    # --- Analyse (conclusions au-dela du classement brut) ---
    insights = donnees.get("insights") or {}
    noms_sous_moyenne = {f["nom"] for f in (insights.get("fiches_sous_la_moyenne") or [])}
    nom_meilleure_fiche = (insights.get("meilleure_fiche") or {}).get("nom")

    if insights:
        _titre_section(pdf, "Analyse")

        if insights.get("meilleure_fiche"):
            mf = insights["meilleure_fiche"]
            _ligne_insight_titree(
                pdf, "Meilleure fiche", f"{mf['nom']} - {mf['moyenne']}/5 ({mf['total']} avis)", COULEUR_SUCCES
            )
        if insights.get("fiche_plus_active"):
            fa = insights["fiche_plus_active"]
            _ligne_insight_titree(pdf, "Fiche la plus active", f"{fa['nom']} - {fa['total']} avis", COULEUR_TEXTE)

        evolution = insights.get("periode_precedente") or {}
        if evolution.get("evolution_pct") is not None:
            pct = evolution["evolution_pct"]
            signe = "+" if pct >= 0 else ""
            couleur = COULEUR_SUCCES if pct >= 0 else COULEUR_ATTENTION
            _ligne_insight_titree(
                pdf, "Tendance", f"{signe}{pct}% vs periode precedente ({evolution.get('total', 0)} avis)", couleur
            )
        else:
            _ligne_insight_titree(
                pdf, "Tendance", "Pas de comparaison possible (aucun avis sur la periode precedente).", COULEUR_GRIS
            )

        alertes = []
        if insights.get("avis_negatifs_non_repondus"):
            alertes.append(
                f"{insights['avis_negatifs_non_repondus']} avis negatif(s) sans reponse dans le groupe sur la periode."
            )
        if insights.get("fiches_sous_la_moyenne"):
            noms = ", ".join(f"{f['nom']} ({f['moyenne']}/5)" for f in insights["fiches_sous_la_moyenne"])
            alertes.append(
                f"{len(insights['fiches_sous_la_moyenne'])} fiche(s) nettement sous la moyenne du groupe : {noms}."
            )
        if insights.get("fiches_sans_avis"):
            fiches_sans_avis = insights["fiches_sans_avis"]
            noms = ", ".join(fiches_sans_avis[:10])
            suffixe = f", et {len(fiches_sans_avis) - 10} autre(s)" if len(fiches_sans_avis) > 10 else ""
            alertes.append(f"{len(fiches_sans_avis)} fiche(s) sans avis sur la periode : {noms}{suffixe}.")

        if alertes:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*COULEUR_ATTENTION)
            pdf.cell(0, 5.5, "A SURVEILLER", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*COULEUR_TEXTE)
            for alerte in alertes:
                pdf.multi_cell(0, 5.5, _nettoyer(f"- {alerte}"))
            pdf.ln(1)

    # --- Classement par fiche, avec la meilleure fiche et celles a surveiller mises en evidence ---
    # Trie par nombre d'avis decroissant, comme sur le web (le JSON stocke
    # l'ordre brut des fiches selectionnees, pas trie).
    classement = sorted(donnees.get("classement") or [], key=lambda l: l.get("total_periode", 0), reverse=True)
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
            nom_affiche = _nettoyer(nom[:38] + "..." if len(nom) > 38 else nom)
            moyenne_ligne = ligne.get("moyenne_periode")

            remplissage = False
            if nom == nom_meilleure_fiche:
                pdf.set_fill_color(224, 246, 233)
                remplissage = True
            elif nom in noms_sous_moyenne:
                pdf.set_fill_color(253, 237, 219)
                remplissage = True

            valeurs = [
                nom_affiche,
                str(ligne.get("total_periode", 0)),
                f"{moyenne_ligne}/5" if moyenne_ligne else "-",
                str(ligne.get("positifs", 0)),
                str(ligne.get("negatifs", 0)),
                str(ligne.get("total_historique", 0)),
            ]
            for indice, (largeur, valeur) in enumerate(zip(LARGEURS_COLONNES_CLASSEMENT, valeurs)):
                pdf.cell(largeur, 6.5, valeur, border=1, align="L" if indice == 0 else "C", fill=remplissage)
            pdf.ln()

    return bytes(pdf.output())
