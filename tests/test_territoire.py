"""Territoire d'analyse : ARA par défaut (chemins inchangés), France métropolitaine à part."""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "optim"))
sys.path.insert(0, str(ROOT / "scripts"))


def load(territoire=None):
    env = {k: v for k, v in os.environ.items() if k != "TERRITOIRE"}
    if territoire is not None:
        env["TERRITOIRE"] = territoire
    with mock.patch.dict(os.environ, env, clear=True):
        import territoire as T
        return importlib.reload(T)


class TerritoireTest(unittest.TestCase):
    def tearDown(self):
        load()  # les autres tests attendent le territoire par défaut

    def test_ara_par_defaut_garde_les_chemins_historiques(self):
        T = load()
        self.assertEqual((T.NOM, T.NIVEAU, T.CODES), ("ara", "region", ["84"]))
        self.assertEqual(T.SILVER, ROOT / "data" / "silver")
        self.assertEqual(T.GOLD, ROOT / "data" / "gold")
        self.assertEqual(T.BRONZE_TERRITOIRE, ROOT / "data" / "bronze")
        self.assertEqual(len(T.DEPARTEMENTS), 12)

    def test_france_metropolitaine_corse_comprise(self):
        T = load("france")
        self.assertEqual(len(T.CODES), 13)
        self.assertIn("94", T.CODES)
        self.assertEqual(len(T.DEPARTEMENTS), 96)
        self.assertEqual(len(set(T.DEPARTEMENTS)), 96)
        self.assertTrue({"2A", "2B", "75", "13"} <= set(T.DEPARTEMENTS))
        self.assertFalse(any(d.startswith("97") for d in T.DEPARTEMENTS))

    def test_france_ecrit_a_part_et_partage_les_sources(self):
        T = load("france")
        self.assertEqual(T.SILVER, ROOT / "data" / "france" / "silver")
        self.assertEqual(T.GOLD, ROOT / "data" / "france" / "gold")
        self.assertEqual(T.BRONZE, ROOT / "data" / "bronze")
        self.assertEqual(T.BRONZE_TERRITOIRE, ROOT / "data" / "france" / "bronze")

    def test_cadre_france_contient_le_cadre_ara(self):
        ara, france = load("ara").CADRE, load("france").CADRE
        self.assertLessEqual(france[0], ara[0])
        self.assertLessEqual(france[1], ara[1])
        self.assertGreaterEqual(france[2], ara[2])
        self.assertGreaterEqual(france[3], ara[3])

    def test_territoire_inconnu_refuse(self):
        with self.assertRaises(SystemExit):
            load("bretagne")


