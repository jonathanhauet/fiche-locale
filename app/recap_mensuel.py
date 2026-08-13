"""
Construction du contenu (sujet + HTML) du recap mensuel envoye par email a
chaque client. Mise en forme volontairement "positive" : les chiffres bruts
sont toujours affiches tels quels (une bonne nouvelle en soi), mais une
comparaison par rapport a l'annee precedente n'est ajoutee que si elle est
favorable - on ne cache jamais un chiffre reel, on evite simplement de mettre
en avant une baisse.

HTML avec styles en ligne uniquement (pas de <style>/CSS externe) : les
clients mail ignorent tres largement le CSS externe, ce template n'utilise
donc pas static/style.css.
"""

import os

from dotenv import load_dotenv

from . import models

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PLATEFORME = os.path.dirname(DOSSIER_APP)
load_dotenv(os.path.join(DOSSIER_PLATEFORME, ".env"))

# Numero WhatsApp de l'agence (format international sans espaces ni "+", ex.
# "33612345678"), propose comme second moyen de reponse dans le pied du recap.
WHATSAPP_NUMERO = os.getenv("RECAP_WHATSAPP_NUMERO", "").strip()

LIBELLES_MOIS = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
    7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}

CLES_VUES = [
    "Vues sur Maps (ordinateur)",
    "Vues sur Recherche Google (ordinateur)",
    "Vues sur Maps (mobile)",
    "Vues sur Recherche Google (mobile)",
]

COULEUR_ACCENT = "#4f46e5"
COULEUR_TEXTE = "#1f2937"
COULEUR_DISCRET = "#6b7280"
COULEUR_FOND_CARTE = "#f9fafb"


def construire_sujet(client: models.Client, mois: int, annee: int) -> str:
    return f"Le récap de {LIBELLES_MOIS[mois]} pour ta fiche Google"


def _total_et_evolution(statistiques: dict, comparatif_visibilite, cles: list[str]) -> tuple[int, float]:
    total = sum(statistiques.get(c, 0) for c in cles)
    if not comparatif_visibilite:
        return total, None
    total_n1 = sum(comparatif_visibilite.get(c, {}).get("n1", 0) for c in cles)
    if not total_n1:
        return total, None
    return total, round((total - total_n1) / total_n1 * 100, 1)


def _ligne_stat(libelle: str, valeur, evolution=None) -> str:
    badge = (
        f'<span style="color:#16a34a;font-weight:600;font-size:13px;margin-left:8px;">▲ {evolution}% vs l\'an dernier</span>'
        if evolution is not None and evolution > 0
        else ""
    )
    return f"""
    <tr>
      <td style="padding:8px 0;color:{COULEUR_TEXTE};font-size:15px;">{libelle}</td>
      <td style="padding:8px 0;text-align:right;white-space:nowrap;">
        <strong style="font-size:16px;color:{COULEUR_TEXTE};">{valeur}</strong>{badge}
      </td>
    </tr>"""


