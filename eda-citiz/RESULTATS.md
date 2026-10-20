# EDA Citiz : résultats et suite

Source : flux GBFS publics de Citiz (liste des stations, sans historique d'usage), lus le 19/10/2026, et API geo.api.gouv.fr (commune, population, région). Script : `eda_citiz.py`. Sorties dans `sorties/`.

## 1. Choix de la région : Auvergne-Rhône-Alpes
17 réseaux Citiz lus, 1 628 stations en France, dont **25 « Ouverture prochaine »**, toutes en Grand Est (annoncées, pas encore ouvertes : hors du tableau ci-dessous). Un 18ᵉ réseau Citiz actif au 19/10/2026, **OGALO (Saumur)**, n'était pas lu par l'EDA ; `optim/build_offre_partagee.py` lit les 18 réseaux (1 605 stations publiques ouvertes). Quatre régions réunissent assez de stations et les types de territoire utiles : Auvergne-Rhône-Alpes, Grand Est, Nouvelle-Aquitaine, Occitanie.

| Région | Total | Villes moyennes | Petites communes / rural | Hors grandes villes |
|---|---|---|---|---|
| Auvergne-Rhône-Alpes | 624 | 188 | 101 | 289 |
| Grand Est | 266 | 66 | 21 | 87 |
| Nouvelle-Aquitaine | 160 | 76 | 11 | 87 |
| Occitanie | 142 | 33 | 19 | 52 |

Raison : seule région avec les quatre types de territoire, et trois fois plus de stations hors grandes villes pour apprendre le modèle d'usage. Point faible : plus lourde à traiter, trois réseaux à fusionner (Lyon, Alpes Loire, Auto-en-Velay).

## 2. Ce que montre la liste des stations (Auvergne-Rhône-Alpes)
- 624 stations, dont **2 fausses** à exclure : « Véhicules hors-circuit » (Chambéry, capacité 103) et « DEMOBOX (Réservé Citiz) » (Grenoble, capacité 100). Reste **622 stations réelles**, dans **130 communes** et 10 départements.
- Forte concentration : Isère (221) et Rhône (208) = 69 % des stations. Les 10 premières communes = 65 %. Lyon 146, Grenoble 129, Chambéry 34, Villeurbanne 22, Saint-Étienne 19, Annecy 19, Valence 13.
- Longue traîne : **68 communes sur 130 n'ont qu'une seule station**.
- L'Auvergne est presque absente : 9 stations en Haute-Loire, 1 dans le Cantal, aucune à Clermont-Ferrand. Citiz est surtout présent côté Rhône-Alpes.
- **96 % des stations sont sur la voirie** (599 sur 624), 15 en parking souterrain, 10 en parking ouvert.
- Petites stations : capacité médiane 2, 75 % des stations ont 3 places ou moins. Le nombre de véhicules par station varie peu : mauvaise cible pour le modèle d'usage.
- Trois stations ont une capacité de 0 (Téléphérique à Grenoble, Mairie de Poisat, Ferney-Voltaire).
- 7 stations virtuelles, toutes du réseau Alpes Loire, dans de petites communes (1 à 3 places). Hypothèse non vérifiée : test de nouvelles petites communes.
- Environ deux tiers des stations ont une autre station à moins de 500 m (risque de cannibalisation en ville, d'où une distance minimale entre nouvelles stations). Une vingtaine sont à plus de 5 km de la plus proche : ce sont des petites communes isolées.
- **21 stations se disent électriques dans leur nom**, toutes sur Alpes Loire, aucune à Lyon (le réseau de Lyon ne l'écrit pas : c'est un minimum). 13 en grande ville (Grenoble 7, Saint-Étienne 4, Annecy 2), 6 en ville moyenne, 2 en petite commune. Elles sont aux gares, parkings-relais, mairies : hypothèse non vérifiée, Citiz les place là où une borne est disponible.

## 3. Limites
- Le type de territoire vient de la population de la commune (premier tri). La grille de densité INSEE sera plus juste. Les seuils 400 000 / 100 000 / 10 000 sont à discuter.
- Compter les stations dit où Citiz est présent, pas où cela marche.
- La liste ne donne **ni usage, ni durée de location, ni distance**. Ces données de réservation sont privées chez Citiz.

## 4. Ce qui attend le relevé dans le temps
Relevé de disponibilité (`eda-citiz/collect.py`, toutes les 5 min ; réseaux Lyon, Alpes Loire et Auto-en-Velay par défaut) :
- stations les plus utilisées et taux d'indisponibilité (cible du modèle d'usage) ;
- durées d'immobilisation et durées de location estimées ;
- heures de pointe, semaine et week-end ;
- nombre réel de voitures électriques et thermiques (types de véhicules).

## 5. Données à croiser avec les stations Citiz
Dans l'énoncé du hackathon :
- GTFS des réseaux de la région : arrêts et fréquences (transport.data.gouv.fr), avec l'agrégat national des arrêts à dédoublonner.
- GBFS des vélos et trottinettes **en stations** de la région. Le free-floating (zones virtuelles de Dott, Voi, Pony, Bird, et Getaround) est exclu : ce ne sont pas des stations.
- Population au carreau de 200 m (INSEE Filosofi) et projections Omphale.
- Contours administratifs et IRIS (IGN).
- Réseau piéton (OpenStreetMap, Geofabrik) pour les isochrones à 5 min.
- Revenus, motorisation des ménages, équipements, emplois (INSEE).

Hors énoncé, à étudier (à vérifier avant usage) :
- bornes de recharge publiques (fichier consolidé IRVE, data.gouv.fr) pour la règle électrique ;
- grille communale de densité INSEE pour un meilleur type de territoire ;
- ADEME : enquête sur l'autopartage et facteurs d'émission, pour le CO2 évité ;
- aides ADVENIR et grilles tarifaires publiques de Citiz, pour le coût et le revenu ;
- gares et parkings-relais, car beaucoup de stations Citiz y sont ;
- tout autre jeu public cité dans SOURCES.md.

Chaque source utilisée doit figurer dans `SOURCES.md` avec son adresse.
