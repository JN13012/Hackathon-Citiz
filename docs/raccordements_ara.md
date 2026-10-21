# Contrôle des raccordements ARA

Le contrôle est distinct du calcul des trajets et des indicateurs O1/O2. Il
projette chaque centre de population et chaque arrêt ou station sur le réseau
piéton construit. Aucun point source n'est déplacé, fusionné ou supprimé.

## Résultat du 20 octobre 2026

**Contrôle régional terminé sur les 791 tuiles contenant des points**, à partir
du réseau de 835 tuiles. Les identifiants, géométries et attributs source ont
été conservés, sans doublon ni point omis. O1/O2 régionaux ne sont pas calculés.

| Points | Total | Dans le seuil | Hors seuil |
|---|---:|---:|---:|
| Centres de population, seuil 150 m | 312 143 | 308 679 (98,89 %) | 3 464 |
| Arrêts et stations, seuil 50 m | 18 735 | 18 720 (99,92 %) | 15 |
| Stations vélo fixes | 901 | 901 | 0 |
| Stations Citiz | 620 | 620 | 0 |
| Stations Clem | 36 | 35 | 1 |
| Points métro | 40 | 40 | 0 |

La population source totalise 7 703 555,5 habitants fiscaux estimés. Les centres
hors seuil représentent **10 364,5 habitants estimés**, soit environ 0,135 % du
total. Cela ne démontre pas une absence de desserte : la représentation au
centre du carreau et la qualité des voies OSM peuvent expliquer certains cas.

Au total, les fichiers d'examen contiennent **5 710 centres** et **110 points
d'offre**. Ils comprennent les échecs de distance, les raccordements sur une
composante locale secondaire et les 40 points métro, même dans le seuil.
Les composantes secondaires concernent 2 298 centres et 56 points d'offre,
dont 7 Citiz et 4 vélo. Les catégories se recouvrent : ne pas additionner les
effectifs d'alertes. Aucune égalité entre composantes distinctes ni recherche
bornée par la marge n'a été rencontrée.

Les 15 points d'offre hors seuil sont **11 bus, 3 modes `autre` et 1 Clem**.
Les noms ci-dessous sont ceux de la source ; aucune correction n'est déduite
du seul éloignement :

L'[audit détaillé des 15 points](audit_15_raccordements_offre_ara.md) a ensuite
reproduit leurs positions sources et le filtre OSM : trois lieux sans aucune
référence horaire dans les GTFS, deux accès proches restreints, dix écarts de
position à confirmer. Aucun point n'a été corrigé automatiquement. Les
rapports de raccordement ci-dessus restent inchangés.

| Point | Réseau / opérateur | Distance au segment retenu |
|---|---|---:|
| BEAUMIER | citea_valence | 52,1 m |
| TROMPETTE | citea_valence | 78,4 m |
| PANETTE | citea_valence | 103,0 m |
| PONTON SEVRIER (`autre`) | sibra_annecy | 69,0 m |
| DEPOT ROMANS (`autre`) | citea_valence | 98,4 m |
| Rue de Sète | tcl_lyon | 211,5 m |
| COMPLEXE SPORTIF | velay_le_puy | 76,6 m |
| Lycée Hôtelier Vidaude | tcl_lyon | 55,5 m |
| Lycée La Xavière | tcl_lyon | 57,5 m |
| LES FERRANDS | citea_valence | 155,9 m |
| LES PIALOUX | citea_valence | 58,8 m |
| CONTON | citea_valence | 50,3 m |
| COMBE LEORAT | citea_valence | 54,0 m |
| PONTON DUINGT (`autre`) | sibra_annecy | 73,2 m |
| CARE SAMOENS - SEVE - Zoe | Clem' | 58,7 m |

Ils doivent être examinés avec les rôles transport et stations partagées :
position du point source, entrée réelle, voie manquante ou filtrée dans OSM.
Un nom de ponton ou de dépôt n'autorise pas à réattribuer un mode sans
vérifier sa source. Ne pas augmenter globalement les seuils ni déplacer les
points pour faire disparaître ces alertes.

## Entrées et exécution

- `data/silver/preparation_reseau_ara/entrees/gold_candidates.parquet` :
  312 143 centres de carreaux habités, avec leur population et tuile principale.
- `data/silver/preparation_reseau_ara/entrees/gold_offre.parquet` :
  18 735 arrêts et stations, dont 901 vélo, 620 Citiz et 36 Clem.
- `data/silver/reseau_ara_bidirectionnel/` : 835 tuiles contrôlées ;
  791 contiennent des points à raccorder, les 44 autres restent dans le réseau.

```bash
docker compose build pipeline
docker compose run --rm pipeline python geo/check_raccordements_ara.py
```

Le script ne télécharge rien, ne relance pas le pipeline des autres rôles et ne
modifie ni les entrées figées ni les fichiers actifs. Les SHA-256 du plan,
des entrées, du manifeste régional, du contrôle indépendant et des fichiers
de chaque tuile utilisée sont vérifiés. Les fichiers d'une tuile sont vérifiés
avant et après sa lecture. Le dossier final apparaît seulement après les
contrôles et la relecture des empreintes d'entrées ; aucun dossier existant
n'est remplacé. Une interruption oblige à relancer dans un dossier libre.

