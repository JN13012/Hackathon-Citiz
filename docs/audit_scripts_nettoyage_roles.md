# Audit des scripts de préparation et de nettoyage — 20 octobre 2026

Mise à jour après cet audit : le constructeur régional
`geo/build_reseau_ara.py` a été implémenté avec nettoyage et validation intégrés.
Voir [construction_reseau_ara.md](construction_reseau_ara.md). Les constats
ci-dessous décrivent l'état observé avant cette implémentation. La génération
et le contrôle indépendant des **835 tuiles ARA sont maintenant terminés** ;
voir [execution_reseau_ara.md](execution_reseau_ara.md). O1/O2 régionaux restent à calculer.

## Périmètre vérifié

Inspection des scripts, du branchement dans le pipeline et des rapports existants.
Les références distantes ont été actualisées avec `git fetch origin`, sans fusion.
La branche locale `geomatique` est au commit `1f24ad6`, avec des modifications non
commitées. Le dernier `origin/main` inspecté est
`e83a984b33e0793d6eef9ed1bbb6ec8b8c2b228a`.

Aucune collecte ni aucun calcul métier n'a été relancé pour cet audit. La présence
d'un script ne prouve donc pas que sa dernière version a déjà produit les fichiers
actuellement livrés. Les scripts distants examinés ont été vérifiés syntaxiquement.

## État par rôle

| Rôle | Scripts | Traitements et contrôles présents | Limites constatées |
|---|---|---|---|
| 1 — transports collectifs | `scripts/build_arrets.py`, `scripts/controle_qualite.py`, `scripts/build_gold.py` | Normalisation des arrêts GTFS, modes et fréquences, dédoublonnage spatial, projection Lambert-93, filtrage par contours, contrôle des identifiants, géométries et valeurs manquantes ; construction des tables gold. | La version locale ajoute l'arrêt avant publication en cas de source GTFS en échec et une protection du remplacement des fichiers. Ces protections ne sont pas encore dans le dernier main. Main apporte de son côté un dédoublonnage répété et une amélioration des noms de réseaux fusionnés : préserver les deux lors de la synchronisation. |
| 2 — géomatique | `geo/build_contours.py`, `geo/build_reseau.py`, `geo/prepare_reseau_ara.py` | Préparation des limites IGN, validation des codes et géométries ; préparation et validation du graphe piéton ; validation des entrées régionales et planification des tuiles. | Le graphe construit couvre Grenoble et sa marge. La construction depuis les PBF régionaux reste à implémenter. Les étapes géomatiques restent commentées dans `scripts/run_all.py`. |
| 3 — population et équipements | `model/build_carreaux.py` dans le dernier main | Sélection des carreaux du territoire, conversions numériques, identifiants dédupliqués, projection, rattachement aux IRIS et communes, agrégation des équipements, marquage des imputations et faibles populations ; contrôles des proportions et de cohérence démographique. | Script absent de la branche locale actuelle et actif dans le pipeline du dernier main. Les exports sont écrits avant le retour d'erreur des contrôles : une validation négative peut donc remplacer une livraison existante. |
| 4 — offre partagée, projections et coûts | `optim/build_offre_partagee.py`, `optim/build_projections.py`, `optim/build_couts.py` | Stations : types et capacités normalisés, exclusion des zones virtuelles sans station et de stations non publiques, séparation des ouvertures futures, contrôle des coordonnées, dédoublonnage par système et identifiant, filtrage territorial. Projections : identifiants et cohérence des facteurs. Coûts : types, bornes, sources et hypothèses contrôlés. | Des flux GBFS en échec sont signalés sans bloquer toute publication des stations. Le dernier main corrige aussi l'alignement des carreaux de projection avec le rôle 3 ; ce correctif n'a pas été rejoué localement pendant cet audit. |
| 5 — ratios CO₂ et restitution | `restitution/build_ratios_co2.py`, `restitution/ratios_co2.yaml` dans le dernier main | Typage des ratios, détection des doublons et valeurs absentes, vérification des sources, pages et bornes, comparaison aux données Impact CO₂ conservées en bronze ; rapport sur les tables et le dictionnaire. | Fichiers absents de la branche locale actuelle. Script actif dans le pipeline du dernier main. La table des ratios n'est publiée que si les contrôles des ratios passent ; l'absence d'autres tables documentées est informative. |

