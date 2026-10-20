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
| Limites administratives et IRIS (IGN, ADMIN EXPRESS) | https://geoservices.ign.fr/adminexpress | contours |
| Réseau piéton et routier (OpenStreetMap, Geofabrik) | https://download.geofabrik.de/europe/france.html | isochrones de 5 min |

Formats : https://gtfs.org/ · https://gbfs.org/

## GTFS du territoire (Auvergne-Rhône-Alpes)

Réseaux téléchargés par `scripts/build_arrets.py` (Rôle 1), 19 jeux de données :

| Réseau | Clé | Jeu de données |
|---|---|---|
| TCL (Lyon) | `tcl_lyon` | https://www.data.gouv.fr/datasets/horaires-theoriques-du-reseau-transports-en-commun-lyonnais |
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
| Populations de référence 2023 (INSEE), contrôle de cohérence | https://www.insee.fr/fr/statistiques/fichier/8680726/ensemble.zip | Filosofi = 94,2 % de la population légale (rôle 3) |
| Enquête nationale sur l'autopartage (ADEME, édition la plus récente) | https://www.ademe.fr | à vérifier |
| Facteur d'émission d'une voiture par km (ADEME, Base Empreinte) | https://base-empreinte.ademe.fr | à relever |

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
| Prime ADVENIR voirie : 30 % du HT, plafond 1 000-1 900 € HT par point (2025) | TotalEnergies : https://www.totalenergies.fr/particuliers/recharge-voiture-electrique/la-prime-advenir ; barème 2026 : https://www.isiohm.fr/prime-advenir ; **à confirmer sur https://advenir.mobi** | station Citiz électrique |
| Tarifs publics Citiz (recettes) | flux GBFS `system_pricing_plans`, ex. https://backend.citiz.fr/public/provider/5/gbfs/v3.0/system_pricing_plans.json | station Citiz |
| Taux normal de TVA (20 %) | https://www.impots.gouv.fr/professionnel/questions/quels-sont-les-taux-de-tva-en-vigueur-en-france-et-dans-lunion-europeenne | recettes HT |
| Entretien d'un arrêt (500 €/an), dimensionnement des stations, durées de vie, usage d'une station Citiz (1 location/jour, 3 h, 30 km) | **hypothèse** (voir `optim/couts.yaml`) | tous |