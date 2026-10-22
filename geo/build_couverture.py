"""gold_couverture ARA : candidats → centres de population atteignables en 300 s."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import json
import multiprocessing
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
import shapely
from shapely import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.build_accessibilite_ara import read_inputs, write_json, tile_receipt
from geo.check_raccordements_ara import sha256, verify_files
from geo.coverage_routing import CoverageNetwork, route_coverage

PAIR_SCHEMA = pa.schema([("id_candidat", pa.string()), ("id_carreau", pa.string()), ("temps_pieton_s", pa.float64())])
UNKNOWN_SCHEMA = pa.schema([("id_candidat", pa.string()), ("id_carreau", pa.string()), ("motif", pa.string())])
CTX = None


def inputs(preparation, network, cache):
    context = read_inputs(preparation, network, cache, None)
    pop = context["pop"]
    candidates = pop.copy()
    candidates["id_candidat"] = candidates.id_carreau
    candidates.loc[candidates.distance_raccordement_m.gt(50), "statut"] = "raccordement_trop_loin"
    return {"population": pop, "candidates": candidates, "manifest": context["manifest"],
            "tree": STRtree(pop.geometry.to_numpy())}


def signature_for(args, ctx):
    files = {"plan": args.preparation / "plan_reseau_ara.json", "reseau": args.reseau / "construction_reseau_ara.json",
             "controle_reseau": args.reseau / "controle_independant.json", "raccordements": args.raccordements / "raccordements_qc.json"}
    for name in ["build_couverture.py", "coverage_routing.py", "csr_walking.py", "build_accessibilite_ara.py", "check_raccordements_ara.py", "station_access.py"]:
        files["code_" + name] = ROOT / "geo" / name
    return {"version": 1, "sha256": {key: sha256(path) for key, path in files.items()},
            "parametres": {"vitesse_kmh": 4.5, "duree_s": 300, "seuil_candidat_m": 50, "seuil_population_m": 150,
                           "candidats": "centres_gold_candidates", "auto_couverture": True},
            "versions": {"numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__,
                         "geopandas": gpd.__version__, "shapely": shapely.__version__, "geos": shapely.geos_version_string,
                         "pyarrow": pa.__version__}, "tuiles": sorted(ctx["manifest"]["tuiles"])}


def init_worker(preparation, network, cache, output, signature):
    global CTX
    CTX = inputs(Path(preparation), Path(network), Path(cache))
    CTX.update(network=Path(network), output=Path(output), signature=signature,
               owners=CTX["candidates"].groupby("tuile_id").indices)


def process_tile(tile):
    ctx, start = CTX, time.monotonic()
    directory = ctx["network"] / tile
    qp = directory / "reseau_pieton_qc.json"
    qc = json.loads(qp.read_text())
    if (sha256(qp) != ctx["manifest"]["tuiles"][tile]["qc_sha256"]
            or qc["signature"] != ctx["manifest"]["signature"]
            or qc["etat"] not in ("valide", "valide_sans_troncon_sans_origine")):  # tuile en mer : vide, sans origine
        raise ValueError(f"Tuile réseau incompatible : {tile}")
    verify_files(directory, qc["fichiers"])
    candidates = ctx["candidates"].iloc[ctx["owners"].get(tile, [])].reset_index(drop=True)
    query = ctx["tree"].query(candidates.geometry.to_numpy(), predicate="dwithin", distance=375.000001)
    js = np.unique(query[1])
    population = ctx["population"].iloc[js].reset_index(drop=True)
    query[1] = np.searchsorted(js, query[1])
    needed = bool(query.size and (candidates.statut.eq("ok").to_numpy()[query[0]] & population.statut.eq("ok").to_numpy()[query[1]]).any())
    engine = None
    if needed and qc["etat"] != "valide":
        raise ValueError(f"Tuile sans tronçon mais avec des trajets à calculer : {tile}")
    if needed:
        edges = pd.read_parquet(directory / "reseau_pieton_troncons.parquet", columns=["de", "vers", "cle", "longueur_m", "temps_pieton_s"])
        engine = CoverageNetwork(edges)
    pairs, pending = route_coverage(engine, candidates, population, query)
    destination = ctx["output"] / "tuiles" / tile
    destination.parent.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as tmp:
        stage = Path(tmp) / tile
        stage.mkdir()
        pq.write_table(pa.Table.from_pandas(pairs, schema=PAIR_SCHEMA, preserve_index=False), stage / "paires.parquet")
        pq.write_table(pa.Table.from_pandas(pending, schema=UNKNOWN_SCHEMA, preserve_index=False), stage / "indeterminees.parquet")
        receipt = {"signature": ctx["signature"], "tuile_id": tile, "candidats": len(candidates),
                   "candidats_raccordables": int(candidates.statut.eq("ok").sum()), "paires_euclidiennes": query.shape[1],
                   "paires_accessibles": len(pairs), "paires_indeterminees": len(pending),
                   "reseau_qc_sha256": sha256(qp), "duree_s": time.monotonic() - start,
                   "fichiers_sha256": {p.name: sha256(p) for p in stage.iterdir()}}
        write_json(stage / "calcul_qc.json", receipt)
        if destination.exists():
            raise ValueError("Refus d'écraser un checkpoint")
        stage.rename(destination)
    return receipt


def summarize(candidates, population, pairs, unknown):
    """Les populations couvertes ne s'additionnent pas entre candidats."""
    served = pairs.merge(population[["id_carreau", "population"]], on="id_carreau", validate="many_to_one")
    result = candidates[["id_candidat", "id_carreau", "tuile_id", "code_commune", "distance_raccordement_m", "geometry"]].rename(columns={"id_carreau": "id_carreau_origine"}).copy()
    result["statut_raccordement"] = candidates.statut
    result["nb_relations_indeterminees"] = result.id_candidat.map(unknown.groupby("id_candidat").size()).fillna(0).astype(int)
    result["statut"] = np.where(result.nb_relations_indeterminees.gt(0), "partiel", "ok")
    result.loc[result.statut_raccordement.ne("ok"), "statut"] = "non_calcule"
    for column, values in [("population_couverte", served.groupby("id_candidat").population.sum()),
                           ("nb_carreaux_couverts", pairs.groupby("id_candidat").size())]:
        result[column + "_min"] = result.id_candidat.map(values).fillna(0).where(result.statut.ne("non_calcule"))
        result[column] = result[column + "_min"].where(result.statut.eq("ok"))
    result["utilisable_optimiseur"] = result.statut.eq("ok")
    return result


