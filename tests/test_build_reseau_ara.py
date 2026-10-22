"""Tests de routage, de fusion et de publication du constructeur PBF régional."""

from collections import Counter
import copy
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import geopandas as gpd
import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import box

from geo.build_reseau_ara import (build, graph_from_objects, node_passable, parser,
                                  publish_tile, read_osm_xml, sha256, way_directions, load_tile_graph)
from geo.build_isochrones import WalkingNetwork

X, Y = 910000, 6450000
ZONE = box(X - 100, Y - 100, X + 200, Y + 200)
TO_WGS = Transformer.from_crs(2154, 4326, always_xy=True)


def objects():
    nodes = {}
    # Deux chemins se croisent géométriquement sans partager de nœud (pont).
    for identifier, x, y in [(1, 0, 0), (2, 50, 0), (3, 100, 0), (4, 50, -50), (5, 50, 50)]:
        lon, lat = TO_WGS.transform(X + x, Y + y)
        nodes[identifier] = {"version": 1, "lon": lon, "lat": lat, "tags": {}}
    ways = {10: {"version": 1, "refs": (1, 2, 3), "tags": {"highway": "residential", "name": "Rue & test"}},
            11: {"version": 1, "refs": (4, 5), "tags": {"highway": "footway"}}}
    return nodes, ways


def write_xml(path, nodes, ways, restrictions=None):
    root = ET.Element("osm", version="0.6", generator="fixture")
    for identifier, node in sorted(nodes.items()):
        element = ET.SubElement(root, "node", id=str(identifier), version=str(node["version"]), lon=str(node["lon"]), lat=str(node["lat"]))
        for k, v in node["tags"].items():
            ET.SubElement(element, "tag", k=k, v=v)
    for identifier, way in sorted(ways.items()):
        element = ET.SubElement(root, "way", id=str(identifier), version=str(way["version"]))
        for ref in way["refs"]:
            ET.SubElement(element, "nd", ref=str(ref))
        for k, v in way["tags"].items():
            ET.SubElement(element, "tag", k=k, v=v)
    for identifier, relation in sorted((restrictions or {}).items()):
        element = ET.SubElement(root, "relation", id=str(identifier), version="1")
        for kind, ref, role in relation["members"]:
            ET.SubElement(element, "member", type=kind, ref=str(ref), role=role)
        for k, v in relation["tags"].items():
            ET.SubElement(element, "tag", k=k, v=v)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


class WalkingPolicyTests(unittest.TestCase):
    def test_direction_tags_do_not_limit_the_bidirectional_model(self):
        self.assertEqual(way_directions({"highway": "residential", "oneway": "yes"})[:2], (True, True))
        for tags in [{"oneway:foot": "yes"}, {"oneway:foot": "-1"}, {"oneway:foot": "no"},
                     {"oneway:foot": "reversible"}, {"foot:forward": "no"}, {"foot:backward": "no"},
                     {"conveying": "forward"}, {"conveying": "backward"}, {"conveying": "reversible"},
                     {"oneway:foot:conditional": "yes @ (Mo-Fr)"}, {"foot:forward:conditional": "no @ (night)"}]:
            with self.subTest(tags=tags):
                self.assertEqual(way_directions({"highway": "footway", **tags})[:2], (True, True))

    def test_foot_permission_overrides_generic_access(self):
        self.assertEqual(way_directions({"highway": "path", "access": "private", "foot": "yes"})[:2], (True, True))
        for access in ["no", "private", "customers", "destination", "use_sidepath", "unknown"]:
            self.assertEqual(way_directions({"highway": "footway", "foot": access})[:2], (False, False))

    def test_cycleways_require_foot_permission_and_construction_is_excluded(self):
        self.assertFalse(way_directions({"highway": "cycleway"})[0])
        self.assertTrue(way_directions({"highway": "cycleway", "foot": "designated"})[0])
        for highway in ["construction", "proposed", "platform", "motorway"]:
            self.assertFalse(way_directions({"highway": highway})[0])
        self.assertFalse(way_directions({"highway": "pedestrian", "area": "yes"})[0])

    def test_general_access_conditions_are_still_excluded(self):
        for tags in [{"foot:conditional": "no @ (night)"}, {"access:conditional": "yes @ (Mo-Fr)"}]:
            self.assertEqual(way_directions({"highway": "footway", **tags})[:2], (False, False))

    def test_barriers_and_node_access(self):
        for tags in [{"barrier": "bollard"}, {"barrier": "gate"}, {"access": "private", "foot": "yes"}]:
            self.assertTrue(node_passable(tags))
        for tags in [{"barrier": "wall"}, {"barrier": "mystery"}, {"locked": "yes", "foot": "yes"},
                     {"foot": "no"}, {"barrier": "gate", "access": "private"}, {"foot:conditional": "yes @ (day)"}]:
            self.assertFalse(node_passable(tags))


