# Carte d'accès Citiz – France métropolitaine (Streamlit)

Commandes (PowerShell, depuis la racine du projet, sans activer .venv) :

    cd C:\Users\bilel\Desktop\epitech\T-HAK-700-MAR-7-1-hack-7
    py -m pip install --user --upgrade streamlit pydeck altair scipy pandas pillow
    py -m streamlit run eda-citiz\dashboard\app.py --server.enableStaticServing true

L'option --server.enableStaticServing true affiche la chaleur en dégradé continu (sans elle, la carte se replie sur de petites cellules).

Fichiers lus (CSV) dans eda-citiz\donnees\ : acces_france_<région>.csv (13 fichiers), offre_france.csv, couverture_pieton_1..4.csv, stations_croisees.csv (facultatif, stations virtuelles ARA).

Scripts (dans dashboard\) :
- calc_france.py : calcule l'accès à 5 min par carreau (réseau piéton, vol d'oiseau pour les carreaux non raccordés) depuis les Parquet gold.
- decouper_france.py : ajoute communes et aires d'attraction INSEE, découpe par région, prépare la couverture des candidats.
- lire_xlsx.py : lecteur du fichier INSEE des aires d'attraction.

Données ajoutées : `enrichir_france.py` ajoute ménages, revenu, emplois, 65+ et population 2030 aux carreaux, et produit `stations_citiz_france.csv` (stations, virtuelles, véhicules) et `tarifs_reference.csv`.
