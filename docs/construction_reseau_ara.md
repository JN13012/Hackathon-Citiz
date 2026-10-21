# Construction et nettoyage du réseau piéton ARA

`geo/build_reseau_ara.py` complète la préparation régionale. Il lit les archives
OSM locales, construit un graphe par fenêtre et applique le nettoyage avant les
contrôles du graphe obtenu. Il ne recalcule pas O1/O2 et ne remplace ni les
transports, ni la population, ni les résultats du pilote de Grenoble.

**Règle du projet, version 2 : toute voie retenue est parcourable à pied dans
les deux sens, avec le même temps de marche.** C'est l'hypothèse de calcul
demandée, y compris lorsqu'un tag OSM indique une restriction directionnelle.

`geo/run_reseau_ara.py` est le lanceur recommandé : il exécute ce même
constructeur et libère les objets Python, buffers Arrow et blocs mémoire
inutilisés avant chaque appel à osmium. Les règles, les tables et leurs
empreintes de validation restent celles du constructeur. Cette gestion mémoire
évite de conserver les buffers d'une grosse tuile pendant l'extraction suivante.

## Utilisation

L'image Docker doit contenir le nouveau code ; `osmium-tool` est déjà déclaré
dans le Dockerfile. Construire une fois :

```bash
docker compose build pipeline
```

Essai sur les tuiles contenant les centres de Grenoble et Lyon :

```bash
docker compose run --rm pipeline python geo/run_reseau_ara.py \
  --tuiles x91_y645 x84_y651 \
  --output-dir data/silver/reseau_ara_bidirectionnel
```

Construction des **835 tuiles planifiées**, en conservant les tuiles voisines
sans origine et en réutilisant les sorties de l'essai :

```bash
docker compose run --rm pipeline python geo/run_reseau_ara.py \
  --output-dir data/silver/reseau_ara_bidirectionnel --reprendre
```

`--lot 1` est la valeur par défaut pour limiter la mémoire des extractions.
Une valeur de 1 à 32 est acceptée ; un essai régional avec 16 extractions
simultanées a été interrompu par le système (SIGKILL). Le graphe est construit et exporté tuile par
tuile. La mémoire et la durée dépendent de la densité OSM : commencer par
l'essai, sans lancer plusieurs constructeurs sur le même dossier.

Une sortie existante est refusée sans `--reprendre`. Avec cette option, les
empreintes des fichiers et la compatibilité du constructeur, du plan, des
archives, de la validation et de la vitesse sont vérifiées. Après un changement
de code ou de plan, choisir un nouveau dossier plutôt que mélanger les versions.

## Nettoyage et règles de marche

1. Vérification SHA-256 de la préparation et des deux archives ; aucune collecte
   HTTP ni lecture des transports actifs pendant la construction.
2. Préfiltrage des voies avec `highway` et des relations de restriction piétonne,
   avec leurs objets référencés. Cache PBF vérifié par empreinte.
3. Extraction `complete_ways` par fenêtres englobantes, puis conservation des
   segments qui intersectent la fenêtre Lambert-93. Les segments de bordure
   restent entiers : leurs extrémités peuvent dépasser la fenêtre.
4. Fusion des nœuds, voies et relations par identifiant OSM. Les copies identiques
   sont dédupliquées ; versions ou contenus contradictoires arrêtent le traitement.
5. Filtrage des accès : les autorisations `foot` précisent l'accès général ; les
   accès privés, réservés à des clients ou à une destination sont exclus sauf
   autorisation piétonne explicite. Les valeurs inconnues ne sont pas autorisées
   automatiquement. Les cycleways et bridleways exigent une autorisation piétonne.
6. Construction systématique d'un aller et d'un retour pour chaque segment.
   Les tags `oneway`, `oneway:foot`, les limitations directionnelles `foot:forward`
   et `foot:backward`, le sens des escaliers mécaniques et les restrictions de
   virage ne limitent pas le routage. Leur présence est comptée dans le rapport.
   Une autorisation piétonne explicite dans un sens suffit à retenir la voie dans
   les deux sens ; les interdictions générales d'accès à pied restent filtrées.
