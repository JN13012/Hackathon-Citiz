# Dictionnaire de données

Conventions : colonnes en `snake_case`, géométries en Lambert 93 (EPSG:2154), valeur manquante = cellule vide (jamais 0). Toute valeur manquante est notée dans la colonne « Manquants ».

## Tables silver (livrées mardi 10 h)

| Table | Rôle | Une ligne = | Colonnes | Manquants |
|---|---|---|---|---|
| `arrets` | 1 | un arrêt physique unique | `arret_id`, `nom`, `mode`, `nb_passages_jour`, `reseau`, `geometry` | aucune valeur manquante ; `nb_passages_jour` = 0 pour 6,8 % des arrêts (pas de service le jour de référence) ; mode « autre » pour 198 arrêts |
| `reseau_pieton` | 2 | un nœud ou un arc orienté ; pilote Grenoble et ARA en 835 tuiles avec marge | nœuds : `noeud_id`, `composante_id` (locale à la tuile), `dans_territoire` (pilote) ou `dans_tuile` (ARA), `dans_zone_marge`, `geometry` · tronçons : `de`, `vers`, `cle`, `longueur_m`, `temps_pieton_s`, tags `*_json`, `geometry` ; ARA ajoute `osmid`, `segment_osm`, `direction_osm` (1 et -1 présents pour chaque segment) | tags OSM absents = texte JSON `null` ; 835 tuiles contrôlées dans `data/silver/reseau_ara_bidirectionnel/` ; les marges contiennent des copies à dédupliquer pour les effectifs régionaux |
| `contours` | 2 | une commune, un EPCI ou un IRIS | `code_insee`, `nom`, `niveau`, `geometry` | à remplir |
| `carreaux_socio` | 3 | un carreau de 200 m | `id_carreau`, `population`, `menages`, `revenu`, `part_menages_sans_voiture`, `nb_equipements`, `emplois`, `donnee_masquee`, `geometry` (+ colonnes complémentaires, détail ci-dessous) | `code_iris` : 483 · autres : 0 |
| `equipements` | 3 | un équipement BPE 2025 géolocalisé | `code_commune`, `typequ`, `domaine`, `geometry` | 0 |
| `stations_partagees` | 4 | une station physique (Citiz, autopartage, vélo) du territoire | `station_id` (`<system_id>:<id GBFS>`, unique en France), `station_id_gbfs`, `operateur`, `type` (`citiz`, `autopartage`, `velo`), `capacite`, `nb_vehicules`, `commune` (code INSEE), `geometry` ; en plus : `nom_commune`, `nom`, `reseau`, `system_id`, `nb_places_libres`, `en_service`, `station_virtuelle`, `type_stationnement` (GBFS `parking_type`), `electrique_nom` (« électrique » dans le nom : minimum), `date_extraction` | `capacite` vide si absente, 0, < 0 ou > 150 (5 en ARA, dont 3 Citiz à 0) ; `station_virtuelle` vide si le flux ne la donne pas (501) ; `type_stationnement` donné par Citiz seulement (937 vides) ; `nb_vehicules` = relevé instantané |
| `stations_partagees_france` | 4 | une station Citiz ouverte, toute la France (entraînement du modèle) | mêmes colonnes sans `commune`, `nom_commune` | `capacite` : 8 |
| `projections` | 4 | un département du territoire | `dep`, `nom_dep`, `annee_depart` (2021), `pop_depart`, `pop_2030`, `taux_croissance` (central), `taux_croissance_basse`, `taux_croissance_haute` | aucun ; populations arrondies au millier par l'INSEE |
| `projections_carreaux` | 4 | un carreau de 200 m du territoire (312 143 en ARA, identiques à `carreaux_socio`) | `id_carreau` (= `idcar_200m` Filosofi), `commune` (code Filosofi 2021 : première commune du territoire si le carreau en touche plusieurs, règle du Rôle 3), `dep`, `multi_communes`, `population_2021`, `population_2030`, `population_2030_basse`, `population_2030_haute`, `geometry` (centre du carreau) | aucun |
| `couts` | 4 | un poste de coût | `type_equipement`, `poste`, `montant`, `unite`, `source` ; en plus : `statut` (`source` / `hypothèse`), `min`, `max`, `nature`, `quantite`, `duree_vie_ans`, `inclus_dans`, `option` (ex. `electrique`), `aide`, `taux`, `base`, `plafond` (aide proportionnelle), `annee`, `coef_euros_constants`, `montant_courant` (avant conversion ; vide pour une aide calculée), `url`, `note` — montants en € HT constants 2025 | `montant` vide si `inclus_dans` est renseigné (2 postes) |
| `couts_equipements` | 4 | un équipement (ou variante, ex. `station_citiz_electrique`) x un scénario de coût (bas, central, haut) | `type_equipement`, `scenario`, `cout_investissement` (net des aides), `aides` (négatif), `cout_exploitation_annuel`, `cout_annualise` | aucun |
| `stations_a_venir` | 4 | une station annoncée « Ouverture prochaine » (France) | mêmes colonnes que `stations_partagees_france` | idem ; aucune en ARA au 19/10/2026 |
| `tarifs_citiz` | 4 | une formule tarifaire Citiz (GBFS `system_pricing_plans`) | `system_id`, `plan_id`, `nom`, `prix_fixe`, `taxable`, `prix_location_type` (TTC, location type de `couts.yaml`), `reference` (formule retenue par réseau) | aucun |
| `marge_citiz` | 4 | une station Citiz (ou électrique) x un scénario (pessimiste, central, optimiste) | `type_equipement`, `scenario`, `recette_annuelle`, `cout_annualise`, `marge_annuelle` (€ HT/an) | aucun ; usage = hypothèse |
| `ratios_co2` | 5 | un ratio | `ratio`, `valeur`, `unite`, `source`, `page` ; en plus : `min`, `max` (fourchette de la source), `titre_source`, `url`, `note` | `min`/`max` vides quand la source ne donne qu'une valeur ; `facteur_velo_mecanique` = 0 (vrai zéro selon la source) |

