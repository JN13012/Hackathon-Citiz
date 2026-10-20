# Nettoyage, protection et restauration des transports

Travail réalisé le 20 octobre 2026. Aucun téléchargement GTFS n'a été relancé pour restaurer les données.

Mise à jour géomatique : les graphes piétons des **835 tuiles ARA sont maintenant
construits et contrôlés indépendamment**, avec marche bidirectionnelle. Voir
[execution_reseau_ara.md](execution_reseau_ara.md). Le présent document conserve
les constats de la restauration ; les calculs O1/O2 régionaux restent à réaliser.

## Ce qui avait été remplacé

Le pipeline avait reçu HTTP 401 pour TCL Lyon, ignoré ce réseau puis écrit `data/silver/arrets.parquet` avec 11 495 arrêts sans filtre IGN, dont 70 hors ARA. `data/gold/gold_offre.parquet` avait été régénéré avec 13 052 offres et aucun métro. Les tables de stations, candidats, projections et coûts avaient également été rejouées ; les effectifs des stations et candidats restaient respectivement 1 557 et 312 143.

**Le réseau routier de Lyon n'a pas été effacé : aucun graphe lyonnais n'avait encore été construit.** Les archives OSM Auvergne et Rhône-Alpes sont toujours présentes. Les empreintes de la population, des contours, du graphe Grenoble, du rapport réseau et de sa zone ont été vérifiées conformes aux références ; ces données n'ont pas été remplacées par la collecte GTFS.

## Protection ajoutée au collecteur et au pipeline

- Un téléchargement ou un fichier GTFS attendu en échec empêche la publication des arrêts.
- Un GTFS vide ou aucune source exploitable produit une erreur.
- Les contours sont obligatoires, chargés avant la collecte et appliqués à la livraison.
- Identifiants, noms, modes et réseaux doivent être présents ; les géométries et fréquences doivent être valides ; les doublons résiduels empêchent la publication.
- Une fréquence source manquante est rejetée avant agrégation, pour éviter qu'une somme la transforme en zéro.
- Le Parquet et son rapport sont préparés dans un dossier temporaire. Si leur publication échoue, les fichiers déjà remplacés sont restaurés.
- `run_all.py` transmet les contours, s'arrête à la première étape en erreur et ajoute le contrôle transport après le gold.

Ces protections concernent la publication du collecteur GTFS. Le pipeline entier n'est pas une transaction unique, et les téléchargements bruts déjà réussis peuvent rester en cache. Les scripts GBFS gardent leurs règles et rapports ; l'exhaustivité des réseaux partagés doit être auditée séparément.

Neuf tests simulent les échecs HTTP, lecture GTFS, source vide, contours absents, fréquence manquante, doublons résiduels et échec de publication ; ils vérifient la conservation des anciens fichiers et l'arrêt avant le gold. Un cas valide contrôle le filtrage géographique et la publication. Les sept tests du préparateur régional passent aussi : **16 tests réussis**.

La protection ne fournit pas de nouvel accès à TCL. Une future collecte complète nécessite un accès fonctionnel à ce GTFS, ou l'ensemble des sources locales appropriées. `--gtfs` remplace la liste des sources : fournir tous les réseaux souhaités, pas uniquement TCL pour une livraison ARA.

## Restauration effectuée

La version partielle et les anciens résultats ont été archivés dans `data/bronze/sauvegardes/avant_restauration_transport_20261020T140418/`.

Les arrêts ont été repris de `data/bronze/livraisons/transport_20261020T115246/arrets 2.parquet`, puis les golds reconstruits avec le générateur de noms corrigé et les tables silver sélectionnées. Ils sont conformes aux entrées régionales figées, hors colonne technique `tuile_id`.

| Données actives | État après restauration |
|---|---|
| `silver/arrets.parquet` | 17 178 arrêts ; aucun point hors ARA ni doublon résiduel selon le contrôle ; 19 réseaux attendus présents |
| `gold/gold_offre.parquet` | 18 735 offres, dont 40 métro, 901 vélo, 620 Citiz et 36 Clem ; noms et codes commune séparés |
| `gold/gold_candidates.parquet` | 312 143 candidats, conformes aux carreaux source |
| Population et contours | Conservés ; empreintes conformes |
| Réseau Grenoble | Conservé ; empreinte conforme |
| Archives OSM régionales | Présentes ; vérifiées lors de la préparation régionale |
| Graphe piéton ARA | À construire ; préparation de 835 tuiles disponible |

Le contrôle transport passe. Une capacité GBFS a changé à Grenoble : Citiz « Quai de France (8) » passe de 1 à 4. Les capacités agrégées O1 et les métadonnées O2 ont été réagrégées sur les paires existantes ; Parquet, CSV, GeoJSON et carte ont été actualisés. Les nombres d'options, fréquences, populations desservies et trajets restent identiques. La provenance des sources de routage initiales reste conservée, séparément du gold courant utilisé pour les agrégats.

Le [compte rendu de restauration](../data/silver/restauration_transport.json) contient les empreintes avant/après, la sauvegarde, les vérifications et le détail de cette actualisation.

## Ce qui reste incomplet ou à documenter

Le succès des contrôles ne prouve pas l'exhaustivité des transports régionaux. Les sources livrées restent le périmètre mesuré ; les trains ne sont pas représentés. Le réseau piéton ARA et les indicateurs régionaux restent à produire.

Les cinq capacités inconnues, les 198 modes `autre` et les 1 164 fréquences à zéro sont conservés et signalés. Une fréquence égale à zéro n'est pas automatiquement une donnée manquante. La population conserve 483 codes IRIS et 381 valeurs d'emploi manquants, ainsi que les estimations et signalements INSEE. Les doublons potentiels de stations partagées et la couverture des stations fixes restent à examiner.

## Comment automatiser le nettoyage

Chaque source garde son script et ses règles métier : GTFS pour les arrêts, GBFS pour les stations, IGN pour les contours, INSEE pour la population, OSM pour les voies piétonnes. Les données bronze restent conservées, les transformations silver doivent être reproductibles et les golds utilisent uniquement des entrées contrôlées.

Ordre à suivre : **lecture des bruts → transformation dans un dossier temporaire → contrôles bloquants et rapport des limites → publication de la livraison valide → calculs gold → contrôles de cohérence**. Une nouvelle règle de fusion ou une valeur inconnue ne doit pas être réparée par une suppression ou un zéro inventé. Les cas métier ambigus restent signalés pour décision.
