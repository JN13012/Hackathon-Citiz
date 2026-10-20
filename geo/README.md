# geo — Réseau piéton, isochrones, O1 et O2 (Rôle 2)

- Construire le graphe piéton OSM et les `contours` (communes, EPCI, IRIS).
- Calculer les isochrones de 5 min à pied (vitesse 4,5 à 5 km/h, à afficher comme hypothèse).
- Produire `gold_isochrones`, `gold_o1`, `gold_o2`, `gold_couverture`.
- Livrer le contour du territoire en premier (lundi 15 h) : tout le monde en a besoin.

**Relation candidat–population ARA disponible** :
`data/gold/gold_couverture.parquet` contient 563 613 relations accessibles sur
835 tuiles. La table `data/gold/candidats_couverture.parquet` conserve les
312 143 candidats et leurs statuts ; 209 953 sont entièrement calculables.
Les cas indéterminés sont dans `data/gold/couverture_indeterminee.parquet`.
Voir [le contrat, les limites et les contrôles](../docs/couverture_ara.md).

```bash
python geo/build_couverture.py --workers 2
# Pour reprendre un dossier existant : ajouter --reprendre.
python geo/validate_couverture.py --publier-dans data/gold
```

Les candidats sont les centres des carreaux habités, pas des implantations
validées. La sélection, les coûts et les gains après retrait de la desserte
existante appartiennent au rôle 4. Les isochrones polygonaux régionaux restent
un livrable séparé de cette matrice routée.

## Préparation du réseau régional ARA

Livraison du 20 octobre 2026 : **835/835 tuiles construites et contrôlées** dans
`data/silver/reseau_ara_bidirectionnel/`, soit 16 608 205 nœuds OSM distincts et
17 246 938 segments parcourables dans les deux sens. Le contrôle indépendant
valide les identifiants, géométries, références, longueurs et temps de marche.
Voir le [compte rendu](../docs/execution_reseau_ara.md). Les calculs **O1/O2 ARA
sont terminés et contrôlés** sur 312 143 carreaux et 18 735 points d'offre,
dont 901 stations vélo. Le dossier de livraison est
`data/gold/accessibilite_ara/resultats/publics/` ; les statuts partiels et les
15 offres non calculables sont conservés. Voir la
[méthode, les effectifs et la reprise](../docs/accessibilite_ara.md).

```bash
python geo/build_accessibilite_ara.py --workers 2
# Si le dossier existe déjà :
python geo/build_accessibilite_ara.py --workers 2 --reprendre
python geo/validate_accessibilite_ara.py
```

Ces commandes utilisent les sources figées et les raccordements contrôlés.
Elles ne relancent pas la collecte ni la construction du réseau. Les tables
`resultats/internes/`, `regroupement/` et les checkpoints restent techniques ;
la diffusion O1 utilise les groupes publics d'au moins 20 habitants.

Le contrôle des raccordements régionaux est disponible séparément :

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/check_raccordements_ara.py
```

Il utilise les entrées figées et les GeoParquet du réseau, sans télécharger ni
recollecter les données des autres rôles. Les distances, projections sur les
voies et diagnostics sont écrits dans `data/silver/raccordements_ara/` :
deux GeoParquet complets, des CSV et GeoJSON de points à examiner, et
`raccordements_qc.json`. Le dossier final est publié seulement après contrôles ;
un dossier existant n'est jamais remplacé. Pour un essai limité, utiliser
`--tuiles x84_y651 x91_y645 --output-dir data/silver/raccordements_ara_validation`.
Le statut reste alors explicitement partiel. Ce contrôle ne calcule aucun
trajet ni O1/O2. Voir [raccordements_ara.md](../docs/raccordements_ara.md).

Les 15 offres hors seuil ont été examinées dans les GTFS/GBFS et des
extractions OSM brutes. Le script `audit_raccordements_offre.py` reproduit les
groupes d'arrêts depuis leurs identifiants et coordonnées, recherche les
références horaires et compare les voies proches au filtre du réseau. Le
[diagnostic détaillé](../docs/audit_15_raccordements_offre_ara.md) précise les
causes, les rôles concernés et les actions. Résultats dans
`data/silver/audit_raccordements_offre_ara/` ; aucune correction source ni
augmentation de seuil n'a été appliquée.

Le [pilote de trajets Lyon](../docs/pilote_trajets_lyon.md) réutilise le moteur
Grenoble sur les quatre tuiles régionales fusionnées, avec voisins et sans
double compte des marges :

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/build_pilote_lyon.py
```

