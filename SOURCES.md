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
| Projections de population 2018-2070 (INSEE, Omphale 2022 ; scénarios central, population basse, population haute) | https://www.insee.fr/fr/statistiques/6652134 | croissance jusqu'en 2030 |
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
Sans ces archives, `geo/download_contours_ign.py` récupère les mêmes données par le service WFS de l'IGN
(https://data.geopf.fr/wfs/ows : `ADMINEXPRESS-COG.LATEST:commune`, `:epci`, `:arrondissement_municipal`,
`STATISTICALUNITS.IRIS:contours_iris`), filtrées sur la région 84, enregistrées dans `data/bronze/ign/` :
4 025 communes, 172 EPCI, 9 arrondissements de Lyon, 5 407 IRIS (vérifié le 21/10/2026).
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
Grenoble. Les candidats par carreau sont désormais disponibles localement. La méthode utilise
[Dijkstra avec seuil de temps (NetworkX)](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.shortest_paths.weighted.single_source_dijkstra_path_length.html)
et la [découpe de lignes (Shapely)](https://shapely.readthedocs.io/en/stable/manual.html#shapely.ops.substring).
Hypothèses supplémentaires : raccordement droit au chemin le plus proche,
distance maximale de 50 m et temps d'accès inclus dans les 300 secondes ;
représentation polygonale par tampon de 10 m autour des chemins atteignables.
Ce tampon cartographique ne démontre pas l'accès au bâti. Les chemins eux-mêmes
sont exportés et le rapport conserve les empreintes des entrées et les exclusions.

## GTFS du territoire (Auvergne-Rhône-Alpes)

Réseaux téléchargés par `scripts/build_arrets.py` (Rôle 1), 19 jeux de données :

| Réseau | Clé | Jeu de données |
|---|---|---|
| TCL (Lyon) | `tcl_lyon` | https://www.data.gouv.fr/datasets/horaires-theoriques-du-reseau-transports-en-commun-lyonnais · **accès Grand Lyon authentifié (HTTP 401)** : GTFS officiel TCL (éditeur TCL, valide du 19/10/2026 au 16/02/2027, 9 842 points d'arrêt, 4 lignes de métro, 8 de tram, 2 funiculaires ; SHA-256 `1e1ea83a1e10a175777f17c2183b0ea68fc708eab7e1dc57247c5a9c94306a0c`), partagé dans le groupe interne Teams de l'équipe ; à copier en `data/bronze/gtfs/tcl_lyon.zip` |
| TAG / M réso (Grenoble) | `tag_grenoble` | https://www.data.gouv.fr/datasets/horaires-theoriques-du-reseau-tag |
| Téléphérique de la Bastille (Grenoble) | `bastille_grenoble` | https://www.data.gouv.fr/datasets/horaires-theoriques-du-telepherique-de-la-bastille |
| Pays Voironnais | `pays_voironnais` | https://www.data.gouv.fr/datasets/horaires-theoriques-du-reseau-transport-du-pays-voironnais |
| STAS (Saint-Étienne) | `stas_saint_etienne` | https://www.data.gouv.fr/datasets/horaires-du-reseau-stas |
| STAR (Roanne) | `star_roanne` | https://www.data.gouv.fr/datasets/star-horaires-theoriques-et-temps-reel-du-reseau-de-transport-urbain-de-roannais-agglomeration |
| T2C (Clermont-Ferrand) | `t2c_clermont` | https://www.data.gouv.fr/datasets/syndicat-mixte-des-transports-en-commun-de-lagglomeration-clermontoise-smtc-ac-reseau-t2c-gtfs-gtfs-rt |
| Mobivie (Vichy) | `mobivie_vichy` | https://www.data.gouv.fr/datasets/mobivie-reseau-urbain-vichy-communaute |
| TUDIP (Le Puy-en-Velay) | `velay_le_puy` | https://www.data.gouv.fr/datasets/fichier-gtfs-des-transports-urbains-de-la-communaute-dagglomeration-du-puy-en-velay |
| Synchro (Chambéry) | `synchro_chambery` | https://www.data.gouv.fr/datasets/arrets-et-horaires-theoriques-bus-temps-reel |
| Sibra (Annecy) | `sibra_annecy` | https://www.data.gouv.fr/datasets/offre-de-transports-sibra-a-annecy-gtfs |
| TAC (Annemasse) | `tac_annemasse` | https://www.data.gouv.fr/datasets/offre-de-transports-reseau-tac-annemasse-agglo |
| Rubis (Bourg-en-Bresse) | `rubis_bourg` | https://www.data.gouv.fr/datasets/rubis-grand-bourg-mobilites-offre-de-transport-theorique-et-temps-reel |
| Citéa (Valence Romans) | `citea_valence` | https://www.data.gouv.fr/datasets/ce-jeu-de-donnees-contient-la-liste-des-arrets-des-horaires-et-des-parcours-theoriques-du-reseau-de-transport-urbain-et-interurbain-de-vrm |
| Cars Région Savoie (73) | `cars_savoie` | https://www.data.gouv.fr/datasets/reseau-interurbain-cars-region-savoie-73 |
| Cars Région Allier (03) | `cars_allier` | https://www.data.gouv.fr/datasets/reseau-interurbain-cars-region-allier-03 |
| Cars Région Ardèche (07) | `cars_ardeche` | https://www.data.gouv.fr/datasets/reseau-interurbain-cars-region-ardeche-07 |
| Cars Région Haute-Loire (43) | `cars_haute_loire` | https://www.data.gouv.fr/datasets/reseau-interurbain-cars-region-haute-loire-43 |
| Cars Région Cantal (15) | `cars_cantal` | https://www.data.gouv.fr/datasets/reseau-interurbain-cars-region-cantal-15 |

## Flux Citiz et offre partagée

- Flux Citiz lus par `optim/build_offre_partagee.py` : catalogue transport.data.gouv.fr + `https://backend.citiz.fr/public/provider/<n>/gbfs/v3.0/gbfs.json`
  (18 fournisseurs actifs le 19/10/2026, dont OGALO Saumur, absent de l'EDA) : 1 605 stations publiques ouvertes en France,
  dont 620 en Auvergne-Rhône-Alpes (Alpes Loire `citiz_auvergne_rhone_alpes` 410, Métropole de Lyon 200, Auto-en-Velay 9,
  Citiz Développement 1 ; 129 communes), + 25 stations « Ouverture prochaine » (Grand Est).
- Formules tarifaires Citiz : flux GBFS `system_pricing_plans` des mêmes fournisseurs (172 formules, 17 réseaux) -> recettes.
- Autres flux GBFS en stations : 5 380 stations vélo et 458 stations d'autopartage hors Citiz en France ; en Auvergne-Rhône-Alpes,
  Vélo'v Lyon (465), VéliVert Saint-Étienne (101), C Vélo Clermont-Ferrand (82), Velonecy Annecy (79), Libélo Valence (67), etc.
  État de chaque flux : `data/bronze/gbfs_flux.csv`.
- Contours des communes du territoire : https://geo.api.gouv.fr/decoupage-administratif (en attendant `contours` du Rôle 2).

## Données complémentaires

| Donnée | Source | Statut |
|---|---|---|
| Ménages sans voiture par IRIS (INSEE, recensement 2022, `P22_RP` et `P22_RP_VOIT1P`) | https://www.insee.fr/fr/statistiques/fichier/8647012/base-ic-logement-2022_csv.zip | chargé le 19/10/2026 (rôle 3) |
| Contours des IRIS (IGN, service WFS, couche `STATISTICALUNITS.IRIS:contours_iris`) | https://data.geopf.fr/wfs/ows | chargé le 19/10/2026 (rôle 3) |
| Base permanente des équipements (INSEE, BPE 2025, fichier-détail géolocalisé) | https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet | chargé le 19/10/2026 (rôle 3) |
| Emplois au lieu de travail par commune (INSEE, recensement 2022, `P22_EMPLT`) | https://www.insee.fr/fr/statistiques/fichier/8581444/base-cc-emploi-pop-active-2022_csv.zip | chargé le 19/10/2026 (rôle 3) |
| Populations de référence 2023 (INSEE), contrôle de cohérence | https://www.insee.fr/fr/statistiques/fichier/8680726/ensemble.zip | Filosofi = 93,9 % de la population légale (rôle 3) |
| Enquête nationale sur l'autopartage 2022 (6t pour l'ADEME, 165 p.) : voitures remplacées (p. 76), utilisateurs par voiture et places libérées (p. 79), km parcourus (p. 81) et évités (p. 82, p. 68) | https://www.ort.bourgogne-franche-comte.developpement-durable.gouv.fr/IMG/pdf/enquete-autopartage-2022-rapport.pdf · notice : https://librairie.ademe.fr/mobilite-et-transport/5804-enquete-autopartage-2022.html | chargé le 20/10/2026 (rôle 5), `ratios_co2` |
| Facteurs d'émission par km : voiture thermique 0,1106 kg CO₂e, électrique 0,0121, bus thermique 0,1135 / passager, tram 0,0038, vélo (Impact CO₂ de l'ADEME, données Base Empreinte) | https://impactco2.fr/api/v1/transport?km=1&displayAll=1 · méthode : https://impactco2.fr/methodologie | relevé le 20/10/2026 (rôle 5), `ratios_co2` |

