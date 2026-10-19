"""EDA Citiz France : où Citiz est-il présent ? (choix de la région)

Usage, depuis la racine du dépôt, avec le .venv activé :
    python eda_citiz/eda_citiz.py

Besoin : internet (flux Citiz, API geo.api.gouv.fr), pandas, numpy, matplotlib.
Sorties dans eda_citiz/sorties/ : stations.csv, par_reseau.csv, par_region.csv,
stations_par_commune.csv, stations_par_region.png. Le cache des communes est
dans eda_citiz/cache_citiz/ (relancer ne refait pas les appels déjà faits).
"""
import matplotlib
matplotlib.use("Agg")  # pas de fenêtre : on enregistre les graphiques
from pathlib import Path

ICI = Path(__file__).resolve().parent
SORTIES = ICI / "sorties"
SORTIES.mkdir(exist_ok=True)

# --- 1. Paramètres et fonctions ---
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
try:
    from IPython.display import display
except ImportError:
    def display(x):
        print(x.to_string() if hasattr(x, 'to_string') else x)

# Réseaux Citiz du catalogue MobilityData (numéro de fournisseur dans l'adresse du flux)
RESEAUX = {
    1: "Grand Est", 2: "Bourgogne-Franche-Comté", 3: "Métropole de Lyon", 4: "Gironde",
    5: "Alpes Loire", 6: "Provence", 7: "Angers", 8: "Occitanie", 9: "Grand Poitiers",
    12: "Développement (France)", 15: "Nantes", 16: "Lille Arras", 18: "La Rochelle",
    19: "Rennes Métropole", 20: "AUPA (Bayonne)", 22: "Citédia Services (Rennes)", 23: "Auto-en-Velay",
}
BASE = "https://backend.citiz.fr/public/provider/{n}/gbfs/v3.0/station_information.json"
CACHE = ICI / "cache_citiz"; CACHE.mkdir(exist_ok=True)

# Seuils de population communale pour le type de territoire (à discuter)
SEUILS = [(400_000, "1 très grande ville"), (100_000, "2 métropole"), (10_000, "3 ville moyenne"), (0, "4 petite commune / rural")]

def get_json(url):
    for essai in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hackathon-mobilite-citiz/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if essai == 2:
                raise
            time.sleep(2 ** (essai + 1))

def type_territoire(pop):
    if pd.isna(pop):
        return "inconnu"
    return next(nom for seuil, nom in SEUILS if pop >= seuil)


# --- 2. Stations de chaque réseau ---
morceaux, erreurs = [], {}
for n, nom_reseau in RESEAUX.items():
    try:
        brut = get_json(BASE.format(n=n))["data"]["stations"]
    except Exception as e:
        erreurs[nom_reseau] = str(e)[:80]
        continue
    df = pd.json_normalize(brut)
    df["nom"] = df["name"].apply(lambda x: x[0]["text"] if isinstance(x, list) and x else x).astype(str).str.strip()
    df["reseau"] = nom_reseau
    for c in ["lat", "lon", "capacity", "parking_type", "is_virtual_station"]:
        if c not in df:
            df[c] = np.nan
    morceaux.append(df[["reseau", "station_id", "nom", "lat", "lon", "capacity", "parking_type", "is_virtual_station"]])

stations = pd.concat(morceaux, ignore_index=True)
stations["ouverture_prochaine"] = stations["nom"].str.contains("ouverture prochaine", case=False)
stations = stations.dropna(subset=["lat", "lon"])
print(len(stations), "stations dans", stations["reseau"].nunique(), "réseaux")
if erreurs:
    print("Réseaux non lus :", erreurs)


# --- 3. Nombre de stations par réseau ---
par_reseau = (stations.groupby("reseau")
              .agg(stations=("station_id", "size"),
                   ouvertes=("ouverture_prochaine", lambda s: int((~s).sum())),
                   virtuelles=("is_virtual_station", lambda s: int(s.fillna(False).astype(bool).sum())))
              .sort_values("stations", ascending=False))
display(par_reseau)
par_reseau.to_csv(SORTIES / 'par_reseau.csv')


