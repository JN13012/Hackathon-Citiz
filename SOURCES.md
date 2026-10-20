# Sources de données

Toute donnée, tout coût et tout ratio utilisé doit figurer ici. Un chiffre sans source est écrit « hypothèse ».

## Données du sujet

| Donnée | Source | Usage |
|---|---|---|
| Arrêts de transport en France (agrégat GTFS, non dédupliqué) | https://www.data.gouv.fr/datasets/arrets-de-transport-en-france | contrôle des arrêts manquants |
| GTFS (transport public) | https://transport.data.gouv.fr/datasets?type=public-transit | arrêts, lignes, passages |
| GBFS (vélos, trottinettes, autopartage) | https://transport.data.gouv.fr/datasets?type=vehicles-sharing | stations partagées |
| Flux Citiz (par réseau régional) | https://transport.data.gouv.fr/datasets?type=vehicles-sharing&q=citiz | stations et véhicules Citiz |
| Catalogue mondial GBFS (MobilityData) | https://github.com/MobilityData/gbfs/blob/master/systems.csv | découverte de flux |
| Population par carreau de 200 m (INSEE, Filosofi) | https://www.insee.fr/fr/statistiques/8735162 | population, revenus |
| Projections de population 2018-2070 (INSEE, Omphale) | https://www.insee.fr/fr/statistiques/6652134 | croissance jusqu'en 2030 |
| Limites administratives (IGN, ADMIN EXPRESS COG) | https://geoservices.ign.fr/adminexpress | communes, EPCI et territoire |
| Contours IRIS (IGN) | https://geoservices.ign.fr/contoursiris | quartiers statistiques |
| Réseau piéton et routier (OpenStreetMap, Geofabrik) | https://download.geofabrik.de/europe/france.html | isochrones de 5 min |

Formats : https://gtfs.org/ · https://gbfs.org/

## Contours IGN utilisés — millésime 2026

Les deux livraisons couvrent la France métropolitaine, en GeoPackage et Lambert 93
(EPSG:2154). Les archives originales sont dans le dossier Windows `Downloads` ;
les données extraites sont conservées sous `data/bronze/ign/`.

