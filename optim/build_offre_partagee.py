"""Rôle 4 - table silver `stations_partagees` : stations Citiz, autopartage et vélos en libre-service.

Sources : flux GBFS « vehicles-sharing » de transport.data.gouv.fr (v1, v2, v3)
+ flux Citiz publiés directement par backend.citiz.fr (réseaux absents du catalogue).
- Citiz : toute la France (le modèle du Rôle 3 apprend sur toutes les stations Citiz).
- Autres opérateurs : seulement les jeux dont la zone (covered_area) touche le territoire,
  plus les jeux nationaux ; recoupe spatiale finale sur le territoire (optim/territoire.py ou --contours).
Le relevé du jour est réutilisé s'il existe (data/bronze/gbfs_cache.json) ; --refresh pour le refaire.

Sorties :
- data/bronze/gbfs_flux.csv                        état de chaque flux (ok, free-floating, échec…)
- data/bronze/gbfs_stations_brut.parquet           stations telles que lues
- data/silver/stations_partagees_france.parquet    stations Citiz de France (entraînement du modèle, Rôle 3)
- data/silver/stations_partagees.parquet           territoire
- data/silver/stations_a_venir.parquet             stations annoncées « Ouverture prochaine » (France), hors offre existante
- data/silver/stations_partagees_qc.txt            rapport qualité
Le free-floating (pas de station_information, ou zones virtuelles d'opérateurs free-floating)
est hors périmètre : seulement compté dans le rapport.
Usage : python optim/build_offre_partagee.py [--contours data/silver/contours.parquet] [--refresh]
"""
import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

import territoire as T

TDG_API = "https://transport.data.gouv.fr/api/datasets"
CITIZ_BACKEND = "https://backend.citiz.fr/public/provider/{}/gbfs/v3.0/gbfs.json"
CITIZ_PROVIDERS = range(1, 41)  # numéros de fournisseur testés (18 actifs en octobre 2026)
TIMEOUT = 15  # s par requête : un serveur qui ne répond pas est abandonné vite (sans nouvelle tentative)
CACHE_META = T.BRONZE / "gbfs_cache.json"

TYPE_BY_FORM_FACTOR = {"car": "autopartage", "bicycle": "velo", "cargo_bicycle": "velo",
                       "scooter": "trottinette", "scooter_standing": "trottinette",
                       "scooter_seated": "trottinette", "moped": "trottinette"}
TYPE_BY_SUB_TYPE = {"carsharing": "autopartage", "bicycle": "velo", "scooter": "trottinette"}
TYPE_PRIORITY = ["autopartage", "velo", "trottinette"]
TITLE_PREFIX = {"vls": "velo", "autopartage": "autopartage"}
MAX_PLACES = 150  # au-delà : agrégat ou erreur de flux (max observé en station réelle ~ 100)
BOOL = {"true": True, "1": True, "false": False, "0": False}
# stations non ouvertes au public, repérées par l'EDA Citiz (ex. « Véhicules hors-circuit », « DEMOBOX (Réservé Citiz) »)
NOT_PUBLIC = re.compile(r"hors[- ]circuit|demobox|r[ée]serv[ée] citiz", re.IGNORECASE)
ELECTRIC = re.compile(r"[ée]lectrique", re.IGNORECASE)
# station annoncée mais pas encore ouverte (Citiz Grand Est surtout) : pas une offre existante,
# mais un signal utile (lieux déjà choisis par Citiz) -> table à part
UPCOMING = re.compile(r"ouverture prochaine", re.IGNORECASE)
COLUMNS = ["station_id", "station_id_gbfs", "operateur", "type", "capacite", "nb_vehicules", "commune", "nom_commune",
           "nom", "reseau", "system_id", "nb_places_libres", "en_service", "station_virtuelle",
           "type_stationnement", "electrique_nom", "date_extraction", "geometry"]


def get_json(url, attempts=3, timeout=TIMEOUT):
    """Lecture d'un flux, avec nouvelles tentatives sur les réponses invalides (ex. Vélo'v renvoie
    parfois un JSON tronqué). Un serveur qui ne répond pas (timeout) n'est pas relancé."""
    for attempt in range(attempts):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.HTTPError, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))


