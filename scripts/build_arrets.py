"""Rôle 1 (Data engineer) : construit la table silver `arrets`.

Une ligne = un arrêt physique unique.
Colonnes : arret_id, nom, mode, nb_passages_jour, reseau, geometry (Lambert 93).

Étapes (cf. plan de projet, Lot 1) :
  1. télécharge le GTFS de chaque réseau du territoire (bronze)
  2. garde les points d'arrêt réels (location_type vide ou 0)
  3. retrouve le mode de chaque arrêt grâce à routes.txt
  4. compte les passages d'un mardi normal entre 6 h et 21 h
  5. projette en Lambert 93 (EPSG:2154)
  6. fusionne les doublons : même nom (sans accents ni majuscules) à moins de 50 m
  7. filtre sur le territoire (si un fichier de contours est fourni)
  8. écrit data/silver/arrets.parquet et un rapport de contrôle qualité

Usage :
  python scripts/build_arrets.py
  python scripts/build_arrets.py --contours data/silver/contours.parquet
  python scripts/build_arrets.py --date 2026-11-10
  python scripts/build_arrets.py --gtfs tisseo=chemin/tisseo.zip --gtfs lio=chemin/lio.zip
"""
import argparse
import hashlib
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree
from sklearn.cluster import DBSCAN

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "data" / "bronze" / "gtfs"
SILVER = ROOT / "data" / "silver"

# Territoire D1 : Occitanie (Toulouse Métropole, Grand Albigeois, Grand Cahors)
# nom du réseau -> identifiant du jeu de données sur data.gouv.fr
NETWORKS = {
    "tisseo": "tisseo-offre-de-transport-gtfs",
    "lio": "reseau-lio-occitanie",
    "grand_albigeois": "offre-de-transports-du-grand-albigeois-gtfs",
    "grand_cahors": "donnees-gtfs-du-reseau-de-transports-public-du-grand-cahors",
}

CRS_LAMBERT93 = 2154
DEDUP_RADIUS_M = 50
HOUR_MIN, HOUR_MAX = 6, 21  # passages comptés de 6 h (inclus) à 21 h (exclu)
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

BASE_MODES = {0: "tram", 1: "metro", 2: "train", 3: "bus", 4: "ferry", 5: "cable_car",
              6: "telepherique", 7: "funiculaire", 11: "trolleybus", 12: "monorail"}


def mode_from_route_type(rt):
    """Traduit un route_type GTFS (standard ou étendu) en mode."""
    if pd.isna(rt):
        return "autre"
    rt = int(rt)
    if rt in BASE_MODES:
        return BASE_MODES[rt]
    for lo, hi, mode in [(100, 199, "train"), (200, 299, "bus"), (400, 499, "metro"),
                         (700, 799, "bus"), (800, 899, "trolleybus"), (900, 999, "tram"),
                         (1000, 1099, "ferry")]:
        if lo <= rt <= hi:
            return mode
    return "autre"


def normalize(name):
    """Nom sans accents ni majuscules, ponctuation et espaces simplifiés."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------- téléchargement

def resolve_gtfs_url(slug):
    """Cherche dans l'API data.gouv.fr la ressource GTFS (hors temps réel) la plus récente."""
    meta = requests.get(f"https://www.data.gouv.fr/api/1/datasets/{slug}/", timeout=60).json()
    cands = []
    for r in meta.get("resources", []):
        fmt = (r.get("format") or "").lower()
        title = (r.get("title") or "").lower()
        url = r.get("url") or ""
        if any(k in (title + url.lower()) for k in ("gtfs-rt", "gtfsrt", "netex", "temps réel", "temps reel")):
            continue
        if fmt == "gtfs" or (url.lower().endswith(".zip") and "gtfs" in (title + url.lower())):
            cands.append((r.get("last_modified") or "", url))
    if not cands:
        raise RuntimeError(f"Aucune ressource GTFS trouvée pour {slug}")
    return sorted(cands)[-1][1]


