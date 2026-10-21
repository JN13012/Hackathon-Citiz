"""Pilote de trajets et O1/O2 à Lyon depuis les entrées ARA figées.

Réutilise le moteur éprouvé du pilote Grenoble. Les tuiles sont réunies par
identifiants OSM, sans additionner leurs copies dans les marges. Aucun appel
réseau, aucune modification des entrées ou des livraisons antérieures.
"""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import union_all

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.build_accessibilite import PairNetwork, aggregate, route_pairs
from geo.check_raccordements_ara import sha256, verify_files
from geo.station_access import load_accesses, route_with_accesses, snap_access


def select_pilot(candidates, offers, boundary, accesses, radius):
    """Les accès peuvent traverser une limite communale sans changer la station logique."""
    inside_ids = set(offers.loc[offers.geometry.covered_by(boundary), "offre_id"])
    pilot_accesses = accesses.loc[accesses.offre_id.isin(inside_ids)]
    study = union_all([boundary, *pilot_accesses.geometry])
    population = candidates.loc[candidates.geometry.distance(study).le(radius + 1e-6)].copy().reset_index(drop=True)
    population["dans_pilote"] = population.geometry.covered_by(boundary)
    close_access_ids = set(accesses.loc[accesses.geometry.distance(boundary).le(radius + 1e-6), "offre_id"])
    offers = offers.loc[offers.geometry.distance(boundary).le(radius + 1e-6) | offers.offre_id.isin(close_access_ids)].copy().reset_index(drop=True)
    offers["dans_pilote"] = offers.geometry.covered_by(boundary)
    return population, offers, study


def merge_network(nodes, edges, window):
    """Fusion des marges : refuse les copies contradictoires, conserve les voies parallèles."""
    ns = gpd.GeoDataFrame(pd.concat(nodes, ignore_index=True), crs=2154)
    es = gpd.GeoDataFrame(pd.concat(edges, ignore_index=True), crs=2154)
    copies = ns.loc[ns.noeud_id.duplicated(keep=False)]
    for _, group in copies.groupby("noeud_id"):
        if group.geometry.nunique() != 1:
            raise ValueError("Coordonnées contradictoires pour un nœud OSM partagé")
    # Les composantes des tuiles n'ont pas des identifiants régionaux communs.
    ns = ns.drop_duplicates("noeud_id").drop(columns=["composante_id", "dans_tuile", "dans_zone_marge"], errors="ignore")
    copy_edges = es.loc[es.duplicated(["de", "vers", "cle"], keep=False)]
    fields = ["geometry", "longueur_m", "temps_pieton_s", "osmid", "segment_osm", "direction_osm"]
    if not copy_edges.empty:
        conflicts = copy_edges.groupby(["de", "vers", "cle"])[fields].nunique(dropna=False).gt(1)
        if conflicts.any().any():
            raise ValueError("Copies contradictoires d'un arc OSM entre tuiles")
    stats = {"copies_noeuds_avant_fusion": sum(len(n) for n in nodes),
             "copies_arcs_avant_fusion": len(es), "arcs_distincts_avant_decoupage": len(es.drop_duplicates(["de", "vers", "cle"]))}
    es = es.drop_duplicates(["de", "vers", "cle"])
    # Pas de découpe des géométries ni de changement des poids : garder tout
    # segment intersectant la fenêtre. Les chemins utiles ne peuvent en sortir.
    es = es.loc[es.intersects(window)].copy()
    refs = set(es.de) | set(es.vers)
    ns = ns.loc[ns.noeud_id.isin(refs)].copy()
    if set(ns.noeud_id) != refs or es.empty:
        raise ValueError("Références de réseau absentes ou réseau vide")
    ns = ns.set_index("noeud_id")
    ns["x"], ns["y"] = ns.geometry.x, ns.geometry.y
    es = es.set_index(["de", "vers", "cle"])
    graph = ox.graph_from_gdfs(ns, es, graph_attrs={"crs": "EPSG:2154", "network_type": "walk",
                                                  "simplified": False, "modele_marche": "bidirectionnel"})
    stats.update(noeuds=len(graph), arcs=graph.number_of_edges())
    return graph, stats