def text(value):
    """GBFS v3 : liste [{text, language}] ; v1/v2 : chaîne."""
    if isinstance(value, list):
        by_lang = {v.get("language"): v.get("text") for v in value if isinstance(v, dict)}
        return by_lang.get("fr") or next(iter(by_lang.values()), None)
    return value


def feed_urls(data, url):
    """Feeds d'un gbfs.json : v3 data.feeds ; v1/v2 data.<langue>.feeds (fr de préférence).
    Certaines ressources pointent directement sur station_information : on la prend telle quelle."""
    stations = data.get("stations")
    if stations:  # station_information si coordonnées, sinon station_status isolé (inexploitable seul)
        return {"station_information": url} if "lat" in stations[0] else {}
    feeds = data.get("feeds")
    if feeds is None:
        lang = data.get("fr") or next((v for v in data.values() if isinstance(v, dict) and "feeds" in v), {})
        feeds = lang.get("feeds", [])
    return {f["name"]: f["url"] for f in feeds if f.get("name") and f.get("url")}


def feed_type(feeds, sub_types, title):
    found = set()
    if "vehicle_types" in feeds:
        try:
            vts = get_json(feeds["vehicle_types"])["data"]["vehicle_types"]
            found = {TYPE_BY_FORM_FACTOR.get(v.get("form_factor")) for v in vts} - {None}
        except Exception:
            pass
    if not found:
        found = {TYPE_BY_SUB_TYPE[s] for s in sub_types if s in TYPE_BY_SUB_TYPE}
    if not found:  # dernier recours : intitulé du jeu de données (« VLS … », « Autopartage … »)
        found = {t for prefix, t in TITLE_PREFIX.items() if title.lower().startswith(prefix)}
    return next((t for t in TYPE_PRIORITY if t in found), None)


def read_feed(dataset, url):
    report = {"reseau": dataset["title"], "url": url, "statut": "ok", "n_stations": 0}
    try:
        data = get_json(url).get("data", {})
        if "datasets" in data:
            # manifest v3 (Getaround : autopartage entre particuliers, sans stations, API limitée en débit)
            report["statut"] = f"manifest de {len(data['datasets'])} systèmes, non exploité"
            return report, []
        feeds = feed_urls(data, url)
        if not feeds:
            report["statut"] = "ressource partielle (pas un gbfs.json)"
            return report, []
        if "station_information" not in feeds:
            report["statut"] = "sans stations (free-floating)"
            return report, []
        stations = get_json(feeds["station_information"])["data"]["stations"]
        status = {}
        if "station_status" in feeds:
            status = {s.get("station_id"): s for s in get_json(feeds["station_status"])["data"]["stations"]}
        operator = None
        if "system_information" in feeds:
            info = get_json(feeds["system_information"]).get("data", {})
            operator = text(info.get("operator")) or text(info.get("name"))
            report["system_id"] = info.get("system_id")
        typ = feed_type(feeds, dataset.get("sub_types") or [], dataset["title"])
    except Exception as e:  # flux hétérogènes ou hors ligne : on continue
        report["statut"] = f"échec : {type(e).__name__}"
        return report, []

    rows = []
    for s in stations:
        st = status.get(s.get("station_id"), {})
        rows.append({
            "station_id": str(s.get("station_id")),
            "nom": text(s.get("name")),
            "operateur": operator or (dataset.get("publisher") or {}).get("name"),
            "reseau": dataset["title"],
            "system_id": report.get("system_id"),
            "type": typ,
            "capacite": s.get("capacity"),
            "nb_vehicules": st.get("num_vehicles_available", st.get("num_bikes_available")),
            "nb_places_libres": st.get("num_docks_available"),
            "en_service": st.get("is_installed"),
            "station_virtuelle": s.get("is_virtual_station"),
            "type_stationnement": s.get("parking_type"),
            "free_floating": "freefloating" in (dataset.get("sub_types") or []),
            "lat": s.get("lat"), "lon": s.get("lon"),
        })
    report["n_stations"] = len(rows)
    return report, rows


def citiz_backend_jobs():
    """Réseaux Citiz publiés par backend.citiz.fr (certains manquent sur transport.data.gouv.fr)."""
    def probe(n):
        try:
            return requests.get(CITIZ_BACKEND.format(n), timeout=TIMEOUT).ok
        except requests.RequestException:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        ok = [n for n, alive in zip(CITIZ_PROVIDERS, pool.map(probe, CITIZ_PROVIDERS)) if alive]
    return [({"title": f"Citiz (backend fournisseur {n})", "sub_types": ["carsharing"]}, CITIZ_BACKEND.format(n))
            for n in ok]


