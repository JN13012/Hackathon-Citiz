"""O1/O2 ARA par tuiles, checkpoints vérifiés et publication globale atomique."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import json
import math
import multiprocessing
import re
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
from shapely import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.check_raccordements_ara import sha256, verify_files
from geo.station_access import load_accesses
from geo.csr_walking import CSRWalking, route_candidates
from geo.population_groups_fast import population_groups_fast

SNAP = ["statut", "raccord_de", "raccord_vers", "raccord_cle", "abscisse_raccord_m", "temps_acces_s", "distance_raccordement_m"]
PAIR_SCHEMA = pa.schema([("id_carreau", pa.string()), ("offre_id", pa.string()), ("temps_pieton_s", pa.float64()), ("acces_id", pa.string())])
UNKNOWN_SCHEMA = pa.schema([("id_carreau", pa.string()), ("offre_id", pa.string()), ("motif", pa.string())])
CTX = None


def write_json(path, value):
    with TemporaryDirectory(dir=path.parent) as tmp:
        temp = Path(tmp) / path.name
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temp.replace(path)


def read_inputs(preparation, network, cache, access_config):
    plan_path, manifest_path = preparation / "plan_reseau_ara.json", network / "construction_reseau_ara.json"
    independent_path, cache_path = network / "controle_independant.json", cache / "raccordements_qc.json"
    plan, manifest, independent, cached = [json.loads(p.read_text()) for p in [plan_path, manifest_path, independent_path, cache_path]]
    if (manifest["etat"] != "regional_termine" or independent["etat"] != "valide"
            or independent["manifest_sha256"] != sha256(manifest_path)
            or manifest["signature"]["plan_sha256"] != sha256(plan_path)
            or cached["etat"] != "controle_regional_termine"
            or cached["sources_sha256"]["plan"] != sha256(plan_path)
            or cached["sources_sha256"]["reseau"] != sha256(manifest_path)
            or cached["sources_sha256"]["controle_independant"] != sha256(independent_path)):
        raise ValueError("Préparation, réseau et raccordements incompatibles")
    if plan["parametres"]["vitesse_kmh"] != 4.5 or plan["parametres"]["duree_s"] != 300:
        raise ValueError("Calcul validé pour 300 secondes à 4,5 km/h")
    if cached["parametres"] != {"vitesse_kmh": 4.5, "duree_s": 300, "seuil_population_m": 150, "seuil_offre_m": 50}:
        raise ValueError("Paramètres des raccordements incompatibles")
    verify_files(preparation, plan["fichiers"])
    verify_files(cache, cached["fichiers_sha256"])
    pop = gpd.read_parquet(preparation / "entrees/gold_candidates.parquet")
    offers = gpd.read_parquet(preparation / "entrees/gold_offre.parquet")
    ps = gpd.read_parquet(cache / "raccordements_population.parquet")
    os = gpd.read_parquet(cache / "raccordements_offre.parquet")
    for frame, snaps, key in [(pop, ps, "id_carreau"), (offers, os, "offre_id")]:
        if not frame[key].is_unique or not snaps[key].is_unique or set(frame[key]) != set(snaps[key]):
            raise ValueError("Identifiants des raccordements incomplets ou dupliqués")
        ordered = snaps.set_index(key).reindex(frame[key])
        if not frame.geometry.reset_index(drop=True).equals(ordered.geometry.reset_index(drop=True)):
            raise ValueError("Coordonnées différentes entre source et raccordements")
    pop = pop.merge(ps[["id_carreau", *SNAP]], on="id_carreau", validate="one_to_one")
    oq = offers.merge(os[["offre_id", *SNAP]], on="offre_id", validate="one_to_one")
    accesses = load_accesses(access_config, offers, network, manifest)
    replaced = set(accesses.offre_id)
    endpoints = oq.loc[~oq.offre_id.isin(replaced), ["offre_id", "geometry", *SNAP]].copy()
    endpoints["acces_id"], endpoints["noeud_osm"] = None, None
    for access in accesses.itertuples(index=False):
        row = {"offre_id": access.offre_id, "acces_id": access.acces_id, "noeud_osm": access.noeud_osm,
               "geometry": access.geometry, "statut": "ok", "raccord_de": None, "raccord_vers": None,
               "raccord_cle": None, "abscisse_raccord_m": 0., "temps_acces_s": 0., "distance_raccordement_m": 0.}
        endpoints = gpd.GeoDataFrame(pd.concat([endpoints, gpd.GeoDataFrame([row], crs=2154)], ignore_index=True), crs=2154)
    oq["via_acces_station"] = oq.offre_id.isin(replaced)
    oq["nb_acces"] = oq.offre_id.map(accesses.groupby("offre_id").size()).fillna(0).astype(int)
    oq["nb_acces_calculables"] = oq.nb_acces
    oq.loc[oq.via_acces_station, "statut"] = "ok"
    oq.loc[oq.via_acces_station, SNAP[1:]] = np.nan
    for col in ["raccord_de", "raccord_vers", "raccord_cle"]:
        oq[col] = pd.array(oq[col], dtype="Int64")
    return {"plan": plan, "manifest": manifest, "pop": pop, "offers": offers, "oq": oq,
            "accesses": accesses, "endpoints": endpoints.reset_index(drop=True),
            "tree": STRtree(endpoints.geometry.to_numpy()), "cached": cached}


def signature_for(args, context):
    files = {"plan": args.preparation / "plan_reseau_ara.json", "reseau": args.reseau / "construction_reseau_ara.json",
             "controle_reseau": args.reseau / "controle_independant.json", "raccordements": args.raccordements / "raccordements_qc.json"}
    for name in ["build_accessibilite_ara.py", "csr_walking.py", "population_groups_fast.py", "station_access.py", "check_raccordements_ara.py"]:
        files["code_" + name] = ROOT / "geo" / name
    if args.acces_stations is not None:
        files["acces_config"] = args.acces_stations
    return {"version": 1, "sha256": {k: sha256(p) for k, p in files.items()},
            "parametres": {"duree_s": 300, "vitesse_kmh": 4.5, "seuil_population": 20},
            "versions": {"numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__,
                         "geopandas": gpd.__version__, "pyarrow": pa.__version__},
            "tuiles": sorted(context["manifest"]["tuiles"])}


def init_worker(preparation, network, cache, access_config, output, signature):
    global CTX
    CTX = read_inputs(Path(preparation), Path(network), Path(cache), Path(access_config) if access_config else None)
    CTX.update(network=Path(network), output=Path(output), signature=signature,
               owners=CTX["pop"].groupby("tuile_id").indices)


def tile_receipt(output, tile, signature):
    directory = output / "tuiles" / tile
    if not directory.exists():
        return None
    r = json.loads((directory / "calcul_qc.json").read_text())
    if r["signature"] != signature or r["tuile_id"] != tile:
        raise ValueError(f"Checkpoint incompatible : {tile}")
    verify_files(directory, r["fichiers_sha256"])
    return r


def process_tile(tile):
    ctx = CTX
    start = time.monotonic()
    directory = ctx["network"] / tile
    qp = directory / "reseau_pieton_qc.json"
    qc = json.loads(qp.read_text())
    if (sha256(qp) != ctx["manifest"]["tuiles"][tile]["qc_sha256"]
            or qc["signature"] != ctx["manifest"]["signature"] or qc["etat"] != "valide"):
        raise ValueError(f"Tuile réseau incompatible : {tile}")
    verify_files(directory, qc["fichiers"])
    pop = ctx["pop"].iloc[ctx["owners"].get(tile, [])].reset_index(drop=True)
    candidates = ctx["tree"].query(pop.geometry.to_numpy(), predicate="dwithin", distance=375.000001)
    js = np.unique(candidates[1])
    targets = ctx["endpoints"].iloc[js].reset_index(drop=True)
    candidates[1] = np.searchsorted(js, candidates[1])
    needed = bool(candidates.size and (pop.statut.eq("ok").to_numpy()[candidates[0]] & targets.statut.eq("ok").to_numpy()[candidates[1]]).any())
    engine = None
    if needed:
        edges = pd.read_parquet(directory / "reseau_pieton_troncons.parquet", columns=["de", "vers", "cle", "longueur_m", "temps_pieton_s"])
        engine = CSRWalking(edges)
    pairs, pending = route_candidates(engine, pop, targets, candidates)
    destination = ctx["output"] / "tuiles" / tile
    destination.parent.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as tmp:
        stage = Path(tmp) / tile
        stage.mkdir()
        pq.write_table(pa.Table.from_pandas(pairs, schema=PAIR_SCHEMA, preserve_index=False), stage / "paires.parquet")
        pq.write_table(pa.Table.from_pandas(pending, schema=UNKNOWN_SCHEMA, preserve_index=False), stage / "indeterminees.parquet")
        receipt = {"tuile_id": tile, "signature": ctx["signature"], "population_points": len(pop),
                   "paires_candidates_acces": candidates.shape[1], "paires_accessibles": len(pairs),
                   "paires_indeterminees": len(pending), "reseau_qc_sha256": sha256(qp),
                   "duree_s": time.monotonic() - start,
                   "fichiers_sha256": {p.name: sha256(p) for p in stage.iterdir()}}
        (stage / "calcul_qc.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        if destination.exists():
            raise ValueError("Refus d'écraser un checkpoint")
        stage.rename(destination)
    return receipt


def regroup(context, args, signature):
    out = args.output_dir / "regroupement"
    if out.exists():
        report = json.loads((out / "groupes_qc.json").read_text())
        if report["signature"] != signature:
            raise ValueError("Regroupement incompatible")
        verify_files(out, report["fichiers_sha256"])
        return gpd.read_parquet(out / "groupes.parquet"), pd.read_parquet(out / "correspondance.parquet"), report["replis_sans_bord_commun"]
    cells = gpd.read_parquet(args.preparation / "entrees/carreaux_socio.parquet", columns=["id_carreau", "population", "geometry"])
    print("Regroupement régional des petits carreaux pour publication O1…", flush=True)
    groups, mapping, fallbacks = population_groups_fast(cells, 20)
    if (not mapping.id_carreau.is_unique or set(mapping.id_carreau) != set(context["pop"].id_carreau)
            or not math.isclose(float(groups.population.sum()), float(context["pop"].population.sum()), abs_tol=1e-6)):
        raise ValueError("Population perdue ou dupliquée dans le regroupement")
    with TemporaryDirectory(dir=args.output_dir) as tmp:
        stage = Path(tmp) / "groupes"
        stage.mkdir()
        groups.to_parquet(stage / "groupes.parquet", index=False)
        mapping.to_parquet(stage / "correspondance.parquet", index=False)
        write_json(stage / "groupes_qc.json", {"signature": signature, "groupes": len(groups),
                   "replis_sans_bord_commun": fallbacks, "fichiers_sha256": {p.name: sha256(p) for p in stage.iterdir()}})
        stage.rename(out)
    return groups, mapping, fallbacks


def aggregate_region(context, pairs, unknown, groups, mapping):
    """Même définition que le pilote ; sommes vectorisées à l'échelle régionale."""
    pop, offers, oq = context["pop"], context["offers"], context["oq"]
    modes = sorted(offers["type"].unique())
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", mode) for mode in modes):
        raise ValueError("Types d'offre non normalisés")
    linked = pairs.merge(offers.drop(columns="geometry"), on="offre_id", validate="many_to_one")
    counts = pd.DataFrame(index=pd.Index(pop.id_carreau, name="id_carreau"))
    counts["nb_options"] = linked.groupby("id_carreau").size().reindex(counts.index, fill_value=0)
    metrics = ["nb_options"]
    for mode in modes:
        column = f"nb_options_{mode}"
        counts[column] = linked.loc[linked["type"].eq(mode)].groupby("id_carreau").size().reindex(counts.index, fill_value=0)
        metrics.append(column)
    for unit, prefix in [("passages_par_jour", "passages_jour"), ("capacite", "capacite")]:
        for mode in modes:
            if not (offers.unite.eq(unit) & offers["type"].eq(mode)).any():
                continue
            subset = linked.loc[linked.unite.eq(unit) & linked["type"].eq(mode)]
            column = f"{prefix}_{mode}"
            counts[column] = subset.groupby("id_carreau").valeur.sum().reindex(counts.index, fill_value=0)
            counts.loc[counts.index.isin(subset.loc[subset.valeur.isna(), "id_carreau"]), column] = np.nan
            metrics.append(column)
    counts["calcul_complet"] = ~counts.index.isin(unknown.id_carreau) & counts.index.isin(pop.loc[pop.statut.eq("ok"), "id_carreau"])
    counts = counts.join(pop.set_index("id_carreau")[["population"]])
    measures = mapping.merge(counts.reset_index(), on="id_carreau", validate="one_to_one")
    grouped = measures.groupby("groupe_id", sort=False)
    complete = grouped.calcul_complet.all()
    evaluated = measures.population.where(measures.calcul_complet, 0).groupby(measures.groupe_id).sum()
    o1 = groups.copy()
    disclosed = o1.seuil_population_atteint
    done = o1.groupe_id.map(complete)
    o1["statut"] = np.where(~disclosed, "population_insuffisante", np.where(done, "ok", "partiel"))
    o1["population_evaluee"] = o1.groupe_id.map(evaluated).where(disclosed)
    for column in metrics:
        total = (measures[column] * measures.population).groupby(measures.groupe_id).sum()
        available = measures[column].notna().groupby(measures.groupe_id).all()
        value = (o1.groupe_id.map(total) / o1.population).where(disclosed & o1.groupe_id.map(available))
        suffix = "options" if column == "nb_options" else column.removeprefix("nb_")
        o1[f"{suffix}_par_habitant_min"] = value
        o1[f"{suffix}_par_habitant"] = value.where(done)
    o1["population"] = o1.population.where(disclosed)
    served = pairs.merge(pop[["id_carreau", "population"]], on="id_carreau", validate="many_to_one")
    o2 = offers.copy()
    o2["statut"] = np.where(o2.offre_id.isin(unknown.offre_id), "partiel", "ok")
    failed = set(oq.loc[oq.statut.ne("ok"), "offre_id"])
    o2.loc[o2.offre_id.isin(failed), "statut"] = "non_calcule"
    for column, values in [("population_desservie", served.groupby("offre_id").population.sum()),
                           ("nb_carreaux_desservis", served.groupby("offre_id").size())]:
        o2[column + "_min"] = o2.offre_id.map(values).fillna(0).where(o2.statut.ne("non_calcule"))
        o2[column] = o2[column + "_min"].where(o2.statut.eq("ok"))
    o2["nb_acces_pietons"] = o2.offre_id.map(oq.set_index("offre_id").nb_acces)
    o2["mode_arrivee"] = np.where(o2.nb_acces_pietons.gt(0), "un_des_acces_exterieurs", "point_source_raccorde")
    return o1, o2, counts


