# O1 et O2 — bilan du pilote Grenoble

Calcul effectué le **20 octobre 2026**, pour Grenoble (INSEE `38185`). Les
résultats sont prêts pour l'intégration du pilote, sous les hypothèses décrites
ci-dessous. Ils ne couvrent pas encore Lyon ou toute l'Auvergne-Rhône-Alpes.

## Résultats

| Indicateur | Résultat sur les données fournies |
|---|---|
| Carreaux de population dont le centre est dans Grenoble | 349 |
| Population Filosofi représentée | 135 063,5 |
| Zones O1 après regroupement des petits effectifs | 323, toutes à au moins 20 habitants |
| Nombre moyen d'options accessibles par habitant, pondéré par la population | **5,38** |
| Étendue des valeurs O1 par zone | 0 à 19 |
| Population avec au moins une option recensée accessible | **129 840**, soit **96,13 %** |
| Population sans option recensée accessible dans le modèle | 5 223,5 |
| Arrêts et stations de Grenoble avec résultat O2 | **303** |
| Relations centre de carreau–offre accessibles | 1 538 |
| Points rejetés pour défaut de raccordement | 0 |

O2, population desservie en cinq minutes, **y compris depuis les communes voisines** :

| Type | Points dans Grenoble | Population médiane par point | Population maximale par point |
|---|---:|---:|---:|
| Bus | 139 | 2 026,5 | 5 953 |
| Tram | 36 | 3 086,5 | 5 645 |
| Téléphérique (`cable_car`) | 2 | 704,75 | 1 409,5 |
| Citiz | 126 | 2 602,75 | 6 004 |

Pour 25 des 303 points, la population desservie inclut des centres hors de
Grenoble. Neuf points n'atteignent aucun centre de carreau peuplé en cinq minutes
dans ce modèle ; une valeur O2 de zéro ne démontre pas l'absence de riverains.
Les populations O2 se recouvrent : ne pas les additionner pour compter des
habitants uniques.

## Méthode à expliquer à l'équipe

Le calcul utilise le réseau OSM de Grenoble avec une marge de **1 km**,
**300 secondes à 4,5 km/h** et les **479 composantes** conservées. Les trajets
traversent la limite communale si nécessaire. Une présélection à 375 m retient
506 centres de population et 372 points d'offre ; la distance directe sert
uniquement à écarter les trajets impossibles, puis Dijkstra vérifie les parcours
sur le réseau. Les polygones tamponnés des isochrones ne servent pas au comptage.

Chaque centre de carreau et point d'offre est projeté sur la voie la plus proche.
Le trajet droit de raccordement consomme du temps aux deux extrémités. Les seuils
sont **150 m pour un centre de carreau de 200 m** et **50 m pour une offre** ;
les distances maximales observées, voisins compris, sont respectivement
97,74 m et 21,70 m. Ces raccords sont des hypothèses : les murs, entrées de
bâtiments, étages et autres obstacles hors du réseau ne sont pas vérifiés.

**O1** : si le centre d'un carreau de 100 habitants rejoint cinq points d'offre,
chaque habitant représenté a cinq options accessibles. La valeur est 5.
Lors d'un regroupement, `O1 = somme(population × nombre_options) / somme(population)`.
Les comptes par mode, fréquences GTFS et capacités GBFS sont exportés séparément.
Les nombres de passages aux différents arrêts ne sont pas des départs uniques
et ne se mélangent pas aux capacités ou aux nombres de stations.

Les 29 carreaux sous 20 habitants sont regroupés en privilégiant les voisins
ayant un bord commun. Deux fusions utilisent un voisin sans bord commun ; leurs
géométries restent disjointes, sans inventer de surface de liaison. La population
est intégralement conservée et aucun petit effectif n'est présenté isolément
dans la carte O1. Cela ne constitue pas une garantie générale d'anonymisation.

**O2** : pour chaque arrêt/station de Grenoble, on somme une seule fois la
population de chaque centre accessible, y compris dans les communes voisines.
Le champ `population_desservie_dans_pilote` fournit la part située dans Grenoble.

## Fichiers à transmettre

Dans `data/gold/accessibilite_grenoble/` :

