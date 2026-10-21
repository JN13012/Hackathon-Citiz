# Vérification de la nouvelle livraison transport

Contrôle du 20 octobre 2026 sur les fichiers de `C:\Users\Cyber\Downloads` : `arrets 2.parquet`, `gold_offre.parquet`, `gold_candidates.parquet`, `controle_qualite.txt`. Comparaison avec les fichiers du dépôt et les contours IGN ARA. Lors de cet audit, les fichiers actifs n'avaient pas été remplacés. La correction autorisée ensuite est décrite ci-dessous ; les autres sections conservent les constats sur les livraisons avant correction.

## Correction appliquée

Le générateur `scripts/build_gold.py` reprend désormais les noms source, utilise l'identifiant si un nom manque et exporte les codes commune des stations dans `code_commune`. Les codes des arrêts ne sont pas présents dans leur table source : ils restent inconnus dans cette colonne.

Les 17 178 nouveaux arrêts ont été intégrés et le gold régénéré : 18 735 offres (901 vélo, 620 Citiz, 36 Clem et 17 178 arrêts). Les 312 143 candidats ont été régénérés et vérifiés conformes à la nouvelle livraison. Les noms, identifiants, coordonnées et valeurs ont été comparés aux sources ; tous les points d'offre sont dans ARA et le contrôle qualité passe. Les cinq capacités manquantes sont conservées.

Les 126 noms Citiz dans O2 Grenoble sont corrigés et leurs codes commune ajoutés, en Parquet, CSV, GeoJSON et dans la carte HTML. Les mesures, les géométries O2, les fichiers O1 et les paires de trajets restent identiques. Les rapports conservent la provenance du routage initial dans la sauvegarde et distinguent la source des nouveaux libellés. Les 11 tests du module d'accessibilité passent ; aucun routage n'a été relancé.

Sauvegarde : `data/bronze/sauvegardes/avant_correction_noms_stations_20261020T115246/`. La livraison originale est conservée dans `data/bronze/livraisons/transport_20261020T115246/`. Le [compte rendu de correction](../data/silver/correction_noms_stations.json) contient les empreintes avant/après, les contrôles et les chemins des sauvegardes. Les fichiers présents dans Downloads restent ceux reçus du collègue.

## Corrections confirmées

| Jeu de données | Avant | Nouvelle livraison | Résultat |
|---|---:|---:|---|
| Arrêts | 17 255 | 17 178 | Identifiants uniques, géométries valides, aucun point hors de l'union des communes ARA, aucune valeur manquante, aucune paire de même nom normalisé à moins de 50 m. |
| Offre | 18 812 | 18 735 | Identifiants conformes aux sources ; 17 178 arrêts et 1 557 stations ; tous les points dans ARA. |
| Candidats | 312 143 | 312 143 | Identifiants, populations et centres conformes aux carreaux socio ; trois attributs supplémentaires conformes à leur source. |

Le passage à 17 178 arrêts se décompose ainsi :

- suppression des 76 points hors ARA ;
- remplacement des deux entrées « CHATEAUNEUF CENTRE » (`91a209f00c`, 32 passages/jour et `db7caf2077`, 24 passages/jour) par `7b40de8ccd`, 56 passages/jour ; la fréquence correspond donc à leur somme ;
- les 17 177 autres arrêts conservent leurs attributs et géométries.

La fusion de CHATEAUNEUF doit rester documentée comme règle de préparation de la source. Ce contrôle constate la transformation et la somme ; il ne vérifie pas dans les GTFS l'absence de comptage commun des courses.

L'offre contient **901 stations vélo, 620 Citiz et 36 autres stations d'autopartage**. Les identifiants des stations sont identiques à ceux de la livraison actuelle. Les fréquences des arrêts et capacités des stations correspondent aux tables silver utilisées pour la comparaison.

Les coordonnées de 1 502 stations diffèrent au niveau binaire de celles du silver local, avec un écart maximal de 0,0603 m et seulement deux écarts supérieurs à 1 mm. Ces écarts sont faibles ; conserver la provenance de chaque livraison plutôt que de considérer les fichiers comme strictement identiques.

Les candidats ajoutent `code_commune`, `faible_population` et `part_menages_sans_voiture`. Ces trois colonnes correspondent aux carreaux socio par identifiant. La population totale est inchangée : 7 703 555,5 habitants fiscaux. Les 482 centres extérieurs à l'union IGN restent des centres de carreaux frontaliers : ce contrôle ne justifie pas leur suppression.

## Points toujours à traiter