## O1 et O2 — calcul local Grenoble du 20 octobre 2026

`geo/build_accessibilite.py` croise la population Filosofi 2021 livrée par le
Rôle 3 (`data/silver/carreaux_socio.parquet`) et `gold_offre.parquet` issu des
livraisons GTFS et GBFS de l'équipe, sur le réseau piéton décrit plus haut.
Les originaux de population sont archivés dans
`data/bronze/teams/population_2026-10-20/` ; les arrêts importés de Teams sont
tracés dans `data/bronze/teams/import_arrets_manifest.json` ; les flux GBFS sont
tracés dans `data/bronze/gbfs_flux.csv`. Aucun accès Teams n'est nécessaire au
recalcul sur ces fichiers locaux.

Hypothèses : population représentée au centre du carreau ; sélection communale
par ce centre ; cinq minutes à 4,5 km/h, raccords droits inclus aux deux bouts ;
seuil de raccordement population 150 m, offre 50 m ; regroupement O1 à au moins
20 habitants. O1 est une moyenne pondérée par la population des nombres d'options
accessibles ; les fréquences GTFS sont aussi exportées par mode, sans être
mélangées aux capacités GBFS. O2 inclut les populations voisines de Grenoble.
Ces choix et les SHA-256 exacts sont dans
`data/gold/accessibilite_grenoble/accessibilite_qc.json`.

