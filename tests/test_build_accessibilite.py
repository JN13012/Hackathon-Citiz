"""Calculs analytiques et invariants de conservation pour O1/O2, hors connexion."""

import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
from shapely.geometry import LineString, Point, box

from geo.build_accessibilite import PairNetwork, aggregate, build_accessibilite, parser, population_groups, route_pairs

X, Y = 900000, 6400000


def network_fixture(disconnected=False):
    graph = nx.MultiDiGraph(crs="EPSG:2154", simplified=True, network_type="walk")
    for n, (x, y) in {1: (0, 0), 2: (1000, 0), 3: (0, 1000), 4: (0, 20), 5: (1000, 20)}.items():
        graph.add_node(n, x=X + x, y=Y + y)
    for u, v in [(1, 2), (1, 3)] + ([(4, 5)] if disconnected else []):
        line = LineString([(graph.nodes[u]["x"], graph.nodes[u]["y"]), (graph.nodes[v]["x"], graph.nodes[v]["y"])])
        for a, b in [(u, v), (v, u)]:
            graph.add_edge(a, b, key=0, geometry=line, longueur_m=line.length, temps_pieton_s=line.length / 1.25)
    return PairNetwork(graph, 4.5)


class RoutingTests(unittest.TestCase):
    def test_long_edge_interior_includes_both_connectors_and_exact_budget(self):
        network = network_fixture()
        a = network.snap(Point(X + 400, Y + 25), 300, 50)
        b = network.snap(Point(X + 725, Y - 25), 300, 50)
        reached = network.node_times(a, 300)
        self.assertEqual(reached, {})
        self.assertAlmostEqual(network.travel_time(a, b, reached), 300)
        self.assertAlmostEqual(network.travel_time(b, a, network.node_times(b, 300)), 300)
        c = network.snap(Point(X + 726, Y - 25), 300, 50)
        self.assertGreater(network.travel_time(a, c, reached), 300)

    def test_junction_route_follows_network_and_not_straight_chord(self):
        network = network_fixture()
        a = network.snap(Point(X + 150, Y - 10), 300, 50)
        b = network.snap(Point(X - 15, Y + 200), 300, 50)
        # 10 m + 150 m jusqu'au carrefour + 200 m + 15 m = 375 m.
        self.assertAlmostEqual(network.travel_time(a, b, network.node_times(a, 300)), 300)

    def test_nearby_and_coincident_disconnected_roads_are_never_linked(self):
        network = network_fixture(disconnected=True)
        a = network.snap(Point(X + 500, Y), 300, 50)
        b = network.snap(Point(X + 500, Y + 20), 300, 50)
        self.assertTrue(math.isinf(network.travel_time(a, b, network.node_times(a, 300))))
        # Vérification de la garde sur les identifiants, même si la géométrie
        # de deux routes déconnectées est rigoureusement superposée.
        from geo.build_accessibilite import Snap
        upper = next(i for i, key in enumerate(network.keys) if key[:2] == (4, 5))
        network.lines[upper] = network.lines[a.index]
        b = Snap(upper, 500, 0, "ok")
        self.assertTrue(math.isinf(network.travel_time(a, b, network.node_times(a, 300))))

    def test_virtual_origin_removed_after_failure_and_invalid_snap_is_unknown(self):
        network = network_fixture()
        before = set(network.graph), set(network.graph.edges(keys=True))
        start = network.snap(Point(X + 500, Y), 300, 50)
        with patch("networkx.single_source_dijkstra_path_length", side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                network.node_times(start, 300)
        self.assertEqual(before, (set(network.graph), set(network.graph.edges(keys=True))))
        far = network.snap(Point(X + 500, Y + 60), 300, 50)
        self.assertEqual(far.statut, "raccordement_trop_loin")
        self.assertTrue(math.isinf(network.travel_time(start, far, {})))

    def test_reverse_geometry_is_required_for_bidirectional_model(self):
        graph = network_fixture().graph
        curve = LineString([(X, Y), (X + 500, Y + 100), (X + 1000, Y)])
        graph[2][1][0].update(geometry=curve, longueur_m=curve.length, temps_pieton_s=curve.length / 1.25)
        with self.assertRaisesRegex(ValueError, "inverse"):
            PairNetwork(graph, 4.5)


class AggregationTests(unittest.TestCase):
    def fixtures(self):
        cells = gpd.GeoDataFrame({"id_carreau": ["a", "b", "c"], "population": [5., 15., 100.]},
            geometry=[box(X + 400, Y - 20, X + 420, Y), box(X + 420, Y - 20, X + 440, Y), box(X + 800, Y - 20, X + 820, Y)], crs=2154)
        pop = cells.copy()
        pop.geometry = pop.geometry.centroid
        pop["dans_pilote"] = [True, True, False]
        offers = gpd.GeoDataFrame({"offre_id": ["s1", "s2"], "type": ["bus", "citiz"], "source": ["arrets", "stations_partagees"],
            "nom": ["Bus", "Citiz"], "unite": ["passages_par_jour", "capacite"], "valeur": [12., 2.], "dans_pilote": [True, False]},
            geometry=[Point(X + 600, Y), Point(X + 300, Y)], crs=2154)
        return cells, pop, offers

    def test_population_weighting_border_cells_and_units_are_separate(self):
        cells, pop, offers = self.fixtures()
        pairs = pd.DataFrame({"id_carreau": ["a", "a", "b", "c"], "offre_id": ["s1", "s2", "s1", "s1"], "temps_pieton_s": [10., 20., 30., 40.]})
        unknown = pd.DataFrame(columns=["id_carreau", "offre_id"])
        pop_qc = pd.DataFrame({"id_carreau": pop.id_carreau, "statut": "ok"})
        offer_qc = pd.DataFrame({"offre_id": offers.offre_id, "statut": "ok"})
        o1, o2, mapping, counts, fallbacks = aggregate(pop, offers, cells.iloc[:2], pairs, unknown, pop_qc, offer_qc, 20)
        self.assertEqual(len(o1), 1)
        row = o1.iloc[0]
        self.assertAlmostEqual(row.options_par_habitant, (5 * 2 + 15 * 1) / 20)
        self.assertEqual(row.options_bus_par_habitant, 1)
        self.assertEqual(row.passages_jour_bus_par_habitant, 12)
        self.assertEqual(row.capacite_citiz_par_habitant, .5)
        self.assertEqual(row.population, 20)
        self.assertEqual(row.nb_carreaux, 2)
        self.assertEqual(fallbacks, 0)
        self.assertEqual(set(mapping.id_carreau), {"a", "b"})
        self.assertEqual(o2.iloc[0].population_desservie, 120)  # c, hors pilote, compte.
        self.assertEqual(o2.iloc[0].population_desservie_dans_pilote, 20)
        self.assertEqual(o2.iloc[0].nb_carreaux_desservis, 3)

    def test_unroutable_population_or_offer_does_not_become_zero(self):
        cells, pop, offers = self.fixtures()
        pop.geometry = [Point(X + 410, Y + 200), Point(X + 430, Y), Point(X + 810, Y)]
        pairs, unknown, pq, oq = route_pairs(network_fixture(), pop, offers, 300, 50, 50)
        self.assertIn("a", set(unknown.id_carreau))
        o1, o2, _, _, _ = aggregate(pop, offers, cells.iloc[:2], pairs, unknown, pq, oq, 20)
        self.assertTrue(pd.isna(o1.iloc[0].options_par_habitant))
        self.assertEqual(o1.iloc[0].statut, "partiel")
        self.assertTrue(pd.isna(o2.iloc[0].population_desservie))
        self.assertEqual(o2.iloc[0].statut, "partiel")
        self.assertGreater(o2.iloc[0].population_desservie_min, 0)

    def test_missing_capacity_does_not_erase_option_count_or_turn_into_zero(self):
        cells, pop, offers = self.fixtures()
        offers.loc[offers["type"].eq("citiz"), "valeur"] = float("nan")
        pairs, unknown, pq, oq = route_pairs(network_fixture(), pop, offers, 300, 150, 50)
        o1, _, _, _, _ = aggregate(pop, offers, cells.iloc[:2], pairs, unknown, pq, oq, 20)
        self.assertTrue(pd.isna(o1.iloc[0].capacite_citiz_par_habitant))
        self.assertEqual(o1.iloc[0].options_par_habitant, 2)

    def test_grouping_is_deterministic_conserves_population_and_suppresses_tiny_scope(self):
        cells, _, _ = self.fixtures()
        a, mapping_a, _ = population_groups(cells, 20)
        b, mapping_b, _ = population_groups(cells.iloc[::-1], 20)
        self.assertEqual(a.population.sum(), cells.population.sum())
        self.assertTrue(a.population.ge(20).all())
        self.assertEqual(set(mapping_a.id_carreau), set(cells.id_carreau))
        self.assertEqual(mapping_a.to_dict("records"), mapping_b.to_dict("records"))
        tiny, _, _ = population_groups(cells.iloc[:1], 20)
        self.assertFalse(tiny.iloc[0].seuil_population_atteint)
        _, pop, offers = self.fixtures()
        pairs, unknown, pq, oq = route_pairs(network_fixture(), pop, offers, 300, 150, 50)
        o1, _, _, _, _ = aggregate(pop, offers, cells.iloc[:1], pairs, unknown, pq, oq, 20)
        self.assertEqual(o1.iloc[0].statut, "population_insuffisante")
        self.assertTrue(pd.isna(o1.iloc[0].population))
        self.assertTrue(pd.isna(o1.iloc[0].options_par_habitant))
        self.assertTrue(pd.isna(o1.iloc[0].options_par_habitant_min))


class ExportTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reseau = self.root / "reseau"
        self.reseau.mkdir()
        network = network_fixture()
        ox.save_graphml(network.graph, self.reseau / "reseau_pieton.graphml")
        (self.reseau / "reseau_pieton_qc.json").write_text(json.dumps({"codes_communes": ["38185"], "vitesse_kmh_hypothese": 4.5}))
        gpd.GeoDataFrame(geometry=[box(X - 1500, Y - 1500, X + 2500, Y + 2500)], crs=2154).to_parquet(self.reseau / "zone_requete.parquet")
        gpd.GeoDataFrame({"id_zone": ["commune:38185"], "code_insee": ["38185"], "niveau": ["commune"]},
            geometry=[box(X + 300, Y - 100, X + 700, Y + 100)], crs=2154).to_parquet(self.root / "contours.parquet")
        cells, _, offers = AggregationTests().fixtures()
        cells.to_parquet(self.root / "population.parquet")
        # L'arrêt extérieur doit encore pouvoir contribuer à O1.
        offers.loc[offers.offre_id.eq("s2"), "geometry"] = Point(X + 200, Y)
        offers.drop(columns="dans_pilote").to_parquet(self.root / "offre.parquet")
        self.output = self.root / "output"

    def args(self, *extra):
        return parser().parse_args(["--codes-communes", "38185", "--population", str(self.root / "population.parquet"),
            "--offre", str(self.root / "offre.parquet"), "--contours", str(self.root / "contours.parquet"),
            "--reseau-dir", str(self.reseau), "--output-dir", str(self.output), *extra])

    def test_real_exports_group_small_cells_include_neighbors_and_are_traceable(self):
        report = build_accessibilite(self.args())
        self.assertEqual(report["etat_calcul"], "complet")
        self.assertEqual(report["carreaux_pilote"], 2)
        self.assertEqual(report["offres_pilote"], 1)
        self.assertEqual(report["paires_indeterminees"], 0)
        o1 = gpd.read_parquet(self.output / "gold_o1.parquet")
        o2 = gpd.read_parquet(self.output / "gold_o2.parquet")
        self.assertEqual(o1.iloc[0].options_par_habitant, 2)
        self.assertEqual(o2.iloc[0].population_desservie, 120)
        self.assertEqual(o1.crs.to_epsg(), 2154)
        self.assertTrue(o1.geometry.is_valid.all())
        self.assertEqual(pd.read_csv(self.output / "gold_o1.csv").iloc[0].population, 20)
        self.assertEqual(len(json.loads((self.output / "gold_o2.geojson").read_text())["features"]), 1)
        self.assertEqual(len(report["sources"]["population"]["sha256"]), 64)
        self.assertTrue((self.output / "apercu_o1_o2.html").is_file())

    def test_invalid_input_or_insufficient_margin_preserves_previous_output(self):
        self.output.mkdir()
        old = self.output / "accessibilite_qc.json"
        old.write_text("précédent")
        cells = gpd.read_parquet(self.root / "population.parquet")
        cells.loc[0, "population"] = float("nan")
        cells.to_parquet(self.root / "population.parquet")
        with self.assertRaisesRegex(ValueError, "Populations"):
            build_accessibilite(self.args())
        self.assertEqual(old.read_text(), "précédent")
        with self.assertRaisesRegex(ValueError, "Marge"):
            build_accessibilite(self.args("--duree-s", "3000"))
        self.assertEqual(old.read_text(), "précédent")


if __name__ == "__main__":
    unittest.main()