# --- 4. Rattachement à la commune ---
regions = {r["code"]: r["nom"] for r in get_json("https://geo.api.gouv.fr/regions")}

def commune(lat, lon):
    url = f"https://geo.api.gouv.fr/communes?lat={lat}&lon={lon}&fields=nom,code,population,codeDepartement,codeRegion"
    try:
        res = get_json(url)
    except Exception:
        return None
    if not res:
        return None
    c = res[0]
    return {"commune": c.get("nom"), "code_insee": c.get("code"), "population": c.get("population"),
            "departement": c.get("codeDepartement"), "region": regions.get(c.get("codeRegion"))}

stations["lat3"] = stations["lat"].astype(float).round(3)     # ~100 m : évite des appels en double
stations["lon3"] = stations["lon"].astype(float).round(3)
fichier_cache = CACHE / "communes.csv"
cache = pd.read_csv(fichier_cache) if fichier_cache.exists() else pd.DataFrame(columns=["lat3", "lon3"])
a_faire = stations[["lat3", "lon3"]].drop_duplicates().merge(cache[["lat3", "lon3"]], how="left", indicator=True)
a_faire = a_faire[a_faire["_merge"] == "left_only"][["lat3", "lon3"]]
print(len(a_faire), "positions à interroger")

def traiter(ligne):
    time.sleep(0.05)
    return {**ligne, **(commune(ligne["lat3"], ligne["lon3"]) or {})}

if len(a_faire):
    with ThreadPoolExecutor(max_workers=4) as pool:
        nouveaux = list(pool.map(traiter, a_faire.to_dict("records")))
    cache = pd.concat([cache, pd.DataFrame(nouveaux)], ignore_index=True)
    cache.to_csv(fichier_cache, index=False)

stations = stations.merge(cache, on=["lat3", "lon3"], how="left")
stations["type_territoire"] = stations["population"].apply(type_territoire)
print("Stations sans commune trouvée :", int(stations["commune"].isna().sum()))


# --- 5. Type de territoire ---
ouvertes = stations[~stations["ouverture_prochaine"]]
tableau = ouvertes.groupby(["reseau", "type_territoire"]).size().unstack(fill_value=0)
tableau["total"] = tableau.sum(axis=1)
display(tableau.sort_values("total", ascending=False))


# --- 6. Par région ---
types = ["1 très grande ville", "2 métropole", "3 ville moyenne", "4 petite commune / rural"]
reg = (ouvertes.groupby(["region", "type_territoire"])
               .agg(stations=("station_id", "size"), communes=("code_insee", "nunique"))
               .unstack(fill_value=0))
stations_reg = reg["stations"].reindex(columns=types, fill_value=0)
communes_reg = reg["communes"].reindex(columns=types, fill_value=0)
resume = stations_reg.copy()
resume["total"] = resume.sum(axis=1)
resume["types_couverts"] = (stations_reg > 0).sum(axis=1)
resume["moyennes+rural OK"] = ((stations_reg[types[2]] >= 10) & (communes_reg[types[2]] >= 3)
                               & (stations_reg[types[3]] >= 10) & (communes_reg[types[3]] >= 3))
display(resume.sort_values("total", ascending=False))

resume.to_csv(SORTIES / 'par_region.csv')


# --- 7. Graphique ---
stations_reg.assign(total=stations_reg.sum(axis=1)).sort_values("total").drop(columns="total").plot.barh(
    stacked=True, figsize=(10, 6), title="Stations Citiz par région et type de territoire")
plt.tight_layout()
plt.savefig(SORTIES / 'stations_par_region.png', dpi=120)


# --- 8. Communes les mieux équipées ---
villes = (ouvertes.groupby(["region", "commune", "type_territoire"])
                  .agg(stations=("station_id", "size"), population=("population", "first"))
                  .reset_index().sort_values("stations", ascending=False))
display(villes.head(30))
villes.to_csv(SORTIES / "stations_par_commune.csv", index=False)

stations.to_csv(SORTIES / 'stations.csv', index=False)
print('Terminé. Résultats dans', SORTIES)
