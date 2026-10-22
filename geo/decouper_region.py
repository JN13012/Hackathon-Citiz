"""Découpe les tables France métropolitaine pour une région, avant le réseau piéton régional.

Le réseau piéton et les accès à 5 min se calculent région par région (mémoire de la machine) avec
les scripts ARA (prepare_reseau_ara.py, build_reseau_ara.py…). Ce script prépare leurs entrées :
- communes et IRIS de la région, contour `territoire` (union des communes) ;
- carreaux de population dont le centre est dans la région (région la plus proche s'il tombe en
  mer) : chaque carreau est calculé une fois et une seule en France. Le code commune Filosofi ne
  convient pas : arrondissements de Paris, Lyon, Marseille et communes fusionnées depuis 2021 ;
- arrêts et stations jusqu'à --marge-offre-m au-delà de la limite, pour ne pas sous-estimer la
  desserte des carreaux frontaliers (une offre peut donc servir à deux régions) ;
- avec --osm, l'extrait OSM de la région (osmium, emprise + marge) et son manifeste sources_osm.json.

Usage : TERRITOIRE=france python geo/decouper_region.py --region 24 [--osm | --osm-seulement]
Les tables France sont lourdes (2,3 M carreaux) : découper sur la machine, et n'extraire l'OSM
(--osm-seulement, osmium) que dans le conteneur, dont la mémoire est limitée.
Sorties : data/france/silver/regions/<code>/ et data/france/bronze/osm_regions/region-<code>.osm.pbf
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import covers, prepare as prepare_geometry, union_all

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "optim"))
import territoire as T  # noqa: E402

CRS = 2154
OSM_FRANCE = T.BRONZE / "osm_france" / "france-latest.osm.pbf"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def carreaux_regions(silver, contours):
    """Région de chaque carreau (centre du carreau), calculée une fois pour la France et gardée en cache."""
    cache = silver / "carreaux_region.parquet"
    if cache.exists() and cache.stat().st_mtime > (silver / "carreaux_socio.parquet").stat().st_mtime:
        return pd.read_parquet(cache)
    communes = contours.loc[contours.niveau.eq("commune")].copy()
    dep2reg = {d: r for r, deps in T.REGIONS_METROPOLE.items() for d in deps}
    communes["region"] = T.dep_of(communes.code_insee.astype(str)).map(dep2reg)
    regions = communes.dissolve("region").reset_index()[["region", "geometry"]]
    cells = gpd.read_parquet(silver / "carreaux_socio.parquet", columns=["id_carreau", "geometry"]).to_crs(CRS)
    centres = gpd.GeoDataFrame(cells[["id_carreau"]], geometry=cells.geometry.centroid, crs=CRS)
    hit = gpd.sjoin(centres, regions, predicate="within", how="left")
    hit = hit[~hit.index.duplicated()]
    alone = hit.region.isna()
    if alone.any():  # centre en mer ou hors contour IGN : région la plus proche
        near = gpd.sjoin_nearest(centres.loc[alone], regions, how="left")
        hit.loc[alone, "region"] = near[~near.index.duplicated()].region
    out = pd.DataFrame({"id_carreau": hit.id_carreau, "region": hit.region,
                        "centre_hors_contour": alone.to_numpy()})
    out.to_parquet(cache, index=False)
    return out


def cut(region, silver, output, marge_offre_m):
    if region not in T.REGIONS_METROPOLE:
        raise ValueError(f"Région inconnue : {region}")
    departements = set(T.REGIONS_METROPOLE[region])
    contours = gpd.read_parquet(silver / "contours.parquet").to_crs(CRS)
    dep = T.dep_of(contours.code_insee.astype(str))
    communes = contours.loc[contours.niveau.eq("commune") & dep.isin(departements)]
    if communes.empty:
        raise ValueError(f"Aucune commune pour la région {region}")
    codes = set(communes.code_insee)
    iris = contours.loc[contours.niveau.eq("iris") & contours.code_insee.isin(codes)]
    boundary = union_all(communes.geometry)
    territory = gpd.GeoDataFrame({"nom": [f"Région {region}"], "geometry": [boundary]}, crs=CRS)
    zone = boundary.buffer(marge_offre_m) if marge_offre_m > 0 else boundary
    prepare_geometry(zone)

    tables = {"contours": gpd.GeoDataFrame(contours.loc[communes.index.union(iris.index)], crs=CRS),
              "territoire": territory}
    for name in ("arrets", "stations_partagees"):
        frame = gpd.read_parquet(silver / f"{name}.parquet").to_crs(CRS)
        tables[name] = frame.loc[covers(zone, frame.geometry.to_numpy())]
    population = gpd.read_parquet(silver / "carreaux_socio.parquet").to_crs(CRS)
    owner = carreaux_regions(silver, contours)
    mine = set(owner.loc[owner.region.eq(region), "id_carreau"])
    tables["carreaux_socio"] = population.loc[population.id_carreau.isin(mine)]

    output.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.reset_index(drop=True).to_parquet(output / f"{name}.parquet", index=False)
    report = {"execution_utc": datetime.now(timezone.utc).isoformat(), "region": region,
              "marge_offre_m": marge_offre_m, "communes": len(communes), "iris": len(iris),
              "arrets": len(tables["arrets"]), "stations": len(tables["stations_partagees"]),
              "carreaux": len(tables["carreaux_socio"]),
              "population": float(tables["carreaux_socio"].population.sum()),
              "surface_km2": boundary.area / 1e6, "emprise_lambert93": list(boundary.bounds)}
    (output / "decoupage_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return boundary


def source_md5():
    """MD5 Geofabrik de l'archive France (download_osm_ara.py --france, ou fichier .md5 voisin)."""
    manifest = OSM_FRANCE.parent / "sources_osm.json"
    if manifest.exists():
        return next(a["md5"] for a in json.loads(manifest.read_text())["archives"] if a["nom"] == "france-latest")
    return (OSM_FRANCE.parent / "france-latest.osm.pbf.md5").read_text().split()[0]