## Tables gold

| Table | Rôle | Contenu | Échéance |
|---|---|---|---|
| `gold_offre` | 1 | un point par arrêt ou station : `offre_id`, `source` (`arrets` ou `stations_partagees`), `type` (bus, tram, métro, vélo, Citiz, autopartage…), `nom`, `operateur`, `valeur` (passages/jour pour un arrêt, capacité pour une station), `unite`, `geometry`, `code_commune` (code INSEE des stations partagées, vide pour les arrêts sans ce champ) ; `nom` conserve le libellé source ou un identifiant de secours ; `valeur` vide pour 5 stations sans capacité | mardi 10 h 30 |
| `gold_candidates` | 1 | centroïdes des carreaux peuplés (312 143), avec `id_carreau`, `population`, `code_commune`, `faible_population`, `part_menages_sans_voiture` | mardi 11 h |
| `gold_isochrones` | 2 | cible : polygone de 5 min à pied par carreau (`id_carreau`) ; pilote actuel : origines de type `arret` dans Grenoble, dossier dédié `isochrones_grenoble_arrets` ; tampon cartographique de chemins routés | mardi 11 h |
| `gold_o1` | 2 | pilote Grenoble : options accessibles par habitant, par groupe de carreaux (minimum 20 habitants), dossier `accessibilite_grenoble` | mardi 12 h 30 |
| `gold_o2` | 2 | pilote Grenoble : population à 5 min de chacun des 303 arrêts/stations, voisins inclus, dossier `accessibilite_grenoble` | mardi 12 h 30 |
| `gold_couverture` | 2 | livré : relation `id_candidat`, `id_carreau`, `temps_pieton_s` ; 563 613 paires ARA à au plus 300 s, avec synthèse des candidats et inconnues séparées ; voir [couverture_ara.md](couverture_ara.md) | mardi 14 h |
| `gold_features` | 3 | variables socio-économiques et d'intermodalité par isochrone | mardi 12 h |
| `gold_liste_priorisee` | 4 | un nouvel équipement retenu sous budget : `rang`, `type` (citiz, bus, velo), `id_candidat`, `commune`, `nom_commune`, `cout_annualise`, `cout_investissement`, `habitants_gagnes_2030`, `habitants_cumules`, `budget_cumule`, `co2_evite_t_an` (Citiz), `methode_couverture`, `scenario_cout`, `equite`, `geometry` | mercredi 10 h |
| `gold_courbe_budget` | 4 | habitants gagnés selon le budget annuel, par scénario de coût et réglage d'équité, avec le nombre d'équipements par type et la part en petites communes | mercredi 10 h |
| `gold_scores` | 3 | usage potentiel prédit par candidat et par type | mardi 16 h 30 |

