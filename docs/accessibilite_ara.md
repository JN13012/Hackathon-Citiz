# O1 et O2 sur la livraison ARA

Le calcul utilise la préparation figée `data/silver/preparation_reseau_ara/`,
le réseau de 835 tuiles `data/silver/reseau_ara_bidirectionnel/` et les
raccordements déjà contrôlés `data/silver/raccordements_ara/`.
Il ne relance pas la collecte des transports et ne modifie pas ces sources.

## Livraison mesurée le 20 octobre 2026

Les **835 tuiles sont terminées** et le contrôle indépendant est **valide**.
Le calcul régional est achevé ; certains indicateurs restent partiels à cause
des raccordements hors seuil déjà identifiés, pas d'une tuile manquante.

| Mesure | Résultat |
|---|---:|
| Carreaux traités | 312 143 |
| Population fiscale conservée, avant et après regroupement | 7 703 555,5 |
| Paires carreau–station accessibles en 300 s | 72 389 |
| Paires indéterminées | 351 |
| Groupes publics O1, tous au moins 20 habitants | 105 936 |
| Groupes O1 complets / partiels | 102 901 / 3 035 |
| Points O2 | 18 735 |
| Points O2 complets / partiels / non calculables | 18 457 / 263 / 15 |
| Croix-Paquet, population / carreaux / accès extérieurs | 6 138 / 5 / 2 |
| Fusions de groupes O1 faute de bord commun | 45 172 |

Les 351 paires indéterminées concernent 290 carreaux : 276 échecs de
raccordement population et 75 échecs de raccordement offre. Aucun segment cible
n'est absent de la tuile utilisée. Les 3 464 centres population hors seuil
restent marqués non calculables même lorsqu'aucune offre n'est proche ; cela
peut aussi rendre leur groupe O1 partiel. Les 15 offres hors seuil restent
celles de l'[audit des raccordements](audit_15_raccordements_offre_ara.md).

Validation : **115 tests réussis** ; les **5 956 paires accessibles et 7 paires
indéterminées** du domaine Lyon sont identiques au pilote avec accès extérieurs
(écart maximal de temps 7,04 × 10⁻¹⁰ s). Tous les O2 lyonnais sont identiques.
Le contrôle indépendant valide aussi **33 trajets positifs sur les 11 types
d'offre et 14 témoins négatifs**, avec NetworkX au lieu du moteur CSR. Une
reprise réelle retrouve 835 checkpoints valides, ne recalcule aucune tuile
et conserve les résultats à l'identique.

Preuves : `data/gold/accessibilite_ara/controle_independant.json`,
`resultats/accessibilite_qc.json` et les journaux
`data/silver/journaux/{accessibilite_ara,tests_accessibilite_ara,validation_accessibilite_ara,reprise_accessibilite_ara}.log`.
Les pilotes précédents et les sources figées sont conservés.

## Définition des indicateurs

**O1, options accessibles par habitant** : chaque habitant est représenté au
centre de son carreau. On compte les points d'offre atteignables depuis ce
centre en 300 secondes à pied. Pour publier les petits carreaux, on les
regroupe jusqu'à au moins 20 habitants et on calcule la moyenne pondérée
`somme(population_i × options_i) / somme(population_i)`.
Cette formule conserve celle des pilotes ; ce n'est pas le ratio nombre de
stations / population du plan V5. Les regroupements peuvent traverser les
limites communales et, faute de voisin contigu, réunir des polygones disjoints.
Le rapport compte ces replis. Aucune population n'est répartie arbitrairement.

**O2, population desservie par arrêt ou station** : somme de la population des
carreaux dont le centre peut atteindre ce point en 300 secondes. Un carreau
compte une fois par station, même s'il atteint plusieurs accès. Il peut compter
pour plusieurs stations ; additionner toutes les lignes O2 ne donne donc pas
une population régionale unique.

La vitesse de marche est de 4,5 km/h, les voies sont bidirectionnelles et les
raccordements rectilignes de départ et d'arrivée sont inclus. Seuils conservés :
150 m pour la population, 50 m pour les points d'offre. Les petites composantes
du réseau ne sont pas supprimées. Pour Croix-Paquet, les deux accès extérieurs
OSM configurés remplacent le point GTFS dans le routage : atteindre l'un suffit,
sans modéliser le temps jusqu'au quai. Aucun passage par la station n'est ajouté
au réseau. Les autres offres utilisent leurs coordonnées sources.

