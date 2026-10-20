# Exécution régionale du réseau piéton ARA — 20 octobre 2026

**Construction et contrôle indépendant terminés le 20 octobre 2026 à 15:05 UTC.**
Les **835 tuiles sur 835** sont présentes, sans tuile manquante. Le statut
d'exécution est `termine_et_controle` et le contrôle indépendant est `valide`.

| Résultat | Effectif |
|---|---:|
| Tuiles construites et contrôlées | 835 |
| Nœuds OSM distincts | 16 608 205 |
| Segments OSM distincts, parcourables dans les deux sens | 17 246 938 |
| Arcs orientés distincts | 34 493 876 |
| Copies de nœuds dans les fichiers avec marges | 24 091 332 |
| Copies d'arcs dans les fichiers avec marges | 49 922 956 |
| Taille du dossier livré | environ 2,50 Gio |

Livraison : `data/silver/reseau_ara_bidirectionnel/`, avec par tuile les
GeoParquet de nœuds, tronçons et fenêtre, ainsi que le rapport qualité.
Les effectifs distincts retirent les répétitions dues aux marges ; ils ne sont
pas obtenus par addition des fichiers.

Rapports finaux :
[construction_reseau_ara.json](../data/silver/reseau_ara_bidirectionnel/construction_reseau_ara.json)
et [controle_independant.json](../data/silver/reseau_ara_bidirectionnel/controle_independant.json).
La marche est modélisée dans les deux sens, à **4,5 km/h**, en **EPSG:2154**.
**Les calculs O1/O2 régionaux ne sont pas encore effectués.**

Construction réalisée avec les entrées
figées de `data/silver/preparation_reseau_ara/` : **835 tuiles** planifiées,
17 178 arrêts, 1 557 stations partagées, 18 735 points d'offre et 312 143 carreaux.
Les données de transport, population et les résultats du pilote sont conservés.

## Méthode et protection des ressources

Le premier essai d'extraction de 16 tuiles simultanées a été interrompu par le
système (SIGKILL), avant publication de la première tuile. Le traitement a été
ramené à une extraction par lot. Une mesure de performances a ensuite montré
le coût important de l'écriture et de la relecture GraphML sur chaque tuile.

Le constructeur utilise désormais les deux GeoParquet pour reconstruire et
valider le graphe routable ; `--graphml` conserve l'export XML en option. Les
contrôles des extrémités sont vectorisés. La validation commune évite une
reprojection lorsqu'un graphe est déjà en EPSG:2154 et utilise des tableaux
pour transférer les poids sans créer une série pandas par tronçon.

**83 tests passent**, dont la reconstruction des graphes depuis les Parquet,
les trajets et leurs coûts, le maintien des composantes distinctes, la marche
bidirectionnelle, les sorties vides sans origine et la reprise après échec.

La construction a été répartie en deux traitements Docker, sur des listes de
418 et 417 tuiles distinctes. Chacun était initialement limité à **5 Gio de mémoire**, sans
swap du conteneur, et quatre CPU. Les archives sont locales, le réseau est
désactivé et le code est monté en lecture seule. Les sorties sont isolées
par partie avant assemblage et contrôle global.

Les 15 tuiles du premier essai séquentiel ont été conservées dans un dossier
`reseau_ara_bidirectionnel_essai_*`. Elles ne sont pas mélangées à la nouvelle
version optimisée. Les empreintes du code, des sources et des sorties figurent
dans les rapports de chaque tuile.

## Suivi de l'exécution

Après 333 tuiles dans la partie 2 et 357 dans la partie 1, une extraction a
de nouveau été interrompue par SIGKILL. Les 690 tuiles déjà publiées ont été
conservées. La partie 2 a repris avec une limite de 6 Gio. La partie 1 a repris
avec 5 Gio et `geo/run_reseau_ara.py`, qui libère les objets et buffers inutilisés
avant chaque appel à osmium. Cette gestion mémoire ne change pas les règles
de construction ni les données des tuiles conservées.

Les tentatives et reprises sont tracées dans le manifeste d'exécution et les
fichiers `reprise_reseau_ara_partie1.json`, `reprise_reseau_ara_partie2.json`.

Le fichier `data/silver/journaux/execution_reseau_ara.json` indique les listes
de tuiles, dossiers de travail, noms des conteneurs, journaux et codes de sortie.
Les journaux détaillés sont dans `data/silver/journaux/reseau_ara_*_partie_*.log`.

Une tuile n'apparaît dans son dossier final qu'après nettoyage, validation et
relecture des sorties. Un échec de traitement laisse les tuiles validées
intactes. Les marges se recouvrent volontairement ; ne pas additionner les
copies de nœuds ou d'arcs pour produire un effectif régional.

## Validation globale réalisée

Après la fin des deux parties, les vérifications suivantes ont réussi :

1. Vérifier que les 835 tuiles prévues sont présentes, sans attribution en double.
2. Assembler leurs dossiers dans `data/silver/reseau_ara_bidirectionnel/` après
   vérification des signatures et empreintes.
3. Produire le bilan global du constructeur en mode `--reprendre`.
4. Effectuer un contrôle indépendant des Parquet : identifiants, géométries,
   références des nœuds, extrémités, longueurs, coûts et retours symétriques.
5. Distinguer les effectifs distincts OSM des copies présentes dans les marges.

Le contrôle indépendant a vérifié les **835 tuiles** : empreintes, unicité des
identifiants dans chaque fichier, géométries, références de nœuds, extrémités,
longueurs, temps positifs et cohérents, et retour de même coût pour chaque arc.
Les 835 tuiles contiennent un réseau. Le code du contrôle est conservé dans
`data/silver/journaux/controle_reseau_ara_execution.py` ; son empreinte figure
dans le rapport. Les tests finaux ont de nouveau confirmé **83 tests réussis**.

Ce document décrit l'exécution et sa méthode ; le statut faisant foi est le
bilan `construction_reseau_ara.json`, complété par `controle_independant.json`.
Les calculs régionaux O1/O2 ne sont pas effectués dans cette étape.
