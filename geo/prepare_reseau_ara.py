"""Fige les entrées et planifie la construction du réseau piéton régional.

Ce script ne télécharge rien et ne construit pas encore de graphe. Les points
sont affectés à une seule tuile principale ; la marge sert au futur routage.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import box, covers, intersects, prepare as prepare_geometry, union_all

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.build_gold import candidates_from_carreaux, offre_from_arrets, offre_from_stations
from scripts.build_arrets import NETWORKS

CRS = 2154


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_table(path, key, geometry_types, required):
    frame = gpd.read_parquet(path)
    missing = set([key, *required]) - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name} : colonnes absentes {sorted(missing)}")
    if frame.empty or frame.crs is None:
        raise ValueError(f"{path.name} : table vide ou projection absente")
    frame = frame.to_crs(CRS)
    if frame[key].isna().any() or not frame[key].is_unique:
        raise ValueError(f"{path.name} : identifiants absents ou dupliqués")
    if (frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid).any():
        raise ValueError(f"{path.name} : géométries absentes, vides ou invalides")
    if not frame.geom_type.isin(geometry_types).all():
        raise ValueError(f"{path.name} : types géométriques inattendus")
    return frame


def tile_ids(points, tile_m):
    """Une origine sur une limite appartient à la tuile à droite/en haut."""
    xs = np.floor(points.geometry.x.to_numpy() / tile_m).astype(np.int64)
    ys = np.floor(points.geometry.y.to_numpy() / tile_m).astype(np.int64)
    return pd.Series([f"x{x}_y{y}" for x, y in zip(xs, ys)], index=points.index, dtype="string")


def plan_tiles(boundary, tile_m, margin_m):
    if not math.isfinite(tile_m) or tile_m <= 0:
        raise ValueError("Taille de tuile invalide")
    if not math.isfinite(margin_m) or margin_m < 0:
        raise ValueError("Marge invalide")
    # La bordure comprend les centres de carreaux frontaliers conservés.
    study = boundary.buffer(margin_m)
    prepare_geometry(study)
    xmin, ymin, xmax, ymax = study.bounds
    rows = []
    for ix in range(math.floor(xmin / tile_m), math.floor(xmax / tile_m) + 1):
        for iy in range(math.floor(ymin / tile_m), math.floor(ymax / tile_m) + 1):
            x, y = ix * tile_m, iy * tile_m
            core = box(x, y, x + tile_m, y + tile_m)
            if not study.intersects(core):
                continue
            rows.append({
                "tuile_id": f"x{ix}_y{iy}", "indice_x": ix, "indice_y": iy,
                "xmin": x, "ymin": y, "xmax": x + tile_m, "ymax": y + tile_m,
                "surface_ara_km2": core.intersection(boundary).area / 1e6,
                "geometry": core,
            })
    return gpd.GeoDataFrame(rows, crs=CRS)


def prepare(args):
    for name in ["taille_tuile_m", "marge_m", "duree_s", "vitesse_kmh"]:
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Paramètre non positif : {name}")
    # Deux rayons couvrent les voisins utiles aux deux côtés d'un trajet local.
    minimum_margin = 2 * args.duree_s * args.vitesse_kmh / 3.6
    if args.marge_m < minimum_margin:
        raise ValueError(f"Marge insuffisante : au moins {minimum_margin:g} m requis")
    if args.output_dir.exists():
        raise ValueError("Le dossier de préparation existe déjà ; choisir un nouveau --output-dir")
    paths = {"contours": args.contours, "territoire": args.territoire, "arrets": args.arrets,
             "stations": args.stations, "population": args.population}
    # Calculer avant lecture et revérifier avant publication pour refuser un
    # remplacement simultané par le pipeline d'un autre rôle.
    hashes = {name: sha256(path) for name, path in paths.items()}
    print("Contrôle des contours et des sources sélectionnées…", flush=True)
    contours = read_table(args.contours, "id_zone", ["Polygon", "MultiPolygon"], ["niveau", "code_insee"])
    communes = contours.loc[contours.niveau.eq("commune")]
    if communes.empty:
        raise ValueError("Aucune commune dans les contours")
    territory = gpd.read_parquet(args.territoire).to_crs(CRS)
    if territory.empty or (territory.geometry.isna() | territory.geometry.is_empty | ~territory.geometry.is_valid).any():
        raise ValueError("Contour régional de référence invalide")
    boundary = union_all(communes.geometry)
    if boundary.symmetric_difference(union_all(territory.geometry)).area > 1:
        raise ValueError("Les communes ne couvrent pas le territoire de référence")
    prepare_geometry(boundary)
    stops = read_table(args.arrets, "arret_id", ["Point"], ["nom", "mode", "reseau", "nb_passages_jour"])
    stations = read_table(args.stations, "station_id", ["Point"], ["nom", "type", "operateur", "commune", "capacite"])
    population = read_table(args.population, "id_carreau", ["Polygon", "MultiPolygon"], ["population"])
    available = {name.strip() for name in ",".join(stops.reseau.dropna().astype(str)).split(",")}
    missing_networks = set(args.reseaux_requis) - available
    if missing_networks:
        raise ValueError(f"Livraison incomplète : réseaux absents {sorted(missing_networks)}")
    for name, frame in [("arrêts", stops), ("stations", stations)]:
        outside = ~covers(boundary, frame.geometry.to_numpy())
        if outside.any():
            raise ValueError(f"{int(outside.sum())} {name} hors ARA : utiliser une livraison validée")
    for frame, column, positive in [(stops, "nb_passages_jour", False), (population, "population", True),
                                     (stations, "capacite", False)]:
        values = pd.to_numeric(frame[column], errors="raise")
        known = values.dropna()
        if (column != "capacite" and values.isna().any()) or not np.isfinite(known).all() or known.lt(0).any():
            raise ValueError(f"Valeurs invalides dans {column}")
        if positive and known.le(0).any():
            raise ValueError("Population non positive")
    if not intersects(boundary, population.geometry.to_numpy()).all():
        raise ValueError("Des carreaux de population sont entièrement hors ARA")
    candidates = candidates_from_carreaux(population)
    if len(candidates) != len(population):
        raise ValueError("Des carreaux habités seraient exclus par le générateur de candidats")
    offers = gpd.GeoDataFrame(pd.concat([offre_from_arrets(stops), offre_from_stations(stations)], ignore_index=True), crs=CRS)
    if not offers.offre_id.is_unique:
        raise ValueError("Identifiants d'offre dupliqués")
    print(f"Entrées validées : {len(stops)} arrêts, {len(stations)} stations, {len(population)} carreaux.", flush=True)
    tiles = plan_tiles(boundary, args.taille_tuile_m, args.marge_m)
    print(f"Découpage régional : {len(tiles)} tuiles avec leurs marges.", flush=True)
    candidates["tuile_id"] = tile_ids(candidates, args.taille_tuile_m)
    offers["tuile_id"] = tile_ids(offers, args.taille_tuile_m)
    planned = set(tiles.tuile_id)
    if not set(candidates.tuile_id).issubset(planned) or not set(offers.tuile_id).issubset(planned):
        raise ValueError("Des points ne sont affectés à aucune tuile planifiée")
    for column, points in [("nb_centres_population", candidates), ("nb_points_offre", offers)]:
        tiles[column] = tiles.tuile_id.map(points.tuile_id.value_counts()).fillna(0).astype(int)
    tiles["calcul_population_offre_utile"] = (tiles.nb_centres_population + tiles.nb_points_offre).gt(0)
    extraction = tiles[["tuile_id", "geometry"]].copy()
    extraction.geometry = [box(row.xmin - args.marge_m, row.ymin - args.marge_m,
                               row.xmax + args.marge_m, row.ymax + args.marge_m)
                           for row in tiles.itertuples(index=False)]
    source_manifest = json.loads((args.osm_dir / "sources_osm.json").read_text())
    archives = []
    for name in ["auvergne", "rhone-alpes"]:
        path = args.osm_dir / f"{name}.osm.pbf"
        source = next((s for s in source_manifest["archives"] if s["nom"] == name), None)
        if source is None or sha256(path) != source["sha256"]:
            raise ValueError(f"Archive OSM absente ou empreinte incorrecte : {name}")
        archives.append({**source, "chemin": str(path.resolve())})
    print("Empreintes des archives OSM conformes ; écriture des entrées figées…", flush=True)
    if any(sha256(paths[name]) != digest for name, digest in hashes.items()):
        raise ValueError("Une source a été modifiée pendant la préparation")
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "region": "84", "crs": CRS,
        "etat": "preparation_terminee_graphe_non_construit", "graphe_construit": False,
        "parametres": {"taille_tuile_m": args.taille_tuile_m, "marge_m": args.marge_m,
                       "duree_s": args.duree_s, "vitesse_kmh": args.vitesse_kmh},
        "sources": {name: {"chemin": str(path.resolve()), "sha256": hashes[name]} for name, path in paths.items()},
        "reseaux_requis": sorted(set(args.reseaux_requis)), "reseaux_presents": sorted(available),
        "script": {"chemin": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "generateur_gold": {"chemin": str(ROOT / "scripts/build_gold.py"), "sha256": sha256(ROOT / "scripts/build_gold.py")},
        "territoire_reference": {"chemin": str(args.territoire.resolve()), "sha256": sha256(args.territoire)},
        "archives_osm": archives, "arrets": len(stops), "stations": len(stations), "offres": len(offers),
        "offres_par_type": offers["type"].value_counts().to_dict(), "carreaux": len(population),
        "population": float(population.population.sum()), "tuiles": len(tiles),
        "tuiles_avec_origines": int(tiles.calcul_population_offre_utile.sum()),
        "centres_frontaliers_conserves": int((~covers(boundary, candidates.geometry.to_numpy())).sum()),
        "capacites_inconnues": int(stations.capacite.isna().sum()),
        "regle_affectation": "Une tuile principale par point, indices floor(x/taille), floor(y/taille). Ne pas compter les copies des marges.",
        "limites": ["Couverture GTFS/GBFS limitée aux sources livrées ; absence de point ne prouve pas absence de service.",
                    "Les extractions OSM nationales peuvent être incomplètes pour les trajets traversant une frontière internationale.",
                    "Le filtre piéton, les restrictions et la connectivité du réseau restent à implémenter et contrôler.",
                    "Les capacités inconnues et les modes autre sont conservés ; aucun calcul O1/O2 n'est publié ici."],
    }
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    # Le dossier complet apparaît seulement après validation de tous les fichiers.
    with TemporaryDirectory(dir=args.output_dir.parent) as temp:
        stage = Path(temp) / "preparation"
        inputs = stage / "entrees"
        inputs.mkdir(parents=True)
        for name, frame in [("contours", contours), ("territoire", territory), ("arrets", stops), ("stations_partagees", stations), ("carreaux_socio", population),
                            ("gold_offre", offers), ("gold_candidates", candidates)]:
            frame.to_parquet(inputs / f"{name}.parquet", index=False)
        tiles.to_parquet(stage / "tuiles.parquet", index=False)
        extraction.to_parquet(stage / "zones_extraction.parquet", index=False)
        (stage / "zones_extraction.geojson").write_text(extraction.to_crs(4326).to_json(drop_id=True))
        report["fichiers"] = {str(p.relative_to(stage)): sha256(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        (stage / "plan_reseau_ara.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if any(sha256(paths[name]) != digest for name, digest in hashes.items()):
            raise ValueError("Une source a été modifiée pendant l'écriture ; préparation non publiée")
        if args.output_dir.exists():
            raise ValueError("Le dossier de sortie est apparu pendant la préparation")
        stage.rename(args.output_dir)
    print(f"Préparation ARA : {len(tiles)} tuiles, {len(offers)} offres, {len(population)} carreaux. Graphe non construit.", flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arrets", type=Path, required=True, help="Parquet d'une livraison contrôlée, avec TCL Lyon")
    ap.add_argument("--stations", type=Path, default=ROOT / "data/silver/stations_partagees.parquet")
    ap.add_argument("--population", type=Path, default=ROOT / "data/silver/carreaux_socio.parquet")
    ap.add_argument("--contours", type=Path, default=ROOT / "data/silver/contours.parquet")
    ap.add_argument("--territoire", type=Path, default=ROOT / "data/silver/territoire.parquet")
    ap.add_argument("--osm-dir", type=Path, default=ROOT / "data/bronze/osm_ara")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--taille-tuile-m", type=float, default=10000)
    ap.add_argument("--marge-m", type=float, default=1000)
    ap.add_argument("--duree-s", type=float, default=300)
    ap.add_argument("--vitesse-kmh", type=float, default=4.5)
    ap.add_argument("--reseaux-requis", nargs="+", default=list(NETWORKS), help="Réseaux attendus, 19 par défaut")
    return ap


if __name__ == "__main__":
    ap = parser()
    try:
        prepare(ap.parse_args())
    except (ValueError, OSError, KeyError) as exc:
        ap.exit(1, f"Erreur préparation ARA : {exc}\n")
