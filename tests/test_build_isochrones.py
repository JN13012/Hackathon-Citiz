"""Vérifie les budgets de marche sur des réseaux analytiques, sans internet."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, Point, box

from geo.build_isochrones import WalkingNetwork, build_isochrones, parser


X, Y = 900000, 6400000


def graph_fixture(branch=False, disconnected=False):
    graph = nx.MultiDiGraph(crs="EPSG:2154", simplified=True, network_type="walk")
    for node, (x, y) in {1: (0, 0), 2: (1000, 0), 3: (0, 1000), 4: (0, 20), 5: (1000, 20)}.items():
        graph.add_node(node, x=X + x, y=Y + y)
    pairs = [(1, 2)]
    if branch:
        pairs.append((1, 3))
    if disconnected:
        pairs.append((4, 5))
    for u, v in pairs:
        line = LineString([(graph.nodes[u]["x"], graph.nodes[u]["y"]), (graph.nodes[v]["x"], graph.nodes[v]["y"])])
        for a, b in [(u, v), (v, u)]:
            # Même géométrie pour l'arête inverse, comme certains exports OSMnx.
            graph.add_edge(a, b, key=0, geometry=line, osmid=u * 100 + v,
                           length=line.length, longueur_m=line.length, temps_pieton_s=line.length / 1.25)
    return graph


class ReachableTests(unittest.TestCase):
    def test_middle_of_long_edge_reaches_partial_segments_without_any_node(self):
        network = WalkingNetwork(graph_fixture(), 4.5)
        result, lines = network.reachable(Point(X + 500, Y))
        self.assertEqual(result["nb_noeuds_atteints"], 0)
        self.assertEqual(result["statut"], "ok")
        self.assertEqual(lines.bounds, (X + 125, Y, X + 875, Y))
        self.assertAlmostEqual(lines.length, 750)
        self.assertLessEqual(result["temps_max_portions_s"], 300)

    def test_access_cost_is_subtracted_and_not_a_free_teleport(self):
        result, lines = WalkingNetwork(graph_fixture(), 4.5).reachable(Point(X + 500, Y + 25))
        self.assertEqual(result["temps_acces_s"], 20)
        self.assertEqual(result["distance_raccordement_m"], 25)
        self.assertEqual(lines.bounds, (X + 150, Y, X + 850, Y + 25))
        self.assertAlmostEqual(lines.length, 725)  # 700 m de voie + 25 m de raccordement.
        self.assertLessEqual(result["temps_max_portions_s"], 300)

    def test_branch_orientation_and_disconnected_nearby_road(self):
        network = WalkingNetwork(graph_fixture(branch=True, disconnected=True), 4.5)
        result, lines = network.reachable(Point(X + 50, Y))
        self.assertEqual(lines.bounds, (X, Y, X + 425, Y + 325))
        self.assertTrue(lines.covers(Point(X, Y + 325)))
        self.assertFalse(lines.covers(Point(X, Y + 326)))
        self.assertFalse(lines.covers(Point(X + 200, Y + 20)))
        self.assertFalse(lines.buffer(10).covers(Point(X + 200, Y + 20)))
        self.assertEqual(result["nb_noeuds_atteints"], 1)

    def test_curved_edge_is_cut_by_walking_length(self):
        graph = graph_fixture()
        graph.nodes[2].update(x=X + 200, y=Y + 200)
        line = LineString([(X, Y), (X, Y + 200), (X + 200, Y + 200)])
        for u, v in [(1, 2), (2, 1)]:
            graph[u][v][0].update(geometry=line, length=400, longueur_m=400, temps_pieton_s=320)
        _, lines = WalkingNetwork(graph, 4.5).reachable(Point(X, Y))
        self.assertAlmostEqual(lines.length, 375)
        self.assertTrue(lines.covers(Point(X + 175, Y + 200)))
        self.assertFalse(lines.covers(Point(X + 176, Y + 200)))
        self.assertFalse(lines.buffer(10).covers(Point(X + 100, Y + 100)))

    def test_too_far_and_exhausted_access_budget_have_explicit_states(self):
        network = WalkingNetwork(graph_fixture(), 4.5)
        far, lines = network.reachable(Point(X + 500, Y + 51))
        self.assertEqual(far["statut"], "raccordement_trop_loin")
        self.assertIsNone(lines)
        exhausted, lines = network.reachable(Point(X + 500, Y + 25), budget_s=10)
        self.assertEqual(exhausted["statut"], "budget_acces_epuise")
        self.assertIsNone(lines)

    def test_missing_weights_invalid_geometry_and_directed_graph_are_rejected(self):
        for problem in ["weight", "geometry", "direction", "speed", "crs"]:
            with self.subTest(problem=problem):
                graph = graph_fixture()
                if problem == "weight":
                    del graph[1][2][0]["temps_pieton_s"]
                elif problem == "geometry":
                    graph[1][2][0]["geometry"] = LineString([(X + 10, Y), (X + 1000, Y)])
                elif problem == "direction":
                    graph.remove_edge(2, 1, 0)
                elif problem == "crs":
                    graph.graph["crs"] = "EPSG:4326"
                with self.assertRaises(ValueError):
                    WalkingNetwork(graph, 5 if problem == "speed" else 4.5)

    def test_virtual_origin_is_always_removed_from_shared_graph(self):
        graph = graph_fixture()
        before = set(graph), set(graph.edges(keys=True))
        network = WalkingNetwork(graph, 4.5)
        network.reachable(Point(X + 500, Y))
        with patch("networkx.single_source_dijkstra_path_length", side_effect=RuntimeError("interruption")):
            with self.assertRaises(RuntimeError):
                network.reachable(Point(X + 500, Y))
        self.assertEqual(before, (set(graph), set(graph.edges(keys=True))))


class ExportTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reseau = self.root / "reseau"
        self.reseau.mkdir()
        self.output = self.root / "gold"
        self.origins_path = self.root / "origines.parquet"
        self.contours_path = self.root / "contours.parquet"
        self.origins = gpd.GeoDataFrame({"id_carreau": ["0001", "0002", "0003"]},
                                      geometry=[Point(X + 500, Y + 25), Point(X + 500, Y + 80), Point(X + 5000, Y)], crs=2154)
        self.origins.to_parquet(self.origins_path)
        gpd.GeoDataFrame({"niveau": ["commune"], "code_insee": ["38185"]},
                         geometry=[box(X - 100, Y - 100, X + 1100, Y + 1100)], crs=2154).to_parquet(self.contours_path)
        gpd.GeoDataFrame(geometry=[box(X - 1000, Y - 1000, X + 2000, Y + 2000)], crs=2154).to_parquet(self.reseau / "zone_requete.parquet")
        ox.save_graphml(graph_fixture(), self.reseau / "reseau_pieton.graphml")
        (self.reseau / "reseau_pieton_qc.json").write_text(json.dumps({"vitesse_kmh_hypothese": 4.5, "codes_communes": ["38185"], "limitations": []}))

    def args(self, *extra):
        return parser().parse_args([
            "--origines", str(self.origins_path), "--id-col", "id_carreau", "--type-origine", "candidat",
            "--codes-communes", "38185", "--contours", str(self.contours_path),
            "--reseau-dir", str(self.reseau), "--output-dir", str(self.output), *extra,
        ])

    def test_exports_preserve_candidate_ids_and_distinguish_failed_origins(self):
        report = build_isochrones(self.args())
        polygons = gpd.read_parquet(self.output / "gold_isochrones.parquet")
        paths = gpd.read_parquet(self.output / "chemins_accessibles.parquet")
        origins = gpd.read_parquet(self.output / "origines_qc.parquet")
        self.assertEqual(set(polygons.origine_id), {"0001"})
        self.assertEqual(set(polygons.id_carreau), {"0001"})
        self.assertEqual(set(paths.origine_id), {"0001"})
        self.assertEqual(set(origins.origine_id), {"0001", "0002"})
        self.assertEqual(report["etat"], "partiel")
        self.assertEqual(report["origines_hors_selection"], 1)
        self.assertEqual(report["statuts"], {"ok": 1, "raccordement_trop_loin": 1})
        self.assertEqual(polygons.crs.to_epsg(), 2154)
        self.assertTrue(polygons.geometry.is_valid.all())
        self.assertTrue(polygons.geometry.iloc[0].covers(paths.geometry.iloc[0]))
        self.assertEqual(report["origines_proches_limite_reseau"], 0)
        geojson = json.loads((self.output / "gold_isochrones.geojson").read_text())
        self.assertEqual(geojson["features"][0]["properties"]["origine_id"], "0001")
        self.assertEqual(json.loads((self.output / "isochrones_qc.json").read_text())["isochrones_produites"], 1)

    def test_all_rejected_origins_export_empty_polygons_with_report(self):
        report = build_isochrones(self.args("--max-raccord-m", "1"))
        self.assertEqual(report["isochrones_produites"], 0)
        self.assertEqual(report["etat"], "partiel")
        self.assertTrue(gpd.read_parquet(self.output / "gold_isochrones.parquet").empty)
        self.assertEqual(len(gpd.read_parquet(self.output / "origines_qc.parquet")), 2)
        self.assertEqual(json.loads((self.output / "gold_isochrones.geojson").read_text())["features"], [])

    def test_network_boundary_risk_is_flagged(self):
        gpd.GeoDataFrame(geometry=[box(X + 400, Y - 100, X + 600, Y + 100)], crs=2154).to_parquet(self.reseau / "zone_requete.parquet")
        report = build_isochrones(self.args())
        self.assertEqual(report["origines_proches_limite_reseau"], 2)

    def test_duplicate_ids_and_missing_communes_preserve_previous_output(self):
        self.output.mkdir()
        previous = self.output / "isochrones_qc.json"
        previous.write_text("précédent")
        self.origins["id_carreau"] = ["0001"] * 3
        self.origins.to_parquet(self.origins_path)
        with self.assertRaisesRegex(ValueError, "dupliqués"):
            build_isochrones(self.args())
        self.assertEqual(previous.read_text(), "précédent")
        self.origins["id_carreau"] = ["0001", "0002", "0003"]
        self.origins.to_parquet(self.origins_path)
        with self.assertRaisesRegex(ValueError, "communes"):
            build_isochrones(self.args("--codes-communes", "01001"))
        self.assertEqual(previous.read_text(), "précédent")


if __name__ == "__main__":
    unittest.main()
