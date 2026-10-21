"""La collecte échouée ne doit jamais remplacer une livraison validée."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely import Point

from scripts import build_arrets as arrets
from scripts import run_all


class CollectionGuardTests(unittest.TestCase):
    def fixture(self, tmp):
        folder = Path(tmp)
        point = gpd.GeoSeries([Point(5.72, 45.18)], crs=4326).to_crs(2154).iloc[0]
        gpd.GeoDataFrame({"niveau": ["commune"]}, geometry=[point.buffer(1000)], crs=2154).to_parquet(folder / "contours.parquet")
        (folder / "arrets.parquet").write_bytes(b"livraison valide precedente")
        (folder / "arrets_qc.txt").write_bytes(b"rapport valide precedent")
        return folder, point

    def raw(self):
        return pd.DataFrame({"nom": ["Grenoble témoin", "Paris hors ARA"], "mode": ["bus", "bus"],
                             "nb_passages_jour": [12., 24.], "reseau": ["test", "test"],
                             "lon": [5.72, 2.35], "lat": [45.18, 48.85]})

    def assert_previous(self, folder):
        self.assertEqual((folder / "arrets.parquet").read_bytes(), b"livraison valide precedente")
        self.assertEqual((folder / "arrets_qc.txt").read_bytes(), b"rapport valide precedent")

    def test_http_failure_preserves_files_and_does_not_parse_partial_collection(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            with patch.object(arrets, "SILVER", folder), patch.object(arrets, "NETWORKS", {"bus": "s1", "tcl_lyon": "s2"}), \
                 patch.object(arrets, "download_network", side_effect=[[('bus', Path('bus.zip'))], RuntimeError("HTTP 401")]), \
                 patch.object(arrets, "stops_from_gtfs") as read:
                with self.assertRaisesRegex(RuntimeError, "Collecte incomplète.*tcl_lyon"):
                    arrets.main([])
                read.assert_not_called()
            self.assert_previous(folder)

    def test_parse_failure_preserves_files(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            with patch.object(arrets, "SILVER", folder), \
                 patch.object(arrets, "stops_from_gtfs", side_effect=ValueError("GTFS invalide")):
                with self.assertRaisesRegex(RuntimeError, "Lecture GTFS incomplète"):
                    arrets.main(["--gtfs", "test=fichier.zip"])
            self.assert_previous(folder)

    def test_empty_gtfs_preserves_files(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            with patch.object(arrets, "SILVER", folder), patch.object(arrets, "stops_from_gtfs", return_value=pd.DataFrame()):
                with self.assertRaisesRegex(RuntimeError, "Lecture GTFS incomplète"):
                    arrets.main(["--gtfs", "test=fichier.zip"])
            self.assert_previous(folder)

    def test_missing_contours_stops_before_network_calls(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            (folder / "contours.parquet").unlink()
            with patch.object(arrets, "SILVER", folder), patch.object(arrets, "download_network") as download:
                with self.assertRaisesRegex(ValueError, "Contours obligatoires"):
                    arrets.main([])
                download.assert_not_called()
            self.assert_previous(folder)

    def test_success_filters_territory_before_publication(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            with patch.object(arrets, "SILVER", folder), patch.object(arrets, "stops_from_gtfs", return_value=self.raw()):
                arrets.main(["--gtfs", "test=fichier.zip"])
            result = gpd.read_parquet(folder / "arrets.parquet")
            self.assertEqual(len(result), 1)
            self.assertEqual(result.iloc[0].nom, "Grenoble témoin")
            self.assertEqual(result.iloc[0].nb_passages_jour, 12.)
            self.assertIn("Arrêts hors territoire écartés     : 1", (folder / "arrets_qc.txt").read_text())

    def test_missing_frequency_is_not_silently_summed_to_zero(self):
        with TemporaryDirectory() as tmp:
            folder, _ = self.fixture(tmp)
            raw = self.raw()
            raw.loc[0, "nb_passages_jour"] = float("nan")
            with patch.object(arrets, "SILVER", folder), patch.object(arrets, "stops_from_gtfs", return_value=raw):
                with self.assertRaisesRegex(ValueError, "Fréquences source absentes"):
                    arrets.main(["--gtfs", "test=fichier.zip"])
            self.assert_previous(folder)

    def test_residual_duplicates_block_publication_but_zero_is_not_missing(self):
        frame = gpd.GeoDataFrame({"arret_id": ["a", "b"], "nom": ["Même arrêt", "Même arrêt"],
            "mode": ["autre", "bus"], "reseau": ["test", "test"], "nb_passages_jour": [0, 12]},
            geometry=[Point(900000, 6400000), Point(900001, 6400000)], crs=2154)
        boundary = frame.geometry.iloc[0].buffer(1000)
        with self.assertRaisesRegex(ValueError, "doublons résiduels"):
            arrets.validate_stops(frame, boundary)
        arrets.validate_stops(frame.iloc[:1], boundary)

    def test_publication_failure_restores_old_data_and_report(self):
        with TemporaryDirectory() as tmp:
            folder, point = self.fixture(tmp)
            frame = gpd.GeoDataFrame({"arret_id": ["a"]}, geometry=[point], crs=2154)
            real_replace = Path.replace

            def fail_second(path, target):
                if path.name == "arrets_qc.txt":
                    raise OSError("Échec disque simulé")
                return real_replace(path, target)

            with patch.object(arrets, "SILVER", folder), patch.object(Path, "replace", fail_second):
                with self.assertRaisesRegex(OSError, "Échec disque"):
                    arrets.publish_stops(frame, "nouveau rapport")
            self.assert_previous(folder)

    def test_pipeline_stops_before_gold_when_collection_fails(self):
        # les étapes qui précèdent la collecte (contours IGN) réussissent ; la collecte des arrêts échoue
        def fake_run(cmd, **kwargs):
            if "scripts/build_arrets.py" in cmd:
                raise subprocess.CalledProcessError(1, ["collecte"])
        with patch.object(run_all.subprocess, "run", side_effect=fake_run) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                run_all.main()
            called = [call.args[0] for call in run.call_args_list]
            self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))
            self.assertIn("scripts/build_arrets.py", called[-1])
            self.assertIn("--contours", called[-1])
            self.assertFalse(any("scripts/build_gold.py" in c for c in called))  # arrêt avant le gold


if __name__ == "__main__":
    unittest.main()
