"""Contrôle des résultats ARA et comparaison à la référence Lyon conservée."""

import argparse
import json
import math
from pathlib import Path
import sys

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.check_raccordements_ara import sha256, verify_files


def compare_lyon(output, pilot, preparation=ROOT / "data/silver/preparation_reseau_ara"):
    """Tous les trajets du domaine pilote, y compris les paires négatives."""
    pilot_qc = json.loads((pilot / "accessibilite_qc.json").read_text())
    verify_files(pilot, pilot_qc["fichiers_sha256"])
    old = pd.read_parquet(pilot / "accessibilite_carreaux_offres.parquet")
    old_unknown = pd.read_parquet(pilot / "paires_indeterminees.parquet")
    ps = gpd.read_parquet(pilot / "raccordements_population.parquet")
    os = gpd.read_parquet(pilot / "raccordements_offre.parquet")
    pop_ids, offer_ids = set(ps.id_carreau), set(os.offre_id)
    city_pop = set(ps.loc[ps.dans_pilote, "id_carreau"])
    city_off = set(os.loc[os.dans_pilote, "offre_id"])
    tiles = sorted(set(gpd.read_parquet(preparation / "entrees/gold_candidates.parquet")
                       .set_index("id_carreau").loc[list(pop_ids), "tuile_id"]))
    def read(name):
        frame = pd.concat([pd.read_parquet(output / "tuiles" / t / name) for t in tiles], ignore_index=True)
        return frame.loc[frame.id_carreau.isin(pop_ids) & frame.offre_id.isin(offer_ids)
                         & (frame.id_carreau.isin(city_pop) | frame.offre_id.isin(city_off))]
    keys = ["id_carreau", "offre_id"]
    current, current_unknown = read("paires.parquet"), read("indeterminees.parquet")
    if set(map(tuple, old[keys].to_numpy())) != set(map(tuple, current[keys].to_numpy())):
        raise ValueError("Les paires régionales diffèrent de la référence Lyon")
    if set(map(tuple, old_unknown[keys].to_numpy())) != set(map(tuple, current_unknown[keys].to_numpy())):
        raise ValueError("Les paires indéterminées diffèrent de la référence Lyon")
    joined = current.merge(old, on=keys, suffixes=("_ara", "_pilote"), validate="one_to_one")
    error = float((joined.temps_pieton_s_ara - joined.temps_pieton_s_pilote).abs().max())
    if error > 1e-6:
        raise ValueError("Les temps régionaux diffèrent de la référence Lyon")
    if not joined.acces_id_ara.fillna("").equals(joined.acces_id_pilote.fillna("")):
        raise ValueError("Accès retenus différents du pilote")
    return {"paires_identiques": len(joined), "indeterminees_identiques": len(current_unknown), "erreur_max_s": error}


def reference_time(graph, edges, source, target, access_node=None):
    """Dijkstra NetworkX avec deux nœuds virtuels, sans moteur CSR."""
    origin, destination = "source_virtuelle", "cible_virtuelle"
    key = (int(source.raccord_de), int(source.raccord_vers), int(source.raccord_cle))
    length = edges.loc[key]
    try:
        graph.add_edge(origin, key[0], weight=source.temps_acces_s + source.abscisse_raccord_m / 1.25)
        graph.add_edge(origin, key[1], weight=source.temps_acces_s + (length - source.abscisse_raccord_m) / 1.25)
        if access_node is not None:
            if access_node not in graph:
                return math.inf
            graph.add_edge(access_node, destination, weight=0.)
        else:
            tk = (int(target.raccord_de), int(target.raccord_vers), int(target.raccord_cle))
            tl = edges.loc[tk]
            graph.add_edge(tk[0], destination, weight=target.temps_acces_s + target.abscisse_raccord_m / 1.25)
            graph.add_edge(tk[1], destination, weight=target.temps_acces_s + (tl - target.abscisse_raccord_m) / 1.25)
            if key[2] == tk[2] and set(key[:2]) == set(tk[:2]):
                position = target.abscisse_raccord_m if key[0] == tk[0] else length - target.abscisse_raccord_m
                graph.add_edge(origin, destination, weight=source.temps_acces_s + abs(source.abscisse_raccord_m - position) / 1.25 + target.temps_acces_s)
        reached = nx.single_source_dijkstra_path_length(graph, origin, cutoff=300.000001, weight="weight")
        return reached.get(destination, math.inf)
    finally:
        graph.remove_nodes_from([origin, destination])