def construire_email(
    client: models.Client, donnees: dict, mois: int, annee: int,
    avis_positifs: list[dict], resume_avis_texte: str = None, lien_fiche_google: str = "",
) -> str:
    statistiques = donnees.get("statistiques") or {}
    resume_avis = donnees.get("resume_avis") or {}
    comparatif_visibilite = donnees.get("comparatif_visibilite")
    posts_publies = donnees.get("posts_publies") or []
    mots_cles = donnees.get("mots_cles") or []
    photos_publiees = donnees.get("photos_publiees") or 0
    nom_mois = LIBELLES_MOIS[mois]
    en_mois = f"en {nom_mois}"

    sections = []

    # --- Avis ---
    lignes_avis = []
    nombre_avis = resume_avis.get("nombre_avis_periode") or 0
    if nombre_avis:
        evolution_avis = donnees.get("evolution_avis")
        lignes_avis.append(_ligne_stat(
            f"Nouveaux avis {en_mois}", nombre_avis,
            evolution_avis if evolution_avis is not None and evolution_avis > 0 else None,
        ))
    if resume_avis.get("note_moyenne_globale"):
        lignes_avis.append(_ligne_stat(
            "Note moyenne actuelle", f"{resume_avis['note_moyenne_globale']}/5 ⭐"
            f" ({resume_avis.get('total_avis_global', 0)} avis au total)",
        ))
    if lignes_avis:
        sections.append(f"""
        <h2 style="font-size:17px;color:{COULEUR_TEXTE};margin:28px 0 4px;">⭐ Tes avis</h2>
        <table role="presentation" width="100%" style="border-collapse:collapse;">{"".join(lignes_avis)}</table>""")

    # --- Avis positifs : citation directe s'il n'y en a qu'un, resume IA s'il
    # y en a plusieurs (repli sur 2 citations si le resume echoue/est absent). ---
    if resume_avis_texte:
        sections.append(f"""
        <div style="background:{COULEUR_FOND_CARTE};border-left:3px solid {COULEUR_ACCENT};
                    padding:12px 16px;margin:10px 0;border-radius:4px;">
          <p style="margin:0;color:{COULEUR_TEXTE};font-size:14px;">{resume_avis_texte}</p>
        </div>""")
    elif len(avis_positifs) == 1:
        a = avis_positifs[0]
        sections.append(f"""
        <div style="background:{COULEUR_FOND_CARTE};border-left:3px solid {COULEUR_ACCENT};
                    padding:12px 16px;margin:10px 0;border-radius:4px;">
          <p style="margin:0;color:{COULEUR_TEXTE};font-style:italic;font-size:14px;">« {a['commentaire']} »</p>
          <p style="margin:6px 0 0;color:{COULEUR_DISCRET};font-size:13px;">— {a['auteur']}</p>
        </div>""")
    elif len(avis_positifs) > 1:
        citations = "".join(f"""
        <div style="background:{COULEUR_FOND_CARTE};border-left:3px solid {COULEUR_ACCENT};
                    padding:12px 16px;margin:10px 0;border-radius:4px;">
          <p style="margin:0;color:{COULEUR_TEXTE};font-style:italic;font-size:14px;">« {a['commentaire']} »</p>
          <p style="margin:6px 0 0;color:{COULEUR_DISCRET};font-size:13px;">— {a['auteur']}</p>
        </div>""" for a in avis_positifs[:2])
        sections.append(citations)

    # --- Visibilite ---
    lignes_visi = []
    total_vues, evolution_vues = _total_et_evolution(statistiques, comparatif_visibilite, CLES_VUES)
    if total_vues:
        lignes_visi.append(_ligne_stat("Vues de ta fiche sur Google", total_vues, evolution_vues))
    for libelle in ("Clics sur \"Appeler\"", "Clics vers le site web", "Demandes d'itinéraire", "Messages reçus"):
        valeur = statistiques.get(libelle)
        if valeur:
            evolution = None
            if comparatif_visibilite:
                evolution = comparatif_visibilite.get(libelle, {}).get("evolution")
            lignes_visi.append(_ligne_stat(libelle, valeur, evolution if evolution and evolution > 0 else None))
    if lignes_visi:
        sections.append(f"""
        <h2 style="font-size:17px;color:{COULEUR_TEXTE};margin:28px 0 4px;">📈 Ta visibilité sur Google</h2>
        <table role="presentation" width="100%" style="border-collapse:collapse;">{"".join(lignes_visi)}</table>""")

    # --- Posts publies : avec leur date, pour montrer tout le travail fait
    # dans le mois (pas de plafond - un mois normal en compte peu). ---
    if posts_publies:
        items = "".join(
            f'<li style="margin-bottom:6px;color:{COULEUR_TEXTE};font-size:14px;">'
            f'<span style="color:{COULEUR_DISCRET};font-size:12px;">{p["date"]}</span> — {p["titre"]}</li>'
            for p in posts_publies
        )
        sections.append(f"""
        <h2 style="font-size:17px;color:{COULEUR_TEXTE};margin:28px 0 4px;">📝 Publié sur ta fiche ({len(posts_publies)})</h2>
        <ul style="margin:8px 0;padding-left:20px;">{items}</ul>""")

    # --- Photos ajoutees : compte seulement, pas de plafond a montrer. ---
    if photos_publiees:
        sections.append(f"""
        <p style="color:{COULEUR_TEXTE};font-size:14px;">📷 <strong>{photos_publiees}</strong> photo(s) ajoutée(s) à ta fiche {en_mois}.</p>""")

    # --- Mots-cles de recherche : preuve concrete du travail SEO. ---
    if mots_cles:
        items = "".join(
            f'<li style="margin-bottom:4px;color:{COULEUR_TEXTE};font-size:14px;">'
            f'{"moins de " if m["est_seuil"] else ""}{m["impressions"]} recherche(s) — <strong>{m["mot_cle"]}</strong></li>'
            for m in mots_cles[:5]
        )
        sections.append(f"""
        <h2 style="font-size:17px;color:{COULEUR_TEXTE};margin:28px 0 4px;">🔎 Ce que tes clients ont tapé sur Google</h2>
        <ul style="margin:8px 0;padding-left:20px;">{items}</ul>""")

    contenu_sections = "".join(sections) or (
        f'<p style="color:{COULEUR_DISCRET};font-size:14px;">'
        f"Pas de nouveauté marquante {en_mois} — on continue le travail de fond !</p>"
    )

    prenom_ou_nom = client.prenom.strip() if client.prenom and client.prenom.strip() else client.nom

    bouton_fiche = (
        f'<p style="margin:24px 0 0;">'
        f'<a href="{lien_fiche_google}" style="display:inline-block;background:{COULEUR_ACCENT};color:#ffffff;'
        f'padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">'
        f"Voir ma fiche sur Google →</a></p>"
        if lien_fiche_google else ""
    )

    pied_contact = "Une question sur ta fiche ? Réponds-moi simplement à cet email"
    if WHATSAPP_NUMERO:
        pied_contact += (
            f' ou écris-moi directement sur <a href="https://wa.me/{WHATSAPP_NUMERO}" '
            f'style="color:{COULEUR_ACCENT};">WhatsApp</a>'
        )
    pied_contact += "."

    return f"""<!doctype html>
<html lang="fr">
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" style="background:#f3f4f6;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" style="max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="background:{COULEUR_ACCENT};padding:24px 32px;">
              <p style="margin:0;color:#ffffff;font-size:13px;letter-spacing:0.5px;text-transform:uppercase;">Récap mensuel</p>
              <h1 style="margin:4px 0 0;color:#ffffff;font-size:22px;">{nom_mois.capitalize()} {annee}</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px;">
              <p style="color:{COULEUR_TEXTE};font-size:15px;">Bonjour {prenom_ou_nom},</p>
              <p style="color:{COULEUR_TEXTE};font-size:15px;">Voici ce qui s'est passé sur ta fiche Google {en_mois} :</p>
              {contenu_sections}
              {bouton_fiche}
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px;background:{COULEUR_FOND_CARTE};">
              <p style="margin:0;color:{COULEUR_DISCRET};font-size:13px;">
                {pied_contact}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
