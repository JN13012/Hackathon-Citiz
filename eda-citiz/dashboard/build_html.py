"""Construit la carte HTML autonome : surcouches de chaleur (France + régions), arrêts, stations, candidats, chiffres clés par zone.
Usage : python build_html.py DOSSIER_DONNEES DOSSIER_SORTIE"""
import sys, re, json, glob, numpy as np, pandas as pd
from pathlib import Path
from scipy import sparse
from scipy.spatial import cKDTree
from PIL import Image
D, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
VMAX = 3.0
POS_V = np.array([0, 1 / 12, 1 / 6, 1 / 3, 1.0]); POS_R = np.array([0, .2, .4, .7, 1.0])
RT = np.array([0, .1, .2, .3, .4, .55, .7, .85, 1.0])
RC = np.array([[35, 45, 205], [75, 105, 235], [115, 190, 230], [110, 200, 165], [115, 190, 100], [200, 215, 80], [245, 215, 60], [240, 140, 50], [215, 45, 35]], float)
def couleur(v):
    t = np.interp(np.clip(v / VMAX, 0, 1), POS_V, POS_R)
    return np.stack([np.interp(t, RT, RC[:, c]) for c in range(3)], -1)
def gauss(a, s):
    def k(n):
        i = np.arange(n); m = np.exp(-0.5 * ((i[:, None] - i[None, :]) / s) ** 2); return m / m.sum(1, keepdims=True)
    return k(a.shape[0]) @ a @ k(a.shape[1]).T
merc = lambda lat: np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))

df = pd.concat([pd.read_csv(f, dtype={"region": str}) for f in sorted(D.glob("acces_france_*.csv"))]).sort_values("k").reset_index(drop=True)
df["categorie"] = pd.cut(df.pop_aire, [-1, 49999, 249999, 999999, 1e12], labels=["Zone rurale", "Ville moyenne", "Grande ville", "Métropole"]).astype(str)
df.loc[df.aire == "Commune hors attraction des villes", "categorie"] = "Zone rurale"
df["nb"] = df[["n_bus", "n_tram_metro", "n_train", "n_velo", "n_autopartage", "n_citiz"]].sum(axis=1)
REGIONS = [["11", "Île-de-France"], ["24", "Centre-Val de Loire"], ["27", "Bourgogne-Franche-Comté"], ["28", "Normandie"], ["32", "Hauts-de-France"], ["44", "Grand Est"], ["52", "Pays de la Loire"],
           ["53", "Bretagne"], ["75", "Nouvelle-Aquitaine"], ["76", "Occitanie"], ["84", "Auvergne-Rhône-Alpes"], ["93", "Provence-Alpes-Côte d'Azur"], ["94", "Corse"]]
RIDX = {c: i for i, (c, _) in enumerate(REGIONS)}; df["ri"] = df.region.map(RIDX).astype(int)
print("carreaux", len(df))

