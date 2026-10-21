# optim — Coûts et optimiseur (Rôle 4)

## Nettoyage (tables silver)

| Script | Sortie (`data/silver/`) | Rapport |
|---|---|---|
| `build_offre_partagee.py` | `stations_partagees` (territoire), `stations_partagees_france` (Citiz de France, pour le modèle), `stations_a_venir` (« Ouverture prochaine ») | `stations_partagees_qc.txt`, `data/bronze/gbfs_flux.csv` |
| `build_projections.py` | `projections` (un département), `projections_carreaux` (un carreau 200 m) | `projections_qc.txt` |
| `build_couts.py` | `couts` (un poste), `couts_equipements` (équipement x scénario de coût bas / central / haut), `tarifs_citiz` (formules GBFS), `marge_citiz` (pessimiste / central / optimiste) | `couts_qc.txt`, `data/bronze/citiz_tarifs.json` |

- **Territoire** : par défaut Auvergne-Rhône-Alpes, région 84 (`NIVEAU` / `CODES` dans `territoire.py`, contours
  geo.api.gouv.fr) ; `--contours` pour le contour du Rôle 2 : `contours.parquet` (lignes `niveau` = commune, EPCI
  retrouvé par les lignes epci) ou `territoire.parquet` (polygone seul : communes geo.api dont l'intérieur y tombe).
  Testé sur le pilote Grenoble-Alpes Métropole (49 communes) avec les deux formats.
  Seul Citiz est lu sur toute la France (entraînement du modèle) ; les autres jeux GBFS ne sont téléchargés que si leur
  zone (`covered_area` : commune, EPCI, département, région) touche le territoire, ou s'ils sont nationaux ; recoupe
  spatiale finale. Filosofi est lu par blocs et filtré sur les départements du territoire.
- **Temps** (19-20/10/2026) : offre partagée 73 s -> 13 s (37 jeux GBFS sur 155), projections 17 s -> 10 s, coûts 16 s -> 1 s.
  Le relevé GBFS et les tarifs Citiz du jour sont réutilisés (`data/bronze/gbfs_cache.json`, `citiz_tarifs.json`) :
  `--refresh` pour les relire. Délai de 15 s par requête ; seules les réponses invalides sont relues (3 fois).
- **Offre partagée** : flux GBFS de transport.data.gouv.fr + backend.citiz.fr (18 réseaux Citiz). Stations physiques seulement :
  les zones virtuelles des opérateurs free-floating (Dott, Voi, Pony, Bird : 61 252), Getaround (autopartage entre
  particuliers, sans stations) et les 2 stations Citiz non publiques repérées par l'EDA (« Véhicules hors-circuit »,
  « DEMOBOX (Réservé Citiz) ») sont exclus. Les stations « Ouverture prochaine » (25, toutes Citiz Grand Est) ne sont pas
  une offre existante : elles vont dans `stations_a_venir` (lieux déjà choisis par Citiz, utiles pour valider le modèle).
  Doublons `system_id` + `station_id` retirés ; capacité 0, < 0 ou > 150 -> vide.
  Résultat ARA (19/10/2026) : 620 stations Citiz (129 communes), 901 stations vélo, 36 Clem.
- **Projections** : Omphale 2022 (central, basse, haute), 2021 -> 2030, appliqué à chaque carreau via son département.
  Carreaux retenus avec la même règle que `carreaux_socio` (Rôle 3) : une commune de `lcog_geo` dans le territoire,
  rattachement à la première ; filtre par département pour une région (Filosofi est au COG 2021). Jointure à 100 %.
- **Coûts** : `couts.yaml`, chaque montant sourcé (url) ou « hypothèse », avec min / max pour la sensibilité.
  Variante `station_citiz_electrique` : + borne de recharge, − prime ADVENIR voirie (advenir.mobi : 30 % du coût HT,
  plafond 1 000 à 9 000 € HT par point), calculée sur la borne de chaque scénario. Éligibilité : borne ouverte à tout
  public, installée par une collectivité ; une borne réservée à Citiz pourrait ne pas l'être.
- **Euros constants 2025** : chaque montant est converti depuis l'année de sa source avec l'indice des prix INSEE
  (série 001763852, dans `couts.yaml`) ; `montant_courant` garde la valeur d'origine.
- **Recettes Citiz** : formules tarifaires publiques des flux GBFS (`system_pricing_plans`) ; formule sans abonnement la
  moins chère de chaque réseau, appliquée à une location type (3 h, 30 km : hypothèse), ramenée en HT.
  Central : ≈ 24 € HT par location, 8 580 € HT/an par station (1 location/jour) contre 9 700 € de coût,
  marge ≈ −1 100 €/an : d'où la question de la péréquation.

## Optimiseur sous budget (`optimise.py`)

Question : avec un budget annuel, quels emplacements et quels équipements (Citiz, bus, vélo) desservent le plus
d'habitants qui n'ont pas encore cette offre à 5 min à pied ?

- **Entrées** : `gold_candidates`, `projections_carreaux` (population 2030), `couts_equipements`, `ratios_co2`,
  `gold_offre` ; et, s'ils sont présents, `gold_couverture` et les paires population-offre du Rôle 2 (réseau
  piéton réel) et `gold_scores` du Rôle 3 (usage prédit). Sans les fichiers du Rôle 2 : approximation à
  308 m à vol d'oiseau (400 m / 1,3, comme l'EDA), signalée dans la colonne `methode_couverture`.
- **Méthode** : couverture maximale sous budget, algorithme glouton paresseux (habitants nouvellement desservis
  par euro), un habitant compté une fois par type, distance minimale entre deux nouveaux équipements du même
  type (Citiz 500 m, d'après l'EDA ; bus et vélo 300 m). Un arrêt de bus n'est proposé qu'à moins de 1 km d'un
  arrêt existant (coût « sur une ligne existante », hypothèse).
- **Réglages** : `--budget` (€ HT 2025/an), `--scenario bas|central|haut`, `--horizon 2021|2030`,
  `--equite 0.5` (+50 % de poids dans les communes < 10 000 hab.), `--repartition citiz=0.5,bus=0.25,velo=0.25`.
- **Sorties** (`data/gold/`) : `gold_liste_priorisee` (.parquet, .csv, .geojson pour Power BI),
  `gold_courbe_budget` (3 scénarios de coût, avec et sans équité), `optimiseur_qc.txt`. Tests : `tests/test_optimise.py`.
- **Résultat (21/10, approximation 308 m, 500 000 €/an, central)** : libre -> 17 Citiz + 277 arrêts de bus,
  349 000 habitants gagnés, 22,8 t CO₂e/an ; avec `--repartition citiz=0.5,bus=0.25,velo=0.25 --equite 0.5` ->
  25 Citiz + 105 bus + 4 vélo, 284 000 habitants, 33,5 t CO₂e/an. Les stations Citiz retenues restent
  déficitaires au scénario central (≈ -1 100 €/an chacune) : péréquation à discuter.

## Optimiseur : suite

- `couts.yaml` : coût par type d'équipement (Citiz, bus, vélo). Chaque valeur est sourcée dans `SOURCES.md` ou marquée « hypothèse ».
- Optimiseur sous budget (PuLP, couverture maximale) : maximise la population nouvellement desservie × croissance Omphale × usage prédit.
- Contraintes : budget, exclusion de la population déjà desservie, distance minimale entre deux nouveaux équipements.
- Coefficient d'équité réglable, courbe budget → habitants desservis, estimation du CO₂ évité.
