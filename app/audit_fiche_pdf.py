"""Rapport PDF de l'audit complet d'une fiche (voir audit_fiche.py) : couverture, score et themes, priorites, controles, IA, positions, conseils, contact."""

from datetime import date

from . import audit_fiche, audit_prospect
from .audit_prospect_pdf import (
    COULEUR_ATTENTION, COULEUR_ATTENTION_CLAIR, COULEUR_BON, COULEUR_BON_CLAIR, COULEUR_DANGER, COULEUR_DANGER_CLAIR, COULEUR_FOND_CARTE, MARGE_LATERALE, RAYON_CARTE,
    _activer_police_signature, _assurer_espace, _carte_autorite_site, _carte_citations, _carte_fiche_google, _carte_site_technique, _encadre_verdict, _page_contact, _page_couverture,
    _recommandations, _section_carte_positions, _tableau_concurrents, _titre_section, COULEUR_NAVY, COULEUR_AMBRE,
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


# ---------------------------------------------------------------------------------------------------------------- synthese sur une page

class _SynthesePDF(RapportPDF):
    def footer(self):
        pass


def _tronquer(pdf: RapportPDF, texte: str, largeur: float) -> str:
    """Texte raccourci avec « ... » pour tenir sur une ligne de `largeur` mm (police et taille deja choisies)."""
    texte = _nettoyer(texte or "")
    if pdf.get_string_width(texte) <= largeur:
        return texte
    while texte and pdf.get_string_width(texte + "...") > largeur:
        texte = texte[:-1]
    return texte.rstrip() + "..."


def _ligne_texte(pdf: RapportPDF, x: float, y: float, largeur: float, texte: str, style: str = "", taille: float = 9, couleur=COULEUR_TEXTE, align: str = "L", hauteur: float = 5):
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", style, taille)
    pdf.set_text_color(*couleur)
    pdf.cell(largeur, hauteur, _tronquer(pdf, texte, largeur), align=align)


def _titre_bloc(pdf: RapportPDF, x: float, y: float, texte: str):
    _ligne_texte(pdf, x, y, 90, texte.upper(), style="B", taille=7.5, couleur=COULEUR_GRIS)


GRIS_CASE = (203, 210, 221)


def _couleur_position(p) -> tuple:
    if p is None:
        return GRIS_CASE
    return COULEURS_STATUT["ok"] if p <= 3 else COULEURS_STATUT["attention"] if p <= 10 else COULEURS_STATUT["probleme"]


def generer_synthese_pdf(nom: str, ville: str, donnees: dict, resultat: dict, lien_public: str = "") -> bytes:
    """Synthese de l'audit tenant sur UNE page A4 : score, themes, 5 priorites, IA, carte, coordonnees. Mise en page a positions fixes : rien ne deborde."""
    pdf = _SynthesePDF(format="A4", unit="mm")
    pdf.set_margins(14, 12, 14)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    G, L = 14, pdf.w - 28
    fiche = donnees.get("fiche") or {}
    score = resultat["score"]
    couleur = _rgb(resultat["couleur"])

    # ---- en-tete : titre a gauche, score a droite
    _ligne_texte(pdf, G, 12, L - 40, " ".join("AUDIT DE LA FICHE GOOGLE"), style="B", taille=8, couleur=COULEUR_ACCENT)
    pdf.set_font("Helvetica", "B", 19)
    largeur_titre = L - 42
    mots, lignes, courante = _nettoyer(nom).split(), [], ""
    for mot in mots:
        essai = (courante + " " + mot).strip()
        if pdf.get_string_width(essai) <= largeur_titre or not courante:
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
    if courante:
        lignes.append(courante)
    if len(lignes) > 2:
        lignes = [lignes[0], " ".join(lignes[1:])]
    for i, ligne in enumerate(lignes[:2]):
        _ligne_texte(pdf, G, 19 + i * 8.5, largeur_titre, ligne, style="B", taille=19, hauteur=8.5)
    y_sous = 19 + len(lignes[:2]) * 8.5 + 1
    sous = " - ".join(x for x in (fiche.get("categorie"), ville, date.today().strftime("%d/%m/%Y")) if x)
    _ligne_texte(pdf, G, y_sous, largeur_titre, sous, taille=9, couleur=COULEUR_GRIS)
    if fiche.get("adresse"):
        _ligne_texte(pdf, G, y_sous + 5, largeur_titre, fiche["adresse"], taille=8.5, couleur=COULEUR_GRIS)
    if score is not None:
        cx, cy, r = G + L - 17, 27, 14
        pdf.set_fill_color(*couleur)
        pdf.ellipse(cx - r, cy - r, 2 * r, 2 * r, style="F")
        _ligne_texte(pdf, cx - r, cy - 8, 2 * r, str(score), style="B", taille=24, couleur=(255, 255, 255), align="C", hauteur=10)
        _ligne_texte(pdf, cx - r, cy + 3, 2 * r, "/ 100", style="B", taille=7, couleur=(255, 255, 255), align="C", hauteur=4)
        _ligne_texte(pdf, cx - 22, cy + r + 1.5, 44, resultat["libelle"], style="B", taille=9, couleur=couleur, align="C")
    pdf.set_draw_color(*COULEUR_ACCENT)
    pdf.set_line_width(0.6)
    pdf.line(G, 52, G + L, 52)
    pdf.set_line_width(0.2)

    # ---- chiffres cles
    y = 56
    releves = (donnees.get("positions") or {}).get("releves") or []
    ia = donnees.get("ia") or {}
    reponses = [r for q in ia.get("questions", []) for r in q.get("moteurs", {}).values() if not r.get("erreur")]
    tuiles = [
        ("Note Google", (f"{fiche['note']:.1f}".replace(".", ",") + " / 5") if fiche.get("note") else "-", f"{fiche.get('nombre_avis') or 0} avis"),
        ("Photos", str(fiche.get("total_photos")) if fiche.get("total_photos") is not None else "-", "sur la fiche"),
        ("Visible sur la carte", f"{round(sum(r['resume']['pourcentage_couverture'] for r in releves) / len(releves))} %" if releves else "-", "de la zone testée"),
        ("Cité par les IA", f"{sum(1 for r in reponses if r.get('citee'))} / {len(reponses)}" if reponses else "-", "réponses ChatGPT, Gemini"),
    ]
    largeur_tuile = (L - 9) / 4
    for i, (titre_t, valeur, detail) in enumerate(tuiles):
        x = G + i * (largeur_tuile + 3)
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(x, y, largeur_tuile, 19, style="F", round_corners=True, corner_radius=2)
        _ligne_texte(pdf, x, y + 1.8, largeur_tuile, titre_t, taille=7.5, couleur=COULEUR_GRIS, align="C", hauteur=4)
        _ligne_texte(pdf, x, y + 6, largeur_tuile, valeur, style="B", taille=14, align="C", hauteur=7)
        _ligne_texte(pdf, x, y + 13.2, largeur_tuile, detail, taille=7, couleur=COULEUR_GRIS, align="C", hauteur=4)

    # ---- themes
    y = 80
    _titre_bloc(pdf, G, y, "Score par thème")
    y += 5.5
    largeur_barre = L - 66
    for t in resultat["themes"]:
        _ligne_texte(pdf, G, y, 52, t["nom"], taille=9, hauteur=5.5)
        pdf.set_fill_color(*COULEUR_FOND_CARTE)
        pdf.rect(G + 54, y + 1.4, largeur_barre, 3, style="F", round_corners=True, corner_radius=1.5)
        if t["score"] is not None:
            pdf.set_fill_color(*_rgb(audit_fiche.etiquette_score(t["score"])[1]))
            pdf.rect(G + 54, y + 1.4, max(2, largeur_barre * t["score"] / 100), 3, style="F", round_corners=True, corner_radius=1.5)
        _ligne_texte(pdf, G + 56 + largeur_barre, y, 10, f"{t['score']}" if t["score"] is not None else "n/d", style="B", taille=9, align="R", hauteur=5.5)
        y += 6.3

    # ---- priorites
    y = 132
    _titre_bloc(pdf, G, y, "À corriger en priorité")
    y += 5.5
    priorites = resultat["priorites"][:5]
    if not priorites:
        _ligne_texte(pdf, G, y, L, "Aucun point bloquant relevé sur ce que l'on peut vérifier publiquement.", taille=9.5, couleur=COULEURS_STATUT["ok"], hauteur=6)
    for i, p in enumerate(priorites, start=1):
        pdf.set_fill_color(*COULEURS_STATUT.get(p["statut"], COULEUR_ATTENTION))
        pdf.ellipse(G, y + 0.8, 5.2, 5.2, style="F")
        _ligne_texte(pdf, G, y + 1.3, 5.2, str(i), style="B", taille=7.5, couleur=(255, 255, 255), align="C", hauteur=4)
        _ligne_texte(pdf, G + 8, y, L - 38, p["titre"], style="B", taille=9.5, hauteur=4.6)
        _ligne_texte(pdf, G + L - 28, y, 28, f"+{str(p['gain']).replace('.', ',')} pts", style="B", taille=9, couleur=COULEUR_BON, align="R", hauteur=4.6)
        _ligne_texte(pdf, G + 8, y + 4.6, L - 8, p["constat"], taille=8, couleur=COULEUR_GRIS, hauteur=4)
        y += 10.2
    if len(priorites) < 4 and resultat["points_forts"]:                                           # peu de manques : on valorise ce qui est deja bien dans la place restante
        y += 3
        _titre_bloc(pdf, G, y, "Ce qui est déjà bien")
        y += 5.5
        for fort in resultat["points_forts"][:max(2, 9 - 2 * len(priorites))]:
            if y > 184:
                break
            pdf.set_fill_color(*COULEURS_STATUT["ok"])
            pdf.ellipse(G + 0.4, y + 1.1, 3.6, 3.6, style="F")
            _ligne_texte(pdf, G + 8, y, 62, fort["libelle"], style="B", taille=9, hauteur=5)
            _ligne_texte(pdf, G + 72, y, L - 72, fort["detail"], taille=8, couleur=COULEUR_GRIS, hauteur=5)
            y += 6

    # ---- deux colonnes : IA et carte
    y = 192
    largeur_col = (L - 6) / 2
    pdf.set_fill_color(*COULEUR_FOND_CARTE)
    pdf.rect(G, y, largeur_col, 52, style="F", round_corners=True, corner_radius=2.5)
    pdf.rect(G + largeur_col + 6, y, largeur_col, 52, style="F", round_corners=True, corner_radius=2.5)
    xi = G + 4
    _titre_bloc(pdf, xi, y + 3, "Dans ChatGPT et Gemini")
    question = (ia.get("questions") or [{}])[0]
    yy = y + 9
    if question.get("question"):
        _ligne_texte(pdf, xi, yy, largeur_col - 8, '"' + question["question"] + '"', style="I", taille=8, couleur=COULEUR_GRIS, hauteur=4.2)
        yy += 5.5
        for moteur, nom_moteur in (("chatgpt", "ChatGPT"), ("gemini", "Gemini")):
            r = question["moteurs"].get(moteur)
            if not r or r.get("erreur"):
                texte, couleur_r = "indisponible", COULEUR_GRIS
            else:
                texte, couleur_r = ("entreprise citée", COULEURS_STATUT["ok"]) if r["citee"] else ("entreprise ABSENTE", COULEURS_STATUT["probleme"])
            _ligne_texte(pdf, xi, yy, 20, nom_moteur, style="B", taille=9, hauteur=5)
            _ligne_texte(pdf, xi + 20, yy, largeur_col - 28, texte, style="B", taille=9, couleur=couleur_r, hauteur=5)
            yy += 5.5
        concurrents = (ia.get("concurrents_cites") or [])[:4]
        if concurrents:
            yy += 1.5
            _ligne_texte(pdf, xi, yy, largeur_col - 8, "L'IA recommande :", style="B", taille=8, couleur=COULEUR_GRIS, hauteur=4.2)
            yy += 4.4
            for c in concurrents:
                _ligne_texte(pdf, xi + 2, yy, largeur_col - 10, "- " + c["nom"], taille=8.5, hauteur=4.4)
                yy += 4.4
        if ia.get("domaines"):
            _ligne_texte(pdf, xi, y + 46.5, largeur_col - 8, "Sources : " + ", ".join(d["domaine"] for d in ia["domaines"][:3]), taille=7, couleur=COULEUR_GRIS, hauteur=4)
    else:
        _ligne_texte(pdf, xi, yy, largeur_col - 8, "Visibilité IA non analysée.", taille=9, couleur=COULEUR_GRIS, hauteur=5)
    xc = G + largeur_col + 6 + 4
    _titre_bloc(pdf, xc, y + 3, "Visibilité sur la carte")
    if releves:
        r = releves[0]
        _ligne_texte(pdf, xc, y + 9, largeur_col - 8, '"' + r["mot_cle"] + '"', style="I", taille=8, couleur=COULEUR_GRIS, hauteur=4.2)
        points = r.get("points") or []
        taille = int(round(len(points) ** 0.5)) or 1
        cote = min(8.2, 30 / taille)
        x0, y0 = xc, y + 15
        for ligne in range(taille):
            for col in range(taille):
                idx = (taille - 1 - ligne) * taille + col
                p = points[idx].get("position") if idx < len(points) else None
                pdf.set_fill_color(*_couleur_position(p))
                pdf.rect(x0 + col * (cote + 1), y0 + ligne * (cote + 1), cote, cote, style="F", round_corners=True, corner_radius=1)
                if p is not None and p <= 10:
                    _ligne_texte(pdf, x0 + col * (cote + 1), y0 + ligne * (cote + 1) + cote / 2 - 2, cote, str(p), style="B", taille=7, couleur=(255, 255, 255), align="C", hauteur=4)
        xd = x0 + taille * (cote + 1) + 3
        largeur_d = largeur_col - (xd - xc) - 4
        _ligne_texte(pdf, xd, y0, largeur_d, f"{r['resume']['pourcentage_couverture']} % visible", style="B", taille=10, hauteur=5.5)
        if r["resume"].get("position_moyenne"):
            _ligne_texte(pdf, xd, y0 + 5.5, largeur_d, f"position moy. {str(r['resume']['position_moyenne']).replace('.', ',')}", taille=8.5, couleur=COULEUR_GRIS, hauteur=4.5)
        for k, c in enumerate((r.get("concurrents") or [])[:3]):
            _ligne_texte(pdf, xd, y0 + 12 + k * 4.4, largeur_d, ("Devant : " if k == 0 else "") + c["nom"], taille=8, couleur=COULEUR_GRIS if k == 0 else COULEUR_TEXTE, hauteur=4.4)
    else:
        _ligne_texte(pdf, xc, y + 9, largeur_col - 8, "Positions non mesurées.", taille=9, couleur=COULEUR_GRIS, hauteur=5)

    # ---- pied : contact
    y = 250
    pdf.set_fill_color(*COULEUR_NAVY)
    pdf.rect(G, y, L, 28, style="F", round_corners=True, corner_radius=3)
    _ligne_texte(pdf, G + 6, y + 3.5, L - 12, "On corrige tout ça ensemble ?", style="B", taille=12, couleur=(255, 255, 255), hauteur=6)
    _ligne_texte(pdf, G + 6, y + 10, L - 12, "Mise à jour de la fiche, avis, annuaires, visibilité dans les IA : je m'en occupe.", taille=8.5, couleur=(220, 226, 240), hauteur=5)
    _ligne_texte(pdf, G + 6, y + 15.5, L - 12, "hello@jonathanhauet.fr  -  www.jonathanhauet.com", style="B", taille=8.5, couleur=COULEUR_AMBRE, hauteur=5)
    if lien_public:
        _ligne_texte(pdf, G + 6, y + 21, L - 12, "Audit détaillé : " + lien_public, taille=8, couleur=(220, 226, 240), hauteur=5)
    _ligne_texte(pdf, G, 280, L, "Données publiques (Google Maps, site, annuaires, ChatGPT, Gemini). Les réponses des IA varient d'un jour à l'autre : indicateur, pas garantie.", taille=6.5, couleur=COULEUR_GRIS, hauteur=4)
    return bytes(pdf.output())
