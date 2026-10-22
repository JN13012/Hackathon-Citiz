import argparse
import hashlib
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree
from shapely import covers, prepare as prepare_geometry, union_all

ROOT = Path(__file__).resolve().parent.parent
BRONZE = ROOT / "data" / "bronze" / "gtfs"
sys.path.insert(0, str(ROOT / "optim"))
import territoire as T  # noqa: E402  (territoire : TERRITOIRE=ara|france)
SILVER = T.SILVER

# Territoire : Auvergne-Rhône-Alpes (grande ville : Lyon) - choix d'équipe du 19/10
# nom du réseau -> identifiant du jeu de données sur data.gouv.fr
NETWORKS = {
    # Lyon et sa métropole
    "tcl_lyon": "horaires-theoriques-du-reseau-transports-en-commun-lyonnais",
    # Grenoble et Isère
    "tag_grenoble": "horaires-theoriques-du-reseau-tag",
    "bastille_grenoble": "horaires-theoriques-du-telepherique-de-la-bastille",
    "pays_voironnais": "horaires-theoriques-du-reseau-transport-du-pays-voironnais",
    # Saint-Étienne, Roanne, Loire
    "stas_saint_etienne": "horaires-du-reseau-stas",
    "star_roanne": "star-horaires-theoriques-et-temps-reel-du-reseau-de-transport-urbain-de-roannais-agglomeration",
    # Clermont-Ferrand, Vichy, Le Puy
    "t2c_clermont": "syndicat-mixte-des-transports-en-commun-de-lagglomeration-clermontoise-smtc-ac-reseau-t2c-gtfs-gtfs-rt",
    "mobivie_vichy": "mobivie-reseau-urbain-vichy-communaute",
    "velay_le_puy": "fichier-gtfs-des-transports-urbains-de-la-communaute-dagglomeration-du-puy-en-velay",
    # Savoie, Haute-Savoie, Ain, Drôme
    "synchro_chambery": "arrets-et-horaires-theoriques-bus-temps-reel",
    "sibra_annecy": "offre-de-transports-sibra-a-annecy-gtfs",
    "tac_annemasse": "offre-de-transports-reseau-tac-annemasse-agglo",
    "rubis_bourg": "rubis-grand-bourg-mobilites-offre-de-transport-theorique-et-temps-reel",
    "citea_valence": "ce-jeu-de-donnees-contient-la-liste-des-arrets-des-horaires-et-des-parcours-theoriques-du-reseau-de-transport-urbain-et-interurbain-de-vrm",
    # Cars de la Région (interurbain), départements trouvés sur transport.data.gouv.fr
    "cars_savoie": "reseau-interurbain-cars-region-savoie-73",
    "cars_allier": "reseau-interurbain-cars-region-allier-03",
    "cars_ardeche": "reseau-interurbain-cars-region-ardeche-07",
    "cars_haute_loire": "reseau-interurbain-cars-region-haute-loire-43",
    "cars_cantal": "reseau-interurbain-cars-region-cantal-15",
}

CRS_LAMBERT93 = 2154

# Hors ARA, les réseaux sont lus dans le catalogue national (jeux « public-transit » avec un GTFS dont la
# zone touche le territoire). Les jeux de portée nationale (SNCF, cars longue distance, aérien) sont
# écartés par défaut, comme dans la liste ARA ; --avec-nationaux les ajoute.
TDG_API = "https://transport.data.gouv.fr/api/datasets"
# Jeux écartés du catalogue : les agrégats régionaux republient des réseaux déjà collectés un par un
# (fréquences comptées deux fois), les programmes de vols ne sont pas une offre de proximité.
EXCLUDED_TITLES = re.compile(r"^(agr[ée]gat\b|programme des vols)", re.I)

