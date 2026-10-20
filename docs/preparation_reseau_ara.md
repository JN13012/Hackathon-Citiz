# Préparation du réseau piéton ARA

Préparation réalisée le 20 octobre 2026 avec `geo/prepare_reseau_ara.py`.
**Le graphe régional n'est pas encore construit.**

## Problème constaté dans la collecte GTFS

Le dernier lancement du pipeline a reçu une réponse HTTP 401 lors du téléchargement
de TCL Lyon. Le collecteur a ignoré ce réseau et a continué. L'orchestration n'a
pas fourni `--contours`, puis le collecteur et le générateur gold ont remplacé
les fichiers actifs par une livraison partielle : 11 495 arrêts, 13 052 offres,
70 points hors ARA et aucun point de type métro. Le code de sortie était pourtant 0.

Cela ne signifie pas que Lyon n'a plus de métro : la source n'a pas été chargée.
Le collecteur a ensuite été protégé : erreurs GTFS bloquantes, contours
obligatoires, contrôles avant publication et restauration si un remplacement
échoue. L'orchestration fournit les contours et vérifie le gold. La livraison
validée a été restaurée sans nouveau téléchargement ; voir
[nettoyage_et_protection_donnees.md](nettoyage_et_protection_donnees.md).

## Entrées régionales disponibles

Le dossier [data/silver/preparation_reseau_ara](../data/silver/preparation_reseau_ara/)
contient une copie autonome des entrées retenues. Les arrêts proviennent de la
livraison validée archivée dans
`data/bronze/livraisons/transport_20261020T115246/arrets 2.parquet`, et non de
la collecte partielle devenue active. Les stations et la population proviennent
des tables silver sélectionnées lors de la préparation. Les noms des stations
du gold sont produits par le générateur corrigé.

| Entrée | Effectif |
|---|---:|
| Arrêts de transport en commun | 17 178 |
| Stations vélo fixes | 901 |
| Stations Citiz | 620 |
| Stations Clem | 36 |
| Total des points d'offre | 18 735 |
| Points métro inclus dans les arrêts | 40 |
| Carreaux habités | 312 143 |
| Centres frontaliers conservés | 482 |
| Capacités inconnues conservées | 5 |

Les codes des 19 réseaux GTFS attendus sont présents. Cela valide la présence
des réseaux déclarés, pas l'exhaustivité de tous les services régionaux.
Les fichiers actifs `data/silver/arrets.parquet` et `data/gold/gold_offre.parquet`
ont depuis été restaurés et vérifiés conformes à ces entrées, hors colonne
technique `tuile_id`. Le futur traitement régional doit continuer à utiliser
les entrées figées pour assurer la reproductibilité.

## Découpage préparé

- Grille Lambert 93 de **10 km × 10 km**, couvrant la région et sa bordure.
- **835 tuiles** au total, dont **791** avec un centre de population ou un point d'offre.
- Fenêtre d'extraction avec **1 km de marge autour de chaque tuile**.
- Un seul propriétaire pour chaque point ; les zones avec marge se chevauchent
  volontairement sans créer d'origines à compter plusieurs fois.
- Hypothèse conservée : 300 secondes à pied à 4,5 km/h, raccordements compris.

Fichiers : `tuiles.parquet`, `zones_extraction.parquet`,
`zones_extraction.geojson`, les sept tables dans `entrees/`, et le
[plan de préparation](../data/silver/preparation_reseau_ara/plan_reseau_ara.json)
avec les empreintes des sources et des sorties.

## Contrôles réalisés

Sept tests couvrent notamment le refus d'une livraison sans réseau attendu,
les points hors région, une marge insuffisante, la conservation des carreaux
frontaliers et des capacités inconnues, les archives modifiées et le refus
d'écraser une préparation existante.

Un contrôle indépendant sur les données complètes a vérifié les empreintes,
la présence des 19 réseaux, la couverture du contour régional avec sa bordure,
l'affectation unique des 312 143 centres et des 18 735 offres, la conservation
des effectifs et la marge de chaque zone.

## Prochain travail : construire les graphes

1. Assembler les archives OSM Auvergne et Rhône-Alpes en dédoublonnant les objets
   communs et en signalant d'éventuelles versions contradictoires.
2. Extraire les fenêtres préparées, avec les nœuds nécessaires aux voies retenues.
3. Construire un graphe piéton par tuile : voies marchables, accès, connexions,
   longueurs et coûts en secondes. Le plan ne définit pas encore ce filtre ;
   les restrictions piétonnes et les voies cyclables autorisées à pied doivent
   être traitées explicitement.
4. Vérifier les identifiants, géométries, références des tronçons, composantes,
   poids et trajets de contrôle, y compris à cheval sur deux tuiles.
5. Adapter l'orchestration des calculs locaux pour utiliser ces graphes et les
   entrées figées ; valider Lyon, puis calculer O1/O2 sur la région.

Le constructeur PBF → graphe est désormais disponible dans
`geo/build_reseau_ara.py`, avec nettoyage intégré, validation et reprise par
tuile ; voir [construction_reseau_ara.md](construction_reseau_ara.md).
Le plan de préparation reste un instantané d'entrée : son champ
`graphe_construit: false` n'est pas modifié par les constructions suivantes.
Leur état est consigné dans `construction_reseau_ara.json` dans le dossier des
graphes. Le modèle retenu est désormais bidirectionnel : toute voie conservée
est parcourable dans les deux sens. L'orchestration régionale O1/O2 reste à écrire.
Les fenêtres ne sont pas des isochrones. La formule O1 reste à harmoniser avec
le plan V5 avant publication ; la construction du réseau peut avancer indépendamment.
Les isochrones des candidats et `gold_couverture` seront ensuite nécessaires au modèle
et à l'optimiseur. L'application de restitution appartient au collègue.
