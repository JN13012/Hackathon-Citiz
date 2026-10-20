"""Contrôles hors ligne de la topologie, des poids et des exports piétons."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, box

from geo.build_reseau import acquire_graph, build_reseau, parser, prepare_graph, select_zone


def fixture_graph():
    graph = nx.MultiDiGraph(crs="EPSG:2154", simplified=True, network_type="walk")
    for node, x, y in [(1, 900000, 6400000), (2, 900100, 6400000), (3, 900200, 6400000),
                       (4, 901000, 6400000), (5, 901100, 6400000), (6, 901000, 6400100)]:
        graph.add_node(node, x=x, y=y)
    for u, v, key, points, osmid in [
        (1, 2, 0, [(900000, 6400000), (900100, 6400000)], 100),
        (1, 2, 1, [(900000, 6400000), (900050, 6400050), (900100, 6400000)], [101, 102]),
        (2, 3, 0, [(900100, 6400000), (900200, 6400000)], 103),
        (4, 5, 0, [(901000, 6400000), (901100, 6400000)], 104),
    ]:
        for a, b, coords in [(u, v, points), (v, u, points[::-1])]:
            graph.add_edge(a, b, key=key, osmid=osmid, name="Chemin", highway="footway",
                           length=LineString(coords).length, geometry=LineString(coords))
    return graph


class BuildReseauTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "silver"
        self.contours = self.root / "contours.parquet"
        gpd.GeoDataFrame({"niveau": ["commune", "iris", "commune"], "code_insee": ["38185", "38185", "01001"]},
                         geometry=[box(899900, 6399900, 900400, 6400200),
                                   box(899900, 6399900, 900000, 6400000),
                                   box(910000, 6410000, 910100, 6410100)], crs=2154).to_parquet(self.contours)

    def args(self, *extra):
        return parser().parse_args([
            "--contours", str(self.contours), "--codes-communes", "38185",
            "--output-dir", str(self.output), "--raw-dir", str(self.root / "bronze"), *extra,
        ])

    def test_selection_ign_buffer_is_metric_and_missing_codes_fail(self):
        boundary, zone = select_zone(self.args())
        self.assertEqual(boundary.area, 150000)
        self.assertEqual(zone.bounds, (898900, 6398900, 901400, 6401200))
        with self.assertRaisesRegex(ValueError, "absentes"):
            select_zone(self.args("--codes-communes", "99999"))
        with self.assertRaisesRegex(ValueError, "limite"):
            select_zone(self.args("--max-area-km2", "0.1"))

    def test_parallel_edges_components_and_shortest_walking_path(self):
        graph, nodes, edges, components = prepare_graph(fixture_graph(), 4.5)
        self.assertEqual([len(c) for c in components], [3, 2, 1])
        self.assertEqual(list(nx.isolates(graph)), [6])
        self.assertEqual(graph.number_of_edges(1, 2), 2)
        self.assertEqual(set(edges.index), set(fixture_graph().edges(keys=True)))
        self.assertEqual(nx.shortest_path_length(graph, 1, 3, weight="longueur_m"), 200)
        self.assertEqual(nx.shortest_path_length(graph, 3, 1, weight="temps_pieton_s"), 160)
        with self.assertRaises(nx.NetworkXNoPath):
            nx.shortest_path(graph, 1, 4, weight="temps_pieton_s")
        self.assertEqual(nodes.crs.to_epsg(), 2154)

    def test_projection_keeps_curved_geometry_and_metric_lengths(self):
        graph, _, edges, _ = prepare_graph(fixture_graph(), 4.5)
        # Reproject round trip as for real longitude/latitude OSM data.
        unprojected = ox.project_graph(graph, to_crs=4326)
        _, _, projected, _ = prepare_graph(unprojected, 4.5)
        self.assertAlmostEqual(projected.loc[(1, 2, 1), "longueur_m"], 2 ** 0.5 * 100, places=5)
        self.assertGreater(edges.loc[(1, 2, 1), "temps_pieton_s"], 80)

    def test_invalid_geometry_coordinates_and_speed_are_rejected(self):
        graph = fixture_graph()
        graph[1][2][0]["geometry"] = LineString([(900000, 6400000)] * 2)
        with self.assertRaisesRegex(ValueError, "tronçon"):
            prepare_graph(graph, 4.5)
        graph = fixture_graph()
        graph.nodes[1]["x"] = float("nan")
        with self.assertRaisesRegex(ValueError, "Coordonnées"):
            prepare_graph(graph, 4.5)
        for speed in [0, -1, float("nan"), float("inf")]:
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                prepare_graph(fixture_graph(), speed)

    @patch("geo.build_reseau.acquire_graph")
    def test_exports_reopen_and_preserve_topology_and_mixed_osm_ids(self, acquire):
        acquire.return_value = (fixture_graph(), {"mode": "fixture_hors_ligne"})
        report = build_reseau(self.args())
        nodes = gpd.read_parquet(self.output / "reseau_pieton_noeuds.parquet")
        edges = gpd.read_parquet(self.output / "reseau_pieton_troncons.parquet")
        restored = ox.load_graphml(
            self.output / "reseau_pieton.graphml",
            edge_dtypes={"longueur_m": float, "temps_pieton_s": float},
            node_dtypes={"composante_id": int},
        )
        self.assertEqual(nx.shortest_path_length(restored, 1, 3, weight="temps_pieton_s"), 160)
        self.assertEqual(set(restored.edges(keys=True)), set(fixture_graph().edges(keys=True)))
        self.assertEqual(set(nodes.noeud_id), set(fixture_graph()))
        self.assertEqual(set(map(tuple, edges[["de", "vers", "cle"]].to_numpy())), set(restored.edges(keys=True)))
        self.assertEqual(json.loads(edges.loc[(edges.de == 1) & (edges.vers == 2) & (edges.cle == 1), "osmid_json"].iloc[0]), [101, 102])
        self.assertEqual(report["noeuds_isoles"], 1)
        self.assertEqual(report["distance_marche_5_min_m"], 375)
        self.assertEqual(int(nodes.dans_territoire.sum()), 3)
        for route in report["trajets_controle_structurel"]:
            self.assertTrue(route["accessible"])
            self.assertGreaterEqual(route["longueur_m"], route["distance_directe_m"])
        self.assertEqual(json.loads((self.output / "reseau_pieton_qc.json").read_text())["noeuds"], 6)

    def test_graphml_replay_checks_original_zone_and_walk_origin(self):
        args = self.args()
        _, zone = select_zone(args)
        graph = fixture_graph()
        graph.graph["zone_sha256"] = hashlib.sha256(zone.wkb).hexdigest()
        archive = self.root / "archive.graphml"
        ox.save_graphml(graph, archive)
        args.graphml = archive
        with patch("osmnx.graph_from_polygon", side_effect=AssertionError("Réseau interdit")):
            restored, acquisition = acquire_graph(args, zone)
        self.assertEqual(acquisition["mode"], "relecture_graphml")
        self.assertEqual(set(restored), set(graph))
        with self.assertRaisesRegex(ValueError, "même contour"):
            acquire_graph(args, zone.buffer(100))

    @patch("osmnx.graph_from_polygon")
    def test_acquisition_preserves_barriers_and_every_component(self, download):
        graph = fixture_graph()
        graph.graph["simplified"] = False
        graph.nodes[2]["barrier"] = "gate"
        download.return_value = graph
        args = self.args()
        _, zone = select_zone(args)
        with patch.dict(vars(ox.settings)):
            result, acquisition = acquire_graph(args, zone)
        self.assertTrue(download.call_args.kwargs["retain_all"])
        self.assertEqual(download.call_args.kwargs["network_type"], "walk")
        self.assertFalse(download.call_args.kwargs["simplify"])
        self.assertTrue(download.call_args.kwargs["truncate_by_edge"])
        self.assertIn(2, result)
        self.assertEqual(result.nodes[2]["barrier"], "gate")
        self.assertEqual(nx.number_weakly_connected_components(result), 3)
        self.assertTrue(Path(acquisition["graphml"]).is_file())

    @patch("geo.build_reseau.acquire_graph")
    def test_validation_failure_keeps_previous_exports(self, acquire):
        self.output.mkdir()
        previous = self.output / "reseau_pieton_qc.json"
        previous.write_text("résultat précédent")
        acquire.return_value = (nx.MultiDiGraph(crs=2154), {})
        with self.assertRaises(ValueError):
            build_reseau(self.args())
        self.assertEqual(previous.read_text(), "résultat précédent")
        acquire.assert_called_once()

    @patch("geo.build_reseau.acquire_graph")
    def test_invalid_parameters_fail_before_network(self, acquire):
        for extra in [("--vitesse-kmh", "nan"), ("--marge-m", "-1"), ("--timeout-s", "0")]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build_reseau(self.args(*extra))
        acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