def overlay(d, nom, larg_max, facteur=1.0):
    marge = max(0.02, 0.03 * max(d.lon.max() - d.lon.min(), d.lat.max() - d.lat.min()))
    lon0, lon1 = d.lon.min() - marge, d.lon.max() + marge
    la0, la1 = d.lat.min() - marge, d.lat.max() + marge
    m0, m1 = merc(la0), merc(la1); grand = max(lon1 - lon0, m1 - m0)
    W, H = int(np.clip(larg_max * (lon1 - lon0) / grand, 120, larg_max)), int(np.clip(larg_max * (m1 - m0) / grand, 120, larg_max))
    px_m = (lon1 - lon0) / W * 111320 * np.cos(np.radians(d.lat.mean()))
    s = max(1.5, facteur * max(250.0, 3 * px_m) / px_m)
    ix = np.minimum(((d.lon - lon0) / (lon1 - lon0) * W).astype(int), W - 1).to_numpy()
    iy = np.minimum(((m1 - merc(d.lat)) / (m1 - m0) * H).astype(int), H - 1).to_numpy()
    num = np.zeros((H, W)); den = np.zeros_like(num)
    np.add.at(num, (iy, ix), (d.nb * d.habitants).to_numpy()); np.add.at(den, (iy, ix), d.habitants.to_numpy())
    n, m = gauss(num, s), gauss(den, s); pres = gauss((den > 0).astype(float), s * 1.5)
    # zones peu peuplées (Corse, montagne, rural) : lissage plus large, pour un dégradé continu au lieu de petites taches isolées
    n2, m2 = gauss(num, s * 3), gauss(den, s * 3); pres2 = gauss((den > 0).astype(float), s * 4.5)
    w = np.clip(pres / 0.30, 0, 1)
    v = w * (n / np.maximum(m, 1e-12)) + (1 - w) * (n2 / np.maximum(m2, 1e-12))
    p = np.maximum(pres, np.clip(pres2 * 2.5, 0, 1))
    rgb = couleur(v); a = np.clip(p / 0.06, 0, 1) * 205
    Image.fromarray(np.dstack([rgb, a]).astype(np.uint8), "RGBA").save(OUT / f"{nom}.webp", quality=88, method=4)
    return [round(la0, 4), round(lon0, 4), round(la1, 4), round(lon1, 4)]

import os
CATS = {"Métropole": "M", "Grande ville": "G", "Ville moyenne": "V", "Zone rurale": "R"}
if os.environ.get("CAT"):       # surcouches d'une seule catégorie (France + régions), noms suffixés
    c = os.environ["CAT"]; k = CATS[c]; d0 = df[df.categorie == c]; b = {f"france_{k}": overlay(d0, f"france_{k}", 2600)}
    for r, d in d0.groupby("region"):
        b[f"r{r}_{k}"] = overlay(d, f"r{r}_{k}", 1800)
    (OUT / f"bornes_{k}.json").write_text(json.dumps(b)); print("catégorie", c, "ok"); sys.exit(0)
if os.environ.get("SANS_IMAGES"):
    over = json.loads(re.search(r"SURCOUCHES=(\{.*?\});", (OUT / "donnees.js").read_text(encoding="utf-8")).group(1))
    over = {k: v for k, v in over.items() if "_" not in k}
    for k in CATS.values():
        over.update(json.loads((OUT / f"bornes_{k}.json").read_text()))
else:
    over = {"france": overlay(df, "france", 2600)}
    for r, d in df.groupby("region"):
        over[f"r{r}"] = overlay(d, f"r{r}", 1800); print("région", r, over[f"r{r}"])

# arrêts et stations (population desservie)
o = pd.read_csv(D / "offre_france.csv")
LIB = {"bus": "b", "autre": "b", "trolleybus": "b", "tram": "t", "metro": "m", "train": "g", "velo": "v", "autopartage": "a", "funiculaire": "c", "cable_car": "c", "telepherique": "c", "ferry": "c"}
o = o[o.type.isin(LIB)]
o["c"] = o.type.map(LIB); o["p"] = o.population_desservie.fillna(0).round().astype(int)
_xy = lambda lon, lat: np.c_[np.asarray(lon) * 111320 * np.cos(np.radians(45.3)), np.asarray(lat) * 110540]
_t0 = cKDTree(_xy(df.lon, df.lat)); _cat_idx = df.categorie.map({"Métropole": 0, "Grande ville": 1, "Ville moyenne": 2, "Zone rurale": 3}).to_numpy()
_q = _t0.query(_xy(o.lon, o.lat))[1]; o["ci"] = _cat_idx[_q]; o["ri"] = df.ri.to_numpy()[_q]
pts = {c: np.c_[(o[o.c == c].lon * 1e4).round().astype(int), (o[o.c == c].lat * 1e4).round().astype(int), o[o.c == c].p, o[o.c == c].ci, o[o.c == c].ri].ravel().tolist() for c in "btmgvac"}