def extract_osm(region, boundary, osm_dir, marge_m):
    """Extrait OSM de la région (emprise + marge, chemins complets) et manifeste pour prepare_reseau_ara."""
    if not shutil.which("osmium"):
        raise ValueError("osmium-tool requis : lancer ce script dans le conteneur (docker compose run pipeline)")
    bbox = gpd.GeoSeries([boundary.buffer(marge_m).envelope], crs=CRS).to_crs(4326).total_bounds
    name = f"region-{region}"
    osm_dir.mkdir(parents=True, exist_ok=True)
    target = osm_dir / f"{name}.osm.pbf"
    stage = osm_dir / f".{name}.osm.pbf"
    subprocess.run(["osmium", "extract", "-b", ",".join(f"{v:.6f}" for v in bbox), str(OSM_FRANCE),
                    "--strategy", "complete_ways", "--overwrite", "-o", str(stage)], check=True)
    stage.replace(target)
    manifest_path = osm_dir / "sources_osm.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"archives": []}
    manifest["archives"] = [a for a in manifest["archives"] if a["nom"] != name] + [{
        "nom": name, "sha256": sha256(target), "source": str(OSM_FRANCE.relative_to(ROOT)),
        "source_md5_geofabrik": source_md5(),
        "emprise_wgs84": [round(v, 6) for v in bbox], "marge_m": marge_m,
        "execution_utc": datetime.now(timezone.utc).isoformat()}]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Extrait OSM {target.name} : {target.stat().st_size / 1e6:.0f} Mo")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, help="code région INSEE, par exemple 24")
    ap.add_argument("--marge-offre-m", type=float, default=1000, help="offres gardées au-delà de la limite")
    ap.add_argument("--osm", action="store_true", help="extraire aussi l'OSM de la région (osmium)")
    ap.add_argument("--osm-seulement", action="store_true",
                    help="extraire seulement l'OSM, d'après le territoire d'une découpe déjà faite")
    ap.add_argument("--marge-osm-m", type=float, default=3000, help="marge de l'extrait OSM autour de la région")
    args = ap.parse_args(argv)
    if T.NOM == "ara":
        ap.error("à lancer avec TERRITOIRE=france")
    output = T.SILVER / "regions" / args.region
    if args.osm_seulement:
        boundary = gpd.read_parquet(output / "territoire.parquet").to_crs(CRS).geometry.union_all()
    else:
        boundary = cut(args.region, T.SILVER, output, args.marge_offre_m)
    if args.osm or args.osm_seulement:
        extract_osm(args.region, boundary, T.BRONZE_TERRITOIRE / "osm_regions", args.marge_osm_m)


if __name__ == "__main__":
    main()
