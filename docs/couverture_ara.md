# gold_couverture — relation candidat–population ARA

Cette livraison du rôle 2 donne, pour chaque emplacement candidat, les carreaux
de population dont le centre peut le rejoindre en au plus 300 secondes à pied.
Le réseau est bidirectionnel, donc le trajet candidat → population donne le
même temps que population → candidat. Il s'agit de couverture **potentielle**,
avant sélection des futures stations par l'optimiseur du rôle 4.

## Livraison mesurée le 20 octobre 2026

Calcul **terminé sur 835 tuiles**, contrôle indépendant **valide** et tables
publiées dans `data/gold/` :

| Mesure | Résultat |
|---|---:|
| Candidats et carreaux de population examinés | 312 143 chacun |
| Population fiscale source conservée | 7 703 555,5 |
| Relations accessibles dans `gold_couverture` | 563 613 |
| Relations indéterminées, table séparée | 450 826 |
| Candidats entièrement calculables | 209 953 |
| Candidats partiels | 4 728 |
| Candidats non calculables au seuil de 50 m | 97 462 |
| Auto-relations, une par candidat raccordable | 214 681 |
| Paires proches examinées, accessibles ou non | 1 616 117 |

Les inconnues sont 445 937 relations dont le candidat dépasse le seuil et
4 889 relations dont la population cible dépasse le seuil de 150 m. Aucun
segment cible n'est absent des tuiles utilisées. Les candidats non calculables
sont des centres de grille trop éloignés d'une voie selon ce modèle ; cela
ne prouve pas une absence de rue dans leur commune ou une impossibilité
d'implanter une station ailleurs dans leur carreau.

L'union des carreaux couverts par au moins un candidat raccordable représente
7 466 768,5 habitants fiscaux. Ce total est une couverture potentielle brute,
pas une population nouvellement desservie, ni un résultat sous budget.

Validation : **121 tests réussis**, contrôle des identifiants et de toutes les
relations indéterminées, **52 témoins positifs vérifiés aussi en sens inverse
et 96 témoins négatifs**, recalculés avec NetworkX. Le contrôle préliminaire
sur Lyon et Grenoble avait validé 19 positifs et 4 négatifs. Une reprise réelle
retrouve les 835 checkpoints, ne recalcule aucune tuile et conserve la même
empreinte des résultats.

Preuves dans `data/gold/couverture_ara/controle_independant.json` et
`data/silver/journaux/{couverture_ara,validation_couverture_ara,tests_couverture_ara,reprise_couverture_ara}.log`.
Les O1/O2 précédents, les sources des autres rôles et les réseaux sont conservés.

Pour partager avec l'équipe : `gold_couverture.parquet`,
`candidats_couverture.parquet`, `couverture_indeterminee.parquet`,
`gold_couverture_qc.json`, `gold_couverture_controle.json` et cette méthode.
Le CSV de synthèse est facultatif.

## Entrées et hypothèses

- Préparation figée : `data/silver/preparation_reseau_ara/`.
- Candidats : les **312 143 centres de `gold_candidates`**, issus des carreaux
  habités, et non les stations existantes. `id_candidat` reprend l'identifiant
  INSEE du carreau d'origine.
- Population cible : les mêmes 312 143 carreaux, population fiscale Filosofi
  2021 représentée au centre. `id_carreau` dans la relation désigne la cible.
- Réseau OSM régional déjà construit et contrôlé : 835 tuiles, avec marge de
  1 km ; une seule tuile propriétaire par candidat, donc pas de doublon de marge.
- Marche à **4,5 km/h**, **300 s**, raccordements rectilignes inclus aux deux
  extrémités. Raccordement maximal : **50 m pour le candidat**, comme un point
  d'offre ; **150 m pour la population**, comme pour O1/O2.

Les positions des candidats restent celles de la source. Un centre à plus de
50 m du réseau est conservé et marqué `non_calcule`, sans déplacement vers une
rue ni invention d'un emplacement de station. Un candidat calculable couvre son
propre carreau : les deux raccordements restent inclus même si les centres sont
confondus. Le temps vaut alors deux fois le coût du raccordement.

La sélection à 375 m à vol d'oiseau élimine les paires impossibles en cinq
minutes ; les paires restantes sont décidées par Dijkstra sur les voies. Les
composantes secondaires sont conservées et ne sont pas reliées artificiellement.
Les rues sont utilisables dans les deux sens, selon la décision du projet.

Ces centres sont des candidats théoriques. Ni disponibilité du terrain,
stationnement, accessibilité aux véhicules, autorisation d'implantation, ni
possibilité de créer une ligne de bus ne sont vérifiés ici. La relation est
géométrique et commune aux types d'équipement ; le rôle 4 associe ensuite les
types autorisés, leurs coûts et les autres contraintes.

## Tables et contrat de jointure

Le dossier de calcul est `data/gold/couverture_ara/`.

| Fichier dans `resultats/` | Contenu |
|---|---|
| `gold_couverture.parquet` | Une ligne par relation accessible connue : `id_candidat`, `id_carreau`, `temps_pieton_s` |
| `couverture_indeterminee.parquet` | Relations géographiquement possibles sans calcul valide : les deux identifiants et `motif` |
| `candidats_couverture.parquet` | Tous les candidats, géométrie EPSG:2154, statut et synthèse de couverture |
| `candidats_couverture.csv` | Même synthèse, sans géométrie |
| `couverture_qc.json` | Paramètres, versions, SHA-256 des sources et du code, effectifs, limites et empreintes des résultats |

