# Dictionnaire de données

Conventions : colonnes en `snake_case`, géométries en Lambert 93 (EPSG:2154), valeur manquante = cellule vide (jamais 0). Toute valeur manquante est notée dans la colonne « Manquants ».

## Tables silver (livrées mardi 10 h)

| Table | Rôle | Une ligne = | Colonnes | Manquants |
|---|---|---|---|---|
| `arrets` | 1 | un arrêt physique unique | `arret_id`, `nom`, `mode`, `nb_passages_jour`, `reseau`, `geometry` | à remplir |
| `reseau_pieton` | 2 | nœuds et tronçons orientés, pilote Grenoble + marge 1 km | nœuds : `noeud_id`, `composante_id`, `dans_territoire`, `dans_zone_marge`, `geometry` · tronçons : `de`, `vers`, `cle`, `longueur_m`, `temps_pieton_s`, tags `*_json`, `geometry` | tags OSM absents = texte JSON `null` ; voir rapport QC |
| `contours` | 2 | une commune, un EPCI ou un IRIS | `code_insee`, `nom`, `niveau`, `geometry` | à remplir |
| `carreaux_socio` | 3 | un carreau de 200 m | `id_carreau`, `population`, `menages`, `revenu`, `part_menages_sans_voiture`, `nb_equipements`, `emplois`, `donnee_masquee`, `geometry` | à remplir |
| `stations_partagees` | 4 | une station physique (Citiz, autopartage, vélo) du territoire | `station_id` (`<system_id>:<id GBFS>`, unique en France), `station_id_gbfs`, `operateur`, `type` (`citiz`, `autopartage`, `velo`), `capacite`, `nb_vehicules`, `commune` (code INSEE), `geometry` ; en plus : `nom_commune`, `nom`, `reseau`, `system_id`, `nb_places_libres`, `en_service`, `station_virtuelle`, `type_stationnement` (GBFS `parking_type`), `electrique_nom` (« électrique » dans le nom : minimum), `date_extraction` | `capacite` vide si absente, 0, < 0 ou > 150 (5 en ARA, dont 3 Citiz à 0) ; `station_virtuelle` vide si le flux ne la donne pas (501) ; `type_stationnement` donné par Citiz seulement (937 vides) ; `nb_vehicules` = relevé instantané |
| `stations_partagees_france` | 4 | idem, toute la France (entraînement du modèle) | mêmes colonnes sans `commune`, `nom_commune` | `capacite` : 264 |
| `projections` | 4 | un département du territoire | `dep`, `nom_dep`, `annee_depart` (2021), `pop_depart`, `pop_2030`, `taux_croissance` (central), `taux_croissance_basse`, `taux_croissance_haute` | aucun ; populations arrondies au millier par l'INSEE |
| `projections_carreaux` | 4 | un carreau de 200 m du territoire | `id_carreau` (= `idcar_200m` Filosofi), `commune`, `dep`, `population_2021`, `population_2030`, `population_2030_basse`, `population_2030_haute`, `geometry` (centre du carreau) | aucun ; `commune` = première commune si le carreau en chevauche plusieurs |
| `couts` | 4 | un poste de coût | `type_equipement`, `poste`, `montant`, `unite`, `source` ; en plus : `statut` (`source` / `hypothèse`), `min`, `max`, `nature`, `quantite`, `duree_vie_ans`, `inclus_dans`, `option` (ex. `electrique`), `aide`, `annee`, `url`, `note` | `montant` vide si `inclus_dans` est renseigné (2 postes) |
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
| `gold_isochrones` | 2 | cible : polygone de 5 min à pied par carreau (`id_carreau`) ; pilote actuel : origines de type `arret` dans Grenoble, dossier dédié `isochrones_grenoble_arrets` ; tampon cartographique de chemins routés | mardi 11 h |
| `gold_o1` | 2 | pilote Grenoble : options accessibles par habitant, par groupe de carreaux (minimum 20 habitants), dossier `accessibilite_grenoble` | mardi 12 h 30 |
| `gold_o2` | 2 | pilote Grenoble : population à 5 min de chacun des 303 arrêts/stations, voisins inclus, dossier `accessibilite_grenoble` | mardi 12 h 30 |
| `gold_couverture` | 2 | pour chaque candidat, les carreaux à moins de 5 min | mardi 14 h |
| `gold_features` | 3 | variables socio-économiques et d'intermodalité par isochrone | mardi 12 h |
| `gold_scores` | 3 | usage potentiel prédit par candidat et par type | mardi 16 h 30 |

## Règles de qualité