L'index de présélection utilise [STRtree (Shapely)](https://shapely.readthedocs.io/en/stable/strtree.html),
puis le routage utilise Dijkstra sur le graphe pondéré en secondes. Les polygones
cartographiques ne déterminent pas la desserte. Aucun flux de stations vélo
n'est présent dans les données du pilote Grenoble ; cela ne démontre pas
l'absence de service vélo sur le terrain. Les limites du réseau OSM et du champ
Filosofi restent applicables.

## Coûts des équipements

Aucun montant n'est fourni par le sujet. Chaque valeur de `optim/couts.yaml` doit avoir une source ici, sinon elle est marquée « hypothèse ».

| Poste | Source | Équipement |
|---|---|---|
| Véhicule (35 000 €), habillage (3 000 €), création de station (6 000 €), exploitation (2 000 €/an) | Grand Périgueux, délibération DD2024-045 : https://grandperigueux.fr/fileadmin/medias/Publications/Deliberations/2024/Conseil_du_30_mai_2024/DD2024-045_PERIMOUV__SERVICE_AUTOPARTAGE_PERIVOIT.pdf | station Citiz |
| Boîtier télématique (4 000 €/véhicule) | Pôle métropolitain du Genevois français, convention CS2023-03.2 : https://genevoisfrancais.org/wp-content/uploads/CS2023_03.2-MAJ-CONVENTION-DEPLOIMENT-AUTOPARTAGE_annexe-1.pdf | station Citiz |
| Vélos et station (2 500-3 000 €/vélo), exploitation (1 500-2 500 €/vélo/an) | Manuel OBIS, synthèse EPOMM : https://epomm.eu/sites/default/files/eupdates/1210_bikesharing_fr.pdf | station vélo |
| Coût total 2 000-3 000 €/vélo/an (enquête GART), contrôle de cohérence | Banque des Territoires : https://www.banquedesterritoires.fr/velos-en-libre-service-un-bilan-coutsavantages-juge-globalement-equilibre | station vélo |
| Accessibilité PMR d'un arrêt (5 500-13 700 €) | Beyne-Heusay (BE) : https://beyne-heusay.be/sites/default/files/documents/news/Tableau-de-bord%20.pdf | arrêt de bus |
| Abri voyageurs (1 500-8 000 €, source commerciale) | HelloPro : https://conseils.hellopro.fr/quel-fabricant-d-abri-bus-choisir-1461.html | arrêt de bus |
| Borne de recharge 7,4-22 kW et installation (2 900-7 200 €, source commerciale) | HelloPro : https://conseils.hellopro.fr/combien-coute-une-borne-de-recharge-pour-vehicules-electriques-1249.html | station Citiz électrique |
| Borne 22/24 kW sur voirie, 22 000 € TTC aides déduites (borne haute) | Roannais Agglomération, délibération de Riorges DCM 2022-69 : https://www.riorges.fr/images/deliberations/DCM_2022_69_Conv_bornes_IRVE.pdf | station Citiz électrique |
| Prime ADVENIR voirie : 30 % du coût HT, plafond 1 000 à 9 000 € HT par point de recharge selon la puissance ; collectivité, borne ouverte à tout public | ADVENIR (Avere France), site officiel : https://advenir.mobi/voirie/ et https://advenir.mobi/primes-et-montants-daides/ (consulté le 20/10/2026) | station Citiz électrique |
| Indice des prix à la consommation, base 2015, hors tabac (euros constants 2025) | INSEE, série 001763852 : https://www.insee.fr/fr/statistiques/serie/001763852 | tous les coûts |
| Tarifs publics Citiz (recettes) | flux GBFS `system_pricing_plans`, ex. https://backend.citiz.fr/public/provider/5/gbfs/v3.0/system_pricing_plans.json | station Citiz |
| Taux normal de TVA (20 %) | https://www.impots.gouv.fr/professionnel/questions/quels-sont-les-taux-de-tva-en-vigueur-en-france-et-dans-lunion-europeenne | recettes HT |
| Entretien d'un arrêt (500 €/an), dimensionnement des stations, durées de vie, usage d'une station Citiz (1 location/jour, 3 h, 30 km) | **hypothèse** (voir `optim/couts.yaml`) | tous |