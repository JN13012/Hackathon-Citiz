# Modèles de potentiel : résultats (21/10/2026)

Scripts : `model/build_features.py` (table `gold_features`, 312 143 carreaux × 61 colonnes) puis `model/train.py`.
Validation croisée **par EPCI** (5 plis, 172 EPCI) : une intercommunalité n'est jamais à la fois en entraînement et en test. Métriques calculées pli par pli puis moyennées.

## Résultats sur toute l'ARA

| Type | Cible | LightGBM | XGBoost (contrôle) | Référence population | Référence pop. × sans voiture |
|---|---|---|---|---|---|
| Citiz | station à moins de 200 m (1 450 carreaux) | aire PR **0,29**, top 1 % : **57 %** | 0,28 / 60 % | 0,19 / 47 % | 0,22 / 45 % |
| Vélo | station à moins de 200 m (2 165) | 0,31 / 48 % | 0,30 / 48 % | 0,29 / 45 % | 0,31 / 47 % |
| Bus | log des passages par jour de l'arrêt proche (30 308) | Spearman **0,66** | 0,66 | 0,60 | 0,58 |

*Aire PR* : aire sous la courbe précision-rappel (le hasard vaut ≈ 0,005). *Top 1 %* : part des stations existantes retrouvées dans le 1 % de carreaux les mieux notés d'un EPCI jamais vu.

## Lecture
- **Citiz** : le modèle bat nettement les deux références (+32 % d'aire PR, +10 points dans le top 1 %). Il les bat aussi **hors des grandes villes** : petites villes-centres (aire PR 0,17 contre 0,08), villes moyennes (0,41 contre 0,26), couronnes des grandes villes (0,19 contre 0,05). Seule exception, le rural hors aire d'attraction (26 positifs) : aucun modèle n'y apprend rien, ni le nôtre ni les références. C'est une limite à dire, et une raison de garder un quota pour le rural dans l'optimiseur.
- **Vélo** : le modèle **ne bat pas** la référence « population × ménages sans voiture ». On le dit au jury ; l'optimiseur peut utiliser la référence pour le vélo.
- **Bus** : gain modeste mais réel (0,66 contre 0,60).
- **XGBoost** donne les mêmes résultats que LightGBM : le résultat ne dépend pas de l'algorithme.
- Variables les plus utiles pour Citiz (SHAP) : emplois, équipements, santé et commerces à 400 m, ménages sans voiture, taille de l'aire d'attraction, distance à une gare. Le modèle trouve donc des **centralités** (lieux d'activité), pas seulement de la densité de population.

## Choix et limites
- Premier essai (arbres de 31 feuilles, positifs repondérés) : **battu par la référence population**. Les arbres apprenaient par cœur les villes vues. Le modèle retenu est volontairement simple (7 feuilles, 500 carreaux minimum par feuille).
- Les bornes de recharge sont **retirées du modèle Citiz** : une partie a été posée pour les stations Citiz (causalité inversée). Elles servent seulement à la règle électrique. Le résultat n'a presque pas bougé (0,30 → 0,29).
- La cible Citiz et vélo dit **où les opérateurs se sont installés**, pas si les stations sont rentables. Le score classe des lieux, ce n'est pas une probabilité de succès.

## Sorties
- `data/gold/gold_scores.csv` : `id_candidat`, `type` (citiz, velo, bus), `score`, `rang_pct`. C'est le format lu par `optim/optimise.py` (fonction des scores du Rôle 3), qui attend pour l'instant du Parquet.
- `model/evaluation_modele.csv` : toutes les métriques, avec écart-type entre plis et détail par territoire.
- `model/importance_variables.csv` : importance SHAP par type.
