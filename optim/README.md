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

## Optimiseur (à faire)

- `couts.yaml` : coût par type d'équipement (Citiz, bus, vélo). Chaque valeur est sourcée dans `SOURCES.md` ou marquée « hypothèse ».
- Optimiseur sous budget (PuLP, couverture maximale) : maximise la population nouvellement desservie × croissance Omphale × usage prédit.
- Contraintes : budget, exclusion de la population déjà desservie, distance minimale entre deux nouveaux équipements.
- Coefficient d'équité réglable, courbe budget → habitants desservis, estimation du CO₂ évité.