def clean(df, qc):
    qc.append(f"Stations brutes (tous flux)          : {len(df)}")
    for col in ("lat", "lon", "capacite", "nb_vehicules", "nb_places_libres"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # valeur manquante ou impossible -> vide, jamais 0
    df.loc[df["capacite"] == 0, "capacite"] = np.nan  # une station sans place n'a pas de capacité connue
    for col in ("capacite", "nb_vehicules", "nb_places_libres"):
        df.loc[(df[col] < 0) | (df[col] > MAX_PLACES), col] = np.nan
        df[col] = df[col].astype("Int64")
    for col in ("en_service", "station_virtuelle"):  # selon les flux : bool, 0/1 ou "true"/"false"
        df[col] = df[col].map(lambda v: BOOL.get(str(v).lower())).astype("boolean")
    # zones de stationnement virtuelles des opérateurs free-floating : ce ne sont pas des stations.
    # Free-floating = jeu marqué « freefloating », ou flux d'un opérateur privé dont toutes les stations sont
    # virtuelles (ex. Bird Vichy). Les VLS publics à stations virtuelles (ex. Lovélo Rouen) sont gardés.
    virtual = df["station_virtuelle"].fillna(False)
    public_vls = df["reseau"].str.lower().str.startswith("vls")
    all_virtual = virtual.groupby(df["reseau"]).transform("all") & ~public_vls
    zones = (df["free_floating"] | all_virtual) & virtual
    df = df[~zones].drop(columns="free_floating")
    qc.append(f"Zones virtuelles free-floating        : {zones.sum()}")
    private = df["nom"].fillna("").str.contains(NOT_PUBLIC)
    qc.append(f"Stations non publiques (nom)          : {private.sum()}  "
              f"{sorted(df.loc[private, 'nom'].unique())}")
    df = df[~private]
    upcoming = df["nom"].fillna("").str.contains(UPCOMING)
    qc.append(f"Stations « Ouverture prochaine »      : {upcoming.sum()}  (écrites dans stations_a_venir)")
    df["a_venir"] = upcoming
    n = len(df)
    df = df.dropna(subset=["lat", "lon", "type"])
    df = df[df.lat.between(-22, 52) & df.lon.between(-62, 56) & ~((df.lat == 0) & (df.lon == 0))]
    qc.append(f"Sans coordonnées ou type valides      : {n - len(df)}")
    # un même système peut être publié par plusieurs jeux (dont Citiz : catalogue + backend)
    n = len(df)
    key = df["system_id"].fillna(df["reseau"])
    df = df[~pd.DataFrame({"k": key, "id": df["station_id"]}).duplicated()].copy()
    qc.append(f"Doublons retirés (system_id + id)     : {n - len(df)}")
    # l'id GBFS n'est unique que dans un système (ex. « 1001 » chez Citiz et chez Vélo'v) :
    # station_id = <system_id>:<id GBFS>, unique en France (offre_id de gold_offre)
    df["station_id_gbfs"] = df["station_id"]
    df["station_id"] = key.loc[df.index] + ":" + df["station_id_gbfs"]
    citiz = df["system_id"].str.startswith("citiz", na=False) | df["reseau"].str.contains("citiz", case=False)
    df.loc[citiz, "type"] = "citiz"
    df.loc[citiz, "reseau"] = df.loc[citiz, "system_id"].fillna(df.loc[citiz, "reseau"])
    # minimum : le réseau de Lyon n'indique pas la motorisation dans le nom (voir vehicle_types)
    df["electrique_nom"] = df["nom"].fillna("").str.contains(ELECTRIC)
    df["date_extraction"] = pd.Timestamp(date.today())
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326).to_crs(T.CRS_LAMBERT93)


