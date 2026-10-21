"""Preuves de provenance et diagnostics sans inférence de desserte réelle."""

from types import SimpleNamespace
import hashlib
import unittest

import geopandas as gpd
import pandas as pd
from shapely import Point

from geo.audit_raccordements_offre import source_cluster, classify


class AuditTests(unittest.TestCase):
    def test_cluster_id_excludes_distant_homonyms_and_parent_stations(self):
        stops = pd.DataFrame({"stop_id": ["a", "b", "far", "parent"],
                              "stop_name": ["École", "ECOLE", "Ecole", "Ecole"],
                              "stop_lon": ["5", "5.0001", "5.1", "5"],
                              "stop_lat": ["45"] * 4, "location_type": ["0", "0", "0", "1"]})
        points = gpd.GeoSeries(gpd.points_from_xy([5, 5.0001], [45, 45]), crs=4326).to_crs(2154)
        x, y = points.x.mean(), points.y.mean()
        identifier = hashlib.blake2s(f"ecole|{round(x)}|{round(y)}".encode(), digest_size=5).hexdigest()
        offer = SimpleNamespace(nom="Ecole", offre_id="arret_" + identifier, geometry=Point(x, y))
        self.assertEqual(set(source_cluster(stops, offer).stop_id), {"a", "b"})
        offer.offre_id = "arret_inconnu"
        with self.assertRaisesRegex(ValueError, "non reproduite"):
            source_cluster(stops, offer)

    def test_unused_stop_is_not_reclassified_as_ferry_or_bus(self):
        cause, owner, action = classify("arrets", 0, {"man_made": "pier"}, 1)
        self.assertEqual(cause, "sans_horaire_dans_gtfs")
        self.assertEqual(owner, "role_1_transport")

    def test_private_access_stays_restricted_and_explicit_foot_permission_wins(self):
        self.assertEqual(classify("arrets", 10, {"highway": "service", "access": "private"}, 20)[0], "acces_proche_restreint")
        self.assertEqual(classify("arrets", 10, {"access": "private", "foot": "yes"}, 20)[0], "ecart_source_reseau_a_confirmer")

    def test_actual_gold_station_source_is_assigned_to_role_four(self):
        cause, owner, _ = classify("stations_partagees", None, {"highway": "residential"}, 59)
        self.assertEqual(cause, "ecart_source_reseau_a_confirmer")
        self.assertEqual(owner, "role_4_stations_et_geo")


if __name__ == "__main__":
    unittest.main()