def validate_pairs(network, population, offers, pairs, unknown, samples=24, accesses=None):
    """Recalcule des trajets témoins par Dijkstra indépendant avec deux points virtuels.

    Le moteur de référence découpe le segment de départ et celui d'arrivée :
    il ne réutilise ni node_times ni travel_time pour la valeur de contrôle.
    """
    if pairs.duplicated(["id_carreau", "offre_id"]).any() or unknown.duplicated(["id_carreau", "offre_id"]).any():
        raise ValueError("Paires comptées plusieurs fois")
    if set(map(tuple, pairs[["id_carreau", "offre_id"]].to_numpy())) & set(map(tuple, unknown.to_numpy())):
        raise ValueError("Une paire est à la fois calculée et indéterminée")
    if not np.isfinite(pairs.temps_pieton_s).all() or not pairs.temps_pieton_s.between(0, 300 + 1e-6).all():
        raise ValueError("Temps de trajet hors budget")
    pop = population.set_index("id_carreau")
    off = offers.set_index("offre_id")
    access_index = accesses.set_index("acces_id") if accesses is not None and not accesses.empty else None
    target_points = []
    for row in pairs.itertuples(index=False):
        access_id = getattr(row, "acces_id", None)
        if pd.notna(access_id):
            if access_index is None or access_id not in access_index.index or access_index.loc[access_id, "offre_id"] != row.offre_id:
                raise ValueError("Accès retenu incompatible avec la station de la paire")
            target_points.append(access_index.loc[access_id].geometry)
        else:
            if access_index is not None and access_index.offre_id.eq(row.offre_id).any():
                raise ValueError("Station à accès multiples sans accès retenu")
            target_points.append(off.loc[row.offre_id].geometry)
    targets = gpd.GeoSeries(target_points, crs=population.crs)
    direct = pop.geometry.reindex(pairs.id_carreau).reset_index(drop=True).distance(targets) / network.speed_ms
    if (pairs.temps_pieton_s.to_numpy() + 1e-6 < direct.to_numpy()).any():
        raise ValueError("Trajet plus court que la borne à vol d'oiseau")
    by_type = pairs.merge(offers[["offre_id", "type"]], on="offre_id", validate="many_to_one")
    chosen = set()
    for _, group in by_type.groupby("type"):
        chosen.add(group.temps_pieton_s.idxmax())
        chosen.add(group.temps_pieton_s.idxmin())
    chosen.update(pairs.temps_pieton_s.nlargest(samples // 2).index)
    chosen.update(pairs.temps_pieton_s.nsmallest(samples // 2).index)
    if "acces_id" in pairs:
        chosen.update(pairs.index[pairs.acces_id.notna()])
    evidence = []
    for i in sorted(chosen):
        row = pairs.loc[i]
        a = network.snap(pop.loc[row.id_carreau].geometry, 300, 150)
        alternatives = accesses.loc[accesses.offre_id.eq(row.offre_id)] if access_index is not None else None
        if alternatives is not None and not alternatives.empty:
            bs = [snap_access(network, access) for access in alternatives.itertuples(index=False)]
        else:
            bs = [network.snap(off.loc[row.offre_id].geometry, 300, 50)]
        if a.statut != "ok" or not any(b.statut == "ok" for b in bs):
            raise ValueError("Paire accessible avec raccordement non calculable")
        source, target = object(), object()
        before_nodes, before_edges = len(network.graph), network.graph.number_of_edges()
        try:
            u, v, _ = network.keys[a.index]
            access_a = a.distance / network.speed_ms
            network.graph.add_edge(source, u, temps_pieton_s=access_a + a.position / network.speed_ms)
            network.graph.add_edge(source, v, temps_pieton_s=access_a + (network.lines[a.index].length - a.position) / network.speed_ms)
            for b in bs:
                if b.statut != "ok":
                    continue
                x, y, _ = network.keys[b.index]
                access_b = b.distance / network.speed_ms
                # Le nœud cible est un puits : aucune traversée de station.
                network.graph.add_edge(x, target, temps_pieton_s=access_b + b.position / network.speed_ms)
                network.graph.add_edge(y, target, temps_pieton_s=access_b + (network.lines[b.index].length - b.position) / network.speed_ms)
                if {u, v} == {x, y} and network.lines[a.index].equals(network.lines[b.index]):
                    projected = network.lines[b.index].interpolate(b.position)
                    along = abs(a.position - network.lines[a.index].project(projected))
                    network.graph.add_edge(source, target, temps_pieton_s=access_a + along / network.speed_ms + access_b)
            reference = nx.shortest_path_length(network.graph, source, target, weight="temps_pieton_s")
        finally:
            network.graph.remove_nodes_from([source, target])
        if (len(network.graph), network.graph.number_of_edges()) != (before_nodes, before_edges):
            raise ValueError("Le contrôle a modifié le réseau")
        reverse_time = min(network.travel_time(b, a, network.node_times(b, 300)) for b in bs if b.statut == "ok")
        if not math.isclose(reference, row.temps_pieton_s, abs_tol=1e-6) or not math.isclose(reverse_time, row.temps_pieton_s, abs_tol=1e-6):
            raise ValueError("Temps témoin différent du Dijkstra de référence ou du trajet inverse")
        evidence.append({"id_carreau": row.id_carreau, "offre_id": row.offre_id,
                         "temps_pieton_s": float(row.temps_pieton_s), "reference_s": float(reference),
                         "retour_s": float(reverse_time), "acces_id_retenu": row.get("acces_id"),
                         "nb_points_arrivee": len(bs)})
    return {"etat": "valide", "paires_controlees": len(pairs), "temoins_reference": evidence,
            "controles": ["unicite_paires", "budget_300s", "borne_vol_oiseau", "dijkstra_deux_points_virtuels", "symetrie", "reseau_non_modifie"]}


def build(args):
    if args.output_dir.exists():
        raise ValueError("Refus d'écraser un pilote existant")
    started = time.monotonic()
    pp = args.preparation / "plan_reseau_ara.json"
    mp = args.reseau / "construction_reseau_ara.json"
    ip = args.reseau / "controle_independant.json"
    paths = [pp, mp, ip, Path(__file__), ROOT / "geo/build_accessibilite.py", ROOT / "geo/build_isochrones.py"]
    access_config = None if args.sans_acces_stations else args.acces_stations
    paths.append(ROOT / "geo/station_access.py")
    if access_config is not None:
        paths.append(access_config)
    source_hashes = {str(p): sha256(p) for p in paths}
    plan, manifest, independent = [json.loads(p.read_text()) for p in [pp, mp, ip]]
    if (manifest["etat"] != "regional_termine" or independent["etat"] != "valide"
            or independent["manifest_sha256"] != source_hashes[str(mp)]
            or manifest["signature"]["plan_sha256"] != source_hashes[str(pp)]):
        raise ValueError("Réseau, préparation et contrôle indépendant incompatibles")
    verify_files(args.preparation, plan["fichiers"])
    inputs = args.preparation / "entrees"
    contours = gpd.read_parquet(inputs / "contours.parquet")
    selected = contours.loc[contours.niveau.eq("commune") & contours.code_insee.eq("69123")]
    if len(selected) != 1:
        raise ValueError("Contour unique de Lyon absent")
    boundary = union_all(selected.geometry)
    speed = float(plan["parametres"]["vitesse_kmh"])
    if speed != 4.5 or plan["parametres"]["duree_s"] != 300:
        raise ValueError("Ce pilote est validé pour 300 s et 4,5 km/h")
    radius = 300 * speed / 3.6
    # Le tampon polygonal arrondit les angles ; 1 m supplémentaire garantit
    # ici une couverture conservatrice du rayon 750 m malgré l'approximation.
    candidates = gpd.read_parquet(inputs / "gold_candidates.parquet")
    offers = gpd.read_parquet(inputs / "gold_offre.parquet")
    accesses = load_accesses(access_config, offers, args.reseau, manifest)
    population, offers, study = select_pilot(candidates, offers, boundary, accesses, radius)
    accesses = accesses.loc[accesses.offre_id.isin(offers.offre_id)].copy()
    window = study.buffer(2 * radius + 1)
    cells = gpd.read_parquet(inputs / "carreaux_socio.parquet")
    cells = cells.loc[cells.id_carreau.isin(population.loc[population.dans_pilote, "id_carreau"]), ["id_carreau", "population", "geometry"]].copy()
    if cells.empty or offers.empty or not population.id_carreau.is_unique or not offers.offre_id.is_unique:
        raise ValueError("Entrées pilotes vides ou identifiants dupliqués")
    if not np.isfinite(population.population).all() or population.population.le(0).any():
        raise ValueError("Population invalide")
    required = {"bus", "metro", "velo", "citiz"}
    if not required.issubset(set(offers.loc[offers.dans_pilote, "type"])):
        raise ValueError("Modes requis manquants dans le pilote")
    if not population.geometry.covered_by(window).all() or population.geometry.distance(window.boundary).lt(radius).any():
        raise ValueError("Fenêtre calculatoire insuffisante pour un trajet complet")
    tiles = gpd.read_parquet(args.preparation / "tuiles.parquet")
    tile_ids = sorted(tiles.loc[tiles.intersects(window), "tuile_id"])
    ns, es, zones, receipts = [], [], [], {}
    print(f"Lyon : {len(cells)} centres pilote, {len(population)} avec voisins ; {int(offers.dans_pilote.sum())} offres pilote, {len(offers)} avec voisins.", flush=True)
    for tile in tile_ids:
        directory = args.reseau / tile
        qp = directory / "reseau_pieton_qc.json"
        h = sha256(qp)
        qc = json.loads(qp.read_text())
        if h != manifest["tuiles"][tile]["qc_sha256"] or qc["signature"] != manifest["signature"] or qc["etat"] != "valide":
            raise ValueError("Tuile incompatible")
        verify_files(directory, qc["fichiers"])
        ns.append(gpd.read_parquet(directory / "reseau_pieton_noeuds.parquet"))
        es.append(gpd.read_parquet(directory / "reseau_pieton_troncons.parquet"))
        zones.append(gpd.read_parquet(directory / "zone_requete.parquet").geometry.iloc[0])
        receipts[tile] = h
    if not union_all(zones).buffer(.001).covers(window):
        raise ValueError("Fenêtre réseau insuffisante pour les voisins et leurs trajets")
    print(f"Fusion de {len(tile_ids)} tuiles sans compter les copies des marges…", flush=True)
    graph, graph_stats = merge_network(ns, es, window)
    del ns, es
    print(f"Graphe pilote : {graph_stats['noeuds']} nœuds, {graph_stats['arcs']} arcs ; validation du moteur…", flush=True)
    network = PairNetwork(graph, speed)
    pairs, unknown, pq, oq, access_qc = route_with_accesses(network, population, offers, accesses, 300, 150, 50)
    validation = validate_pairs(network, population, offers, pairs, unknown, accesses=accesses)
    print("Trajets témoins validés ; agrégation O1/O2 du pilote…", flush=True)
    o1, o2, mapping, counts, fallbacks = aggregate(population, offers, cells, pairs, unknown, pq, oq, 20)
    access_counts = accesses.groupby("offre_id").size()
    o2["nb_acces_pietons"] = o2.offre_id.map(access_counts).fillna(0).astype(int)
    o2["mode_arrivee"] = np.where(o2.nb_acces_pietons.gt(0), "acces_exterieur_station", "coordonnees_source")
    if not math.isclose(float(o1.population.sum()), float(cells.population.sum()), abs_tol=1e-6):
        raise ValueError("Population perdue lors du regroupement O1")
    if not o2.offre_id.is_unique or set(o2.offre_id) != set(offers.loc[offers.dans_pilote, "offre_id"]):
        raise ValueError("Offres O2 perdues ou répétées")
    pilot_counts = counts.loc[counts.dans_pilote]
    pop_total = float(cells.population.sum())
    o1_complete = bool(pilot_counts.calcul_complet.all())
    linked = pairs.merge(population[["id_carreau", "dans_pilote"]], on="id_carreau", validate="many_to_one").merge(offers[["offre_id", "type", "dans_pilote"]], on="offre_id", suffixes=("_population", "_offre"), validate="many_to_one")
    if not required.issubset(set(linked.loc[linked.dans_pilote_offre, "type"])):
        raise ValueError("Un mode requis ne possède aucun trajet accessible témoin")
    report = {"execution_utc": datetime.now(timezone.utc).isoformat(), "territoire": "Lyon — commune 69123",
              "validation_technique": "valide", "etat_indicateurs": "complet" if o1.statut.eq("ok").all() and o2.statut.eq("ok").all() else "partiel",
              "pilotage": "validation locale ; aucun calcul ARA complet", "crs": "EPSG:2154",
              "carreaux_pilote": len(cells), "carreaux_avec_voisins": len(population), "population_pilote": pop_total,
              "offres_pilote": len(o2), "offres_avec_voisins": len(offers), "offres_par_type": o2["type"].value_counts().to_dict(),
              "paires_accessibles": len(pairs), "paires_indeterminees": len(unknown),
              "acces_stations": {"nombre_acces": len(accesses), "stations": sorted(set(accesses.offre_id)),
                                 "paires_via_acces": int(pairs.acces_id.notna().sum()) if "acces_id" in pairs else 0,
                                 "temps_arrivee": "Minimum jusqu'à un accès extérieur ; station logique comptée une fois, sans temps supplémentaire jusqu'au quai."},
              "paires_accessibles_par_type": linked["type"].value_counts().to_dict(),
              "paires_depuis_voisins_vers_offre_lyon": int((~linked.dans_pilote_population & linked.dans_pilote_offre).sum()),
              "paires_depuis_lyon_vers_offre_voisine": int((linked.dans_pilote_population & ~linked.dans_pilote_offre).sum()),
              "statuts_population": pq.statut.value_counts().to_dict(), "statuts_offre": oq.statut.value_counts().to_dict(),
              "statuts_o1": o1.statut.value_counts().to_dict(), "statuts_o2": o2.statut.value_counts().to_dict(),
              "groupes_o1": len(o1), "regroupements_sans_bord_commun": fallbacks,
              "o1_moyenne_options_par_habitant": float((pilot_counts.population * pilot_counts.nb_options).sum() / pop_total) if o1_complete else None,
              "o1_moyenne_options_par_habitant_min": float((pilot_counts.population * pilot_counts.nb_options).sum() / pop_total),
              "graphe": graph_stats, "tuiles": receipts, "sources_sha256": source_hashes,
              "parametres": {"duree_s": 300, "vitesse_kmh": speed, "max_raccord_population_m": 150, "max_raccord_offre_m": 50,
                             "marge_points_m": radius, "marge_reseau_m": 2 * radius + 1, "publication_population_min": 20},
              "definitions": {"o1": "Nombre de points d'offre accessibles au centre ; moyenne pondérée par population pour les groupes, selon le pilote Grenoble.",
                              "o2": "Population des centres accessibles par offre logique, voisins inclus ; un centre n'est compté qu'une fois même si plusieurs accès sont atteints."},
              "limites": ["Raccords droits hypothétiques ; pentes, murs, entrées réelles et accès aux bâtiments non résolus.",
                          "Points métro testés géométriquement ; position des entrées publiques non certifiée.",
                          "Croix-Paquet : deux nœuds extérieurs avant tourniquets acceptés pour le MVP ; cinq minutes jusqu'à l'accès, sans temps d'accès au quai ni attente.",
                          "Valeur complète vide si une paire potentielle reste indéterminée ; borne inférieure conservée.",
                          "Population fiscale estimée représentée aux centres de carreaux ; seuil de regroupement 20 habitants.",
                          "Offre limitée aux sources livrées ; pas d'exhaustivité ferroviaire ou régionale démontrée.",
                          "O1 conserve la définition du pilote, à harmoniser avec le plan V5 avant publication régionale."],
              "duree_execution_s": time.monotonic() - started}
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir.parent) as temp:
        stage = Path(temp) / "pilote"
        stage.mkdir()
        for name, f in [("gold_o1", o1), ("gold_o2", o2), ("raccordements_population", pq), ("raccordements_offre", oq)]:
            f.to_parquet(stage / (name + ".parquet"), index=False)
            if name.startswith("gold"):
                f.drop(columns="geometry").to_csv(stage / (name + ".csv"), index=False)
                (stage / (name + ".geojson")).write_text(f.to_crs(4326).to_json(drop_id=True), encoding="utf-8")
        pairs.to_parquet(stage / "accessibilite_carreaux_offres.parquet", index=False)
        unknown.to_parquet(stage / "paires_indeterminees.parquet", index=False)
        accesses.to_parquet(stage / "acces_stations.parquet", index=False)
        accesses.drop(columns="geometry").to_csv(stage / "acces_stations.csv", index=False)
        (stage / "acces_stations.geojson").write_text(accesses.to_crs(4326).to_json(drop_id=True), encoding="utf-8")
        access_qc.to_parquet(stage / "raccordements_acces_stations.parquet", index=False)
        mapping.to_parquet(stage / "groupes_o1.parquet", index=False)
        gpd.GeoDataFrame({"code_commune": ["69123"]}, geometry=[boundary], crs=2154).to_parquet(stage / "perimetre.parquet", index=False)
        (stage / "validation_trajets.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["fichiers_sha256"] = {f.name: sha256(f) for f in stage.iterdir()}
        for name in ["gold_o1", "gold_o2"]:
            f = gpd.read_parquet(stage / (name + ".parquet"))
            if not f.geometry.is_valid.all():
                raise ValueError("Export géométrique invalide")
        for path, digest in source_hashes.items():
            if sha256(Path(path)) != digest:
                raise ValueError("Source ou code modifié pendant le pilote")
        verify_files(args.preparation, plan["fichiers"])
        for tile, digest in receipts.items():
            directory = args.reseau / tile
            if sha256(directory / "reseau_pieton_qc.json") != digest:
                raise ValueError("Reçu de tuile modifié")
            verify_files(directory, json.loads((directory / "reseau_pieton_qc.json").read_text())["fichiers"])
        (stage / "accessibilite_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.output_dir.exists():
            raise ValueError("Dossier de sortie apparu pendant le pilote")
        stage.rename(args.output_dir)
    print(json.dumps({k: report[k] for k in ["validation_technique", "etat_indicateurs", "paires_accessibles", "paires_indeterminees", "statuts_o1", "statuts_o2"]}, ensure_ascii=False, indent=2), flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/accessibilite_lyon_acces_stations")
    ap.add_argument("--acces-stations", type=Path, default=ROOT / "geo/config/acces_stations.json")
    ap.add_argument("--sans-acces-stations", action="store_true", help="Reproduire le modèle historique sans accès explicites")
    return ap


if __name__ == "__main__":
    build(parser().parse_args())