def download_gtfs(name, slug, refresh=False):
    BRONZE.mkdir(parents=True, exist_ok=True)
    dest = BRONZE / f"{name}.zip"
    if dest.exists() and not refresh:
        return dest
    url = resolve_gtfs_url(slug)
    print(f"[{name}] téléchargement {url}")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


# ---------------------------------------------------------------- lecture GTFS

def read_gtfs_table(zf, filename, wanted):
    """Lit un fichier GTFS (en chaînes), None s'il est absent. Ne garde que les colonnes utiles."""
    members = [m for m in zf.namelist() if m.lower().endswith(filename)]
    if not members:
        return None
    with zf.open(members[0]) as f:
        df = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig",
                         usecols=lambda c: c.strip() in wanted)
    df.columns = [c.strip() for c in df.columns]
    return df


def pick_service_date(calendar, calendar_dates, trips, wanted=None):
    """Choisit un mardi 'normal' : celui qui a le plus de courses actives dans la fenêtre de validité.

    Évite ainsi un mardi de vacances scolaires, où l'offre est réduite."""
    if wanted:
        return wanted
    bounds = []
    if calendar is not None and len(calendar):
        bounds += [pd.to_datetime(calendar.start_date, format="%Y%m%d").min(),
                   pd.to_datetime(calendar.end_date, format="%Y%m%d").max()]
    elif calendar_dates is not None and len(calendar_dates):
        d = pd.to_datetime(calendar_dates.date, format="%Y%m%d")
        bounds += [d.min(), d.max()]
    if not bounds:
        raise RuntimeError("Ni calendar.txt ni calendar_dates.txt exploitables")
    start, end = bounds[0].date(), bounds[1].date()
    today = date.today()
    lo = max(start, today)
    hi = min(end, today + timedelta(days=120))
    if lo > hi:  # flux périmé ou pas encore valide : on prend toute la validité
        lo, hi = start, end
    first_tuesday = lo + timedelta(days=(1 - lo.weekday()) % 7)
    candidates = []
    d = first_tuesday
    while d <= hi:
        candidates.append(d)
        d += timedelta(days=7)
    if not candidates:
        return first_tuesday
    trips_per_service = trips.groupby("service_id").size()
    best, best_n = candidates[0], -1
    for d in candidates:
        n = trips_per_service.reindex(list(active_services(calendar, calendar_dates, d))).fillna(0).sum()
        if n > best_n:
            best, best_n = d, n
    return best


def active_services(calendar, calendar_dates, d):
    ds = d.strftime("%Y%m%d")
    active = set()
    if calendar is not None:
        c = calendar[(calendar.start_date <= ds) & (calendar.end_date >= ds)
                     & (calendar[DAYS[d.weekday()]] == "1")]
        active |= set(c.service_id)
    if calendar_dates is not None:
        x = calendar_dates[calendar_dates.date == ds]
        active |= set(x[x.exception_type == "1"].service_id)
        active -= set(x[x.exception_type == "2"].service_id)
    return active


