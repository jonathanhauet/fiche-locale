/*
 * Calendrier a selection multiple : choisir plusieurs dates a la fois (une
 * par post genere) sur un seul calendrier toujours visible, plutot que
 * d'ouvrir un champ de date separe (et donc un calendrier separe) pour
 * chaque post. Reprend le rendu visuel du popup de calendrier_champ.js.
 */
window.initCalendrierMulti = function (options) {
  const conteneur = document.getElementById(options.conteneurId);
  if (!conteneur) return;

  const LIBELLES_MOIS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
  ];

  function pad(n) { return String(n).padStart(2, "0"); }
  function versISO(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }

  const joursOccupes = new Set(options.joursOccupes || []);
  const champNombre = document.getElementById(options.champNombreId);
  const conteneurInputs = document.getElementById(options.conteneurInputsId);
  const titre = conteneur.querySelector(".calendrier-multi-titre");
  const grille = conteneur.querySelector(".calendrier-multi-grille");
  const compteur = conteneur.querySelector(".calendrier-multi-compteur");
  const boutonEffacer = conteneur.querySelector(".calendrier-multi-effacer");

  let moisAffiche = new Date();
  let selection = [];

  function maxActuel() {
    return Math.max(1, Number(champNombre.value) || 1);
  }

  function majInputsCaches() {
    selection.sort();
    conteneurInputs.innerHTML = "";
    selection.forEach((iso) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "dates_prevues";
      input.value = iso;
      conteneurInputs.appendChild(input);
    });
  }

  function majCompteur() {
    const max = maxActuel();
    const pluriel = max > 1 ? "s" : "";
    compteur.textContent = `${selection.length}/${max} date${pluriel} sélectionnée${pluriel}`;
  }

  function basculerJour(iso) {
    const index = selection.indexOf(iso);
    if (index !== -1) {
      selection.splice(index, 1);
    } else {
      if (selection.length >= maxActuel()) return;
      selection.push(iso);
    }
    majInputsCaches();
    rendre();
  }

  function rendre() {
    const annee = moisAffiche.getFullYear();
    const mois = moisAffiche.getMonth();
    const premierJourMois = new Date(annee, mois, 1);
    const decalage = (premierJourMois.getDay() + 6) % 7; // lundi = 0
    const debutGrille = new Date(annee, mois, 1 - decalage);
    const aujourdhuiISO = versISO(new Date());

    titre.textContent = `${LIBELLES_MOIS[mois]} ${annee}`;

    let html = "";
    for (let i = 0; i < 42; i++) {
      const jour = new Date(debutGrille);
      jour.setDate(debutGrille.getDate() + i);
      const iso = versISO(jour);
      const estPasse = iso < aujourdhuiISO;
      const classes = ["popup-calendrier-jour"];
      if (jour.getMonth() !== mois) classes.push("popup-calendrier-jour-hors-mois");
      if (iso === aujourdhuiISO) classes.push("popup-calendrier-jour-aujourdhui");
      if (selection.includes(iso)) classes.push("popup-calendrier-jour-selectionne");
      if (joursOccupes.has(iso)) classes.push("popup-calendrier-jour-occupe");
      if (estPasse) classes.push("popup-calendrier-jour-passe");
      html += `<button type="button" class="${classes.join(" ")}" data-iso="${iso}" ${estPasse ? "disabled" : ""}>${jour.getDate()}</button>`;
    }
    grille.innerHTML = html;

    grille.querySelectorAll(".popup-calendrier-jour").forEach((bouton) => {
      bouton.addEventListener("click", () => basculerJour(bouton.dataset.iso));
    });

    majCompteur();
  }

  conteneur.querySelectorAll(".popup-calendrier-nav").forEach((bouton) => {
    bouton.addEventListener("click", () => {
      moisAffiche = new Date(moisAffiche.getFullYear(), moisAffiche.getMonth() + Number(bouton.dataset.sens), 1);
      rendre();
    });
  });

  if (boutonEffacer) {
    boutonEffacer.addEventListener("click", () => {
      selection = [];
      majInputsCaches();
      rendre();
    });
  }

  // Si le nombre de posts diminue en dessous du nombre de dates deja
  // choisies, on retire les dates les plus tardives en trop plutot que de
  // laisser une selection incoherente avec le nombre reel de posts a venir.
  champNombre.addEventListener("input", () => {
    const max = maxActuel();
    if (selection.length > max) {
      selection.sort();
      selection = selection.slice(0, max);
      majInputsCaches();
    }
    rendre();
  });

  rendre();
  majInputsCaches();
};