def assemble(args, ctx, signature, receipts):
    destination = args.output_dir / "resultats"
    if destination.exists():
        report = json.loads((destination / "couverture_qc.json").read_text())
        if report["signature"] != signature:
            raise ValueError("Résultats incompatibles")
        verify_files(destination, report["fichiers_sha256"])
        return report
    if sum(r["candidats"] for r in receipts.values()) != len(ctx["candidates"]):
        raise ValueError("Candidats perdus ou dupliqués entre tuiles")
    pairs = pd.concat([pd.read_parquet(args.output_dir / "tuiles" / t / "paires.parquet") for t in sorted(receipts)], ignore_index=True)
    unknown = pd.concat([pd.read_parquet(args.output_dir / "tuiles" / t / "indeterminees.parquet") for t in sorted(receipts)], ignore_index=True)
    candidate_ids, pop_ids = set(ctx["candidates"].id_candidat), set(ctx["population"].id_carreau)
    for frame in [pairs, unknown]:
        if (frame.duplicated(["id_candidat", "id_carreau"]).any()
                or not set(frame.id_candidat).issubset(candidate_ids) or not set(frame.id_carreau).issubset(pop_ids)):
            raise ValueError("Identifiants inconnus ou relations dupliquées")
    if (not pairs.merge(unknown, on=["id_candidat", "id_carreau"]).empty
            or not np.isfinite(pairs.temps_pieton_s).all() or not pairs.temps_pieton_s.between(0, 300).all()):
        raise ValueError("Relations incohérentes ou temps invalide")
    good = set(ctx["candidates"].loc[ctx["candidates"].statut.eq("ok"), "id_candidat"])
    self_pairs = pairs.loc[pairs.id_candidat.eq(pairs.id_carreau)]
    if set(self_pairs.id_candidat) != good or not set(pairs.id_candidat).issubset(good):
        raise ValueError("Auto-couverture absente ou candidat non raccordable présenté comme couvert")
    summary = summarize(ctx["candidates"], ctx["population"], pairs, unknown)
    if signature_for(args, inputs(args.preparation, args.reseau, args.raccordements)) != signature:
        raise ValueError("Code ou sources modifiés pendant le calcul")
    for tile in receipts:
        tile_receipt(args.output_dir, tile, signature)
    with TemporaryDirectory(dir=args.output_dir) as tmp:
        stage = Path(tmp) / "resultats"
        stage.mkdir()
        pairs.to_parquet(stage / "gold_couverture.parquet", index=False)
        unknown.to_parquet(stage / "couverture_indeterminee.parquet", index=False)
        summary.to_parquet(stage / "candidats_couverture.parquet", index=False)
        summary.drop(columns="geometry").to_csv(stage / "candidats_couverture.csv", index=False)
        report = {"etat": "regional_termine", "date_utc": datetime.now(timezone.utc).isoformat(), "signature": signature,
                  "tuiles": len(receipts), "candidats": len(summary), "carreaux_population": len(ctx["population"]),
                  "population_source": float(ctx["population"].population.sum()), "relations_accessibles": len(pairs),
                  "relations_indeterminees": len(unknown), "relations_euclidiennes": sum(r["paires_euclidiennes"] for r in receipts.values()),
                  "statuts_candidats": {str(k): int(v) for k, v in summary.statut.value_counts().items()},
                  "auto_relations": len(self_pairs), "population_couverte_unique_min": float(ctx["population"].loc[ctx["population"].id_carreau.isin(pairs.id_carreau), "population"].sum()),
                  "motifs_indetermines": {str(k): int(v) for k, v in unknown.motif.value_counts().items()},
                  "limites": ["Candidats théoriques : centres de gold_candidates, aucune faisabilité foncière ou technique vérifiée.",
                              "id_candidat égale l'id_carreau d'origine ; id_carreau dans la relation désigne la population cible.",
                              "Marche bidirectionnelle, 4,5 km/h, 300 s raccordements inclus, seuil candidat 50 m et population 150 m.",
                              "Centre source et cible confondus : les deux raccordements restent inclus, comme pour O1/O2.",
                              "Un candidat peut couvrir plusieurs carreaux et un carreau plusieurs candidats : union nécessaire pour éviter les doubles comptes.",
                              "L'absence de paire signifie hors budget uniquement pour les candidats au statut ok ; les échecs ne deviennent pas zéro.",
                              "Couverture brute potentielle, sans soustraire la desserte existante, sans coût, prédiction, type d'équipement ni sélection sous budget.",
                              "Utiliser les candidats utilisable_optimiseur=true pour une optimisation sur des résultats complets ; documenter toute utilisation des bornes partielles.",
                              "Données détaillées pour l'équipe projet ; pas une diffusion O1 des petits effectifs ; population fiscale 2021 représentée au centre des carreaux."],
                  "fichiers_sha256": {p.name: sha256(p) for p in stage.iterdir()}}
        write_json(stage / "couverture_qc.json", report)
        stage.rename(destination)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--raccordements", type=Path, default=ROOT / "data/silver/raccordements_ara")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/couverture_ara")
    ap.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    ap.add_argument("--tuiles", nargs="+")
    ap.add_argument("--reprendre", action="store_true")
    return ap


