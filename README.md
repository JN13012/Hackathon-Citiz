# Hackathon Mobility × Citiz

Outil d'aide à la décision : où implanter de nouvelles stations d'autopartage Citiz (et, plus largement, où ajouter un arrêt de bus ou une station vélo) pour toucher le plus d'habitants au moindre coût.

- **Territoire (D1)** : Auvergne-Rhône-Alpes, avec Lyon comme grande ville (choix d'équipe du 19/10, EDA `eda-citiz/`).
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

## Exécution

```bash
python scripts/compter_stations_citiz.py   # compte stations et véhicules des réseaux Citiz (D1)
python scripts/run_all.py                  # rejoue la chaîne bronze -> silver -> gold
python optim/build_offre_partagee.py       # Rôle 4 seul : stations partagées (idem build_projections, build_couts)
```

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
