/*
 * Correction du regard du prompteur (option, bêta) : le détecteur de visage MediaPipe repère l'iris de chaque œil, et l'image enregistrée est retouchée pour
 * ramener l'iris vers un regard droit (le texte est lu un peu sous l'objectif). La retouche est une déformation locale et douce de l'ouverture de
 * l'œil : l'iris glisse, les paupières ne bougent pas. Les calculs (calculerYeux, lisser) ne dépendent ni du navigateur ni du réseau.
 */
(function (global) {
  "use strict";
  const VERSION = "0.10.14";
  const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@" + VERSION;
  const MODELE = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
  // Points du maillage de visage (478 avec les iris) pour chaque œil : coins, paupières haute et basse, centre et bord de l'iris.
  const YEUX = [
    { externe: 33, interne: 133, haut: 159, bas: 145, iris: 468, bord: [469, 470, 471, 472] },
    { externe: 263, interne: 362, haut: 386, bas: 374, iris: 473, bord: [474, 475, 476, 477] },
  ];
  const FORCES = { aucun: 0, leger: 0.5, normal: 0.8, fort: 1 };
  const DEPLACEMENT_MIN = 0.6;           // en dessous (pixels de la video), rien a corriger
  const NEUTRE = 0.45;                   // position de l'iris entre les deux coins (0 = externe, 1 = interne) quand on regarde droit : les coins du maillage englobent la caroncule

  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  // Pour chaque œil bien visible : {x, y, r, dx, dy, ax, ay} = centre et rayon de l'iris, deplacement a appliquer (pixels de la video), axe de l'œil.
  // Aucun resultat pour un œil ferme, une mesure aberrante, ou un regard franchement detourne (ce n'est pas une lecture : on n'y touche pas).
  function calculerYeux(points, largeurVideo, hauteurVideo, force, neutre) {
    if (!points || points.length < 478 || !(force > 0)) return [];
    const resultat = [];
    YEUX.forEach((oeil) => {
      const P = (i) => ({ x: points[i].x * largeurVideo, y: points[i].y * hauteurVideo });
      const ext = P(oeil.externe), int = P(oeil.interne), haut = P(oeil.haut), bas = P(oeil.bas), iris = P(oeil.iris);
      const rayon = oeil.bord.reduce((s, i) => s + distance(P(i), iris), 0) / oeil.bord.length;
      const largeur = distance(ext, int), ouverture = distance(haut, bas);
      if (largeur < 12 || !(rayon > 1.5) || rayon > largeur * 0.4 || ouverture < largeur * 0.12) return;      // visage trop petit, mesure aberrante ou paupiere fermee (clignement)
      const ax = (int.x - ext.x) / largeur, ay = (int.y - ext.y) / largeur;               // axe de l'œil, du coin externe vers le coin interne
      const t = ((iris.x - ext.x) * ax + (iris.y - ext.y) * ay) / largeur;                // 0 = coin externe, 1 = coin interne
      const cible = neutre === undefined ? NEUTRE : neutre;
      if (Math.abs(t - cible) > 0.28) return;
      const limite = 0.16 * largeur;
      const horizontal = Math.max(-limite, Math.min(limite, (cible - t) * largeur));
      const milieuY = (haut.y + bas.y) / 2 - 0.05 * ouverture;                             // l'iris est un peu au-dessus du milieu quand on regarde droit
      const marge = Math.min(0.7 * rayon, 0.35 * ouverture);                               // la paupiere recouvre deja une partie de l'iris : on ne le sort pas de l'œil
      const minY = haut.y + marge, maxY = bas.y - marge;
      const vertical = Math.max(minY, Math.min(maxY, milieuY)) - iris.y;
      let dx = horizontal * ax * force, dy = (horizontal * ay + vertical) * force;
      if (Math.hypot(dx, dy) < DEPLACEMENT_MIN) return;
      resultat.push({ x: iris.x, y: iris.y, r: rayon, dx: dx, dy: dy, ax: ax, ay: ay, ex: (ext.x + int.x) / 2, ey: (haut.y + bas.y) / 2, a: largeur / 2 * 0.95, b: Math.max(ouverture / 2 * 1.2, rayon * 0.5) });
    });
    return resultat;
  }

  // Adoucit les mouvements d'une mesure a l'autre (les points du visage tremblent legerement) ; un œil qui apparait est pris tel quel.
  function lisser(anciens, nouveaux, alpha) {
    return nouveaux.map((n, i) => {
      const a = anciens && anciens[i];
      if (!a || Math.hypot(a.x - n.x, a.y - n.y) > n.r * 2) return n;
      const m = (u, v) => u * (1 - alpha) + v * alpha;
      return Object.assign({}, n, { x: m(a.x, n.x), y: m(a.y, n.y), dx: m(a.dx, n.dx), dy: m(a.dy, n.dy), r: m(a.r, n.r) });
    });
  }

  // Deplace l'iris par une deformation locale et douce (poids centre sur la NOUVELLE position de l'iris : chaque pixel d'arrivee est pris dans l'image d'origine, un deplacement plus loin) (comme un doigt qui fait glisser l'image) : dans l'œil, avec un poids qui s'efface en s'eloignant de l'iris et en approchant des paupieres. Le blanc de l'œil s'etire d'un cote et se comprime de l'autre,
  // les paupieres restent en place. geom = { ox, oy, e } : passage des pixels de la video a ceux du canevas (x_canevas = (x - ox) * e). Renvoie le nombre d'yeux traites.
  function deformer(g, yeux, geom) {
    if (!yeux.length) return 0;
    const largeurCanevas = g.canvas.width, hauteurCanevas = g.canvas.height, e = geom.e;
    const zones = yeux.map((o) => {
      const R = o.r * e * 2.6, plein = o.r * e * 1.15;      // poids 1 sur l'iris (il glisse d'un bloc), puis il s'efface jusqu'a R
      const dx = o.dx * e, dy = o.dy * e;
      return { cx: (o.x - geom.ox) * e + dx, cy: (o.y - geom.oy) * e + dy, R: R, plein: plein, dx: dx, dy: dy, ex: (o.ex - geom.ox) * e, ey: (o.ey - geom.oy) * e, a: o.a * e, b: o.b * e, ax: o.ax, ay: o.ay };
    });
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    zones.forEach((z) => {
      z.x0 = Math.max(0, Math.floor(z.cx - z.R - 2)); z.x1 = Math.min(largeurCanevas, Math.ceil(z.cx + z.R + 2));
      z.y0 = Math.max(0, Math.floor(z.cy - z.R - 2)); z.y1 = Math.min(hauteurCanevas, Math.ceil(z.cy + z.R + 2));
      x0 = Math.min(x0, z.x0); y0 = Math.min(y0, z.y0); x1 = Math.max(x1, z.x1); y1 = Math.max(y1, z.y1);
    });
    const L = x1 - x0, H = y1 - y0;
    if (!(L > 2 && H > 2) || L * H > 400000) return 0;
    const image = g.getImageData(x0, y0, L, H), src = new Uint8ClampedArray(image.data), dst = image.data;
    let traites = 0;
    zones.forEach((z) => {
      let touche = false;
      for (let y = z.y0; y < z.y1; y++) {
        for (let x = z.x0; x < z.x1; x++) {
          const px = x + 0.5, py = y + 0.5, ddx = px - z.cx, ddy = py - z.cy, dist = Math.sqrt(ddx * ddx + ddy * ddy);
          if (dist >= z.R) continue;
          const sw = dist <= z.plein ? 0 : (dist - z.plein) / (z.R - z.plein), w = 1 - sw * sw * (3 - 2 * sw);
          const qx = px - z.ex, qy = py - z.ey, u = (qx * z.ax + qy * z.ay) / z.a, v = (-qx * z.ay + qy * z.ax) / z.b, e2 = u * u + v * v;
          const m = e2 <= 0.6 ? 1 : e2 >= 1.2 ? 0 : (1.2 - e2) / 0.6;      // dans l'ouverture de l'œil seulement
          const k = w * m;
          if (k < 0.002) continue;
          // pixel d'origine : on recule de k fois le deplacement (interpolation bilineaire dans l'image d'origine)
          const sx = px - z.dx * k - 0.5 - x0, sy = py - z.dy * k - 0.5 - y0;
          const ix = Math.max(0, Math.min(L - 2, Math.floor(sx))), iy = Math.max(0, Math.min(H - 2, Math.floor(sy)));
          const fx = Math.max(0, Math.min(1, sx - ix)), fy = Math.max(0, Math.min(1, sy - iy));
          const o00 = (iy * L + ix) * 4, o10 = o00 + 4, o01 = o00 + L * 4, o11 = o01 + 4, d = ((y - y0) * L + (x - x0)) * 4;
          for (let c = 0; c < 3; c++) dst[d + c] = (src[o00 + c] * (1 - fx) + src[o10 + c] * fx) * (1 - fy) + (src[o01 + c] * (1 - fx) + src[o11 + c] * fx) * fy;
          touche = true;
        }
      }
      if (touche) traites++;
    });
    if (traites) g.putImageData(image, x0, y0);
    return traites;
  }

  // Charge le detecteur de visage (bibliotheque et modele telecharges une fois, ~13 Mo, puis gardes par le navigateur). Leve une exception si impossible.
  async function charger() {
    const vision = await import(CDN + "/vision_bundle.mjs");
    const ensemble = await vision.FilesetResolver.forVisionTasks(CDN + "/wasm");
    const options = (delegue) => ({ baseOptions: { modelAssetPath: MODELE, delegate: delegue }, runningMode: "VIDEO", numFaces: 1 });
    try { return await vision.FaceLandmarker.createFromOptions(ensemble, options("GPU")); }
    catch (e) { return await vision.FaceLandmarker.createFromOptions(ensemble, options("CPU")); }
  }

  global.PrompteurRegard = { FORCES: FORCES, NEUTRE: NEUTRE, calculerYeux: calculerYeux, lisser: lisser, deformer: deformer, charger: charger, YEUX: YEUX };
})(window);