class TopologyTests(unittest.TestCase):
    def test_real_osm_junctions_only_and_metric_walking_costs(self):
        nodes, ways = objects()
        graph, _, _, components, _ = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        self.assertAlmostEqual(nx.shortest_path_length(graph, 1, 3, weight="temps_pieton_s"), 80, places=4)
        self.assertEqual(sorted(map(len, components)), [2, 3])
        self.assertFalse(nx.has_path(graph, 1, 4))

    def test_barrier_is_not_bypassed_by_simplification(self):
        nodes, ways = objects()
        nodes[2]["tags"] = {"barrier": "gate", "locked": "yes"}
        graph, _, _, _, qc = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        self.assertNotIn(2, graph)
        self.assertEqual(qc["segments_barriere_ou_acces_bloque"], 2)
        self.assertFalse(graph.has_edge(1, 3))

    def test_parallel_ways_are_preserved_and_travel_times_are_symmetric(self):
        nodes, ways = objects()
        ways[10]["tags"]["oneway:foot"] = "yes"
        ways[12] = copy.deepcopy(ways[10])
        graph, _, _, _, _ = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        self.assertEqual(graph.number_of_edges(1, 2), 2)
        self.assertTrue(nx.has_path(graph, 1, 3))
        self.assertEqual(nx.shortest_path_length(graph, 1, 3, weight="temps_pieton_s"),
                         nx.shortest_path_length(graph, 3, 1, weight="temps_pieton_s"))
        for u, v, key, data in graph.edges(keys=True, data=True):
            self.assertEqual(data["temps_pieton_s"], graph[v][u][key]["temps_pieton_s"])
        self.assertIs(WalkingNetwork(graph, 4.5).graph, graph)

    def test_missing_nodes_fail(self):
        nodes, ways = objects()
        del nodes[2]
        with self.assertRaisesRegex(ValueError, "références"):
            graph_from_objects(nodes, ways, {}, ZONE, 4.5)

    def test_superposed_distinct_nodes_are_nudged_without_losing_connection(self):
        # règles v3 : nœuds distincts au même endroit, décalés de 1 mm au lieu d'arrêter la construction
        nodes, ways = objects()
        nodes[2] = copy.deepcopy(nodes[1])
        graph, _, _, _, qc = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        self.assertTrue(nx.has_path(graph, 1, 3))
        self.assertEqual(qc["noeuds_superposes_decales_1mm"], 1)
        self.assertTrue(all(d["longueur_m"] > 0 for _, _, d in graph.edges(data=True)))

    def test_duplicate_adjacent_reference_removed_without_losing_connection(self):
        nodes, ways = objects()
        ways[10]["refs"] = (1, 2, 2, 3)
        graph, _, _, _, qc = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        self.assertTrue(nx.has_path(graph, 1, 3))
        self.assertEqual(qc["references_consecutives_identiques_supprimees"], 1)

    def test_foot_turn_restriction_is_counted_and_ignored_in_this_model(self):
        nodes, ways = objects()
        relations = {50: {"members": (("way", 10, "from"),), "tags": {"restriction:foot": "no_left_turn"}}}
        graph, _, _, _, qc = graph_from_objects(nodes, ways, relations, ZONE, 4.5)
        self.assertTrue(nx.has_path(graph, 1, 3))
        self.assertTrue(nx.has_path(graph, 3, 1))
        self.assertEqual(qc["relations_restriction_pietonne_ignorees"], 1)

    def test_overlapping_tiles_keep_identical_segment_keys(self):
        nodes, ways = objects()
        a, *_ = graph_from_objects(nodes, ways, {}, ZONE, 4.5)
        b, *_ = graph_from_objects(nodes, ways, {}, box(X + 20, Y - 100, X + 200, Y + 200), 4.5)
        common = set(a.edges(keys=True)) & set(b.edges(keys=True))
        self.assertTrue(common)
        for u, v, k in common:
            self.assertEqual(a[u][v][k]["geometry"], b[u][v][k]["geometry"])
            self.assertEqual(a[u][v][k]["temps_pieton_s"], b[u][v][k]["temps_pieton_s"])


