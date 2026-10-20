"""Télécharge et contrôle un réseau piéton OSM sur une sélection IGN explicite.

Exemple : python geo/build_reseau.py --codes-communes 38185 \
    --output-dir data/silver/reseau_grenoble
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import requests
from shapely import union_all


ROOT = Path(__file__).resolve().parent.parent
CRS = "EPSG:2154"
LIMITATIONS = [
    "Filtre walk OSMnx : dépend de la complétude des chemins et des tags OSM ; "
    "les cycleways sont exclus même si la marche y est parfois autorisée.",
    "Marche bidirectionnelle à vitesse constante ; pente, feux, attente, "
    "horaires, restrictions directionnelles piétonnes et accessibilité PMR non modélisés.",
    "Les obstacles ponctuels (portails, barrières) sont conservés comme tags, "
    "sans pénalité ni interdiction de passage calculée.",
    "Toutes les composantes sont conservées. Un trajet entre composantes est inaccessible.",
]


def select_zone(args):
    path = args.territoire or args.contours
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        raise ValueError("Projection absente dans les contours.")
    if args.codes_communes:
        if not {"niveau", "code_insee"}.issubset(frame.columns):
            raise ValueError("Les contours doivent contenir niveau et code_insee.")
        frame = frame.loc[(frame.niveau == "commune") & frame.code_insee.isin(args.codes_communes)]
        missing = set(args.codes_communes) - set(frame.code_insee)
        if missing:
            raise ValueError(f"Communes absentes des contours : {sorted(missing)}")
        if frame.code_insee.duplicated().any():
            raise ValueError("Codes communes en double dans les contours.")
    if frame.empty or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("La sélection doit contenir des polygones non vides.")
    if (frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid).any():
        raise ValueError("Géométries de contours absentes, vides ou invalides.")
    boundary = union_all(frame.to_crs(CRS).geometry)
    zone = boundary.buffer(args.marge_m)
    if zone.area / 1e6 > args.max_area_km2:
        raise ValueError(
            f"Zone avec marge : {zone.area / 1e6:.1f} km², limite : {args.max_area_km2:g}. "
            "Préférer des lots de communes pour Overpass ; augmenter --max-area-km2 explicitement si nécessaire."
        )
    return boundary, zone


def acquire_graph(args, zone):
    """Archive GraphML et cache brut séparés des tables dérivées."""
    digest = hashlib.sha256(zone.wkb).hexdigest()
    if args.graphml:
        graph = ox.load_graphml(
            args.graphml, edge_dtypes={"longueur_m": float, "temps_pieton_s": float},
            node_dtypes={"composante_id": int},
        )
        if graph.graph.get("zone_sha256") != digest or graph.graph.get("network_type") != "walk":
            raise ValueError("Le GraphML doit provenir de ce script, du même contour et de la même marge.")
        return graph, {"mode": "relecture_graphml", "graphml": str(args.graphml)}

    raw = args.raw_dir / digest[:16]
    raw.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = raw / "cache"
    ox.settings.log_console = False
    ox.settings.requests_timeout = args.timeout_s
    ox.settings.overpass_url = args.overpass_url.rstrip("/")
    ox.settings.bidirectional_network_types = ["walk"]
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | {
        "foot", "foot:forward", "foot:backward", "oneway:foot", "surface", "incline",
        "smoothness", "lit", "sidewalk", "wheelchair", "sac_scale",
    })
    ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | {
        "barrier", "access", "foot", "crossing", "wheelchair",
    })
    polygon = gpd.GeoSeries([zone], crs=CRS).to_crs(4326).iloc[0]
    print(f"Téléchargement OSM walk via {ox.settings.overpass_url} (cache activé)…", flush=True)
    graph = ox.graph_from_polygon(
        polygon, network_type="walk", retain_all=True,
        simplify=False, truncate_by_edge=True,
    )
    graph = ox.simplify_graph(
        graph, node_attrs_include=["barrier", "highway", "crossing", "access", "foot"],
        remove_rings=False,
    )
    graph.graph.update({
        "network_type": "walk", "zone_sha256": digest,
        "execution_acquisition_utc": datetime.now(timezone.utc).isoformat(),
        "overpass_url": ox.settings.overpass_url,
        "osmnx_acquisition_version": ox.__version__,
    })
    path = raw / "reseau_osm.graphml"
    ox.save_graphml(graph, path)
    return graph, {"mode": "overpass_avec_cache", "graphml": str(path), "cache": str(raw / "cache")}


def prepare_graph(graph, speed_kmh):
    if not math.isfinite(speed_kmh) or speed_kmh <= 0:
        raise ValueError("La vitesse doit être finie et strictement positive.")
    if not isinstance(graph, nx.MultiDiGraph) or not graph.nodes or not graph.edges:
        raise ValueError("Le graphe orienté doit contenir des nœuds et des tronçons.")
    if "crs" not in graph.graph:
        raise ValueError("Projection absente du graphe.")
    xy = np.array([(d.get("x", np.nan), d.get("y", np.nan)) for _, d in graph.nodes(data=True)], dtype=float)
    if not np.isfinite(xy).all():
        raise ValueError("Coordonnées de nœuds absentes ou non finies.")
    graph = ox.project_graph(graph, to_crs=CRS)
    nodes, edges = ox.graph_to_gdfs(graph)
    bad = edges.geometry.isna() | edges.geometry.is_empty | ~edges.geometry.is_valid
    bad |= ~edges.geom_type.eq("LineString") | ~np.isfinite(edges.geometry.length) | edges.geometry.length.le(0)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} tronçon(s) de géométrie invalide ou de longueur nulle.")
    # La longueur OSMnx pré-simplification reste dans length. Les poids de marche
    # sont mesurés sur la ligne projetée, donc dans la même unité que les contours.
    edges["longueur_m"] = edges.geometry.length
    edges["temps_pieton_s"] = edges.longueur_m / (speed_kmh / 3.6)
    components = sorted(nx.weakly_connected_components(graph), key=lambda c: (-len(c), min(c)))
    membership = {node: index for index, component in enumerate(components) for node in component}
    nodes["composante_id"] = [membership[node] for node in nodes.index]
    for node in graph:
        graph.nodes[node]["composante_id"] = membership[node]
    for (u, v, key), row in edges.iterrows():
        graph[u][v][key]["longueur_m"] = float(row.longueur_m)
        graph[u][v][key]["temps_pieton_s"] = float(row.temps_pieton_s)
    if not edges.index.is_unique or not nodes.index.is_unique:
        raise ValueError("Identifiants de nœuds ou de tronçons dupliqués.")
    return graph, nodes, edges, components


def route_checks(graph, nodes, component):
    """Trois trajets structurels reproductibles, sans prétendre valider le terrain."""
    selected = nodes.loc[sorted(component)]
    start = int(selected.geometry.x.idxmin())
    targets = [int(selected.geometry.x.idxmax()), int(selected.geometry.y.idxmin()), int(selected.geometry.y.idxmax())]
    lengths = nx.single_source_dijkstra_path_length(graph, start, weight="longueur_m")
    times = nx.single_source_dijkstra_path_length(graph, start, weight="temps_pieton_s")
    routes = []
    for end in dict.fromkeys(targets):
        if start == end:
            continue
        reachable = end in lengths
        routes.append({
            "de": start, "vers": end, "accessible": reachable,
            "longueur_m": lengths.get(end), "temps_pieton_s": times.get(end),
            "distance_directe_m": float(nodes.loc[start].geometry.distance(nodes.loc[end].geometry)),
        })
    return routes


def build_reseau(args):
    for value, label, allow_zero in [
        (args.marge_m, "marge", True), (args.vitesse_kmh, "vitesse", False),
        (args.max_area_km2, "surface maximale", False), (args.timeout_s, "délai", False),
    ]:
        if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
            raise ValueError(f"Valeur invalide pour {label}.")
    boundary, zone = select_zone(args)
    print(f"Zone : {zone.area / 1e6:.2f} km², marge {args.marge_m:g} m", flush=True)
    graph, acquisition = acquire_graph(args, zone)
    graph, nodes, edges, components = prepare_graph(graph, args.vitesse_kmh)
    nodes["dans_territoire"] = nodes.geometry.covered_by(boundary)
    nodes["dans_zone_marge"] = nodes.geometry.covered_by(zone)
    # Les tags OSM peuvent être scalaires ou listes après simplification.
    # GraphML garde les attributs originaux ; les Parquet ont un schéma stable.
    node_export = nodes[["geometry", "composante_id", "dans_territoire", "dans_zone_marge"]].reset_index(names="noeud_id")
    edge_export = edges[["geometry", "longueur_m", "temps_pieton_s"]].reset_index(names=["de", "vers", "cle"])
    for tag in ("osmid", "name", "highway", "foot", "surface", "access"):
        edge_export[f"{tag}_json"] = [
            json.dumps(graph[u][v][key].get(tag), ensure_ascii=False) for u, v, key in edges.index
        ]
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "crs": CRS,
        "source": "© OpenStreetMap contributors, ODbL", "licence": "https://www.openstreetmap.org/copyright",
        "osmnx_version": ox.__version__, "acquisition": acquisition,
        "network_type": "walk", "retain_all": True, "truncate_by_edge": True,
        "longueur_m_definition": "longueur de la géométrie du tronçon en EPSG:2154",
        "cle_troncon": ["de", "vers", "cle"],
        "acquisition_origine": {key: graph.graph.get(key) for key in (
            "execution_acquisition_utc", "overpass_url", "osmnx_acquisition_version", "zone_sha256",
        )},
        "date_snapshot_osm": None, "cache_possible": True,
        "contours_source": str(args.territoire or args.contours),
        "codes_communes": sorted(set(args.codes_communes)) if args.codes_communes else None,
        "marge_m": args.marge_m, "surface_territoire_km2": boundary.area / 1e6,
        "surface_zone_marge_km2": zone.area / 1e6, "vitesse_kmh_hypothese": args.vitesse_kmh,
        "distance_marche_5_min_m": args.vitesse_kmh / 3.6 * 300,
        "noeuds": len(nodes), "troncons_orientes": len(edges),
        "noeuds_dans_territoire": int(nodes.dans_territoire.sum()),
        "noeuds_hors_zone_marge": int((~nodes.dans_zone_marge).sum()),
        "composantes_faibles": len(components), "tailles_composantes": list(map(len, components)),
        "part_noeuds_composante_principale": len(components[0]) / len(nodes),
        "noeuds_isoles": len(list(nx.isolates(graph))),
        "geometries_invalides": 0, "identifiants_dupliques": 0,
        "trajets_controle_structurel": route_checks(graph, nodes, components[0]),
        "limitations": LIMITATIONS,
    }
    # Une validation échouée laisse les exports précédents intacts.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir) as temp:
        stage = Path(temp)
        node_export.to_parquet(stage / "reseau_pieton_noeuds.parquet", index=False)
        edge_export.to_parquet(stage / "reseau_pieton_troncons.parquet", index=False)
        gpd.GeoDataFrame({"marge_m": [args.marge_m]}, geometry=[zone], crs=CRS).to_parquet(stage / "zone_requete.parquet", index=False)
        ox.save_graphml(graph, stage / "reseau_pieton.graphml")
        (stage / "reseau_pieton_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(args.output_dir / path.name)
    print(f"Écrit dans {args.output_dir} : {len(nodes)} nœuds, {len(edges)} tronçons orientés, {len(components)} composantes.", flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    zone = ap.add_mutually_exclusive_group(required=True)
    zone.add_argument("--codes-communes", nargs="+", help="codes INSEE présents dans contours.parquet")
    zone.add_argument("--territoire", type=Path, help="GeoParquet polygonal à traiter en entier")
    ap.add_argument("--contours", type=Path, default=ROOT / "data/silver/contours.parquet")
    ap.add_argument("--output-dir", type=Path, required=True, help="dossier dédié à ce périmètre")
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/bronze/osm")
    ap.add_argument("--graphml", type=Path, help="rejouer hors ligne une archive produite par ce script")
    ap.add_argument("--marge-m", type=float, default=1000)
    ap.add_argument("--vitesse-kmh", type=float, default=4.5)
    ap.add_argument("--max-area-km2", type=float, default=1000)
    ap.add_argument("--timeout-s", type=int, default=180)
    ap.add_argument("--overpass-url", default="https://overpass-api.de/api")
    return ap


if __name__ == "__main__":
    arguments = parser()
    args = arguments.parse_args()
    try:
        build_reseau(args)
    except (ValueError, OSError, requests.RequestException, nx.NetworkXException) as exc:
        arguments.exit(1, f"Erreur réseau piéton : {exc}\n")