Le périmètre couvre les 312 143 carreaux habités et 18 735 points livrés par
l'équipe, dont 901 stations vélo fixes, 620 Citiz et 36 Clem. Cela comprend les
modes présents dans les GTFS, dont le métro ; aucun train n'est livré. La
population est celle des ménages fiscaux Filosofi 2021, estimée au centre du
carreau. L'offre livrée ne garantit pas l'exhaustivité de tous les réseaux ARA ;
les habitants hors de la livraison ARA ne sont pas ajoutés aux stations de bord.

Un mauvais raccordement ou une paire indéterminée reste explicite : valeur
complète absente, statut `partiel` ou `non_calcule`, borne connue dans les colonnes
`*_min` lorsqu'elle est calculable. Les cinq capacités manquantes ne deviennent
pas zéro et n'effacent pas les nombres de stations. Fréquences par jour et
capacités restent séparées par mode. Un zéro calculé concerne ce modèle et
cette offre, pas une preuve d'absence réelle de service.

## Lancement et reprise

Avec les dépendances de `requirements.txt` installées :

```bash
python geo/build_accessibilite_ara.py --workers 2
```

Après interruption, reprendre le même dossier :

```bash
python geo/build_accessibilite_ara.py --workers 2 --reprendre
```

Deux processus chargent chacun une tuile à la fois en matrice creuse SciPy.
Chaque centre appartient à une seule tuile principale ; les marges de 1 km
permettent d'inclure les trajets vers les voisins, sans recompter la population.
La présélection de 375 m à vol d'oiseau élimine seulement les paires impossibles
en cinq minutes ; c'est le trajet sur le réseau qui décide de l'accessibilité.

Le dossier `data/gold/accessibilite_ara/` contient :

| Chemin | Usage |
|---|---|
| `signature.json` | Empreintes des sources, du code, des paramètres et versions des bibliothèques |
| `progression.json` | État du calcul et nombre de tuiles terminées |
| `tuiles/<id>/` | Paires accessibles et indéterminées, reçus et empreintes pour reprise |
| `regroupement/` | Regroupement des carreaux et correspondance technique, réutilisables après interruption |
| `resultats/publics/gold_o1.{parquet,csv,geojson}` | O1 par groupe, population, statut et mesures par mode |
| `resultats/publics/gold_o2.{parquet,csv,geojson}` | O2 par point d'offre, géométrie source, code commune et nom source |
| `resultats/publics/acces_stations.parquet` | Provenance et positions des deux accès de Croix-Paquet |
| `resultats/internes/` | Paires, raccordements, correspondance des groupes et O1 par petit carreau : fichiers de calcul, pas de diffusion tels quels |
| `resultats/accessibilite_qc.json` | Effectifs, hypothèses, limites et empreintes de tous les résultats |
| `controle_independant.json` | Conservation, comparaison Lyon et trajets témoins NetworkX, après vérification séparée |

Les sorties GeoParquet sont en EPSG:2154, les GeoJSON en EPSG:4326 et les CSV
contiennent les attributs sans géométrie. Les colonnes O1 conservent les noms
des pilotes. O2 ajoute `nb_acces_pietons` et `mode_arrivee` :
`un_des_acces_exterieurs` pour Croix-Paquet, `point_source_raccorde` ailleurs.

Une reprise vérifie chaque empreinte avant réutilisation. Une modification des
sources, paramètres ou du code producteur exige un nouveau dossier de sortie,
pas un mélange de versions. Les dossiers de tuiles et le dossier global
`resultats/` sont publiés par renommage après écriture complète. Aucun résultat
global n'est publié avant la fin des 835 tuiles. `--tuiles <id> ...` permet un
contrôle limité et conserve explicitement l'état partiel. Une autre exécution
ne peut pas écrire simultanément dans le même dossier.

## Vérification et intégration

```bash
python geo/validate_accessibilite_ara.py
python -m unittest discover -s tests -v
```

Le contrôle indépendant vérifie les empreintes, identifiants, seuils de
publication O1, conservation de population, temps et borne à vol d'oiseau,
absence de doubles comptes et valeurs O2. Il compare toutes les paires et
les temps du domaine Lyon au pilote conservé, ainsi que les O2 lyonnais. Des
témoins positifs de chaque mode, des franchissements de tuiles et des témoins
négatifs sont recalculés avec NetworkX, indépendamment du moteur CSR.

Pour la restitution de l'équipe, utiliser les fichiers `resultats/publics/`
avec le rapport QC et cette méthode. Ce traitement n'ajoute pas d'application.
Les isochrones polygonaux régionaux restent à produire. La matrice
`gold_couverture` entre **futurs emplacements candidats** et population est
désormais livrée séparément : voir [couverture_ara.md](couverture_ara.md).
Les paires calculées ici relient la population à **l'offre existante**.