- `gold_o1.parquet`, `gold_o1.csv`, `gold_o1.geojson` : résultats par `groupe_id` ;
- `gold_o2.parquet`, `gold_o2.csv`, `gold_o2.geojson` : résultats par `offre_id` ;
- `apercu_o1_o2.html` : carte autonome, sans serveur ni connexion ; bascule O1/O2 et valeurs au survol ;
- `accessibilite_qc.json` : paramètres, effectifs, limites, versions et empreintes SHA-256 ;
- `accessibilite_validation.json` : contre-calcul indépendant et contrôles de conservation.

Pour les jointures et l'audit : `groupes_o1.parquet` relie les carreaux aux
groupes ; `accessibilite_carreaux_offres.parquet` donne les paires accessibles
et leur temps. Les tables `raccordements_*.parquet` décrivent les points et leur
projection. `paires_indeterminees.parquet` est vide sur cette exécution ; une
paire rejetée pour défaut de calcul ne serait jamais interprétée comme inaccessible.

Les GeoParquet sont en EPSG:2154 ; les GeoJSON en EPSG:4326. Les CSV contiennent
les attributs sans géométrie. O1 utilise `groupe_id`, pas une ligne par carreau
individuel. Le [dictionnaire](dictionnaire_donnees.md) décrit les colonnes.
Les données sont exclues de Git : transmettre le dossier de résultats avec le code.

Pour reproduire le calcul depuis les données locales déjà préparées :

```bash
docker compose build
docker compose run --rm pipeline python geo/build_accessibilite.py \
  --codes-communes 38185
```

Le [guide géomatique](../geo/README.md) décrit les entrées, paramètres et sorties.
Il n'est pas nécessaire de relancer le pipeline d'ingestion pour ce calcul.

## Contrôles et limites

Les **48 tests géomatiques passent**, avertissements traités comme des erreurs.
Les nouveaux tests couvrent les trajets à l'intérieur de longues voies, le coût
des deux raccords, les carrefours, les composantes voisines déconnectées, la limite
de 300 secondes, les effectifs regroupés, les unités et les valeurs manquantes.

Un contre-calcul indépendant reconstruit un graphe non orienté avec ses voies
parallèles, découpe toutes les voies aux projections des centres et des offres,
puis relance Dijkstra depuis les 506 centres. Il retrouve les **1 538 paires** ;
l'écart maximal des temps est inférieur à **0,000001 s**. Les agrégations O1/O2,
la conservation de la population, la validité des géométries, le seuil de
publication et les trajets transfrontaliers sont aussi contrôlés. La carte a
été ouverte dans Chromium : 323 zones O1 et 303 points O2, bascule et survol valides.
Ces contrôles valident le modèle et les fichiers, pas les conditions de terrain.
Le rapport de validation est lié à l'empreinte du rapport QC de cette exécution ;
un nouveau calcul nécessite une nouvelle validation des données.

Limites des sources à conserver dans la restitution :

- Filosofi 2021 couvre les ménages fiscaux : population estimée, parfois imputée ou fractionnaire. Les habitants d'un carreau sont tous représentés à son centre ; les carreaux frontaliers lui sont entièrement attribués. Les 135 063,5 ne sont pas un total communal de recensement.
- **Aucune station vélo n'est recensée à Grenoble dans la livraison utilisée.** Cela ne prouve pas qu'aucun service vélo n'existe sur le terrain.
- Neuf arrêts ont une fréquence source de zéro, conservée telle quelle. O1 compte les points d'offre, sans garantir un départ effectif ou un véhicule disponible. Les fréquences exportées permettent de distinguer cette situation.
- Une capacité Citiz manque dans Grenoble et rend la somme des capacités indéterminée pour cinq zones O1. Elle reste vide ; le nombre de stations et la population desservie restent calculables.
- Le champ `nom` de certaines stations est un code commune dans le gold source fourni. Les identifiants sont préservés ; ce libellé devra être corrigé dans la construction de `gold_offre` pour la restitution définitive.
- Le réseau conserve les barrières comme tags, sans pénalité de passage ; pente, feux, horaires, restrictions directionnelles et accessibilité PMR ne sont pas modélisés. Le filtre OSM peut omettre des chemins utilisables à pied.

## Ce qui reste hors de cette livraison

L'extension du réseau et des calculs à Lyon/ARA, les contrôles sur des trajets de
terrain connus et `gold_couverture` entre **candidats et population** restent à
réaliser pour le périmètre complet du projet. La relation entre carreaux et
**offre existante** produite ici répond à O1/O2 ; elle ne remplace pas la matrice
de couverture attendue pour l'implantation de nouvelles stations.
