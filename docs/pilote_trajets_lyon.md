# Validation des trajets piétons — pilote Lyon

**Version suivante disponible :** les deux accès de Croix-Paquet ont été
ajoutés selon l'hypothèse MVP acceptée. Résultats actuels dans
`data/gold/accessibilite_lyon_acces_stations/` ; voir
[acces_stations.md](acces_stations.md). Croix-Paquet dessert désormais cinq
centres, soit 6 138 habitants fiscaux estimés. Ce document conserve les
résultats du premier pilote, avant cette correction.

**Calcul terminé le 20 octobre 2026 ; tests techniques valides, indicateurs
partiels et accès métro à examiner avant une publication régionale.**

| Résultat | Effectif |
|---|---:|
| Centres dans Lyon / avec voisins | 976 / 1 318 |
| Population fiscale estimée des centres de Lyon | 450 909 |
| Offres dans Lyon / avec voisins | 1 226 / 1 437 |
| Paires accessibles en 300 s | 5 951 |
| Paires indéterminées | 7 |
| Groupes O1 calculés / partiels | 913 / 5 |
| Offres O2 calculées / non calculées | 1 225 / 1 |
| Paires traversant une limite de tuile principale | 164 |
| Paires depuis les voisins vers une offre lyonnaise | 208 |
| Paires depuis Lyon vers une offre voisine | 161 |

Les 1 226 offres lyonnaises comprennent **628 bus, 314 vélos fixes, 148 Citiz,
56 trolleybus, 45 tramways, 28 métros, 4 ferries et 3 funiculaires**. Les huit
modes ont des paires accessibles. Le réseau pilote fusionné compte
174 199 nœuds et 404 392 arcs. Ses copies de marges ne sont pas recomptées.

**101 tests du dépôt passent.** Les 32 trajets témoins sont identiques au
Dijkstra de référence et au trajet inverse à 10⁻⁶ s près. Un contre-calcul
séparé avec **SciPy CSR**, sans les méthodes de routage NetworkX du pilote,
confirme aussi ces 32 positifs et **32 négatifs** (quatre par mode).
Les sommes O2, la population O1, les identifiants, les valeurs partielles et
les empreintes ont également été contrôlés après export.

## Cas nécessitant une revue

**Lycée La Xavière** est l'offre O2 non calculée : la voie publique retenue
est à 57,52 m, au-delà du seuil de 50 m, et les accès plus proches sont marqués
privés. Les cinq groupes O1 partiels gardent une valeur complète vide. Deux
centres et un autre arrêt hors de Lyon (`Rue de Sète`) ont également un
raccordement hors seuil ; les sept paires potentielles indéterminées sont
conservées, sans les assimiler à zéro option.

**Croix-Paquet met en évidence une limite de représentation des métros.**
Le point GTFS est projeté sur une composante de 13 nœuds. Les deux voies
la reliant au reste du réseau passent par des nœuds OSM
`barrier=turnstile` : `7937873346` et `7937873362`, sur les voies
`850918864` et `857548004`. Ces tourniquets sont exclus par le filtre actuel.
Le graphe donne donc zéro habitant accessible, alors que 11 centres sont
à moins de 375 m à vol d'oiseau. Ce zéro décrit le graphe et **ne démontre
pas une absence réelle de population desservie**.

La suite est de représenter les accès de la station côté espace public et
leur lien avec le quai. Autoriser globalement les tourniquets comme
raccourcis de voirie ne serait pas une correction appropriée. Aucun accès
ni point source n'a été déplacé dans ce test. Le diagnostic brut de 500 m
autour de Croix-Paquet est conservé avec les tags, identifiants et empreintes.

Une table séparée contient **66 offres à examiner**, en croisant les alertes
du contrôle régional avec les résultats du pilote. Elle comprend les
28 points métro, les échecs et les zéros du modèle. Au total, **38 offres**
ont zéro dans le modèle : 8 n'ont aucun centre à moins de 375 m, et 12 sont
raccordées à une composante secondaire. Ces catégories ne sont pas une
validation de non-desserte sur le terrain ; les détails sont conservés.

Fichiers de revue : `data/silver/validation_trajets_lyon/`, notamment
`offres_a_verifier.csv`, `.parquet`, `.geojson`, `revue_qc.json` et
`diagnostic_croix_paquet.json`. La jointure avec O2 se fait par `offre_id` ;
les alertes doivent accompagner les résultats dans la restitution.

Le pilote utilise la commune IGN **69123 — Lyon**, et non toute la métropole
ni la région. Les arrondissements sont inclus géométriquement dans ce contour.
Les ménages fiscaux sont représentés au centre des carreaux ; il ne s'agit pas
de la population communale exhaustive ni d'un accès prouvé aux bâtiments.

## Méthode

- Points sources : entrées figées de `preparation_reseau_ara`, avec les modes
  bus, métro, vélo fixe et Citiz, ainsi que les autres modes livrés dans Lyon.
- O1 sélectionne les centres dans Lyon et peut compter les offres voisines.
  O2 sélectionne les offres dans Lyon et peut inclure les centres voisins.
- Population et offre voisines conservées jusqu'à **375 m** de la commune,
  par une distance géométrique directe au contour, pas par un tampon de desserte.
