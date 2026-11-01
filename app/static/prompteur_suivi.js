/*
 * Suivi de lecture du prompteur : compare ce que la voix vient de dire au texte pour savoir ou en est le lecteur.
 * Fonctions pures (aucun acces a la page) : elles sont testees dans un vrai navigateur par tests_plateforme/test_prompteur_suivi.py.
 *
 * Principe : le texte est decoupe en mots ; apres chaque extrait de voix transcrit, on cherche a quel endroit du texte (devant la position actuelle, ou
 * juste derriere) se terminent le mieux les derniers mots dits. Un mot long et rare compte plus qu'un « de » ; une correspondance approximative est
 * acceptee (accents, pluriel, mot mal ecrit par la transcription). Un saut loin devant exige plus de preuves qu'une avancee de quelques mots.
 */
(function (racine) {
  "use strict";

  function normaliser(mot) {
    return String(mot).toLowerCase().replace(/œ/g, "oe").replace(/æ/g, "ae").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]/g, "");
  }

  function decouper(texte) {
    return String(texte || "").split(/\s+/).filter(Boolean);
  }

  function distance(a, b, maximum) {
    if (a === b) return 0;
    if (Math.abs(a.length - b.length) > maximum) return maximum + 1;
    let precedent = [];
    for (let j = 0; j <= b.length; j++) precedent.push(j);
    for (let i = 1; i <= a.length; i++) {
      const courant = [i];
      for (let j = 1; j <= b.length; j++) {
        courant.push(Math.min(precedent[j] + 1, courant[j - 1] + 1, precedent[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)));
      }
      precedent = courant;
    }
    return precedent[b.length];
  }

  // Deux mots (deja normalises) sont « proches » s'ils sont identiques, ou longs et a une ou deux lettres pres.
  function proches(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    const longueur = Math.max(a.length, b.length);
    if (longueur < 5) return false;                       // « de » / « du », « la » / « le » : aucune tolerance sur les mots courts
    const tolerance = longueur >= 9 ? 2 : 1;
    return distance(a, b, tolerance) <= tolerance;
  }

  function poids(mot) {
    return mot.length >= 7 ? 3 : (mot.length >= 4 ? 2 : 1);
  }

  // Score d'un alignement qui se termine juste avant le mot d'indice e : on remonte le texte et les mots dits en parallele, en tolerant un mot du
  // texte non prononce ou un mot parasite dit en plus (deux a la suite au plus).
  function scoreFin(normes, e, dits, jDepart) {
    let i = e - 1, j = jDepart, score = 0, correspondances = 0, sautsTexte = 0, sautsDits = 0;
    while (i >= 0 && j >= 0) {
      if (normes[i] === "") { i--; continue; }            // ponctuation seule : ne se prononce pas
      if (proches(normes[i], dits[j])) {
        score += poids(dits[j]); correspondances++; i--; j--; sautsTexte = 0; sautsDits = 0;
        continue;
      }
      // un mot saute coute un peu : a preuves egales, la position qui n'en saute aucun l'emporte (un extrait renvoye ne fait pas avancer le texte)
      if (sautsTexte < 2 && i > 0 && proches(normes[i - 1], dits[j])) { i--; sautsTexte++; score -= 0.6; continue; }
      if (sautsDits < 2 && j > 0 && proches(normes[i], dits[j - 1])) { j--; sautsDits++; score -= 0.3; continue; }
      break;
    }
    return { score: score, correspondances: correspondances };
  }

  /*
   * normes : mots du texte normalises (voir normaliser) ; position : indice du prochain mot a lire ; dits : mots entendus (normalises).
   * Renvoie { position, score, bouge } : la nouvelle position, ou l'ancienne si rien de convaincant n'a ete entendu.
   */
  function avancer(normes, position, dits, options) {
    const o = Object.assign({ fenetreAvant: 4, fenetreApres: 70, queue: 10, seuilSaut: 5, seuilLocal: 3, zoneLocale: 8 }, options || {});
    const queue = (dits || []).filter(Boolean).slice(-o.queue);
    if (!queue.length) return { position: position, score: 0, bouge: false };
    const debut = Math.max(1, position - o.fenetreAvant), fin = Math.min(normes.length, position + o.fenetreApres);
    let meilleur = null;
    for (let e = debut; e <= fin; e++) {
      for (let retrait = 0; retrait <= 1 && queue.length - 1 - retrait >= 0; retrait++) {   // le dernier mot entendu peut etre mal transcrit : on essaie sans lui
        const { score, correspondances } = scoreFin(normes, e, queue, queue.length - 1 - retrait);
        if (!score) continue;
        const local = e >= position - 2 && e <= position + o.zoneLocale;
        if (score < (local ? o.seuilLocal : o.seuilSaut)) continue;
        const note = score - Math.abs(e - position - correspondances) * 0.02 - retrait * 0.5;   // a score egal : l'endroit le plus proche de la lecture attendue
        if (!meilleur || note > meilleur.note) meilleur = { position: e, score: score, note: note };
      }
    }
    if (!meilleur) return { position: position, score: 0, bouge: false };
    return { position: meilleur.position, score: meilleur.score, bouge: meilleur.position !== position };
  }

  // Mots difficiles a reconnaitre (noms propres, termes longs), donnes a Whisper comme vocabulaire : jamais une phrase du texte (voir main.py).
  function vocabulaire(mots, maximum) {
    const vus = new Set(), choisis = [];
    mots.forEach(function (mot) {
      const propre = mot.replace(/[^\p{L}\p{N}'’-]/gu, "");
      const cle = normaliser(propre);
      if (cle.length >= 7 && !vus.has(cle)) { vus.add(cle); choisis.push(propre); }
    });
    // ordre alphabetique : la liste ne reproduit pas l'ordre du texte
    return choisis.sort(function (a, b) { return a.localeCompare(b, "fr"); }).slice(0, maximum || 30).join(", ");
  }

  function dureeEstimee(nbMots, motsParMinute) {
    return Math.round(nbMots / Math.max(40, motsParMinute || 150) * 60);
  }

  const api = { normaliser: normaliser, decouper: decouper, proches: proches, avancer: avancer, vocabulaire: vocabulaire, dureeEstimee: dureeEstimee };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else racine.PrompteurSuivi = api;
})(typeof window !== "undefined" ? window : this);