1. **Les 1 557 noms de stations dans `gold_offre` sont encore des codes commune.** Les identifiants sont corrects mais les libellés ne correspondent pas à `stations_partagees.nom`. Le script local `scripts/build_gold.py`, fonction `offre_from_stations`, remplit actuellement `nom` depuis `commune`. Corriger cette construction, prévoir le nom source vide signalé par l'audit précédent, puis régénérer le gold.
2. Cinq valeurs de capacité restent manquantes, comme dans le silver : trois Citiz et deux vélo. Le contrôle fourni les affiche mais ne les considère pas comme des erreurs bloquantes. Ne pas les remplacer par zéro.
3. Les arrêts comportent toujours 1 164 fréquences à zéro et 198 modes `autre`. Ils nécessitent une interprétation documentée, même si le seuil du contrôle qualité est respecté.
4. Les rapprochements de stations Citiz, les champs optionnels absents et la couverture des réseaux vélo restent ceux décrits dans l'audit précédent : aucune nouvelle table `stations_partagees` n'est fournie ici.
5. La colonne `reseau` contient 31 valeurs distinctes. Le nombre de **19 sources GTFS** annoncé dans Teams ne peut pas être confirmé à partir de ce seul champ ; plusieurs libellés peuvent dépendre d'une même source. Il faut un manifeste des sources pour vérifier ce nombre.

Les neuf lignes supplémentaires de contenu identique hors identifiant dans le gold restent présentes. Ne pas les supprimer automatiquement : la perte des noms peut rendre des offres distinctes indiscernables.

## Portée du « tout est OK »

Le rapport fourni est cohérent avec le filtrage et les effectifs constatés. Il valide les critères de son script. Il ne vérifie pas la justesse des noms des stations, l'exhaustivité régionale des réseaux vélo, ni les choix métier de fusion et de traitement des valeurs inconnues. Il ne présente pas de contrôle dédié des candidats ; ceux-ci ont été comparés ici aux carreaux source.

## Vérification précise des noms et de leur impact

Comparaison renouvelée entre le silver des stations, le gold actif et le nouveau gold de Downloads : les deux golds présentent le même écart sur les trois catégories.

| Type | Lignes avec code commune dans `nom` | Exemple constaté : gold → nom source |
|---|---:|---|
| Vélo | 901 sur 901 | `69243` → `Gare de Tarare` (`station_mp_TARARE:391`) |
| Citiz | 620 sur 620 | `43157` → `Le Puy en Velay - Gare` (`station_auto-en-velay:1625`) |
| Autopartage Clem' | 36 sur 36 | `69243` → `Gare SNCF - Tarare Zoe` (`station_clem-france:clem-1659`) |

Les identifiants restent uniques. Les noms sources sont renseignés pour 1 556 stations sur 1 557 ; une station vélo a un nom vide. Les 36 lignes de type `autopartage` ont toutes pour opérateur `Clem'`. Le code commune est une information valide : l'erreur consiste à l'exporter comme nom de station. Garder des colonnes distinctes pour le nom, le code commune et l'identifiant.

Une vérification a réexécuté la fonction réelle `aggregate()` de `geo/build_accessibilite.py` deux fois sur les données du pilote Grenoble et les paires de trajets déjà calculées, avec les noms d'origine puis avec les noms source. Les 323 lignes O1 sont identiques, et les 303 lignes O2 sont identiques après exclusion de la seule colonne `nom` (126 noms Citiz changent). Le routage n'a pas été relancé : il dépend des identifiants et géométries conservés, pas des noms. Cette vérification ne constitue pas une validation régionale des autres problèmes de qualité.

Le défaut de libellé gêne donc l'identification des points dans les cartes et les tableaux, sans modifier les valeurs O1/O2 du pilote vérifié. Détails : [verification_noms_stations.json](../data/silver/verification_noms_stations.json). Aucune correction des fichiers sources n'a été appliquée pendant cette vérification.

Pour Grenoble et sa marge d'accès de 375 m, les 372 identifiants d'offre restent identiques. L'écart maximal de coordonnées est inférieur à 3 × 10⁻⁹ m. Le remplacement de CHATEAUNEUF et les exclusions n'affectent donc pas cet ensemble local. O1/O2 n'ont pas été recalculés ; les empreintes d'entrées des anciens rapports restent celles des anciens fichiers.

Cette livraison ne fournit pas de réseau piéton régional supplémentaire. Les archives OSM ARA sont disponibles ; leur transformation en graphe régional reste à effectuer.

## Traces de contrôle

- [Résultats détaillés et empreintes SHA-256 des quatre fichiers](../data/silver/audit_livraison_transport_2026-10-20.json).
- [Liste des 78 anciennes entrées retirées, dont deux remplacées par une fusion](../data/silver/arrets_retires_livraison_2026-10-20.csv).
- [Audit précédent des fichiers actifs](audit_nettoyage_ara.md).

Avant intégration : préserver les anciennes versions, utiliser `arrets 2.parquet` sous le nom standard `data/silver/arrets.parquet`, puis régénérer un gold avec les noms corrigés. Les nouveaux candidats peuvent être placés dans `data/gold/gold_candidates.parquet`. Conserver le rapport fourni comme trace de cette livraison, distinct du contrôle exécuté sur la future version intégrée.
