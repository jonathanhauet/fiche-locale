"""
Checklist de la conference « Dominer Google Maps » (Ales, Cevennes Tourisme, 24/11/2026).

- /ales : page publique (QR code de la derniere slide). Le visiteur laisse ses coordonnees.
- Un e-mail part aussitot (Brevo, au nom de Jonathan Hauet) avec un lien personnel vers la checklist : l'adresse est
  ainsi verifiee. Le premier clic sur le lien confirme l'inscription (double opt-in).
- /ales/checklist/<jeton> : la checklist a cocher en ligne ; /ales/checklist/<jeton>/pdf : la meme en PDF.
- /ales/inscrits (connexion requise) : liste des inscrits ; /ales/inscrits.csv : export.
- Si CHECKLIST_BREVO_LISTE_ID est defini, les inscrits confirmes qui ont accepte les conseils sont ajoutes a cette
  liste de contacts Brevo (jamais les autres).
"""

import csv
import io
import os
import re
import secrets
from datetime import datetime, timedelta
from time import monotonic

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from . import brevo_email
from .database import Base, obtenir_session

DOSSIER_APP = os.path.dirname(os.path.abspath(__file__))
routeur = APIRouter()
_templates = Jinja2Templates(directory=os.path.join(DOSSIER_APP, "templates"))

NOM_EXPEDITEUR = "Jonathan Hauët"
TITRE = "Dominer Google Maps : la checklist"
ENTETES = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
ACTIVITES = ("Hébergement", "Restauration", "Activités de loisirs et de pleine nature", "Site de visite, musée",
             "Commerce, producteur", "Office de tourisme, institution", "Autre")
FORMAT_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_ENVOIS_PAR_HEURE = 3            # par adresse : renvoi du lien si la personne se reinscrit
MAX_DEMANDES_PAR_IP = 60            # par 10 minutes : toute la salle peut partager le meme Wi-Fi
LONGUEUR_MAX = 120

