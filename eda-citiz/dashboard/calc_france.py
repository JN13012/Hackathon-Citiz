"""Accès à 5 min à pied par carreau de 200 m, France métropolitaine.
Réseau piéton (paires_accessibles ≤ 300 s) là où le carreau est raccordé ; vol d'oiseau (308 m) sinon.
Usage : python calc_france.py DOSSIER_GOLD DOSSIER_PAIRES SORTIE"""
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq, shapely
from scipy.spatial import cKDTree
from pyproj import Transformer
gold, paires, out = sys.argv[1:4]
R = 308
t = Transformer.from_crs(2154, 4326, always_xy=True)

g = pq.read_table(f"{gold}/gold_candidates.parquet").to_pandas()
g = g[g.population >= 11].reset_index(drop=True)
pts = shapely.from_wkb(g.geometry.to_numpy())
g["x"], g["y"] = shapely.get_x(pts), shapely.get_y(pts)
g["lon"], g["lat"] = t.transform(g.x.to_numpy(), g.y.to_numpy())
g = g.drop(columns=["geometry"])
print("carreaux", len(g), "habitants", int(g.population.sum()))

cc = pq.read_table(f"{gold}/candidats_couverture.parquet", columns=["id_candidat", "statut", "utilisable_optimiseur"]).to_pandas()
cc = cc[cc.id_candidat.isin(set(g.id_carreau))]
calcule = g.id_carreau.isin(set(cc.loc[cc.statut != "non_calcule", "id_candidat"])).to_numpy()

FAM = {"bus": "n_bus", "autre": "n_bus", "tram": "n_tram_metro", "metro": "n_tram_metro", "trolleybus": "n_tram_metro", "funiculaire": "n_tram_metro",
       "cable_car": "n_tram_metro", "ferry": "n_tram_metro", "telepherique": "n_tram_metro", "train": "n_train", "velo": "n_velo",
       "autopartage": "n_autopartage", "citiz": "n_citiz"}
cols = ["n_bus", "n_tram_metro", "n_train", "n_velo", "n_autopartage", "n_citiz"]
o = pq.read_table(f"{paires}/c0f5aa9c-gold_o2.parquet", columns=["offre_id", "type", "nom", "population_desservie", "geometry"]).to_pandas()
pts = shapely.from_wkb(o.geometry.to_numpy()); o["x"], o["y"] = shapely.get_x(pts), shapely.get_y(pts)
o["lon"], o["lat"] = t.transform(o.x.to_numpy(), o.y.to_numpy())
o["fam"] = o["type"].map(FAM)
o.drop(columns=["geometry"]).to_csv(out.replace("acces", "offre"), index=False)

p = pq.read_table(f"{paires}/fcdc3518-paires_accessibles.parquet", columns=["id_carreau", "offre_id", "temps_pieton_s"]).to_pandas()
p = p[p.temps_pieton_s <= 300].merge(o[["offre_id", "fam"]], on="offre_id")
res = p.groupby(["id_carreau", "fam"]).size().unstack(fill_value=0).reindex(columns=cols, fill_value=0)
net = g[["id_carreau"]].merge(res, left_on="id_carreau", right_index=True, how="left").fillna(0)[cols].to_numpy()
crow = np.zeros((len(g), len(cols)))
idx = np.flatnonzero(~calcule)
for k, c in enumerate(cols):
    s_ = o[o.fam == c]
    if len(s_):
        crow[idx, k] = [len(v) for v in cKDTree(s_[["x", "y"]].to_numpy()).query_ball_point(g.loc[idx, ["x", "y"]].to_numpy(), R)]
vals = np.where(calcule[:, None], net, crow).astype(np.int16)
for k, c in enumerate(cols): g[c] = vals[:, k]
g["nb_arrets_5min"] = g.n_bus + g.n_tram_metro + g.n_train
g["nb_stations_citiz_5min"] = g.n_citiz
g["nb_options_5min"] = g[cols].sum(axis=1)
g["methode"] = np.where(calcule, "réseau piéton", "vol d'oiseau")
g = g.rename(columns={"population": "habitants", "part_menages_sans_voiture": "part_sans_voiture"})
g["lon"], g["lat"] = g.lon.round(5), g.lat.round(5)
keep = ["id_carreau", "lon", "lat", "code_commune", "habitants", "part_sans_voiture", "methode"] + cols + ["nb_arrets_5min", "nb_stations_citiz_5min", "nb_options_5min"]
g[keep].to_csv(out, index=False)
w = g.habitants
print("part réseau piéton", round(w[calcule].sum() / w.sum(), 3), "| options/hab", round((g.nb_options_5min * w).sum() / w.sum(), 2), "| sans option", round(w[g.nb_options_5min == 0].sum() / w.sum(), 3))
for dep, nom in [("69", "Rhône"), ("75", "Paris"), ("13", "Bouches-du-Rhône"), ("38", "Isère"), ("48", "Lozère")]:
    m = g.code_commune.str.startswith(dep)
    print(nom, int(w[m].sum()), round((g.loc[m, "nb_options_5min"] * w[m]).sum() / w[m].sum(), 2), round(w[m & (g.nb_options_5min == 0)].sum() / w[m].sum(), 2))
