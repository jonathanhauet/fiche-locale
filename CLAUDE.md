# Fiche Locale — plateforme de Jonathan Hauët

Application FastAPI (`app/main.py`) déployée sur Railway : push sur `main` = mise en ligne. Clients, publication de posts Google / réseaux sociaux, suivi Google Ads et PagesJaunes (GoldUnion), page de suivi AssoRH pour Franck (`/assorh/<jeton>`, module `app/suivi_assorh.py`).

## Travailler depuis plusieurs ordinateurs
Jonathan travaille sur son PC fixe, son portable et claude.ai/code. Dépôt privé github.com/jonathanhauet/fiche-locale.
- **Au début** : `git pull origin main`. **À la fin de chaque modification vérifiée** : commit sur `main` et `git push origin main`, sans branche ni pull request, sans demander. Si la session cloud est sur une branche `claude/…`, fusionner dans `main` et pousser `main`.
- Toute nouvelle règle ou décision de Jonathan est ajoutée à ce fichier : c'est la seule mémoire partagée entre les ordinateurs.
- Jamais de secret dans le dépôt (`.env`, clés d'API, mots de passe) : les valeurs de production sont dans les variables Railway.

## Règles de Jonathan
- **Toujours répondre en français**, même pour une réponse courte ou après une capture d'écran.
- Une fois une modification vérifiée, pousser sur `main` (déploiement automatique sur Railway) sans redemander. Prévenir avant seulement si c'est risqué : migration de base, envois réels à des clients.
- Jamais de texte clair sur fond clair : un encart clair dans un bloc sombre fixe sa propre couleur de texte.
- Textes publics : pas de « certifié », « agréé », « officiel » (sauf vraie source d'État), « label », « garanti », superlatifs, promesses absolues, « avis vérifiés ».
- Ne jamais présenter le filtrage d'avis (review gating) comme une bonne pratique.

## Tests
Les tests ne sont **pas** dans ce dépôt : ils sont dans `tests_plateforme/` du dépôt racine jonathanhauet/cc-fiche-locale (dossier `CC Fiche Locale`, dont ce projet est le sous-dossier `plateforme_web`).
- Sur le PC : depuis `tests_plateforme/`, `python suite_ciblee.py "message" fichier1 fichier2…` (tests concernés + fumée, puis commit et push si tout passe). `TOUT=1` pour les gros changements. Ne jamais modifier un fichier suivi pendant qu'une suite tourne.
- En session cloud, ces tests ne sont pas disponibles : se limiter aux petits changements sûrs, vérifier au moins que l'application s'importe (`python -c "import app.main"` avec une SECRET_KEY de test), et le dire à Jonathan.
