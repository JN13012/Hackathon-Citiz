"""Territoire d'analyse du Rôle 4 : les sources sont nettoyées au niveau national, puis recoupées ici.

Changer de territoire = variable d'environnement TERRITOIRE (ara par défaut, france) ou --contours,
puis relancer les scripts (`python scripts/run_all.py --territoire france`).
Contours : fichier `contours` du Rôle 2 s'il est fourni, sinon communes de geo.api.gouv.fr (cache en bronze).

Les sources nationales restent partagées dans data/bronze/ ; les tables d'un autre territoire que
l'ARA sont écrites à part (data/<territoire>/silver, gold, bronze pour les relevés filtrés) pour ne
jamais remplacer les résultats ARA.
"""
import json
import os
from pathlib import Path

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parent.parent
CRS_LAMBERT93 = 2154

# Régions de France métropolitaine (codes INSEE) et leurs départements, Corse comprise
REGIONS_METROPOLE = {
    "11": ["75", "77", "78", "91", "92", "93", "94", "95"],  # Île-de-France
    "24": ["18", "28", "36", "37", "41", "45"],  # Centre-Val de Loire
    "27": ["21", "25", "39", "58", "70", "71", "89", "90"],  # Bourgogne-Franche-Comté
    "28": ["14", "27", "50", "61", "76"],  # Normandie
    "32": ["02", "59", "60", "62", "80"],  # Hauts-de-France
    "44": ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],  # Grand Est
    "52": ["44", "49", "53", "72", "85"],  # Pays de la Loire
    "53": ["22", "29", "35", "56"],  # Bretagne
    "75": ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],  # Nouvelle-Aquitaine
    "76": ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],  # Occitanie
    "84": ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],  # Auvergne-Rhône-Alpes
    "93": ["04", "05", "06", "13", "83", "84"],  # Provence-Alpes-Côte d'Azur
    "94": ["2A", "2B"],  # Corse
}

# Territoires prévus. Niveaux : region, departement, epci (SIREN), commune (code INSEE).
# cadre : rectangle large en Lambert 93 (x min, y min, x max, y max) pour le contrôle qualité.
TERRITOIRES = {
    # D1 : Auvergne-Rhône-Alpes (grande ville : Lyon), choix d'équipe du 19/10
    "ara": {"niveau": "region", "codes": ["84"], "dossier": None,
            "cadre": (600_000, 6_300_000, 1_060_000, 6_700_000), "libelle": "Auvergne-Rhône-Alpes"},
    # extension à toute la France métropolitaine, Corse comprise (demande du 21/10)
    "france": {"niveau": "region", "codes": list(REGIONS_METROPOLE), "dossier": "france",
               "cadre": (60_000, 6_020_000, 1_260_000, 7_130_000), "libelle": "France métropolitaine"},
}
NOM = os.environ.get("TERRITOIRE", "ara").strip().lower()
if NOM not in TERRITOIRES:
    raise SystemExit(f"TERRITOIRE inconnu : {NOM!r} (choix : {', '.join(TERRITOIRES)})")
NIVEAU = TERRITOIRES[NOM]["niveau"]
CODES = TERRITOIRES[NOM]["codes"]
CADRE = TERRITOIRES[NOM]["cadre"]
LIBELLE = TERRITOIRES[NOM]["libelle"]
# départements du territoire (None si le territoire n'est pas défini par régions ou départements)
DEPARTEMENTS = ([d for c in CODES for d in REGIONS_METROPOLE[c]] if NIVEAU == "region"
                else list(CODES) if NIVEAU == "departement" else None)

BRONZE = ROOT / "data" / "bronze"  # sources nationales, partagées entre territoires
_dossier = TERRITOIRES[NOM]["dossier"]
DATA = ROOT / "data" / _dossier if _dossier else ROOT / "data"
SILVER, GOLD = DATA / "silver", DATA / "gold"
BRONZE_TERRITOIRE = DATA / "bronze" if _dossier else BRONZE  # relevés filtrés sur le territoire (GBFS)
# accès à 5 min (paires carreau-offre) : livraison Rôle 2 pour l'ARA, assemblage des régions sinon
ACCESSIBILITE = GOLD / "accessibilite_ara" if NOM == "ara" else GOLD / "accessibilite"
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
    """Communes du territoire en Lambert 93 : commune, nom_commune, dep, epci, region, geometry.

    contours : fichier du Rôle 2 (geo/build_contours.py), au choix
    - `contours.parquet` : lignes `niveau` = commune (code_insee), iris, epci (code_epci) ;
      l'EPCI de chaque commune est retrouvé par les lignes epci quand elles existent ;
    - `territoire.parquet` ou tout polygone sans codes : communes de geo.api.gouv.fr (NIVEAU / CODES)
      dont un point intérieur tombe dans le polygone.
    """
    if contours is None:
        gdf = _communes_geo_api()
    else:
        path = Path(contours)
        src = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        src = src.to_crs(CRS_LAMBERT93)
        if {"niveau", "code_insee"} <= set(src.columns):
            gdf = src[src["niveau"] == "commune"].rename(columns={"code_insee": "commune", "nom": "nom_commune"})
            gdf["commune"] = gdf["commune"].astype(str)
            gdf["dep"] = dep_of(gdf["commune"])
            epci = src[src["niveau"] == "epci"]
            if len(epci):
                inner = gpd.GeoDataFrame(gdf[["commune"]], geometry=gdf.representative_point(), crs=gdf.crs)
                hit = gpd.sjoin(inner, epci[["code_epci", "geometry"]], predicate="within")
                gdf["epci"] = hit[~hit.index.duplicated()]["code_epci"].astype(str).reindex(gdf.index)
        else:  # polygone seul : on prend les communes geo.api dont l'intérieur est dans le polygone
            zone = src.union_all()
            ref = _communes_geo_api().to_crs(CRS_LAMBERT93)
            gdf = ref[ref.representative_point().within(zone)]
            print(f"--contours sans codes de commune : {len(gdf)} communes de {NIVEAU} {CODES} dans le polygone")
        if gdf.empty:
            raise SystemExit(f"Aucune commune trouvée dans {contours}")
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
