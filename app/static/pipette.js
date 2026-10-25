// Pipette sur tous les selecteurs de couleur de la plateforme : un clic, puis un clic n'importe ou sur l'ecran (meme sur un 2e ecran ou dans une autre fenetre)
// prend la couleur sous le curseur. Disponible dans Chrome, Edge et Opera (API EyeDropper) ; ailleurs, aucun bouton n'est ajoute.
(function () {
  if (!("EyeDropper" in window)) return;

  function dejaEquipe(selecteur) {
    for (let parent = selecteur.parentElement, i = 0; parent && i < 1; parent = parent.parentElement, i++) {
      if (parent.querySelector('[class*="pipette"]:not(.pipette-auto)')) return true;                      // pages qui ont deja leur propre pipette (motion design, charte graphique)
    }
    return false;
  }

  function equiper(selecteur) {
    if (selecteur.dataset.pipette || dejaEquipe(selecteur)) return;
    selecteur.dataset.pipette = "1";
    const bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "bouton-secondaire bouton-mini pipette-auto";
    bouton.textContent = "🎯 Pipette";
    bouton.title = "Prélever une couleur n'importe où sur l'écran (même sur ton deuxième écran)";
    bouton.addEventListener("click", async (evenement) => {
      evenement.preventDefault();
      try {
        const { sRGBHex } = await new window.EyeDropper().open();                   // curseur en loupe : un clic prend la couleur
        selecteur.value = sRGBHex;
        selecteur.dispatchEvent(new Event("input", { bubbles: true }));            // les pages recopient le code et enregistrent comme pour un choix a la main
        selecteur.dispatchEvent(new Event("change", { bubbles: true }));
      } catch (erreur) {
        // prelevement annule (Echap) : rien a faire
      }
    });
    let apres = selecteur;
    const champCode = selecteur.nextElementSibling;
    if (champCode && champCode.tagName === "INPUT" && champCode.type === "text") apres = champCode;   // a droite du code hexadecimal
    apres.insertAdjacentElement("afterend", bouton);
  }

  function equiperTout(racine) {
    if (racine.matches && racine.matches('input[type="color"]')) equiper(racine);
    if (racine.querySelectorAll) racine.querySelectorAll('input[type="color"]').forEach(equiper);
  }

  function demarrer() {
    equiperTout(document);
    new MutationObserver((changements) => {
      changements.forEach((c) => c.addedNodes.forEach((n) => { if (n.nodeType === 1) equiperTout(n); }));
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
