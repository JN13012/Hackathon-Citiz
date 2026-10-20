"""Contrôle indépendant de gold_couverture, sans utiliser le moteur de production."""

import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.check_raccordements_ara import sha256, verify_files
from geo.validate_accessibilite_ara import reference_time


def publish(output, destination):
    """Publier des tables vérifiées dans gold, sans remplacer une livraison différente."""
    source = output / "resultats"
    qp = source / "couverture_qc.json"
    proof = output / "controle_independant.json"
    report = json.loads(proof.read_text())
    if report["etat"] != "valide" or report["couverture_qc_sha256"] != sha256(qp):
        raise ValueError("Publication refusée : contrôle indépendant absent ou incompatible")
    qc = json.loads(qp.read_text())
    verify_files(source, qc["fichiers_sha256"])
    files = {name: source / name for name in qc["fichiers_sha256"]}
    files["gold_couverture_qc.json"] = qp
    files["gold_couverture_controle.json"] = proof
    # Contrôler toutes les collisions avant la première écriture.
    for name, path in files.items():
        target = destination / name
        if target.exists() and (not target.is_file() or sha256(target) != sha256(path)):
            raise ValueError(f"Publication refusée : livraison différente existante {target}")
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination) as tmp:
        for name, path in files.items():
            target = destination / name
            if target.exists():
                continue
            staged = Path(tmp) / name
            shutil.copyfile(path, staged)
            if sha256(staged) != sha256(path):
                raise ValueError("Copie différente de la livraison vérifiée")
            # Le lien atomique échoue si un autre processus a créé la destination.
            target.hardlink_to(staged)
    return sorted(files)


def witnesses(pairs, unknown, points, network):
    """Extrêmes par département, auto-couvertures, frontières et témoins négatifs."""
    indexed = points.set_index("id_carreau")
    linked = pairs.copy()
    linked["departement"] = linked.id_candidat.map(indexed.code_commune).str[:2]
    linked["tuile"] = linked.id_candidat.map(indexed.tuile_id)
    chosen = set()
    for _, group in linked.groupby("departement"):
        chosen.update([group.temps_pieton_s.idxmin(), group.temps_pieton_s.idxmax()])
    cross = linked.loc[linked.tuile.ne(linked.id_carreau.map(indexed.tuile_id))]
    chosen.update(cross.drop_duplicates("tuile").head(8).index)
    chosen.update(linked.loc[linked.id_candidat.eq(linked.id_carreau)].drop_duplicates("departement").index)
    chosen.update(linked.sample(n=min(8, len(linked)), random_state=84).index)
    selected = linked.loc[sorted(chosen)]
    existing = set(map(tuple, pd.concat([pairs, unknown])[["id_candidat", "id_carreau"]].to_numpy()))
    population = indexed.loc[indexed.statut.eq("ok")]
    tree = STRtree(population.geometry.to_numpy())
    evidence, negatives = [], []
    for tile, group in selected.groupby("tuile"):
        directory = network / tile
        qc = json.loads((directory / "reseau_pieton_qc.json").read_text())
        verify_files(directory, qc["fichiers"])
        edges = pd.read_parquet(directory / "reseau_pieton_troncons.parquet", columns=["de", "vers", "cle", "longueur_m", "temps_pieton_s"])
        graph = nx.DiGraph()
        shortest = edges.groupby(["de", "vers"]).temps_pieton_s.min().reset_index()
        graph.add_weighted_edges_from(shortest.itertuples(index=False, name=None))
        lengths = edges.set_index(["de", "vers", "cle"]).longueur_m
        for row in group.itertuples(index=False):
            a, b = indexed.loc[row.id_candidat], indexed.loc[row.id_carreau]
            forward = reference_time(graph, lengths, a, b)
            backward = reference_time(graph, lengths, b, a)
            if not np.isclose(forward, row.temps_pieton_s, atol=1e-6, rtol=0) or not np.isclose(backward, forward, atol=1e-6, rtol=0):
                raise ValueError("Témoin NetworkX ou sens inverse différent de la relation produite")
            evidence.append({"id_candidat": row.id_candidat, "id_carreau": row.id_carreau,
                             "temps_s": forward, "tuile": tile, "sens_inverse_verifie": True})
        local = population.loc[population.tuile_id.eq(tile) & population.distance_raccordement_m.le(50)]
        query = tree.query(local.geometry.to_numpy(), predicate="dwithin", distance=375.000001)
        found = 0
        for i, j in query.T:
            a, b = local.iloc[i], population.iloc[j]
            if (a.name, b.name) in existing or (int(b.raccord_de), int(b.raccord_vers), int(b.raccord_cle)) not in lengths.index:
                continue
            value = reference_time(graph, lengths, a, b)
            if value <= 300.0000001:
                raise ValueError("Faux négatif dans la couverture")
            negatives.append({"id_candidat": a.name, "id_carreau": b.name, "tuile": tile})
            found += 1
            if found == 2:
                break
        print(f"Contrôle NetworkX : {tile}, {len(group)} positifs, {found} négatifs", flush=True)
    return evidence, negatives


