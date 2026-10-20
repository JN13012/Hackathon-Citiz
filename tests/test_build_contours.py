"""Contrôles hors réseau sur de petites limites administratives synthétiques."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import warnings

import geopandas as gpd
from shapely.geometry import Point, Polygon, box

from geo.build_contours import build_contours, parser


SCRIPT = Path(__file__).resolve().parents[1] / "geo" / "build_contours.py"


class BuildContoursTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "silver"
        self.communes = gpd.GeoDataFrame(
            {
                "INSEE_COM": ["38185", "38421", "01001", "31555"],
                "NOM": ["Grenoble", "Saint-Martin", "Commune Ain", "Toulouse"],
                "INSEE_REG": ["84", "84", "84", "76"],
            },
            geometry=[
                box(900000, 6400000, 900100, 6400100),
                box(900100, 6400000, 900200, 6400100),
                box(910000, 6410000, 910100, 6410100),
                box(570000, 6270000, 570100, 6270100),
            ],
            crs=2154,
        )
        self.commune_path = self.root / "communes.parquet"
        self.communes.to_parquet(self.commune_path, index=False)

    def args(self, *extra):
        return parser().parse_args([
            "--communes", str(self.commune_path),
            "--output-dir", str(self.output), *extra,
        ])

    def test_default_region_preserves_codes_and_reports_missing_levels(self):
        report = build_contours(self.args())
        result = gpd.read_parquet(self.output / "contours.parquet")
        territory = gpd.read_parquet(self.output / "territoire.parquet")
        self.assertEqual(set(result.code_insee), {"38185", "38421", "01001"})
        self.assertEqual(result.crs.to_epsg(), 2154)
        self.assertEqual(territory.crs.to_epsg(), 2154)
        self.assertEqual(len(territory), 1)
        self.assertAlmostEqual(territory.geometry.area.iloc[0], 30000)
        self.assertEqual(report["niveaux_non_fournis"], ["iris", "epci"])
        self.assertIsNone(report["communes_sans_iris"])
        self.assertEqual(json.loads((self.output / "contours_qc.json").read_text())["effectifs"], {"commune": 3})

    def test_pilot_iris_reprojection_and_clipping(self):
        iris = gpd.GeoDataFrame(
            {
                "CODE_IRIS": ["381850001", "381850002", "315550001"],
                "INSEE_COM": ["38185", "38185", "31555"],
                "NOM_IRIS": ["Nord", "Sud", "Autre région"],
            },
            geometry=[
                box(899990, 6400000, 900050, 6400100),
                box(900050, 6400000, 900100, 6400100),
                self.communes.geometry.iloc[3],
            ],
            crs=2154,
        ).to_crs(4326)
        path = self.root / "iris.geojson"
        iris.to_file(path, driver="GeoJSON")
        report = build_contours(self.args("--codes-communes", "38185", "--iris", str(path)))
        result = gpd.read_parquet(self.output / "contours.parquet")
        self.assertEqual(report["effectifs"], {"iris": 2, "commune": 1})
        self.assertEqual(set(result.code_insee), {"38185"})
        self.assertEqual(set(result.code_iris.dropna()), {"381850001", "381850002"})
        self.assertGreaterEqual(report["geometries_redecoupees_au_territoire"]["iris"], 1)
        self.assertEqual(report["communes_sans_iris"], [])
        territory = gpd.read_parquet(self.output / "territoire.parquet").geometry.iloc[0]
        self.assertTrue(result.geometry.covered_by(territory).all())

    def test_epci_id_is_distinct_from_commune_and_boundary_touches_are_dropped(self):
        epci = gpd.GeoDataFrame(
            {"CODE_SIREN": ["200000001", "200000002"], "NOM": ["Agglo", "Voisin"]},
            geometry=[
                box(899900, 6400000, 900050, 6400100),
                box(900100, 6400000, 900200, 6400100),
            ],
            crs=2154,
        )
        path = self.root / "epci.parquet"
        epci.to_parquet(path, index=False)
        build_contours(self.args("--codes-communes", "38185", "--epci", str(path)))
        result = gpd.read_parquet(self.output / "contours.parquet")
        epci_result = result[result.niveau == "epci"]
        self.assertEqual(epci_result.code_epci.tolist(), ["200000001"])
        self.assertTrue(epci_result.code_insee.isna().all())
        self.assertAlmostEqual(epci_result.geometry.area.sum(), 5000)

    def test_multilayer_geopackage_requires_explicit_layer(self):
        path = self.root / "admin.gpkg"
        self.communes.to_file(path, layer="COMMUNE", driver="GPKG")
        self.communes.to_file(path, layer="AUTRE", driver="GPKG")
        self.commune_path = path
        with self.assertRaisesRegex(ValueError, "Préciser la couche"):
            build_contours(self.args())
        report = build_contours(self.args("--communes-layer", "COMMUNE"))
        self.assertEqual(report["effectifs"], {"commune": 3})
        self.assertEqual(report["sources"]["communes"]["couche"], "COMMUNE")

    def test_shapefile_lowercase_columns_and_reprojection(self):
        path = self.root / "COMMUNE.shp"
        self.communes.rename(columns={
            "INSEE_COM": "insee_com", "INSEE_REG": "insee_reg", "NOM": "nom",
        }).to_crs(4326).to_file(path, driver="ESRI Shapefile")
        self.commune_path = path
        build_contours(self.args("--codes-communes", "01001"))
        result = gpd.read_parquet(self.output / "contours.parquet")
        self.assertEqual(result.code_insee.tolist(), ["01001"])
        self.assertEqual(result.crs.to_epsg(), 2154)
        self.assertAlmostEqual(result.geometry.area.iloc[0], 10000, delta=0.1)

    def test_admin_express_v4_field_names(self):
        path = self.root / "admin-v4.gpkg"
        self.communes.rename(columns={
            "INSEE_COM": "code_insee", "INSEE_REG": "code_insee_de_la_region", "NOM": "nom_officiel",
        }).to_file(path, layer="commune", driver="GPKG")
        self.commune_path = path
        epci_path = self.root / "epci-v4.gpkg"
        gpd.GeoDataFrame(
            {"code_siren": ["200000001"], "nom_officiel": ["Agglo"]},
            geometry=[self.communes.geometry.iloc[0]], crs=2154,
        ).to_file(epci_path, layer="epci", driver="GPKG")
        report = build_contours(self.args("--region", "84", "--epci", str(epci_path)))
        self.assertEqual(report["effectifs"], {"commune": 3, "epci": 1})

    def test_iris_with_conflicting_parent_is_rejected(self):
        iris = gpd.GeoDataFrame(
            {"CODE_IRIS": ["381850001"], "INSEE_COM": ["31555"], "NOM_IRIS": ["Quartier"]},
            geometry=[self.communes.geometry.iloc[0]], crs=2154,
        )
        path = self.root / "iris.parquet"
        iris.to_parquet(path, index=False)
        with self.assertRaisesRegex(ValueError, "préfixe"):
            build_contours(self.args("--codes-communes", "38185", "--iris", str(path)))

    def test_lyon_iris_use_official_arrondissement_mapping(self):
        admin_path = self.root / "admin-lyon.gpkg"
        gpd.GeoDataFrame(
            {"code_insee": ["69123"], "nom_officiel": ["Lyon"], "code_insee_de_la_region": ["84"]},
            geometry=[box(840000, 6510000, 840200, 6510100)], crs=2154,
        ).to_file(admin_path, layer="commune", driver="GPKG")
        gpd.GeoDataFrame(
            {"code_insee": ["69381", "69382"], "code_insee_de_la_commune_de_rattach": ["69123", "69123"]},
            geometry=[box(840000, 6510000, 840100, 6510100), box(840100, 6510000, 840200, 6510100)], crs=2154,
        ).to_file(admin_path, layer="arrondissement_municipal", driver="GPKG")
        iris_path = self.root / "lyon-iris.parquet"
        gpd.GeoDataFrame(
            {"code_insee": ["69381", "69382"], "code_iris": ["693810101", "693820101"], "nom_iris": ["Quartier 1", "Quartier 2"]},
            geometry=[box(840000, 6510000, 840100, 6510100), box(840100, 6510000, 840200, 6510100)], crs=2154,
        ).to_parquet(iris_path, index=False)
        self.commune_path = admin_path
        report = build_contours(self.args("--communes-layer", "commune", "--iris", str(iris_path)))
        result = gpd.read_parquet(self.output / "contours.parquet")
        iris = result[result.niveau == "iris"]
        self.assertEqual(iris.code_insee.tolist(), ["69123", "69123"])
        self.assertEqual(iris.code_iris.tolist(), ["693810101", "693820101"])
        self.assertEqual(iris.code_arrondissement_municipal.tolist(), ["69381", "69382"])
        self.assertEqual(report["communes_sans_iris"], [])
        self.assertEqual(report["rattachements_arrondissements_municipaux"], {"69381": "69123", "69382": "69123"})

    def test_unknown_requested_commune_fails_without_outputs(self):
        with self.assertRaisesRegex(ValueError, "absentes.*99999"):
            build_contours(self.args("--codes-communes", "38185", "99999"))
        self.assertFalse(self.output.exists())

    def test_missing_region_field_requires_explicit_communes(self):
        self.communes.drop(columns="INSEE_REG").to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "INSEE_REG"):
            build_contours(self.args())
        report = build_contours(self.args("--codes-communes", "38185"))
        self.assertEqual(report["effectifs"], {"commune": 1})

    def test_duplicate_identifiers_are_rejected(self):
        self.communes.loc[1, "INSEE_COM"] = "38185"
        self.communes.to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "en double"):
            build_contours(self.args())

    def test_invalid_polygon_is_rejected_without_repair(self):
        self.communes.loc[0, "geometry"] = Polygon([
            (900000, 6400000), (900100, 6400100),
            (900000, 6400100), (900100, 6400000), (900000, 6400000),
        ])
        self.communes.to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "invalide"):
            build_contours(self.args())
        self.assertFalse(self.output.exists())

    def test_validation_failure_preserves_previous_outputs(self):
        build_contours(self.args())
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.communes.loc[0, "geometry"] = Point(900000, 6400000)
        self.communes.to_parquet(self.commune_path, index=False)
        with self.assertRaises(ValueError):
            build_contours(self.args())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_missing_crs_is_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.communes.set_crs(None, allow_override=True).to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "Projection source absente"):
            build_contours(self.args())

    def test_incomplete_code_is_not_zero_padded(self):
        self.communes.loc[2, "INSEE_COM"] = "1001"
        self.communes.to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "Codes commune invalides"):
            build_contours(self.args())

    def test_point_is_not_accepted_as_boundary(self):
        self.communes.loc[0, "geometry"] = Point(900000, 6400000)
        self.communes.to_parquet(self.commune_path, index=False)
        with self.assertRaisesRegex(ValueError, "non polygonale"):
            build_contours(self.args())

    def test_cli_reports_missing_file_without_traceback(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--communes", str(self.root / "absent.shp")],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Fichier absent", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
