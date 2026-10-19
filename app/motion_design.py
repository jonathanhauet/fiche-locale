"""
Motion design des montages (sous-titres et titres), ecrit en evenements ASS rendus par ffmpeg (libass) : aucune dependance en plus.

Principe : chaque mot est place a la main (position calculee a partir de la vraie largeur de la police), ce qui permet ce que les
sous-titres classiques ne savent pas faire : une pastille arrondie qui saute de mot en mot pour suivre la voix, des mots qui
montent en cascade, un titre dont chaque mot sort d'un masque avec une courbe d'acceleration, une barre de couleur qui balaie le mot cle.
Les polices viennent de static/polices (voir fontsdir dans montage_parole.rendre) : le rendu est le meme sur tous les serveurs.
"""

import os
import re

from PIL import ImageFont

DOSSIER_POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "polices")
FACTEUR_POLICE_ASS = 0.558      # taille ASS (\fs) -> taille reelle de la police en pixels, mesuree sur le rendu de libass avec Poppins Bold
BLANC, NOIR_DOUX = (255, 255, 255), (17, 17, 17)
_POLICES = {}


def horodatage(secondes: float) -> str:
    centiemes = max(0, int(round(secondes * 100)))
    return f"{centiemes // 360000}:{centiemes // 6000 % 60:02d}:{centiemes // 100 % 60:02d}.{centiemes % 100:02d}"


def couleur(rgb: tuple) -> str:
    """Couleur pour une balise ASS (\\1c...) : &HBBGGRR&."""
    return "&H{:02X}{:02X}{:02X}&".format(rgb[2], rgb[1], rgb[0])


def luminance(rgb: tuple) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def mesurer(texte: str, taille: float, police: str = "Poppins-Bold.ttf") -> float:
    """Largeur en pixels du texte tel que libass l'affiche a la taille ASS donnee."""
    echelle = 4
    taille_reelle = max(6, int(round(taille * FACTEUR_POLICE_ASS * echelle)))
    cle = (police, taille_reelle)
    if cle not in _POLICES:
        _POLICES[cle] = ImageFont.truetype(os.path.join(DOSSIER_POLICES, police), taille_reelle)
    return _POLICES[cle].getlength(texte) / echelle


def rect_arrondi(largeur: float, hauteur: float, rayon: float) -> str:
    """Dessin ASS (a utiliser avec \\p1) d'un rectangle aux coins arrondis, de 0,0 a largeur,hauteur."""
    r = max(1.0, min(rayon, largeur / 2, hauteur / 2))
    k = r * 0.45
    w, h = largeur, hauteur
    return (f"m {r:.1f} 0 l {w - r:.1f} 0 b {w - k:.1f} 0 {w:.1f} {k:.1f} {w:.1f} {r:.1f} l {w:.1f} {h - r:.1f} b {w:.1f} {h - k:.1f} {w - k:.1f} {h:.1f} {w - r:.1f} {h:.1f} "
            f"l {r:.1f} {h:.1f} b {k:.1f} {h:.1f} 0 {h - k:.1f} 0 {h - r:.1f} l 0 {r:.1f} b 0 {k:.1f} {k:.1f} 0 {r:.1f} 0")


def _evenement(couche: int, debut: float, fin: float, balises: str, texte: str) -> str:
    return f"Dialogue: {couche},{horodatage(debut)},{horodatage(max(fin, debut + 0.05))},Mot,,0,0,0,,{{{balises}}}{texte}"


# --------------------------------------------------------------------------- sous-titres « spotlight »

