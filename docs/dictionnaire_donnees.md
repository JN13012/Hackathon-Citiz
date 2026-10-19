# Dictionnaire de données

Conventions : colonnes en `snake_case`, géométries en Lambert 93 (EPSG:2154), valeur manquante = cellule vide (jamais 0). Toute valeur manquante est notée dans la colonne « Manquants ».

## Tables silver (livrées mardi 10 h)

| Table | Rôle | Une ligne = | Colonnes | Manquants |
|---|---|---|---|---|
| `arrets` | 1 | un arrêt physique unique | `arret_id`, `nom`, `mode`, `nb_passages_jour`, `reseau`, `geometry` | à remplir |
| `reseau_pieton` | 2 | nœuds et tronçons | nœuds : `noeud_id`, `geometry` · tronçons : `de`, `vers`, `longueur_m` | à remplir |
| `contours` | 2 | une commune, un EPCI ou un IRIS | `code_insee`, `nom`, `niveau`, `geometry` | à remplir |
| `carreaux_socio` | 3 | un carreau de 200 m | `id_carreau`, `population`, `menages`, `revenu`, `part_menages_sans_voiture`, `nb_equipements`, `emplois`, `donnee_masquee`, `geometry` (+ colonnes complémentaires, détail ci-dessous) | `emplois` : 381 (communes fusionnées depuis 2021) · `code_iris` : 483 · autres : 0 |
| `equipements` | 3 | un équipement BPE 2025 géolocalisé | `code_commune`, `typequ`, `domaine`, `geometry` | 0 |
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

## Détail · `carreaux_socio` (rôle 3)

312 143 carreaux habités, 7 703 556 habitants (Auvergne-Rhône-Alpes). Clé : `id_carreau`.

| Colonne | Type | Manquants | Description |
|---|---|---|---|
| `id_carreau` | texte | 0 | Identifiant INSEE du carreau de 200 m (Filosofi 2021) |
| `code_commune` | texte | 0 | Code INSEE de la commune (la première du territoire si le carreau en touche plusieurs) |
| `code_iris` | texte | 483 | IRIS contenant le centre du carreau (vide : centre hors des IRIS du territoire) |
| `multi_communes` | booléen | 0 | Vrai si le carreau touche plusieurs communes |
| `population` | nombre | 0 | Habitants, ménages fiscaux (Filosofi 2021) |
| `menages` | nombre | 0 | Ménages fiscaux (Filosofi 2021) |
| `revenu` | nombre | 0 | Niveau de vie moyen par habitant, €/an (`ind_snv / ind`) |
| `part_moins_18` | nombre (0-1) | 0 | Part des moins de 18 ans (Filosofi 2021) |
| `part_18_39` | nombre (0-1) | 0 | Part des 18-39 ans (Filosofi 2021) |
| `part_40_64` | nombre (0-1) | 0 | Part des 40-64 ans (Filosofi 2021) |
| `part_65_plus` | nombre (0-1) | 0 | Part des 65 ans et plus (Filosofi 2021) |
| `part_menages_sans_voiture` | nombre (0-1) | 0 | Part des résidences principales sans voiture (recensement 2022, IRIS, sinon commune) |
| `source_part_voiture` | texte | 0 | Niveau utilisé : `iris` (99,7 % des carreaux) ou `commune` |
| `nb_equipements` | entier | 0 | Équipements BPE 2025 : services + commerces + enseignement + santé (0 = vrai zéro) |
| `nb_equipements_tous` | entier | 0 | Tous les équipements BPE 2025 du carreau (0 = vrai zéro) |
| `nb_equip_services`, `nb_equip_commerces`, `nb_equip_enseignement`, `nb_equip_sante`, `nb_equip_transports`, `nb_equip_sports_culture`, `nb_equip_tourisme` | entier | 0 | Équipements BPE 2025 par domaine (0 = vrai zéro) |
| `emplois` | nombre | 381 | HYPOTHÈSE : emplois de la commune (recensement 2022) répartis au prorata des équipements, sinon de la population. Vide : commune fusionnée depuis 2021 |
| `donnee_masquee` | booléen | 0 | Vrai si l'INSEE a estimé les valeurs (carreau de moins de 11 ménages, secret statistique) |
| `faible_population` | booléen | 0 | Vrai si moins de 20 habitants : à regrouper (O1, vie privée) |
| `x_centre`, `y_centre` | nombre | 0 | Centre du carreau, Lambert 93 (m) |
| `lon`, `lat` | nombre | 0 | Centre du carreau, WGS84 (pour Power BI) |
| `geometry` | géométrie | 0 | Carreau de 200 m, Lambert 93 (EPSG:2154) |

**Contrôles réalisés**
- Identifiants uniques, tous les carreaux dans le territoire, projection Lambert 93.
- Population = 94,2 % de la population légale 2023 : Filosofi ne compte pas les personnes vivant en communauté.
- 79,8 % des carreaux, soit 22,3 % des habitants, ont des valeurs estimées par l'INSEE.
- 32 lignes Filosofi écartées car leur code commune était illisible.

**Hypothèses**
1. Un carreau sur plusieurs communes est rattaché à la première commune du territoire.
2. Un carreau prend la part sans voiture de l'IRIS qui contient son centre, sinon celle de sa commune.
3. Les emplois d'une commune sont répartis entre ses carreaux au prorata des équipements (sinon de la population).
4. Le recensement 2022 est utilisé : le format 2023 publié le 19/10/2026 n'était pas lisible automatiquement.

## Règles de qualité

- Aucun doublon d'arrêt de même nom à moins de 50 m.
- Tous les points sont dans le territoire.
- Chaque coût et chaque ratio a une source ou la mention « hypothèse ».
- Somme de la population des carreaux proche de la population INSEE du territoire.