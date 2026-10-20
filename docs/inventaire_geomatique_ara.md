# Étape 1 — Inventaire géomatique ARA

Contrôle : 2026-10-20T11:09:27.119604+02:00. Sources conservées sans correction.

| Données | Effectif | État |
|---|---:|---|
| Contours IGN ARA | 4025 communes, 5407 IRIS, 172 EPCI | Disponibles |
| Population Filosofi 2021 | 312 143 carreaux ; 7 703 555,5 habitants fiscaux | Disponible ; conserver les estimations et la bordure |
| Arrêts | 17 255 | 76 hors ARA ; 1 paire de même nom à moins de 50 m |
| Stations partagées | 1 557 | 901 vélo, 620 Citiz, 36 autres stations d’autopartage ; 5 capacités manquantes |
| Offre gold | 18 812 | 76 points hors ARA ; 1557 noms de stations remplacés par des codes commune dans le gold fourni |
| Candidats | 312 143 | IDs, populations et centres conformes aux carreaux |
| Archives OSM ARA | Auvergne + Rhône-Alpes | Téléchargées ; SHA-256 revérifiés ; réseau régional non construit |
| Réseau piéton et O1/O2 | Grenoble uniquement | Extension régionale à réaliser |

## Corrections ou décisions à traiter avant les calculs régionaux

1. Filtrer les 76 arrêts hors région dans une nouvelle livraison, en conservant les sources et la liste des exclusions.
2. Examiner la paire CHATEAUNEUF CENTRE et définir une règle de fusion qui conserve les identifiants et la provenance des fréquences.
3. Corriger la construction des noms de stations dans gold_offre (1557 libellés actuellement réduits au code commune).
4. Auditer les 12 réseaux vélo déjà présents et rechercher les réseaux fixes manquants ; ne pas assimiler stationnement vélo et station de location.
5. Conserver et signaler les 198 modes non précisés et les 1170 fréquences à zéro ; leur interprétation est à vérifier avec la source.
6. Conserver les 482 centres frontaliers : leurs polygones intersectent tous l'ARA. Ne pas supprimer leur population sur la seule position du centre.
7. Prévoir le regroupement O1 des petits effectifs à l’échelle régionale et les jointures avec les codes communaux historiques.
8. Construire le réseau régional par lots et indiquer la couverture effectivement calculée avant la restitution.

## Réseaux de vélos fixes dans la livraison existante

| Réseau | Stations |
|---|---:|
| VLS BYCOR Ouest Rhodanien | 2 |
| VLS C Vélo Clermont-Ferrand | 82 |
| VLS Cité Cycle Saint-Flour | 3 |
| VLS Libélo Valence Romans | 67 |
| VLS MontenVélo CC Pays du Mont-Blanc (CCPMB) | 23 |
| VLS Rubis'Vélo Grand Bourg Agglomération | 46 |
| VLS VELPAR CC Pays de l'Arbresle | 4 |
| VLS Velonecy Grand Annecy | 79 |
| VLS ViVélo Vichy Communauté | 8 |
| VLS Viavélo CA Villefranche Beaujolais Saône | 21 |
| VLS VéliVert Saint-Etienne | 101 |
| VLS Vélo'v Lyon | 465 |

Grenoble : 0 station vélo dans la livraison ; cela ne démontre pas l’absence de service vélo sur le terrain.

## Qualité technique

Les six tables contrôlées sont en EPSG:2154, avec des identifiants uniques et des géométries présentes, non vides et valides. Les populations sont positives et finies.
Les champs IRIS et emplois restent manquants pour respectivement 483 et 381 carreaux. Les valeurs imputées par l’INSEE restent signalées.

Le détail des contrôles et la liste des sources sont dans data/silver/inventaire_ara.json. Ce contrôle porte sur les fichiers locaux ; il ne prouve pas l’exhaustivité des réseaux de transport. La recherche de nouvelles sources est l’étape suivante.
