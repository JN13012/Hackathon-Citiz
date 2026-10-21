"""Routage régional, confidentialité et reprise : comparaison au moteur du pilote."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import Point, box

from geo.build_accessibilite import aggregate, population_groups
from geo.build_accessibilite_ara import aggregate_region, tile_receipt
from geo.check_raccordements_ara import sha256
from geo.csr_walking import CSRWalking, route_candidates
from geo.population_groups_fast import population_groups_fast
from tests import test_build_accessibilite as pilot_tests
from tests.test_build_accessibilite import network_fixture, X, Y


def cached(network, points, identifiers, key="offre_id", max_snap=50):
    rows = []
    for point, identifier in zip(points, identifiers):
        snap = network.snap(point, 300, max_snap)
        u, v, edge = network.keys[snap.index]
        rows.append({key: identifier, "geometry": point, "statut": snap.statut,
                     "raccord_de": u, "raccord_vers": v, "raccord_cle": edge,
                     "abscisse_raccord_m": snap.position, "temps_acces_s": snap.distance / 1.25,
                     "distance_raccordement_m": snap.distance, "noeud_osm": None, "acces_id": None})
    return gpd.GeoDataFrame(rows, crs=2154)


def engine_from(network):
    rows = [{"de": u, "vers": v, "cle": k, "longueur_m": a["longueur_m"], "temps_pieton_s": a["temps_pieton_s"]}
            for u, v, k, a in network.graph.edges(keys=True, data=True)]
    return CSRWalking(pd.DataFrame(rows))


class CSRTests(unittest.TestCase):
    def test_exact_boundary_long_edge_junction_and_disconnected_match_legacy(self):
        network = network_fixture(disconnected=True)
        engine = engine_from(network)
        points = [Point(X + 400, Y - 25), Point(X + 725, Y - 25),
                  Point(X + 726, Y - 25), Point(X - 15, Y + 200),
                  Point(X + 150, Y - 10), Point(X + 400, Y + 20)]
        targets = cached(network, points, [str(i) for i in range(len(points))])
        prepared = engine.prepare_targets(targets)
        for i, point in enumerate(points):
            snap = network.snap(point, 300, 50)
            result = engine.durations(targets.iloc[i], targets, prepared)
            for j, target in enumerate(points):
                reference = network.travel_time(snap, network.snap(target, 300, 50), network.node_times(snap, 300))
                if reference <= 300:
                    self.assertAlmostEqual(result[j], reference)
                else:
                    self.assertGreater(result[j], 300)
        self.assertAlmostEqual(engine.durations(targets.iloc[0], targets, prepared)[1], 300)

    def test_parallel_arcs_are_alternatives_not_summed(self):
        network = network_fixture()
        network.graph.add_edge(1, 2, key=1, longueur_m=2000., temps_pieton_s=1600.)
        engine = engine_from(network)
        self.assertEqual(engine.matrix[engine.ids.get_loc(1), engine.ids.get_loc(2)], 800)

    def test_access_union_minimum_and_unknown_are_not_double_counted(self):
        network = network_fixture()
        engine = engine_from(network)
        source = cached(network, [Point(X + 100, Y)], ["p"], key="id_carreau")
        targets = cached(network, [Point(X, Y), Point(X, Y)], ["station", "station"])
        targets["noeud_osm"] = [1, 999999]
        targets["acces_id"] = ["entree", "absente"]
        candidates = np.array([[0, 0], [0, 1]])
        pairs, pending = route_candidates(engine, source, targets, candidates)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs.iloc[0].acces_id, "entree")
        self.assertAlmostEqual(pairs.iloc[0].temps_pieton_s, 80)
        self.assertTrue(pending.empty)
        targets.loc[0, "noeud_osm"] = 888888
        pairs, pending = route_candidates(engine, source, targets, candidates)
        self.assertTrue(pairs.empty)
        self.assertEqual(len(pending), 1)

    def test_bad_snaps_are_indeterminate_and_missing_source_key_fails(self):
        network = network_fixture()
        engine = engine_from(network)
        source = cached(network, [Point(X + 100, Y)], ["p"], key="id_carreau")
        targets = cached(network, [Point(X + 100, Y + 60)], ["s"])
        pairs, pending = route_candidates(None, source, targets, np.array([[0], [0]]))
        self.assertTrue(pairs.empty)
        self.assertEqual(len(pending), 1)
        source.loc[0, "raccord_cle"] = 99
        with self.assertRaises(KeyError):
            engine.durations(source.iloc[0], targets, engine.prepare_targets(targets))


class RegionalGroupingTests(unittest.TestCase):
    def test_fast_grouping_exactly_matches_greedy_reference(self):
        rng = np.random.default_rng(42)
        for size, spacing in [(1, 200), (30, 200), (40, 230)]:
            cells = gpd.GeoDataFrame({"id_carreau": [f"c{i:03}" for i in range(size)],
                                     "population": rng.integers(1, 36, size).astype(float)},
                                    geometry=[box(i % 6 * spacing, i // 6 * spacing,
                                                  i % 6 * spacing + 200, i // 6 * spacing + 200) for i in range(size)], crs=2154)
            expected, mapping, fallback = population_groups(cells, 20)
            actual, other, count = population_groups_fast(cells.sample(frac=1, random_state=8), 20)
            self.assertEqual(mapping.to_dict("records"), other.to_dict("records"))
            self.assertEqual(fallback, count)
            self.assertEqual(expected.population.tolist(), actual.population.tolist())
            self.assertTrue(expected.geometry.geom_equals(actual.geometry).all())

    def test_vectorized_aggregate_matches_pilot_with_missing_and_uncertain_values(self):
        cells, pop, offers = pilot_tests.AggregationTests().fixtures()
        pop["dans_pilote"], offers["dans_pilote"] = True, True
        pop["statut"] = ["ok", "ok", "raccordement_trop_loin"]
        offers.loc[1, "valeur"] = np.nan
        pq = pop[["id_carreau", "statut"]]
        oq = pd.DataFrame({"offre_id": offers.offre_id, "statut": ["ok", "ok"], "nb_acces": [0, 2]})
        pairs = pd.DataFrame({"id_carreau": ["a", "a", "b"], "offre_id": ["s1", "s2", "s1"], "temps_pieton_s": [10., 20., 30.]})
        unknown = pd.DataFrame({"id_carreau": ["c"], "offre_id": ["s1"]})
        expected_o1, expected_o2, _, expected_counts, _ = aggregate(pop, offers, cells, pairs, unknown, pq, oq, 20)
        groups, mapping, _ = population_groups_fast(cells, 20)
        o1, o2, counts = aggregate_region({"pop": pop, "offers": offers, "oq": oq}, pairs, unknown, groups, mapping)
        for column in expected_o1.columns:
            if column == "geometry":
                self.assertTrue(o1.geometry.geom_equals(expected_o1.geometry).all())
            else:
                pd.testing.assert_series_equal(o1[column].isna(), expected_o1[column].isna())
                valid = o1[column].notna()
                pd.testing.assert_series_equal(o1.loc[valid, column], expected_o1.loc[valid, column], check_dtype=False)
        for column in ["statut", "population_desservie", "population_desservie_min", "nb_carreaux_desservis"]:
            pd.testing.assert_series_equal(o2[column], expected_o2[column], check_dtype=False)
        pd.testing.assert_frame_equal(counts, expected_counts.drop(columns="dans_pilote"), check_dtype=False)

    def test_checkpoint_rejects_incompatible_signature_and_corruption(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(tile_receipt(root, "tile", {"v": 1}))
            tile = root / "tuiles/tile"
            tile.mkdir(parents=True)
            (tile / "paires.parquet").write_bytes(b"checkpoint")
            receipt = {"signature": {"v": 1}, "tuile_id": "tile", "fichiers_sha256": {"paires.parquet": sha256(tile / "paires.parquet")}}
            (tile / "calcul_qc.json").write_text(json.dumps(receipt))
            self.assertEqual(tile_receipt(root, "tile", {"v": 1}), receipt)
            with self.assertRaises(ValueError):
                tile_receipt(root, "tile", {"v": 2})
            (tile / "paires.parquet").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                tile_receipt(root, "tile", {"v": 1})


if __name__ == "__main__":
    unittest.main()
