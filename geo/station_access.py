"""Accès terminaux des stations : union des accès et minimum des temps connus.

Les coordonnées de l'offre logique restent celles de la source. Aucune arête
n'est ajoutée entre les accès : la station ne devient jamais un raccourci.
"""

import json
import math

import geopandas as gpd
import pandas as pd
from shapely import Point

try:
    from geo.build_accessibilite import Snap, route_pairs
    from geo.check_raccordements_ara import sha256, verify_files
except ModuleNotFoundError:
    from build_accessibilite import Snap, route_pairs
    from check_raccordements_ara import sha256, verify_files


def empty_accesses():
    return gpd.GeoDataFrame({"acces_id": pd.Series(dtype="string"), "offre_id": pd.Series(dtype="string"),
                             "noeud_osm": pd.Series(dtype="int64")}, geometry=[], crs=2154)


def load_accesses(path, offers, network_dir, manifest):
    if path is None:
        return empty_accesses()
    config = json.loads(path.read_text())
    if config.get("version") != 1:
        raise ValueError("Version inconnue du fichier d'accès")
    indexed = offers.set_index("offre_id")
    rows, cache = [], {}
    for entry in config["acces"]:
        identifier = entry["offre_id"]
        if identifier not in indexed.index or indexed.loc[identifier, "type"] != "metro":
            raise ValueError("Station métro de l'accès absente de l'offre")
        if indexed.loc[identifier, "nom"] != entry["nom_station"]:
            raise ValueError("Nom de station différent de la configuration d'accès")
        tile = entry["tuile_id"]
        if tile not in cache:
            directory = network_dir / tile
            qp = directory / "reseau_pieton_qc.json"
            if sha256(qp) != manifest["tuiles"][tile]["qc_sha256"]:
                raise ValueError("Reçu du réseau d'accès incompatible")
            qc = json.loads(qp.read_text())
            if qc["signature"] != manifest["signature"]:
                raise ValueError("Version du réseau d'accès incompatible")
            verify_files(directory, qc["fichiers"])
            cache[tile] = gpd.read_parquet(directory / "reseau_pieton_noeuds.parquet").set_index("noeud_id")
        nodes = cache[tile]
        if entry["noeud_osm"] not in nodes.index:
            raise ValueError("Nœud d'accès absent du réseau public retenu")
        if entry["tourniquet_osm"] in nodes.index:
            raise ValueError("Le réseau d'accès ne correspond plus au filtre des tourniquets")
        point = nodes.loc[entry["noeud_osm"]].geometry
        source = indexed.loc[identifier].geometry
        d = point.distance(source)
        if not math.isfinite(d) or d > entry["ecart_station_max_m"]:
            raise ValueError("Écart entre station et accès supérieur à la limite explicite")
        rows.append({**entry, "station_source_x": source.x, "station_source_y": source.y,
                     "distance_station_source_m": d, "hypothese": config["hypothese"], "geometry": point})
    if not rows:
        return empty_accesses()
    result = gpd.GeoDataFrame(rows, crs=2154)
    if result.acces_id.duplicated().any() or result.duplicated(["offre_id", "noeud_osm"]).any():
        raise ValueError("Accès dupliqué")
    if set(result.acces_id) & set(offers.offre_id):
        raise ValueError("Identifiant d'accès identique à une offre logique")
    return result.sort_values("acces_id").reset_index(drop=True)


def snap_access(network, access):
    """Ancrage sur l'identifiant OSM, jamais sur une voie superposée déconnectée."""
    node = int(access.noeud_osm)
    if node not in network.graph:
        raise ValueError(f"Nœud d'accès absent du graphe pilote : {node}")
    data = network.graph.nodes[node]
    if Point(data["x"], data["y"]).distance(access.geometry) > .001:
        raise ValueError("Coordonnées d'accès différentes de celles du nœud OSM")
    choices = [i for i, key in enumerate(network.keys) if node in key[:2]]
    if not choices:
        raise ValueError("Aucun segment incident au nœud d'accès")
    index = min(choices, key=lambda i: network.keys[i])
    position = 0. if network.keys[index][0] == node else network.lines[index].length
    return Snap(index, position, 0., "ok")


