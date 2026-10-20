"""Prépare une livraison régionale séparée, avec audit des stations vélo fixes GBFS.

Les sources du pilote et des autres rôles restent inchangées. Les réseaux en
free-floating sont inventoriés, sans transformer leurs places en stations.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import union_all

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = "https://transport.data.gouv.fr/api/datasets"
CRS = 2154


def in_region(dataset, codes):
    areas = dataset.get("covered_area") or []
    if not areas:
        return True
    return any(a.get("type") == "pays" or str(a.get("insee")) in codes.get(a.get("type"), set()) for a in areas)


def bike_dataset(dataset):
    return dataset.get("type") == "vehicles-sharing" and ("bicycle" in (dataset.get("sub_types") or []) or dataset.get("title", "").lower().startswith("vls"))


def prepare(args):
    contours = gpd.read_parquet(args.contours).to_crs(CRS)
    communes = contours.loc[contours.niveau.eq("commune")].copy()
    territory = union_all(communes.geometry)
    codes = {"region": {"84"}, "commune": set(communes.code_insee),
             "departement": set(communes.code_insee.str[:2]),
             "epci": set(contours.code_epci.dropna())}
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    def fetch(url, attempts=3, timeout=30):
        # Un relevé brut par URL ; un échec de téléchargement ne devient pas 0.
        path = args.raw_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        with lock:
            if path.exists() and not args.refresh:
                return json.loads(path.read_text())
        error = None
        for _ in range(attempts):
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()
                data = response.json()
                with lock:
                    path.write_text(json.dumps(data, ensure_ascii=False))
                return data
            except (requests.RequestException, ValueError) as exc:
                error = exc
        raise error
    datasets = fetch(CATALOGUE, timeout=120)
    candidates = [d for d in datasets if bike_dataset(d) and in_region(d, codes)]
    # Réutiliser les lecteurs GBFS versionnés par l'équipe, en archivant les réponses.
    sys.path.insert(0, str(ROOT / "optim"))
    gbfs = importlib.import_module("build_offre_partagee")
    gbfs.get_json = fetch
    jobs, excluded = [], []
    for dataset in candidates:
        if "freefloating" in (dataset.get("sub_types") or []):
            excluded.append({"reseau": dataset["title"], "statut": "hors_perimetre_free_floating"})
            continue
        resources = [r for r in dataset.get("resources", []) if (r.get("format") or "").lower() == "gbfs" and r.get("url")]
        if not resources:
            excluded.append({"reseau": dataset["title"], "statut": "aucun_flux_gbfs"})
        jobs.extend((dataset, resource["url"]) for resource in resources)
    print(f"Vélos fixes : {len(jobs)} flux à auditer ; {len(excluded)} jeux non exploités.", flush=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda job: gbfs.read_feed(*job), jobs))
    reports = [report for report, rows in results]
    raw = pd.DataFrame([row for report, rows in results for row in rows])
    if raw.empty:
        raise ValueError("Aucune station récupérée : sources existantes conservées, aucun export remplacé.")
    notes = []
    bikes = gbfs.clean(raw, notes)
    bikes = bikes.loc[bikes["type"].eq("velo") & ~bikes.a_venir].copy()
    before = len(bikes)
    bikes = bikes.loc[bikes.geometry.covered_by(territory)].copy()
    bikes = bikes.loc[~bikes.en_service.eq(False).fillna(False)].copy()
    bikes = bikes.drop(columns=[c for c in ["lat", "lon", "a_venir"] if c in bikes])
    # Le relevé antérieur sert de repli explicite aux seuls systèmes absents du
    # nouveau relevé. Ce n'est pas un état temps réel.
    old = gpd.read_parquet(args.stations).to_crs(CRS)
    fallback = old.loc[old["type"].eq("velo") & ~old.system_id.isin(set(bikes.system_id))].copy()
    bikes["releve_conserve"] = False
    fallback["releve_conserve"] = True
    bikes = gpd.GeoDataFrame(pd.concat([bikes, fallback], ignore_index=True), crs=CRS)
    bikes = bikes.loc[bikes.geometry.covered_by(territory)].copy()
    if bikes.station_id.duplicated().any():
        raise ValueError("Identifiants de station vélo dupliqués.")
    others = old.loc[~old["type"].eq("velo") & old.geometry.covered_by(territory)].copy()
    stations = gpd.GeoDataFrame(pd.concat([others, bikes], ignore_index=True), crs=CRS)
    stops = gpd.read_parquet(args.arrets).to_crs(CRS)
    outside = stops.loc[~stops.geometry.covered_by(territory)].copy()
    stops = stops.loc[stops.geometry.covered_by(territory)].copy()
    public_transport = gpd.GeoDataFrame({
        "offre_id": "arret_" + stops.arret_id.astype(str), "source": "arrets", "type": stops["mode"],
        "nom": stops.nom, "operateur": stops.reseau, "valeur": stops.nb_passages_jour,
        "unite": "passages_par_jour", "geometry": stops.geometry,
    }, crs=CRS)
    sharing = gpd.GeoDataFrame({
        "offre_id": "station_" + stations.station_id.astype(str), "source": "stations_partagees", "type": stations["type"],
        "nom": stations.nom, "operateur": stations.operateur, "valeur": stations.capacite,
        "unite": "capacite", "geometry": stations.geometry,
    }, crs=CRS)
    offers = gpd.GeoDataFrame(pd.concat([public_transport, sharing], ignore_index=True), crs=CRS)
    if offers.offre_id.duplicated().any():
        raise ValueError("Identifiants d'offre dupliqués.")
    population = gpd.read_parquet(args.population).to_crs(CRS)
    if not population.id_carreau.is_unique or not np.isfinite(population.population).all() or not population.population.gt(0).all():
        raise ValueError("Population invalide.")
    # Conserver les carreaux frontaliers de l'étude, même si leur centre est hors IGN.
    if not population.geometry.intersects(territory).all():
        raise ValueError("Des carreaux sont entièrement hors de l'ARA : vérifier la source.")
    candidates_pop = population[["id_carreau", "population", "geometry"]].copy()
    candidates_pop.geometry = candidates_pop.geometry.centroid
    for column in ["code_commune", "faible_population", "donnee_masquee"]:
        if column in population:
            candidates_pop[column] = population[column]
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "region": "84", "catalogue": CATALOGUE,
        "definition": "Stations de réseaux GBFS en stations ; free-floating et zones virtuelles de ces opérateurs exclus.",
        "stations_velo": len(bikes), "stations_ancien_releve_conserve": int(bikes.releve_conserve.sum()),
        "reseaux_velo": bikes.groupby(["system_id", "reseau"]).size().rename("stations").reset_index().to_dict("records"),
        "stations_velo_grenoble": int(bikes.geometry.covered_by(union_all(communes.loc[communes.code_insee.eq("38185")].geometry)).sum()),
        "offres": len(offers), "offres_par_type": offers["type"].value_counts().to_dict(), "arrets_hors_ara_ecartes": len(outside),
        "population": float(population.population.sum()), "carreaux": len(population),
        "centres_population_hors_ign_conserves": int((~candidates_pop.geometry.covered_by(territory)).sum()),
        "flux": reports, "jeux_non_exploites": excluded, "nettoyage": notes, "stations_avant_recoupe": before,
        "limites": ["Catalogue et flux non exhaustifs ; ancien relevé signalé par releve_conserve si le système n'est pas récupéré.",
                    "Les stations extérieures à l'ARA ne sont pas inventoriées ; desserte aux frontières régionales à interpréter comme régionale.",
                    "Fréquences source conservées, zéro compris ; dédoublonnage inter-flux des départs non garanti.",
                    "Les contrôles initiaux signalaient une paire de même nom à moins de 50 m ; les identifiants source sont conservés."],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir) as tmp:
        stage = Path(tmp)
        for name, frame in [("stations_velo", bikes), ("stations_partagees", stations), ("arrets", stops),
                            ("arrets_hors_ara", outside), ("gold_offre", offers), ("gold_candidates", candidates_pop)]:
            frame.to_parquet(stage / f"{name}.parquet", index=False)
        (stage / "sources_ara_qc.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        for path in stage.iterdir():
            path.replace(args.output_dir / path.name)
    print(f"ARA : {len(bikes)} stations vélo, {len(offers)} points d'offre, {len(population)} carreaux.", flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contours", type=Path, default=ROOT / "data/silver/contours.parquet")
    ap.add_argument("--stations", type=Path, default=ROOT / "data/silver/stations_partagees.parquet")
    ap.add_argument("--arrets", type=Path, default=ROOT / "data/silver/arrets.parquet")
    ap.add_argument("--population", type=Path, default=ROOT / "data/silver/carreaux_socio.parquet")
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/bronze/velos_ara")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/silver/ara")
    ap.add_argument("--refresh", action="store_true")
    return ap


if __name__ == "__main__":
    prepare(parser().parse_args())