def assemble(context, args, signature, receipts):
    destination = args.output_dir / "resultats"
    if destination.exists():
        report = json.loads((destination / "accessibilite_qc.json").read_text())
        if report["signature"] != signature:
            raise ValueError("Résultats incompatibles")
        verify_files(destination, report["fichiers_sha256"])
        return report
    if sum(r["population_points"] for r in receipts.values()) != len(context["pop"]):
        raise ValueError("Population répartie sur les tuiles incomplète")
    pairs = pd.concat([pd.read_parquet(args.output_dir / "tuiles" / t / "paires.parquet") for t in sorted(receipts)], ignore_index=True)
    unknown = pd.concat([pd.read_parquet(args.output_dir / "tuiles" / t / "indeterminees.parquet") for t in sorted(receipts)], ignore_index=True)
    for frame in [pairs, unknown]:
        if (frame.duplicated(["id_carreau", "offre_id"]).any()
                or not set(frame.id_carreau).issubset(set(context["pop"].id_carreau))
                or not set(frame.offre_id).issubset(set(context["offers"].offre_id))):
            raise ValueError("Paires dupliquées ou identifiants inconnus")
    if (not pairs.merge(unknown, on=["id_carreau", "offre_id"]).empty
            or not np.isfinite(pairs.temps_pieton_s).all() or not pairs.temps_pieton_s.between(0, 300).all()):
        raise ValueError("Temps invalides ou paire à la fois connue et indéterminée")
    groups, mapping, fallbacks = regroup(context, args, signature)
    o1, o2, counts = aggregate_region(context, pairs, unknown, groups, mapping)
    # Recontrôler les sources et tous les checkpoints avant la publication globale.
    verified = read_inputs(args.preparation, args.reseau, args.raccordements, args.acces_stations)
    if signature_for(args, verified) != signature:
        raise ValueError("Sources ou code modifiés pendant le calcul")
    for tile in receipts:
        tile_receipt(args.output_dir, tile, signature)
    with TemporaryDirectory(dir=args.output_dir) as tmp:
        stage = Path(tmp) / "resultats"
        public, internal = stage / "publics", stage / "internes"
        public.mkdir(parents=True)
        internal.mkdir()
        for name, frame in [("gold_o1", o1), ("gold_o2", o2)]:
            frame.to_parquet(public / f"{name}.parquet", index=False)
            frame.drop(columns="geometry").to_csv(public / f"{name}.csv", index=False)
            frame.to_crs(4326).to_file(public / f"{name}.geojson", driver="GeoJSON")
        context["accesses"].to_parquet(public / "acces_stations.parquet", index=False)
        pairs.to_parquet(internal / "paires_accessibles.parquet", index=False)
        unknown.to_parquet(internal / "paires_indeterminees.parquet", index=False)
        mapping.to_parquet(internal / "correspondance_groupes.parquet", index=False)
        counts.reset_index().to_parquet(internal / "o1_carreaux_non_diffusables.parquet", index=False)
        context["pop"].to_parquet(internal / "raccordements_population.parquet", index=False)
        context["oq"].to_parquet(internal / "raccordements_offre.parquet", index=False)
        report = {"etat": "regional_termine", "date_utc": datetime.now(timezone.utc).isoformat(), "signature": signature,
                  "tuiles": len(receipts), "carreaux": len(context["pop"]), "population_source": float(context["pop"].population.sum()),
                  "offres": len(o2), "offres_par_type": {str(k): int(v) for k, v in o2["type"].value_counts().items()},
                  "paires_accessibles": len(pairs), "paires_indeterminees": len(unknown), "groupes_o1": len(o1),
                  "statuts_o1": {str(k): int(v) for k, v in o1.statut.value_counts().items()},
                  "statuts_o2": {str(k): int(v) for k, v in o2.statut.value_counts().items()},
                  "population_o1": float(o1.population.sum()), "replis_regroupement_sans_bord_commun": fallbacks,
                  "limites": ["Population fiscale Filosofi 2021 représentée au centre des carreaux, pas aux bâtiments.",
                              "Offre et population limitées aux livraisons ARA de l'équipe ; exhaustivité des réseaux non garantie ; aucun train livré.",
                              "Marche bidirectionnelle à 4,5 km/h, raccordements inclus, maximum 300 secondes.",
                              "Raccordement population maximum 150 m, offre maximum 50 m ; échecs conservés comme indéterminés.",
                              "Croix-Paquet : station atteinte dès l'un des deux accès extérieurs ; temps jusqu'au quai non modélisé.",
                              "O1 public regroupé à au moins 20 habitants ; dossiers internes et checkpoints non diffusables tels quels.",
                              "O2 compte chaque carreau une fois par station ; les populations des stations ne sont pas additionnables.",
                              "Les regroupements O1 peuvent traverser les limites des communes ; fréquences et capacités restent séparées."],
                  "fichiers_sha256": {str(p.relative_to(stage)): sha256(p) for p in stage.rglob("*") if p.is_file()}}
        write_json(stage / "accessibilite_qc.json", report)
        stage.rename(destination)
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    result.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    result.add_argument("--raccordements", type=Path, default=ROOT / "data/silver/raccordements_ara")
    access = result.add_mutually_exclusive_group()
    access.add_argument("--acces-stations", type=Path, default=ROOT / "geo/config/acces_stations.json")
    access.add_argument("--sans-acces-stations", action="store_true")
    result.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/accessibilite_ara")
    result.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    result.add_argument("--tuiles", nargs="+", help="Sous-ensemble de contrôle ; aucune livraison globale avant les 835 tuiles")
    result.add_argument("--reprendre", action="store_true")
    return result