La synthèse contient :

| Champ | Définition |
|---|---|
| `id_candidat`, `id_carreau_origine` | Identifiant du candidat et carreau d'origine ; valeurs identiques dans cette version |
| `code_commune`, `tuile_id`, `geometry` | Attributs de localisation de la source, sans déplacer le candidat |
| `distance_raccordement_m`, `statut_raccordement` | Distance au réseau et validation au seuil candidat de 50 m |
| `statut` | `ok` si toutes les relations proches sont déterminées ; `partiel` si une cible est indéterminée ; `non_calcule` si le candidat n'est pas raccordable |
| `population_couverte`, `nb_carreaux_couverts` | Population fiscale et nombre de carreaux couverts ; absents pour un calcul incomplet |
| `population_couverte_min`, `nb_carreaux_couverts_min` | Contribution connue pour un candidat partiel ; absente pour un candidat non calculable |
| `nb_relations_indeterminees` | Nombre de paires proches non résolues ; détails dans la table dédiée |
| `utilisable_optimiseur` | Vrai uniquement lorsque `statut == 'ok'` ; filtre de départ pour une optimisation sans données partielles |

Une absence dans `gold_couverture` représente une absence de trajet à cinq
minutes seulement si le candidat est entièrement calculable. Pour un candidat
partiel, consulter les relations indéterminées. Un échec de raccordement ne
devient jamais une couverture de population nulle.

Les petites populations restent nécessaires à la matrice de calcul pour
l'équipe projet. Cette table ne constitue pas une publication O1 individuelle :
la restitution publique O1 utilise les groupes d'au moins 20 habitants.

## Utilisation par le rôle 4

Joindre `id_candidat` à `gold_candidates.id_carreau` pour les caractéristiques
du lieu ; joindre la colonne cible `id_carreau` à `carreaux_socio.id_carreau`
pour les habitants couverts et aux projections du rôle 4 lorsqu'elles existent.
Le script de projections intégré sur main utilise désormais les mêmes
312 143 carreaux que le rôle 3. Contrôler néanmoins les identifiants et la
version effectivement livrée avant jointure ; ne pas supprimer silencieusement
les identifiants sans correspondance ni leur attribuer une croissance inventée.

Exemple de population couverte par plusieurs candidats retenus :

```python
couverture = pd.read_parquet("data/gold/gold_couverture.parquet")
candidats = gpd.read_parquet("data/gold/candidats_couverture.parquet")
valides = candidats.loc[candidats.utilisable_optimiseur, "id_candidat"]
relations = couverture.loc[couverture.id_candidat.isin(valides)]
carreaux_couverts = relations.loc[
    relations.id_candidat.isin(candidats_retenus), "id_carreau"
].unique()
population_couverte = population.set_index("id_carreau").loc[
    carreaux_couverts, "population"
].sum()
```

Il faut prendre **l'union des carreaux** avant de sommer la population : deux
candidats peuvent couvrir les mêmes habitants. Pour les **habitants gagnés**,
le rôle 4 doit aussi retirer la desserte existante selon le type d'équipement
et son objectif, à partir des relations population–offre O1/O2 et de leurs
statuts. `gold_couverture` ne fait aucune soustraction de service existant et
ne produit ni classement, ni coûts, ni score prédictif, ni sélection sous budget.

## Exécution et reprise

Avec les dépendances du projet installées :

```bash
python geo/build_couverture.py --workers 2
# Après interruption, ou pour vérifier un calcul déjà terminé :
python geo/build_couverture.py --workers 2 --reprendre
# Contrôle indépendant et publication des tables dans data/gold/ :
python geo/validate_couverture.py --publier-dans data/gold
```

Les entrées O1/O2, les fichiers des autres rôles et le réseau ne sont pas
modifiés. Le script réutilise les raccordements contrôlés et route une tuile
à la fois en matrice creuse SciPy. Deux processus se partagent les tuiles ;
aucun graphe NetworkX régional n'est chargé.

La publication copie les trois Parquet et le CSV de synthèse à la racine de
`data/gold/`, avec `gold_couverture_qc.json` et `gold_couverture_controle.json`.
Elle vérifie les empreintes et refuse de remplacer un fichier de contenu
différent. Sans `--publier-dans`, le contrôle reste dans le dossier de calcul.

`--tuiles x84_y652 x91_y645` limite un essai à Lyon et Grenoble. Le résultat
reste explicitement partiel et aucune table régionale finale n'est publiée.
`progression.json` indique l'avancement. Chaque tuile de `tuiles/<id>/` a ses
Parquet et son reçu signé ; une reprise vérifie toutes les empreintes. Modifier
le code producteur, les paramètres ou les sources exige un autre dossier
`--output-dir`. Un verrou interdit deux écritures simultanées. Les tuiles et
`resultats/` sont publiés par renommage après écriture complète.

Le contrôle indépendant vérifie les identifiants, doubles comptes, temps,
borne à vol d'oiseau, statuts, auto-couverture et sommes de population. Il
reconstitue toutes les paires géographiques pour vérifier qu'aucune inconnue
n'est perdue. Des trajets positifs et négatifs sont recalculés avec NetworkX,
incluant les départements, frontières de tuiles et le sens inverse.

Cette matrice ne remplace pas les polygones `gold_isochrones` : les polygones
servent à représenter les zones, les relations routées servent aux comptages.
