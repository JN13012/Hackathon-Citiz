"""Garanties de préparation : couverture des bordures et refus des entrées partielles."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import geopandas as gpd
import pandas as pd
from shapely import Point, box, union_all

from geo.prepare_reseau_ara import parser, plan_tiles, prepare, sha256, tile_ids

X, Y = 912000, 6456000


class PreparationTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        boundary = box(X, Y, X + 2000, Y + 2000)
        contours = gpd.GeoDataFrame({"id_zone": ["commune_38185"], "niveau": ["commune"],
                                     "code_insee": ["38185"]}, geometry=[boundary], crs=2154)
        contours.to_parquet(root / "contours.parquet", index=False)
        gpd.GeoDataFrame({"region": ["84"]}, geometry=[boundary], crs=2154).to_parquet(root / "territoire.parquet")
        stops = gpd.GeoDataFrame({"arret_id": ["a"], "nom": ["Métro témoin"], "mode": ["metro"],
                                  "reseau": ["tcl_lyon"], "nb_passages_jour": [12]},
                                 geometry=[Point(X + 500, Y + 500)], crs=2154)
        stops.to_parquet(root / "arrets.parquet", index=False)
        stations = gpd.GeoDataFrame({"station_id": ["velo:1"], "nom": [""], "type": ["velo"],
                                     "commune": ["38185"], "operateur": ["Test"], "capacite": [None]},
                                    geometry=[Point(X + 900, Y + 900)], crs=2154)
        stations.to_parquet(root / "stations.parquet", index=False)
        # Le centre de b est extérieur, mais le carreau intersecte le territoire.
        population = gpd.GeoDataFrame({"id_carreau": ["a", "b"], "population": [25., 10.]},
            geometry=[box(X + 400, Y + 400, X + 600, Y + 600), box(X - 150, Y + 600, X + 50, Y + 800)], crs=2154)
        population.to_parquet(root / "population.parquet", index=False)
        osm = root / "osm"
        osm.mkdir()
        sources = []
        for name in ["auvergne", "rhone-alpes"]:
            pbf = osm / f"{name}.osm.pbf"
            pbf.write_bytes(name.encode())
            sources.append({"nom": name, "sha256": sha256(pbf)})
        (osm / "sources_osm.json").write_text(json.dumps({"archives": sources}))
        args = parser().parse_args([
            "--arrets", str(root / "arrets.parquet"), "--stations", str(root / "stations.parquet"),
            "--population", str(root / "population.parquet"), "--contours", str(root / "contours.parquet"),
            "--territoire", str(root / "territoire.parquet"), "--osm-dir", str(osm),
            "--output-dir", str(root / "sortie"), "--taille-tuile-m", "1000", "--reseaux-requis", "tcl_lyon",
        ])
        return args

    def test_tiles_cover_study_area_and_boundary_points_have_one_owner(self):
        boundary = box(X, Y, X + 2000, Y + 2000)
        tiles = plan_tiles(boundary, 1000, 1000)
        self.assertTrue(union_all(tiles.geometry).covers(boundary.buffer(1000)))
        points = gpd.GeoDataFrame(geometry=[Point(X, Y), Point(X + 1000, Y + 1000),
                                             Point(X - 50, Y + 700)], crs=2154)
        assigned = tile_ids(points, 1000)
        self.assertEqual(len(assigned), len(points))
        self.assertTrue(set(assigned).issubset(set(tiles.tuile_id)))
        self.assertEqual(assigned.iloc[1], f"x{X // 1000 + 1}_y{Y // 1000 + 1}")

    def test_partial_gtfs_is_rejected_before_publication(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            stops = gpd.read_parquet(args.arrets)
            stops["reseau"] = "tag_grenoble"
            stops.to_parquet(args.arrets, index=False)
            before = sha256(args.arrets)
            with self.assertRaisesRegex(ValueError, "réseaux absents.*tcl_lyon"):
                prepare(args)
            self.assertFalse(args.output_dir.exists())
            self.assertEqual(sha256(args.arrets), before)

    def test_outside_stop_is_rejected_before_publication(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            stops = gpd.read_parquet(args.arrets)
            stops.geometry = [Point(X + 5000, Y + 5000)]
            stops.to_parquet(args.arrets, index=False)
            with self.assertRaisesRegex(ValueError, "arrêts hors ARA"):
                prepare(args)
            self.assertFalse(args.output_dir.exists())

    def test_margin_must_cover_neighbours(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            args.marge_m = 500
            with self.assertRaisesRegex(ValueError, "Marge insuffisante"):
                prepare(args)
            self.assertFalse(args.output_dir.exists())

    def test_snapshot_preserves_border_population_unknown_capacity_and_labels(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            before = sha256(args.arrets)
            report = prepare(args)
            self.assertFalse(report["graphe_construit"])
            self.assertEqual(report["centres_frontaliers_conserves"], 1)
            self.assertEqual(report["population"], 35.)
            offers = gpd.read_parquet(args.output_dir / "entrees/gold_offre.parquet")
            station = offers.loc[offers["type"].eq("velo")].iloc[0]
            self.assertEqual(station.nom, "velo:1")
            self.assertEqual(station.code_commune, "38185")
            self.assertTrue(pd.isna(station.valeur))
            tiles = gpd.read_parquet(args.output_dir / "tuiles.parquet")
            self.assertEqual(int(tiles.nb_centres_population.sum()), 2)
            self.assertEqual(int(tiles.nb_points_offre.sum()), 2)
            zones = gpd.read_parquet(args.output_dir / "zones_extraction.parquet").set_index("tuile_id")
            for row in tiles.itertuples():
                self.assertTrue(zones.loc[row.tuile_id].geometry.covers(row.geometry.buffer(1000)))
            for path, digest in report["fichiers"].items():
                self.assertEqual(sha256(args.output_dir / path), digest)
            self.assertEqual(sha256(args.arrets), before)

    def test_existing_preparation_is_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            args.output_dir.mkdir()
            sentinel = args.output_dir / "ancien.txt"
            sentinel.write_text("à conserver")
            with self.assertRaisesRegex(ValueError, "existe déjà"):
                prepare(args)
            self.assertEqual(sentinel.read_text(), "à conserver")

    def test_bad_archive_checksum_leaves_no_published_snapshot(self):
        with TemporaryDirectory() as tmp:
            args = self.fixture(tmp)
            (args.osm_dir / "auvergne.osm.pbf").write_bytes(b"corrompu")
            with self.assertRaisesRegex(ValueError, "empreinte incorrecte"):
                prepare(args)
            self.assertFalse(args.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
