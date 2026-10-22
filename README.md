# Hackathon Mobility × Citiz

Outil d'aide à la décision : où implanter de nouvelles stations d'autopartage Citiz (et, plus largement, où ajouter un arrêt de bus ou une station vélo) pour toucher le plus d'habitants au moindre coût.

- **Territoire (D1)** : Auvergne-Rhône-Alpes (code région 84), avec Lyon comme grande ville (choix d'équipe du 19/10, EDA `eda-citiz/`). C'est le **cas concret**, vérifié de bout en bout.
- **Simulation** : la France métropolitaine (Corse comprise) montre que la méthode se généralise : mêmes tables et mêmes règles, écrites dans `data/france/` (voir [docs/donnees_france.md](docs/donnees_france.md)).
- **Période** : hackathon du lundi 19 au mercredi 21 octobre 2026, soutenance vendredi 23 octobre au matin.
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
| 5. Restitution et pilotage | | `ratios_co2`, application Streamlit (`eda-citiz/dashboard/`), slides, démo de secours |

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

### 1. Environnement Python

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate    |    Linux/Mac : source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.12 recommandé (celui de l'image Docker).

### 2. Données

Les dossiers `data/` ne sont pas dans Git : il faut les produire ou les récupérer.

| Besoin | Comment |
|---|---|
| Données France (simulation) | Dézipper l'archive d'équipe à la racine du dépôt : les fichiers arrivent dans `data/france/silver/` et `data/france/gold/`. Vérifier avec `MANIFESTE_SHA256.txt`. |
| Données ARA (cas concret) | `python scripts/run_all.py` (territoire par défaut : `ara`). |
| GTFS TCL (Lyon) | Compte data.grandlyon.com requis : voir « GTFS TCL (Lyon) » dans la section Docker ci-dessous (fichier `data/bronze/gtfs/tcl_lyon.zip`). |

Ne **pas** relancer `python scripts/run_all.py --territoire france` : le calcul complet prend 5 à 9 h, environ 10 Go de téléchargements et 40 Go de disque.

### 3. Contrôle qualité

```bash
python scripts/controle_qualite.py                      # ARA
TERRITOIRE=france python scripts/controle_qualite.py    # France (PowerShell : $env:TERRITOIRE = "france")
```

Résultat attendu : `TOUT EST OK (0 problème(s))`. Rapports : `data/gold/controle_qualite.txt` (ARA) et `data/france/gold/controle_qualite.txt` (France).

### 4. Application Streamlit (carte d'accès)

```bash
python -m streamlit run eda-citiz/dashboard/app.py --server.enableStaticServing true
```

À lancer depuis la racine du dépôt. L'option `--server.enableStaticServing true` affiche la chaleur en dégradé continu (sans elle, la carte se replie sur de petites cellules). L'appli lit les CSV de `eda-citiz/donnees/` (`acces_france_<région>.csv`, `offre_france.csv`, `couverture_pieton_*.csv`…), produits par les scripts de `eda-citiz/dashboard/` : voir `eda-citiz/dashboard/LISEZMOI.md`.

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

**Contours** : la première étape (`geo/download_contours_ign.py`) récupère ADMIN EXPRESS et les IRIS par le
service WFS de l'IGN, puis les prépare avec `geo/build_contours.py` (4 025 communes, 172 EPCI, 5 407 IRIS).
Un `data/silver/contours.parquet` déjà présent est conservé ; `--refresh` le reconstruit.

**GTFS TCL (Lyon)** : le serveur Grand Lyon exige désormais une authentification (HTTP 401). Avant
`run_all.py`, copier le GTFS TCL officiel partagé dans le groupe interne Teams de l'équipe (`tcl_lyon.ZIP`, 23,3 Mo,
SHA-256 `1e1ea83a…4306a0c`, valide du 19/10/2026 au 16/02/2027) en `data/bronze/gtfs/tcl_lyon.zip` :
il est réutilisé sans téléchargement. Sans ce fichier, la collecte s'arrête et indique quoi faire.

La collecte GTFS exige les contours IGN (`data/silver/contours.parquet`). Si un
réseau attendu échoue au téléchargement ou à la lecture, elle termine en erreur
et conserve les fichiers d'arrêts précédents. Les identifiants, géométries,
fréquences, contours et doublons résiduels sont contrôlés avant publication ;
les deux sorties sont d'abord écrites dans un dossier temporaire, avec retour
à la version précédente si leur remplacement échoue. `run_all.py` s'arrête à
la première erreur et exécute aussi le contrôle transport après le gold.
Ces protections ne garantissent pas l'exhaustivité des sources GBFS.

Pour reconstruire le gold à partir des tables silver déjà validées, sans
relancer les téléchargements :

```bash
docker compose run --rm pipeline python scripts/build_gold.py
docker compose run --rm pipeline python scripts/controle_qualite.py
```

**Limite connue (téléchargement INSEE).** L'étape `carreaux_socio` télécharge le fichier BPE 2025 (plus de 100 Mo). Si la connexion est coupée, le fichier reste incomplet (erreur `IncompleteRead`, puis `Parquet magic bytes not found`). Supprimer alors `data/bronze/insee/bpe_2025.parquet*` et relancer, ou y déposer le fichier téléchargé à la main sous le nom `bpe_2025.parquet`. L'étape `arrets` a été vérifiée dans Docker (17 254 arrêts avec Lyon, 0 doublon).

Voir [le fonctionnement du nettoyage et la restauration des transports](docs/nettoyage_et_protection_donnees.md).

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
python scripts/run_all.py --territoire france  # France métropolitaine -> data/france/ (docs/donnees_france.md)
python geo/download_contours_ign.py        # Rôle 2 seul : contours IGN (WFS) -> data/silver/contours.parquet
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
| Vendredi 23 octobre, matin | Soutenance |

Sources : voir `SOURCES.md`.