# Sources qui ne se téléchargent plus automatiquement : on indique comment les obtenir.
MANUAL_SOURCES = {
    "tcl_lyon": "le serveur Grand Lyon exige une authentification (HTTP 401). Récupérer le GTFS TCL officiel "
                "(tcl_lyon.ZIP, groupe interne Teams de l'équipe) et le copier en data/bronze/gtfs/tcl_lyon.zip "
                "(il sera réutilisé sans téléchargement), ou passer --gtfs tcl_lyon=chemin.zip",
}
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

EXCLUDED_WORDS = ("fichiers txt", "gtfs-rt", "gtfsrt", "gtfs_rt", "netex", "trip_updates", "vehicle_positions")


TRANSIENT = (requests.ConnectionError, requests.Timeout)  # coupure réseau passagère : on réessaie


def with_retries(action, label, attempts=3):
    """Relance une requête après une coupure réseau ou une erreur serveur 5xx (5 s puis 10 s d'attente)."""
    for attempt in range(attempts):
        try:
            return action()
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code < 500 or attempt == attempts - 1:
                raise
            error = exc
        except TRANSIENT as exc:
            if attempt == attempts - 1:
                raise
            error = exc
        print(f"[{label}] erreur réseau passagère ({type(error).__name__}), nouvel essai dans {5 * 2 ** attempt} s")
        time.sleep(5 * 2 ** attempt)


def cached_gtfs(name):
    """GTFS déjà téléchargés pour un réseau dans data/bronze/gtfs/ (archives zip valides)."""
    files = sorted(BRONZE.glob(f"{name}.zip")) or sorted(BRONZE.glob(f"{name}_[0-9]*.zip"))
    return [(f.stem, f) for f in files if zipfile.is_zipfile(f)]


def resolve_gtfs_urls(slug):
    """Cherche dans l'API data.gouv.fr les fichiers GTFS statiques d'un jeu de données.

    Un réseau peut publier plusieurs GTFS (ex. Grand Albigeois : urbain, périurbain, navettes) :
    on les retourne tous, en gardant la version la plus récente de chaque fichier.
    Retourne une liste [(titre, url)]."""
    def fetch():
        r = requests.get(f"https://www.data.gouv.fr/api/1/datasets/{slug}/", timeout=60)
        r.raise_for_status()
        return r.json()
    meta = with_retries(fetch, slug)
    best = {}
    for r in meta.get("resources", []):
        fmt = (r.get("format") or "").lower()
        title = r.get("title") or ""
        text = f"{title} {r.get('description') or ''}".lower()
        url = r.get("url") or ""
        if r.get("type") == "documentation" or fmt in ("pdf", "csv", "json", "gtfs-rt", "netex"):
            continue
        # Hors ARA, un fichier déclaré zip/GTFS n'est écarté que si son titre annonce du NeTEx : sa
        # description cite souvent le GTFS-RT du même jeu (Fil Bleu, Tango, Ametis). ARA : règle d'origine.
        static = T.NOM != "ara" and fmt in ("gtfs", "zip") and "netex" not in title.lower()
        if not static and any(w in f"{text} {url.lower()}" for w in EXCLUDED_WORDS):
            continue  # temps réel, NeTEx...
        if not (fmt in ("gtfs", "zip") or "gtfs" in text):  # certains réseaux déclarent "zip" ou "octet-stream"
            continue
        modified = r.get("last_modified") or ""
        if title.lower() not in best or modified > best[title.lower()][0]:
            best[title.lower()] = (modified, title, url)
    if not best:
        raise RuntimeError(f"Aucune ressource GTFS trouvée pour {slug}")
    return sorted((t, u) for _, t, u in best.values())


def download_one(label, url, refresh=False):
    BRONZE.mkdir(parents=True, exist_ok=True)
    dest = BRONZE / f"{label}.zip"
    if dest.exists() and not refresh:
        if zipfile.is_zipfile(dest):
            print(f"[{label}] déjà téléchargé : {dest.name}")
            return dest
        print(f"[{label}] fichier incomplet ou corrompu, nouveau téléchargement")
    print(f"[{label}] téléchargement {url.split('?')[0]}")  # sans la clé d'API éventuelle
    tmp = dest.with_suffix(".part")  # on n'écrit le .zip qu'une fois le téléchargement terminé
    def fetch():
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    with_retries(fetch, label)
    tmp.replace(dest)
    print(f"[{label}] téléchargé ({dest.stat().st_size / 1e6:.1f} Mo)")
    return dest


