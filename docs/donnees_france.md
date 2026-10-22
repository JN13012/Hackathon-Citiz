# Données France métropolitaine (Corse comprise)

Extension de la chaîne ARA à toute la France métropolitaine : mêmes nettoyages, mêmes règles
de réseau piéton (5 min à 4,5 km/h sur le réseau réel), mêmes tables. Le territoire se choisit avec
`TERRITOIRE=france` (défaut : `ara`, inchangé). Les tables France sont écrites dans `data/france/`
et ne remplacent jamais celles de l'ARA (`data/silver`, `data/gold`). Le modèle n'est pas lancé ici :
les tables sont prêtes pour lui.

## Lancer

```bash
python scripts/run_all.py --territoire france
```

Prérequis : `osmium-tool` sur la machine (Mac : `brew install osmium-tool`) ou l'image Docker du
projet (`RUNNER=docker scripts/run_regions_france.sh`, mémoire du conteneur à 8 Go au moins).
Environ 10 Go de téléchargements et 40 Go de disque. Le réseau piéton prend 5 à 9 h ; deux files en
parallèle (une locale, une Docker) divisent ce temps par deux :

```bash
scripts/run_regions_france.sh 75 53 93 44 52 28 11 &           # sur la machine
RUNNER=docker scripts/run_regions_france.sh 84 76 27 32 24 94   # dans le conteneur
```

Tout est reprenable : une étape faite est sautée, une région en échec n'arrête pas les suivantes,
et l'assemblage final refuse de publier tant qu'une région manque. Le capot de l'ordinateur doit
rester ouvert (la veille met le calcul en pause). Journal : `data/france/journaux/progression.txt`.

## Étapes

| Étape | Script | Sortie |
|---|---|---|
| OSM France (Geofabrik, MD5 vérifié) | `geo/download_osm_ara.py --france` | `data/bronze/osm_france/` |
| Contours IGN des 13 régions | `geo/download_contours_ign.py` | `silver/contours`, `territoire` |
| Arrêts : réseaux GTFS du catalogue national | `scripts/build_arrets.py --tolerer-echecs` | `silver/arrets` |
| Carreaux INSEE (IRIS IGN locaux) | `model/build_carreaux.py` | `silver/carreaux_socio` |
| Stations, projections, coûts, CO₂, gold | scripts habituels | `silver/*`, `gold/gold_offre`, `gold_candidates` |
| Découpe par région, extrait OSM régional | `geo/decouper_region.py` | `silver/regions/<code>/` |
| Réseau piéton par tuiles de 10 km, contrôle | `geo/run_reseau_ara.py`, `geo/controle_reseau.py` | `silver/regions/<code>/reseau/` |
| Raccordements, accès à 5 min, couverture | scripts ARA habituels | `gold/regions/<code>/` |
| Assemblage France | `geo/assembler_regions.py` | `gold/*` ci-dessous |

## Tables prêtes pour le modèle (`data/france/`)

Mêmes colonnes que l'ARA (voir [dictionnaire_donnees.md](dictionnaire_donnees.md)).

| Table | Effectif (22/10/2026) |
|---|---|
| `silver/carreaux_socio` | 2 298 582 carreaux, 62,6 M habitants |
| `silver/arrets` | 197 239 arrêts, 452 réseaux |
| `silver/stations_partagees` | 7 695 stations, dont 1 603 Citiz |
| `gold/gold_offre` | arrêts et stations, une ligne par point d'offre |
| `gold/candidats_couverture` | 2 298 582 candidats, 1 468 373 entièrement calculables (`utilisable_optimiseur`) |
| `gold/gold_couverture` | 4 168 457 paires candidat → carreau atteignable en 300 s |
| `gold/accessibilite/resultats/internes/paires_accessibles` | 822 557 paires carreau → offre atteignable en 300 s |
| `gold/gold_o1`, `gold/gold_o2` | indicateurs d'accès publiables, par groupe de carreaux et par offre |

Chaque carreau est rattaché à une seule région (par son centre) : aucun doublon, aucun manquant
(`gold/assemblage_regions_qc.json`). Le chemin des paires est donné par `territoire.ACCESSIBILITE`.

## Choix et limites

- **GTFS** : les jeux « Agrégat des réseaux… » (7 régions) sont écartés car ils republient des
  réseaux déjà collectés (fréquences comptées deux fois) ; un arrêt présent dans deux fichiers d'un
  même réseau est compté une fois. 19 réseaux sont en échec (sources hors ligne, dont cars et TER
  Hauts-de-France ; navettes saisonnières sans calendrier), listés dans `silver/arrets_qc.txt`.
- **Vélos** : pas de flux GBFS publié pour Orléans et Tours.
- **Corse** : Filosofi couvre environ 75 % de la population ; la région n'a aucune station partagée.
- **Limites régionales** : les offres sont gardées jusqu'à 1 km au-delà de la limite ; une offre
  frontalière n'est comptée qu'une fois dans `gold_o2`.
- **Réseau piéton, règles v3** : deux nœuds OSM distincts au même endroit sont décalés de 1 mm au
  lieu d'arrêter la construction (un seul cas en France, en Nouvelle-Aquitaine). Les tuiles en mer,
  sans voie ni habitant, sont acceptées.
- **Trait de côte** : quelques carreaux INSEE littoraux débordent les communes IGN (tolérance 200 m).
