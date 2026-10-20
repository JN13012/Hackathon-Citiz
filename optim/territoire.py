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


FIELDS = "code,nom,codeDepartement,codeEpci,codeRegion"


def _communes_geo_api():
    cache = BRONZE / f"communes_{NIVEAU}_{'-'.join(CODES)}.geojson"
    if cache.exists() and "codeEpci" not in cache.read_text(encoding="utf-8")[:5000]:
        cache.unlink()  # ancien cache sans EPCI ni région
    if not cache.exists():
        features = []
        for code in CODES:
            params = {"format": "geojson", "geometry": "contour", "fields": FIELDS}
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
    return gdf.rename(columns={"code": "commune", "nom": "nom_commune", "codeDepartement": "dep",
                               "codeEpci": "epci", "codeRegion": "region"})


def load_communes(contours=None):
    """Communes du territoire en Lambert 93 : commune, nom_commune, dep, epci, region, geometry."""
    if contours is None:
        gdf = _communes_geo_api()
    else:  # table `contours` du Rôle 2 : code_insee, nom, niveau, geometry
        path = Path(contours)
        gdf = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        if "niveau" in gdf.columns:
            gdf = gdf[gdf["niveau"] == "commune"]
        gdf = gdf.rename(columns={"code_insee": "commune", "nom": "nom_commune"})
        gdf["dep"] = dep_of(gdf["commune"].astype(str))
    gdf = gdf.reindex(columns=["commune", "nom_commune", "dep", "epci", "region", "geometry"])
    return gdf.set_geometry("geometry").to_crs(CRS_LAMBERT93)


def area_codes(communes):
    """Codes INSEE du territoire par niveau, au format du champ covered_area de transport.data.gouv.fr.
    Un niveau inconnu (ex. contour du Rôle 2 sans EPCI) reste vide : on ne filtre pas sur ce niveau."""
    return {"commune": set(communes["commune"].dropna()), "departement": set(communes["dep"].dropna()),
            "epci": set(communes["epci"].dropna()), "region": set(communes["region"].dropna())}


def covers(covered_area, codes):
    """Vrai si une zone d'un jeu de données touche le territoire (pays : toujours)."""
    if not covered_area:
        return True  # zone non renseignée : on garde, la recoupe spatiale tranchera
    for area in covered_area:
        kind, code = area.get("type"), str(area.get("insee"))
        if kind == "pays" or kind not in codes or not codes[kind] or code in codes[kind]:
            return True
    return False


def clip(gdf, communes):
    """Garde les points dans le territoire et ajoute commune / nom_commune (spatial)."""
    gdf = gdf.drop(columns=[c for c in ("commune", "nom_commune") if c in gdf.columns])
    out = gpd.sjoin(gdf.to_crs(CRS_LAMBERT93), communes[["commune", "nom_commune", "geometry"]],
                    how="inner", predicate="within")
    out = out[~out.index.duplicated()].drop(columns="index_right")
    return out, len(gdf) - len(out)