def sous_titres_bloc(mots: list, coupures: set, debut: float, fin: float, largeur: int, hauteur: int, taille: int, bas: int,
                     accent: tuple, pilule: tuple) -> list:
    """
    Un bloc de sous-titres (une ou deux lignes) : tous les mots apparaissent en cascade, une pastille arrondie suit le mot prononce.
    mots : [{"affiche", "debut", "fin", "cle"}] ; coupures : indices de mots qui commencent la 2e ligne ; bas : ordonnee du bas du bloc.
    """
    lignes, courante = [], []
    for i in range(len(mots)):
        if i in coupures and courante:
            lignes.append(courante)
            courante = []
        courante.append(i)
    lignes.append(courante)
    largeurs = [mesurer(m["affiche"], taille) for m in mots]
    em = taille * FACTEUR_POLICE_ASS
    espace = 0.36 * em
    largeur_lignes = [sum(largeurs[i] for i in ligne) + espace * (len(ligne) - 1) for ligne in lignes]
    if max(largeur_lignes) > largeur * 0.92 and taille > 30:     # trop large : on reduit la taille du bloc
        return sous_titres_bloc(mots, coupures, debut, fin, largeur, hauteur, int(taille * largeur * 0.92 / max(largeur_lignes)), bas, accent, pilule)
    hauteur_ligne = 0.86 * taille
    haut = bas - len(lignes) * hauteur_ligne
    marge = 0.20 * em
    texte_pilule = BLANC if luminance(pilule) < 0.62 else NOIR_DOUX
    evenements = []
    for numero_ligne, ligne in enumerate(lignes):
        x = (largeur - largeur_lignes[numero_ligne]) / 2
        y = haut + numero_ligne * hauteur_ligne
        for i in ligne:
            m = mots[i]
            w = largeurs[i]
            suivant = mots[i + 1]["debut"] if i + 1 < len(mots) else max(m["fin"], m["debut"] + 0.25)
            debut_mot = debut + min(0.12, 0.03 * i)                          # cascade a l'apparition
            actif_a = max(0, int((m["debut"] - debut_mot) * 1000))
            actif_b = int((min(suivant, fin) - debut_mot) * 1000)
            couleur_repos = couleur(accent) if m.get("cle") and accent != pilule else couleur(BLANC)
            couleur_active = couleur(accent) if (m.get("cle") and accent != pilule and luminance(pilule) < 0.62) else couleur(texte_pilule)
            bascule = f"\\1c{couleur_repos}\\t({max(0, actif_a - 30)},{actif_a + 50},\\1c{couleur_active})"
            if i + 1 < len(mots):
                bascule += f"\\t({actif_b - 20},{actif_b + 80},\\1c{couleur_repos})"
            base = f"\\an7\\fnPoppins\\b1\\fs{taille}\\bord0\\fad(90,150)"
            evenements.append(_evenement(0, debut_mot, fin, f"{base}\\shad0\\1c&H000000&\\1a&H70&\\blur7\\move({x:.0f},{y + 22:.0f},{x:.0f},{y + 6:.0f},0,150)", m["affiche"]))
            evenements.append(_evenement(2, debut_mot, fin, f"{base}\\shad0\\blur0\\move({x:.0f},{y + 16:.0f},{x:.0f},{y:.0f},0,150){bascule}", m["affiche"]))
            largeur_pilule, hauteur_pilule = w + 2 * marge, 0.68 * taille
            debut_pilule = max(m["debut"], debut_mot)
            fin_pilule = min(suivant, fin) if i + 1 < len(mots) else fin
            if fin_pilule - debut_pilule >= 0.04:
                sortie = "\\fad(30,150)" if i + 1 == len(mots) else "\\fad(30,0)"
                evenements.append(_evenement(
                    1, debut_pilule, fin_pilule,
                    f"\\an5\\pos({x + w / 2:.0f},{y + 0.50 * taille:.0f})\\p1\\bord0\\shad0\\blur0\\1c{couleur(pilule)}\\fscx86\\fscy86\\t(0,120,0.5,\\fscx100\\fscy100){sortie}",
                    rect_arrondi(largeur_pilule, hauteur_pilule, 0.30 * hauteur_pilule) + "{\\p0}"))
            x += w + espace
    return evenements


# --------------------------------------------------------------------------- titre cinetique

# Courbe d'acceleration d'un mot qui monte (fractions du trajet, instants en ms) : rapide au depart, doux a l'arrivee.
COURBE_MONTEE = [(0, 0.00), (70, 0.60), (150, 0.86), (240, 0.97), (340, 1.00)]


def _lignes_titre(mots: list, taille: int, largeur_max: float) -> list:
    espace = 0.40 * taille * FACTEUR_POLICE_ASS
    lignes, courante, largeur_courante = [], [], 0.0
    for mot in mots:
        w = mesurer(mot, taille)
        if courante and largeur_courante + espace + w > largeur_max:
            lignes.append(courante)
            courante, largeur_courante = [], 0.0
        largeur_courante += (espace if courante else 0) + w
        courante.append(mot)
    if courante:
        lignes.append(courante)
    return lignes


