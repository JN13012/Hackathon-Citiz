# optim — Coûts et optimiseur (Rôle 4)

## Nettoyage (tables silver)

| Script | Sortie (`data/silver/`) | Rapport |
|---|---|---|
| `build_offre_partagee.py` | `stations_partagees` (territoire), `stations_partagees_france` (national, pour le modèle), `stations_a_venir` (« Ouverture prochaine ») | `stations_partagees_qc.txt`, `data/bronze/gbfs_flux.csv` |
| `build_projections.py` | `projections` (un département), `projections_carreaux` (un carreau 200 m) | `projections_qc.txt` |
| `build_couts.py` | `couts` (un poste), `couts_equipements` (équipement x scénario de coût bas / central / haut), `tarifs_citiz` (formules GBFS), `marge_citiz` (pessimiste / central / optimiste) | `couts_qc.txt`, `data/bronze/citiz_tarifs.json` |

- **Territoire** : nettoyage national, recoupe à la fin. Par défaut Auvergne-Rhône-Alpes, région 84 (`NIVEAU` / `CODES` dans `territoire.py`,
  contours geo.api.gouv.fr) ; `--contours data/silver/contours.parquet` pour utiliser le contour du Rôle 2.
- **Offre partagée** : 196 flux GBFS (transport.data.gouv.fr + backend.citiz.fr). Stations physiques seulement :
  les zones virtuelles des opérateurs free-floating (Dott, Voi, Pony, Bird : 61 252), Getaround (autopartage entre
  particuliers, sans stations) et les 2 stations Citiz non publiques repérées par l'EDA (« Véhicules hors-circuit »,
  « DEMOBOX (Réservé Citiz) ») sont exclus. Les stations « Ouverture prochaine » (25, toutes Citiz Grand Est) ne sont pas
  une offre existante : elles vont dans `stations_a_venir` (lieux déjà choisis par Citiz, utiles pour valider le modèle).
  Doublons `system_id` + `station_id` retirés ; capacité 0, < 0 ou > 150 -> vide. Chaque flux est relu jusqu'à 3 fois.
  Résultat ARA (19/10/2026) : 620 stations Citiz (129 communes), 901 stations vélo, 36 Clem.
- **Projections** : Omphale 2022 (central, basse, haute), 2021 -> 2030, appliqué à chaque carreau via son département.
- **Coûts** : `couts.yaml`, chaque montant sourcé (url) ou « hypothèse », avec min / max pour la sensibilité.
  Variante `station_citiz_electrique` : + borne de recharge, − prime ADVENIR (voirie : 30 % du HT, plafonnée).
- **Recettes Citiz** : formules tarifaires publiques des flux GBFS (`system_pricing_plans`) ; formule sans abonnement la
  moins chère de chaque réseau, appliquée à une location type (3 h, 30 km : hypothèse), ramenée en HT.
  Central : ≈ 24 € HT par location, 8 580 € HT/an par station (1 location/jour) contre 9 600 € de coût, marge ≈ −1 000 €/an :
  d'où la question de la péréquation.

## Optimiseur (à faire)

- `couts.yaml` : coût par type d'équipement (Citiz, bus, vélo). Chaque valeur est sourcée dans `SOURCES.md` ou marquée « hypothèse ».
- Optimiseur sous budget (PuLP, couverture maximale) : maximise la population nouvellement desservie × croissance Omphale × usage prédit.
- Contraintes : budget, exclusion de la population déjà desservie, distance minimale entre deux nouveaux équipements.
- Coefficient d'équité réglable, courbe budget → habitants desservis, estimation du CO₂ évité.