def route_with_accesses(network, population, offers, accesses, budget_s=300, pop_snap_m=150, offer_snap_m=50):
    if accesses.empty:
        pairs, unknown, pq, oq = route_pairs(network, population, offers, budget_s, pop_snap_m, offer_snap_m)
        return pairs, unknown, pq, oq, empty_accesses()
    if not offers.offre_id.is_unique or not accesses.acces_id.is_unique or not set(accesses.offre_id).issubset(set(offers.offre_id)):
        raise ValueError("Identifiants d'offres ou d'accès incompatibles")
    overridden = set(accesses.offre_id)
    endpoint_rows, snaps = [], []
    for offer in offers.itertuples(index=False):
        alternatives = accesses.loc[accesses.offre_id.eq(offer.offre_id)]
        if alternatives.empty:
            endpoint_rows.append({"offre_id": offer.offre_id, "offre_logique_id": offer.offre_id, "acces_id": None,
                                  "dans_pilote": offer.dans_pilote, "geometry": offer.geometry})
            snaps.append(network.snap(offer.geometry, budget_s, offer_snap_m))
        else:
            for access in alternatives.sort_values("acces_id").itertuples(index=False):
                endpoint_rows.append({"offre_id": access.acces_id, "offre_logique_id": offer.offre_id, "acces_id": access.acces_id,
                                      "dans_pilote": offer.dans_pilote, "geometry": access.geometry})
                snaps.append(snap_access(network, access))
    endpoints = gpd.GeoDataFrame(endpoint_rows, crs=offers.crs)
    raw, pending, pq, endpoint_qc = route_pairs(network, population, endpoints, budget_s, pop_snap_m, offer_snap_m, offer_snaps=snaps)
    mapping = endpoints[["offre_id", "offre_logique_id", "acces_id"]]
    known = raw.merge(mapping, on="offre_id", validate="many_to_one")
    known = known.sort_values(["id_carreau", "offre_logique_id", "temps_pieton_s", "offre_id"])
    known = known.drop_duplicates(["id_carreau", "offre_logique_id"])
    pairs = known[["id_carreau", "offre_logique_id", "temps_pieton_s", "acces_id"]].rename(columns={"offre_logique_id": "offre_id"}).reset_index(drop=True)
    uncertain = pending.merge(mapping, on="offre_id", validate="many_to_one")
    uncertain = uncertain[["id_carreau", "offre_logique_id"]].rename(columns={"offre_logique_id": "offre_id"}).drop_duplicates()
    # Un accès atteint suffit à prouver la desserte, même si un autre accès
    # était indéterminé. Sinon une incertitude doit rester explicite.
    uncertain = uncertain.merge(pairs[["id_carreau", "offre_id"]].assign(atteignable=True), on=["id_carreau", "offre_id"], how="left")
    unknown = uncertain.loc[uncertain.atteignable.isna(), ["id_carreau", "offre_id"]].reset_index(drop=True)
    detail = endpoint_qc.merge(mapping, on="offre_id", validate="one_to_one").rename(columns={"offre_id": "point_calcul_id", "offre_logique_id": "offre_id"})
    diagnostics = []
    for offer in offers.itertuples(index=False):
        group = detail.loc[detail.offre_id.eq(offer.offre_id)]
        if offer.offre_id not in overridden:
            row = group.iloc[0].drop(labels=["point_calcul_id", "acces_id"]).to_dict()
            row.update(via_acces_station=False, nb_acces=0, nb_acces_calculables=0)
        else:
            # Aucun raccord unique du point GTFS : les deux vrais diagnostics
            # sont conservés dans la table d'accès. Ne pas inventer un raccord.
            ok = int(group.statut.eq("ok").sum())
            row = {"offre_id": offer.offre_id, "dans_pilote": offer.dans_pilote,
                   "statut": "ok" if ok else "raccordement_trop_loin", "geometry": offer.geometry,
                   "distance_raccordement_m": None, "temps_acces_s": None, "raccord_de": None,
                   "raccord_vers": None, "raccord_cle": None, "abscisse_raccord_m": None,
                   "via_acces_station": True, "nb_acces": len(group), "nb_acces_calculables": ok}
        diagnostics.append(row)
    oq = gpd.GeoDataFrame(diagnostics, crs=offers.crs)
    for name in ["raccord_de", "raccord_vers", "raccord_cle"]:
        oq[name] = pd.array(oq[name], dtype="Int64")
    access_qc = gpd.GeoDataFrame(detail.loc[detail.acces_id.notna()].copy(), crs=offers.crs)
    return pairs, unknown, pq, oq, access_qc
