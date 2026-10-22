"""Prépare les fichiers de l'appli France : carreaux par région, communes (noms, aires d'attraction), couverture des candidats.
Usage : python decouper_france.py ACCES.csv DOSSIER_GOLD AAV.xlsx DOSSIER_SORTIE"""
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from lire_xlsx import lire
acces, gold, aav, out = sys.argv[1:5]
out = Path(out); out.mkdir(parents=True, exist_ok=True)

rows = lire(aav, "Composition_communale")[6:]
com = pd.DataFrame([{"code": r["A"], "commune": r["B"], "aav": r["C"], "aire": r["D"], "region": r["G"]} for r in rows if "A" in r])
a = pd.read_csv(acces, dtype={"code_commune": str})
# arrondissements municipaux : Paris, Lyon, Marseille
arr = {"751": "75056", "693": "69123", "132": "13055"}
a["code"] = a["code_commune"].map(lambda c: "75056" if c.startswith("751") and len(c) == 5 and c != "75056" else
                                  "69123" if c.startswith("6938") else "13055" if c.startswith("132") and len(c) == 5 else c)
manque = ~a["code"].isin(set(com.code)); print("carreaux sans commune connue :", int(manque.sum()), a.loc[manque, "code"].unique()[:10])
a = a.merge(com, on="code", how="left")
a["aire"] = a["aire"].fillna("Commune hors attraction des villes"); a["commune"] = a["commune"].fillna("?")
dep_reg = com.assign(dep=com.code.str[:2]).groupby("dep")["region"].agg(lambda s: s.mode().iloc[0])
a["region"] = a["region"].fillna(a["code"].str[:2].map(dep_reg))               # communes fusionnées depuis : région du département
a["commune"] = np.where(a["commune"] == "?", "Commune " + a["code"], a["commune"])
a["k"] = np.arange(len(a))
a["methode"] = (a["methode"] == "réseau piéton").astype(int)          # 1 = réseau piéton, 0 = vol d'oiseau
a = a.drop(columns=["id_carreau", "code_commune", "aav"]) if False else a
# population de chaque aire d'attraction, tous carreaux confondus (y compris les petits carreaux masqués)
tout = pq.read_table(f"{gold}/gold_candidates.parquet", columns=["population", "code_commune"]).to_pandas()
tout["code"] = tout["code_commune"].map(lambda c: "75056" if c.startswith("751") and len(c) == 5 and c != "75056" else "69123" if c.startswith("6938") else "13055" if c.startswith("132") and len(c) == 5 else c)
pop_com = tout.groupby("code")["population"].sum()
aire_pop = com.assign(pop=com.code.map(pop_com).fillna(0)).groupby("aire")["pop"].sum()
a["pop_aire"] = a["aire"].map(aire_pop).fillna(0).round().astype(int)
a.loc[a["aire"] == "Commune hors attraction des villes", "pop_aire"] = 0
cols = ["k", "lon", "lat", "commune", "aire", "region", "pop_aire", "habitants", "part_sans_voiture", "methode", "n_bus", "n_tram_metro", "n_train", "n_velo",
        "n_autopartage", "n_citiz", "nb_arrets_5min", "nb_stations_citiz_5min", "nb_options_5min"]
for reg, s in a.groupby("region"):
    s[cols].to_csv(out / f"acces_france_{reg}.csv", index=False)
    print(reg, len(s), round((out / f"acces_france_{reg}.csv").stat().st_size / 1e6, 1), "Mo")
com.to_csv(out / "communes_france.csv", index=False)

# couverture à pied des candidats (utilisables, et carreaux d'au moins 11 habitants des deux côtés)
idk = pd.Series(a["k"].to_numpy(), index=a["id_carreau"])
cc = pq.read_table(f"{gold}/candidats_couverture.parquet", columns=["id_candidat", "utilisable_optimiseur"]).to_pandas()
ok = set(cc.loc[cc.utilisable_optimiseur, "id_candidat"]) & set(idk.index)
g = pq.read_table(f"{gold}/gold_couverture.parquet", columns=["id_candidat", "id_carreau", "temps_pieton_s"]).to_pandas()
g = g[(g.temps_pieton_s <= 300) & g.id_candidat.isin(ok) & g.id_carreau.isin(set(idk.index))]
p = pd.DataFrame({"candidat": idk[g.id_candidat].to_numpy(), "carreau": idk[g.id_carreau].to_numpy()}).sort_values(["candidat", "carreau"])
print("paires", len(p), "candidats", p.candidat.nunique())
bornes = np.linspace(0, len(p), 5).astype(int)
for i in range(4):
    p.iloc[bornes[i]:bornes[i + 1]].to_csv(out / f"couverture_pieton_{i + 1}.csv", index=False)
