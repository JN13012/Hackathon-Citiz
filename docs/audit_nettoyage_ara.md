# Audit du nettoyage des données ARA

Contrôle du 20 octobre 2026, sur les fichiers locaux. Les sources n'ont pas été modifiées.

Une nouvelle livraison reçue ensuite dans Downloads est contrôlée dans [audit_livraison_transport_2026-10-20.md](audit_livraison_transport_2026-10-20.md). Elle corrige les arrêts hors ARA et remplace la paire CHATEAUNEUF. Le présent document décrit les versions antérieures ; elles ont ensuite été sauvegardées et remplacées lors de la correction des noms des stations, décrite dans ce second rapport.

Résultat : les identifiants et les géométries passent les contrôles techniques, mais la livraison régionale nécessite encore des corrections et des décisions métier. Le réseau piéton exploitable reste limité à Grenoble.

## Périmètre et méthode

Huit tables GeoParquet contrôlées : identifiants absents ou dupliqués, lignes identiques hors identifiant, géométries absentes/vides/invalides, coordonnées identiques, valeurs nulles, textes vides, valeurs numériques non finies ou négatives, proportions hors de [0, 1], appartenance à l'union des communes ARA. Rapprochement des arrêts de même nom normalisé à moins de 50 m et des stations de même opérateur, type et nom à moins de 50 m.

Contrôles supplémentaires : correspondance des identifiants silver/gold et des populations, champs obligatoires des contours par niveau, références des tronçons aux nœuds, longueurs et durées positives, cohérence des durées avec 4,5 km/h. Lecture complète des deux archives OSM avec `osmium fileinfo -e -j`, puis extraction des chemins `highway=cycleway`.

Résultats détaillés : [audit_nettoyage_ara.json](../data/silver/audit_nettoyage_ara.json). Copie du script utilisé : [audit_nettoyage_ara_source.py](../data/silver/audit_nettoyage_ara_source.py), exécuté dans le conteneur pipeline avec le dépôt monté en lecture seule sur `/app` et le dossier de sortie sur `/sortie`. Les détails des rapprochements sont dans [audit_nettoyage_ara_details.txt](../data/silver/audit_nettoyage_ara_details.txt).

Les huit tables sont en EPSG:2154, sans clé absente ou dupliquée et sans géométrie absente, vide ou invalide. Cela ne garantit ni l'exhaustivité des sources ni la justesse de chaque donnée métier.

## Résultats par jeu de données

| Fichier | Effectif | Résultat et action |
|---|---:|---|
| `silver/contours.parquet` | 9 604 zones | Aucun doublon de contenu. Champs obligatoires renseignés par niveau. Les champs IRIS/EPCI non applicables aux autres niveaux restent normalement nuls. |
| `silver/carreaux_socio.parquet` | 312 143 carreaux | Aucun doublon de contenu ; population positive et finie. 483 codes IRIS et 381 valeurs d'emplois manquants. Ne pas remplacer par zéro. Rapprocher les codes communaux historiques du référentiel IGN utilisé. |
| `silver/arrets.parquet` | 17 255 arrêts | 76 points hors des limites IGN ARA. Une paire de même nom à 47,66 m à examiner. 1 170 fréquences à zéro et 198 modes `autre` à documenter avec la source. Aucune valeur nulle ni ligne identique hors identifiant. |
| `silver/stations_partagees.parquet` | 1 557 stations | 901 vélo, 620 Citiz, 36 autres stations d'autopartage. Tous les points sont dans ARA. Cinq capacités manquantes et un nom vide. Une paire de stations Citiz au même emplacement à examiner. |
| `gold/gold_offre.parquet` | 18 812 offres | Identifiants conformes aux deux tables sources. Les 76 points hors ARA et les cinq capacités manquantes sont propagés. Les noms des 1 557 stations sont remplacés par des codes commune : corriger le générateur puis régénérer. |
| `gold/gold_candidates.parquet` | 312 143 centres | Identifiants et populations conformes aux carreaux ; aucun doublon de contenu. 482 centres hors de l'union IGN, à interpréter avec les polygones frontaliers plutôt qu'à supprimer automatiquement. |
| `silver/reseau_grenoble/reseau_pieton_noeuds.parquet` | 41 060 nœuds | Deux identifiants OSM distincts ont le même point et les mêmes attributs exportés. Vérifier la topologie avant toute fusion. |
| `silver/reseau_grenoble/reseau_pieton_troncons.parquet` | 107 496 tronçons orientés | Clés `(de, vers, cle)` uniques ; toutes les extrémités existent ; longueurs et durées positives et cohérentes avec 4,5 km/h. |

## Doublons possibles et données manquantes