- Réseau issu des quatre tuiles `x83_y651`, `x83_y652`, `x84_y651`, `x84_y652`.
  Les copies d'un même arc OSM sont dédupliquées par `(de, vers, cle)` ; des
  coordonnées ou poids contradictoires empêchent le calcul. Les composantes
  locales des tuiles ne sont pas assimilées à des composantes régionales.
- Les segments intersectant une fenêtre autour de Lyon sont conservés
  intégralement, avec leurs poids. Marge réseau de **751 m** : deux rayons de
  marche plus 1 m pour compenser l'approximation polygonale des angles.
  La fenêtre doit couvrir un trajet complet depuis chaque centre retenu.
- Marche bidirectionnelle à **4,5 km/h**, budget **300 s**, raccords inclus
  au départ et à l'arrivée. Seuils de **150 m** pour la population et **50 m**
  pour l'offre. Aucune restriction d'accès n'est levée pour faciliter un trajet.

Le moteur `PairNetwork` du pilote Grenoble est réutilisé. Les points sont
projetés sur les segments, en tenant compte de leur orientation. Le routage
conserve les connexions OSM et les voies parallèles distinctes ; la proximité
géométrique de deux voies ne crée pas de connexion.

O1 garde la définition du pilote : nombre de points d'offre accessibles à un
centre, puis moyenne pondérée par la population pour les groupes de carreaux.
Les petits groupes sont réunis pour atteindre **20 habitants** avant publication.
La définition du plan V5 reste à harmoniser avant une publication régionale.
O2 somme la population des centres accessibles pour chaque offre ; les
populations desservies par plusieurs offres se recouvrent et ne s'additionnent
pas pour obtenir une population unique.

## Contrôles

- SHA-256 des entrées figées, du manifeste régional, du contrôle indépendant,
  des fichiers des quatre tuiles et du code, avant et après calcul.
- Unicité des couples population-offre, temps finis dans le budget, borne
  inférieure du temps à vol d'oiseau pour toutes les paires accessibles.
- Trajets témoins par mode et près du budget, recalculés avec deux nœuds
  virtuels et un Dijkstra de référence qui ne réutilise pas `travel_time`.
- Symétrie des trajets témoins dans le modèle bidirectionnel et retrait des
  nœuds virtuels après le test.
- Conservation de la population lors du regroupement O1 et des identifiants
  d'offre O2 ; une ligne par offre du pilote.
- Échec de raccordement conservé dans `paires_indeterminees`, jamais transformé
  en absence de desserte. Une valeur complète reste vide si le calcul est
  partiel ; une borne inférieure est conservée quand elle est calculable.

Un contrôle technique réussi n'affirme donc pas que tous les indicateurs sont
complets. Les entrées de métro, murs, étages, accès au bâti, pentes et exhaustivité
des sources restent les limites du modèle. Les points hors seuil examinés
dans [l'audit](audit_15_raccordements_offre_ara.md) ne sont pas déplacés.

## Lancement et fichiers

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/build_pilote_lyon.py
```

Le pilote est local, sans téléchargement. Les résultats historiques sont dans
`data/gold/accessibilite_lyon_validation/`. Le script utilise maintenant par
défaut `data/gold/accessibilite_lyon_acces_stations/`, avec refus d'écraser un dossier
existant. Choisir un autre `--output-dir` pour rejouer. Le dossier final est
publié seulement après contrôles ; Grenoble et les données ARA sont conservés.

| Fichier | Contenu |
|---|---|
| `gold_o1.parquet`, `.csv`, `.geojson` | Indicateurs par groupe de carreaux, statuts et bornes inférieures |
| `gold_o2.parquet`, `.csv`, `.geojson` | Population desservie par point d'offre de Lyon, voisins inclus |
| `accessibilite_carreaux_offres.parquet` | Paires accessibles uniques et temps de marche |
| `paires_indeterminees.parquet` | Paires potentielles impossibles à trancher avec les raccordements actuels |
| `raccordements_population.parquet`, `raccordements_offre.parquet` | Projections et statuts locaux utilisés par le moteur |
| `groupes_o1.parquet` | Lien entre les carreaux source et les groupes de publication |
| `perimetre.parquet` | Contour de Lyon retenu |
| `validation_trajets.json` | Contrôles et temps des trajets témoins |
| `accessibilite_qc.json` | Effectifs, hypothèses, limites, sources et empreintes des sorties |

GeoParquet en EPSG:2154, GeoJSON en EPSG:4326. Les fichiers techniques restent
internes, notamment les petits carreaux de population et les paires détaillées.
Aucune application de restitution n'est ajoutée : les données peuvent servir
à l'application du collègue.

Journaux de validation :

- `data/silver/journaux/tests_pilote_trajets_lyon.log` : 101 tests réussis.
- `data/silver/journaux/pilote_trajets_lyon.log` : exécution locale.
- `data/silver/journaux/controle_exports_pilote_lyon.json` : relecture des sorties.
- `data/silver/journaux/controle_independant_pilote_lyon.json` : 32 positifs et
  32 négatifs confirmés par SciPy ; code de ce contre-calcul conservé dans
  `controler_pilote_lyon_scipy.py`, dans le même dossier.