# stations Citiz
st = pd.read_csv(D / "stations_citiz_france.csv"); st["commune"] = st.commune.fillna("")
xy = lambda lon, lat: np.c_[np.asarray(lon) * 111320 * np.cos(np.radians(45.3)), np.asarray(lat) * 110540]
tree = cKDTree(xy(df.lon, df.lat)); st["k"] = tree.query(xy(st.lon, st.lat))[1]
st["ri"] = df.ri.to_numpy()[st.k]; st["aire"] = df.aire.to_numpy()[st.k]; st["cat"] = df.categorie.to_numpy()[st.k]
stations = [[round(r.lon, 5), round(r.lat, 5), r.nom, r.commune, int(r.virtuelle), int(r.vehicules), int(r.a_venir), ["Métropole", "Grande ville", "Ville moyenne", "Zone rurale"].index(r.cat), int(r.ri)] for r in st.itertuples()]

# candidats : 5 par aire d'au moins 50 000 habitants (réseau piéton)
c = pd.concat([pd.read_csv(f) for f in sorted(D.glob("couverture_pieton_*.csv"))])
pos = pd.Series(np.arange(len(df)), index=df.k.to_numpy())
c = c[c.candidat.isin(pos.index) & c.carreau.isin(pos.index)]
M = sparse.csr_matrix((np.ones(len(c), np.float32), (pos[c.candidat].to_numpy(), pos[c.carreau].to_numpy())), shape=(len(df),) * 2)
hab = df.habitants.to_numpy(float); base = hab * (df.n_citiz.to_numpy() == 0); gains0 = M @ base
cands = []
aires = df[df.categorie != "Zone rurale"].groupby("aire").habitants.sum().sort_values(ascending=False).index
for a in aires:
    idx = np.flatnonzero((df.aire.to_numpy() == a) & (gains0 > 0) & (hab >= 30) & (df.nb_arrets_5min.to_numpy() >= 1))
    if idx.size == 0: continue
    idx = idx[np.argsort(-gains0[idx])[:3000]]; Mc = M[idx]; rest = base.copy()
    for rang in range(1, 6):
        g = Mc @ rest; k = int(g.argmax())
        if g[k] <= 0: break
        r = df.iloc[idx[k]]; cands.append([round(r.lon, 5), round(r.lat, 5), a, r.commune, rang, int(g[k])]); rest[Mc[k].indices] = 0
print("candidats", len(cands))

# population totale (tous les carreaux, y compris ceux de moins de 11 habitants)
import pyarrow.parquet as pq
g = pq.read_table("/mnt/project-files/gold_candidates.parquet", columns=["population", "code_commune"]).to_pandas()
g["code"] = g.code_commune.map(lambda c: "75056" if c.startswith("751") and len(c) == 5 and c != "75056" else "69123" if c.startswith("6938") else "13055" if c.startswith("132") and len(c) == 5 else c)
com = pd.read_csv(D / "communes_france.csv", dtype=str)
g = g.merge(com[["code", "aire", "region"]], on="code", how="left"); g["aire"] = g.aire.fillna("Commune hors attraction des villes")
pop_aire = g.groupby("aire").population.sum()
cat_aire = lambda p: "Métropole" if p >= 1e6 else "Grande ville" if p >= 250000 else "Ville moyenne" if p >= 50000 else "Zone rurale"
pop_cat = {c: 0.0 for c in ["Métropole", "Grande ville", "Ville moyenne", "Zone rurale"]}
for a, p in pop_aire.items(): pop_cat["Zone rurale" if a == "Commune hors attraction des villes" else cat_aire(p)] += p
g["region"] = g.region.fillna(""); g["cat"] = g.aire.map(lambda a: "Zone rurale" if a == "Commune hors attraction des villes" else cat_aire(pop_aire.get(a, 0)))
pop_reg = g.groupby("region").population.sum(); pop_regcat = g.groupby(["region", "cat"]).population.sum()
POP_TOT = float(g.population.sum()); print("population totale", POP_TOT, pop_cat)

