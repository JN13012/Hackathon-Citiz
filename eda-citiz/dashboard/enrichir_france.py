"""Ajoute aux fichiers de l'appli France : ménages, revenu, emplois, part des 65 ans et plus, population 2030 (par carreau),
et écrit stations_citiz_france.csv (nom, commune, station virtuelle, véhicules) et tarifs_reference.csv.
Usage : python enrichir_france.py ACCES.csv(avec id_carreau) DOSSIER_REGIONS DOSSIER_DONNEES_BRUTES"""
import sys, numpy as np, pandas as pd, shapely
from pathlib import Path
from pyproj import Transformer
acces, regions, brut = sys.argv[1:4]; regions, brut = Path(regions), Path(brut)
a = pd.read_csv(acces, usecols=["id_carreau"]); a["k"] = np.arange(len(a))
so = pd.read_parquet(brut / "carreaux_socio.parquet", columns=["id_carreau", "menages", "revenu", "emplois", "part_65_plus", "nb_equipements_tous"])
pr = pd.read_parquet(brut / "projections_carreaux.parquet", columns=["id_carreau", "population_2030"])
a = a.merge(so, on="id_carreau", how="left").merge(pr, on="id_carreau", how="left")
for c in ("menages", "emplois", "nb_equipements_tous"): a[c] = a[c].fillna(0).round(1)
a["revenu"] = a["revenu"].round(0); a["part_65_plus"] = a["part_65_plus"].round(3); a["pop_2030"] = a["population_2030"].round(1)
ajout = ["menages", "revenu", "emplois", "part_65_plus", "pop_2030"]
for f in sorted(regions.glob("acces_france_*.csv")):
    d = pd.read_csv(f, dtype={"region": str}).drop(columns=[c for c in ajout if c in pd.read_csv(f, nrows=0).columns])
    d = d.merge(a[["k"] + ajout], on="k", how="left")
    d.to_csv(f, index=False); print(f.name, len(d))

s = pd.read_parquet(brut / "stations_partagees.parquet")
s = s[s.type == "citiz"].copy()
p = shapely.from_wkb(s.geometry.to_numpy())
lon, lat = Transformer.from_crs(2154, 4326, always_xy=True).transform(shapely.get_x(p), shapely.get_y(p))
if not (-6 < np.median(lon) < 10): lon, lat = shapely.get_x(p), shapely.get_y(p)
out = pd.DataFrame({"lon": lon, "lat": lat, "nom": s["nom"].to_numpy(), "commune": s["nom_commune"].astype(str).to_numpy(), "reseau": s["reseau"].to_numpy(),
                    "virtuelle": s["station_virtuelle"].fillna(False).astype(int).to_numpy(), "vehicules": s["nb_vehicules"].fillna(0).astype(int).to_numpy(),
                    "en_service": s["en_service"].fillna(True).astype(int).to_numpy()})
out.to_csv(regions / "stations_citiz_france.csv", index=False); print(out.describe().T)
t = pd.read_parquet(brut / "tarifs_citiz.parquet"); t[t.reference][["system_id", "nom", "prix_location_type"]].to_csv(regions / "tarifs_reference.csv", index=False)
