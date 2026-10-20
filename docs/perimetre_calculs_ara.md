# Périmètre des calculs géomatiques et préparation ARA

Vérification du 20 octobre 2026, après le passage du pipeline ayant réécrit les fichiers de transport. Aucun réseau régional ou indicateur n'a été recalculé pendant cette vérification.

Une préparation régionale a ensuite été produite depuis la livraison validée, dans un dossier distinct : voir [preparation_reseau_ara.md](preparation_reseau_ara.md). Les constats ci-dessous sur la collecte partielle restent ceux des fichiers actifs ; le graphe régional reste à construire.

Mise à jour : les fichiers actifs ont ensuite été restaurés et le collecteur protégé. Les effectifs de la collecte partielle décrits plus bas sont historiques ; voir [nettoyage_et_protection_donnees.md](nettoyage_et_protection_donnees.md) pour l'état après restauration. Le graphe régional n'est toujours pas construit.

## Références relues

- Sujet officiel `hackathon_mobility-project.pdf`, pages 2 et 3 : accès aux options de mobilité à cinq minutes de marche, population desservie par arrêt/station, puis choix d'emplacements sous budget.
- `Plan de projet – Hackathon Mobility × Citiz_V5.pdf`, pages 5 et 18 : O1/O2, isochrones par carreau candidat, marquage de la desserte et matrice candidat-population.
- `PLAN.md`, sections 1, 3 et rôle 2 : territoire ARA, offre existante GTFS/GBFS et équipements candidats Citiz, bus, vélo.
- La fiche de cadrage PDF mentionne un ancien périmètre Occitanie. La décision ARA inscrite dans le dépôt et les consignes actuelles de l'équipe sont retenues pour cette vérification.
- `geo/build_accessibilite.py` : définition mathématique réellement appliquée au pilote.

## Ce que représentent les cinq minutes

Un habitant est représenté au centre de son carreau de population de 200 m. Le calcul cherche si ce centre peut rejoindre un point d'offre en au plus 300 secondes **à pied**, en suivant les voies marchables. L'hypothèse actuelle est une vitesse constante de 4,5 km/h ; les temps de raccordement au réseau sont compris dans les 300 secondes. Un seul réseau piéton sert pour tous les types d'offre.

Le point d'arrivée peut être un arrêt de bus ou de car, de tram, de métro, de trolleybus, de funiculaire, de téléphérique, de ferry, ou un autre mode réellement présent dans les GTFS. Il peut aussi être une station vélo fixe, une station Citiz ou une station Clem. La marche jusqu'à la station vélo n'est pas un trajet à vélo ; le calcul ne poursuit pas le voyage dans le véhicule et ne mesure pas les temps d'attente.

Les 198 modes `autre` restent des modes non identifiés, pas une absence de transport. Une source train serait à intégrer si la desserte ferroviaire est retenue : aucun point de type `train` n'est présent dans les fichiers contrôlés ou la livraison validée. L'exhaustivité régionale des GTFS et GBFS n'est pas établie par leurs seuls effectifs. Les vélos et trottinettes sans station restent hors du périmètre accepté par l'équipe.

## Livrables à calculer

| Livrable | Question et sortie |
|---|---|
| Réseau piéton ARA | Nœuds et voies marchables avec longueur, coût en secondes, accès et contrôles de connectivité. Construire par lots avec marges, sans perdre les trajets aux frontières des lots. |
| Relation population-offre | Une paire unique `id_carreau`, `offre_id`, `temps_pieton_s` pour chaque point accessible en 300 s ; conserver les échecs ou cas indéterminés séparément. |
| O1 | Nombre de points d'offre accessibles à un habitant représenté au centre de son carreau ; détail par type. La formule de publication est à harmoniser avec le plan V5, voir ci-dessous. |
| O2 | Pour chaque arrêt/station, somme de la population des centres accessibles, chaque carreau compté une fois pour ce point. Les populations desservies par différents points se recouvrent. |
| Desserte existante | Indicateur oui/non par carreau et par type d'équipement, utile pour identifier la population nouvellement desservie par un candidat. |
| `gold_isochrones` | Zones de cinq minutes à pied pour les candidats ; les polygones de représentation ne remplacent pas les trajets pour les comptages. |
| `gold_couverture` | Pour chaque candidat, liste des carreaux de population accessibles en cinq minutes, destinée aux variables et à l'optimiseur. Cette relation diffère de la relation population-offre existante. |