def stops_from_gtfs(name, zip_path, wanted_date=None):
    """Retourne un DataFrame : stop_id, nom, lat, lon, mode, nb_passages_jour, reseau."""
    with zipfile.ZipFile(zip_path) as zf:
        stops = read_gtfs_table(zf, "stops.txt",
                                {"stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"})
        routes = read_gtfs_table(zf, "routes.txt", {"route_id", "route_type"})
        trips = read_gtfs_table(zf, "trips.txt", {"trip_id", "route_id", "service_id"})
        stop_times = read_gtfs_table(zf, "stop_times.txt",
                                     {"trip_id", "stop_id", "arrival_time", "departure_time"})
        calendar = read_gtfs_table(zf, "calendar.txt", {"service_id", "start_date", "end_date", *DAYS})
        calendar_dates = read_gtfs_table(zf, "calendar_dates.txt",
                                         {"service_id", "date", "exception_type"})

    # points d'arrêt réels uniquement
    if "location_type" in stops.columns:
        stops = stops[stops.location_type.isin(["", "0"])]
    stops = stops.assign(lat=pd.to_numeric(stops.stop_lat, errors="coerce"),
                         lon=pd.to_numeric(stops.stop_lon, errors="coerce"))
    stops = stops.dropna(subset=["lat", "lon"])
    stops = stops[(stops.lat != 0) | (stops.lon != 0)]

    service_date = pick_service_date(calendar, calendar_dates, trips, wanted_date)
    print(f"[{name}] {len(stops)} arrêts, jour de référence : {service_date} ({DAYS[service_date.weekday()]})")
    active = active_services(calendar, calendar_dates, service_date)
    active_trips = set(trips.loc[trips.service_id.isin(active), "trip_id"])

    # mode de chaque course (via sa ligne), puis de chaque arrêt
    routes["route_type"] = pd.to_numeric(routes.route_type, errors="coerce")
    trip_rt = trips.merge(routes[["route_id", "route_type"]], on="route_id", how="left") \
                   .drop_duplicates("trip_id").set_index("trip_id").route_type
    st = stop_times
    st["rt"] = st.trip_id.map(trip_rt)
    mode_counts = st.groupby(["stop_id", "rt"], dropna=False).size().reset_index(name="n")
    best = mode_counts.loc[mode_counts.groupby("stop_id").n.idxmax()].set_index("stop_id").rt
    stop_mode = best.map(mode_from_route_type)

    # passages d'un mardi normal entre 6 h et 21 h
    t = st.departure_time.where(st.departure_time != "", st.arrival_time)
    hour = pd.to_numeric(t.str.slice(0, 2), errors="coerce")
    ok = st.trip_id.isin(active_trips) & (hour >= HOUR_MIN) & (hour < HOUR_MAX)
    passages = st[ok].groupby("stop_id").size()

    out = pd.DataFrame({"stop_id": stops.stop_id.values,
                        "nom": stops.stop_name.values,
                        "lat": stops.lat.values, "lon": stops.lon.values})
    out["mode"] = out.stop_id.map(stop_mode).fillna("autre")
    out["nb_passages_jour"] = out.stop_id.map(passages).fillna(0).astype(int)
    out["reseau"] = name
    return out


# ---------------------------------------------------------------- dédoublonnage

def dedupe(df):
    """Fusionne les arrêts de même nom normalisé situés à moins de 50 m (en Lambert 93)."""
    df = df.reset_index(drop=True)
    df["nom_norm"] = df.nom.map(normalize)
    labels = np.zeros(len(df), dtype=int)
    nxt = 0
    for _, grp in df.groupby("nom_norm"):
        pos = grp.index.to_numpy()
        if len(pos) == 1:
            labels[pos] = nxt
            nxt += 1
            continue
        cl = DBSCAN(eps=DEDUP_RADIUS_M, min_samples=1).fit_predict(df.loc[pos, ["x", "y"]].to_numpy())
        labels[pos] = cl + nxt
        nxt += cl.max() + 1
    df["cluster"] = labels

    mode_tot = df.groupby(["cluster", "mode"]).nb_passages_jour.sum().reset_index()
    mode_best = mode_tot.loc[mode_tot.groupby("cluster").nb_passages_jour.idxmax()].set_index("cluster")["mode"]
    agg = df.groupby("cluster").agg(
        nom=("nom", lambda s: s.value_counts().index[0]),
        nom_norm=("nom_norm", "first"),
        nb_passages_jour=("nb_passages_jour", "sum"),
        reseau=("reseau", lambda s: ",".join(sorted(set(s)))),
        x=("x", "mean"), y=("y", "mean"),
        n_brut=("x", "size"),
    ).reset_index()
    agg["mode"] = agg.cluster.map(mode_best)
    agg["arret_id"] = [
        hashlib.blake2s(f"{n}|{round(x)}|{round(y)}".encode(), digest_size=5).hexdigest()
        for n, x, y in zip(agg.nom_norm, agg.x, agg.y)
    ]
    return agg


