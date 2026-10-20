# Tableau de bord Citiz (Streamlit)

Remplace Power BI / Fabric. Répond aux deux premières questions du projet (la voiture sera-t-elle utilisée ? peut-elle être électrique ?) et chiffre recettes, coûts et solde de chaque station proposée.

## Installer et lancer (Windows, à la racine du projet)

```
cd C:\Users\bilel\Desktop\epitech\T-HAK-700-MAR-7-1-hack-7
.venv\Scripts\activate
python -m pip install streamlit pydeck altair scipy pandas
python -m streamlit run eda-citiz\dashboard\app.py
```

Le navigateur s'ouvre sur http://localhost:8501. Premier affichage : environ 10 secondes (calculs mis en cache ensuite).

## Fichiers lus (CSV uniquement, aucun Parquet)

Dans `eda-citiz\donnees\` :
- `powerbi\carte_acces_200m.csv` (cellule 15), obligatoire ;
- `stations_croisees.csv` et `communes_croisees.csv` (cellule 10, version CSV) ;
- `bornes_ara.csv` : bornes publiques en accès libre (19 400 points), créé automatiquement depuis `irve_consolide.csv` s'il manque ;
- `vehicle_status_*.csv` et `citiz_*_vehicle_types_*.json` (collecteur GBFS) : motorisation de la flotte, voitures par station, part réservée. Tous les fichiers `vehicle_status_*.csv` sont lus : relancer le collecteur enrichit le tableau de bord sans rien changer.

## Indicateurs clés (en haut de page)

1. Habitants nouvellement desservis (sans station Citiz à 500 m avant).
2. Locations par mois et par station (médiane).
3. Part électrifiable (et part avec une borne publique à 300 m déjà en place).
4. Solde annuel des stations proposées (recettes moins coûts).
5. Stations rentables seules ; les autres relèvent de la péréquation.

## Hypothèses

Tarifs, coûts et part d'abonnés ne sont pas publics : ils sont dans la barre latérale, modifiables, et annoncés comme hypothèses. Par défaut, la part d'abonnés est **calée** pour que les 622 stations actuelles soient, ensemble, à l'équilibre avec les tarifs et coûts saisis (interrupteur « Caler la demande sur le réseau actuel »).