- Arrêts : `91a209f00c` et `db7caf2077`, « CHATEAUNEUF CENTRE », distants de 47,664 m. Examiner quais, lignes et provenance avant de fusionner ; ne pas additionner automatiquement les fréquences.
- Citiz : `citiz_auvergne_rhone_alpes:1082` (« Plan de ville », capacité 2) et `citiz_auvergne_rhone_alpes:1247` (« Plan-de-Ville », capacité 4), mêmes coordonnées à Saint-Pierre-de-Chartreuse. Les capacités diffèrent : le rapprochement seul ne prouve pas un doublon.
- Vélo : `lyon:10122`, à Villeurbanne, a un nom vide, une capacité inconnue et `en_service=false`. Conserver la trace de la station ; définir sa prise en compte dans les indicateurs.
- Capacités manquantes : trois stations Citiz et deux stations vélo. Les champs optionnels `station_virtuelle` et `type_stationnement` sont absents pour respectivement 501 et 937 stations. Ils ne suffisent pas seuls à conclure qu'une station est fixe ou virtuelle.
- Coordonnées partagées : 31 lignes supplémentaires parmi les arrêts et 16 parmi les stations. Plusieurs services peuvent partager un point : ce ne sont pas des doublons certains.
- Gold offre : neuf lignes supplémentaires ont un contenu identique hors identifiant. La perte des noms dans le générateur rend notamment des offres distinctes indiscernables ; réexaminer après correction. Ne pas dédupliquer le gold directement.
- Nœuds OSM coïncidents : `12112852544` et `12112971226`. Leur fusion éventuelle doit tenir compte des connexions, niveaux et accès de la source OSM.

Les neuf codes communaux à rapprocher sont `01039`, `01330`, `26371`, `42072`, `42109`, `42252`, `63330`, `69152`, `69159`. Les codes `69381` à `69389` désignent les arrondissements municipaux de Lyon et sont reconnus par les contours : ne pas les traiter comme des codes erronés.

La population comporte 249 165 carreaux marqués `donnee_masquee`. Préserver le signalement des estimations de la source ; il ne s'agit pas de valeurs nulles à inventer ou à supprimer. L'inventaire géométrique précédent établit que tous les polygones des carreaux intersectent ARA, y compris ceux dont le centre est extérieur. Le rapport existant `data/silver/contours_validation.json` indique 1,254 km² sans couverture IRIS : conserver cette limite dans les restitutions.

## Réseau routier régional et vélos : ce qui est disponible

Les fichiers bruts sont dans `data/bronze/osm_ara/` :

| Archive | Taille en octets | Date de l'extrait dans l'en-tête | Objets `highway=cycleway` |
|---|---:|---|---:|
| `auvergne.osm.pbf` | 156 325 688 | 18/10/2026 à 20:25:06 UTC | 1 240 |
| `rhone-alpes.osm.pbf` | 531 254 877 | 18/10/2026 à 20:25:06 UTC | 8 883 |

Les deux archives sont lisibles et contiennent notamment des routes, chemins et pistes cyclables cartographiés dans OSM. Les effectifs `cycleway` ne constituent ni un inventaire de toutes les voies accessibles à vélo ni un total régional dédupliqué : les extraits peuvent partager des objets aux frontières. La connectivité et les droits d'accès d'un futur graphe régional restent à contrôler.

**Le graphe régional n'est pas construit.** Il faudra fusionner les extraits par identifiant OSM, sélectionner les voies autorisées à pied, conserver une marge adaptée aux trajets en bordure et construire puis contrôler le réseau par lots. Le seul graphe utilisable actuellement couvre Grenoble avec une marge de 1 km. Aucun graphe cyclable régional n'est livré.

Les **stations de location vélo** sont une couche distincte du réseau des rues : 901 stations issues de 12 réseaux dans la livraison actuelle, aucune rattachée à Grenoble (`38185`). Cela ne démontre ni l'exhaustivité régionale ni l'absence de services à Grenoble. Vérifier la couverture des réseaux fixes et les stations actives avant de déclarer cette partie complète. Pour O1/O2, le trajet étudié reste une marche de cinq minutes vers les arrêts et les stations.

## Ordre de traitement proposé

1. Conserver les fichiers sources ; produire une livraison ARA avec la liste des points exclus et décider de la marge transfrontalière utile aux trajets.
2. Examiner les deux paires suspectes ; définir les règles pour fréquences nulles, modes inconnus et stations hors service, sans inventer de valeurs.
3. Corriger les noms dans `scripts/build_gold.py`, puis régénérer les sorties après validation des tables silver.
4. Compléter et documenter la couverture des stations vélo fixes et les rattachements territoriaux.
5. Construire et contrôler le réseau piéton ARA avant d'étendre O1/O2 et la carte.

Cet audit n'a ni supprimé de ligne, ni rempli de valeur manquante, ni régénéré les indicateurs. Les équipements et les résultats O1/O2 ne font pas partie des huit tables réauditées ici.