def run(args):
    if args.sans_acces_stations:
        args.acces_stations = None
    context = read_inputs(args.preparation, args.reseau, args.raccordements, args.acces_stations)
    signature = signature_for(args, context)
    requested = sorted(set(args.tuiles or signature["tuiles"]))
    if not set(requested).issubset(set(signature["tuiles"])):
        raise ValueError("Tuile inconnue")
    if args.output_dir.exists() and not args.reprendre:
        raise ValueError("Le dossier existe : utiliser --reprendre pour vérifier les checkpoints")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = args.output_dir / "signature.json"
        if path.exists():
            if json.loads(path.read_text()) != signature:
                raise ValueError("Reprise refusée : code, paramètres ou sources différents")
        else:
            if args.reprendre and any(p.name != ".lock" for p in args.output_dir.iterdir()):
                raise ValueError("Dossier non vide sans signature de calcul")
            write_json(path, signature)
        receipts = {}
        for tile in signature["tuiles"]:
            receipt = tile_receipt(args.output_dir, tile, signature)
            if receipt is not None:
                receipts[tile] = receipt
        pending = [tile for tile in requested if tile not in receipts]
        print(f"Tuiles déjà contrôlées : {len(receipts)}/{len(signature['tuiles'])} ; à calculer : {len(pending)}", flush=True)

        def progress(state):
            write_json(args.output_dir / "progression.json", {"etat": state, "tuiles_terminees": len(receipts),
                       "tuiles_total": len(signature["tuiles"]), "paires_accessibles": sum(r["paires_accessibles"] for r in receipts.values()),
                       "paires_indeterminees": sum(r["paires_indeterminees"] for r in receipts.values()),
                       "date_utc": datetime.now(timezone.utc).isoformat()})

        progress("routage_en_cours")
        if pending:
            initargs = (args.preparation, args.reseau, args.raccordements, args.acces_stations, args.output_dir, signature)
            # spawn évite le socket local du forkserver, indisponible dans certains conteneurs.
            with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn"),
                                     initializer=init_worker, initargs=initargs) as executor:
                futures = {executor.submit(process_tile, tile): tile for tile in pending}
                for future in as_completed(futures):
                    tile = futures[future]
                    receipts[tile] = future.result()
                    if len(receipts) % 10 == 0 or all(t in receipts for t in requested):
                        print(f"Routage : {len(receipts)}/{len(signature['tuiles'])} tuiles ; {sum(r['paires_accessibles'] for r in receipts.values())} paires accessibles", flush=True)
                        progress("routage_en_cours")
        if len(receipts) != len(signature["tuiles"]):
            progress("partiel_reprise_possible")
            return None
        progress("aggregation_en_cours")
        report = assemble(context, args, signature, receipts)
        progress("regional_termine")
        print(json.dumps({k: report[k] for k in ["etat", "carreaux", "offres", "paires_accessibles", "paires_indeterminees", "groupes_o1", "statuts_o1", "statuts_o2"]}, ensure_ascii=False, indent=2), flush=True)
        return report


if __name__ == "__main__":
    run(parser().parse_args())