# ---------------------------------------------------------------- qualité

def quality_report(final, n_brut, n_hors_territoire, contours_used, service_dates):
    lines = ["Rapport qualité - table arrets", "=" * 40,
             f"Arrêts bruts (tous réseaux)        : {n_brut}",
             f"Arrêts après dédoublonnage         : {len(final) + n_hors_territoire}",
             f"Arrêts hors territoire écartés     : {n_hors_territoire}"
             + ("" if contours_used else "  (aucun contour fourni : pas de filtre)"),
             f"Arrêts livrés                      : {len(final)}"]
    # doublons résiduels : même nom, moins de 50 m
    xy = np.c_[final.geometry.x, final.geometry.y]
    residual = 0
    for _, idx in final.groupby(final.nom.map(normalize)).indices.items():
        if len(idx) > 1:
            residual += len(cKDTree(xy[idx]).query_pairs(DEDUP_RADIUS_M))
    lines.append(f"Doublons résiduels (même nom <50 m) : {residual}  (attendu : 0)")
    lines.append(f"Arrêts sans mode ('autre')          : {(final['mode'] == 'autre').sum()}")
    lines.append(f"Arrêts à 0 passage                  : {(final.nb_passages_jour == 0).sum()}")
    lines.append(f"Jours de référence                  : {service_dates}")
    lines.append("\nPar mode :\n" + final["mode"].value_counts().to_string())
    lines.append("\nPar réseau :\n" + final.reseau.value_counts().to_string())
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="jour de référence YYYY-MM-DD (un mardi), sinon choix automatique")
    ap.add_argument("--contours", help="contour du territoire (gpkg/parquet/geojson) pour filtrer")
    ap.add_argument("--gtfs", action="append", default=[], metavar="NOM=CHEMIN.zip",
                    help="utiliser un GTFS local au lieu du téléchargement (répétable)")
    ap.add_argument("--refresh", action="store_true", help="re-télécharger les GTFS")
    args = ap.parse_args()

    wanted = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    if args.gtfs:
        sources = {kv.split("=", 1)[0]: Path(kv.split("=", 1)[1]) for kv in args.gtfs}
    else:
        sources = {n: download_gtfs(n, slug, args.refresh) for n, slug in NETWORKS.items()}

    frames = [stops_from_gtfs(n, p, wanted) for n, p in sources.items()]
    allstops = pd.concat(frames, ignore_index=True)
    n_brut = len(allstops)

    # Lambert 93 pour toutes les couches
    pts = gpd.GeoSeries(gpd.points_from_xy(allstops.lon, allstops.lat), crs=4326).to_crs(CRS_LAMBERT93)
    allstops["x"], allstops["y"] = pts.x.values, pts.y.values

    merged = dedupe(allstops)
    gdf = gpd.GeoDataFrame(merged, geometry=gpd.points_from_xy(merged.x, merged.y), crs=CRS_LAMBERT93)

    n_hors = 0
    if args.contours:
        path = Path(args.contours)
        contours = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        contours = contours.to_crs(CRS_LAMBERT93)
        inside = gpd.sjoin(gdf, contours[["geometry"]], how="inner", predicate="within")
        keep = inside.index.unique()
        n_hors = len(gdf) - len(keep)
        gdf = gdf.loc[keep]

    final = gdf[["arret_id", "nom", "mode", "nb_passages_jour", "reseau", "geometry"]] \
        .sort_values(["nom", "arret_id"]).reset_index(drop=True)
    assert final.arret_id.is_unique, "arret_id en double"

    SILVER.mkdir(parents=True, exist_ok=True)
    final.to_parquet(SILVER / "arrets.parquet")
    report = quality_report(final, n_brut, n_hors, bool(args.contours),
                            wanted or "automatique (mardi le plus chargé)")
    (SILVER / "arrets_qc.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nÉcrit : {SILVER / 'arrets.parquet'}")


if __name__ == "__main__":
    main()
