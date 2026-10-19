"""Territoire d'analyse du Rôle 4 : les sources sont nettoyées au niveau national, puis recoupées ici.

Changer de territoire = modifier NIVEAU / CODES (ou passer --contours) et relancer les scripts.
Contours : fichier `contours` du Rôle 2 s'il est fourni, sinon communes de geo.api.gouv.fr (cache en bronze).
"""
import json
from pathlib import Path

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "data" / "bronze"
SILVER = ROOT / "data" / "silver"
CRS_LAMBERT93 = 2154

# Territoire D1 : Auvergne-Rhône-Alpes (grande ville : Lyon), choix d'équipe du 19/10.
# Niveaux : region, departement, epci (SIREN), commune (code INSEE)
NIVEAU = "region"
CODES = ["84"]
GEO_API = "https://geo.api.gouv.fr"
FILTRE = {"region": "codeRegion", "departement": "codeDepartement", "epci": "codeEpci"}


def dep_of(code_commune):
    """Code département d'un code commune INSEE (DROM sur 3 caractères)."""
    return code_commune.str[:3].where(code_commune.str.startswith("97"), code_commune.str[:2])


def _communes_geo_api():
    cache = BRONZE / f"communes_{NIVEAU}_{'-'.join(CODES)}.geojson"
    if not cache.exists():
        features = []
        for code in CODES:
            params = {"format": "geojson", "geometry": "contour", "fields": "code,nom,codeDepartement"}
            if NIVEAU == "commune":
                url = f"{GEO_API}/communes/{code}"
            else:
                url = f"{GEO_API}/communes"
                params[FILTRE[NIVEAU]] = code
            r = requests.get(url, timeout=300, params=params)
            r.raise_for_status()
            data = r.json()
            features += data["features"] if data.get("type") == "FeatureCollection" else [data]
        if not features:
            raise SystemExit(f"Aucune commune pour {NIVEAU} {CODES}")
        BRONZE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    gdf = gpd.read_file(cache).set_crs(4326, allow_override=True)
    return gdf.rename(columns={"code": "commune", "nom": "nom_commune", "codeDepartement": "dep"})


def load_communes(contours=None):
    """Communes du territoire en Lambert 93 : commune, nom_commune, dep, geometry."""
    if contours is None:
        gdf = _communes_geo_api()
    else:  # table `contours` du Rôle 2 : code_insee, nom, niveau, geometry
        path = Path(contours)
        gdf = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        if "niveau" in gdf.columns:
            gdf = gdf[gdf["niveau"] == "commune"]
        gdf = gdf.rename(columns={"code_insee": "commune", "nom": "nom_commune"})
        gdf["dep"] = dep_of(gdf["commune"].astype(str))
    return gdf[["commune", "nom_commune", "dep", "geometry"]].to_crs(CRS_LAMBERT93)


def clip(gdf, communes):
    """Garde les points dans le territoire et ajoute commune / nom_commune (spatial)."""
    gdf = gdf.drop(columns=[c for c in ("commune", "nom_commune") if c in gdf.columns])
    out = gpd.sjoin(gdf.to_crs(CRS_LAMBERT93), communes[["commune", "nom_commune", "geometry"]],
                    how="inner", predicate="within")
    out = out[~out.index.duplicated()].drop(columns="index_right")
    return out, len(gdf) - len(out)
