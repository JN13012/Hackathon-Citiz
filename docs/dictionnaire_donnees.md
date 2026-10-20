# Dictionnaire de données

Conventions : colonnes en `snake_case`, géométries en Lambert 93 (EPSG:2154), valeur manquante = cellule vide (jamais 0). Toute valeur manquante est notée dans la colonne « Manquants ».

## Tables silver (livrées mardi 10 h)

| Table | Rôle | Une ligne = | Colonnes | Manquants |
|---|---|---|---|---|
| `arrets` | 1 | un arrêt physique unique | `arret_id`, `nom`, `mode`, `nb_passages_jour`, `reseau`, `geometry` | à remplir |
| `reseau_pieton` | 2 | nœuds et tronçons | nœuds : `noeud_id`, `geometry` · tronçons : `de`, `vers`, `longueur_m` | à remplir |
| `contours` | 2 | une commune, un EPCI ou un IRIS | `code_insee`, `nom`, `niveau`, `geometry` | à remplir |
| `carreaux_socio` | 3 | un carreau de 200 m | `id_carreau`, `population`, `menages`, `revenu`, `part_menages_sans_voiture`, `nb_equipements`, `emplois`, `donnee_masquee`, `geometry` | à remplir |
| `stations_partagees` | 4 | une station physique (Citiz, autopartage, vélo) du territoire | `station_id` (`<system_id>:<id GBFS>`, unique en France), `station_id_gbfs`, `operateur`, `type` (`citiz`, `autopartage`, `velo`), `capacite`, `nb_vehicules`, `commune` (code INSEE), `geometry` ; en plus : `nom_commune`, `nom`, `reseau`, `system_id`, `nb_places_libres`, `en_service`, `station_virtuelle`, `type_stationnement` (GBFS `parking_type`), `electrique_nom` (« électrique » dans le nom : minimum), `date_extraction` | `capacite` vide si absente, 0, < 0 ou > 150 (5 en ARA, dont 3 Citiz à 0) ; `station_virtuelle` vide si le flux ne la donne pas (501) ; `type_stationnement` donné par Citiz seulement (937 vides) ; `nb_vehicules` = relevé instantané |
| `stations_partagees_france` | 4 | une station Citiz ouverte, toute la France (entraînement du modèle) | mêmes colonnes sans `commune`, `nom_commune` | `capacite` : 8 |
| `projections` | 4 | un département du territoire | `dep`, `nom_dep`, `annee_depart` (2021), `pop_depart`, `pop_2030`, `taux_croissance` (central), `taux_croissance_basse`, `taux_croissance_haute` | aucun ; populations arrondies au millier par l'INSEE |
| `projections_carreaux` | 4 | un carreau de 200 m du territoire | `id_carreau` (= `idcar_200m` Filosofi), `commune`, `dep`, `population_2021`, `population_2030`, `population_2030_basse`, `population_2030_haute`, `geometry` (centre du carreau) | aucun ; `commune` = première commune si le carreau en chevauche plusieurs |
| `couts` | 4 | un poste de coût | `type_equipement`, `poste`, `montant`, `unite`, `source` ; en plus : `statut` (`source` / `hypothèse`), `min`, `max`, `nature`, `quantite`, `duree_vie_ans`, `inclus_dans`, `option` (ex. `electrique`), `aide`, `taux`, `base`, `plafond` (aide proportionnelle), `annee`, `coef_euros_constants`, `montant_courant` (avant conversion ; vide pour une aide calculée), `url`, `note` — montants en € HT constants 2025 | `montant` vide si `inclus_dans` est renseigné (2 postes) |
| `couts_equipements` | 4 | un équipement (ou variante, ex. `station_citiz_electrique`) x un scénario de coût (bas, central, haut) | `type_equipement`, `scenario`, `cout_investissement` (net des aides), `aides` (négatif), `cout_exploitation_annuel`, `cout_annualise` | aucun |
| `stations_a_venir` | 4 | une station annoncée « Ouverture prochaine » (France) | mêmes colonnes que `stations_partagees_france` | idem ; aucune en ARA au 19/10/2026 |
| `tarifs_citiz` | 4 | une formule tarifaire Citiz (GBFS `system_pricing_plans`) | `system_id`, `plan_id`, `nom`, `prix_fixe`, `taxable`, `prix_location_type` (TTC, location type de `couts.yaml`), `reference` (formule retenue par réseau) | aucun |
| `marge_citiz` | 4 | une station Citiz (ou électrique) x un scénario (pessimiste, central, optimiste) | `type_equipement`, `scenario`, `recette_annuelle`, `cout_annualise`, `marge_annuelle` (€ HT/an) | aucun ; usage = hypothèse |
| `ratios_co2` | 5 | un ratio | `valeur`, `unite`, `source`, `page` | à remplir |

## Tables gold

| Table | Rôle | Contenu | Échéance |
|---|---|---|---|
| `gold_offre` | 1 | un point par arrêt ou station : type (bus, tram, vélo, trottinette, Citiz), fréquence ou capacité | mardi 10 h 30 |
| `gold_candidates` | 1 | centroïdes des carreaux peuplés, avec `id_carreau` | mardi 11 h |
| `gold_isochrones` | 2 | polygone de 5 min à pied par carreau | mardi 11 h |
| `gold_o1` | 2 | options accessibles par habitant, par carreau (carreaux peu peuplés regroupés) | mardi 12 h 30 |
| `gold_o2` | 2 | population à 5 min de chaque arrêt ou station | mardi 12 h 30 |
| `gold_couverture` | 2 | pour chaque candidat, les carreaux à moins de 5 min | mardi 14 h |
| `gold_features` | 3 | variables socio-économiques et d'intermodalité par isochrone | mardi 12 h |
| `gold_scores` | 3 | usage potentiel prédit par candidat et par type | mardi 16 h 30 |

## Règles de qualité

- Aucun doublon d'arrêt de même nom à moins de 50 m.
- Tous les points sont dans le territoire.
- Chaque coût et chaque ratio a une source ou la mention « hypothèse ».
- Somme de la population des carreaux proche de la population INSEE du territoire.
