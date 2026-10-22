"""Rôle 2 - contours IGN téléchargés automatiquement, puis préparés par geo/build_contours.py.

build_contours.py lit des fichiers ADMIN EXPRESS téléchargés à la main. Ce script les récupère
par le service WFS officiel de l'IGN (data.geopf.fr, couches ADMINEXPRESS-COG.LATEST et
STATISTICALUNITS.IRIS), les enregistre en GeoPackage dans data/bronze/ign/, puis appelle
build_contours.py sans le modifier. Ainsi `run_all.py` produit data/silver/contours.parquet
sur n'importe quelle machine.

Un contours.parquet déjà présent (par exemple construit depuis les fichiers IGN du Rôle 2)
est conservé ; --refresh le reconstruit.

Usage : python geo/download_contours_ign.py [--region 84 ...] [--refresh] [--sans-iris]
Par défaut, les régions du territoire (optim/territoire.py, variable TERRITOIRE).
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_contours  # noqa: E402  (script du Rôle 2, utilisé tel quel)
sys.path.insert(0, str(ROOT / "optim"))
import territoire as T  # noqa: E402

WFS = "https://data.geopf.fr/wfs/ows"
LAYERS = {"commune": "ADMINEXPRESS-COG.LATEST:commune", "epci": "ADMINEXPRESS-COG.LATEST:epci",
          "arrondissement_municipal": "ADMINEXPRESS-COG.LATEST:arrondissement_municipal",
          "iris": "STATISTICALUNITS.IRIS:contours_iris"}
PAGE = 5000
BRONZE = ROOT / "data" / "bronze" / "ign"
SILVER = T.SILVER


def get_features(layer, cql=None):
    """Toutes les entités d'une couche WFS, page par page, en Lambert 93."""
    frames, start = [], 0
    while True:
        params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": layer,
                  "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:2154", "COUNT": PAGE, "STARTINDEX": start}
        if cql:
            params["CQL_FILTER"] = cql
        for attempt in range(4):  # le service renvoie parfois une erreur passagère
            try:
                r = requests.get(WFS, params=params, timeout=300)
                r.raise_for_status()
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                if attempt == 3:
                    raise
                print(f"   service IGN indisponible ({type(e).__name__}), nouvel essai dans {5 * 2 ** attempt} s")
                time.sleep(5 * 2 ** attempt)
        features = data.get("features", [])
        if features:
            frames.append(gpd.GeoDataFrame.from_features(features, crs=2154))
        if len(features) < PAGE:
            break
        start += PAGE
    if not frames:
        raise SystemExit(f"Aucune entité pour {layer} ({cql})")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=2154)


def download(regions, with_iris, refresh=False):
    """Couches ADMIN EXPRESS (et IRIS) des régions, enregistrées en GeoPackage dans data/bronze/ign/."""
    BRONZE.mkdir(parents=True, exist_ok=True)
    tag = regions[0] if len(regions) == 1 else T.NOM  # une région : nom de fichier ARA inchangé
    admin = BRONZE / f"admin_express_wfs_{tag}.gpkg"
    iris_cached = BRONZE / f"iris_wfs_{tag}.gpkg"
    if not refresh and admin.exists() and (not with_iris or iris_cached.exists()):
        print(f"   fichiers IGN déjà téléchargés réutilisés : {admin.name}" + (f", {iris_cached.name}" if with_iris else ""))
        return admin, iris_cached if with_iris else None
    liste = ",".join(f"'{r}'" for r in regions)
    communes = get_features(LAYERS["commune"], f"code_insee_de_la_region IN ({liste})")
    print(f"   communes : {len(communes)}")
    # EPCI de ces communes (une commune peut lister plusieurs SIREN séparés par « / »)
    sirens = set(communes["codes_siren_des_epci"].dropna().str.split("/").explode().str.strip())
    epci = get_features(LAYERS["epci"])
    epci = epci[epci["code_siren"].isin(sirens)]
    print(f"   EPCI : {len(epci)}")
    arr = get_features(LAYERS["arrondissement_municipal"])
    arr = arr[arr["code_insee_de_la_commune_de_rattach"].isin(set(communes["code_insee"]))]
    print(f"   arrondissements municipaux : {len(arr)}")
    if admin.exists():
        admin.unlink()
    communes.to_file(admin, layer="commune", driver="GPKG")
    epci.to_file(admin, layer="epci", driver="GPKG")
    arr.to_file(admin, layer="arrondissement_municipal", driver="GPKG")
    iris_path = None
    if with_iris:
        deps = sorted(communes["code_insee_du_departement"].unique())
        iris = pd.concat([get_features(LAYERS["iris"], f"code_insee LIKE '{d}%'") for d in deps], ignore_index=True)
        iris = gpd.GeoDataFrame(iris, crs=2154)
        print(f"   IRIS : {len(iris)}")
        iris_path = BRONZE / f"iris_wfs_{tag}.gpkg"
        if iris_path.exists():
            iris_path.unlink()
        iris.to_file(iris_path, layer="iris", driver="GPKG")
    return admin, iris_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", nargs="+", default=T.CODES if T.NIVEAU == "region" else ["84"],
                    help="code(s) région INSEE (84 = Auvergne-Rhône-Alpes), par défaut ceux du territoire")
    ap.add_argument("--refresh", action="store_true", help="reconstruire contours.parquet même s'il existe")
    ap.add_argument("--sans-iris", action="store_true", help="ne pas télécharger les IRIS")
    ap.add_argument("--output-dir", type=Path, default=SILVER)
    args = ap.parse_args(argv)

    target = args.output_dir / "contours.parquet"
    if target.exists() and not args.refresh:
        print(f"{target.relative_to(ROOT)} existe déjà : conservé (--refresh pour le reconstruire)")
        return 0
    print(f"Contours IGN (WFS) : région(s) {', '.join(args.region)}")
    admin, iris = download(args.region, not args.sans_iris, args.refresh)
    cmd = ["--communes", str(admin), "--communes-layer", "commune",
           "--epci", str(admin), "--epci-layer", "epci",
           "--arrondissements", str(admin), "--arrondissements-layer", "arrondissement_municipal",
           "--region", *args.region, "--output-dir", str(args.output_dir),
           "--millesime", f"ADMIN EXPRESS COG LATEST, service WFS IGN, {date.today().isoformat()}"]
    if iris:
        cmd += ["--iris", str(iris), "--iris-layer", "iris"]
    return build_contours.main(cmd)


if __name__ == "__main__":
    sys.exit(main())
