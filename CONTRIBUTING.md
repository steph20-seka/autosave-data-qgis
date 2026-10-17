# Contribuer

Les retours terrain sont précieux : un cas où l'extension a enregistré au mauvais moment, ou ne l'a pas fait, vaut un correctif.

## Signaler un problème
Ouvrez une *issue* avec : version de QGIS et du système, version de l'extension, ce que vous faisiez (édition, mise en page, traitement), et les lignes du journal (Vue > Panneaux > Messages de journalisation > onglet AutoSave Data).

## Proposer une modification
1. Forkez le dépôt et créez une branche.
2. Installez les outils : `pip install -r requirements-dev.txt`.
3. Lancez `pytest` et `python -m pyflakes autosave_data tests` avant chaque *pull request*.
4. Toute règle de décision (quand enregistrer, attendre ou ignorer) vit dans `autosave_data/core.py` sous forme de fonctions pures testables. Ajoutez un test pour chaque comportement nouveau.

## Principes de conception
- Ne jamais poser de question à l'utilisateur au moment d'enregistrer.
- Ne jamais écrire dans une couche en cours d'édition.
- Rester compatible Qt5 et Qt6 : imports uniquement via `qgis.PyQt`, énumérations qualifiées.
- Une erreur dans l'extension ne doit jamais perturber QGIS.