class FusionAndPublicationTests(unittest.TestCase):
    def test_xml_duplicates_are_fused_but_version_and_data_conflicts_fail(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "source.osm"
            nodes, ways = objects()
            write_xml(path, nodes, ways)
            merged_nodes, merged_ways, relations, counts = {}, {}, {}, Counter()
            read_osm_xml(path, merged_nodes, merged_ways, relations, counts)
            read_osm_xml(path, merged_nodes, merged_ways, relations, counts)
            self.assertEqual(len(merged_ways), 2)
            self.assertEqual(counts["doublons_node_fusionnes"], 5)
            self.assertEqual(merged_ways[10]["tags"]["name"], "Rue & test")
            nodes[1]["version"] = 2
            write_xml(path, nodes, ways)
            with self.assertRaisesRegex(ValueError, "Conflit OSM"):
                read_osm_xml(path, merged_nodes, merged_ways, relations, counts)

    def test_export_roundtrip_bidirectional_and_failure_preserves_existing_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "input.osm"
            nodes, ways = objects()
            ways[10]["tags"]["oneway:foot"] = "yes"
            write_xml(path, nodes, ways)
            directory = root / "x91_y645"
            qc = publish_tile(directory, [path, path], ZONE, ZONE, 4.5, {"fixture": True}, True)
            graph = ox.load_graphml(directory / "reseau_pieton.graphml", edge_dtypes={"temps_pieton_s": float})
            self.assertEqual(nx.shortest_path_length(graph, 1, 3, weight="temps_pieton_s"),
                             nx.shortest_path_length(graph, 3, 1, weight="temps_pieton_s"))
            self.assertEqual(qc["modele_marche"], "bidirectionnel")
            self.assertEqual(qc["arcs_sans_retour_direct"], 0)
            self.assertEqual(qc["nettoyage"]["doublons_way_fusionnes"], 2)
            self.assertTrue(gpd.read_parquet(directory / "reseau_pieton_troncons.parquet").crs.to_epsg() == 2154)
            previous = {p.name: sha256(p) for p in directory.iterdir()}
            with self.assertRaisesRegex(ValueError, "écraser"):
                publish_tile(directory, [path], ZONE, ZONE, 4.5, {"fixture": True}, True)
            self.assertEqual(previous, {p.name: sha256(p) for p in directory.iterdir()})
            with patch("geo.build_reseau_ara.ox.save_graphml", side_effect=OSError("disque")):
                with self.assertRaises(OSError):
                    publish_tile(root / "x92_y645", [path], ZONE, ZONE, 4.5, {}, True)
            self.assertFalse((root / "x92_y645").exists())

    def test_empty_tile_with_origins_is_not_published_as_zero_access(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "empty.osm"
            write_xml(path, {}, {})
            with self.assertRaisesRegex(ValueError, "Aucun tronçon"):
                publish_tile(root / "x91_y645", [path], ZONE, ZONE, 4.5, {}, True)
            self.assertFalse((root / "x91_y645").exists())

    def test_parquet_only_exports_reconstruct_the_same_routable_graph(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            nodes, ways = objects()
            source = root / "source.osm"
            write_xml(source, nodes, ways)
            output = root / "x91_y645"
            report = publish_tile(output, [source], ZONE, ZONE, 4.5,
                                  {"export_graphml": False}, True, export_graphml=False)
            self.assertFalse((output / "reseau_pieton.graphml").exists())
            graph = load_tile_graph(output)
            self.assertAlmostEqual(nx.shortest_path_length(graph, 1, 3, weight="temps_pieton_s"), 80, places=4)
            self.assertAlmostEqual(nx.shortest_path_length(graph, 3, 1, weight="temps_pieton_s"), 80, places=4)
            self.assertFalse(nx.has_path(graph, 1, 4))
            self.assertIs(WalkingNetwork(graph, 4.5).graph, graph)
            self.assertEqual(len(report["fichiers"]), 3)

    def test_empty_tile_without_origins_is_explicit_and_reopenable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "empty.osm"
            write_xml(path, {}, {})
            for graphml, tile in [(True, "x91_y645"), (False, "x92_y645")]:
                with self.subTest(graphml=graphml):
                    report = publish_tile(root / tile, [path], ZONE, ZONE, 4.5,
                                          {"export_graphml": graphml}, False, export_graphml=graphml)
                    self.assertEqual(report["etat"], "valide_sans_troncon_sans_origine")
                    self.assertEqual(report["noeuds"], 0)
                    self.assertTrue(gpd.read_parquet(root / tile / "reseau_pieton_troncons.parquet").empty)


@unittest.skipUnless(shutil.which("osmium"), "osmium-tool requis pour la chaîne PBF réelle")
class PbfIntegrationTests(unittest.TestCase):
    def fixture(self, root):
        nodes, ways = objects()
        path = root / "input.osm"
        write_xml(path, nodes, ways)
        osm_dir, prep = root / "osm", root / "preparation"
        osm_dir.mkdir()
        prep.mkdir()
        archives = []
        for name in ("auvergne", "rhone-alpes"):
            target = osm_dir / f"{name}.osm.pbf"
            subprocess.run(["osmium", "cat", str(path), "-o", str(target)], check=True)
            archives.append({"nom": name, "sha256": sha256(target)})
        tiles = gpd.GeoDataFrame({"tuile_id": ["x91_y645", "x92_y645"], "calcul_population_offre_utile": [True, True]}, geometry=[ZONE, ZONE], crs=2154)
        for name in ("tuiles.parquet", "zones_extraction.parquet"):
            tiles.to_parquet(prep / name, index=False)
        plan = {"archives_osm": archives, "parametres": {"vitesse_kmh": 4.5}, "fichiers": {p.name: sha256(p) for p in prep.iterdir()}}
        (prep / "plan_reseau_ara.json").write_text(json.dumps(plan))
        return parser().parse_args(["--osm-dir", str(osm_dir), "--preparation", str(prep),
            "--output-dir", str(root / "result"), "--cache-dir", str(root / "cache"), "--tuiles", "x91_y645"])

    def test_real_pbf_partial_resume_then_complete_and_detect_corrupt_output(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            args = self.fixture(root)
            first = build(args)
            self.assertEqual(first["etat"], "partiel")
            self.assertFalse(first["o1_o2_calcules"])
            with self.assertRaisesRegex(ValueError, "existantes"):
                build(args)
            args.reprendre = True
            with patch("geo.build_reseau_ara.publish_tile", side_effect=AssertionError("ne pas reconstruire")):
                self.assertEqual(build(args)["tuiles_validees"], 1)
            args.tuiles = None
            self.assertEqual(build(args)["etat"], "regional_termine")
            (args.output_dir / "x91_y645/reseau_pieton_noeuds.parquet").write_text("corrompu")
            with self.assertRaisesRegex(ValueError, "modifié"):
                build(args)

    def test_changed_input_or_pbf_is_rejected_before_output(self):
        for corrupt in ["pbf", "preparation"]:
            with self.subTest(corrupt=corrupt), TemporaryDirectory() as temp:
                args = self.fixture(Path(temp))
                target = args.osm_dir / "auvergne.osm.pbf" if corrupt == "pbf" else args.preparation / "tuiles.parquet"
                target.write_bytes(b"corrompu")
                with self.assertRaisesRegex(ValueError, "modifi"):
                    build(args)
                self.assertFalse(args.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
