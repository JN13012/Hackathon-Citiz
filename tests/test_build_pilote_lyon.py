"""Fusion des tuiles, coûts de référence et conservation des cas inconnus."""

import unittest

import geopandas as gpd
import pandas as pd
from shapely import Point, LineString, box

from geo.build_pilote_lyon import merge_network, validate_pairs
from geo.build_accessibilite import PairNetwork, route_pairs


def fixture():
    nodes = gpd.GeoDataFrame({"noeud_id": [1, 2, 3], "composante_id": [0, 0, 0]},
                             geometry=[Point(0, 0), Point(1000, 0), Point(1000, 500)], crs=2154)
    rows = []
    for u, v, k, coords in [(1, 2, 10, [(0, 0), (1000, 0)]), (2, 3, 20, [(1000, 0), (1000, 500)])]:
        line = LineString(coords)
        for a, b, geometry, d in [(u, v, line, 1), (v, u, LineString(coords[::-1]), -1)]:
            rows.append({"de": a, "vers": b, "cle": k, "geometry": geometry,
                         "longueur_m": line.length, "temps_pieton_s": line.length / 1.25,
                         "osmid": k, "segment_osm": 0, "direction_osm": d})
    return nodes, gpd.GeoDataFrame(rows, crs=2154)


class PilotTests(unittest.TestCase):
    def test_margin_copies_are_merged_and_local_component_ids_are_ignored(self):
        nodes, edges = fixture()
        other = nodes.copy()
        other.composante_id = 7
        graph, stats = merge_network([nodes, other], [edges, edges.iloc[::-1]], box(-100, -100, 1100, 600))
        self.assertEqual((len(graph), graph.number_of_edges()), (3, 4))
        self.assertEqual(stats["copies_arcs_avant_fusion"], 8)
        self.assertNotIn("composante_id", graph.nodes[1])

    def test_conflicting_copies_are_rejected(self):
        nodes, edges = fixture()
        bad = nodes.copy()
        bad.loc[0, "geometry"] = Point(1, 1)
        with self.assertRaisesRegex(ValueError, "Coordonnées contradictoires"):
            merge_network([nodes, bad], [edges, edges], box(-100, -100, 1100, 600))
        bad_edge = edges.copy()
        bad_edge.loc[0, "temps_pieton_s"] += 1
        with self.assertRaisesRegex(ValueError, "Copies contradictoires"):
            merge_network([nodes, nodes], [edges, bad_edge], box(-100, -100, 1100, 600))

    def test_clipping_keeps_full_segment_and_reference_proves_exact_budget(self):
        nodes, edges = fixture()
        graph, _ = merge_network([nodes], [edges], box(300, -50, 800, 50))
        self.assertEqual(graph[1][2][10]["longueur_m"], 1000)
        self.assertNotIn(3, graph)
        net = PairNetwork(graph, 4.5)
        pop = gpd.GeoDataFrame({"id_carreau": ["p"], "population": [100.], "dans_pilote": [True]}, geometry=[Point(400, 25)], crs=2154)
        offers = gpd.GeoDataFrame({"offre_id": ["v", "outside"], "type": ["velo", "bus"], "dans_pilote": [True, True]}, geometry=[Point(725, -25), Point(726, -25)], crs=2154)
        pairs, unknown, _, _ = route_pairs(net, pop, offers, 300, 150, 50)
        self.assertEqual(list(pairs.offre_id), ["v"])
        self.assertAlmostEqual(pairs.temps_pieton_s.iloc[0], 300)
        validation = validate_pairs(net, pop, offers, pairs, unknown)
        self.assertEqual(validation["etat"], "valide")
        self.assertEqual(validation["temoins_reference"][0]["reference_s"], 300)

    def test_failed_snap_is_unknown_and_duplicate_pairs_never_validate(self):
        nodes, edges = fixture()
        graph, _ = merge_network([nodes], [edges], box(-100, -100, 1100, 600))
        net = PairNetwork(graph, 4.5)
        pop = gpd.GeoDataFrame({"id_carreau": ["p"], "dans_pilote": [True]}, geometry=[Point(400, 0)], crs=2154)
        offers = gpd.GeoDataFrame({"offre_id": ["bad"], "type": ["bus"], "dans_pilote": [True]}, geometry=[Point(500, 60)], crs=2154)
        pairs, unknown, _, _ = route_pairs(net, pop, offers, 300, 150, 50)
        self.assertTrue(pairs.empty)
        self.assertEqual(unknown.to_dict("records"), [{"id_carreau": "p", "offre_id": "bad"}])
        bad = pd.DataFrame({"id_carreau": ["p", "p"], "offre_id": ["bad", "bad"], "temps_pieton_s": [10., 10.]})
        with self.assertRaisesRegex(ValueError, "plusieurs fois"):
            validate_pairs(net, pop, offers, bad, unknown)


if __name__ == "__main__":
    unittest.main()