def validate(args):
    result = args.output_dir / "resultats"
    qc_path = result / "accessibilite_qc.json"
    qc = json.loads(qc_path.read_text())
    plan_path = args.preparation / "plan_reseau_ara.json"
    if sha256(plan_path) != qc["signature"]["sha256"]["plan"]:
        raise ValueError("Préparation différente de celle du calcul")
    verify_files(args.preparation, json.loads(plan_path.read_text())["fichiers"])
    verify_files(result, qc["fichiers_sha256"])
    pop = gpd.read_parquet(args.preparation / "entrees/gold_candidates.parquet")
    offers = gpd.read_parquet(args.preparation / "entrees/gold_offre.parquet")
    o1, o2 = [gpd.read_parquet(result / "publics" / (name + ".parquet")) for name in ["gold_o1", "gold_o2"]]
    mapping = pd.read_parquet(result / "internes/correspondance_groupes.parquet")
    pairs = pd.read_parquet(result / "internes/paires_accessibles.parquet")
    unknown = pd.read_parquet(result / "internes/paires_indeterminees.parquet")
    ps = gpd.read_parquet(result / "internes/raccordements_population.parquet").set_index("id_carreau")
    os = gpd.read_parquet(result / "internes/raccordements_offre.parquet").set_index("offre_id")
    accesses = gpd.read_parquet(result / "publics/acces_stations.parquet").set_index("acces_id")
    if (not mapping.id_carreau.is_unique or set(mapping.id_carreau) != set(pop.id_carreau)
            or set(mapping.groupe_id) != set(o1.groupe_id) or not o1.groupe_id.is_unique
            or not o2.offre_id.is_unique or set(o2.offre_id) != set(offers.offre_id)
            or not math.isclose(float(o1.population.sum()), float(pop.population.sum()), abs_tol=1e-6)
            or o1.population.dropna().lt(20).any() or int(o1.nb_carreaux.sum()) != len(pop)
            or not o1.geometry.is_valid.all() or o1.geometry.is_empty.any()):
        raise ValueError("Identifiants, conservation de population ou seuil O1 incorrects")
    if not o2.geometry.reset_index(drop=True).equals(offers.set_index("offre_id").loc[o2.offre_id].geometry.reset_index(drop=True)):
        raise ValueError("Coordonnées de l'offre source modifiées")
    for frame in [pairs, unknown]:
        if frame.duplicated(["id_carreau", "offre_id"]).any():
            raise ValueError("Paire comptée plusieurs fois")
    if not pairs.merge(unknown, on=["id_carreau", "offre_id"]).empty:
        raise ValueError("Paire simultanément connue et indéterminée")
    targets = offers.set_index("offre_id").geometry.reindex(pairs.offre_id).reset_index(drop=True)
    mask = pairs.acces_id.notna()
    targets.loc[mask] = accesses.geometry.reindex(pairs.loc[mask, "acces_id"]).to_numpy()
    straight = ps.geometry.reindex(pairs.id_carreau).reset_index(drop=True).distance(targets) / 1.25
    if (pairs.temps_pieton_s.lt(straight - 1e-6).any() or not pairs.temps_pieton_s.between(0, 300).all()):
        raise ValueError("Temps hors budget ou inférieur à la borne à vol d'oiseau")
    served = pairs.merge(pop[["id_carreau", "population"]], on="id_carreau", validate="many_to_one").groupby("offre_id").population.sum()
    expected = o2.offre_id.map(served).fillna(0).where(o2.statut.ne("non_calcule"))
    np.testing.assert_allclose(o2.population_desservie_min, expected, atol=1e-6, rtol=0, equal_nan=True)
    if o1.loc[o1.statut.ne("ok"), "options_par_habitant"].notna().any() or o2.loc[o2.statut.ne("ok"), "population_desservie"].notna().any():
        raise ValueError("Résultat incomplet publié comme complet")
    cell_options = pairs.groupby("id_carreau").size()
    weighted = mapping.merge(pop[["id_carreau", "population"]], on="id_carreau", validate="one_to_one")
    weighted["somme_options"] = weighted.id_carreau.map(cell_options).fillna(0) * weighted.population
    expected_o1 = weighted.groupby("groupe_id").somme_options.sum() / weighted.groupby("groupe_id").population.sum()
    np.testing.assert_allclose(o1.options_par_habitant_min, o1.groupe_id.map(expected_o1), atol=1e-9, rtol=0)
    # Deux extrêmes par mode et six trajets franchissant une limite de tuile.
    linked = pairs.merge(offers[["offre_id", "type", "tuile_id"]], on="offre_id")
    chosen = set()
    for _, group in linked.groupby("type"):
        chosen.update([group.temps_pieton_s.idxmax(), group.temps_pieton_s.idxmin()])
    owner = pop.set_index("id_carreau").tuile_id
    chosen.update(linked.index[linked.id_carreau.map(owner).ne(linked.tuile_id)][:6])
    chosen.update(linked.index[linked.acces_id.notna()])
    witnesses = linked.loc[sorted(chosen)].copy()
    witnesses["tuile_source"] = witnesses.id_carreau.map(owner)
    evidence, negative_count = [], 0
    for tile, rows in witnesses.groupby("tuile_source"):
        e = pd.read_parquet(args.reseau / tile / "reseau_pieton_troncons.parquet", columns=["de", "vers", "cle", "longueur_m", "temps_pieton_s"])
        graph = nx.DiGraph()
        shortest = e.groupby(["de", "vers"]).temps_pieton_s.min().reset_index()
        graph.add_weighted_edges_from(shortest.itertuples(index=False, name=None))
        lengths = e.set_index(["de", "vers", "cle"]).longueur_m
        for row in rows.itertuples(index=False):
            node = int(accesses.loc[row.acces_id].noeud_osm) if pd.notna(row.acces_id) else None
            value = reference_time(graph, lengths, ps.loc[row.id_carreau], os.loc[row.offre_id], node)
            if abs(value - row.temps_pieton_s) > 1e-6:
                raise ValueError("Témoin NetworkX différent du calcul CSR")
            evidence.append({"id_carreau": row.id_carreau, "offre_id": row.offre_id, "type": row.type,
                             "temps_s": value, "tuile": tile})
        # Une paire géographiquement candidate mais rejetée par le réseau, par tuile témoin.
        local = ps.loc[ps.tuile_id.eq(tile) & ps.statut.eq("ok")]
        ordinary = os.loc[os.statut.eq("ok") & os.nb_acces.eq(0)]
        indices = STRtree(ordinary.geometry.to_numpy()).query(local.geometry.to_numpy(), predicate="dwithin", distance=375)
        existing = set(map(tuple, pd.concat([pairs, unknown])[["id_carreau", "offre_id"]].to_numpy()))
        for i, j in indices.T:
            a, b = local.iloc[i], ordinary.iloc[j]
            if (a.name, b.name) in existing or (int(b.raccord_de), int(b.raccord_vers), int(b.raccord_cle)) not in lengths.index:
                continue
            value = reference_time(graph, lengths, a, b)
            if value <= 300.0000001:
                raise ValueError("Faux négatif de routage régional")
            negative_count += 1
            break
        print(f"Contrôle indépendant : {tile}, {len(rows)} trajets positifs", flush=True)
    comparison = compare_lyon(args.output_dir, args.pilote_lyon, args.preparation)
    pilot_o2 = gpd.read_parquet(args.pilote_lyon / "gold_o2.parquet").set_index("offre_id")
    np.testing.assert_allclose(o2.set_index("offre_id").loc[pilot_o2.index, "population_desservie_min"],
                               pilot_o2.population_desservie_min, atol=1e-6, rtol=0, equal_nan=True)
    return {"etat": "valide", "resultats_qc_sha256": sha256(qc_path), "carreaux": len(pop), "offres": len(o2),
            "population_conservee": float(o1.population.sum()), "comparaison_lyon": comparison,
            "temoins_positifs_networkx": evidence, "temoins_negatifs_networkx": negative_count,
            "script_sha256": sha256(Path(__file__))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/accessibilite_ara")
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--pilote-lyon", type=Path, default=ROOT / "data/gold/accessibilite_lyon_acces_stations")
    args = ap.parse_args()
    report = validate(args)
    (args.output_dir / "controle_independant.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "temoins_positifs_networkx"}, ensure_ascii=False, indent=2))