# Le contenu de la checklist : (titre de la section, [cases a cocher]).
CHECKLIST = [
    ("Votre fiche : les bases", [
        "Ma catégorie principale est celle des 3 premiers sur ma recherche principale",
        "J’ai ajouté les catégories secondaires qui correspondent à une vraie activité (jusqu’à 9)",
        "Le nom de ma fiche est mon vrai nom, sans mot-clé ni ville ajoutés",
        "Adresse, téléphone et site : identiques à mon site et à toutes mes autres fiches",
        "Mon lien de réservation (ou de rendez-vous) est renseigné",
        "Tous mes attributs sont cochés : piscine, animaux acceptés, accès PMR, Wi-Fi, parking, terrasse…",
        "Ma description (750 caractères) dit l’essentiel dans les 250 premiers, avec mes villages et mes prestations",
        "Mes services, produits ou menus sont renseignés",
    ]),
    ("Les horaires, surtout en basse saison", [
        "Mes horaires d’hiver sont à jour",
        "Fermeture de 7 jours ou plus : fiche en « Fermé temporairement » (jamais « Fermé définitivement »)",
        "Les horaires des jours fériés sont saisis à l’avance",
        "Les horaires supplémentaires (petit-déjeuner, cuisine, accueil…) sont renseignés",
    ]),
    ("Des photos qui parlent", [
        "Au moins 20 photos, dont la façade ou l’entrée et l’intérieur",
        "4 à 5 photos par prestation, prises au téléphone, en situation",
        "Aucune photo de banque d’images, aucun doublon, pas de texte incrusté",
        "Une nouvelle photo chaque mois",
        "Je regarde les photos ajoutées par les clients et je signale celles qui posent problème",
    ]),
    ("Une fiche vivante", [
        "Un post par semaine, programmé un mois à l’avance",
        "L’accroche dans les 150 premiers caractères ; mes villages et mes prestations dans le texte",
        "Aucun numéro de téléphone dans le texte (le bouton « Appeler » est fait pour ça)",
        "Un post « Offre » actif en permanence",
        "La saison, oui ; l’actualité qui ne me concerne pas, non",
    ]),
    ("Des avis qui vendent", [
        "J’ai récupéré mon lien direct et mon QR code (« Demander des avis » dans ma fiche)",
        "Je les place là où le client vit son meilleur moment : livret d’accueil, addition, photo souvenir, e-mail du lendemain",
        "Ma demande d’avis contient 3 ou 4 questions : l’occasion, le coup de cœur, un conseil pour les suivants",
        "Je ne promets jamais rien en échange d’un avis et je ne trie pas les clients mécontents",
        "Je réponds à chaque avis en 2 à 4 phrases, en reprenant un détail, et je signe",
        "Avis négatif : merci, reconnaître, une solution vraie, signer — sous 48 heures",
        "J’ai banni « N’hésitez pas à » de mes réponses",
    ]),
    ("Une présence cohérente partout", [
        "Ma fiche Apple Plans (Apple Business Connect) est revendiquée et à jour",
        "Ma fiche Bing Places est à jour",
        "TripAdvisor, PagesJaunes, Booking et les autres plateformes : mêmes informations, mêmes horaires",
        "Ma fiche sur le site de Cévennes Tourisme est à jour",
        "Chaque semaine, je vérifie que personne n’a modifié mon nom, mon téléphone, ma catégorie ou mon statut",
    ]),
    ("Être recommandé par les IA", [
        "J’ai posé à ChatGPT et à Gemini les 3 questions que mes clients me posent : qui est cité ? avec quelles sources ?",
        "Mon site laisse passer les robots des IA (robots.txt, pare-feu)",
        "Mon site a des données structurées LocalBusiness précises (Hotel, Restaurant…)",
        "Mon site a une FAQ de 5 à 8 vraies questions de clients",
        "Mon site affiche du texte lisible sans JavaScript et un seul titre principal (H1)",
    ]),
    ("Mesurer et tenir dans la durée", [
        "Je suis les appels, les itinéraires, les clics et les réservations (onglet « Performances »)",
        "Le lien de mon site sur la fiche contient un paramètre de suivi (UTM)",
        "Je compare toujours à la même période de l’année précédente",
        "Ma routine du lundi : 15 minutes, une personne responsable",
    ]),
]


class InscriptionChecklistAles(Base):
    """Une personne qui a demande la checklist ; confirme_le est rempli au premier clic sur le lien recu par e-mail."""

    __tablename__ = "inscriptions_checklist_ales"

    id = Column(Integer, primary_key=True)
    prenom = Column(String, default="")
    nom = Column(String, default="")
    email = Column(String, index=True, nullable=False)
    etablissement = Column(String, default="")
    activite = Column(String, default="")
    commune = Column(String, default="")
    accepte_conseils = Column(Boolean, default=False)
    jeton = Column(String(64), unique=True, nullable=False)
    cree_le = Column(DateTime, default=datetime.utcnow)
    confirme_le = Column(DateTime, nullable=True)
    nb_envois = Column(Integer, default=0)
    dernier_envoi_le = Column(DateTime, nullable=True)
    envois_heure_json = Column(Text, default="")       # horodatages ISO des envois de la derniere heure, separes par des virgules
    erreur_envoi = Column(Text, default="")
    ajoute_liste_brevo = Column(Boolean, default=False)


_demandes_ip: dict = {}


def _trop_de_demandes(request: Request) -> bool:
    adresse = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "?")
    maintenant = monotonic()
    recentes = [t for t in _demandes_ip.get(adresse, []) if maintenant - t < 600]
    if len(_demandes_ip) > 5000:
        _demandes_ip.clear()
    recentes.append(maintenant)
    _demandes_ip[adresse] = recentes
    return len(recentes) > MAX_DEMANDES_PAR_IP


def _propre(valeur) -> str:
    return " ".join(str(valeur or "").split())[:LONGUEUR_MAX]


