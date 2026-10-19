"""Rôle 4 - tables silver `projections` (une ligne = un département) et `projections_carreaux`.

Source : INSEE Omphale 2022, scénarios central, population basse, population haute.
Base = 2021 (millésime Filosofi 2021) ; horizon = 2030 (sujet). Chaque carreau 200 m croît au rythme
de son département : Omphale ne descend pas plus bas (hypothèse).

Sorties :
- data/bronze/omphale/*.xlsx, data/bronze/filosofi/*.zip    fichiers INSEE tels que téléchargés
- data/silver/projections.parquet                          départements du territoire
- data/silver/projections_carreaux.parquet                 carreaux du territoire (id_carreau, populations)
- data/silver/projections_qc.txt                           rapport qualité
Usage : python optim/build_projections.py [--contours data/silver/contours.parquet]
"""
import argparse
import re
import zipfile

import geopandas as gpd
import pandas as pd
import requests

import territoire as T

BASE_YEAR = 2021
HORIZON = 2030
SCENARIOS = {"central": "central", "basse": "population_basse", "haute": "population_haute"}
OMPHALE_URL = "https://www.insee.fr/fr/statistiques/fichier/6652134/projections_scenario_{}.xlsx"
FILOSOFI_URL = "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip"
IDCAR = re.compile(r"CRS(\d+)RES200mN(\d+)E(\d+)")


def download(url, path):
    if not path.exists():
        print(f"téléchargement {url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with requests.get(url, stream=True, timeout=600, headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        tmp.replace(path)
    return path


def load_omphale():
    """Population par département, scénario et année (fichier INSEE en milliers)."""
    out = []
    for scenario, slug in SCENARIOS.items():
        path = download(OMPHALE_URL.format(slug), T.BRONZE / "omphale" / f"omphale_{slug}.xlsx")
        df = pd.read_excel(path, "Population_DEP", header=5, dtype={"DEP": str})
        df = df[["DEP", "LIBDEP", f"POP_{BASE_YEAR}", f"POP_{HORIZON}"]].assign(scenario=scenario)
        out.append(df)
    df = pd.concat(out).rename(columns={"DEP": "dep", "LIBDEP": "nom_dep"})
    assert df[[f"POP_{BASE_YEAR}", f"POP_{HORIZON}"]].gt(0).all().all() and df["dep"].nunique() == 101
    wide = df.pivot(index=["dep", "nom_dep"], columns="scenario", values=[f"POP_{BASE_YEAR}", f"POP_{HORIZON}"])
    proj = pd.DataFrame({"pop_depart": wide[(f"POP_{BASE_YEAR}", "central")] * 1000,
                         "pop_2030": wide[(f"POP_{HORIZON}", "central")] * 1000})
    for scenario in SCENARIOS:
        rate = wide[(f"POP_{HORIZON}", scenario)] / wide[(f"POP_{BASE_YEAR}", scenario)] - 1
        proj["taux_croissance" if scenario == "central" else f"taux_croissance_{scenario}"] = rate
    proj["annee_depart"] = BASE_YEAR
    return proj.reset_index().astype({"pop_depart": "int64", "pop_2030": "int64"})


def load_carreaux():
    """Filosofi 2021 : id du carreau, commune, population ; centre du carreau en Lambert 93."""
    path = download(FILOSOFI_URL, T.BRONZE / "filosofi" / "Filosofi2021_carreaux_200m_csv.zip")
    parts = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            with z.open(name) as f:
                parts.append(pd.read_csv(f, usecols=["idcar_200m", "lcog_geo", "ind"],
                                         dtype={"idcar_200m": str, "lcog_geo": str}))
    df = pd.concat(parts, ignore_index=True).rename(columns={"idcar_200m": "id_carreau",
                                                             "ind": "population_2021"})
    # carreau à cheval sur plusieurs communes : lcog_geo les concatène, on garde la première
    df["commune"] = df.pop("lcog_geo").str[:5]
    df["dep"] = T.dep_of(df["commune"])
    xy = df["id_carreau"].str.extract(IDCAR).astype({0: int, 1: float, 2: float})
    parts = []
    for crs, idx in xy.groupby(0).groups.items():  # 3035 en métropole, CRS locaux pour les DROM
        # l'identifiant donne le coin sud-ouest : centre = +100 m
        pts = gpd.GeoSeries(gpd.points_from_xy(xy.loc[idx, 2] + 100, xy.loc[idx, 1] + 100), index=idx, crs=crs)
        parts.append(pts.to_crs(T.CRS_LAMBERT93))
    return gpd.GeoDataFrame(df, geometry=pd.concat(parts).sort_index(), crs=T.CRS_LAMBERT93)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contours", help="contour du territoire (table contours du Rôle 2)")
    args = ap.parse_args()

    proj_fr = load_omphale()
    communes = T.load_communes(args.contours)
    deps = sorted(set(communes["dep"]))
    proj = proj_fr[proj_fr["dep"].isin(deps)].reset_index(drop=True)

    car = load_carreaux()
    car = car[car["dep"].isin(deps)]  # préfiltre rapide avant la recoupe spatiale
    car, n_out = T.clip(car, communes.drop(columns="nom_commune").assign(nom_commune=None))
    car = car.drop(columns="nom_commune")
    rates = proj.set_index("dep")
    for scenario in SCENARIOS:
        col = "taux_croissance" if scenario == "central" else f"taux_croissance_{scenario}"
        suffix = "" if scenario == "central" else f"_{scenario}"
        # département absent d'Omphale -> vide, jamais 0
        car[f"population_{HORIZON}{suffix}"] = car["population_2021"] * (1 + car["dep"].map(rates[col]))
    car = car[["id_carreau", "commune", "dep", "population_2021", f"population_{HORIZON}",
               f"population_{HORIZON}_basse", f"population_{HORIZON}_haute", "geometry"]].reset_index(drop=True)
    assert car["id_carreau"].is_unique

    T.SILVER.mkdir(parents=True, exist_ok=True)
    proj.to_parquet(T.SILVER / "projections.parquet")
    car.to_parquet(T.SILVER / "projections_carreaux.parquet")

    # contrôle : Filosofi (ménages fiscaux) contre Omphale à l'année de base
    ctrl = proj.set_index("dep")[["nom_dep", "pop_depart", "pop_2030", "taux_croissance"]].join(
        car.groupby("dep")["population_2021"].sum().rename("filosofi_2021"))
    ctrl["ecart_filosofi_pct"] = (ctrl["filosofi_2021"] / ctrl["pop_depart"] - 1) * 100
    lines = ["Rapport qualité - tables projections et projections_carreaux", "=" * 60,
             f"Départements du territoire           : {len(proj)}",
             f"Carreaux du territoire               : {len(car)}",
             f"Carreaux du département hors contour : {n_out}",
             f"Population 2021 Filosofi / Omphale   : {car['population_2021'].sum():,.0f} / {proj['pop_depart'].sum():,.0f}",
             f"Population {HORIZON} (central, carreaux)   : {car[f'population_{HORIZON}'].sum():,.0f}",
             f"Carreaux sans projection             : {car[f'population_{HORIZON}'].isna().sum()}  (attendu : 0)",
             "Écart Filosofi / Omphale attendu ~ -4 % (Filosofi ne compte que les ménages fiscaux).",
             "", ctrl.to_string(float_format=lambda v: f"{v:,.3f}")]
    report = "\n".join(lines)
    (T.SILVER / "projections_qc.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