Le nettoyage est intégré aux scripts de construction : un fichier nommé
`clean_data.py` séparé n'est pas nécessaire pour qu'un rôle possède un traitement
de nettoyage reproductible. Le contrôle qualité doit cependant bloquer la
publication lorsqu'une condition obligatoire échoue.

## Notre partie géomatique : conserver et compléter

### Contours IGN : conserver `build_contours.py`

Le script normalise les codes, vérifie leur format et leur unicité, exige une
projection connue, sélectionne ARA et contrôle les géométries polygonales. Il
traite notamment le rattachement des arrondissements de Lyon et prépare les
communes, IRIS et EPCI. Les géométries invalides sont rejetées, pas réparées
silencieusement. Les sorties sont préparées avant leur remplacement.

La livraison régionale existante comprend 4 025 communes, 5 407 IRIS et 172 EPCI.
Il n'y a pas besoin de refaire ce nettoyage de zéro.

### Graphe piéton : réutiliser `build_reseau.py`

Sa fonction `prepare_graph` vérifie la présence et la validité des coordonnées,
projette en EPSG:2154, refuse les tronçons invalides ou de longueur nulle,
contrôle l'unicité des identifiants et calcule le temps de marche à partir des
longueurs et de la vitesse. Elle conserve et identifie les composantes connexes.
Les contrôles de trajets sont structurels ; ils ne constituent pas une
validation de terrain.

Ce traitement existe pour le graphe Grenoble + 1 km. Son acquisition par
Overpass n'est pas un constructeur régional depuis les archives PBF. Augmenter
simplement sa limite de surface ne suffit pas à préparer ARA.

### Entrées régionales : préparation disponible, graphe non construit

`download_osm_ara.py` télécharge et vérifie l'intégrité des archives OSM ; ce n'est
pas un nettoyage des rues.

`prepare_reseau_ara.py` vérifie les tables, identifiants, géométries et sources,
fige les entrées et prépare les tuiles avec marge. La préparation existante
contient 835 tuiles, dont 791 avec des origines de calcul. Ce script indique
explicitement qu'il ne construit pas encore le graphe.

Il manque donc le constructeur régional, par exemple `geo/build_reseau_ara.py`,
qui devra :

1. Lire les PBF téléchargés et extraire les rues et chemins utiles aux tuiles.
2. Appliquer les règles d'accès piéton, les barrières et les sens autorisés ;
   examiner explicitement les voies cyclables autorisant aussi la marche.
3. Gérer les recouvrements et identifiants OSM entre extractions, sans créer de
   doublons artificiels ni perdre les connexions nécessaires aux trajets.
4. Réutiliser les validations et calculs de poids du graphe existant, produire
   un rapport qualité et publier uniquement après validation.

## Suites recommandées

- Lors de la synchronisation manuelle avec main, récupérer les scripts des rôles
  3 et 5 et la correction des projections ; préserver les protections GTFS et
  les corrections géomatiques locales non commitées.
- Compléter le constructeur piéton régional en réutilisant le code existant.
- Renforcer la publication des rôles 3 et 4 : définir les sources et contrôles
  obligatoires, valider avant remplacement et conserver la livraison précédente
  en cas d'échec. Ne pas transformer un téléchargement indisponible en offre nulle.
- Recalculer ensuite les accès à cinq minutes et O1/O2 sur les données validées.

Conclusion : chaque rôle possède maintenant des scripts de préparation ou de
validation dans le dernier main. Notre nettoyage géomatique existe ; il faut
compléter la construction régionale, pas réécrire tout le nettoyage.
