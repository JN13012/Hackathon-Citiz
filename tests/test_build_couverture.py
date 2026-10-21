"""Couverture candidat–population : référence analytique et contrôles d'intégration."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import Point

from geo.build_couverture import summarize
from geo.coverage_routing import CoverageNetwork, route_coverage
from geo.check_raccordements_ara import sha256
from geo.validate_couverture import publish
from tests.test_build_accessibilite import network_fixture, X, Y
from tests.test_build_accessibilite_ara import cached


def engine_from(network):
    return CoverageNetwork(pd.DataFrame([
        {"de": u, "vers": v, "cle": k, "longueur_m": a["longueur_m"], "temps_pieton_s": a["temps_pieton_s"]}
        for u, v, k, a in network.graph.edges(keys=True, data=True)]))


class CoverageTests(unittest.TestCase):
    def test_publication_requires_valid_proof_and_preserves_existing_delivery(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, destination = root / "calcul", root / "gold"
            result = output / "resultats"
            result.mkdir(parents=True)
            destination.mkdir()
            (result / "gold_couverture.parquet").write_bytes(b"verified")
            qc = result / "couverture_qc.json"
            qc.write_text(json.dumps({"fichiers_sha256": {"gold_couverture.parquet": sha256(result / "gold_couverture.parquet")}}))
            proof = output / "controle_independant.json"
            proof.write_text(json.dumps({"etat": "valide", "couverture_qc_sha256": "wrong"}))
            with self.assertRaises(ValueError):
                publish(output, destination)
            proof.write_text(json.dumps({"etat": "valide", "couverture_qc_sha256": sha256(qc)}))
            target = destination / "gold_couverture.parquet"
            target.write_bytes(b"other delivery")
            with self.assertRaises(ValueError):
                publish(output, destination)
            self.assertEqual(target.read_bytes(), b"other delivery")
            self.assertFalse((destination / "gold_couverture_qc.json").exists())
            target.unlink()
            publish(output, destination)
            self.assertEqual(target.read_bytes(), b"verified")
            before = target.stat().st_mtime_ns
            publish(output, destination)
            self.assertEqual(target.stat().st_mtime_ns, before)

    def test_seed_cases_long_edge_junction_budget_and_components_match_reference(self):
        network = network_fixture(disconnected=True)
        engine = engine_from(network)
        points = [Point(X + 400, Y - 25), Point(X + 725, Y - 25), Point(X + 726, Y - 25),
                  Point(X + 100, Y), Point(X + 150, Y - 10), Point(X - 15, Y + 200),
                  Point(X + 100, Y + 20), Point(X, Y), Point(X + 1000, Y)]
        frame = cached(network, points, [str(i) for i in range(len(points))], key="id_carreau")
        prepared = engine.prepare(frame)
        for i, point in enumerate(points):
            start = network.snap(point, 300, 50)
            actual = engine.times(prepared, i, prepared, np.arange(len(points)))
            for j, end in enumerate(points):
                expected = network.travel_time(start, network.snap(end, 300, 50), network.node_times(start, 300))
                if expected <= 300:
                    self.assertAlmostEqual(actual[j], expected)
                else:
                    self.assertGreater(actual[j], 300)
        self.assertAlmostEqual(engine.times(prepared, 0, prepared, np.array([1]))[0], 300)

    def test_relations_self_coverage_and_two_distinct_keys(self):
        network = network_fixture()
        pop = cached(network, [Point(X + 100, Y), Point(X + 300, Y), Point(X + 700, Y)], ["a", "b", "c"], key="id_carreau")
        candidates = pop.copy()
        candidates["id_candidat"] = candidates.id_carreau
        query = np.array([[0, 0, 1, 1, 2], [0, 1, 0, 1, 2]])
        pairs, unknown = route_coverage(engine_from(network), candidates, pop, query)
        self.assertEqual(set(map(tuple, pairs[["id_candidat", "id_carreau"]].to_numpy())),
                         {("a", "a"), ("a", "b"), ("b", "a"), ("b", "b"), ("c", "c")})
        self.assertTrue(unknown.empty)
        self.assertAlmostEqual(pairs.loc[pairs.id_candidat.eq("a") & pairs.id_carreau.eq("b"), "temps_pieton_s"].iloc[0], 160)

    def test_bad_candidate_is_not_zero_and_population_threshold_is_distinct(self):
        network = network_fixture()
        pop = cached(network, [Point(X + 100, Y), Point(X + 100, Y + 100)], ["a", "b"], key="id_carreau", max_snap=150)
        candidates = pop.copy()
        candidates["id_candidat"] = candidates.id_carreau
        candidates.loc[candidates.distance_raccordement_m.gt(50), "statut"] = "raccordement_trop_loin"
        pairs, unknown = route_coverage(engine_from(network), candidates, pop, np.array([[0, 0, 1, 1], [0, 1, 0, 1]]))
        self.assertEqual(set(pairs.id_candidat), {"a"})
        self.assertEqual(set(pairs.id_carreau), {"a", "b"})
        self.assertEqual(set(unknown.id_candidat), {"b"})
        candidates["tuile_id"], candidates["code_commune"] = "t", "00000"
        pop["population"] = [10., 20.]
        result = summarize(candidates, pop, pairs, unknown).set_index("id_candidat")
        self.assertEqual(result.loc["a", "population_couverte"], 30)
        self.assertEqual(result.loc["b", "statut"], "non_calcule")
        self.assertTrue(pd.isna(result.loc["b", "population_couverte_min"]))
        self.assertFalse(result.loc["b", "utilisable_optimiseur"])

    def test_missing_target_is_unknown_missing_primary_source_fails(self):
        network = network_fixture()
        pop = cached(network, [Point(X + 100, Y)], ["a"], key="id_carreau")
        candidate = pop.assign(id_candidat="a")
        missing = pop.copy()
        missing["raccord_cle"] = 999
        pairs, unknown = route_coverage(engine_from(network), candidate, missing, np.array([[0], [0]]))
        self.assertTrue(pairs.empty)
        self.assertEqual(unknown.motif.iloc[0], "population_segment_absent_tuile")
        with self.assertRaises(ValueError):
            route_coverage(engine_from(network), missing.assign(id_candidat="a"), pop, np.array([[0], [0]]))

    def test_partial_summary_exposes_lower_bound_and_does_not_sum_overlap(self):
        candidates = gpd.GeoDataFrame({"id_candidat": ["a", "b"], "id_carreau": ["a", "b"], "tuile_id": ["t", "t"],
                                      "code_commune": ["00000", "00000"], "distance_raccordement_m": [0., 0.], "statut": ["ok", "ok"]},
                                     geometry=[Point(0, 0), Point(100, 0)], crs=2154)
        pop = pd.DataFrame({"id_carreau": ["p", "q"], "population": [50., 10.]})
        pairs = pd.DataFrame({"id_candidat": ["a", "b"], "id_carreau": ["p", "p"], "temps_pieton_s": [10., 20.]})
        unknown = pd.DataFrame({"id_candidat": ["a"], "id_carreau": ["q"], "motif": ["population_raccordement_trop_loin"]})
        result = summarize(candidates, pop, pairs, unknown).set_index("id_candidat")
        self.assertEqual(result.loc["a", "statut"], "partiel")
        self.assertEqual(result.loc["a", "population_couverte_min"], 50)
        self.assertTrue(pd.isna(result.loc["a", "population_couverte"]))
        self.assertEqual(result.loc["b", "population_couverte"], 50)
        self.assertEqual(pop.loc[pop.id_carreau.isin(pairs.id_carreau), "population"].sum(), 50)


if __name__ == "__main__":
    unittest.main()
