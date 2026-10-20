"""Analyse les offres hors seuil à partir des sources GTFS/GBFS et OSM locales.

Ne corrige ni les coordonnées ni les accès : produit les preuves et les actions
à examiner, dans un nouveau dossier. Extraction OSM préalable avec provenance.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import LineString, Point, STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.build_reseau_ara import read_osm_xml, way_directions, node_passable
from geo.check_raccordements_ara import sha256, verify_files
from scripts.build_arrets import normalize, connected_labels


def source_cluster(stops, offer):
    """Reproduit l'identifiant du groupe, pas une correspondance au nom seul."""
    selected = stops.loc[stops.stop_name.map(normalize).eq(normalize(offer.nom))].copy()
    if "location_type" in selected:
        selected = selected.loc[selected.location_type.isin(["", "0"])]
    if selected.empty:
        raise ValueError(f"Arrêt absent de la source : {offer.offre_id}")
    xy = gpd.GeoSeries(gpd.points_from_xy(selected.stop_lon.astype(float), selected.stop_lat.astype(float)), crs=4326).to_crs(2154)
    selected["x"], selected["y"] = xy.x.values, xy.y.values
    labels = connected_labels(selected[["x", "y"]].to_numpy(), 50)
    for label in sorted(set(labels)):
        group = selected.loc[labels == label].copy()
        x, y = group.x.mean(), group.y.mean()
        identifier = hashlib.blake2s(f"{normalize(offer.nom)}|{round(x)}|{round(y)}".encode(), digest_size=5).hexdigest()
        if "arret_" + identifier == offer.offre_id:
            if Point(x, y).distance(offer.geometry) > .001:
                raise ValueError("Identifiant reproduit mais coordonnées sources différentes")
            return group
    raise ValueError(f"Fusion source non reproduite : {offer.offre_id}")


def classify(source_kind, references, near_tags, near_distance):
    if source_kind == "arrets" and references == 0:
        return "sans_horaire_dans_gtfs", "role_1_transport", "Vérifier le statut de l'arrêt dans l'offre ; ne pas inventer un mode ni une desserte."
    access = near_tags.get("foot", near_tags.get("access", "yes"))
    if near_distance <= 50 and access in {"private", "no", "customers", "destination"}:
        return "acces_proche_restreint", "role_1_transport_et_geo", "Vérifier l'entrée publique et l'autorisation piétonne ; conserver les restrictions OSM."
    owner = "role_4_stations_et_geo" if source_kind == "stations_partagees" else "role_1_transport_et_geo"
    return "ecart_source_reseau_a_confirmer", owner, "Vérifier la position et l'accès réel ; aucun déplacement ni élargissement automatique du seuil."