Un contrôle limité est possible avec `--tuiles x84_y651 x91_y645` et un autre
`--output-dir`. Il porte alors le statut `controle_partiel_termine`, même si
ces tuiles sont toutes terminées. La livraison complète porte
`controle_regional_termine` : cela signifie contrôle exécuté, sans prétendre
que tous les raccordements sont valides ou que tous les trajets sont possibles.

## Méthode et hypothèses

Chaque point est traité une seule fois, dans sa tuile principale. Le réseau
utilisé inclut la marge de 1 km, donc les voies de la tuile voisine. Les deux
arcs d'un segment OSM physique sont réunis pour la recherche de proximité.
En cas d'égalité exacte, le choix suit l'ordre `(de, vers, cle)`, compatible
avec `PairNetwork.snap`, et le nombre de segments et de composantes candidats
est conservé. Des voies superposées portant des nœuds distincts ne sont pas
connectées artificiellement.

Les paramètres du pilote sont conservés : maximum **150 m pour un centre de
population**, **50 m pour un point d'offre**, vitesse **4,5 km/h** et budget
**300 s**. La distance est celle du raccord droit au segment, le coût vaut
`distance / (4,5 / 3,6)`. Il sera ajouté au départ et à l'arrivée des futurs
trajets O1/O2. Aucun itinéraire n'est calculé à cette étape.

Statuts :

- `ok` : projection dans le seuil, coût de raccord inférieur au budget.
- `raccordement_trop_loin` : seuil dépassé ; cas conservé et signalé.
- `budget_acces_epuise` : raccord dans le seuil mais coût supérieur ou égal
  au budget ; utile si les seuils sont modifiés.

`a_examiner` est vrai si le statut n'est pas `ok`, si la composante locale
n'est pas la plus grande, si plusieurs composantes sont exactement
équidistantes, ou si le point représente un métro. Un point `ok` peut donc
rester à examiner. Tous les points de métro sont signalés : leurs coordonnées
ne prouvent pas la position d'une entrée accessible en surface.

Une composante secondaire peut représenter un chemin réellement isolé, mais
aussi une coupure de fenêtre ou une lacune OSM. Sa taille est **locale à la
tuile et sa marge**, pas régionale. Elle ne justifie ni suppression ni
raccordement forcé à une autre composante.

Le seuil de raccord doit être inférieur à la distance du point au bord de la
fenêtre ; sinon le contrôle échoue. Quand la distance au segment le plus
proche dépasse cette distance au bord, `recherche_bornee_par_marge` avertit
qu'une voie hors fenêtre pourrait être plus proche. Le rejet au seuil reste
valide, mais cette distance ne doit pas être publiée comme minimum régional
garanti.

## Fichiers livrés

Dossier `data/silver/raccordements_ara/` :

| Fichier | Usage |
|---|---|
| `raccordements_population.parquet` | Tous les centres, identifiants et populations source, projection et diagnostics |
| `raccordements_offre.parquet` | Tous les arrêts et stations avec leurs attributs source et diagnostics |
| `a_examiner_population.csv`, `.geojson` | Centres signalés, distances, statuts, coordonnées source et raccord |
| `a_examiner_offre.csv`, `.geojson` | Arrêts et stations signalés, exploitables en tableur ou QGIS |
| `raccordements_qc.json` | Effectifs, statuts, distances, population concernée, détail par mode et empreintes |

GeoParquet et coordonnées `point_x`, `point_y`, `raccord_x`, `raccord_y` en
Lambert 93, EPSG:2154. Les GeoJSON représentent les points source en EPSG:4326.
Les fichiers d'examen sont **internes** : ils conservent les petits effectifs
INSEE. Agréger avant toute diffusion grand public.

## Validation et suite

Dix tests dédiés couvrent les seuils exacts, les coûts, les lignes courbes,
les voies superposées déconnectées, les jonctions partagées, les composantes
secondaires, les alertes métro et bordure, la publication sans écrasement et
les sources corrompues ou modifiées pendant le contrôle. La suite du dépôt
comporte 93 tests, tous passés avec `osmium-tool` disponible.

Une comparaison réelle de 20 points, répartis entre les tuiles centrales de
Lyon et Grenoble et les deux catégories, valide les statuts, segments
physiques, distances et abscisses à 10⁻⁶ m près avec `PairNetwork.snap`.
Le moteur historique indexe les deux arcs : trois choix équivalents portent
sur le sens inverse du même segment, avec conversion de l'abscisse. Il ne
faut donc pas confondre une orientation d'arc différente avec une voie différente.

Contrôles après export : tous les identifiants et attributs source conservés,
coûts et distances cohérents avec les coordonnées de projection, statuts et
empreintes des sorties valides. Traces :

- `data/silver/journaux/tests_raccordements_ara.log` : 93 tests réussis.
- `data/silver/journaux/validation_raccordements_moteur.json` : comparaison au moteur.
- `data/silver/journaux/controle_exports_raccordements_ara.json` : contrôle des sorties.
- `data/silver/journaux/raccordements_ara.log` : journal d'exécution régionale.

Les alertes doivent être conservées dans les étapes suivantes. Un échec de
raccordement reste indéterminé et ne devient pas une absence de desserte.
Il faudra ensuite orchestrer les trajets population-offre avec les voisins,
dédupliquer les paires, produire leurs statuts et calculer O1/O2. Le contrôle
géométrique ne résout pas les murs, accès au bâti, étages, pentes, ni la
couverture incomplète des sources transport ou OSM.
