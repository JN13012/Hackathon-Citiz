# restitution — Power BI et démo (Rôle 5)

- Modèle sémantique Power BI : relations entre tables gold (clé `id_carreau`, code INSEE).
- Pages : carte O1, couche O2 (arrêts et stations cliquables), recommandations avec paramètre de budget, page « pourquoi ce lieu ».
- Vocabulaire non stigmatisant : « potentiel d'amélioration » plutôt que « mal desservi ».
- Démo de secours : vidéo de 2 min et captures d'écran.

## Nettoyage (fait)

- `ratios_co2.yaml` : ratios sourcés (enquête ADEME autopartage 2022 avec page, facteurs Impact CO₂ de l'ADEME).
- `build_ratios_co2.py` -> `data/silver/ratios_co2.parquet` et `ratios_co2_qc.txt` :
  vérifie source et page de chaque ratio, compare les facteurs au relevé Impact CO₂ (`data/bronze/ademe/`),
  et contrôle que chaque table de `data/silver` et `data/gold` figure dans `docs/dictionnaire_donnees.md`.
- CO₂ évité par une voiture en autopartage (thermique évitée) : 0,51 à 2,17 t CO₂e/an, central 1,34 t.
