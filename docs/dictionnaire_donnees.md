# Dictionnaire de données

Conventions : colonnes en `snake_case`, géométries en Lambert 93 (EPSG:2154), valeur manquante = cellule vide (jamais 0). Toute valeur manquante est notée dans la colonne « Manquants ».

## Tables silver (livrées mardi 10 h)

| Table | Rôle | Une ligne = | Colonnes | Manquants |
|---|---|---|---|---|
| `arrets` | 1 | un arrêt physique unique | `arret_id`, `nom`, `mode`, `nb_passages_jour`, `reseau`, `geometry` | à remplir |
| `reseau_pieton` | 2 | nœuds et tronçons | nœuds : `noeud_id`, `geometry` · tronçons : `de`, `vers`, `longueur_m` | à remplir |
| `contours` | 2 | une commune, un EPCI ou un IRIS | `code_insee`, `nom`, `niveau`, `geometry` | à remplir |
| `carreaux_socio` | 3 | un carreau de 200 m | `id_carreau`, `population`, `menages`, `revenu`, `part_menages_sans_voiture`, `nb_equipements`, `emplois`, `donnee_masquee`, `geometry` | à remplir |
| `stations_partagees` | 4 | une station | `station_id`, `operateur`, `type`, `capacite`, `nb_vehicules`, `commune`, `geometry` | à remplir |
| `projections` | 4 | un département | population de départ, population 2030, taux de croissance | à remplir |
| `couts` | 4 | un poste de coût | `type_equipement`, `poste`, `montant`, `unite`, `source` | à remplir |
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