7. Blocage des nœuds interdits, verrouillés ou portant une barrière inconnue ;
   les barrières usuelles franchissables à pied sont autorisées par défaut sous
   réserve des tags d'accès. Aucun raccourci ne contourne un nœud bloqué.
8. Construction de segments entre références OSM successives, sans simplifier les
   intersections. Un croisement géométrique sans nœud commun ne crée pas de connexion.
   Les chemins parallèles distincts sont conservés. Les références consécutives
   identiques sont retirées et comptées ; des nœuds distincts de mêmes coordonnées
   créant un segment nul provoquent une erreur à examiner.
9. Réutilisation de `prepare_graph` : EPSG:2154, coordonnées finies, géométries
   valides, longueurs positives, clés uniques et temps de marche à **4,5 km/h**
   selon le plan. Toutes les composantes sont conservées et recensées.
10. Vérification des extrémités, poids, présence du retour de chaque arc avec le
    même coût et exports relus, puis publication du
    dossier complet de la tuile. Un échec conserve les dossiers déjà validés.

`load_tile_graph(dossier_tuile)` reconstruit le graphe depuis les GeoParquet.
Le constructeur vérifie cette relecture avant publication ; les trajets ont les
mêmes coûts et la même topologie. L'export GraphML est facultatif pour éviter
la sérialisation XML coûteuse sur 835 tuiles. Les vérifications des coordonnées,
extrémités et poids utilisent des opérations vectorisées, avec les mêmes règles.