## Détail · `carreaux_socio` (rôle 3)

312 143 carreaux habités, 7 703 556 habitants (Auvergne-Rhône-Alpes). Clé : `id_carreau`.

| Colonne | Type | Manquants | Description |
|---|---|---|---|
| `id_carreau` | texte | 0 | Identifiant INSEE du carreau de 200 m (Filosofi 2021) |
| `code_commune` | texte | 0 | Code INSEE de la commune (la première du territoire si le carreau en touche plusieurs) |
| `code_commune_actuelle` | texte | 0 | Commune actuelle : celle de l'IRIS qui contient le centre du carreau (corrige les fusions de communes depuis 2021), sinon `code_commune` |
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
| `emplois` | nombre | 0 | HYPOTHÈSE : emplois de la commune actuelle (recensement 2022) répartis au prorata des équipements, sinon de la population |
| `donnee_masquee` | booléen | 0 | Vrai si l'INSEE a estimé les valeurs (carreau de moins de 11 ménages, secret statistique) |
| `faible_population` | booléen | 0 | Vrai si moins de 20 habitants : à regrouper (O1, vie privée) |
| `x_centre`, `y_centre` | nombre | 0 | Centre du carreau, Lambert 93 (m) |
| `lon`, `lat` | nombre | 0 | Centre du carreau, WGS84 (pour Power BI) |
| `geometry` | géométrie | 0 | Carreau de 200 m, Lambert 93 (EPSG:2154) |

**Contrôles réalisés**
- Identifiants uniques, tous les carreaux dans le territoire, projection Lambert 93.
- Population = 93,9 % de la population légale 2023 (Lyon compris) : Filosofi ne compte pas les personnes vivant en communauté.
- 79,8 % des carreaux, soit 22,3 % des habitants, ont des valeurs estimées par l'INSEE.
- 0 ligne Filosofi écartée pour code commune illisible.
- 22 146 équipements BPE mal géolocalisés (`QUALITE_XY` = M) écartés.

**Hypothèses**
1. Un carreau sur plusieurs communes est rattaché à la première commune du territoire.
2. Un carreau prend la part sans voiture de l'IRIS qui contient son centre, sinon celle de sa commune.
3. Les emplois d'une commune sont répartis entre ses carreaux au prorata des équipements (sinon de la population).
4. Le recensement 2022 est utilisé : le format 2023 publié le 19/10/2026 n'était pas lisible automatiquement.

## Règles de qualité

### Isochrones du pilote

Le pilote des isochrones conserve `origine_id`, `type_origine` et l'identifiant
source (`arret_id` ou `id_carreau`), avec `duree_s`, `vitesse_kmh`,
`distance_raccordement_m`, `temps_acces_s`, `tampon_m`, `surface_m2` et `geometry`.
Les origines sans calcul ont un statut dans `origines_qc.parquet` et ne produisent
pas de polygone. `chemins_accessibles.parquet` conserve les portions routées ;
les surfaces tamponnées servent à la cartographie, pas à prouver un accès au bâti.

### Détail O1/O2 — pilote Grenoble

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
| `gold_o2.offre_id`, `source`, `type`, `nom`, `code_commune`, `operateur`, `valeur`, `unite`, `geometry` | Point d'offre source dans Grenoble ; valeur = passages/jour pour un arrêt, capacité pour une station ; noms des stations corrigés depuis le silver, code commune conservé séparément | valeurs manquantes conservées ; code commune vide pour les arrêts |
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

