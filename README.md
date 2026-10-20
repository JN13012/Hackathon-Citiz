# Hackathon Mobility × Citiz

Outil d'aide à la décision : où implanter de nouvelles stations d'autopartage Citiz (et, plus largement, où ajouter un arrêt de bus ou une station vélo) pour toucher le plus d'habitants au moindre coût.

- **Territoire (D1)** : Auvergne-Rhône-Alpes (code région 84), avec Lyon comme grande ville (choix d'équipe du 19/10, EDA `eda-citiz/`).
- **Période** : lundi 19 au mercredi 21 octobre 2026, soutenance mercredi après-midi.
- **Référence unique** : le PDF du sujet (`Hackathon Mobility`, Epitech v0.4.0).

## Objectifs

| # | Objectif | Livrable |
|---|---|---|
| O1 | Carte du nombre d'options de transport par habitant à 5 min de marche | `gold_o1` |
| O2 | Types d'arrêts et de stations, avec la population desservie | `gold_o2` |
| O3 | Modèle prédictif, coût par type d'équipement, liste priorisée sous budget | `gold_scores`, liste priorisée |

## Équipe

| Rôle | Membre | Livrable principal |
|---|---|---|
| 1. Data engineer | | `arrets`, `gold_offre`, `gold_candidates`, pipeline rejouable |
| 2. Géomatique | | `reseau_pieton`, `contours`, `gold_isochrones`, `gold_o1`, `gold_o2`, `gold_couverture` |
| 3. Data scientist | | `carreaux_socio`, `gold_features`, `gold_scores`, tableau d'évaluation |
| 4. Optimisation et coûts | | `stations_partagees`, `projections`, `couts`, liste priorisée |
| 5. Restitution et pilotage | | `ratios_co2`, Power BI, slides, démo de secours |

## Structure du dépôt

```
data/bronze/     données brutes téléchargées (exclues de Git)
data/silver/     tables propres, une par jeu de données (exclues de Git)
data/gold/       tables prêtes pour l'analyse et Power BI (exclues de Git)
geo/             réseau piéton, contours, isochrones, O1, O2
model/           variables, baseline, LightGBM, SHAP
optim/           coûts (couts.yaml) et optimiseur sous budget
restitution/     Power BI, pages carte et recommandations
slides/          présentation de soutenance
scripts/         scripts d'ingestion et de contrôle (run_all.py rejoue la chaîne)
docs/            dictionnaire de données
```

## Conventions

- **Nettoyage** (pour chaque fichier) : garder le territoire ; garder les colonnes utiles en `snake_case` ; typer chaque colonne ; tout en **Lambert 93 (EPSG:2154)** ; supprimer les doublons ; valeur manquante = cellule vide, **jamais 0**, notée dans `docs/dictionnaire_donnees.md`.
- **Tables** : nommées comme dans `docs/dictionnaire_donnees.md`, écrites en Parquet dans `data/silver/` ou `data/gold/`.
- **Git** : une branche par personne ou par tâche (`role1-arrets`, `role3-modele`…), pull request vers `main`, commits en français (`feat(arrets): ...`). Pas de données dans Git.
- **Chaque coût et chaque ratio** a une source (voir `SOURCES.md`) ou est marqué « hypothèse ».

## Installation

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate    |    Linux/Mac : source .venv/bin/activate
pip install -r requirements.txt
```

## Exécution avec Docker

Prérequis : Docker et Docker Compose v2 installés, avec le moteur Docker démarré
(Docker Desktop sous Windows/macOS). Les commandes suivantes se lancent à la racine
du dépôt ; aucune installation locale de Python n'est nécessaire.

Construire l'image, puis lancer le pipeline :

```bash
docker compose build
docker compose run --rm pipeline
```

 L'image utilise Python 3.12 sur Debian Trixie et installe les bibliothèques de `requirements.txt`,
  ainsi que `libgomp1` pour LightGBM. Le conteneur exécute

  `python scripts/run_all.py`, puis s'arrête. Les étapes actives construisent les
  arrêts, la population par carreau (`carreaux_socio`), les stations partagées,
  les projections, les coûts et les tables `gold_offre` et `gold_candidates`
  lorsque leurs entrées sont disponibles. Le téléchargement des sources nécessite
  un accès Internet. Les scripts géomatiques se lancent séparément, avec les
  commandes du guide ci-dessous.

Le dossier local `data/` est monté dans `/app/data` : les données téléchargées et les
résultats restent disponibles dans `data/bronze/`, `data/silver/` et `data/gold/`,
même après suppression du conteneur. Les données, `.venv`, `.git` et les fichiers
`.env` ne sont pas copiés dans l'image.

Pour lancer un autre script ou vérifier les dépendances sans télécharger de données :

```bash
docker compose run --rm pipeline python scripts/compter_stations_citiz.py
docker compose run --rm pipeline python scripts/build_arrets.py --help
docker compose run --rm pipeline python -m pip check
```

Le conteneur utilise par défaut l'utilisateur `1000:1000`. Sous Linux/WSL, si votre
UID ou GID est différent, adaptez l'utilisateur pour pouvoir écrire dans `data/` :

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose run --rm pipeline
```

Après une modification du code ou de `requirements.txt`, relancez
`docker compose build`. Les versions des dépendances ne sont pas encore figées :
deux constructions à des dates différentes peuvent installer des versions différentes.

## Pilote géomatique Grenoble

Les contours IGN couvrent l'Auvergne-Rhône-Alpes. Le réseau piéton et les calculs
d'accessibilité couvrent actuellement Grenoble avec ses communes voisines utiles
aux trajets. Les étapes et paramètres sont décrits dans le [guide géomatique](geo/README.md).

Avec les contours, le réseau, `carreaux_socio.parquet` et `gold_offre.parquet`
déjà présents dans `data/`, recalculer les indicateurs du pilote :

```bash
docker compose run --rm pipeline python geo/build_accessibilite.py \
  --codes-communes 38185
```

Les sorties sont dans `data/gold/accessibilite_grenoble/` : tableaux, carte HTML
et rapports de contrôle. Voir le [bilan Grenoble](docs/resultats_geomatique_grenoble.md)
pour les résultats et les limites. Les données et résultats sont partagés
séparément du code ; ils restent exclus de Git.

## Exécution

```bash
python scripts/compter_stations_citiz.py   # compte stations et véhicules des réseaux Citiz (D1)
python scripts/run_all.py                  # rejoue la chaîne bronze -> silver -> gold
python model/build_carreaux.py --help      # Rôle 3 seul : carreaux_socio (INSEE, téléchargé dans data/bronze/insee/)
python optim/build_offre_partagee.py       # Rôle 4 seul : stations partagées (idem build_projections, build_couts)
```

## Dépannage

**`SSL: CERTIFICATE_VERIFY_FAILED` au téléchargement (macOS).** Le Python installé depuis
python.org n'a pas de certificats racine pour `urllib`. Les scripts utilisent désormais ceux de
`certifi` (dans `requirements.txt`) : relancez `pip install -r requirements.txt`. Pour corriger
Python une fois pour toutes : `open "/Applications/Python 3.12/Install Certificates.command"`.
Derrière un réseau qui inspecte le HTTPS (école, entreprise), installez aussi `pip install truststore`
pour utiliser le trousseau du système.

## Points de contrôle

| Quand | Quoi |
|---|---|
| Lundi 17 h 45 | Toutes les sources chargées en bronze |
| Mardi 10 h | Tables silver livrées |
| Mardi 12 h 30 | La carte O1 s'affiche |
| Mardi 17 h 45 | Démo complète v1 |
| Mercredi 10 h | Gel du code, présentation croisée des briques |
| Mercredi après-midi | Soutenance |

Sources : voir `SOURCES.md`.