def validate(args):
    directory = args.output_dir / "resultats"
    qp = directory / "couverture_qc.json"
    qc = json.loads(qp.read_text())
    if qc["etat"] != "regional_termine":
        raise ValueError("Calcul régional incomplet")
    verify_files(directory, qc["fichiers_sha256"])
    plan = args.preparation / "plan_reseau_ara.json"
    snap_qc = args.raccordements / "raccordements_qc.json"
    network_qc = args.reseau / "construction_reseau_ara.json"
    for key, path in [("plan", plan), ("raccordements", snap_qc), ("reseau", network_qc)]:
        if qc["signature"]["sha256"][key] != sha256(path):
            raise ValueError("Sources différentes de celles du calcul")
    verify_files(args.preparation, json.loads(plan.read_text())["fichiers"])
    verify_files(args.raccordements, json.loads(snap_qc.read_text())["fichiers_sha256"])
    pop = gpd.read_parquet(args.raccordements / "raccordements_population.parquet")
    source = gpd.read_parquet(args.preparation / "entrees/gold_candidates.parquet")
    pd.testing.assert_series_equal(pop.id_carreau, source.id_carreau, check_names=False)
    if not pop.geometry.equals(source.geometry):
        raise ValueError("Positions candidates différentes de la population raccordée")
    pairs = pd.read_parquet(directory / "gold_couverture.parquet")
    unknown = pd.read_parquet(directory / "couverture_indeterminee.parquet")
    summary = gpd.read_parquet(directory / "candidats_couverture.parquet")
    keys = ["id_candidat", "id_carreau"]
    ids = set(source.id_carreau)
    for frame in [pairs, unknown]:
        if frame.duplicated(keys).any() or not set(frame.id_candidat).issubset(ids) or not set(frame.id_carreau).issubset(ids):
            raise ValueError("Relations dupliquées ou identifiants inconnus")
    if not pairs.merge(unknown, on=keys).empty:
        raise ValueError("Relation à la fois connue et indéterminée")
    if not summary.id_candidat.is_unique or set(summary.id_candidat) != ids or not summary.id_candidat.eq(summary.id_carreau_origine).all():
        raise ValueError("Candidats incomplets ou identifiants incohérents")
    indexed = pop.set_index("id_carreau")
    if not summary.geometry.reset_index(drop=True).equals(indexed.geometry.reindex(summary.id_candidat).reset_index(drop=True)):
        raise ValueError("Géométrie candidate modifiée")
    good = pop.statut.eq("ok") & pop.distance_raccordement_m.le(50)
    good_ids = set(pop.loc[good, "id_carreau"])
    if not set(pairs.id_candidat).issubset(good_ids) or set(pairs.loc[pairs.id_candidat.eq(pairs.id_carreau), "id_candidat"]) != good_ids:
        raise ValueError("Candidat non raccordable couvert ou auto-couverture manquante")
    targets = indexed.geometry.reindex(pairs.id_carreau).reset_index(drop=True)
    starts = indexed.geometry.reindex(pairs.id_candidat).reset_index(drop=True)
    straight = starts.distance(targets) / 1.25
    if not np.isfinite(pairs.temps_pieton_s).all() or not pairs.temps_pieton_s.between(0, 300).all() or pairs.temps_pieton_s.lt(straight - 1e-6).any():
        raise ValueError("Temps hors budget ou inférieur à la borne à vol d'oiseau")
    # Tous les candidats, y compris les échecs : les inconnues ne sont pas omises.
    query = STRtree(pop.geometry.to_numpy()).query(source.geometry.to_numpy(), predicate="dwithin", distance=375.000001)
    unresolved = ~good.to_numpy()[query[0]] | ~pop.statut.eq("ok").to_numpy()[query[1]]
    expected_unknown = pd.DataFrame({"id_candidat": source.id_carreau.to_numpy()[query[0, unresolved]],
                                     "id_carreau": pop.id_carreau.to_numpy()[query[1, unresolved]]})
    pd.testing.assert_index_equal(pd.MultiIndex.from_frame(unknown[keys]).sort_values(),
                                  pd.MultiIndex.from_frame(expected_unknown).sort_values())
    if qc["relations_euclidiennes"] != query.shape[1]:
        raise ValueError("Paires candidates omises ou dupliquées entre tuiles")
    served = pairs.merge(pop[["id_carreau", "population"]], on="id_carreau", validate="many_to_one")
    for name, values in [("population_couverte", served.groupby("id_candidat").population.sum()),
                         ("nb_carreaux_couverts", pairs.groupby("id_candidat").size())]:
        expected = summary.id_candidat.map(values).fillna(0).where(summary.statut.ne("non_calcule"))
        np.testing.assert_allclose(summary[name + "_min"], expected, rtol=0, atol=1e-6, equal_nan=True)
        np.testing.assert_allclose(summary[name], expected.where(summary.statut.eq("ok")), rtol=0, atol=1e-6, equal_nan=True)
    expected_status = np.where(~summary.id_candidat.isin(good_ids), "non_calcule", np.where(summary.id_candidat.isin(unknown.id_candidat), "partiel", "ok"))
    if not summary.statut.eq(expected_status).all() or not summary.utilisable_optimiseur.eq(summary.statut.eq("ok")).all():
        raise ValueError("Statuts des candidats incohérents")
    positives, negatives = witnesses(pairs, unknown, pop, args.reseau)
    return {"etat": "valide", "couverture_qc_sha256": sha256(qp), "script_sha256": sha256(Path(__file__)),
            "reference_networkx_sha256": sha256(ROOT / "geo/validate_accessibilite_ara.py"),
            "candidats": len(summary), "relations_accessibles": len(pairs), "relations_indeterminees": len(unknown),
            "temoins_positifs": positives, "temoins_negatifs": negatives,
            "tuiles_total": qc["tuiles"], "paires_euclidiennes_exhaustives": query.shape[1]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/couverture_ara")
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--raccordements", type=Path, default=ROOT / "data/silver/raccordements_ara")
    ap.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--publier-dans", type=Path, help="Copier les tables validées dans ce dossier gold, sans écraser une autre livraison")
    args = ap.parse_args()
    report = validate(args)
    (args.output_dir / "controle_independant.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.publier_dans is not None:
        print("Fichiers publiés : " + ", ".join(publish(args.output_dir, args.publier_dans)), flush=True)
    print(json.dumps({k: v for k, v in report.items() if k not in ["temoins_positifs", "temoins_negatifs"]}, ensure_ascii=False, indent=2))