### Raccordements régionaux ARA

Dossier interne `data/silver/raccordements_ara/`, distinct des indicateurs du
pilote : une ligne par centre de population ou point d'offre, chacun dans sa
tuile principale. GeoParquet EPSG:2154 ; les GeoJSON d'examen sont en EPSG:4326.
Les petits effectifs INSEE restent présents : agréger avant publication publique.

| Colonne | Définition |
|---|---|
| `id_carreau` ou `offre_id`, `tuile_id`, `geometry` | Identifiant et point source ; les marges ne créent pas de copies de points |
| `statut` | `ok`, `raccordement_trop_loin` ou `budget_acces_epuise` ; `ok` ne prouve pas un trajet de 5 minutes |
| `distance_raccordement_m`, `temps_acces_s`, `seuil_raccordement_m` | Raccord droit hypothétique, coût à 4,5 km/h ; seuil de 150 m pour la population et 50 m pour l'offre |
| `raccord_de`, `raccord_vers`, `raccord_cle`, `abscisse_raccord_m` | Arc choisi, stable par ordre `(de, vers, cle)` en cas d'égalité ; abscisse dans son sens |
| `raccord_x`, `raccord_y` | Coordonnées projetées sur le segment, en Lambert 93 |
| `composante_locale_id`, `composante_locale_noeuds`, `composante_locale_minoritaire` | Composante de la fenêtre avec marge, taille et appartenance hors de la plus grande ; ne prouve pas un isolement régional |
| `nb_troncons_equidistants`, `nb_composantes_equidistantes` | Égalités entre segments physiques et composantes distinctes ; les arcs aller-retour ne sont pas comptés deux fois |
| `recherche_bornee_par_marge` | Distance trouvée dépassant la distance au bord de fenêtre : minimum régional non garanti, rejet au seuil toujours valide |
| `entree_metro_a_verifier`, `a_examiner` | Métro signalé systématiquement ; examen aussi si statut non `ok`, composante secondaire ou égalité entre composantes |

### Accès de stations — pilote Lyon MVP

Dossier actuel `data/gold/accessibilite_lyon_acces_stations/`. Configuration
dans `geo/config/acces_stations.json`. Croix-Paquet conserve son identifiant
et sa géométrie GTFS ; deux accès extérieurs sont des destinations alternatives.
Le temps jusqu'au quai après l'accès n'est pas modélisé.

| Table / champ | Définition |
|---|---|
| `acces_stations.acces_id`, `offre_id`, `noeud_osm`, `geometry` | Identifiant stable de l'accès, lien à la station logique et nœud extérieur OSM servant de destination |
| `station_source_x`, `station_source_y`, `distance_station_source_m` | Coordonnées GTFS conservées séparément et écart descriptif au point d'accès ; cet écart n'est pas ajouté au temps d'arrivée |
| `source`, `hypothese`, `voie_osm`, `tourniquet_osm` | Provenance OSM et hypothèse MVP acceptée ; aucune entrée officielle ou visite terrain n'est présumée |
| `raccordements_acces_stations` | Un diagnostic réel par accès, ancré à son identifiant OSM |
| `accessibilite_carreaux_offres.acces_id` | Accès retenu pour le minimum de temps ; vide pour une arrivée au point source. Une paire unique par carreau et station |
| `gold_o2.nb_acces_pietons`, `mode_arrivee` | Deux accès pour Croix-Paquet et mode `acces_exterieur_station` ; zéro accès explicite et mode `coordonnees_source` pour les autres offres |
| `raccordements_offre.via_acces_station`, `nb_acces`, `nb_acces_calculables` | Diagnostic au niveau de la station ; projection unique du point GTFS vide lorsqu'il est remplacé pour le calcul par les accès |

O1 compte une station accessible une seule fois, même si deux accès sont
atteints ; O2 somme une fois la population de chaque carreau desservi. Le
réseau n'autorise aucun passage d'un accès à l'autre par la station.

### O1/O2 régionaux ARA

