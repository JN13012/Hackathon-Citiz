# geo — Réseau piéton, isochrones, O1 et O2 (Rôle 2)

- Construire le graphe piéton OSM et les `contours` (communes, EPCI, IRIS).
- Calculer les isochrones de 5 min à pied (vitesse 4,5 à 5 km/h, à afficher comme hypothèse).
- Produire `gold_isochrones`, `gold_o1`, `gold_o2`, `gold_couverture`.
- Livrer le contour du territoire en premier (lundi 15 h) : tout le monde en a besoin.

## Préparer les contours

`build_contours.py` lit des fichiers locaux IGN et écrit des GeoParquet en
Lambert 93 (EPSG:2154). Il accepte Shapefile, GeoPackage, GeoJSON et GeoParquet.
Pour les éditions actuelles d'ADMIN EXPRESS, privilégier GeoPackage (`.gpkg`) :
la version 4 n'est plus diffusée en Shapefile. Les anciens Shapefiles restent
acceptés par le script. Les exemples utilisent des noms de fichiers simplifiés ;
les noms exacts des couches doivent correspondre au contenu du fichier téléchargé.

Télécharger les communes et, si nécessaire, les EPCI depuis
[ADMIN EXPRESS (IGN)](https://geoservices.ign.fr/adminexpress), et les IRIS depuis
[Contours IRIS (IGN)](https://geoservices.ign.fr/contoursiris). Décompresser les
archives dans `data/bronze/ign/`. Pour un Shapefile, conserver ensemble les fichiers
`.shp`, `.shx`, `.dbf`, `.prj` et les autres fichiers livrés. Les chemins ci-dessous
sont des exemples à adapter aux noms des fichiers téléchargés.

Premier essai sur Grenoble, avec les communes seules :

```bash
docker compose build
docker compose run --rm pipeline python geo/build_contours.py \
  --communes data/bronze/ign/admin.gpkg \
  --communes-layer commune \
  --codes-communes 38185
```

Région Auvergne-Rhône-Alpes entière (code 84), avec les IRIS :

```bash
docker compose run --rm pipeline python geo/build_contours.py \
  --communes data/bronze/ign/admin.gpkg \
  --communes-layer commune \
  --iris data/bronze/ign/iris.gpkg \
  --region 84
```

Ajouter `--epci chemin/EPCI.gpkg` pour inclure les EPCI. Avec un GeoPackage contenant
plusieurs couches, préciser leurs noms avec `--communes-layer COMMUNE`,
`--iris-layer IRIS` ou `--epci-layer EPCI`. Les noms de couches disponibles sont
affichés si ce choix manque. Les colonnes IGN habituelles sont reconnues sans
tenir compte de leur casse ; si une colonne manque, le message d'erreur liste les
colonnes disponibles.

La sélection régionale nécessite une colonne telle que `INSEE_REG`,
`code_insee_de_la_region` (ADMIN EXPRESS v4) ou `CODE_REGION` dans les communes.
À défaut, fournir explicitement les codes avec
`--codes-communes 38185 38421`. Sans sélection explicite, la région 84 est utilisée.
Le script ne déduit pas le territoire des stations Citiz existantes.

Les trois sorties, dans `data/silver/` par défaut :

| Fichier | Contenu |
|---|---|
| `contours.parquet` | Une ligne par commune, IRIS ou portion d'EPCI sélectionné |
| `territoire.parquet` | Une ligne : union des communes retenues, sans marge ni simplification |
| `contours_qc.json` | Sources locales, sélection, projections, effectifs, découpages et niveaux absents |

`contours.parquet` contient `nom`, `niveau`, `geometry` et les identifiants suivants :

- `id_zone` : identifiant unique préfixé, par exemple `commune:38185` ;
- `code_insee` : code de la commune, pour les communes et leurs IRIS ; vide pour les EPCI ;
- `code_arrondissement_municipal` : code de l'arrondissement pour les IRIS de Lyon, Paris ou Marseille, quand la correspondance officielle est disponible ;
- `code_iris` : code complet de l'IRIS, uniquement pour ce niveau ;
- `code_epci` : SIREN de l'EPCI, uniquement pour ce niveau.

Les IRIS sont sélectionnés par leur commune de rattachement. Les IRIS et EPCI
sont ensuite découpés à la limite du territoire ; leur géométrie de sortie peut
donc représenter seulement une partie de la zone administrative d'origine. Le
rapport compte ces découpages. Un EPCI qui touche seulement la frontière sans
surface commune n'est pas conservé.

Les IRIS de Lyon sont codés par arrondissement municipal (`69381` à `69389`),
alors que la commune est `69123`. Le script utilise automatiquement la couche
`arrondissement_municipal` si elle est présente dans le GeoPackage ADMIN EXPRESS.
Il conserve le code de l'arrondissement et le code IRIS d'origine, et renseigne
`code_insee` avec la commune de rattachement officielle. Pour des fichiers
séparés, fournir cette correspondance avec `--arrondissements chemin/fichier`
et, si nécessaire, `--arrondissements-layer nom_de_couche`.

Commande pour les fichiers IGN 2026 extraits dans ce dépôt, avec les trois niveaux :

```bash
docker compose run --rm pipeline python geo/build_contours.py \
  --communes data/bronze/ign/admin.gpkg --communes-layer commune \
  --iris data/bronze/ign/iris.gpkg --iris-layer contours_iris \
  --epci data/bronze/ign/admin.gpkg --epci-layer epci \
  --region 84 --millesime 2026
```

Les IRIS et EPCI sont facultatifs : un premier résultat avec les communes seules
est possible, et le rapport indique les niveaux non fournis. Si des IRIS sont
fournis, les communes sans IRIS correspondant sont listées. Une absence n'est
jamais remplacée par une géométrie inventée. Le script refuse les identifiants
dupliqués, les géométries invalides et les projections inconnues. Il ne répare pas
automatiquement les données. Les codes sont conservés comme texte, sans ajouter
de zéros à des codes incomplets.

Noter les éditions utilisées dans `SOURCES.md` ; `--millesime 2026` permet aussi
d'inscrire un millésime déclaré dans le rapport. Vérifier la cohérence des
éditions communes/IRIS/EPCI avant de les croiser. `--output-dir chemin` permet de
conserver plusieurs essais : sinon, une exécution réussie remplace les trois sorties.

Le script se lance séparément de `scripts/run_all.py`. Le responsable de
l'ingestion pourra utiliser `territoire.parquet` avec l'option `--contours` de
`scripts/build_arrets.py`.

L'exécution sur les sources IGN 2026 produit 4 025 communes, 5 407 IRIS et 172 EPCI
pour l'ARA, dont les 185 IRIS des neuf arrondissements de Lyon. Un contrôle
indépendant des exports est conservé dans `data/silver/contours_validation.json` :
les identifiants et rattachements correspondent aux sources et l'union des
communes correspond à la région IGN. Les IRIS couvrent 99,9982 % de cette surface ;
un écart géométrique d'environ 1,25 km² subsiste entre leur couverture et le
territoire. Cet écart est mesuré et conservé, sans créer de polygones de remplacement.

Les tests utilisent uniquement des données synthétiques et un répertoire temporaire :

```bash
docker compose run --rm pipeline python -m unittest discover -s tests -p test_build_contours.py -v
```

## Construire le réseau piéton OSM

`build_reseau.py` sélectionne des communes dans `contours.parquet`, ajoute une
marge de **1 km**, télécharge les voies avec le filtre `walk` d'OSMnx via Overpass,
puis produit un graphe et deux tables GeoParquet en EPSG:2154. Il conserve toutes
les composantes, les tronçons parallèles et les nœuds portant des informations
de passage (barrières, traversées, accès). Les tronçons sont orientés dans les deux
sens pour la marche. La clé complète d'un tronçon est `(de, vers, cle)`.

Premier périmètre : **Grenoble, code INSEE 38185, et sa marge de 1 km**.
Le réseau pilote ne couvre pas l'ARA entière.

L'exécution du 19 octobre 2026 produit **41 060 nœuds**, **107 496 tronçons
orientés** et **479 composantes**. La composante principale contient 96,36 %
des nœuds ; les autres composantes restent présentes. Il n'y a aucun nœud isolé.

```bash
docker compose build
docker compose run --rm pipeline python geo/build_reseau.py \
  --codes-communes 38185 \
  --output-dir data/silver/reseau_grenoble
```

| Fichier dans le dossier de sortie | Contenu |
|---|---|
| `reseau_pieton_noeuds.parquet` | `noeud_id` OSM, `composante_id`, `dans_territoire`, `dans_zone_marge`, points |
| `reseau_pieton_troncons.parquet` | `de`, `vers`, `cle`, `longueur_m`, `temps_pieton_s`, tags OSM en JSON, lignes |
| `reseau_pieton.graphml` | Graphe OSMnx projeté, attributs OSM et poids de marche |
| `zone_requete.parquet` | Polygone IGN sélectionné avec la marge |
| `reseau_pieton_qc.json` | Provenance, couverture, effectifs, composantes, hypothèses et trajets de contrôle |

La longueur est mesurée sur la ligne en Lambert 93. Le temps est calculé avec
une hypothèse de **4,5 km/h**, modifiable avec `--vitesse-kmh`. À cette vitesse,
cinq minutes représentent **375 m parcourus sur le réseau**. La longueur initiale
calculée par OSMnx reste disponible dans l'attribut `length` du GraphML.
Les tags `osmid_json`, `name_json`, `highway_json`, `foot_json`, `surface_json`
et `access_json` des tronçons sont des textes JSON : ils peuvent représenter un
scalaire, une liste après simplification, ou `null` quand le tag manque.
Les autres tags disponibles et ceux des nœuds restent dans le GraphML.

Pour réutiliser le graphe en Python, typer explicitement les poids ajoutés au
format GraphML (OSMnx connaît déjà le type des attributs standard comme `length`) :

```python
import networkx as nx
import osmnx as ox

graphe = ox.load_graphml(
    "data/silver/reseau_grenoble/reseau_pieton.graphml",
    edge_dtypes={"longueur_m": float, "temps_pieton_s": float},
    node_dtypes={"composante_id": int},
)
# Temps minimal entre deux identifiants OSM présents dans le graphe :
# nx.shortest_path_length(graphe, depart, arrivee, weight="temps_pieton_s")
# Nœuds atteignables en 5 min depuis un nœud (ceci ne crée pas un polygone) :
# nx.single_source_dijkstra_path_length(graphe, depart, cutoff=300, weight="temps_pieton_s")
```

Les réponses Overpass et le GraphML d'origine sont archivés sous
`data/bronze/osm/<empreinte_de_la_zone>/`. Le cache évite les téléchargements
répétés. La date d'exécution n'est pas présentée comme la date du dernier relevé
OSM ; le rapport laisse la date de snapshot indéterminée. Pour refaire les exports
sans connexion, reprendre le chemin de l'archive indiqué dans le rapport :

```bash
docker compose run --rm pipeline python geo/build_reseau.py \
  --codes-communes 38185 \
  --graphml data/bronze/osm/<empreinte_de_la_zone>/reseau_osm.graphml \
  --output-dir data/silver/reseau_grenoble
```

Le contour et la marge doivent être identiques à ceux de l'archive. On peut
sélectionner plusieurs communes avec `--codes-communes 38185 38421`, ou fournir
un territoire polygonal avec `--territoire chemin/territoire.parquet`. Préciser
un dossier de sortie adapté au périmètre. Le script refuse par défaut une zone
avec marge supérieure à 1 000 km² : pour l'ARA, prévoir des lots et leur assemblage
avant le calcul régional des isochrones. L'assemblage régional reste à réaliser.

Les contrôles refusent les géométries invalides ou de longueur nulle et les
coordonnées absentes. Le rapport distingue les composantes et les nœuds isolés ;
aucun raccordement artificiel n'est créé. Quelques nœuds voisins hors de la marge
peuvent être conservés pour garder les tronçons traversant sa frontière ; leur
nombre est indiqué dans le rapport. Les trajets de contrôle relient des nœuds
extrêmes de la composante principale : ils vérifient le calcul, pas l'état du terrain.

Limites : le filtre OSMnx dépend des tags OSM et exclut notamment les voies
`cycleway`, même si certaines permettent la marche. Les pentes, temps d'attente,
horaires et restrictions piétonnes directionnelles ne sont pas modélisés.
Les barrières restent repérables dans les tags, sans calcul d'interdiction de
passage. Ce script prépare le réseau ; `build_isochrones.py`, décrit ci-dessous,
calcule ensuite les chemins atteignables et leur représentation polygonale.

Tests hors connexion sur graphes synthétiques :

```bash
docker compose run --rm --no-deps pipeline python -W error -m unittest discover \
  -s tests -p test_build_reseau.py -v
```

## Calculer les zones de marche de cinq minutes

`build_isochrones.py` travaille **hors connexion** sur les fichiers du réseau.
Le pilote utilise les arrêts déjà présents dans Grenoble : les candidats et les
carreaux de population ne sont pas encore livrés dans le dépôt. Les arrêts ne
remplacent pas ces futurs candidats dans la livraison régionale.

```bash
docker compose build
docker compose run --rm pipeline python geo/build_isochrones.py \
  --origines data/silver/arrets.parquet --id-col arret_id --type-origine arret \
  --codes-communes 38185 \
  --output-dir data/gold/isochrones_grenoble_arrets
```

Le script sélectionne les origines dans la commune IGN, puis projette chaque
point sur le tronçon le plus proche. Le raccordement droit au chemin est une
**hypothèse**, limitée par défaut à **50 m** ; son temps est déduit des 300 secondes.
Le coût du chemin depuis la projection jusqu'aux deux nœuds est aussi pris en
compte. Dijkstra calcule ensuite les temps minimaux et les tronçons sont coupés
au budget restant. Une voie longue peut donc être partiellement accessible,
même si aucun de ses nœuds n'est atteignable. Les voies voisines déconnectées ne
sont pas raccordées artificiellement au réseau.

La vitesse est lue dans le rapport du réseau : **4,5 km/h pour ce pilote**.
Le script refuse des poids incohérents avec la géométrie ou cette vitesse.
Les options `--duree-s`, `--max-raccord-m` et `--tampon-m` permettent de déclarer
d'autres hypothèses. Une origine trop éloignée, ou dont le temps d'accès épuise
le budget, conserve un statut explicite dans les contrôles ; aucune zone vide
n'est présentée comme une accessibilité calculée.

| Fichier dans le dossier de sortie | Contenu |
|---|---|
| `gold_isochrones.parquet` | Une représentation polygonale par origine calculée, EPSG:2154 |
| `chemins_accessibles.parquet` | Lignes réellement atteignables dans le modèle, raccordement hypothétique inclus |
| `origines_qc.parquet` | Toutes les origines sélectionnées, statuts, point de raccordement et coût d'accès |
| `gold_isochrones.geojson` | Polygones en longitude/latitude (EPSG:4326), pour un outil cartographique |
| `apercu_isochrones.html` | Carte locale interactive : sélectionner un arrêt pour voir ses chemins et sa zone ; ouverture directe dans un navigateur, sans serveur |
| `isochrones_qc.json` | Paramètres, effectifs, exclusions, limites et empreintes SHA-256 des sources |

Les tables conservent `origine_id`, `type_origine`, `nom_origine`, `duree_s`,
`vitesse_kmh`, `distance_raccordement_m`, `temps_acces_s`, les identifiants
`raccord_de`, `raccord_vers`, `raccord_cle`, l'abscisse sur la ligne et les
coordonnées du raccord. Les identifiants usuels `arret_id`, `station_id` et
`id_carreau` sont également conservés sous leur nom source. Les polygones
ajoutent `tampon_m` et `surface_m2` ; cette surface décrit le tampon cartographique.

**Les polygones sont une approximation cartographique** : un tampon de **10 m
de chaque côté des chemins atteignables**, sans enveloppe convexe. Ce tampon
peut dépasser les extrémités atteignables ou franchir un mur ou un cours d'eau
de faible largeur. Il ne démontre pas l'accès aux bâtiments et aux parcelles.
Pour les futurs indicateurs de population et d'offre, utiliser le routage des
points, avec leur coût de raccordement, plutôt que compter aveuglément les
points inclus dans ces polygones. L'aperçu HTML simplifie à 1 m uniquement pour
l'affichage ; les GeoParquet conservent les géométries calculées.

Les destinations peuvent être dans une commune voisine : les chemins ne sont
pas coupés à la limite de Grenoble. Le champ `proche_limite_reseau` signale une
origine dont le rayon maximal de marche déborde de la zone d'extraction et dont
les résultats pourraient être tronqués. Une marge de 1 km couvre largement les
375 m théoriques de marche du pilote.

Quand `gold_candidates.parquet` sera disponible, utiliser le même script avec
`--origines chemin/gold_candidates.parquet --id-col id_carreau --type-origine candidat`
et un dossier de sortie distinct. Les candidats doivent être des points avec
leurs identifiants d'origine. Pour toute l'ARA, le réseau régional doit d'abord
être construit et assemblé ; ce pilote ne calcule pas encore O1, O2 ou les
couvertures de population.

Tests analytiques hors connexion (coût d'accès, courbes, branches, voies longues,
sens des géométries, composantes déconnectées et exports) :

```bash
docker compose run --rm pipeline python -W error -m unittest discover \
  -s tests -p test_build_isochrones.py -v
```
