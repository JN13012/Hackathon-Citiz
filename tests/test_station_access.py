"""Accès alternatifs : minimum de temps, station unique, ancrage et absence de raccourci."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely import LineString, Point, box

from geo.build_accessibilite import PairNetwork, aggregate
from geo.build_pilote_lyon import select_pilot, validate_pairs
from geo.station_access import load_accesses, route_with_accesses, snap_access
from geo.check_raccordements_ara import sha256


def fixture(coincident=False):
    graph = nx.MultiDiGraph(crs="EPSG:2154", network_type="walk", simplified=True)
    coords = {1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (500, 0), 5: (0, 0 if coincident else 20), 6: (100, 0 if coincident else 20)}
    for n, (x, y) in coords.items():
        graph.add_node(n, x=x, y=y)
    for u, v in [(1, 2), (2, 3), (3, 4), (5, 6)]:
        line = LineString([coords[u], coords[v]])
        for a, b, geom in [(u, v, line), (v, u, LineString(list(line.coords)[::-1]))]:
            graph.add_edge(a, b, key=0, geometry=geom, longueur_m=line.length, temps_pieton_s=line.length / 1.25)
    net = PairNetwork(graph, 4.5)
    pop = gpd.GeoDataFrame({"id_carreau": ["p"], "population": [100.], "dans_pilote": [True]}, geometry=[Point(0, 0)], crs=2154)
    offers = gpd.GeoDataFrame({"offre_id": ["m"], "type": ["metro"], "source": ["arrets"], "nom": ["Station test"],
                              "unite": ["passages_par_jour"], "valeur": [10.], "dans_pilote": [True]}, geometry=[Point(100, 100)], crs=2154)
    accesses = gpd.GeoDataFrame({"offre_id": ["m", "m"], "acces_id": ["a", "b"], "noeud_osm": [2, 3]},
                               geometry=[Point(100, 0), Point(200, 0)], crs=2154)
    return net, pop, offers, accesses


class AccessTests(unittest.TestCase):
    def test_two_reachable_accesses_count_station_population_and_frequency_once(self):
        net, pop, offers, accesses = fixture()
        original = offers.copy()
        pairs, unknown, pq, oq, aq = route_with_accesses(net, pop, offers, accesses)
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs.offre_id.iloc[0], pairs.acces_id.iloc[0], pairs.temps_pieton_s.iloc[0]), ("m", "a", 80))
        self.assertEqual(len(aq), 2)
        self.assertTrue(unknown.empty)
        self.assertTrue(oq.via_acces_station.iloc[0])
        self.assertTrue(pd.isna(oq.raccord_cle.iloc[0]))
        cells = gpd.GeoDataFrame({"id_carreau": ["p"], "population": [100.]}, geometry=[box(-10, -10, 10, 10)], crs=2154)
        o1, o2, _, _, _ = aggregate(pop, offers, cells, pairs, unknown, pq, oq, 20)
        self.assertEqual(o1.options_metro_par_habitant.iloc[0], 1)
        self.assertEqual(o1.passages_jour_metro_par_habitant.iloc[0], 10)
        self.assertEqual(o2.population_desservie.iloc[0], 100)
        self.assertTrue(offers.equals(original))
        validation = validate_pairs(net, pop, offers, pairs, unknown, accesses=accesses)
        self.assertEqual(validation["temoins_reference"][0]["nb_points_arrivee"], 2)
        self.assertEqual(validation["temoins_reference"][0]["reference_s"], 80)

    def test_one_access_suffices_and_input_order_does_not_change_winner(self):
        net, pop, offers, accesses = fixture()
        pop.geometry = [Point(500, 0)]
        accesses.noeud_osm = [1, 4]
        accesses.geometry = [Point(0, 0), Point(500, 0)]
        a = route_with_accesses(net, pop, offers, accesses)[0]
        b = route_with_accesses(net, pop, offers, accesses.iloc[::-1])[0]
        self.assertEqual(a.to_dict("records"), b.to_dict("records"))
        self.assertEqual(a.acces_id.iloc[0], "b")
        self.assertEqual(a.temps_pieton_s.iloc[0], 0)

    def test_station_does_not_connect_disconnected_streets(self):
        net, pop, offers, accesses = fixture()
        bus = offers.copy()
        bus.offre_id = "bus"
        bus["type"] = "bus"
        bus.geometry = [Point(100, 20)]
        offers = gpd.GeoDataFrame(pd.concat([offers, bus], ignore_index=True), crs=2154)
        accesses.noeud_osm = [2, 6]
        accesses.geometry = [Point(100, 0), Point(100, 20)]
        before = set(net.graph), set(net.graph.edges(keys=True))
        pairs, _, _, _, _ = route_with_accesses(net, pop, offers, accesses)
        self.assertEqual(list(pairs.offre_id), ["m"])
        self.assertEqual(before, (set(net.graph), set(net.graph.edges(keys=True))))
        self.assertFalse(nx.has_path(net.graph, 1, 6))

    def test_declared_node_wins_over_coincident_disconnected_geometry(self):
        net, pop, offers, accesses = fixture(coincident=True)
        access = accesses.iloc[:1].copy()
        access.noeud_osm = [5]
        access.geometry = [Point(0, 0)]
        self.assertEqual(net.keys[snap_access(net, access.iloc[0]).index][:2], (5, 6))
        pairs, unknown, _, _, _ = route_with_accesses(net, pop, offers, access)
        self.assertTrue(pairs.empty)
        self.assertTrue(unknown.empty)  # impossible in this graph, not a snap failure

    def test_bad_ordinary_offer_stays_unknown_and_missing_access_node_blocks(self):
        net, pop, offers, accesses = fixture()
        bus = offers.copy()
        bus.offre_id = "bad"
        bus["type"] = "bus"
        bus.geometry = [Point(50, -60)]
        mixed = gpd.GeoDataFrame(pd.concat([offers, bus], ignore_index=True), crs=2154)
        pairs, unknown, _, oq, _ = route_with_accesses(net, pop, mixed, accesses)
        self.assertEqual(list(pairs.offre_id), ["m"])
        self.assertEqual(unknown.to_dict("records"), [{"id_carreau": "p", "offre_id": "bad"}])
        self.assertEqual(oq.loc[oq.offre_id.eq("bad"), "statut"].iloc[0], "raccordement_trop_loin")
        accesses.loc[0, "noeud_osm"] = 999
        with self.assertRaisesRegex(ValueError, "absent du graphe"):
            route_with_accesses(net, pop, offers, accesses)

    def test_selection_includes_neighbors_of_access_outside_city(self):
        _, pop, offers, accesses = fixture()
        boundary = box(0, 0, 100, 100)
        offers.geometry = [Point(90, 50)]
        accesses = accesses.iloc[:1].copy()
        accesses.geometry = [Point(150, 50)]
        pop.geometry = [Point(500, 50)]  # 400 m from city, 350 m from entry
        selected_pop, selected_offer, study = select_pilot(pop, offers, boundary, accesses, 375)
        self.assertEqual(len(selected_pop), 1)
        self.assertFalse(selected_pop.dans_pilote.iloc[0])
        self.assertTrue(selected_offer.dans_pilote.iloc[0])
        self.assertTrue(study.covers(accesses.geometry.iloc[0]))

    def test_config_preserves_gtfs_position_and_rejects_missing_or_duplicate_nodes(self):
        _, _, offers, _ = fixture()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tile = root / "x0_y0"
            tile.mkdir()
            nodes = gpd.GeoDataFrame({"noeud_id": [2]}, geometry=[Point(100, 0)], crs=2154)
            nodes.to_parquet(tile / "reseau_pieton_noeuds.parquet")
            qp = tile / "reseau_pieton_qc.json"
            qp.write_text(json.dumps({"signature": {"version": 2}, "fichiers": {"reseau_pieton_noeuds.parquet": sha256(tile / "reseau_pieton_noeuds.parquet")}}))
            manifest = {"signature": {"version": 2}, "tuiles": {"x0_y0": {"qc_sha256": sha256(qp)}}}
            entry = {"offre_id": "m", "nom_station": "Station test", "acces_id": "a", "noeud_osm": 2,
                     "tourniquet_osm": 99, "tuile_id": "x0_y0", "ecart_station_max_m": 150}
            config = {"version": 1, "hypothese": "MVP accepté", "acces": [entry]}
            path = root / "config.json"
            path.write_text(json.dumps(config))
            access = load_accesses(path, offers, root, manifest)
            self.assertEqual((access.station_source_x.iloc[0], access.station_source_y.iloc[0]), (100, 100))
            self.assertEqual(access.geometry.iloc[0], Point(100, 0))
            config["acces"].append(entry.copy())
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "dupliqué"):
                load_accesses(path, offers, root, manifest)


if __name__ == "__main__":
    unittest.main()