def download_network(name, slug, refresh=False):
    """Télécharge tous les GTFS d'un réseau. Retourne [(étiquette, chemin)].
    Si data.gouv.fr reste injoignable, les GTFS déjà présents dans data/bronze/gtfs/ sont réutilisés."""
    try:
        resources = resolve_gtfs_urls(slug)
    except TRANSIENT as exc:
        cached = cached_gtfs(name)
        if refresh or not cached:
            raise
        print(f"[{name}] data.gouv.fr injoignable ({type(exc).__name__}) : GTFS déjà téléchargé réutilisé "
              f"({', '.join(path.name for _, path in cached)})")
        return cached
    out = []
    for i, (title, url) in enumerate(resources, 1):
        label = name if len(resources) == 1 else f"{name}_{i}"
        if len(resources) > 1:
            print(f"[{name}] fichier {i}/{len(resources)} : {title}")
        out.append((label, download_one(label, url, refresh)))
    return out


def network_name(slug):
    """Nom court d'un réseau : celui de la liste ARA s'il y figure (GTFS déjà en cache), sinon le slug."""
    known = {v: k for k, v in NETWORKS.items()}
    return known.get(slug) or re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")[:60]


def discover_networks(codes, with_national=False):
    """Réseaux GTFS du catalogue transport.data.gouv.fr qui touchent le territoire : {nom: id data.gouv}.
    codes : codes INSEE du territoire par niveau (territoire.area_codes)."""
    def fetch():
        r = requests.get(TDG_API, timeout=120)
        r.raise_for_status()
        return r.json()
    datasets = with_retries(fetch, "catalogue")
    found, excluded = {}, []
    for d in datasets:
        if d.get("type") != "public-transit" or not d.get("datagouv_id"):
            continue
        if EXCLUDED_TITLES.match(d.get("title") or ""):
            excluded.append(d["title"])
            continue
        if not any((r.get("format") or "").upper() == "GTFS" for r in d.get("resources", [])):
            continue
        area = d.get("covered_area") or []
        if not with_national and any(a.get("type") == "pays" for a in area):
            continue
        if area and T.covers(area, codes):
            found[network_name(d.get("slug") or d["datagouv_id"])] = d["datagouv_id"]
    if not found:
        raise RuntimeError("Aucun réseau GTFS trouvé dans le catalogue pour ce territoire")
    for title in sorted(excluded):
        print(f"[catalogue] écarté (agrégat ou vols) : {title}")
    return dict(sorted(found.items()))


def territory_codes(contours_path):
    """Codes INSEE du territoire par niveau, lus dans la table contours (régions : territoire.CODES)."""
    codes = T.area_codes(T.load_communes(contours_path))
    if T.NIVEAU == "region":
        codes["region"] = set(T.CODES)
    return codes