Résultats actuels dans `data/gold/accessibilite_lyon_acces_stations/` : paires
accessibles, cas indéterminés, O1/O2 et rapports. Deux accès extérieurs de
Croix-Paquet sont configurés dans `geo/config/acces_stations.json`. Atteindre
l'un des accès en cinq minutes suffit ; la station, ses fréquences et la
population sont comptées une seule fois. Le graphe public et les coordonnées
GTFS sont conservés. Voir [acces_stations.md](../docs/acces_stations.md).

Le pilote initial sans accès reste dans `data/gold/accessibilite_lyon_validation/`
avec sa revue dans `data/silver/validation_trajets_lyon/`. Les indicateurs
actuels restent partiels pour les cas non résolus. Aucun calcul ARA complet
n'est annoncé par ce pilote.

`prepare_reseau_ara.py` prépare les entrées et le découpage de toute la région,
sans téléchargement et sans construire encore le graphe. Les archives OSM
Auvergne et Rhône-Alpes sont déjà dans `data/bronze/osm_ara/`.

Utiliser explicitement la livraison validée des arrêts. Un ancien lancement
avait ignoré TCL Lyon après une erreur HTTP 401 et omis `--contours`, puis
publié une collecte partielle. Le collecteur a depuis été protégé et les
fichiers actifs restaurés : un échec GTFS arrête maintenant la publication,
les contours sont obligatoires et les contrôles précèdent le remplacement.
Les entrées figées de cette préparation restent la référence du futur
traitement régional.

```bash
docker compose run --rm --build pipeline python geo/prepare_reseau_ara.py \
  --arrets "data/bronze/livraisons/transport_20261020T115246/arrets 2.parquet" \
  --output-dir data/silver/preparation_reseau_ara
```

Le script refuse de remplacer un dossier de préparation existant : choisir un
nouveau `--output-dir` pour une nouvelle version. Par défaut il exige les 19
réseaux déclarés dans le collecteur GTFS, vérifie que les points d'offre sont
dans les communes IGN, conserve les carreaux frontaliers et les capacités
inconnues, et vérifie les SHA-256 des deux archives OSM. La présence d'un code
de réseau ne prouve pas que tous ses arrêts sont présents ; l'exhaustivité reste
limitée à la livraison contrôlée.

Sorties dans le dossier demandé :

| Fichier | Rôle |
|---|---|
| `entrees/contours.parquet`, `territoire.parquet` | Référentiel régional figé |
| `entrees/arrets.parquet`, `stations_partagees.parquet`, `carreaux_socio.parquet` | Copies des entrées sélectionnées pour la préparation |
| `entrees/gold_offre.parquet`, `gold_candidates.parquet` | Entrées dérivées avec noms des stations corrigés et affectation à une tuile |
| `tuiles.parquet` | Grille Lambert 93 de 10 km, couvrant ARA et sa bordure ; effectifs de points par tuile principale |
| `zones_extraction.parquet`, `.geojson` | Fenêtres de chaque tuile avec une marge supplémentaire de 1 km |
| `plan_reseau_ara.json` | Sources, empreintes, hypothèses, effectifs et état explicite `graphe_construit: false` |

La règle `floor(x / taille)`, `floor(y / taille)` attribue un seul propriétaire
à chaque point, y compris sur une limite de tuile. Les copies de points dans
les marges ne devront pas être recomptées dans O1/O2. Les fenêtres avec marge
se chevauchent volontairement pour conserver les trajets entre tuiles. Le
paramètre `calcul_population_offre_utile` indique les tuiles contenant des
origines ; il ne constitue pas une autorisation de supprimer leurs voisines du
réseau.

`build_reseau_ara.py` construit maintenant les graphes depuis les PBF : fusion
des objets OSM identiques, filtrage des accès piétons, traitement des
barrières, contrôle des connexions et des poids, puis exports par tuile.
Les validations de `build_reseau.py` sont réutilisées. Les versions OSM
contradictoires et les données invalides provoquent un échec avant publication
de la tuile. Hypothèse retenue pour le projet : **toute voie conservée peut être
parcourue à pied dans les deux sens**, avec le même temps de marche. Les tags
de direction et restrictions de virage sont ignorés et signalés dans le rapport.
Les accès conditionnels sont exclus et comptés. Les voies cyclables autorisées
explicitement à pied sont incluses.

Les deux GeoParquet de nœuds et tronçons suffisent à reconstruire le graphe
avec `load_tile_graph(dossier_tuile)`. L'export GraphML, coûteux pour 835 tuiles,
est facultatif avec `--graphml`. Les contrôles incluent la relecture du graphe
depuis les Parquet. Une seule extraction est effectuée par lot par défaut pour
limiter la mémoire. Voir le suivi et la méthode dans
[execution_reseau_ara.md](../docs/execution_reseau_ara.md).

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/run_reseau_ara.py \
  --tuiles x91_y645 x84_y651 --output-dir data/silver/reseau_ara_bidirectionnel