def run(args):
    ctx = inputs(args.preparation, args.reseau, args.raccordements)
    signature = signature_for(args, ctx)
    requested = sorted(set(args.tuiles or signature["tuiles"]))
    if not set(requested).issubset(signature["tuiles"]):
        raise ValueError("Tuile inconnue")
    if args.output_dir.exists() and not args.reprendre:
        raise ValueError("Dossier existant : utiliser --reprendre pour vérifier les checkpoints")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sp = args.output_dir / "signature.json"
        if sp.exists():
            if json.loads(sp.read_text()) != signature:
                raise ValueError("Reprise refusée : sources, code ou paramètres différents")
        else:
            if any(p.name != ".lock" for p in args.output_dir.iterdir()):
                raise ValueError("Dossier non vide sans signature")
            write_json(sp, signature)
        receipts = {}
        for tile in signature["tuiles"]:
            receipt = tile_receipt(args.output_dir, tile, signature)
            if receipt is not None:
                receipts[tile] = receipt
        pending = [t for t in requested if t not in receipts]
        print(f"Tuiles contrôlées : {len(receipts)}/{len(signature['tuiles'])} ; à calculer : {len(pending)}", flush=True)

        def progress(state):
            write_json(args.output_dir / "progression.json", {"etat": state, "tuiles_terminees": len(receipts),
                       "tuiles_total": len(signature["tuiles"]), "relations_accessibles": sum(r["paires_accessibles"] for r in receipts.values()),
                       "relations_indeterminees": sum(r["paires_indeterminees"] for r in receipts.values()),
                       "date_utc": datetime.now(timezone.utc).isoformat()})

        progress("routage_en_cours")
        if pending:
            initargs = (args.preparation, args.reseau, args.raccordements, args.output_dir, signature)
            with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn"), initializer=init_worker, initargs=initargs) as executor:
                for future in as_completed([executor.submit(process_tile, tile) for tile in pending]):
                    receipt = future.result()
                    receipts[receipt["tuile_id"]] = receipt
                    if len(receipts) % 10 == 0 or all(t in receipts for t in requested):
                        print(f"Couverture : {len(receipts)}/{len(signature['tuiles'])} tuiles ; {sum(r['paires_accessibles'] for r in receipts.values())} relations", flush=True)
                        progress("routage_en_cours")
        if len(receipts) != len(signature["tuiles"]):
            progress("partiel_reprise_possible")
            return None
        progress("assemblage_en_cours")
        report = assemble(args, ctx, signature, receipts)
        progress("regional_termine")
        print(json.dumps({k: report[k] for k in ["etat", "candidats", "relations_accessibles", "relations_indeterminees", "statuts_candidats"]}, ensure_ascii=False, indent=2), flush=True)
        return report


if __name__ == "__main__":
    run(parser().parse_args())