Les fréquences des transports en commun et les capacités des stations partagées sont des informations distinctes. Elles ne changent pas la règle d'accessibilité et ne doivent pas être additionnées dans une unité commune. La population source n'est pas réestimée : les calculs agrègent les valeurs livrées par le rôle 3. Les petits effectifs sont regroupés pour la publication O1, selon le seuil de 20 habitants du pilote.

## Définition O1 à harmoniser

Le sujet officiel demande un nombre d'options atteignables par habitant, sans fournir de formule détaillée. La page 5 du plan V5 décrit un nombre d'options divisé par la population. Le pilote utilise une autre définition : chaque habitant du carreau reçoit le nombre d'options accessible à son centre ; un groupe publie `somme(population_i × options_i) / somme(population_i)`.

Exemple : 100 habitants ont accès à cinq points. Le pilote donne **5 options accessibles par habitant**, tandis que le ratio du V5 donne **0,05 point par habitant**. Ces deux mesures ne doivent pas porter une définition interchangeable. Recommandation : conserver la définition du pilote, expliciter sa formule dans la restitution et harmoniser le plan avec cette décision avant la livraison régionale. Aucun choix nouveau ni changement de formule n'a été appliqué pendant cet audit.

## Constat avant protection et restauration

Les archives OSM Auvergne et Rhône-Alpes sont présentes et leurs SHA-256 ont été revérifiés. La population ARA comporte 312 143 carreaux, sans identifiant dupliqué ni géométrie invalide, et aucune population manquante. La préparation régionale `data/silver/ara/sources_ara_qc.json` et un réseau `data/silver/reseau_ara` ne sont pas présents. Le graphe livré reste celui de Grenoble ; le traitement régional à partir des PBF n'est pas implémenté.

Le journal du dernier `docker compose` montre : téléchargement du GTFS TCL Lyon refusé (HTTP 401), poursuite du pipeline sans ce réseau et construction des arrêts sans `--contours`. L'exécution s'est terminée avec un code 0 malgré cette livraison partielle. Les anciens indicateurs Grenoble ne prouvent pas la qualité de ce nouveau jeu de sources.

| Données | Livraison validée et sauvegardée | Fichiers actifs après pipeline |
|---|---:|---:|
| Arrêts | 17 178 | 11 495 |
| Points d'offre | 18 735 | 13 052 |
| Points d'offre hors ARA | 0 | 70 |
| Métro | 40 | 0 |
| Stations vélo | 901 | 901 |
| Stations Citiz | 620 | 620 |
| Stations Clem | 36 | 36 |

Les arrêts actifs ont 5 756 identifiants de la livraison validée absents et 73 identifiants nouveaux ; il ne s'agit donc pas d'une simple suppression de lignes. Le rapport des arrêts signale également une paire résiduelle de même nom à moins de 50 m. Les données détaillées de ce contrôle sont dans [etat_calculs_ara_2026-10-20.json](../data/silver/etat_calculs_ara_2026-10-20.json).

## Ordre des prochaines actions

1. Rétablir la livraison validée depuis `data/bronze/livraisons/transport_20261020T115246/`, régénérer le gold avec les noms corrigés et refaire les contrôles.
2. Fiabiliser l'orchestration avec le rôle 1 : contours explicites, vérification des réseaux attendus, conservation de la livraison valide lorsqu'une collecte est incomplète. Un échec réseau ne doit pas devenir une absence apparente de transport dans une publication régionale.
3. Fixer et documenter la formule O1 ; vérifier la couverture et le traitement des stations fixes actives avec le rôle 4.
4. Construire le réseau piéton à partir des PBF par lots, contrôler voies autorisées, composantes et trajets témoins ; les petites composantes isolées ne doivent pas être transformées en absence de population ou d'offre.
5. Valider un second pilote à Lyon, notamment métro et vélo, puis étendre les paires et O1/O2 à ARA. Livrer séparément les isochrones et la couverture des candidats pour le modèle et l'optimiseur.

Les points 1 et 2 concernent la fiabilité des entrées ; le point 4 est le travail géomatique régional restant. La création de l'application de restitution appartient au collègue et ne fait pas partie de ces actions.