```

Pour traiter ensuite toutes les tuiles du plan et reprendre l'essai :

```bash
docker compose run --rm pipeline python geo/run_reseau_ara.py \
  --output-dir data/silver/reseau_ara_bidirectionnel --reprendre
```

La reprise vérifie les empreintes et refuse de mélanger des versions de code,
de plan ou d'archives différentes. Les tuiles sont publiées séparément après
validation ; le bilan distingue une construction partielle de la couverture
régionale complète. Voir les règles, sorties et limites dans
[construction_reseau_ara.md](../docs/construction_reseau_ara.md).

Le nouveau réseau suit le même modèle bidirectionnel que les scripts
d'accessibilité actuels. Leur orchestration par tuile reste à mettre en place.
Après validation des graphes, leur orchestration par tuile et la fusion des
résultats seront nécessaires. La construction du réseau peut avancer sans
modifier la formule O1 ; sa définition de publication reste à harmoniser avec
le plan V5 avant de publier les indicateurs régionaux.

```bash
docker compose run --rm pipeline python -W error -m unittest discover \
  -s tests -p test_prepare_reseau_ara.py -v
```

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
Le premier pilote utilise les arrêts présents dans Grenoble. Les carreaux de
population et les candidats sont maintenant disponibles localement ; le calcul
O1/O2 ci-dessous les croise avec les arrêts et les stations partagées.

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

Le dossier local `isochrones_grenoble_arrets` provient du premier jeu d'arrêts,
avant l'import Teams du 20 octobre. Ces exports historiques n'ont pas été
recalculés après cet import ; vérifier leur rapport et l'empreinte source avant
une jointure. Les indicateurs O1/O2 ci-dessous utilisent la livraison actuelle
et routent directement les paires de points.

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
Pour les indicateurs de population et d'offre, utiliser le routage des
points, avec leur coût de raccordement, plutôt que compter aveuglément les
points inclus dans ces polygones. L'aperçu HTML simplifie à 1 m uniquement pour
l'affichage ; les GeoParquet conservent les géométries calculées.

Les destinations peuvent être dans une commune voisine : les chemins ne sont
pas coupés à la limite de Grenoble. Le champ `proche_limite_reseau` signale une
origine dont le rayon maximal de marche déborde de la zone d'extraction et dont
les résultats pourraient être tronqués. Une marge de 1 km couvre largement les
375 m théoriques de marche du pilote.

Pour calculer les polygones depuis `gold_candidates.parquet`, utiliser le même script avec
`--origines chemin/gold_candidates.parquet --id-col id_carreau --type-origine candidat`
et un dossier de sortie distinct. Les candidats doivent être des points avec
leurs identifiants d'origine. Pour toute l'ARA, le réseau régional doit d'abord
être construit et assemblé. Le script O1/O2 ci-dessous route directement les
paires de points ; il ne nécessite pas de fabriquer leurs polygones.

Tests analytiques hors connexion (coût d'accès, courbes, branches, voies longues,
sens des géométries, composantes déconnectées et exports) :

```bash
docker compose run --rm pipeline python -W error -m unittest discover \
  -s tests -p test_build_isochrones.py -v
```


## O1 et O2 — pilote Grenoble

`build_accessibilite.py` calcule les deux indicateurs **hors connexion** sur le
réseau piéton local. Les entrées doivent déjà être présentes :
`data/silver/carreaux_socio.parquet`, `data/gold/gold_offre.parquet`, les contours
IGN et `data/silver/reseau_grenoble/`. Les données restent exclues de Git.

```bash
docker compose build
docker compose run --rm pipeline python geo/build_accessibilite.py \
  --codes-communes 38185
