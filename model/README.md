# model — Modèle prédictif (Rôle 3)

- `gold_features` : variables socio-économiques et d'intermodalité par isochrone.
- Cible par type d'équipement (Citiz : véhicules par station ; vélo : capacité ; bus : fréquence), à valider en équipe.
- Baseline fondée sur la population seule, puis LightGBM avec validation croisée **par commune ou EPCI**.
- `gold_scores` pour tous les candidats, explications SHAP, tableau d'évaluation (MAE, corrélation de rang).
- Limite à assumer à l'oral : la cible reflète l'offre existante, pas une demande mesurée.
