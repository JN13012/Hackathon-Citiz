# EDA région Auvergne-Rhône-Alpes : sources minimales

Base : les 622 stations réelles de `eda_citiz/sorties/stations.csv`. Tout est croisé par position (Lambert 93) ou par code commune.
Les adresses et noms de colonnes viennent de ma connaissance des fichiers, **pas vérifiés depuis ici** (pas d'accès réseau aux sites INSEE et data.gouv) : les cellules affichent les colonnes trouvées pour qu'on corrige vite.

| # | Donnée | Source | Maille | Sert à | Téléchargement |
|---|---|---|---|---|---|
| 1 | Communes : contours, population, surface | geo.api.gouv.fr (`/departements/{dep}/communes?geometry=contour&format=geojson`) | Commune | Unité d'analyse des zones desservies ou vides | Automatique |
| 2 | Aires d'attraction des villes 2020 (pôle, couronne, hors aire) | INSEE, https://www.insee.fr/fr/information/4803954 | Commune | **Tester la périphérie des grandes villes** | À la main, `AAV2020_au_01-01-20XX.xlsx` |
| 3 | Grille communale de densité (7 niveaux) | INSEE, https://www.insee.fr/fr/information/6439600 | Commune | Type de territoire plus juste que les seuils de population | À la main, fichier communal `.xlsx` |
| 4 | Flux domicile-travail (base flux de mobilité) | INSEE, « Mobilités professionnelles », fichier `base-flux-mobilite-domicile-lieu-travail-20XX` | Couple de communes | Actifs, emplois, **part des actifs qui vont travailler au pôle** | À la main, zip |
| 5 | Motorisation des ménages | INSEE, recensement, base communale « Logement » `base-cc-logement-20XX`, ou à défaut « Base du dossier complet » `base-cc-dossier-complet` | Commune | Part des ménages sans voiture (variable clé de l'autopartage) | À la main, zip ou xlsx |
| 6 | Population et revenus au carreau de 200 m | INSEE Filosofi, https://www.insee.fr/fr/statistiques/8735162 (même lien que la carte) | 200 m | Habitants et niveau de vie autour de chaque station | À la main, `.gpkg` (gros) |
| 7 | Arrêts de transport en commun | data.gouv.fr, agrégat `arrets-de-transport-en-france` | Point | Arrêts autour des stations (la fréquence via GTFS viendra ensuite) | Automatique |
| 8 | Bornes de recharge publiques (IRVE consolidé) | data.gouv.fr, `fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques` | Point de charge | Borne proche, puissance : préparer la question électrique | Automatique |

Laissées pour plus tard, volontairement :
- GTFS par réseau (fréquences) : on regarde d'abord si le simple nombre d'arrêts différencie les zones.
- ADVENIR et tarifs Citiz : servent au coût, pas à l'EDA de localisation.
- Motorisation à l'IRIS : la commune suffit pour comparer communes desservies et vides ; l'IRIS servira dans les grandes villes.

Dépendances : `pip install geopandas pyogrio scipy python-calamine requests pyarrow`.
Les fichiers téléchargés vont dans `eda-citiz/donnees/` (à garder hors de Git).