def download(communes):
    """Relevé GBFS : Citiz partout, autres opérateurs seulement si leur zone touche le territoire."""
    codes = T.area_codes(communes)
    datasets = [d for d in get_json(TDG_API, timeout=120) if d.get("type") == "vehicles-sharing"]
    kept = [d for d in datasets if "citiz" in d["title"].lower() or T.covers(d.get("covered_area"), codes)]
    print(f"Jeux GBFS gardés : {len(kept)} / {len(datasets)} (Citiz + zone touchant le territoire + nationaux)")
    jobs = [(d, r["url"]) for d in kept for r in d.get("resources", [])
            if (r.get("format") or "").lower() == "gbfs" and r.get("url")]
    jobs += citiz_backend_jobs()  # après le catalogue : en cas de doublon, l'intitulé du catalogue est gardé
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda job: read_feed(*job), jobs))
    flux = pd.DataFrame([r for r, _ in results])
    return flux, pd.DataFrame([row for _, rows in results for row in rows])


def summary(gdf):
    return (gdf.groupby("type").agg(stations=("station_id", "size"), reseaux=("reseau", "nunique"),
                                    vehicules=("nb_vehicules", "sum"),
                                    capacite_manquante=("capacite", lambda s: s.isna().sum()))
               .to_string())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contours", help="contour du territoire (table contours du Rôle 2)")
    ap.add_argument("--refresh", action="store_true", help="refaire le relevé GBFS même s'il date d'aujourd'hui")
    args = ap.parse_args()

    communes = T.load_communes(args.contours)
    territory = f"{T.NIVEAU}:{','.join(T.CODES)}" if args.contours is None else str(args.contours)
    T.BRONZE.mkdir(parents=True, exist_ok=True)
    T.SILVER.mkdir(parents=True, exist_ok=True)
    meta = json.loads(CACHE_META.read_text()) if CACHE_META.exists() else {}
    if not args.refresh and meta == {"date": date.today().isoformat(), "territoire": territory}:
        print(f"Relevé GBFS du jour réutilisé ({CACHE_META.name}) ; --refresh pour le refaire")
        flux = pd.read_csv(T.BRONZE / "gbfs_flux.csv")
        raw = pd.read_parquet(T.BRONZE / "gbfs_stations_brut.parquet")
    else:
        flux, raw = download(communes)
        flux.to_csv(T.BRONZE / "gbfs_flux.csv", index=False)
        raw.astype({"capacite": str, "nb_vehicules": str, "nb_places_libres": str,
                    "en_service": str, "station_virtuelle": str}).to_parquet(T.BRONZE / "gbfs_stations_brut.parquet")
        CACHE_META.write_text(json.dumps({"date": date.today().isoformat(), "territoire": territory}))

    qc = ["Rapport qualité - table stations_partagees", "=" * 44,
          f"Flux lus : {len(flux)}", flux["statut"].str.split(" :").str[0].value_counts().to_string(), ""]
    france = clean(raw, qc)
    national_cols = [c for c in COLUMNS if c not in ("commune", "nom_commune")]
    france[france["a_venir"]][national_cols].reset_index(drop=True).to_parquet(T.SILVER / "stations_a_venir.parquet")
    france = france[~france["a_venir"]]
    france[france["type"] == "citiz"][national_cols].to_parquet(T.SILVER / "stations_partagees_france.parquet")
    qc += ["", "Lu (Citiz France + autres opérateurs du territoire et nationaux) :", summary(france)]

    terr, n_out = T.clip(france, communes)
    terr = terr[COLUMNS].sort_values(["type", "reseau", "station_id"]).reset_index(drop=True)
    assert terr["station_id"].is_unique, "station_id en double"
    terr.to_parquet(T.SILVER / "stations_partagees.parquet")
    qc += ["", f"Territoire ({territory}) :",
           summary(terr), f"Communes avec une station Citiz : {terr.loc[terr['type'] == 'citiz', 'commune'].nunique()}",
           "\nPar réseau :", terr.groupby(["type", "reseau"]).size().to_string(),
           f"Stations Citiz « électriques » d'après le nom : {terr.loc[terr['type'] == 'citiz', 'electrique_nom'].sum()}",
           "Type de stationnement Citiz :", terr.loc[terr['type'] == 'citiz', 'type_stationnement'].value_counts(dropna=False).to_string(),
           "\nValeurs manquantes :", terr.drop(columns="geometry").isna().sum()[lambda s: s > 0].to_string()]
    report = "\n".join(qc)
    (T.SILVER / "stations_partagees_qc.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nÉcrit : {T.SILVER / 'stations_partagees.parquet'}")


if __name__ == "__main__":
    main()
