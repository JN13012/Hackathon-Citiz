# Accès piétons de Croix-Paquet — hypothèse MVP

**Correction appliquée et pilote Lyon recalculé.** Les coordonnées GTFS et
le réseau public sont conservés ; les accès servent uniquement de destinations.

| Résultat | Avant | Avec les deux accès |
|---|---:|---:|
| Centres pouvant rejoindre Croix-Paquet en 300 s | 0 | **5** |
| Population fiscale estimée correspondante | 0 | **6 138** |
| Paires accessibles dans le pilote Lyon | 5 951 | **5 956** |
| Paires indéterminées | 7 | **7** |

Quatre des cinq centres atteignent les deux accès. Les neuf trajets vers des
accès donnent bien **cinq paires logiques**, sans double compte de population,
d'option ni de fréquence. Le meilleur temps est retenu pour chaque paire,
entre environ 78 et 249 secondes pour Croix-Paquet.

**108 tests passent.** Le contrôle du pilote vérifie 37 trajets témoins,
dont toutes les nouvelles paires de Croix-Paquet, avec un Dijkstra à
destinations terminales et le trajet inverse. Un contre-calcul indépendant
SciPy sur les quatre tuiles entières contrôle les **11 centres candidats**,
confirme les cinq centres accessibles et les temps minimaux vers les accès.
Il vérifie les incréments O1, les fréquences et la population O2 comptés une fois.

Tous les autres trajets et offres O2 gardent leurs résultats. Les groupes
O1 évoluent uniquement de l'apport attendu de Croix-Paquet ; la population,
les regroupements et les cas indéterminés sont conservés. Le pilote reste
partiel pour les autres motifs documentés, dont Lycée La Xavière.

Traces dans `data/silver/journaux/` : `tests_acces_stations.log`,
`pilote_lyon_acces_stations.log`, `controle_acces_croix_paquet.json` et
`controler_acces_croix_paquet.py`. Le pilote initial reste une référence
historique, distincte de cette version.

Décision utilisateur : **une station est accessible dès qu'au moins un de ses
accès extérieurs est atteint en cinq minutes à pied**. Les coordonnées GTFS
et l'identifiant de la station sont conservés. Le temps jusqu'au quai après
l'accès n'est pas ajouté ; il ne s'agit pas d'un temps d'embarquement.

## Points retenus

Configuration versionnée : `geo/config/acces_stations.json`.

| Station logique | Accès extérieur OSM | Tourniquet voisin | Voie OSM | Écart au point GTFS |
|---|---:|---:|---:|---:|
| `arret_f9dad1e4bc` — Croix-Paquet | `7937873345` | `7937873346` | `850918864` | 12,92 m |
| `arret_f9dad1e4bc` — Croix-Paquet | `7995153793` | `7937873362` | `857548004` | 11,27 m |

Ces points sont des nœuds de chemins avant les tourniquets, déjà présents
dans le réseau public de la tuile `x84_y652`. Leur usage comme accès a été
accepté pour le MVP. Leur précision reste celle d'OSM et de cette hypothèse.
Les autres stations gardent leur représentation actuelle.

Les coordonnées sont lues depuis les GeoParquet du réseau contrôlé, sans
recopier une latitude approximative. Le chargement vérifie les empreintes,
l'existence du nœud, l'identifiant et le nom de la station, l'absence du
tourniquet dans le réseau filtré et une limite explicite de 50 m d'écart au
point GTFS. Une configuration incompatible bloque le calcul.

## Règle de calcul

Pour un centre de population `p` et une station `s` avec plusieurs accès :

`temps(p, s) = minimum des temps vers les accès calculables de s`.

La station est accessible si ce minimum est inférieur ou égal à 300 s. Les
trajets vers chaque accès suivent le réseau piéton public à 4,5 km/h ; le
coût du raccordement de départ reste inclus. Un accès défini par un nœud
OSM est ancré à **cet identifiant**, afin de ne pas rejoindre une autre voie
superposée mais déconnectée. Aucun coût entre l'accès et le point GTFS n'est
ajouté selon la définition retenue.

Les accès sont des destinations terminales. Aucune arête entre les deux
accès n'est ajoutée au réseau public : la station ne permet pas de traverser
une rue, un quartier ou une zone de contrôle comme raccourci. Les
tourniquets restent exclus du réseau public. Aucun graphe régional n'est
reconstruit pour cette correction.

Après le routage, les paires sont regroupées par `(id_carreau, offre_id)` :

- **O1 :** une seule option métro par station, même si deux accès sont
  accessibles ; fréquence et capacité de l'offre comptées une fois.
- **O2 :** un carreau et sa population comptés une seule fois pour la
  station, dès qu'un accès est atteignable.
- Le temps le plus court et son `acces_id` sont conservés ; en cas d'égalité,
  l'identifiant du point d'accès départage de façon déterministe.
- Un accès atteint suffit à prouver l'accessibilité, même si un autre était
  indéterminé. Sans accès atteint, toute incertitude pertinente reste dans
  les paires indéterminées. Un temps connu est un minimum sur les seuls
  accès calculables.

La sélection des voisins s'appuie aussi sur les accès des stations du
pilote. Si un accès est de l'autre côté d'une frontière communale, les
habitants pouvant le rejoindre sont conservés et la station garde son
appartenance au territoire d'après le point source.

## Utilisation et sorties

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/build_pilote_lyon.py
```

Le dossier par défaut devient `data/gold/accessibilite_lyon_acces_stations/`.
Le pilote historique sans accès reste dans
`data/gold/accessibilite_lyon_validation/`. Aucun dossier existant n'est
remplacé. Pour rejouer, fournir un autre `--output-dir`.

Pour reproduire le modèle sans accès explicites, avec un dossier neuf :

```bash
docker compose run --rm pipeline python geo/build_pilote_lyon.py \
  --sans-acces-stations --output-dir data/gold/lyon_sans_acces_nouvelle_execution
```

Outre les sorties O1/O2 habituelles :

| Fichier / colonne | Usage |
|---|---|
| `acces_stations.parquet`, `.csv`, `.geojson` | Deux points, liens à la station, identifiants OSM, source et hypothèse ; coordonnées GTFS dans `station_source_x/y` |
| `raccordements_acces_stations.parquet` | Diagnostic individuel des accès et ancrage réel au graphe |
| `accessibilite_carreaux_offres.acces_id` | Accès retenu pour le temps de la paire logique ; vide pour une arrivée au point source |
| `gold_o2.nb_acces_pietons`, `mode_arrivee` | Nombre d'accès et règle d'arrivée ; géométrie d'affichage GTFS conservée |
| `raccordements_offre.via_acces_station`, `nb_acces`, `nb_acces_calculables` | Diagnostic logique, distinct des raccordements individuels |

Le raccordement unique du point GTFS reste vide dans le diagnostic logique
d'une station avec accès : les véritables projections sont dans la table
des accès. Les indicateurs gardent une seule ligne O2 pour Croix-Paquet.

Les tests couvrent le choix du minimum, le double comptage, la conservation
des coordonnées et fréquences, les rues déconnectées, les géométries
superposées, les cas indéterminés et les accès hors limite communale. Les
trajets de référence du pilote testent l'ensemble des accès comme des
destinations terminales et vérifient toutes les nouvelles paires de station.