Sorties dans `data/gold/accessibilite_ara/resultats/publics/` ; méthode et reprise
dans [accessibilite_ara.md](accessibilite_ara.md). Les unités et la formule O1
restent celles du pilote. Un groupe O1 peut traverser une limite communale.

| Champ / table | Définition |
|---|---|
| `gold_o1.groupe_id`, `nb_carreaux`, `population` | Groupe de publication, nombre de carreaux entiers et population fiscale ; minimum 20 habitants |
| `gold_o1.population_evaluee`, `statut` | Population des membres entièrement calculables ; `ok`, `partiel` ou `population_insuffisante` |
| `gold_o1.options_par_habitant`, `options_<mode>_par_habitant` | Moyenne du nombre de points atteignables, pondérée par la population ; aucune valeur complète si une paire du groupe reste indéterminée |
| `gold_o1.passages_jour_<mode>_par_habitant`, `capacite_<mode>_par_habitant` | Moyennes pondérées des valeurs sources ; unités séparées, capacité inconnue conservée manquante |
| `*_min` | Contribution connue ; ne remplace pas une valeur complète lorsque le calcul est partiel |
| `gold_o2.population_desservie`, `nb_carreaux_desservis` | Somme de population et nombre de centres atteignant la station en 300 s ; non additionnables entre stations |
| `gold_o2.statut` | `ok`, `partiel` si une paire est indéterminée, `non_calcule` si le point d'offre n'a pas de raccordement valide |
| `gold_o2.nb_acces_pietons`, `mode_arrivee` | Deux et `un_des_acces_exterieurs` pour Croix-Paquet ; zéro et `point_source_raccorde` pour les autres points |
| `internes/paires_accessibles.acces_id` | Identifiant de l'accès gagnant, vide pour les autres points ; minimum de temps et paire carreau–station unique |
| `internes/paires_indeterminees.motif` | Cause du raccordement non calculable ou segment cible absent de la tuile source ; ce n'est pas une absence de desserte |

Les petits carreaux individuels, correspondances, raccordements et checkpoints
restent des tables techniques internes ; la diffusion O1 utilise les groupes.

### Couverture des candidats ARA

Tables de calcul détaillées pour les rôles 3 et 4, dans `data/gold/`. Méthode
et résultats mesurés : [couverture_ara.md](couverture_ara.md).

| Table / champ | Définition |
|---|---|
| `gold_couverture.id_candidat` | Identifiant de l'emplacement théorique, égal à `gold_candidates.id_carreau` |
| `gold_couverture.id_carreau` | Identifiant du carreau de population cible ; jointure à `carreaux_socio` |
| `gold_couverture.temps_pieton_s` | Trajet connu à au plus 300 s, raccord candidat ≤ 50 m et population ≤ 150 m inclus |
| `candidats_couverture` | Un candidat par centre source, géométrie conservée, `id_carreau_origine`, statut et mesures de couverture |
| `candidats_couverture.statut`, `utilisable_optimiseur` | `ok`, `partiel`, `non_calcule` ; booléen vrai seulement pour un calcul entièrement déterminé |
| `population_couverte`, `nb_carreaux_couverts`, suffixe `_min` | Population et nombre de carreaux par candidat ; valeurs complètes absentes en cas d'échec, contribution connue distincte en cas partiel |
| `couverture_indeterminee` | Les deux identifiants et `motif` ; raccord candidat ou population hors seuil, éventuellement segment cible absent |
| `gold_couverture_qc`, `gold_couverture_controle` | Rapport du calcul et contrôle indépendant, empreintes des entrées et sorties |

Une même personne peut être couverte par plusieurs candidats. Calculer l'union
des `id_carreau` avant de sommer la population ; retrancher séparément la
desserte existante pour obtenir les habitants gagnés. Aucun futur emplacement
n'est retenu ou validé techniquement par cette matrice.

### Règles communes

- Aucun doublon d'arrêt de même nom à moins de 50 m.
- Tous les points sont dans le territoire.
- Chaque coût et chaque ratio a une source ou la mention « hypothèse ».
- Somme de la population des carreaux proche de la population INSEE du territoire.