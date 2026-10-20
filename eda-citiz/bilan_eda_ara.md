# Bilan de l'EDA Auvergne-Rhône-Alpes (état du 19/10, notebook exécuté)

Tout vient du notebook `02_region_ara.ipynb` exécuté par BiLoL (cellules 0 à 12). On compte des **stations**, pas des usages : une station présente ne dit pas qu'elle marche.

## 1. Où est Citiz

622 stations dans 129 communes sur 4 025 (3 %). Seuls 5 352 des 311 660 carreaux de 200 m (1,7 %) sont à moins de 500 m d'une station.

| Type de territoire (aire d'attraction) | Communes | Habitants | Communes avec station | Stations | Stations pour 100 000 hab. | Habitants à moins de 500 m d'une station |
|---|---|---|---|---|---|---|
| Grande aire · pôle | 61 | 2,13 M | 38 (62 %) | 406 | 19,1 | 53,5 % |
| Grande aire · couronne | 1 011 | 2,41 M | 37 (4 %) | 68 | 2,8 | 5,2 % |
| Aire moyenne · pôle | 22 | 0,50 M | 14 (64 %) | 84 | 16,7 | 33,0 % |
| Aire moyenne · couronne | 444 | 0,71 M | 18 (4 %) | 30 | 4,2 | 6,6 % |
| Petite aire · pôle | 116 | 0,82 M | 9 (8 %) | 19 | 2,3 | 5,8 % |
| Petite aire · couronne | 1 174 | 0,97 M | 7 (0,6 %) | 7 | 0,7 | 0,7 % |
| Hors aire | 1 197 | 0,66 M | 6 (0,5 %) | 8 | 1,2 | 0,6 % |

La couronne des grandes villes pèse autant d'habitants que leur pôle (2,4 M contre 2,1 M), avec 6 fois moins de stations par habitant.

## 2. Ce qui distingue les communes avec et sans station (médianes, cellule 11)

Dans chaque type de territoire, les communes desservies sont plus **grandes** et plus **denses**, avec plus de ménages sans voiture et plus d'arrêts et de bornes.

| Couronne des grandes aires | Sans station | Avec station |
|---|---|---|
| Habitants | 1 181 | 7 865 |
| Densité (hab./km²) | 124 | 541 |
| Ménages sans voiture | 4 % | 9 % |
| Arrêts pour 1 000 hab. | 3,1 | 4,8 |
| Points de charge dans la commune | 0 | 13 |
| Niveau de vie moyen | 27 300 € | 28 900 € |
| Part des actifs qui travaillent au pôle | 25 % | 24 % |

Même écart de taille dans les autres types : aires moyennes (870 contre 4 307 habitants), petites aires (523 contre 2 439), hors aire (289 contre 1 606).

## 3. L'idée de départ : flux domicile-travail et périphérie (cellule 12)

Le tableau croise, pour les communes hors pôle, la part des communes desservies selon la taille et la part des actifs qui vont travailler au pôle :

| Habitants | < 20 % vers le pôle | 20 à 40 % | > 40 % |
|---|---|---|---|
| < 2 000 | 0 % (1 905 communes) | 1 % (933) | 1 % (379) |
| 2 000 à 5 000 | 1 % (153) | 6 % (177) | 7 % (106) |
| 5 000 à 10 000 | 17 % (30) | 12 % (48) | 19 % (42) |
| ≥ 10 000 | 100 % (11) | 40 % (15) | 0 % (11) |

- **La taille de la commune explique bien plus que les flux** : presque aucune commune de moins de 2 000 habitants n'a de station, quelle que soit sa dépendance au pôle. Au niveau des médianes, la part des actifs qui vont au pôle est la même avec et sans station (cellule 11).
- **L'idée « la périphérie qui travaille au pôle est desservie » n'est pas confirmée.** Chez les communes de plus de 10 000 habitants, c'est même l'inverse : les 11 communes qui envoient plus de 40 % de leurs actifs au pôle n'ont aucune station, alors que les 11 qui en envoient moins de 20 % en ont toutes. Effectifs faibles, à ne pas sur-interpréter.
- Lecture possible (hypothèse) : Citiz est présent dans les petites villes autonomes (emplois sur place, ménages sans voiture), pas dans les villes-dortoirs de la couronne.

**Communes candidates** (profil des communes desservies, sans station, au moins 2 000 habitants, 30 % ou plus des actifs vers le pôle, au moins un arrêt) : 213 communes. En tête : Givors (21 400 hab., 20 % de ménages sans voiture), Cournon-d'Auvergne, Riom, Firminy (25 % sans voiture), Saint-Just-Saint-Rambert, Mions, Genas, Brignais, Le Chambon-Feugerolles (21 %).
**Attention** : une partie de la liste est dans l'aire de Clermont-Ferrand (Cournon, Riom, Pont-du-Château, Gerzat, Cébazat, Lempdes), de Vichy (Bellerive) ou de Montluçon (Domérat), où Citiz n'a aucune station : ce n'est pas un trou dans un réseau existant. Les candidats les plus crédibles sont dans les aires de Lyon, Saint-Étienne et Grenoble, où Citiz est déjà implanté.

## 4. Autour des stations existantes (cellule 10, médianes par station)

| Type de territoire | Habitants à 500 m | Arrêts à 300 m | Points de charge à 300 m |
|---|---|---|---|
| Grande aire · pôle | 7 949 | 2 | 4 |
| Grande aire · couronne | 2 446 | 3 | 4 |
| Aire moyenne · pôle | 5 221 | 7 | 2 |
| Petite aire · couronne | 807 | 2 | 2 |
| Hors aire | 642 | 1 | 2 |

449 stations sur 622 (72 %) ont un point de charge public à moins de 300 m ; la station médiane est à 132 m d'un arrêt. Cela rend l'électrique plausible mais ne donne pas le temps de recharge entre deux locations.

Motorisation (recensement 2023) : ménages sans voiture 15,8 % dans les pôles des grandes aires contre 3,8 % dans leurs couronnes ; ménages à deux voitures ou plus 31 % contre 59 %.

## 5. Limites

- Les stations ne mesurent pas l'usage ; le modèle d'usage viendra du relevé de disponibilité.
- Les médianes par type de territoire cachent de fortes différences entre communes ; les petits effectifs (11 à 48 communes dans certaines cases) donnent des pourcentages fragiles.
- L'aire de Genève-Annemasse n'est comptée que pour sa partie française.
- Données de millésimes différents : flux et motorisation 2023, Filosofi 2021 (7,70 M d'habitants contre 8,21 M dans la population communale actuelle), communes au 1er janvier 2026. 11 communes sans flux.
- Tous les « à 500 m » et « à 300 m » sont des distances à vol d'oiseau, pas à pied par les rues : les habitants couverts sont surestimés, surtout hors des centres. Un test de réseau piéton (`cellule_13_reseau_pieton.md`) n'a pas pu aboutir le 19/10, les serveurs OpenStreetMap publics étant saturés.
- La colonne de densité retenue (`DENS`) n'est pas utilisée dans les comparaisons ; le type de territoire vient des aires d'attraction, pas des seuils de population de la première EDA.

## 6. À quoi serviront les données du collecteur

Le relevé toutes les 5 minutes du nombre de voitures par station (et de l'état de chaque voiture), sur au moins un jour de semaine et un week-end complets, donnera :
- un **indicateur d'usage par station** (part du temps où des voitures sont absentes), cible du modèle « la voiture sera-t-elle utilisée ? », à relier aux variables de cette EDA (habitants à 500 m, motorisation, arrêts, taille de la commune) ;
- les **heures de pointe** en semaine et le week-end ;
- la **durée entre deux départs** d'une même voiture, donc la fenêtre de recharge pour « peut-elle être électrique ? », avec l'autonomie de `vehicle_status` ;
- la **part de voitures électriques** par station.

Limites : ce sont des disponibilités, pas des réservations. Au dernier contrôle, le relevé ne comptait que 3 mesures sur 6 minutes : il faut le relancer en continu.

## 7. Prochaines étapes

1. Lancer le collecteur en continu, au moins une semaine si possible.
2. Définir l'indicateur d'usage et le relier aux variables de la table `stations_croisees.parquet`.
3. Pour les candidats, garder les communes des aires où Citiz est déjà présent, et traiter à part l'extension vers Clermont-Ferrand, Vichy et Montluçon.