def has_stops(zip_path):
    """Vrai si l'archive contient un stops.txt (sinon : NeTEx ou autre format livré en zip)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return any(m.lower().endswith("stops.txt") for m in zf.namelist())
    except zipfile.BadZipFile:
        return False


def collapse_network_files(stops):
    """Un réseau publié en plusieurs fichiers qui se recouvrent (ex. GTFS complet et GTFS du SAE) :
    un même arrêt (même identifiant, mêmes coordonnées) n'est compté qu'une fois, avec la fréquence
    la plus élevée, au lieu de la somme des fichiers."""
    cols = ["reseau", "stop_id", "_lat", "_lon"]
    stops = stops.assign(_lat=stops.lat.round(5), _lon=stops.lon.round(5))
    stops["nb_passages_jour"] = stops.groupby(cols).nb_passages_jour.transform("max")
    out = stops.drop_duplicates(cols).drop(columns=["_lat", "_lon"])
    if len(out) < len(stops):
        print(f"Arrêts présents dans plusieurs fichiers d'un même réseau, comptés une fois : {len(stops) - len(out)}")
    return out.reset_index(drop=True)


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


def stops_from_gtfs(name, zip_path, wanted_date=None, label=None):
    """Retourne un DataFrame : stop_id, nom, lat, lon, mode, nb_passages_jour, reseau."""
    label = label or name
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
    print(f"[{label}] {len(stops)} arrêts, jour de référence : {service_date} ({DAYS[service_date.weekday()]})")
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

def connected_labels(xy, radius):
    """Regroupe les points reliés de proche en proche par des distances <= radius.

    Équivaut à DBSCAN(eps=radius, min_samples=1), sans dépendre de scikit-learn."""
    n = len(xy)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(xy).query_pairs(radius):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
    roots = np.array([find(i) for i in range(n)])
    return np.unique(roots, return_inverse=True)[1]


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
        cl = connected_labels(df.loc[pos, ["x", "y"]].to_numpy(), DEDUP_RADIUS_M)
        labels[pos] = cl + nxt
        nxt += cl.max() + 1
    df["cluster"] = labels

    mode_tot = df.groupby(["cluster", "mode"]).nb_passages_jour.sum().reset_index()
    mode_best = mode_tot.loc[mode_tot.groupby("cluster").nb_passages_jour.idxmax()].set_index("cluster")["mode"]
    agg = df.groupby("cluster").agg(
        nom=("nom", lambda s: s.value_counts().index[0]),
        nom_norm=("nom_norm", "first"),
        nb_passages_jour=("nb_passages_jour", "sum"),
        reseau=("reseau", lambda s: ",".join(sorted({r for v in s for r in v.split(",")}))),
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

def residual_duplicates(final):
    """Paires restant à examiner après la fusion, sans suppression implicite."""
    xy = np.c_[final.geometry.x, final.geometry.y]
    return sum(len(cKDTree(xy[idx]).query_pairs(DEDUP_RADIUS_M))
               for idx in final.groupby(final.nom.map(normalize)).indices.values() if len(idx) > 1)


def validate_stops(final, boundary):
    if final.empty:
        raise ValueError("Aucun arrêt après préparation : publication annulée")
    if final.crs is None or final.crs.to_epsg() != CRS_LAMBERT93:
        raise ValueError("Projection des arrêts incorrecte")
    for column in ["arret_id", "nom", "mode", "reseau"]:
        if final[column].isna().any() or final[column].astype("string").str.strip().eq("").any():
            raise ValueError(f"Valeurs absentes dans {column}")
    if not final.arret_id.is_unique:
        raise ValueError("Identifiants d'arrêt dupliqués")
    if (final.geometry.isna() | final.geometry.is_empty | ~final.geometry.is_valid).any() or not final.geom_type.eq("Point").all():
        raise ValueError("Géométries d'arrêt invalides")
    values = pd.to_numeric(final.nb_passages_jour, errors="raise")
    if not np.isfinite(values).all() or values.lt(0).any():
        raise ValueError("Fréquences absentes, non finies ou négatives")
    if not covers(boundary, final.geometry.to_numpy()).all():
        raise ValueError("Des arrêts sont hors des contours")
    residual = residual_duplicates(final)
    if residual:
        raise ValueError(f"{residual} paire(s) de doublons résiduels : publication annulée")


def load_boundary(path):
    if not path.is_file():
        raise ValueError(f"Contours obligatoires introuvables : {path}")
    contours = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
    if contours.empty or contours.crs is None:
        raise ValueError("Contours vides ou projection absente")
    if "niveau" in contours and contours.niveau.eq("commune").any():
        contours = contours.loc[contours.niveau.eq("commune")]
    if (contours.geometry.isna() | contours.geometry.is_empty | ~contours.geometry.is_valid).any():
        raise ValueError("Contours invalides")
    if not contours.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Les contours doivent être polygonaux")
    boundary = union_all(contours.to_crs(CRS_LAMBERT93).geometry)
    prepare_geometry(boundary)
    return boundary


def publish_stops(final, report):
    """Écrire et valider avant remplacement ; restaurer si la publication échoue."""
    SILVER.mkdir(parents=True, exist_ok=True)
    names = ["arrets.parquet", "arrets_qc.txt"]
    with TemporaryDirectory(dir=SILVER, prefix=".arrets_") as tmp:
        stage = Path(tmp)
        final.to_parquet(stage / names[0], index=False)
        (stage / names[1]).write_text(report, encoding="utf-8")
        backups = {}
        for name in names:
            dest = SILVER / name
            if dest.exists():
                backups[name] = stage / ("ancien_" + name)
                shutil.copy2(dest, backups[name])
        replaced = []
        try:
            for name in names:
                (stage / name).replace(SILVER / name)
                replaced.append(name)
        except BaseException:
            for name in replaced:
                if name in backups:
                    shutil.copy2(backups[name], SILVER / name)
                else:
                    (SILVER / name).unlink(missing_ok=True)
            raise


def quality_report(final, n_brut, n_hors_territoire, contours_used, service_dates, echecs=(), non_gtfs=()):
    lines = ["Rapport qualité - table arrets", "=" * 40,
             f"Arrêts bruts (tous réseaux)        : {n_brut}",
             f"Arrêts après dédoublonnage         : {len(final) + n_hors_territoire}",
             f"Arrêts hors territoire écartés     : {n_hors_territoire}"
             + ("" if contours_used else "  (aucun contour fourni : pas de filtre)"),
             f"Arrêts livrés                      : {len(final)}"]
    # doublons résiduels : même nom, moins de 50 m
    residual = residual_duplicates(final)
    lines.append(f"Doublons résiduels (même nom <50 m) : {residual}  (attendu : 0)")
    lines.append(f"Arrêts sans mode ('autre')          : {(final['mode'] == 'autre').sum()}")
    lines.append(f"Arrêts à 0 passage                  : {(final.nb_passages_jour == 0).sum()}")
    lines.append(f"Jours de référence                  : {service_dates}")
    lines.append("Réseaux en échec (ignorés)          : " + (", ".join(echecs) if echecs else "aucun"))
    lines.append("Fichiers non GTFS ignorés (NeTEx)   : " + (", ".join(non_gtfs) if non_gtfs else "aucun"))
    lines.append("\nPar mode :\n" + final["mode"].value_counts().to_string())
    lines.append("\nPar réseau :\n" + final.reseau.value_counts().to_string())
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="jour de référence YYYY-MM-DD (un mardi), sinon choix automatique")
    ap.add_argument("--contours", type=Path, default=SILVER / "contours.parquet",
                    help="contour obligatoire ; par défaut data/silver/contours.parquet")
    ap.add_argument("--gtfs", action="append", default=[], metavar="NOM=CHEMIN.zip",
                    help="utiliser un GTFS local au lieu du téléchargement (répétable)")
    ap.add_argument("--refresh", action="store_true", help="re-télécharger les GTFS")
    ap.add_argument("--avec-nationaux", action="store_true",
                    help="hors ARA : garder aussi les jeux de portée nationale (SNCF, cars longue distance)")
    ap.add_argument("--tolerer-echecs", action="store_true",
                    help="hors ARA : publier malgré des réseaux en échec, listés dans le rapport qualité "
                         "(les réseaux à récupérer à la main restent obligatoires)")
    args = ap.parse_args(argv)

    boundary = load_boundary(args.contours)
    wanted = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    sources = []  # (réseau, étiquette du fichier, chemin)
    echecs = []
    if args.gtfs:
        for kv in args.gtfs:
            n, p = kv.split("=", 1)
            sources.append((n, n, Path(p)))
    else:
        networks = NETWORKS if T.NOM == "ara" else discover_networks(territory_codes(args.contours), args.avec_nationaux)
        print(f"Territoire {T.LIBELLE} : {len(networks)} réseau(x) GTFS")
        for n, slug in networks.items():
            try:
                sources += [(n, label, path) for label, path in download_network(n, slug, args.refresh)]
            except Exception as exc:
                echecs.append(n)
                print(f"[{n}] ÉCHEC : {str(exc)[:150]}")
                if n in MANUAL_SOURCES:
                    print(f"[{n}] À FAIRE : {MANUAL_SOURCES[n]}")
    tolerated = args.tolerer_echecs and T.NOM != "ara"
    if echecs and (not tolerated or any(n in MANUAL_SOURCES for n in echecs)):
        raise RuntimeError(f"Collecte incomplète : {', '.join(echecs)}. Fichiers silver conservés.")

    frames, non_gtfs = [], []
    for reseau, label, p in sources:
        if any(r == reseau and q != p for r, _, q in sources) and not has_stops(p):
            non_gtfs.append(label)  # NeTEx ou autre format livré en zip, le réseau a un autre fichier
            print(f"[{label}] pas un GTFS (aucun stops.txt) : fichier ignoré")
            continue
        try:
            frame = stops_from_gtfs(reseau, p, wanted, label)
            if frame.empty:
                raise ValueError("Aucun arrêt lu dans le GTFS")
            frames.append(frame)
        except Exception as exc:
            echecs.append(label)
            print(f"[{label}] ÉCHEC : {str(exc)[:150]}")
    if echecs and (not tolerated or any(n in MANUAL_SOURCES for n in echecs)):
        raise RuntimeError(f"Lecture GTFS incomplète : {', '.join(echecs)}. Fichiers silver conservés.")
    if not frames:
        raise RuntimeError("Aucune source GTFS exploitable. Fichiers silver conservés.")
    allstops = pd.concat(frames, ignore_index=True)
    if T.NOM != "ara":
        allstops = collapse_network_files(allstops)
    allstops["nb_passages_jour"] = pd.to_numeric(allstops.nb_passages_jour, errors="raise")
    if not np.isfinite(allstops.nb_passages_jour).all() or allstops.nb_passages_jour.lt(0).any():
        raise ValueError("Fréquences source absentes, non finies ou négatives")
    # coordonnées absentes ou à (0, 0) dans certains GTFS : on les écarte
    valides = allstops.lat.notna() & allstops.lon.notna() & ~((allstops.lat == 0) & (allstops.lon == 0))
    if (~valides).any():
        print(f"{(~valides).sum()} arrêt(s) sans coordonnées valides écartés")
        allstops = allstops[valides].reset_index(drop=True)
    n_brut = len(allstops)

    # Lambert 93 pour toutes les couches
    pts = gpd.GeoSeries(gpd.points_from_xy(allstops.lon, allstops.lat), crs=4326).to_crs(CRS_LAMBERT93)
    allstops["x"], allstops["y"] = pts.x.values, pts.y.values

    merged = dedupe(allstops)
    for _ in range(5):  # la fusion déplace les points (moyenne) : on repasse tant que de nouveaux doublons apparaissent
        again = dedupe(merged)
        if len(again) == len(merged):
            break
        merged = again
    gdf = gpd.GeoDataFrame(merged, geometry=gpd.points_from_xy(merged.x, merged.y), crs=CRS_LAMBERT93)

    inside = covers(boundary, gdf.geometry.to_numpy())
    n_hors = int((~inside).sum())
    gdf = gdf.loc[inside]

    final = gdf[["arret_id", "nom", "mode", "nb_passages_jour", "reseau", "geometry"]] \
        .sort_values(["nom", "arret_id"]).reset_index(drop=True)
    validate_stops(final, boundary)

    report = quality_report(final, n_brut, n_hors, bool(args.contours),
                            wanted or "automatique (mardi le plus chargé)", echecs, non_gtfs)
    publish_stops(final, report)
    print(report)
    print(f"\nÉcrit : {SILVER / 'arrets.parquet'}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"Échec de la collecte des arrêts : {exc}")
