/*
 * Calendrier personnalise remplacant les <input type="date"> natifs (non
 * stylables et sans possibilite d'y marquer des jours occupes). Chaque champ
 * ".champ-date-personnalise" contient un input texte affiche (lecture seule)
 * et un input cache portant la vraie valeur "AAAA-MM-JJ" soumise au formulaire.
 */
(function () {
  const LIBELLES_JOURS = ["Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di"];
  const LIBELLES_MOIS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
  ];

  function pad(n) { return String(n).padStart(2, "0"); }
  function versISO(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function versAffichage(d) { return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`; }
  function depuisISO(iso) {
    const [a, m, j] = iso.split("-").map(Number);
    return new Date(a, m - 1, j);
  }

  function initChamp(wrapper) {
    const champAffichage = wrapper.querySelector(".champ-date-affichage");
    const champValeur = wrapper.querySelector(".champ-date-valeur");
    if (!champAffichage || !champValeur) return;

    const joursOccupes = new Set(JSON.parse(wrapper.dataset.joursOccupes || "[]"));

    if (champValeur.value) {
      champAffichage.value = versAffichage(depuisISO(champValeur.value));
    }

    let panneau = null;
    let moisAffiche = new Date();

    function surClicExterieur(evenement) {
      if (wrapper.contains(evenement.target)) return;
      fermer();
    }

    function fermer() {
      if (!panneau) return;
      panneau.remove();
      panneau = null;
      document.removeEventListener("click", surClicExterieur, true);
    }

    function choisir(d) {
      champValeur.value = versISO(d);
      champAffichage.value = versAffichage(d);
      champAffichage.classList.remove("champ-invalide");
      champValeur.dispatchEvent(new Event("change", { bubbles: true }));
      fermer();
    }

    function effacer() {
      champValeur.value = "";
      champAffichage.value = "";
      champValeur.dispatchEvent(new Event("change", { bubbles: true }));
      fermer();
    }

    function rendre() {
      const annee = moisAffiche.getFullYear();
      const mois = moisAffiche.getMonth();
      const premierJourMois = new Date(annee, mois, 1);
      const decalage = (premierJourMois.getDay() + 6) % 7; // lundi = 0
      const debutGrille = new Date(annee, mois, 1 - decalage);

      const aujourdhuiISO = versISO(new Date());
      const valeurActuelle = champValeur.value;

      let joursHtml = "";
      for (let i = 0; i < 42; i++) {
        const jour = new Date(debutGrille);
        jour.setDate(debutGrille.getDate() + i);
        const iso = versISO(jour);
        const classes = ["popup-calendrier-jour"];
        if (jour.getMonth() !== mois) classes.push("popup-calendrier-jour-hors-mois");
        if (iso === aujourdhuiISO) classes.push("popup-calendrier-jour-aujourdhui");
        if (iso === valeurActuelle) classes.push("popup-calendrier-jour-selectionne");
        if (joursOccupes.has(iso)) classes.push("popup-calendrier-jour-occupe");
        joursHtml += `<button type="button" class="${classes.join(" ")}" data-iso="${iso}">${jour.getDate()}</button>`;
      }

      panneau.innerHTML = `
        <div class="popup-calendrier-entete">
          <button type="button" class="popup-calendrier-nav" data-sens="-1">‹</button>
          <span>${LIBELLES_MOIS[mois]} ${annee}</span>
          <button type="button" class="popup-calendrier-nav" data-sens="1">›</button>
        </div>
        <div class="popup-calendrier-jours-semaine">${LIBELLES_JOURS.map((j) => `<span>${j}</span>`).join("")}</div>
        <div class="popup-calendrier-grille">${joursHtml}</div>
        <div class="popup-calendrier-pied">
          <span class="popup-calendrier-legende"><span class="pastille-occupe"></span> déjà programmé</span>
          ${valeurActuelle ? '<button type="button" class="popup-calendrier-effacer">Effacer</button>' : ""}
        </div>
      `;

      panneau.querySelectorAll(".popup-calendrier-nav").forEach((bouton) => {
        bouton.addEventListener("click", () => {
          moisAffiche = new Date(annee, mois + Number(bouton.dataset.sens), 1);
          rendre();
        });
      });
      panneau.querySelectorAll(".popup-calendrier-jour").forEach((bouton) => {
        bouton.addEventListener("click", () => choisir(depuisISO(bouton.dataset.iso)));
      });
      const boutonEffacer = panneau.querySelector(".popup-calendrier-effacer");
      if (boutonEffacer) boutonEffacer.addEventListener("click", effacer);
    }

    function ouvrir() {
      if (panneau) return;
      panneau = document.createElement("div");
      panneau.className = "popup-calendrier-champ";
      wrapper.appendChild(panneau);
      moisAffiche = champValeur.value ? depuisISO(champValeur.value) : new Date();
      rendre();
      setTimeout(() => document.addEventListener("click", surClicExterieur, true), 0);
    }

    champAffichage.addEventListener("click", ouvrir);
  }

  function initTout() {
    document.querySelectorAll(".champ-date-personnalise").forEach(initChamp);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTout);
  } else {
    initTout();
  }

  // Le champ soumis au formulaire est "hidden" : l'attribut required natif ne
  // s'y applique pas de facon fiable, d'ou cette validation manuelle avant envoi.
  // Respecte formnovalidate (ex : bouton "Rejeter" qui n'a pas besoin de date).
  document.addEventListener("submit", (evenement) => {
    if (evenement.submitter && evenement.submitter.hasAttribute("formnovalidate")) return;
    const champsRequis = evenement.target.querySelectorAll(
      ".champ-date-personnalise[data-requis] .champ-date-valeur"
    );
    for (const champ of champsRequis) {
      if (!champ.value) {
        evenement.preventDefault();
        const affichage = champ.closest(".champ-date-personnalise").querySelector(".champ-date-affichage");
        affichage.classList.add("champ-invalide");
        affichage.focus();
        return;
      }
    }
  });
})();
