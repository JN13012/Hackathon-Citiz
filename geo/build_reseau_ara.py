"""Construit les graphes piétons ARA depuis les PBF et une préparation figée.

Exemple : python geo/build_reseau_ara.py --tuiles x91_y645
Sans --tuiles, construit toutes les tuiles planifiées. --reprendre vérifie les
empreintes des résultats existants avant de les réutiliser. Aucun appel réseau.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.build_reseau import prepare_graph, route_checks
from geo.prepare_reseau_ara import sha256

RULE_VERSION = 2
PUBLIC = {"yes", "designated", "official", "permissive"}
WALK_HIGHWAYS = {
    "residential", "living_street", "service", "unclassified", "tertiary", "tertiary_link",
    "secondary", "secondary_link", "primary", "primary_link", "road", "track", "path",
    "footway", "pedestrian", "steps", "crossing", "corridor",
}
PASSABLE_BARRIERS = {"no", "bollard", "cycle_barrier", "kerb", "entrance", "gate", "lift_gate",
                     "swing_gate", "kissing_gate", "stile", "cattle_grid", "sally_port"}
FILTERS = ["w/highway", "r/restriction:foot", "r/restriction:foot:conditional"]
LIMITATIONS = [
    "Vitesse constante ; pente, attentes, PMR et accès au bâti non modélisés.",
    "Les tags OSM absents ne prouvent pas l'accessibilité sur le terrain.",
    "Accès conditionnels piétons exclus sans interprétation des horaires ; accès destination/customers/private exclus.",
    "Cycleways et bridleways retenus seulement si une autorisation piétonne explicite existe.",
    "Barrières inconnues ou verrouillées exclues ; barrières linéaires sans connexion OSM non modélisées.",
    "Hypothèse du projet : toute voie retenue est parcourable dans les deux sens ; sens uniques, sens des escaliers mécaniques et restrictions de virage ignorés.",
    "Extractions complete_ways : une voie sans aucun nœud dans la fenêtre peut manquer ; bordures internationales limitées aux archives fournies.",
    "Toutes les composantes sont conservées ; les marges se recouvrent et ne doivent pas être additionnées.",
    "Le réseau est bidirectionnel comme le calculateur existant ; l'orchestration des origines par tuile reste nécessaire pour O1/O2 régionaux.",
]


def value(tags, key, default=""):
    return tags.get(key, default).strip().lower()


def conditional(tags):
    return any(tags.get(k) for k in ("access:conditional", "foot:conditional"))


def way_directions(tags):
    """Une voie autorisée à pied est parcourable dans les deux sens dans ce modèle."""
    highway = value(tags, "highway")
    if not highway or value(tags, "area") == "yes" or highway in {"construction", "proposed", "abandoned", "razed", "platform", "elevator"}:
        return False, False, "hors_reseau"
    if conditional(tags):
        return False, False, "acces_conditionnel"
    explicit = any(value(tags, k) in PUBLIC for k in ("foot", "foot:forward", "foot:backward"))
    if (highway not in WALK_HIGHWAYS or value(tags, "motorroad") == "yes") and not explicit:
        return False, False, "classe_sans_autorisation_pietonne"
    base = value(tags, "foot", value(tags, "access", "yes"))
    allowed = base in PUBLIC or any(value(tags, k) in PUBLIC for k in ("foot:forward", "foot:backward"))
    return allowed, allowed, "retenue" if allowed else "acces_interdit_ou_inconnu"


def node_passable(tags):
    if conditional(tags) or value(tags, "locked") in {"yes", "1", "true"}:
        return False
    access = value(tags, "foot", value(tags, "access", "yes"))
    if access not in PUBLIC:
        return False
    barrier = value(tags, "barrier")
    return not barrier or barrier in PASSABLE_BARRIERS or value(tags, "foot") in PUBLIC


def read_osm_xml(path, nodes, ways, restrictions, counts):
    """Fusion par identifiant OSM ; toute version ou donnée contradictoire est bloquante."""
    destinations = {"node": nodes, "way": ways, "relation": restrictions}
    source = path.open("rb")
    context = ET.iterparse(source, events=("start", "end"))
    try:
        _, root = next(context)
        for event, elem in context:
            if event != "end" or elem.tag not in destinations:
                continue
            kind, identifier = elem.tag, int(elem.attrib["id"])
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in elem.findall("tag")}
            record = {"version": int(elem.get("version", "0")), "tags": tags}
            if elem.get("visible", "true") != "true":
                raise ValueError(f"Objet OSM supprimé dans le snapshot : {kind} {identifier}")
            if kind == "node":
                record.update(lon=float(elem.attrib["lon"]), lat=float(elem.attrib["lat"]))
                if not (math.isfinite(record["lon"]) and math.isfinite(record["lat"]) and
                        -180 <= record["lon"] <= 180 and -90 <= record["lat"] <= 90):
                    raise ValueError(f"Coordonnées OSM invalides : {identifier}")
            elif kind == "way":
                record["refs"] = tuple(int(nd.attrib["ref"]) for nd in elem.findall("nd"))
            else:
                record["members"] = tuple((m.attrib["type"], int(m.attrib["ref"]), m.get("role", "")) for m in elem.findall("member"))
            target = destinations[kind]
            if identifier in target:
                if target[identifier] != record:
                    raise ValueError(f"Conflit OSM {kind} {identifier} : versions ou données différentes entre archives")
                counts[f"doublons_{kind}_fusionnes"] += 1
            else:
                target[identifier] = record
            root.remove(elem)
    finally:
        if hasattr(context, "close"):
            context.close()
        source.close()
    return nodes, ways, restrictions


def graph_from_objects(nodes, ways, restrictions, zone, speed):
    counts = Counter(noeuds_osm=len(nodes), voies_osm=len(ways))
    selected = {}
    for identifier, way in ways.items():
        forward, backward, reason = way_directions(way["tags"])
        counts[f"voies_{reason}"] += 1
        if forward or backward:
            if any(k in way["tags"] for k in ("oneway", "oneway:foot", "oneway:foot:conditional",
                    "foot:forward", "foot:backward", "foot:forward:conditional", "foot:backward:conditional", "conveying")):
                counts["voies_tags_direction_ignores"] += 1
            if len(way["refs"]) < 2 or any(n not in nodes for n in way["refs"]):
                raise ValueError(f"Voie {identifier} : références de nœuds absentes ou insuffisantes")
            selected[identifier] = (way, forward, backward)
    for relation in restrictions.values():
        if any(k.startswith("restriction:foot") for k in relation["tags"]) and any(
                kind == "way" and ref in selected for kind, ref, _ in relation["members"]):
            counts["relations_restriction_pietonne_ignorees"] += 1
    refs = sorted({n for way, _, _ in selected.values() for n in way["refs"]})
    transformer = Transformer.from_crs(4326, 2154, always_xy=True)
    xs, ys = transformer.transform([nodes[n]["lon"] for n in refs], [nodes[n]["lat"] for n in refs])
    positions = dict(zip(refs, zip(xs, ys)))
    if not np.isfinite([xy for xy in positions.values()]).all():
        raise ValueError("Coordonnées non finies après projection")
    blocked = {n for n in refs if not node_passable(nodes[n]["tags"])}
    counts["noeuds_acces_bloque"] = len(blocked)
    # OSMnx conserve les géométries fournies pendant la projection avec ce drapeau.
    # Les sorties rétablissent simplified=False : chaque nœud OSM est conservé.
    graph = nx.MultiDiGraph(crs="EPSG:2154", simplified=True, network_type="walk",
                            construction="segments_osm_non_simplifies", regles_version=RULE_VERSION)
    for identifier, (way, forward, backward) in sorted(selected.items()):
        for index, (u, v) in enumerate(zip(way["refs"], way["refs"][1:])):
            if u in blocked or v in blocked:
                counts["segments_barriere_ou_acces_bloque"] += 1
                continue
            if u == v:
                counts["references_consecutives_identiques_supprimees"] += 1
                continue
            line = LineString([positions[u], positions[v]])
            if line.length <= 0:
                raise ValueError(f"Voie {identifier} : nœuds distincts de coordonnées identiques")
            if not line.intersects(zone):
                counts["segments_hors_fenetre"] += 1
                continue
            for n in (u, v):
                if n not in graph:
                    x, y = positions[n]
                    graph.add_node(n, x=x, y=y, **{k: nodes[n]["tags"][k] for k in ("barrier", "foot", "access") if k in nodes[n]["tags"]})
            tags = {k: way["tags"][k] for k in ("name", "highway", "foot", "access", "surface", "oneway:foot", "foot:forward", "foot:backward") if k in way["tags"]}
            for a, b, direction, geom in [(u, v, 1, line), (v, u, -1, LineString(list(line.coords)[::-1]))]:
                if (direction == 1 and forward) or (direction == -1 and backward):
                    # Clé stable entre tuiles, sans fusion de chemins parallèles réels.
                    key = identifier * 1_000_000 + index
                    if index >= 1_000_000 or graph.has_edge(a, b, key):
                        raise ValueError("Clé de segment OSM dupliquée ou voie trop longue")
                    graph.add_edge(a, b, key=key, osmid=identifier, segment_osm=index,
                                   direction_osm=direction, length=geom.length, geometry=geom, **tags)
    if not graph.edges:
        graph.graph["simplified"] = False
        return graph, None, None, [], dict(counts)
    graph, node_frame, edge_frame, components = prepare_graph(graph, speed)
    for u, v, key, data in graph.edges(keys=True, data=True):
        if not graph.has_edge(v, u, key) or not math.isclose(data["temps_pieton_s"], graph[v][u][key]["temps_pieton_s"], abs_tol=1e-6):
            raise ValueError("Le modèle exige chaque segment dans les deux sens avec le même temps de marche")
    graph.graph["modele_marche"] = "bidirectionnel"
    graph.graph["simplified"] = False
    return graph, node_frame, edge_frame, components, dict(counts)


def run_osmium(*args):
    subprocess.run(["osmium", *map(str, args)], check=True)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify_files(directory, files):
    for name, digest in files.items():
        path = directory / name
        if not path.is_relative_to(directory) or ".." in Path(name).parts or not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Fichier absent ou modifié : {path}")


def source_archives(args, plan):
    result = []
    for archive in plan["archives_osm"]:
        name = archive["nom"]
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError("Nom d'archive invalide")
        path = args.osm_dir / f"{name}.osm.pbf"
        if sha256(path) != archive["sha256"]:
            raise ValueError(f"Archive OSM modifiée : {name}")
        result.append({"nom": name, "path": path, "sha256": archive["sha256"]})
    if not result or len({a["nom"] for a in result}) != len(result):
        raise ValueError("Archives absentes ou répétées dans le plan")
    return result


def filtered_archive(archive, cache):
    """Cache adressé par contenu ; conserve tous les nœuds référencés et leurs tags."""
    directory = cache / archive["sha256"]
    directory.mkdir(parents=True, exist_ok=True)
    target, receipt = directory / "highways.osm.pbf", directory / "cache.json"
    signature = {"source_sha256": archive["sha256"], "filtres": FILTERS}
    if receipt.exists():
        cached = json.loads(receipt.read_text())
        if cached["signature"] != signature:
            raise ValueError("Cache de filtre incompatible")
        verify_files(directory, cached["fichiers"])
        return target
    with TemporaryDirectory(dir=directory) as temp:
        stage = Path(temp) / target.name
        print(f"Préfiltrage OSM {archive['nom']}…", flush=True)
        run_osmium("tags-filter", archive["path"], *FILTERS, "-o", stage)
        digest = sha256(stage)
        stage.replace(target)
        write_json(Path(temp) / "cache.json", {"signature": signature, "fichiers": {target.name: digest}})
        (Path(temp) / "cache.json").replace(receipt)
    return target


def tile_receipt(directory, signature):
    report = json.loads((directory / "reseau_pieton_qc.json").read_text())
    if report["signature"] != signature or report["tuile_id"] != directory.name:
        raise ValueError(f"Tuile incompatible avec les paramètres actuels : {directory.name}")
    expected = {"reseau_pieton_noeuds.parquet", "reseau_pieton_troncons.parquet", "reseau_pieton.graphml", "zone_requete.parquet"}
    if set(report["fichiers"]) != expected:
        raise ValueError("Livraison de tuile incomplète")
    verify_files(directory, report["fichiers"])
    return report


def publish_tile(directory, xmls, zone, core, speed, signature, useful):
    nodes, ways, restrictions, counts = {}, {}, {}, Counter()
    for path in xmls:
        read_osm_xml(path, nodes, ways, restrictions, counts)
    graph, node_frame, edge_frame, components, cleaning = graph_from_objects(nodes, ways, restrictions, zone, speed)
    if not graph.edges and useful:
        # Une fenêtre avec origines vide ne doit pas devenir un résultat d'accès nul.
        raise ValueError(f"Aucun tronçon piéton dans {directory.name} (tuile avec origines : {useful})")
    if not graph.edges:
        node_frame = gpd.GeoDataFrame({"composante_id": pd.Series(dtype="int64")}, geometry=[], crs=2154)
        edge_frame = gpd.GeoDataFrame({
            key: pd.Series(dtype="float64" if key in {"longueur_m", "temps_pieton_s"} else "int64")
            for key in ("longueur_m", "temps_pieton_s", "osmid", "segment_osm", "direction_osm")
        }, geometry=[], crs=2154)
        edge_frame.index = pd.MultiIndex.from_arrays([[], [], []], names=["u", "v", "key"])
    node_export = node_frame[["geometry", "composante_id"]].reset_index(names="noeud_id")
    node_export["dans_tuile"] = node_export.geometry.covered_by(core)
    node_export["dans_zone_marge"] = node_export.geometry.covered_by(zone)
    edge_export = edge_frame[["geometry", "longueur_m", "temps_pieton_s", "osmid", "segment_osm", "direction_osm"]].reset_index(names=["de", "vers", "cle"])
    for tag in ("name", "highway", "foot", "access", "surface"):
        edge_export[f"{tag}_json"] = [json.dumps(graph[u][v][k].get(tag), ensure_ascii=False) for u, v, k in edge_frame.index]
    # Vérification des extrémités après projection et des poids dans les deux exports.
    for u, v, _, data in graph.edges(keys=True, data=True):
        line = data["geometry"]
        if Point(line.coords[0]).distance(Point(graph.nodes[u]["x"], graph.nodes[u]["y"])) > 0.001 or Point(line.coords[-1]).distance(Point(graph.nodes[v]["x"], graph.nodes[v]["y"])) > 0.001:
            raise ValueError("Extrémités de tronçon incohérentes")
        if not math.isclose(data["temps_pieton_s"], data["longueur_m"] / (speed / 3.6), abs_tol=1e-6):
            raise ValueError("Temps piéton incohérent")
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "signature": signature,
        "etat": "valide" if graph.edges else "valide_sans_troncon_sans_origine",
        "tuile_id": directory.name, "crs": "EPSG:2154", "network_type": "walk",
        "modele_marche": "bidirectionnel",
        "source": "© OpenStreetMap contributors, ODbL", "licence": "https://www.openstreetmap.org/copyright",
        "vitesse_kmh_hypothese": speed, "nettoyage": {**dict(counts), **cleaning},
        "noeuds": len(node_frame), "troncons_orientes": len(edge_frame),
        "composantes_faibles": len(components), "tailles_composantes": list(map(len, components)),
        "noeuds_hors_zone_marge": int((~node_export.dans_zone_marge).sum()),
        "geometries_invalides": 0, "identifiants_dupliques": 0,
        "arcs_sans_retour_direct": sum(not graph.has_edge(v, u) for u, v in graph.edges()),
        "trajets_controle_structurel": route_checks(graph, node_frame, components[0]) if components else [],
        "limitations": LIMITATIONS,
    }
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory.parent) as temp:
        stage = Path(temp) / directory.name
        stage.mkdir()
        node_export.to_parquet(stage / "reseau_pieton_noeuds.parquet", index=False)
        edge_export.to_parquet(stage / "reseau_pieton_troncons.parquet", index=False)
        gpd.GeoDataFrame({"tuile_id": [directory.name]}, geometry=[zone], crs=2154).to_parquet(stage / "zone_requete.parquet", index=False)
        ox.save_graphml(graph, stage / "reseau_pieton.graphml")
        # Relecture des exports avant publication de tout le dossier.
        restored = ox.load_graphml(stage / "reseau_pieton.graphml", edge_dtypes={"longueur_m": float, "temps_pieton_s": float})
        if set(restored.edges(keys=True)) != set(graph.edges(keys=True)) or len(gpd.read_parquet(stage / "reseau_pieton_noeuds.parquet")) != len(node_export) or len(gpd.read_parquet(stage / "reseau_pieton_troncons.parquet")) != len(edge_export):
            raise ValueError("Relecture des exports incohérente")
        report["fichiers"] = {p.name: sha256(p) for p in stage.iterdir()}
        write_json(stage / "reseau_pieton_qc.json", report)
        if directory.exists():
            raise ValueError(f"Refus d'écraser une tuile existante : {directory}")
        stage.rename(directory)
    return report


def build(args):
    if not shutil.which("osmium"):
        raise ValueError("osmium-tool requis (déjà déclaré dans le Dockerfile)")
    if args.lot < 1 or args.lot > 32:
        raise ValueError("--lot doit être compris entre 1 et 32")
    plan_path = args.preparation / "plan_reseau_ara.json"
    plan_hash = sha256(plan_path)
    plan = json.loads(plan_path.read_text())
    verify_files(args.preparation, plan["fichiers"])
    archives = source_archives(args, plan)
    speed = plan["parametres"]["vitesse_kmh"]
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Vitesse invalide dans le plan")
    tiles = gpd.read_parquet(args.preparation / "tuiles.parquet").set_index("tuile_id")
    zones = gpd.read_parquet(args.preparation / "zones_extraction.parquet").set_index("tuile_id")
    for frame in (tiles, zones):
        if frame.crs is None or frame.crs.to_epsg() != 2154 or not frame.index.is_unique or (frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid).any():
            raise ValueError("Tuiles ou fenêtres invalides")
    if set(tiles.index) != set(zones.index):
        raise ValueError("Tuiles et fenêtres non alignées")
    all_ids = sorted(tiles.index)
    requested = sorted(set(args.tuiles)) if args.tuiles else all_ids
    if not requested or not set(requested).issubset(all_ids) or any(not re.fullmatch(r"x-?\d+_y-?\d+", t) for t in requested):
        raise ValueError("Sélection de tuiles vide ou inconnue")
    signature = {"plan_sha256": plan_hash, "constructeur_sha256": sha256(Path(__file__)),
                 "validation_sha256": sha256(ROOT / "geo/build_reseau.py"), "regles_version": RULE_VERSION,
                 "osmnx_version": ox.__version__, "vitesse_kmh": speed,
                 "archives": {a["nom"]: a["sha256"] for a in archives}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any((args.output_dir / t).exists() for t in requested) and not args.reprendre:
        raise ValueError("Tuiles existantes : utiliser --reprendre ou un nouveau dossier de sortie")
    reports, pending = {}, []
    # Vérifier aussi les tuiles existantes hors sélection avant toute écriture.
    for tile in all_ids:
        directory = args.output_dir / tile
        if directory.exists():
            reports[tile] = tile_receipt(directory, signature)
        elif tile in requested:
            pending.append(tile)
    filtered = [(a, filtered_archive(a, args.cache_dir)) for a in archives] if pending else []
    for start in range(0, len(pending), args.lot):
        batch = pending[start:start + args.lot]
        print(f"Extraction du lot {start // args.lot + 1} : {', '.join(batch)}", flush=True)
        with TemporaryDirectory(dir=args.output_dir) as temp:
            stage = Path(temp)
            xmls = {tile: [] for tile in batch}
            for archive, path in filtered:
                config = {"extracts": [{"output": str(stage / f"{tile}-{archive['nom']}.osm.pbf"),
                    "bbox": list(Transformer.from_crs(2154, 4326, always_xy=True).transform_bounds(*zones.loc[tile].geometry.bounds, densify_pts=21))} for tile in batch]}
                config_path = stage / "extracts.json"
                write_json(config_path, config)
                run_osmium("extract", path, "-c", config_path, "--strategy", "complete_ways")
                for tile in batch:
                    pbf = stage / f"{tile}-{archive['nom']}.osm.pbf"
                    xml = pbf.with_suffix(".osm")
                    run_osmium("cat", pbf, "-o", xml)
                    xmls[tile].append(xml)
            for tile in batch:
                reports[tile] = publish_tile(args.output_dir / tile, xmls[tile], zones.loc[tile].geometry,
                    tiles.loc[tile].geometry, speed, signature, bool(tiles.loc[tile, "calcul_population_offre_utile"]))
                print(f"Tuile {tile} validée : {reports[tile]['noeuds']} nœuds, {reports[tile]['troncons_orientes']} arcs", flush=True)
    # Ne jamais annoncer une couverture régionale pour un simple échantillon.
    if sha256(plan_path) != plan_hash:
        raise ValueError("Plan modifié pendant la construction")
    verify_files(args.preparation, plan["fichiers"])
    source_archives(args, plan)
    summary = {"execution_utc": datetime.now(timezone.utc).isoformat(), "signature": signature,
               "etat": "regional_termine" if len(reports) == len(all_ids) else "partiel",
               "tuiles_planifiees": len(all_ids), "tuiles_validees": len(reports),
               "tuiles_manquantes": sorted(set(all_ids) - set(reports)),
               "tuiles": {t: {"noeuds": r["noeuds"], "troncons_orientes": r["troncons_orientes"],
                              "qc_sha256": sha256(args.output_dir / t / "reseau_pieton_qc.json")} for t, r in sorted(reports.items())},
               "o1_o2_calcules": False, "limitations": LIMITATIONS}
    with TemporaryDirectory(dir=args.output_dir) as temp:
        target = Path(temp) / "construction_reseau_ara.json"
        write_json(target, summary)
        target.replace(args.output_dir / target.name)
    print(f"Construction {summary['etat']} : {len(reports)}/{len(all_ids)} tuiles validées.", flush=True)
    return summary


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--osm-dir", type=Path, default=ROOT / "data/bronze/osm_ara")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "data/bronze/osm_ara/cache_reseau")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--tuiles", nargs="+", help="identifiants du plan ; toutes les tuiles par défaut")
    ap.add_argument("--lot", type=int, default=1, help="extractions groupées, de 1 à 32 ; 1 limite la mémoire")
    ap.add_argument("--reprendre", action="store_true", help="réutiliser seulement les sorties compatibles et intègres")
    return ap


if __name__ == "__main__":
    ap = parser()
    try:
        build(ap.parse_args())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, nx.NetworkXException) as exc:
        ap.exit(1, f"Erreur construction réseau ARA : {exc}\n")