class DecouverteReseauxTest(unittest.TestCase):
    """Hors ARA, les réseaux GTFS viennent du catalogue national, filtrés sur le territoire."""

    def setUp(self):
        load("france")
        import build_arrets
        self.B = importlib.reload(build_arrets)

    def tearDown(self):
        load()
        import build_arrets
        importlib.reload(build_arrets)

    def catalogue(self):
        gtfs = [{"format": "GTFS"}]
        return [
            {"type": "public-transit", "slug": "horaires-theoriques-du-reseau-tag", "datagouv_id": "a",
             "resources": gtfs, "covered_area": [{"type": "epci", "insee": "200040715"}]},
            {"type": "public-transit", "slug": "reseau-de-rennes", "datagouv_id": "b",
             "resources": gtfs, "covered_area": [{"type": "commune", "insee": "35238"}]},
            {"type": "public-transit", "slug": "sncf-ter", "datagouv_id": "c",
             "resources": gtfs, "covered_area": [{"type": "pays", "insee": "FR"}]},
            {"type": "public-transit", "slug": "reseau-martinique", "datagouv_id": "d",
             "resources": gtfs, "covered_area": [{"type": "departement", "insee": "972"}]},
            {"type": "public-transit", "slug": "netex-seulement", "datagouv_id": "e",
             "resources": [{"format": "NeTEx"}], "covered_area": [{"type": "commune", "insee": "35238"}]},
            {"type": "vehicles-sharing", "slug": "velos", "datagouv_id": "f",
             "resources": gtfs, "covered_area": [{"type": "commune", "insee": "35238"}]},
        ]

    def codes(self):
        return {"commune": {"35238", "38185"}, "departement": {"35", "38"},
                "epci": {"200040715"}, "region": {"53", "84"}}

    def run_discovery(self, **kw):
        response = mock.Mock()
        response.json.return_value = self.catalogue()
        with mock.patch.object(self.B.requests, "get", return_value=response):
            return self.B.discover_networks(self.codes(), **kw)

    def test_garde_les_reseaux_du_territoire_avec_gtfs(self):
        found = self.run_discovery()
        # nom ARA réutilisé (GTFS déjà en cache), sinon nom tiré du slug
        self.assertEqual(found, {"reseau_de_rennes": "b", "tag_grenoble": "a"})

    def test_agregats_regionaux_et_vols_ecartes(self):
        extra = [
            {"type": "public-transit", "title": "Agrégat des réseaux urbains et interurbains du Grand Est",
             "slug": "agregat-grand-est", "datagouv_id": "g", "resources": [{"format": "GTFS"}],
             "covered_area": [{"type": "region", "insee": "53"}]},
            {"type": "public-transit", "title": "Programme des vols Air Corsica", "slug": "vols", "datagouv_id": "h",
             "resources": [{"format": "GTFS"}], "covered_area": [{"type": "region", "insee": "53"}]},
        ]
        response = mock.Mock()
        response.json.return_value = self.catalogue() + extra
        with mock.patch.object(self.B.requests, "get", return_value=response):
            found = self.B.discover_networks(self.codes())
        self.assertFalse({"agregat_grand_est", "vols"} & set(found))

    def test_zip_gtfs_garde_meme_si_la_description_cite_le_gtfs_rt(self):
        meta = {"resources": [
            {"format": "zip", "title": "GTFS - pour être utilisé avec le GTFS-RT", "url": "https://x/gtfs.zip",
             "last_modified": "2026-10-01"},
            {"format": "zip", "title": "Offre NeTEx", "url": "https://x/netex.zip", "last_modified": "2026-10-01"},
            {"format": "gtfs-rt", "title": "Positions", "url": "https://x/rt.pb"}]}
        response = mock.Mock()
        response.json.return_value = meta
        with mock.patch.object(self.B.requests, "get", return_value=response):
            self.assertEqual([u for _, u in self.B.resolve_gtfs_urls("x")], ["https://x/gtfs.zip"])

    def test_archive_sans_stops_txt_reconnue(self):
        import tempfile
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            netex, gtfs = Path(tmp) / "netex.zip", Path(tmp) / "gtfs.zip"
            with zipfile.ZipFile(netex, "w") as z:
                z.writestr("ligne.xml", "<x/>")
            with zipfile.ZipFile(gtfs, "w") as z:
                z.writestr("stops.txt", "stop_id\n")
            self.assertFalse(self.B.has_stops(netex))
            self.assertTrue(self.B.has_stops(gtfs))

    def test_meme_arret_dans_deux_fichiers_du_reseau_compte_une_fois(self):
        import pandas as pd
        stops = pd.DataFrame({"reseau": ["ametis", "ametis", "ametis", "autre"], "stop_id": ["1", "1", "2", "1"],
                              "lat": [49.9, 49.9, 49.8, 49.9], "lon": [2.3, 2.3, 2.2, 2.3],
                              "nb_passages_jour": [40, 55, 10, 7]})
        out = self.B.collapse_network_files(stops)
        self.assertEqual(len(out), 3)
        self.assertEqual(out.loc[(out.reseau == "ametis") & (out.stop_id == "1"), "nb_passages_jour"].item(), 55)
        self.assertEqual(out.loc[out.reseau == "autre", "nb_passages_jour"].item(), 7)

    def test_jeux_nationaux_seulement_sur_demande(self):
        self.assertIn("sncf_ter", self.run_discovery(with_national=True))
        self.assertNotIn("sncf_ter", self.run_discovery())


class ChaineFranceTest(unittest.TestCase):
    """run_all --territoire france : sources nationales, puis réseau piéton des régions, sans modèle."""

    def test_etapes_france(self):
        import run_all
        with mock.patch.object(run_all.subprocess, "run") as run:
            run_all.main(["--territoire", "france"])
        cmds = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(call.kwargs["env"]["TERRITOIRE"] == "france" for call in run.call_args_list))
        self.assertIn("--france", cmds[0])
        self.assertIn("--tolerer-echecs", next(c for c in cmds if "scripts/build_arrets.py" in c))
        self.assertEqual(cmds[-1], ["bash", "scripts/run_regions_france.sh"])
        self.assertFalse(any("model/train.py" in c or "optim/optimise.py" in c for c in cmds))

    def test_etapes_ara_inchangees(self):
        import run_all
        with mock.patch.object(run_all.subprocess, "run") as run:
            run_all.main([])
        cmds = [call.args[0] for call in run.call_args_list]
        self.assertIn("data/silver/contours.parquet", next(c for c in cmds if "scripts/build_arrets.py" in c))
        self.assertTrue(any("optim/optimise.py" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
