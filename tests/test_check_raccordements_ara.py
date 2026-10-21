"""Raccordements : coûts, ambiguïtés topologiques, bordures et publication sûre."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely import LineString, Point, box

from geo.check_raccordements_ara import build, parser, sha256, snap_points


def network(lines=None):
    lines = lines or [(1, 2, 10, [(0, 0), (100, 0)], 0)]
    ns, es = {}, []
    for u, v, k, coords, comp in lines:
        ns[u] = (Point(coords[0]), comp)
        ns[v] = (Point(coords[-1]), comp)
        es.extend([{"de": u, "vers": v, "cle": k, "geometry": LineString(coords)},
                   {"de": v, "vers": u, "cle": k, "geometry": LineString(coords[::-1])}])
    nodes = gpd.GeoDataFrame([{"noeud_id": n, "composante_id": c, "geometry": p} for n, (p, c) in ns.items()], crs=2154)
    return nodes, gpd.GeoDataFrame(es, crs=2154)


def points(coords, types=None, thresholds=None):
    types = types or ["bus"] * len(coords)
    thresholds = thresholds or [50] * len(coords)
    return gpd.GeoDataFrame({"type": types, "seuil_raccordement_m": thresholds}, geometry=[Point(*p) for p in coords], crs=2154)


class SnappingTests(unittest.TestCase):
    def test_costs_and_thresholds_include_the_exact_boundary(self):
        n, e = network()
        p = points([(50, 50), (50, 50.1), (50, 150), (50, 150.1)], thresholds=[50, 50, 150, 150])
        r = snap_points(p, n, e, box(-1000, -1000, 1100, 1100), 4.5, 300)
        self.assertEqual(list(r.statut), ["ok", "raccordement_trop_loin", "ok", "raccordement_trop_loin"])
        self.assertEqual(r.temps_acces_s.iloc[0], 40)
        self.assertEqual(r.nb_troncons_equidistants.iloc[0], 1)  # reverse arc ignored

    def test_curved_geometry_projection_and_abscissa(self):
        n, e = network([(8, 2, 3, [(0, 0), (0, 100), (100, 100)], 0)])
        r = snap_points(points([(50, 110)]), n, e, box(-1000, -1000, 1100, 1100), 4.5, 300).iloc[0]
        self.assertEqual((r.raccord_de, r.raccord_vers, r.raccord_cle), (2, 8, 3))
        self.assertEqual((r.raccord_x, r.raccord_y), (50, 100))
        self.assertEqual(r.abscisse_raccord_m, 50)
        self.assertEqual(r.temps_acces_s, 8)

    def test_coincident_disconnected_segments_are_flagged_and_deterministic(self):
        n, e = network([(1, 2, 20, [(0, 0), (100, 0)], 0), (3, 4, 10, [(0, 0), (100, 0)], 1)])
        p = points([(50, 5)])
        for edges in [e, e.iloc[::-1]]:
            r = snap_points(p, n, edges, box(-1000, -1000, 1100, 1100), 4.5, 300).iloc[0]
            self.assertEqual(r.raccord_cle, 20)  # tuple ordering, not key alone
            self.assertEqual(r.nb_troncons_equidistants, 2)
            self.assertEqual(r.nb_composantes_equidistantes, 2)
            self.assertTrue(r.a_examiner)

    def test_shared_junction_is_not_a_disconnected_ambiguity(self):
        n, e = network([(1, 2, 10, [(0, 0), (100, 0)], 0), (2, 3, 20, [(100, 0), (100, 100)], 0)])
        r = snap_points(points([(100, 0)]), n, e, box(-1000, -1000, 1100, 1100), 4.5, 300).iloc[0]
        self.assertEqual(r.nb_troncons_equidistants, 2)
        self.assertEqual(r.nb_composantes_equidistantes, 1)
        self.assertFalse(r.a_examiner)

    def test_secondary_component_and_metro_stay_in_outputs(self):
        n, e = network([(1, 2, 10, [(0, 0), (100, 0)], 0), (2, 3, 20, [(100, 0), (200, 0)], 0),
                        (4, 5, 30, [(0, 100), (100, 100)], 1)])
        r = snap_points(points([(50, 101), (50, 1)], types=["velo", "metro"]), n, e, box(-1000, -1000, 1100, 1100), 4.5, 300)
        self.assertEqual(list(r.statut), ["ok", "ok"])
        self.assertEqual(list(r.composante_locale_noeuds), [2, 3])
        self.assertTrue(r.composante_locale_minoritaire.iloc[0])
        self.assertTrue(r.entree_metro_a_verifier.iloc[1])
        self.assertTrue(r.a_examiner.all())

    def test_truncated_search_is_explicit_and_insufficient_margin_is_rejected(self):
        n, e = network()
        r = snap_points(points([(50, 800)]), n, e, box(-1000, -1000, 1100, 1100), 4.5, 300).iloc[0]
        self.assertTrue(r.recherche_bornee_par_marge)
        self.assertEqual(r.statut, "raccordement_trop_loin")
        with self.assertRaisesRegex(ValueError, "Marge insuffisante"):
            snap_points(points([(50, 80)]), n, e, box(-100, -100, 200, 100), 4.5, 300)


class PublicationTests(unittest.TestCase):
    def fixture(self, tmp):
        root = Path(tmp)
        prep, graph = root / "preparation", root / "reseau"
        inputs = prep / "entrees"
        inputs.mkdir(parents=True)
        tile = "x0_y0"
        td = graph / tile
        td.mkdir(parents=True)
        p = gpd.GeoDataFrame({"id_carreau": ["p"], "population": [12.], "tuile_id": [tile]}, geometry=[Point(50, 10)], crs=2154)
        o = gpd.GeoDataFrame({"offre_id": ["o"], "type": ["metro"], "nom": ["station"], "tuile_id": [tile]}, geometry=[Point(50, 20)], crs=2154)
        p.to_parquet(inputs / "gold_candidates.parquet", index=False)
        o.to_parquet(inputs / "gold_offre.parquet", index=False)
        plan = {"tuiles": 1, "tuiles_avec_origines": 1, "carreaux": 1, "offres": 1,
                "parametres": {"taille_tuile_m": 10000, "vitesse_kmh": 4.5, "duree_s": 300},
                "fichiers": {str(f.relative_to(prep)): sha256(f) for f in inputs.iterdir()}}
        pp = prep / "plan_reseau_ara.json"
        pp.write_text(json.dumps(plan))
        n, e = network()
        n.to_parquet(td / "reseau_pieton_noeuds.parquet", index=False)
        e.to_parquet(td / "reseau_pieton_troncons.parquet", index=False)
        gpd.GeoDataFrame(geometry=[box(-1000, -1000, 11000, 11000)], crs=2154).to_parquet(td / "zone_requete.parquet", index=False)
        signature = {"plan_sha256": sha256(pp), "vitesse_kmh": 4.5}
        qc = {"signature": signature, "etat": "valide", "modele_marche": "bidirectionnel", "tuile_id": tile,
              "fichiers": {f.name: sha256(f) for f in td.iterdir()}}
        qp = td / "reseau_pieton_qc.json"
        qp.write_text(json.dumps(qc))
        manifest = graph / "construction_reseau_ara.json"
        manifest.write_text(json.dumps({"etat": "regional_termine", "signature": signature, "tuiles_validees": 1,
                                       "tuiles": {tile: {"qc_sha256": sha256(qp)}}}))
        (graph / "controle_independant.json").write_text(json.dumps({"etat": "valide", "tuiles_controlees": 1, "manifest_sha256": sha256(manifest)}))
        return parser().parse_args(["--preparation", str(prep), "--reseau", str(graph), "--output-dir", str(root / "sortie")])

    def test_full_publication_counts_once_and_preserves_sources(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            before = sha256(args.preparation / "entrees/gold_candidates.parquet")
            r = build(args)
            self.assertEqual(r["etat"], "controle_regional_termine")
            self.assertEqual(r["resultats"]["population"]["population_totale"], 12)
            self.assertEqual(r["resultats"]["offre"]["points"], 1)
            self.assertFalse(r["o1_o2_calcules"])
            self.assertEqual(sha256(args.preparation / "entrees/gold_candidates.parquet"), before)
            for name, digest in r["fichiers_sha256"].items():
                self.assertEqual(sha256(args.output_dir / name), digest)
            with self.assertRaisesRegex(ValueError, "Refus d'écraser"):
                build(args)

    def test_partial_run_is_never_marked_regional(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            args.tuiles = ["x0_y0"]
            self.assertEqual(build(args)["etat"], "controle_partiel_termine")

    def test_corrupted_tile_prevents_publication(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            (args.reseau / "x0_y0/reseau_pieton_noeuds.parquet").write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "empreinte"):
                build(args)
            self.assertFalse(args.output_dir.exists())

    def test_source_changed_during_snapping_prevents_publication(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            original_snap = snap_points

            def change_source(*a, **kw):
                result = original_snap(*a, **kw)
                with (args.preparation / "entrees/gold_offre.parquet").open("ab") as stream:
                    stream.write(b"changed")
                return result

            with patch("geo.check_raccordements_ara.snap_points", side_effect=change_source):
                with self.assertRaisesRegex(ValueError, "empreinte"):
                    build(args)
            self.assertFalse(args.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