Le pilote des isochrones conserve `origine_id`, `type_origine` et l'identifiant
source (`arret_id` ou `id_carreau`), avec `duree_s`, `vitesse_kmh`,
`distance_raccordement_m`, `temps_acces_s`, `tampon_m`, `surface_m2` et `geometry`.
Les origines sans calcul ont un statut dans `origines_qc.parquet` et ne produisent
pas de polygone. `chemins_accessibles.parquet` conserve les portions routées ;
les surfaces tamponnées servent à la cartographie, pas à prouver un accès au bâti.

- Aucun doublon d'arrêt de même nom à moins de 50 m.
- Tous les points sont dans le territoire.
- Chaque coût et chaque ratio a une source ou la mention « hypothèse ».
- Somme de la population des carreaux proche de la population INSEE du territoire.


## Détail O1/O2 — pilote Grenoble

Dossier : `data/gold/accessibilite_grenoble/`. Formats GeoParquet (EPSG:2154),
CSV sans géométrie et GeoJSON (EPSG:4326). Les codes et identifiants sont du texte.

| Table / colonnes | Définition | Manquants |
|---|---|---|
| `gold_o1.groupe_id`, `nb_carreaux`, `geometry` | Groupe stable, nombre de carreaux et union de leurs géométries complètes | aucun pour ce pilote |
| `gold_o1.population` | Somme conservée des populations Filosofi des carreaux regroupés, centres dans Grenoble | vide si tout le groupe reste sous le seuil de publication |
| `gold_o1.options_par_habitant` | Moyenne des nombres d'options accessibles par centre, pondérée par la population ; ex. 100 habitants et 5 options → 5 | vide si calcul incomplet ou seuil non atteint |
| `gold_o1.options_<mode>_par_habitant` | Même moyenne, pour un mode (`bus`, `tram`, `cable_car`, `citiz` dans le pilote) | idem ; aucun champ vélo ne signifie pas qu'aucun vélo n'existe sur le terrain |
| `gold_o1.passages_jour_<mode>_par_habitant` | Somme des passages/jour des arrêts accessibles par centre, puis moyenne pondérée par la population ; compte les passages à des arrêts distincts | vide si une fréquence concernée manque ou si calcul incomplet |
| `gold_o1.capacite_<mode>_par_habitant` | Capacité cumulée des stations accessibles par centre, puis moyenne pondérée ; séparée des fréquences | vide si une capacité concernée manque ou si calcul incomplet |
| `gold_o1.population_evaluee`, `statut` | Population avec calcul complet ; `ok`, `partiel` ou `population_insuffisante` | population évaluée masquée si groupe sous le seuil |
| `gold_o2.offre_id`, `source`, `type`, `nom`, `operateur`, `valeur`, `unite`, `geometry` | Point d'offre source dans Grenoble ; valeur = passages/jour pour un arrêt, capacité pour une station ; `nom` conservé du gold fourni (code commune pour certaines stations) | valeurs manquantes conservées |
| `gold_o2.population_desservie` | Somme des populations de centres accessibles en 300 s, voisins inclus ; jamais arrondie à l'entier dans la table | vide si raccordement impossible ou population potentiellement accessible non calculée |
| `gold_o2.population_desservie_dans_pilote` | Sous-ensemble dont les centres sont dans Grenoble | idem |
| `gold_o2.nb_carreaux_desservis`, `statut` | Nombre distinct de centres accessibles ; `ok`, `partiel` ou `non_calcule` | nombre vide si calcul incomplet |
| `*_min` | Borne inférieure fondée sur les seules paires accessibles prouvées ; égale à la valeur complète quand le calcul est complet | vide si indicateur non calculable, grandeur source manquante, ou population sous le seuil |
| `groupes_o1` | Jointure technique `id_carreau`, `groupe_id` ; pas de mesure O1 individuelle sur les petits carreaux | aucun |
| `accessibilite_carreaux_offres` | `id_carreau`, `offre_id`, `temps_pieton_s` ; une paire unique accessible, temps incluant les deux raccords | les paires indéterminées sont dans une autre table ; hors budget = absence de paire |
| `raccordements_population`, `raccordements_offre` | Identifiant, `dans_pilote`, `statut`, `distance_raccordement_m`, `temps_acces_s`, `raccord_de`, `raccord_vers`, `raccord_cle`, `abscisse_raccord_m`, point source | statut explicite en cas d'échec ; aucune exclusion muette |

Les fréquences et capacités décrivent des unités différentes et ne forment pas
un score additionné. La population Filosofi des ménages fiscaux n'est pas la
population communale exhaustive. Les carreaux frontaliers sont attribués par
leur centre, sans correction de leur population. Les populations O2 se
recouvrent entre stations : ne pas les additionner pour une population unique.
La somme des populations O1 doit être égale à celle des carreaux sélectionnés,
pas forcée à une population administrative de référence.