def wq(v, w, p):
    i = np.argsort(v); c = np.cumsum(np.asarray(w)[i]); return float(np.asarray(v)[i][np.searchsorted(c, p / 100 * c[-1])])
def bbw(d):
    la0, la1, lo0, lo1 = wq(d.lat, d.habitants, 10), wq(d.lat, d.habitants, 90), wq(d.lon, d.habitants, 10), wq(d.lon, d.habitants, 90)
    pa, po = max(.01, .45 * (la1 - la0)), max(.015, .45 * (lo1 - lo0)); return [round(la0 - pa, 3), round(lo0 - po, 3), round(la1 + pa, 3), round(lo1 + po, 3)]
# chiffres clés par zone
def kp(d, nst, tot=None):
    h = d.habitants.sum()
    return {"hab": int(tot if tot else h), "hab_calc": int(h), "sans": round(float((d.habitants * (d.nb == 0)).sum() / h), 4), "citiz": round(float((d.habitants * (d.nb_stations_citiz_5min > 0)).sum() / h), 4),
            "sv": round(float((d.habitants * d.part_sans_voiture).sum() / h), 4), "opt": round(float((d.habitants * d.nb).sum() / h), 2), "st": int(nst)}
zones = {"France": {"cat": "", "k": kp(df, (st.a_venir == 0).sum(), POP_TOT), "bbox": [41.2, -5.3, 51.2, 9.7]}}
for cat in ["Métropole", "Grande ville", "Ville moyenne", "Zone rurale"]:
    d = df[df.categorie == cat]; zones[cat] = {"cat": "", "k": kp(d, ((st.cat == cat) & (st.a_venir == 0)).sum(), pop_cat[cat]), "bbox": zones["France"]["bbox"]}
for a, d in df.groupby("aire"):
    if a == "Commune hors attraction des villes": continue
    q = lambda s, p: float(np.percentile(s, p))
    zones[a] = {"cat": d.categorie.iloc[0], "k": kp(d, ((st.aire == a) & (st.a_venir == 0)).sum(), pop_aire.get(a)), "bbox": bbw(d), "pop": int(pop_aire.get(a, d.habitants.sum()))}
bb = lambda d: [round(float(np.percentile(d.lat, 1)) - .02, 3), round(float(np.percentile(d.lon, 1)) - .02, 3), round(float(np.percentile(d.lat, 99)) + .02, 3), round(float(np.percentile(d.lon, 99)) + .02, 3)]
for code, nom in REGIONS:
    dr = df[df.region == code]
    zones["R" + code] = {"cat": "", "k": kp(dr, ((st.ri == RIDX[code]) & (st.a_venir == 0)).sum(), pop_reg.get(code)), "bbox": bb(dr), "nom": nom}
    for cat in CATS:
        d = dr[dr.categorie == cat]
        if len(d) < 30: continue
        zones[f"R{code}|{cat}"] = {"cat": "", "k": kp(d, ((st.ri == RIDX[code]) & (st.cat == cat) & (st.a_venir == 0)).sum(), pop_regcat.get((code, cat))), "bbox": bb(d)}
for a in list(zones):
    if "cat" in zones[a] and zones[a]["cat"] and a in df.aire.values: zones[a]["reg"] = str(df[df.aire == a].region.mode().iloc[0])
(OUT / "donnees.js").write_text("const SURCOUCHES=" + json.dumps(over) + ";\nconst ARRETS=" + json.dumps(pts, separators=(",", ":")) + ";\nconst STATIONS=" + json.dumps(stations, ensure_ascii=False, separators=(",", ":")) +
    ";\nconst CANDIDATS=" + json.dumps(cands, ensure_ascii=False, separators=(",", ":")) + ";\nconst REGIONS=" + json.dumps(REGIONS, ensure_ascii=False) + ";\nconst ZONES=" + json.dumps(zones, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
print("donnees.js", round((OUT / "donnees.js").stat().st_size / 1e6, 2), "Mo")
