# Historique de développement

Journal détaillé des itérations de travail (essais, corrections intermédiaires) conservé pour mémoire. Ces itérations ne sont pas des versions diffusées : le journal public, plus court, est [CHANGELOG.md](../../CHANGELOG.md).

## [0.3.0] - 2026-10-17

### Ajouté
- Reprise après un arrêt brutal (plantage, coupure de courant, fermeture forcée). Un petit journal de session (dossier du profil QGIS, sous `AutoSaveData/sessions`) est tenu à jour toutes les 30 s et supprimé à la fermeture normale. Au démarrage suivant, si une session précédente s'est terminée sans l'être, une fenêtre propose de rouvrir le projet tel quel ou l'une de ses copies de secours.
- Ancienneté lisible de chaque version dans la fenêtre de restauration (« il y a 3 min », « il y a 2 h », « il y a 1 j »).
- Réglage « Proposer la reprise après un arrêt brutal » (activé par défaut).
- Fenêtres de réglages et de restauration aux couleurs améthyste (en-tête dégradé, boutons, groupes), avec une feuille de style qui suit le thème clair ou sombre de QGIS.

### Limites
- La reprise ne peut redonner que ce qui est sur le disque : les modifications faites depuis le dernier enregistrement, et les objets d'une couche en édition non validés, sont perdus.
- Les versions proposées sont celles des copies de secours (une toutes les 10 min par défaut, 10 conservées). Une granularité plus fine (3 minutes) demande un instantané indépendant du projet ouvert, non encore validé dans un QGIS réel.

## [0.2.2] - 2026-10-17

### Ajouté
- Un clic sur l'icône de la barre d'outils ou sur l'indicateur de la barre d'état ouvre les réglages. L'activation se fait dans le menu ou dans la fenêtre de réglages.
- Fenêtre de réglages refondue : en-tête avec version, bloc « Projet en cours » (état, dernier enregistrement, nombre de copies) avec les boutons Enregistrer maintenant, Restaurer une copie et Ouvrir le dossier des copies, options regroupées en deux colonnes, options grisées quand elles n'ont plus d'effet, bouton « Valeurs par défaut ».
- Tests de la fenêtre avec le vrai Qt (PyQt5 et PyQt6 si installés).

### Corrigé
- L'indicateur de la barre d'état restait sur « désactivé » après une réactivation (constaté dans QGIS 3.44).
- Libellés des réglages raccourcis : plus de texte tronqué.

## [0.2.1] - 2026-10-17

### Ajouté
- Protection contre l'écrasement : si le fichier projet a été modifié en dehors de la session (autre utilisateur, autre fenêtre de QGIS), l'enregistrement automatique n'écrit rien, affiche un avertissement unique et un indicateur « fichier modifié ailleurs ». L'enregistrement manuel écrase après avoir copié la version étrangère dans les copies de secours, même si les copies sont désactivées.

### Corrigé
- Après un enregistrement manuel pendant une pause (édition ou traitement), les modifications suivantes n'étaient plus planifiées avant le contrôle de sécurité et l'indicateur restait sur « en pause ».

### Modifié
- `QAction` est importé depuis `QtGui` (Qt 6) avec repli sur `QtWidgets` (Qt 5) : le chargement ne dépend plus de ce que la couche de compatibilité de QGIS réexporte.
- `supportsQt6` retiré de `metadata.txt` : QGIS 4 ne le reconnaît plus, seul `qgisMaximumVersion` compte.
- README : mise en garde sur le bon fichier à installer (rubrique Assets de la release, pas « Download ZIP »).
- README : le délai maximal ne « garantit » plus l'enregistrement, il le force hors pause ; la compatibilité QGIS 4 est présentée comme déclarée et non encore essayée.

## [0.2.0] - 2026-10-17

### Ajouté
- Contrôle du fichier projet après chaque écriture (archive .qgz intacte, .qgs complet). En cas de fichier illisible, la version précédente saine est remise en place et un nouvel essai est programmé.
- Fenêtre de restauration : liste des copies de secours (date, taille), duplication à côté du projet puis ouverture, sans rien écraser.
- Indicateur d'état dans la barre d'état (actif, en attente, en pause, enregistré, échec).
- Pause adaptative : au moins 10 fois la durée du dernier enregistrement, avec un plafond de 600 s.
- Réglage « Allonger la pause sur les gros projets ».
- Liens `homepage`, `repository` et `tracker` dans `metadata.txt`.
- README en anglais, bannière et aperçus des fenêtres.

### Modifié
- 77 tests (contre 46) ; fenêtres vérifiées sous Qt 5 et Qt 6.

## [0.1.0] - 2026-10-16

### Ajouté
- Enregistrement automatique du projet après une pause (20 s par défaut) suivant une modification, sans fenêtre de confirmation.
- Délai maximal en travail continu (120 s) pour qu'un travail ininterrompu soit tout de même enregistré.
- Pause automatique tant qu'une couche vectorielle est en mode édition (vectorisation, saisie). Les modifications de couche ne sont jamais validées par l'extension.
- Attente pendant les traitements en arrière-plan et pendant qu'une fenêtre modale est ouverte.
- Copies de secours horodatées dans `<projet>_sauvegardes`, avec rotation (10 conservées, 1 toutes les 10 min au plus). La première copie de la session est toujours faite.
- Contrôle de sécurité périodique (5 min) pour rattraper une modification manquée.
- Bouton de barre d'outils (activer/désactiver), menu Extensions, boîte de réglages.
- Compatibilité QGIS 3.22 à 4.x (Qt5 et Qt6).
- Suite de tests automatisée avec QGIS simulé.