def titre_cinetique(debut: float, fin: float, texte: str, largeur: int, hauteur: int, accent: tuple) -> list:
    """
    Titre d'accroche en motion design : chaque mot monte d'un masque en deux temps (rapide puis doux), les mots se suivent en cascade,
    une barre de la couleur d'accent balaie le dernier mot, un trait s'etire au-dessus. Sortie en fondu.
    """
    mots = [m for m in re.sub(r"[{}\\]", "", texte or "").split() if m]
    if not mots:
        return []
    mots[0] = mots[0][:1].upper() + mots[0][1:]
    vertical = hauteur > largeur
    taille = int(largeur * (0.150 if vertical else 0.066))
    lignes = _lignes_titre(mots, taille, largeur * 0.84)
    while len(lignes) > 3 and taille > 40:
        taille = int(taille * 0.9)
        lignes = _lignes_titre(mots, taille, largeur * 0.84)
    em = taille * FACTEUR_POLICE_ASS
    espace = 0.40 * em
    hauteur_ligne = 0.80 * taille
    haut = hauteur * (0.085 if vertical else 0.07) + 0.34 * taille
    nombre_mots = len(mots)
    mot_cle = nombre_mots - 1 if (len(mots[-1]) >= 3 or nombre_mots == 1) else nombre_mots - 2
    texte_barre = NOIR_DOUX if luminance(accent) >= 0.45 else BLANC
    evenements, rang = [], 0
    premiere_ligne_x = None
    for numero_ligne, ligne in enumerate(lignes):
        largeurs = [mesurer(m, taille) for m in ligne]
        largeur_ligne = sum(largeurs) + espace * (len(ligne) - 1)
        x = (largeur - largeur_ligne) / 2
        y = haut + numero_ligne * hauteur_ligne
        if premiere_ligne_x is None:
            premiere_ligne_x = x
        masque = f"\\clip({x - 40:.0f},{y - 6:.0f},{x + largeur_ligne + 40:.0f},{y + 1.06 * taille:.0f})"
        for j, mot in enumerate(ligne):
            w = largeurs[j]
            debut_mot = debut + 0.11 * rang
            decalage = 0.95 * taille                                         # le mot part sous le bord du masque
            positions = [(t, y + decalage * (1 - f)) for t, f in COURBE_MONTEE]
            cle = rang == mot_cle
            for (t0, y0), (t1, y1) in zip(positions, positions[1:]):
                evenements.append(_evenement(
                    2, debut_mot + t0 / 1000, debut_mot + t1 / 1000,
                    f"\\an7\\fnPoppins\\b1\\fs{taille}\\bord0\\shad0\\blur0\\1c{couleur(BLANC)}{masque}\\move({x:.0f},{y0:.0f},{x:.0f},{y1:.0f},0,{t1 - t0})", mot))
            fin_montee = debut_mot + COURBE_MONTEE[-1][0] / 1000
            balises_fixes = f"\\an7\\fnPoppins\\b1\\fs{taille}\\bord0\\shad0\\blur0\\pos({x:.0f},{y:.0f}){masque}\\fad(0,250)"
            if cle:
                balayage = fin_montee + 0.05
                balises_fixes += f"\\1c{couleur(BLANC)}\\t({int((balayage - fin_montee) * 1000) + 60},{int((balayage - fin_montee) * 1000) + 260},\\1c{couleur(texte_barre)})"
            else:
                balises_fixes += f"\\1c{couleur(BLANC)}"
            ombre = (f"\\an7\\fnPoppins\\b1\\fs{taille}\\bord0\\shad0\\1c&H000000&\\1a&H78&\\blur8\\pos({x:.0f},{y + 5:.0f}){masque}\\fad(0,250)")
            evenements.append(_evenement(0, debut_mot + 0.12, fin, ombre, mot))
            evenements.append(_evenement(2, fin_montee, fin, balises_fixes, mot))
            if cle:
                haut_barre, bas_barre = y + 0.16 * taille, y + 0.86 * taille
                gauche, droite = x - 0.17 * em, x + w + 0.17 * em
                evenements.append(_evenement(
                    1, fin_montee + 0.05, fin,
                    f"\\an7\\pos({gauche:.0f},{haut_barre:.0f})\\p1\\bord0\\shad0\\blur0\\1c{couleur(accent)}\\clip({gauche - 4:.0f},{haut_barre - 4:.0f},{gauche - 4:.0f},{bas_barre + 4:.0f})"
                    f"\\t(0,300,0.45,\\clip({gauche - 4:.0f},{haut_barre - 4:.0f},{droite + 4:.0f},{bas_barre + 4:.0f}))\\fad(0,250)",
                    rect_arrondi(droite - gauche, bas_barre - haut_barre, 0.28 * (bas_barre - haut_barre)) + "{\\p0}"))
            x += w + espace
            rang += 1
    # trait d'accent qui s'etire au-dessus du titre
    y_trait = haut - 0.30 * taille
    longueur = largeur * 0.16
    evenements.append(_evenement(
        1, debut + 0.02, fin,
        f"\\an7\\pos({premiere_ligne_x:.0f},{y_trait:.0f})\\p1\\bord0\\shad0\\blur0\\1c{couleur(accent)}\\fscx2\\t(0,380,0.4,\\fscx100)\\fad(0,250)",
        rect_arrondi(longueur, max(8, 0.075 * taille), 99) + "{\\p0}"))
    return evenements


# --------------------------------------------------------------------------- balayage de couleur (transition sur une coupe)

DUREE_BALAYAGE = 0.50


def balayage(debut: float, fin: float, largeur: int, hauteur: int, couleur_1: tuple, couleur_2: tuple) -> list:
    """
    Transition « balayage » : deux bandes de couleur obliques traversent l'ecran de gauche a droite et recouvrent l'image un instant,
    le temps de la coupe (qui se trouve au milieu de la fenetre). La deuxieme bande suit la premiere avec un leger retard.
    """
    duree = max(0.3, fin - debut)
    longueur = largeur * 1.25
    biais = largeur * 0.12
    forme = f"m {biais:.0f} 0 l {longueur:.0f} 0 l {longueur - biais:.0f} {hauteur} l 0 {hauteur}"
    evenements = []
    for rang, (couleur_bande, couche) in enumerate(((couleur_2, 4), (couleur_1, 5))):
        retard = 0.06 * (1 - rang)
        d = debut + retard
        evenements.append(_evenement(
            couche, d, d + duree - 0.06,
            f"\\an7\\move({-longueur:.0f},0,{largeur:.0f},0,0,{int((duree - 0.06) * 1000)})\\p1\\bord0\\shad0\\blur0\\1c{couleur(couleur_bande)}", forme + "{\\p0}"))
    return evenements
