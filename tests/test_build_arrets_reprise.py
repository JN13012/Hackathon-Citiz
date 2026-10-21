"""Coupures réseau passagères : la collecte réessaie, puis réutilise un GTFS déjà téléchargé."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
import zipfile

import requests

from scripts import build_arrets as arrets


def make_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stops.txt", "stop_id\n")


class RetryAndCacheTests(unittest.TestCase):
    def test_transient_error_is_retried_then_succeeds(self):
        calls = []
        def action():
            calls.append(1)
            if len(calls) == 1:
                raise requests.ConnectionError("Connection reset by peer")
            return "ok"
        with patch.object(arrets.time, "sleep"):
            self.assertEqual(arrets.with_retries(action, "test"), "ok")
        self.assertEqual(len(calls), 2)

    def test_client_error_is_not_retried(self):
        response = MagicMock(status_code=401)
        def action():
            raise requests.HTTPError("401", response=response)
        with patch.object(arrets.time, "sleep") as sleep, self.assertRaises(requests.HTTPError):
            arrets.with_retries(action, "tcl_lyon")
        sleep.assert_not_called()

    def test_api_down_reuses_cached_gtfs(self):
        with TemporaryDirectory() as tmp:
            bronze = Path(tmp)
            make_zip(bronze / "citea_valence.zip")
            with patch.object(arrets, "BRONZE", bronze), \
                 patch.object(arrets, "resolve_gtfs_urls", side_effect=requests.ConnectionError("reset")):
                out = arrets.download_network("citea_valence", "slug")
            self.assertEqual(out, [("citea_valence", bronze / "citea_valence.zip")])

    def test_api_down_without_cache_still_fails(self):
        with TemporaryDirectory() as tmp:
            with patch.object(arrets, "BRONZE", Path(tmp)), \
                 patch.object(arrets, "resolve_gtfs_urls", side_effect=requests.ConnectionError("reset")):
                with self.assertRaises(requests.ConnectionError):
                    arrets.download_network("citea_valence", "slug")

    def test_refresh_does_not_fall_back_to_cache(self):
        with TemporaryDirectory() as tmp:
            bronze = Path(tmp)
            make_zip(bronze / "citea_valence.zip")
            with patch.object(arrets, "BRONZE", bronze), \
                 patch.object(arrets, "resolve_gtfs_urls", side_effect=requests.ConnectionError("reset")):
                with self.assertRaises(requests.ConnectionError):
                    arrets.download_network("citea_valence", "slug", refresh=True)

    def test_multi_file_network_cache_and_corrupt_zip_ignored(self):
        with TemporaryDirectory() as tmp:
            bronze = Path(tmp)
            make_zip(bronze / "cars_savoie_1.zip")
            make_zip(bronze / "cars_savoie_2.zip")
            (bronze / "cars_savoie_3.zip").write_bytes(b"pas un zip")
            with patch.object(arrets, "BRONZE", bronze):
                self.assertEqual([label for label, _ in arrets.cached_gtfs("cars_savoie")],
                                 ["cars_savoie_1", "cars_savoie_2"])


if __name__ == "__main__":
    unittest.main()
