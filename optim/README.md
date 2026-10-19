# optim — Coûts et optimiseur (Rôle 4)

- `couts.yaml` : coût par type d'équipement (Citiz, bus, vélo). Chaque valeur est sourcée dans `SOURCES.md` ou marquée « hypothèse ».
- Optimiseur sous budget (PuLP, couverture maximale) : maximise la population nouvellement desservie × croissance Omphale × usage prédit.
- Contraintes : budget, exclusion de la population déjà desservie, distance minimale entre deux nouveaux équipements.
- Coefficient d'équité réglable, courbe budget → habitants desservis, estimation du CO₂ évité.