- [ADMIN EXPRESS COG 4.0, édition 2026-01-01](https://data.geopf.fr/telechargement/download/ADMIN-EXPRESS-COG/ADMIN-EXPRESS-COG_4-0__GPKG_LAMB93_FXX_2026-01-01/ADMIN-EXPRESS-COG_4-0__GPKG_LAMB93_FXX_2026-01-01.7z) : couches `commune` et `epci`. Archive de 238 498 257 octets, MD5 officiel vérifié : `e00f1664dad5d11a7a41001862386877`.
- [Contours IRIS 3.0, édition 2026-01-01](https://data.geopf.fr/telechargement/download/CONTOURS-IRIS/CONTOURS-IRIS_3-0__GPKG_LAMB93_FXX_2026-01-01/CONTOURS-IRIS_3-0__GPKG_LAMB93_FXX_2026-01-01.7z) : couche `contours_iris`. Archive de 42 269 590 octets, MD5 officiel vérifié : `0462dcf1de71cca15c5de7466ad7602d`.

Les liens relatifs `data/bronze/ign/admin.gpkg` et `data/bronze/ign/iris.gpkg`
pointent vers les GeoPackage extraits, en conservant les noms d'origine.
`geo/build_contours.py` sélectionne les communes de la région 84, rattache les
IRIS par code commune et découpe les IRIS et EPCI à l'union de ces communes.
Pour les IRIS de Lyon, la couche officielle `arrondissement_municipal` d'ADMIN
EXPRESS fournit le rattachement des codes `69381` à `69389` à la commune `69123` ;
les codes d'arrondissement et d'IRIS sont conservés dans les sorties.
Le rapport `data/silver/contours_qc.json` conserve les chemins sources, couches,
effectifs et contrôles de l'exécution.

## Réseau piéton OSM — pilote Grenoble

`geo/build_reseau.py` utilise [OpenStreetMap](https://www.openstreetmap.org/)
via [Overpass](https://overpass-api.de/api) et le filtre `walk` d'
[OSMnx](https://osmnx.readthedocs.io/en/stable/user-reference.html).
Attribution : © OpenStreetMap contributors, licence
[ODbL](https://www.openstreetmap.org/copyright).
La ligne Geofabrik ci-dessus reste une source possible pour une extraction régionale ;
le pilote est téléchargé via Overpass.

Périmètre : commune IGN de Grenoble (`38185`), avec marge métrique de 1 000 m.
Les données brutes et le cache sont dans `data/bronze/osm/`, les exports pilotes
dans `data/silver/reseau_grenoble/`. Le rapport `reseau_pieton_qc.json` renseigne
l'exécution, l'API, la version d'OSMnx et l'empreinte de la zone. Le cache peut être
réutilisé : la date d'exécution ne garantit pas un snapshot OSM à cette date.
Hypothèse : marche constante à 4,5 km/h, sans pente ni attente ; cinq minutes
correspondent à 375 m sur les tronçons. Les limites du filtre et du modèle de
marche sont conservées dans le rapport et dans `geo/README.md`.

`geo/build_isochrones.py` utilise ce réseau local et les origines fournies par
l'équipe. Premier jeu : les arrêts de `data/silver/arrets.parquet` situés dans
Grenoble. Les futurs candidats par carreau restent à livrer. La méthode utilise
[Dijkstra avec seuil de temps (NetworkX)](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.shortest_paths.weighted.single_source_dijkstra_path_length.html)
et la [découpe de lignes (Shapely)](https://shapely.readthedocs.io/en/stable/manual.html#shapely.ops.substring).
Hypothèses supplémentaires : raccordement droit au chemin le plus proche,
distance maximale de 50 m et temps d'accès inclus dans les 300 secondes ;
représentation polygonale par tampon de 10 m autour des chemins atteignables.
Ce tampon cartographique ne démontre pas l'accès au bâti. Les chemins eux-mêmes
sont exportés et le rapport conserve les empreintes des entrées et les exclusions.

## GTFS de l'ancien périmètre (Occitanie)

Cette liste conserve les premières sources étudiées. Le périmètre courant est
l'Auvergne-Rhône-Alpes ; ses réseaux sont définis dans `scripts/build_arrets.py`.

| Réseau | Jeu de données |
|---|---|
| Tisséo (Toulouse) | https://transport.data.gouv.fr/datasets/tisseo-offre-de-transport-gtfs |
| liO (réseau régional) | https://transport.data.gouv.fr/datasets/reseau-lio-occitanie |
| Grand Albigeois | https://www.data.gouv.fr/datasets/offre-de-transports-du-grand-albigeois-gtfs |
| Grand Cahors | https://transport.data.gouv.fr/datasets/donnees-gtfs-du-reseau-de-transports-public-du-grand-cahors |

## Flux Citiz

À compléter avec le résultat de `scripts/compter_stations_citiz.py` (réseaux Occitanie et autres réseaux utilisés pour entraîner le modèle).

## Données complémentaires (à compléter)

| Donnée | Source | Statut |
|---|---|---|
| Ménages sans voiture par IRIS (INSEE, recensement) | https://www.insee.fr | à charger |
| Base permanente des équipements (INSEE, BPE) | https://www.insee.fr | à charger |
| Emplois au lieu de travail par commune (INSEE) | https://www.insee.fr | à charger |
| Enquête nationale sur l'autopartage (ADEME, édition la plus récente) | https://www.ademe.fr | à vérifier |
| Facteur d'émission d'une voiture par km (ADEME, Base Empreinte) | https://base-empreinte.ademe.fr | à relever |

## Coûts des équipements

Aucun montant n'est fourni par le sujet. Chaque valeur de `optim/couts.yaml` doit avoir une source ici, sinon elle est marquée « hypothèse ».
