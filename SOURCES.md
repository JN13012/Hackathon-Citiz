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
| Population par carreau de 200 m (INSEE, Filosofi 2021, dernière édition carroyée) | https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip | population, ménages, revenus, âges · téléchargé le 19/10/2026 (rôle 3) |
| Projections de population 2018-2070 (INSEE, Omphale) | https://www.insee.fr/fr/statistiques/6652134 | croissance jusqu'en 2030 |
| Limites administratives et IRIS (IGN, ADMIN EXPRESS) | https://geoservices.ign.fr/adminexpress | contours |
| Réseau piéton et routier (OpenStreetMap, Geofabrik) | https://download.geofabrik.de/europe/france.html | isochrones de 5 min |

Formats : https://gtfs.org/ · https://gbfs.org/

## GTFS du territoire (Occitanie)

| Réseau | Jeu de données |
|---|---|
| Tisséo (Toulouse) | https://transport.data.gouv.fr/datasets/tisseo-offre-de-transport-gtfs |
| liO (réseau régional) | https://transport.data.gouv.fr/datasets/reseau-lio-occitanie |
| Grand Albigeois | https://www.data.gouv.fr/datasets/offre-de-transports-du-grand-albigeois-gtfs |
| Grand Cahors | https://transport.data.gouv.fr/datasets/donnees-gtfs-du-reseau-de-transports-public-du-grand-cahors |

## Flux Citiz

À compléter avec le résultat de `scripts/compter_stations_citiz.py` (réseaux Occitanie et autres réseaux utilisés pour entraîner le modèle).

## Données complémentaires

| Donnée | Source | Statut |
|---|---|---|
| Ménages sans voiture par IRIS (INSEE, recensement 2022, `P22_RP` et `P22_RP_VOIT1P`) | https://www.insee.fr/fr/statistiques/fichier/8647012/base-ic-logement-2022_csv.zip | chargé le 19/10/2026 (rôle 3) |
| Contours des IRIS (IGN, service WFS, couche `STATISTICALUNITS.IRIS:contours_iris`) | https://data.geopf.fr/wfs/ows | chargé le 19/10/2026 (rôle 3) |
| Base permanente des équipements (INSEE, BPE 2025, fichier-détail géolocalisé) | https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet | chargé le 19/10/2026 (rôle 3) |
| Emplois au lieu de travail par commune (INSEE, recensement 2022, `P22_EMPLT`) | https://www.insee.fr/fr/statistiques/fichier/8581444/base-cc-emploi-pop-active-2022_csv.zip | chargé le 19/10/2026 (rôle 3) |
| Populations de référence 2023 (INSEE), contrôle de cohérence | https://www.insee.fr/fr/statistiques/fichier/8680726/ensemble.zip | Filosofi = 94,2 % de la population légale (rôle 3) |
| Enquête nationale sur l'autopartage (ADEME, édition la plus récente) | https://www.ademe.fr | à vérifier |
| Facteur d'émission d'une voiture par km (ADEME, Base Empreinte) | https://base-empreinte.ademe.fr | à relever |

## Coûts des équipements

Aucun montant n'est fourni par le sujet. Chaque valeur de `optim/couts.yaml` doit avoir une source ici, sinon elle est marquée « hypothèse ».