```

Les résultats sont dans `data/gold/accessibilite_grenoble/`. Lancer ce script
séparément du pipeline d'ingestion pour réutiliser les livraisons locales sans
les télécharger de nouveau. `--output-dir` permet de conserver plusieurs essais.
Le réseau de Grenoble ne permet pas de sélectionner une autre ville : construire
son réseau, puis le fournir avec `--reseau-dir`.

### Définitions et hypothèses

**O1** mesure combien de points d'offre distincts un habitant représenté au
centre de son carreau peut atteindre. Avec 100 habitants et 5 options accessibles,
la valeur est **5 options par habitant**. Lors d'un regroupement de carreaux,
la moyenne est pondérée par leur population : `somme(P_i × N_i) / somme(P_i)`.
On ne divise pas les 5 options par 100 et on ne somme pas les valeurs des carreaux.
Les comptes par mode sont aussi fournis. Pour les GTFS, les sommes des
`passages_par_jour` accessibles sont conservées séparément par mode ; elles ne
sont additionnées ni aux capacités GBFS ni aux nombres de stations.

Les carreaux de **moins de 20 habitants** sont fusionnés avec un voisin, en
privilégiant le plus long bord commun, jusqu'à atteindre ce seuil. À défaut de
voisin contigu, le groupe géométriquement le plus proche est utilisé et ce repli
est compté dans le rapport. Chaque ligne publique O1 représente un `groupe_id`,
même si elle ne contient qu'un carreau. `groupes_o1.parquet` permet la jointure
technique avec les identifiants de carreaux. Le regroupement évite d'afficher
les petits effectifs individuellement ; ce n'est pas une garantie générale
d'anonymisation. Un périmètre entier sous le seuil est masqué.

**O2** somme la population des carreaux dont le centre rejoint chaque arrêt ou
station de Grenoble en cinq minutes. Les habitants des communes voisines sont
inclus ; `population_desservie_dans_pilote` permet de les distinguer. Un carreau
ne compte qu'une fois par station. Une personne peut être desservie par plusieurs
stations : **les lignes O2 ne s'additionnent pas pour obtenir des habitants uniques**.

Le calcul conserve le modèle du réseau : **300 s à 4,5 km/h**, soit au maximum
375 m, raccordements compris. Les raccords droits à la voie la plus proche
consomment du temps aux **deux** extrémités. Seuils déclarés : **150 m pour un
centre de carreau de 200 m**, **50 m pour une offre**. Ils sont réglables avec
`--max-raccord-population-m` et `--max-raccord-offre-m`. Ce sont des hypothèses
d'accès : aucune traversée de mur, entrée de bâtiment ou différence d'étage
n'est vérifiée. Les trajets respectent les composantes du graphe et prennent en
compte les portions intérieures des longues voies, même sans nœud atteignable.
Les surfaces tamponnées des isochrones ne servent jamais aux comptages.

La population d'un carreau est attribuée à son centre, puis entièrement conservée,
sans prorata par surface à la frontière de Grenoble. La présélection utilise
une marge de 375 m pour l'offre et la population ; le script exige une extraction
réseau assez grande pour couvrir ces trajets. Les **135 063,5 habitants estimés**
proviennent de Filosofi 2021 et ne constituent pas un recensement communal :
champ des ménages fiscaux, valeurs parfois imputées ou fractionnaires.

Un raccordement impossible produit un statut explicite. Les indicateurs complets
sont alors vides ; les champs `*_min` gardent les résultats connus quand une
borne inférieure peut être calculée. Une capacité manquante reste vide sans
empêcher de compter la station. `complet` décrit le calcul sur les fichiers
fournis, sans garantir l'exhaustivité des transports recensés.

### Sorties

| Fichier | Contenu |
|---|---|
| `gold_o1.parquet`, `.csv`, `.geojson` | Groupes de carreaux, population, options accessibles par habitant, comptes par mode, fréquences et capacités séparées |
| `gold_o2.parquet`, `.csv`, `.geojson` | Offre dans Grenoble, type, valeur/unité source, population desservie totale et dans le pilote |
| `groupes_o1.parquet` | Correspondance technique `id_carreau` → `groupe_id` |
| `accessibilite_carreaux_offres.parquet` | Paires accessibles uniques et temps de marche en secondes ; inclut les voisins utiles à O1/O2 |
| `paires_indeterminees.parquet` | Paires géométriquement possibles sans calcul valide ; aucune présentée comme inaccessible |
| `raccordements_population.parquet`, `raccordements_offre.parquet` | Points, distances, coûts d'accès, tronçons, abscisses et statuts |
| `accessibilite_qc.json` | Périmètre, hypothèses, effectifs, contrôles et SHA-256 des entrées |
| `apercu_o1_o2.html` | Carte interactive autonome, à ouvrir directement dans un navigateur |

GeoParquet : EPSG:2154 ; GeoJSON : EPSG:4326 ; CSV : attributs sans géométrie.
Les exports techniques servent à auditer et à refaire les jointures ; la carte
O1 utilise uniquement les groupes. Les fichiers gold existants à la racine de
`data/gold/` restent des entrées, les nouvelles sorties sont dans le dossier pilote.

Résultats mesurés et limites de livraison : [bilan Grenoble](../docs/resultats_geomatique_grenoble.md).
L'extension à Lyon est validée. L'exécution O1/O2 ARA et sa reprise sont décrites
dans [accessibilite_ara.md](../docs/accessibilite_ara.md). `gold_couverture` entre
futurs emplacements candidats et population est désormais produite séparément ;
voir [couverture_ara.md](../docs/couverture_ara.md). La relation carreaux–offre
existante sert aux indicateurs actuels et conserve ses fichiers propres.

Tests analytiques et exports (avec les contrôles des étapes précédentes) :

```bash
docker compose run --rm pipeline python -W error -m unittest discover \
  -s tests -p 'test_build*.py' -v
```