def build(args):
    if args.output_dir.exists():
        raise ValueError("Refus d'écraser un audit existant")
    extraction_path = args.extraction / "extraction.json"
    extraction = json.loads(extraction_path.read_text())
    verify_files(args.extraction, extraction["fichiers"])
    plan_path = ROOT / "data/silver/preparation_reseau_ara/plan_reseau_ara.json"
    plan = json.loads(plan_path.read_text())
    if extraction["archives_sources_sha256"] != {a["nom"]: a["sha256"] for a in plan["archives_osm"]}:
        raise ValueError("Extraction OSM incompatible avec le réseau")
    if sha256(args.raccordements) != extraction["raccordements_offre_sha256"]:
        raise ValueError("Raccordements différents de ceux de l'extraction")
    frame = gpd.read_parquet(args.raccordements)
    offers = frame.loc[frame.statut.ne("ok")].copy()
    if offers.empty or offers.crs.to_epsg() != 2154:
        raise ValueError("Offres hors seuil requises en EPSG:2154")
    source_hashes = {str(args.raccordements): sha256(args.raccordements), str(extraction_path): sha256(extraction_path),
                     str(plan_path): sha256(plan_path), str(Path(__file__)): sha256(Path(__file__)),
                     str(ROOT / "geo/build_reseau_ara.py"): sha256(ROOT / "geo/build_reseau_ara.py")}
    nodes, ways, relations, counts = {}, {}, {}, Counter()
    for path in sorted(args.extraction.glob("*.osm")):
        read_osm_xml(path, nodes, ways, relations, counts)
    transform = Transformer.from_crs(4326, 2154, always_xy=True)
    xy = {n: transform.transform(v["lon"], v["lat"]) for n, v in nodes.items()}
    segments = []
    for wid, way in ways.items():
        tags = way["tags"]
        if not (tags.get("highway") or tags.get("man_made") == "pier"):
            continue
        permission = way_directions(tags)
        for index, (u, v) in enumerate(zip(way["refs"], way["refs"][1:])):
            if u not in xy or v not in xy:
                raise ValueError("Référence OSM absente")
            geometry = LineString([xy[u], xy[v]])
            if geometry.length == 0:
                continue
            blocked = [n for n in (u, v) if not node_passable(nodes[n]["tags"])]
            segments.append({"osm_way_id": wid, "segment_osm": index, "autorise_modele": bool(permission[0] and not blocked),
                             "motif_filtre": permission[2] if not blocked else "noeud_acces_bloque",
                             "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
                             "noeuds_bloques_json": json.dumps(blocked), "geometry": geometry})
    osm = gpd.GeoDataFrame(segments, crs=2154)
    tree = STRtree(osm.geometry.to_numpy())
    allowed = osm.loc[osm.autorise_modele].reset_index(drop=True)
    allowed_tree = STRtree(allowed.geometry.to_numpy())
    gtfs_paths = {name: ROOT / "data/bronze/gtfs" / f"{name}.zip" for name in offers.loc[offers.source.eq("arrets"), "operateur"].unique()}
    for value in args.gtfs:
        name, path = value.split("=", 1)
        gtfs_paths[name] = Path(path)
    source_rows = []
    for operator, group in offers.loc[offers.source.eq("arrets")].groupby("operateur"):
        path = gtfs_paths[operator]
        source_hashes[str(path)] = sha256(path)
        with zipfile.ZipFile(path) as archive:
            stops = pd.read_csv(archive.open("stops.txt"), dtype=str, keep_default_na=False)
            clusters = {r.offre_id: source_cluster(stops, r) for r in group.itertuples()}
            ids = {i for c in clusters.values() for i in c.stop_id}
            references = Counter()
            for chunk in pd.read_csv(archive.open("stop_times.txt"), usecols=["stop_id"], dtype=str, chunksize=250000):
                references.update(chunk.loc[chunk.stop_id.isin(ids), "stop_id"].value_counts().to_dict())
            for identifier, cluster in clusters.items():
                for r in cluster.itertuples():
                    p = Point(r.x, r.y)
                    j = allowed_tree.nearest(p)
                    source_rows.append({"offre_id": identifier, "source": "GTFS", "identifiant_source": r.stop_id,
                                        "nom_source": r.stop_name, "references_stop_times": references[r.stop_id],
                                        "distance_voie_autorisee_m": p.distance(allowed.geometry.iloc[j]),
                                        "source_fichier": str(path), "geometry": p})
    raw_path = ROOT / "data/bronze/gbfs_stations_brut.parquet"
    raw = pd.read_parquet(raw_path)
    source_hashes[str(raw_path)] = sha256(raw_path)
    for offer in offers.loc[offers.source.eq("stations_partagees")].itertuples():
        # Préfixe du gold = station_ ; le reste est system_id:station_id GBFS.
        system, station = offer.offre_id.removeprefix("station_").split(":", 1)
        match = raw.loc[raw.system_id.eq(system) & raw.station_id.eq(station)]
        if len(match) != 1:
            raise ValueError("Station GBFS absente ou ambiguë")
        r = match.iloc[0]
        p = Point(transform.transform(float(r.lon), float(r.lat)))
        if p.distance(offer.geometry) > .001:
            raise ValueError("Coordonnées GBFS différentes de la livraison")
        j = allowed_tree.nearest(p)
        source_rows.append({"offre_id": offer.offre_id, "source": "GBFS", "identifiant_source": station,
                            "nom_source": r.nom, "references_stop_times": None,
                            "distance_voie_autorisee_m": p.distance(allowed.geometry.iloc[j]),
                            "source_fichier": str(raw_path), "geometry": p})
    originals = gpd.GeoDataFrame(source_rows, crs=2154)
    diagnostics, nearby = [], []
    for offer in offers.itertuples():
        nearest = osm.iloc[tree.nearest(offer.geometry)]
        accessible = allowed.iloc[allowed_tree.nearest(offer.geometry)]
        distance_allowed = offer.geometry.distance(accessible.geometry)
        if abs(distance_allowed - offer.distance_raccordement_m) > .001:
            raise ValueError(f"Écart entre réseau et OSM brut : {offer.offre_id}")
        rows = originals.loc[originals.offre_id.eq(offer.offre_id)]
        if rows.empty:
            raise ValueError(f"Preuve source absente : {offer.offre_id}")
        total_references = int(rows.references_stop_times.sum()) if offer.source == "arrets" else None
        tags = json.loads(nearest.tags_json)
        closest = offer.geometry.distance(nearest.geometry)
        cause, owner, action = classify(offer.source, total_references, tags, closest)
        same_name = []
        for n, v in nodes.items():
            if (v["tags"].get("highway") == "bus_stop" or v["tags"].get("public_transport") in {"platform", "stop_position"}) and normalize(v["tags"].get("name", "")) == normalize(offer.nom):
                distance = offer.geometry.distance(Point(xy[n]))
                if distance <= 300:
                    same_name.append({"osm_node_id": n, "distance_m": distance, "tags": v["tags"]})
        original_min = float(rows.distance_voie_autorisee_m.min())
        original_max = float(rows.distance_voie_autorisee_m.max())
        row = {"offre_id": offer.offre_id, "nom": offer.nom, "type": offer.type, "operateur": offer.operateur,
               "distance_reseau_m": offer.distance_raccordement_m, "diagnostic": cause, "role_concerne": owner,
               "action": action, "coordonnees_sources_reproduites": True, "nb_points_sources": len(rows),
               "references_stop_times": total_references, "distance_source_min_m": original_min,
               "distance_source_max_m": original_max, "au_moins_un_point_source_dans_seuil": original_min <= 50,
               "voie_osm_proche": int(nearest.osm_way_id), "distance_voie_osm_proche_m": closest,
               "tags_voie_proche_json": nearest.tags_json, "motif_filtre_voie_proche": nearest.motif_filtre,
               "voie_osm_autorisee": int(accessible.osm_way_id), "voie_autorisee_nom": json.loads(accessible.tags_json).get("name", ""),
               "osm_transport_meme_nom_json": json.dumps(same_name, ensure_ascii=False),
               "correction_automatique": False, "statut_calcul": "indetermine", "geometry": offer.geometry}
        diagnostics.append(row)
        for j in tree.query(offer.geometry, predicate="dwithin", distance=300):
            s = osm.iloc[j].to_dict()
            s.update(offre_id=offer.offre_id, distance_point_m=offer.geometry.distance(s["geometry"]))
            nearby.append(s)
    result = gpd.GeoDataFrame(diagnostics, crs=2154)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir.parent) as temp:
        stage = Path(temp) / "audit"
        stage.mkdir()
        result.to_parquet(stage / "diagnostic_offres.parquet", index=False)
        coords = result.to_crs(4326)
        table = result.drop(columns="geometry").copy()
        table["longitude"], table["latitude"] = coords.geometry.x, coords.geometry.y
        table.to_csv(stage / "diagnostic_offres.csv", index=False)
        (stage / "diagnostic_offres.geojson").write_text(coords.to_json(drop_id=True), encoding="utf-8")
        originals.to_parquet(stage / "points_sources.parquet", index=False)
        gpd.GeoDataFrame(nearby, crs=2154).to_parquet(stage / "voies_osm_proches.parquet", index=False)
        verify_files(args.extraction, extraction["fichiers"])
        for path, digest in source_hashes.items():
            if sha256(Path(path)) != digest:
                raise ValueError("Une source a changé pendant l'audit")
        report = {"execution_utc": datetime.now(timezone.utc).isoformat(), "etat": "audit_termine",
                  "points_examines": len(result), "diagnostics": result.diagnostic.value_counts().to_dict(),
                  "coordonnees_reproduites": int(result.coordonnees_sources_reproduites.sum()),
                  "voies_autorisees_conformes_au_reseau": len(result), "corrections_appliquees": 0,
                  "sources_sha256": source_hashes, "fichiers_sha256": {f.name: sha256(f) for f in stage.iterdir()},
                  "limites": ["Absence d'horaire constatée dans les GTFS locaux, pas preuve d'absence de transport sur le terrain.",
                              "OSM ne prouve pas l'autorisation réelle ; aucune restriction n'est supprimée sans confirmation.",
                              "La concordance des coordonnées sources n'en garantit pas la précision physique.",
                              "Audit des seules offres hors seuil ; métro, composantes secondaires et population restent des examens distincts."]}
        (stage / "audit_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.output_dir.exists():
            raise ValueError("Dossier final apparu pendant l'audit")
        stage.rename(args.output_dir)
    print(json.dumps({k: report[k] for k in ["etat", "points_examines", "diagnostics", "corrections_appliquees"]}, ensure_ascii=False, indent=2))
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raccordements", type=Path, default=ROOT / "data/silver/raccordements_ara/raccordements_offre.parquet")
    ap.add_argument("--extraction", type=Path, default=ROOT / "data/bronze/osm_ara/audit_raccordements_20261020")
    ap.add_argument("--gtfs", action="append", default=[], metavar="RESEAU=CHEMIN.zip")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/silver/audit_raccordements_offre_ara")
    return ap


if __name__ == "__main__":
    build(parser().parse_args())
