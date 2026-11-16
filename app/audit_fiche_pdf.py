"""Rapport PDF de l'audit complet d'une fiche (voir audit_fiche.py) : couverture, score et themes, priorites, controles, IA, positions, conseils, contact."""

from datetime import date

from . import audit_fiche, audit_prospect
from .audit_prospect_pdf import (
    COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR, COULEUR_BON, COULEUR_BON_CLAIR, COULEUR_DANGER, COULEUR_DANGER_CLAIR, COULEUR_FOND_CARTE, MARGE_LATERALE, RAYON_CARTE,
    _activer_police_signature, _assurer_espace, _carte_autorite_site, _carte_citations, _carte_fiche_google, _carte_site_technique, _encadre_verdict, _page_contact, _page_couverture,
    _recommandations, _section_carte_positions, _tableau_concurrents, _titre_section,
)
from .rapport_pdf import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_TEXTE, RapportPDF, _nettoyer

COULEURS_STATUT = {"ok": COULEUR_BON, "attention": COULEUR_ATTENTION, "probleme": COULEUR_DANGER, "inconnu": (156, 163, 175)}


def _rgb(hexa: str) -> tuple:
    h = hexa.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _bandeau(pdf: RapportPDF, nom: str, ville: str):
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COULEUR_ACCENT)
    pdf.cell(0, 6, " ".join("AUDIT DE LA FICHE GOOGLE"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 21)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.multi_cell(0, 10, _nettoyer(nom)[:90], new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 6, f"{_nettoyer(ville)} - genere le {date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_draw_color(*COULEUR_ACCENT)
    pdf.set_line_width(0.7)
    pdf.line(pdf.l_margin, pdf.y, pdf.w - pdf.r_margin, pdf.y)
    pdf.set_line_width(0.2)
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.ln(8)


def _bandeau_score(pdf: RapportPDF, resultat: dict):
    score = resultat["score"]
    if score is None:
        return
    couleur = _rgb(resultat["couleur"])
    largeur = pdf.w - pdf.l_margin - pdf.r_margin
    hauteur = 34
    _assurer_espace(pdf, hauteur)
    y = pdf.y
    pdf.set_fill_color(*COULEUR_FOND_CARTE)
    pdf.rect(pdf.l_margin, y, largeur, hauteur, style="F", round_corners=True, corner_radius=RAYON_CARTE)
    pdf.set_xy(pdf.l_margin + 8, y + 6)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.cell(0, 6, "Score global de la fiche", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 8)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*couleur)
    texte = f"{score}/100"
    pdf.cell(pdf.get_string_width(texte) + 6, 14, texte)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 14, "  " + _nettoyer(resultat["libelle"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*COULEUR_TEXTE)
    pdf.set_y(y + hauteur + 8)


def _barres_themes(pdf: RapportPDF, themes: list):
    _titre_section(pdf, "Score par theme")
    largeur_barre = pdf.w - pdf.l_margin - pdf.r_margin - 78
    for t in themes:
        _assurer_espace(pdf, 9)
        y = pdf.y
        pdf.set_xy(pdf.l_margin, y)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(55, 7, _nettoyer(t["nom"]))
        x = pdf.l_margin + 56
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(x, y + 1.6, largeur_barre, 3.8, style="F", round_corners=True, corner_radius=1.9)
        if t["score"] is not None:
            _, couleur = audit_fiche.etiquette_score(t["score"])
            pdf.set_fill_color(*_rgb(couleur))
            pdf.rect(x, y + 1.6, max(2.5, largeur_barre * t["score"] / 100), 3.8, style="F", round_corners=True, corner_radius=1.9)
        pdf.set_xy(x + largeur_barre + 3, y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(18, 7, f"{t['score']}/100" if t["score"] is not None else "n/d", align="R")
        pdf.set_y(y + 8.5)
    pdf.ln(5)


def _synthese(pdf: RapportPDF, synthese: dict):
    if not synthese or not synthese.get("resume"):
        return
    _titre_section(pdf, "En bref")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 5.8, _nettoyer(synthese["resume"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)


def _priorites(pdf: RapportPDF, priorites: list):
    if not priorites:
        return
    _titre_section(pdf, "A corriger en priorite", "Classees par gain de score possible, impact et effort.")
    for i, p in enumerate(priorites[:8], start=1):
        _assurer_espace(pdf, 34)
        y = pdf.y
        couleur = COULEURS_STATUT.get(p["statut"], COULEUR_ATTENTION)
        pdf.set_fill_color(*couleur)
        pdf.ellipse(pdf.l_margin, y + 1, 6.5, 6.5, style="F")
        pdf.set_xy(pdf.l_margin, y + 1.5)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(6.5, 5.5, str(i), align="C")
        pdf.set_xy(pdf.l_margin + 9, y)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.multi_cell(0, 6.5, _nettoyer(p["titre"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin + 9)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*COULEUR_BON)
        pdf.cell(26, 5, f"+{str(p['gain']).replace('.', ',')} points")
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(0, 5, _nettoyer(f"Impact {audit_fiche.IMPACTS[p['impact']].lower()} - Effort {audit_fiche.EFFORTS[p['effort']].lower()}"), new_x="LMARGIN", new_y="NEXT")
        for libelle, texte in (("Constat", p["constat"]), ("Pourquoi", p["pourquoi"]), ("Comment", p["comment"])):
            pdf.set_x(pdf.l_margin + 9)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.cell(18, 5, libelle)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*COULEUR_TEXTE)
            pdf.set_x(pdf.l_margin + 27)
            pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 27, 5, _nettoyer(texte), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
    pdf.ln(2)


def _controles(pdf: RapportPDF, themes: list):
    _titre_section(pdf, "Detail des controles")
    for t in themes:
        if not t["controles"]:
            continue
        _assurer_espace(pdf, 16)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(0, 7, _nettoyer(f"{t['nom']}" + (f" - {t['score']}/100" if t["score"] is not None else " - non evalue")), new_x="LMARGIN", new_y="NEXT")
        for c in t["controles"]:
            _assurer_espace(pdf, 9)
            y = pdf.y
            pdf.set_fill_color(*COULEURS_STATUT.get(c["statut"], COULEUR_GRIS))
            pdf.ellipse(pdf.l_margin + 1, y + 2, 3, 3, style="F")
            pdf.set_xy(pdf.l_margin + 7, y)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*COULEUR_TEXTE)
            pdf.cell(62, 6, _nettoyer(c["libelle"])[:48])
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*COULEUR_GRIS)
            pdf.multi_cell(0, 5, _nettoyer(c["detail"]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.6)
        pdf.ln(3)


def _carte_ia(pdf: RapportPDF, ia: dict):
    if not ia or not ia.get("questions"):
        return
    _titre_section(pdf, "Visibilite dans les assistants IA", "On pose a ChatGPT et Gemini la question qu'un client leur poserait, avec la ville.")
    noms = {"chatgpt": "ChatGPT", "gemini": "Gemini"}
    for q in ia["questions"]:
        _assurer_espace(pdf, 24)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.multi_cell(0, 6, _nettoyer(f"Question : {q['question']}"), new_x="LMARGIN", new_y="NEXT")
        for moteur, r in q["moteurs"].items():
            _assurer_espace(pdf, 14)
            pdf.set_x(pdf.l_margin + 4)
            if r.get("erreur"):
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(*COULEUR_GRIS)
                pdf.cell(0, 6, _nettoyer(f"{noms.get(moteur, moteur)} : indisponible"), new_x="LMARGIN", new_y="NEXT")
                continue
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*(COULEUR_BON if r["citee"] else COULEUR_DANGER))
            pdf.cell(0, 6, _nettoyer(f"{noms.get(moteur, moteur)} : " + ("l'entreprise est citee" if r["citee"] else "l'entreprise n'est PAS citee")), new_x="LMARGIN", new_y="NEXT")
            if r.get("concurrents") and not r["citee"]:
                pdf.set_x(pdf.l_margin + 4)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(*COULEUR_TEXTE)
                pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 4, 5, _nettoyer("L'IA recommande a sa place : " + ", ".join(r["concurrents"][:5])), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    if ia.get("domaines"):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(0, 6, "Sites consultes par les IA pour repondre", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.multi_cell(0, 5, _nettoyer(", ".join(f"{d['domaine']} ({d['nb']})" for d in ia["domaines"][:10])), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)


def _liste(pdf: RapportPDF, titre: str, sous_titre: str, elements: list, cle_titre: str = "titre", cle_texte: str = "texte"):
    if not elements:
        return
    _titre_section(pdf, titre, sous_titre)
    for e in elements:
        _assurer_espace(pdf, 16)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*COULEUR_TEXTE)
        pdf.cell(0, 6, _nettoyer(e[cle_titre]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.multi_cell(0, 5, _nettoyer(e[cle_texte]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
    pdf.ln(4)


def generer_audit_fiche_pdf(nom: str, ville: str, donnees: dict, resultat: dict, avis_jonathan: dict = None) -> bytes:
    pdf = RapportPDF(format="A4", unit="mm")
    pdf.set_margins(MARGE_LATERALE, 16, MARGE_LATERALE)
    pdf.set_auto_page_break(auto=True, margin=24)
    signature = _activer_police_signature(pdf)
    _page_couverture(pdf, nom, ville, avis_jonathan, signature)

    pdf.add_page()
    _bandeau(pdf, nom, ville)
    _bandeau_score(pdf, resultat)
    _barres_themes(pdf, resultat["themes"])
    _synthese(pdf, resultat.get("synthese"))
    _priorites(pdf, resultat["priorites"])
    if resultat["points_forts"]:
        _liste(pdf, "Ce qui est deja bien", None, [{"titre": p["libelle"], "texte": p["detail"]} for p in resultat["points_forts"]])
    _controles(pdf, resultat["themes"])

    fiche = donnees.get("fiche") or {}
    _carte_fiche_google(pdf, {**fiche, "trouve": True})
    site = donnees.get("site") or {}
    if site.get("vitesse"):
        _carte_site_technique(pdf, {"url": fiche.get("site_web", ""), "score_performance": None, "score_seo": None, "premier_affichage": "", "affichage_complet": "", "points_bloquants": [], "points_positifs": [], **site["vitesse"]})
    if site.get("autorite"):
        _carte_autorite_site(pdf, {"rang": 0, "backlinks": 0, "domaines_referents": 0, **site["autorite"]})
    if (donnees.get("annuaires") or {}).get("resultats"):
        _carte_citations(pdf, donnees["annuaires"]["resultats"])
    for releve in (donnees.get("positions") or {}).get("releves") or []:
        resume = releve["resume"]
        _encadre_verdict(pdf, releve["mot_cle"], resume)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Carte de positionnement", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*COULEUR_GRIS)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 5, f"Zone testee : rayon d'environ {audit_prospect.RAYON_KM_DEFAUT:g} km autour de la fiche", new_x="LMARGIN", new_y="NEXT")
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
        _tableau_concurrents(pdf, releve["concurrents"], {**fiche, "trouve": True}, resume)
        pdf.ln(8)

    _carte_ia(pdf, donnees.get("ia"))
    _liste(pdf, "Idees propres a votre activite", None, resultat["conseils"] + [o for o in ((resultat.get("synthese") or {}).get("opportunites") or [])])
    _liste(pdf, "Ce que cet audit ne peut pas voir", "Il s'appuie sur des donnees publiques, sans acces a la fiche : ces points se verifient avec le proprietaire.", resultat["a_verifier"], "titre", "detail")
    _page_contact(pdf, signature)
    return bytes(pdf.output())