def _url_publique(request: Request) -> str:
    return (os.getenv("URL_PUBLIQUE_CHECKLIST") or str(request.base_url)).rstrip("/")


def _contenu_email(prenom: str, lien: str) -> str:
    bonjour = f"Bonjour {prenom}," if prenom else "Bonjour,"
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.55;color:#13262E;max-width:560px">
<p>{bonjour}</p>
<p>Merci d’avoir assisté à la conférence <strong>« Dominer Google Maps : l’art de capter le voyageur »</strong> à Alès.</p>
<p>Comme promis, voici votre checklist : tout ce qu’il faut vérifier sur votre fiche Google, à cocher à votre rythme, et à télécharger en PDF.</p>
<p style="margin:28px 0"><a href="{lien}" style="background:#1F9E92;color:#ffffff;text-decoration:none;font-weight:bold;padding:14px 26px;border-radius:30px;display:inline-block">Ouvrir ma checklist</a></p>
<p style="font-size:14px;color:#3E4E55">Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br><a href="{lien}" style="color:#157A70">{lien}</a></p>
<p>Une question en appliquant la checklist ? Répondez simplement à cet e-mail.</p>
<p>Belle saison à vous,<br><strong>Jonathan Hauët</strong><br>Expert Produit Google · Référencement local</p>
<p style="font-size:12px;color:#5E6E74;margin-top:28px">Vous recevez cet e-mail parce que votre adresse a été saisie sur la page de la checklist de la conférence. Si ce n’est pas vous, ignorez-le : rien ne vous sera envoyé d’autre.</p>
</div>"""


def _envoyer_lien(inscription: InscriptionChecklistAles, request: Request) -> None:
    """Envoie (ou renvoie) le lien personnel. Leve RuntimeError si l'envoi est impossible."""
    maintenant = datetime.utcnow()
    recents = [d for d in (inscription.envois_heure_json or "").split(",") if d]
    recents = [d for d in recents if maintenant - datetime.fromisoformat(d) < timedelta(hours=1)]
    if len(recents) >= MAX_ENVOIS_PAR_HEURE:
        return                                                    # deja envoye plusieurs fois : on n'insiste pas, la page reste la meme
    lien = f"{_url_publique(request)}/ales/checklist/{inscription.jeton}"
    brevo_email.envoyer_email(
        inscription.email, " ".join(p for p in (inscription.prenom, inscription.nom) if p) or inscription.email,
        "Votre checklist « Dominer Google Maps »", _contenu_email(inscription.prenom, lien), nom_expediteur=NOM_EXPEDITEUR,
        reponse_a=os.getenv("EMAIL_NOTIFICATION_LEADS") or None,
    )
    recents.append(maintenant.isoformat())
    inscription.envois_heure_json = ",".join(recents)
    inscription.nb_envois = (inscription.nb_envois or 0) + 1
    inscription.dernier_envoi_le = maintenant
    inscription.erreur_envoi = ""