Sources des conventions et de l'extraction :
[foot](https://wiki.openstreetmap.org/wiki/Key:foot),
[oneway:foot](https://wiki.openstreetmap.org/wiki/Key:oneway:foot),
[barrier](https://wiki.openstreetmap.org/wiki/Key:barrier),
[osmium tags-filter](https://docs.osmcode.org/osmium/latest/osmium-tags-filter.html),
[osmium extract](https://docs.osmcode.org/osmium/latest/osmium-extract.html).
Les choix conservateurs du script constituent un modèle d'accès public, pas une
preuve de terrain pour chaque voie.

## Sorties et contrôle qualité

Dans `data/silver/reseau_ara_bidirectionnel/<tuile_id>/` :

| Fichier | Contenu |
|---|---|
| `reseau_pieton.graphml` | Export facultatif avec `--graphml` ; les deux GeoParquet suffisent à reconstruire le graphe routable |
| `reseau_pieton_noeuds.parquet` | Nœuds, composantes, appartenance à la tuile et à sa fenêtre |
| `reseau_pieton_troncons.parquet` | Arcs, géométries, longueurs, secondes, identifiant de voie, segment et direction OSM |
| `zone_requete.parquet` | Fenêtre d'extraction avec marge |
| `reseau_pieton_qc.json` | Comptages du nettoyage, composantes, trajets structurels, limites et empreintes |

`construction_reseau_ara.json`, à la racine, distingue `partiel` de
`regional_termine` et liste les tuiles manquantes. Aucun effectif régional de
nœuds ou de tronçons n'est obtenu par simple addition des tuiles : les marges
se recouvrent. Les clés `(de, vers, cle)` et les références OSM sont stables
entre extractions compatibles. Les origines restent affectées à leur tuile
principale dans les entrées figées.

`direction_osm` vaut `1` ou `-1` pour indiquer l'orientation de l'arc par rapport
à l'ordre des nœuds dans la voie OSM. Chaque segment retenu possède les deux
orientations avec le même coût de marche : ce champ n'impose aucun sens unique.

Une fenêtre sans tronçon **avec des origines** est une erreur à examiner ; elle
ne devient pas un résultat d'accès nul. Sans origine, une tuile vide peut être
publiée avec l'état explicite `valide_sans_troncon_sans_origine`.

## Limites et étapes suivantes

- Les accès piétons conditionnels sont exclus et comptés ; aucun horaire de
  départ n'est interprété. Les conditions portant uniquement sur une direction
  et les restrictions de virage piétonnes sont ignorées dans le modèle bidirectionnel.
- Les portes et barrières restent dépendantes des tags OSM. Les obstacles
  linéaires sans raccordement au réseau ne sont pas interprétés géométriquement.
- La vitesse ignore la pente, les attentes et les besoins PMR. Les ascenseurs,
  surfaces piétonnes sans chemin linéaire et traversées maritimes ne sont pas routés.
- `complete_ways` peut omettre une longue voie traversant une fenêtre sans nœud
  intérieur ; les trajets aux frontières internationales dépendent des extraits.
- La relecture et les trajets structurels ne remplacent pas des contrôles de
  terrain ni la validation de la couverture des points de population et d'offre.
- Le réseau suit le même modèle bidirectionnel que le calculateur d'isochrones
  existant. Il reste à orchestrer les origines et la fusion des résultats par
  tuile avant de calculer et publier O1/O2 à l'échelle régionale.

## Validation réalisée le 20 octobre 2026

**La construction régionale de la version 2 est terminée : 835/835 tuiles,
contrôle indépendant valide et 83 tests réussis.** La livraison est dans
`data/silver/reseau_ara_bidirectionnel/` : 16 608 205 nœuds OSM distincts,
17 246 938 segments, 34 493 876 arcs orientés distincts, environ 2,50 Gio.
Voir [execution_reseau_ara.md](execution_reseau_ara.md) pour les rapports et
les reprises après interruption. O1/O2 régionaux restent à calculer.

La version 2 est vérifiée par les tests, notamment les trajets aller/retour de
même durée malgré les tags directionnels, la relecture GraphML et l'acceptation
du graphe par `WalkingNetwork`, le calculateur d'isochrones existant.

Les essais historiques ci-dessous concernent la version 1, avant le choix du
modèle bidirectionnel. Ils restent séparés de la livraison régionale de la
version 2. Le constructeur refuse de mélanger ces versions à la reprise.

- **82 tests réussis**, sans avertissement ni test ignoré, dans un environnement
  temporaire Python 3.15 avec osmium-tool 1.19.0 et OSMnx 2.1.1. La chaîne PBF
  réelle est testée : filtres, extraction, fusion, export, reprise et refus des
  entrées ou sorties modifiées. Les autres tests géomatiques et protections GTFS
  du dépôt passent également.
- Essais préliminaires sur les vrais PBF dans
  `data/silver/reseau_ara_validation/` : nord de Lyon, tuile `x84_y652`,
  **163 078 nœuds / 369 735 arcs** ; Grenoble, tuile `x91_y645`,
  **182 416 nœuds / 411 453 arcs**. Ce dossier d'essai utilise une version
  intermédiaire du constructeur ; il reste séparé des sorties de la version finale.
- Version 1 exécutée sur Clermont-Ferrand, tuile `x70_y651`, dans
  `data/silver/reseau_ara/` : **95 510 nœuds / 206 997 arcs / 185 composantes**.
  La reprise a été vérifiée sur ces fichiers, sans reconstruction de la tuile.
  Un contrôle indépendant a relu les Parquet et vérifié les identifiants,
  références des arcs, géométries, longueurs, temps et empreintes.
- Le bilan de la version 1 indique **1/835 tuiles validées**, état
  `partiel`. Les essais préliminaires ne sont pas additionnés à ce bilan.
  Ce bilan historique ne décrit pas la livraison régionale actuelle ; seuls
  les calculs O1/O2 restent à exécuter sur le nouveau réseau.

Vérification des tests dans l'image reconstruite :

```bash
docker compose run --rm pipeline python -W error -m unittest discover -s tests -v
```
