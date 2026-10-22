"""Accès à 5 min à pied par carreau de 200 m, par type d'option.
Réseau piéton (paires_accessibles_existant.csv, ≤ 300 s) là où le carreau est raccordé au réseau ; vol d'oiseau (308 m) sinon."""
import sys, pandas as pd, numpy as np
from scipy.spatial import cKDTree
src, f200, out = sys.argv[1:4]
R = 308
d = pd.read_csv(f200, usecols=["lon", "lat", "commune", "aire", "habitants"])
soc = pd.read_csv(src + "/carreaux_socio.csv", usecols=["id_carreau", "menages", "revenu", "part_menages_sans_voiture", "lon", "lat"])
j = cKDTree(soc[["lon", "lat"]].to_numpy()).query(d[["lon", "lat"]].to_numpy())[1]   # même grille : correspondance exacte
s = soc.iloc[j].reset_index(drop=True)
d["id_carreau"] = s["id_carreau"]
d["menages"] = s["menages"].round(1); d["part_sans_voiture"] = s["part_menages_sans_voiture"].round(4); d["niveau_vie"] = s["revenu"]
cc = pd.read_csv(src + "/candidats_couverture.csv", usecols=["id_candidat", "statut"])
calcule = d["id_carreau"].isin(set(cc.loc[cc.statut != "non_calcule", "id_candidat"]))
d["methode"] = np.where(calcule, "réseau piéton", "vol d'oiseau")
FAM = {"bus": "n_bus", "autre": "n_bus", "tram": "n_tram_metro", "metro": "n_tram_metro", "trolleybus": "n_tram_metro", "funiculaire": "n_tram_metro",
       "cable_car": "n_tram_metro", "ferry": "n_tram_metro", "velo": "n_velo", "autopartage": "n_autopartage", "citiz": "n_citiz"}
cols = ["n_bus", "n_tram_metro", "n_velo", "n_autopartage", "n_citiz"]
o = pd.read_csv(src + "/gold_o2.csv", usecols=["offre_id", "type", "x_l93", "y_l93"])
o["fam"] = o["type"].map(FAM)
# 1) réseau piéton
p = pd.read_csv(src + "/paires_accessibles_existant.csv", usecols=["id_carreau", "offre_id", "temps_pieton_s"])
p = p[p.temps_pieton_s <= 300].merge(o[["offre_id", "fam"]], on="offre_id")
res = p.groupby(["id_carreau", "fam"]).size().unstack(fill_value=0).reindex(columns=cols, fill_value=0)
net = d[["id_carreau"]].merge(res, left_on="id_carreau", right_index=True, how="left").fillna(0)[cols].to_numpy()
# 2) vol d'oiseau (carreaux non raccordés)
from pyproj import Transformer
xy = np.c_[Transformer.from_crs(4326, 2154, always_xy=True).transform(d.lon.values, d.lat.values)]
crow = np.zeros((len(d), len(cols)))
for k, c in enumerate(cols):
    s_ = o[o.fam == c]
    crow[:, k] = [len(v) for v in cKDTree(s_[["x_l93", "y_l93"]].to_numpy()).query_ball_point(xy, R)]
vals = np.where(calcule.to_numpy()[:, None], net, crow).astype(int)
for k, c in enumerate(cols): d[c] = vals[:, k]
d["nb_arrets_5min"] = d.n_bus + d.n_tram_metro
d["nb_stations_citiz_5min"] = d.n_citiz
d["nb_options_5min"] = d[cols].sum(axis=1)
keep = ["lon", "lat", "commune", "aire", "habitants", "menages", "part_sans_voiture", "niveau_vie", "methode"] + cols + ["nb_arrets_5min", "nb_stations_citiz_5min", "nb_options_5min"]
d[keep].to_csv(out, index=False)
w = d.habitants
print("part réseau piéton", round(w[calcule].sum() / w.sum(), 3))
for ai in ["Lyon", "Grenoble", "Saint-Étienne"]:
    m = d.aire.astype(str).str.contains(ai)
    print(ai, {c: round((d.loc[m, c] * w[m]).sum() / w[m].sum(), 2) for c in cols + ["nb_options_5min"]}, "sans option", round(w[m & (d.nb_options_5min == 0)].sum() / w[m].sum(), 2))
print("region", round((d.nb_options_5min * w).sum() / w.sum(), 2), "sans option", round(w[d.nb_options_5min == 0].sum() / w.sum(), 3))

# 3) couverture piétonne des candidats : un candidat (carreau) couvre les carreaux à ≤ 300 s à pied (indices de lignes de acces_region_200m.csv)
cu = pd.read_csv(src + "/candidats_couverture.csv", usecols=["id_candidat", "utilisable_optimiseur"])
ok_ids = set(cu.loc[cu.utilisable_optimiseur, "id_candidat"])
idx = pd.Series(np.arange(len(d)), index=d["id_carreau"])
g = pd.read_csv(src + "/gold_couverture.csv")
print("temps max couverture", g.temps_pieton_s.max())
g = g[(g.temps_pieton_s <= 300) & g.id_candidat.isin(ok_ids)]
g = g[g.id_candidat.isin(idx.index) & g.id_carreau.isin(idx.index)]
pd.DataFrame({"candidat": idx[g.id_candidat].to_numpy(), "carreau": idx[g.id_carreau].to_numpy()}).to_csv(out.replace("acces", "couverture_pieton").replace("2.csv", ".csv"), index=False)
print("paires", len(g), "candidats", g.id_candidat.nunique())