def _ajouter_liste_brevo(inscription: InscriptionChecklistAles) -> None:
    liste = os.getenv("CHECKLIST_BREVO_LISTE_ID", "").strip()
    if not (liste.isdigit() and inscription.accepte_conseils and brevo_email.BREVO_API_KEY) or inscription.ajoute_liste_brevo:
        return
    try:
        reponse = requests.post(
            "https://api.brevo.com/v3/contacts",
            headers={"api-key": brevo_email.BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
            json={"email": inscription.email, "listIds": [int(liste)], "updateEnabled": True,
                  "attributes": {"PRENOM": inscription.prenom, "NOM": inscription.nom}},
            timeout=20,
        )
        inscription.ajoute_liste_brevo = reponse.status_code < 300
    except requests.RequestException:
        pass


def _page_inscription(request: Request, **contexte) -> HTMLResponse:
    valeurs = {"erreur": None, "envoye": False, "email": "", "valeurs": {}, "activites": ACTIVITES}
    valeurs.update(contexte)
    return _templates.TemplateResponse(request, "checklist_ales_inscription.html", valeurs,
                                       status_code=400 if valeurs["erreur"] else 200, headers=ENTETES)


@routeur.get("/ales", response_class=HTMLResponse)
def page_inscription_checklist(request: Request):
    return _page_inscription(request)


@routeur.post("/ales", response_class=HTMLResponse)
async def inscription_checklist(request: Request, db: Session = Depends(obtenir_session)):
    formulaire = await request.form()
    if (formulaire.get("site_web_perso") or "").strip():           # piege a robots : reponse normale, rien d'enregistre
        return _page_inscription(request, envoye=True, email="")
    if _trop_de_demandes(request):
        return _page_inscription(request, erreur="Trop de demandes en peu de temps : patientez quelques minutes puis réessayez.", valeurs=formulaire)

    prenom, nom = _propre(formulaire.get("prenom")), _propre(formulaire.get("nom"))
    email = _propre(formulaire.get("email")).lower()
    etablissement, commune = _propre(formulaire.get("etablissement")), _propre(formulaire.get("commune"))
    activite = _propre(formulaire.get("activite"))
    activite = activite if activite in ACTIVITES else ""
    accepte = str(formulaire.get("accepte_conseils") or "") in ("1", "on", "true")

    if not prenom or not email or not etablissement:
        return _page_inscription(request, erreur="Merci d’indiquer au moins votre prénom, votre e-mail et votre établissement.", valeurs=formulaire)
    if not FORMAT_EMAIL.match(email):
        return _page_inscription(request, erreur="Cette adresse e-mail ne semble pas valide : vérifiez-la.", valeurs=formulaire)

    inscription = db.query(InscriptionChecklistAles).filter(InscriptionChecklistAles.email == email).first()
    if inscription is None:
        inscription = InscriptionChecklistAles(email=email, jeton=secrets.token_urlsafe(24))
        db.add(inscription)
    inscription.prenom, inscription.nom, inscription.etablissement = prenom, nom, etablissement
    inscription.activite, inscription.commune = activite, commune
    inscription.accepte_conseils = accepte or bool(inscription.accepte_conseils)
    db.commit()

    try:
        _envoyer_lien(inscription, request)
    except Exception as erreur:                                      # Brevo absent ou en panne : on le dit, l'inscription reste enregistree
        inscription.erreur_envoi = str(erreur)[:500]
        db.commit()
        return _page_inscription(request, erreur="L’e-mail n’a pas pu partir pour le moment. Réessayez dans quelques minutes.", valeurs=formulaire)
    db.commit()
    return _page_inscription(request, envoye=True, email=email)


def _inscription_par_jeton(db: Session, jeton: str):
    if not jeton or len(jeton) > 64:
        return None
    return db.query(InscriptionChecklistAles).filter(InscriptionChecklistAles.jeton == jeton).first()


@routeur.get("/ales/checklist/{jeton}", response_class=HTMLResponse)
def page_checklist(jeton: str, request: Request, db: Session = Depends(obtenir_session)):
    inscription = _inscription_par_jeton(db, jeton)
    if inscription is None:
        return RedirectResponse("/ales", status_code=303)
    if inscription.confirme_le is None:
        inscription.confirme_le = datetime.utcnow()
        _ajouter_liste_brevo(inscription)
        db.commit()
    nb_cases = sum(len(cases) for _, cases in CHECKLIST)
    return _templates.TemplateResponse(request, "checklist_ales.html", {
        "inscription": inscription, "sections": CHECKLIST, "nb_cases": nb_cases, "jeton": jeton,
    }, headers=ENTETES)


def construire_pdf(prenom: str = "", etablissement: str = "") -> bytes:
    from fpdf import FPDF

    polices = os.path.join(DOSSIER_APP, "static", "polices")
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("Poppins", "", os.path.join(polices, "Poppins-Regular.ttf"))
    pdf.add_font("Poppins", "B", os.path.join(polices, "Poppins-Bold.ttf"))
    pdf.set_title(TITRE)
    pdf.set_author(NOM_EXPEDITEUR)
    pdf.add_page()
    pdf.set_fill_color(19, 38, 46)
    pdf.rect(0, 0, 210, 44, style="F")
    pdf.set_text_color(60, 194, 178)
    pdf.set_font("Poppins", "B", 9)
    pdf.set_xy(15, 11)
    pdf.cell(0, 5, "CONFÉRENCE · ALÈS · 24 NOVEMBRE 2026")
    pdf.set_text_color(245, 241, 232)
    pdf.set_font("Poppins", "B", 20)
    pdf.set_xy(15, 18)
    pdf.cell(0, 10, "Dominer Google Maps : la checklist")
    pdf.set_font("Poppins", "", 10)
    pdf.set_xy(15, 30)
    pour = f"Pour {prenom}" + (f" · {etablissement}" if etablissement else "") if prenom else "Jonathan Hauët · Expert Produit Google"
    pdf.cell(0, 6, pour)
    pdf.set_y(52)
    for titre, cases in CHECKLIST:
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_text_color(21, 122, 112)
        pdf.set_font("Poppins", "B", 12)
        pdf.cell(0, 8, titre, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Poppins", "", 10)
        pdf.set_text_color(19, 38, 46)
        for case in cases:
            if pdf.get_y() > 272:
                pdf.add_page()
            y = pdf.get_y()
            pdf.set_draw_color(21, 122, 112)
            pdf.rect(pdf.l_margin, y + 1.3, 3.6, 3.6)
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(0, 6, case, align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.8)
        pdf.ln(3)
    pdf.set_text_color(94, 110, 116)
    pdf.set_font("Poppins", "", 9)
    pdf.multi_cell(0, 5, "Jonathan Hauët · Expert Produit Google, spécialiste du référencement local · jonathanhauet.com", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@routeur.get("/ales/checklist/{jeton}/pdf")
def pdf_checklist(jeton: str, db: Session = Depends(obtenir_session)):
    inscription = _inscription_par_jeton(db, jeton)
    if inscription is None:
        return RedirectResponse("/ales", status_code=303)
    contenu = construire_pdf(inscription.prenom, inscription.etablissement)
    return Response(contenu, media_type="application/pdf", headers={
        **ENTETES, "Content-Disposition": 'attachment; filename="checklist-dominer-google-maps.pdf"'})


@routeur.get("/ales/inscrits", response_class=HTMLResponse)
def liste_inscrits(request: Request, db: Session = Depends(obtenir_session)):
    if not request.session.get("user_id"):
        return RedirectResponse("/connexion", status_code=303)
    inscrits = db.query(InscriptionChecklistAles).order_by(InscriptionChecklistAles.cree_le.desc()).all()
    return _templates.TemplateResponse(request, "checklist_ales_inscrits.html", {
        "inscrits": inscrits, "nb_confirmes": sum(1 for i in inscrits if i.confirme_le),
        "nb_conseils": sum(1 for i in inscrits if i.confirme_le and i.accepte_conseils),
    }, headers=ENTETES)


@routeur.get("/ales/inscrits.csv")
def export_inscrits(request: Request, db: Session = Depends(obtenir_session)):
    if not request.session.get("user_id"):
        return RedirectResponse("/connexion", status_code=303)
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";")
    ecrivain.writerow(["Prénom", "Nom", "E-mail", "Établissement", "Activité", "Commune", "Accepte les conseils", "Inscrit le", "E-mail confirmé le"])
    for i in db.query(InscriptionChecklistAles).order_by(InscriptionChecklistAles.cree_le).all():
        ecrivain.writerow([i.prenom, i.nom, i.email, i.etablissement, i.activite, i.commune, "oui" if i.accepte_conseils else "non",
                           i.cree_le.strftime("%d/%m/%Y %H:%M") if i.cree_le else "", i.confirme_le.strftime("%d/%m/%Y %H:%M") if i.confirme_le else ""])
    return Response("﻿" + tampon.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="inscrits-checklist-ales.csv"'})
