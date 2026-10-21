"""Contrôle régional des raccordements ; aucun trajet ni O1/O2 n'est calculé.

Utilise les GeoParquet contrôlés, une tuile principale par point et les marges
du réseau. Les composantes sont locales à une tuile, pas des îlots régionaux.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree, distance, line_interpolate_point, line_locate_point

ROOT = Path(__file__).resolve().parent.parent
NODE_FILE = "reseau_pieton_noeuds.parquet"
EDGE_FILE = "reseau_pieton_troncons.parquet"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_files(directory, expected):
    for name, digest in expected.items():
        path = directory / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Fichier absent ou empreinte différente : {path}")


def read_points(path, key, tile_m):
    frame = gpd.read_parquet(path)
    if frame.crs is None or frame.crs.to_epsg() != 2154:
        raise ValueError(f"Projection EPSG:2154 requise : {path}")
    if (frame[key].isna().any() or not frame[key].is_unique
            or frame[key].astype(str).str.strip().eq("").any()):
        raise ValueError(f"Identifiants absents ou dupliqués : {path}")
    if (frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid).any() or not frame.geom_type.eq("Point").all():
        raise ValueError(f"Points invalides : {path}")
    xy = np.column_stack([frame.geometry.x, frame.geometry.y])
    if not np.isfinite(xy).all():
        raise ValueError(f"Coordonnées non finies : {path}")
    ix, iy = np.floor(xy / tile_m).astype(np.int64).T
    expected = [f"x{x}_y{y}" for x, y in zip(ix, iy)]
    if not np.array_equal(frame.tuile_id.to_numpy(), expected):
        raise ValueError(f"Affectation à une tuile principale incorrecte : {path}")
    return frame


def snap_points(points, nodes, edges, zone, speed_kmh, budget_s):
    """Projection sur les segments physiques, égalités départagées par (de,vers,clé).

    La paire d'arcs aller-retour ne constitue pas une ambiguïté. Les égalités
    entre segments physiques ou composantes distinctes restent dans le rapport.
    """
    if points.empty or edges.empty or nodes.empty:
        raise ValueError("Points et réseau non vides requis")
    if not nodes.noeud_id.is_unique or edges.duplicated(["de", "vers", "cle"]).any():
        raise ValueError("Identifiants de réseau dupliqués")
    # Chaque clé régionale désigne un segment OSM physique et ses deux arcs.
    physical = edges.sort_values(["de", "vers", "cle"]).drop_duplicates("cle").reset_index(drop=True)
    if len(edges) != 2 * len(physical):
        raise ValueError("Deux arcs par segment physique attendus")
    components = nodes.set_index("noeud_id").composante_id
    c_from = physical.de.map(components)
    c_to = physical.vers.map(components)
    if c_from.isna().any() or c_to.isna().any() or not c_from.equals(c_to):
        raise ValueError("Références de nœuds ou composantes incohérentes")
    if (physical.geometry.isna() | physical.geometry.is_empty | ~physical.geometry.is_valid).any() or not physical.geom_type.eq("LineString").all():
        raise ValueError("Segments invalides")
    component_sizes = components.value_counts()
    major = int(component_sizes.idxmax())
    lines = physical.geometry.to_numpy()
    pairs, distances = STRtree(lines).query_nearest(points.geometry.to_numpy(), all_matches=True, return_distance=True)
    candidates = pd.DataFrame({"point": pairs[0], "segment": pairs[1], "distance": distances})
    candidates["composante"] = c_from.to_numpy()[pairs[1]]
    chosen = candidates.sort_values(["point", "segment"]).drop_duplicates("point").set_index("point").reindex(range(len(points)))
    if chosen.segment.isna().any():
        raise ValueError("Recherche du segment le plus proche incomplète")
    indices = chosen.segment.to_numpy(dtype=int)
    selected = physical.iloc[indices]
    selected_lines = lines[indices]
    positions = line_locate_point(selected_lines, points.geometry.to_numpy())
    snapped = line_interpolate_point(selected_lines, positions)
    distances = distance(points.geometry.to_numpy(), snapped)
    if not np.isfinite(distances).all() or not np.isfinite(positions).all():
        raise ValueError("Projection non finie")
    result = points.reset_index(drop=True).copy()
    result["distance_raccordement_m"] = distances
    result["temps_acces_s"] = distances / (speed_kmh / 3.6)
    result["statut"] = np.where(distances > result.seuil_raccordement_m, "raccordement_trop_loin",
                                np.where(result.temps_acces_s >= budget_s, "budget_acces_epuise", "ok"))
    for source, target in [("de", "raccord_de"), ("vers", "raccord_vers"), ("cle", "raccord_cle")]:
        result[target] = selected[source].to_numpy()
    result["abscisse_raccord_m"] = positions
    result["raccord_x"] = np.array([p.x for p in snapped])
    result["raccord_y"] = np.array([p.y for p in snapped])
    result["composante_locale_id"] = c_from.to_numpy()[indices]
    result["composante_locale_noeuds"] = result.composante_locale_id.map(component_sizes)
    result["composante_locale_minoritaire"] = result.composante_locale_id.ne(major)
    result["nb_troncons_equidistants"] = candidates.groupby("point").size().reindex(range(len(points))).to_numpy()
    result["nb_composantes_equidistantes"] = candidates.groupby("point").composante.nunique().reindex(range(len(points))).to_numpy()
    # Si le plus proche dépasse le bord de la fenêtre, la distance au réseau
    # régional complet n'est pas certifiée ; le rejet au seuil reste valide.
    boundary_distance = distance(points.geometry.to_numpy(), zone.boundary)
    if not points.geometry.covered_by(zone).all() or (boundary_distance <= result.seuil_raccordement_m).any():
        raise ValueError("Marge insuffisante pour certifier les raccordements au seuil")
    result["recherche_bornee_par_marge"] = distances >= boundary_distance
    result["entree_metro_a_verifier"] = result["type"].eq("metro")
    result["a_examiner"] = (result.statut.ne("ok") | result.composante_locale_minoritaire
                            | result.nb_composantes_equidistantes.gt(1) | result.entree_metro_a_verifier)
    return result


def summarize(frame):
    d = frame.distance_raccordement_m
    report = {"points": len(frame), "statuts": frame.statut.value_counts().to_dict(),
              "a_examiner": int(frame.a_examiner.sum()),
              "composante_locale_minoritaire": int(frame.composante_locale_minoritaire.sum()),
              "egalite_plusieurs_composantes": int(frame.nb_composantes_equidistantes.gt(1).sum()),
              "entrees_metro_a_verifier": int(frame.entree_metro_a_verifier.sum()),
              "recherche_bornee_par_marge": int(frame.recherche_bornee_par_marge.sum()),
              "distance_m": {"mediane": float(d.median()), "p95": float(d.quantile(.95)),
                             "p99": float(d.quantile(.99)), "maximum_dans_fenetre": float(d.max())}}
    if "population" in frame:
        report["population_totale"] = float(frame.population.sum())
        report["population_raccordement_ok"] = float(frame.loc[frame.statut.eq("ok"), "population"].sum())
        report["population_raccordement_non_ok"] = float(frame.loc[frame.statut.ne("ok"), "population"].sum())
    return report


def build(args):
    if args.output_dir.exists():
        raise ValueError("Refus d'écraser un contrôle existant ; choisir un autre --output-dir")
    for key in ["max_raccord_population_m", "max_raccord_offre_m"]:
        value = getattr(args, key)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Seuil invalide : {key}")
    plan_path = args.preparation / "plan_reseau_ara.json"
    network_path = args.reseau / "construction_reseau_ara.json"
    independent_path = args.reseau / "controle_independant.json"
    input_hashes = {"plan": sha256(plan_path), "reseau": sha256(network_path), "controle_independant": sha256(independent_path)}
    plan = json.loads(plan_path.read_text())
    network = json.loads(network_path.read_text())
    independent = json.loads(independent_path.read_text())
    if (network["etat"] != "regional_termine" or independent["etat"] != "valide"
            or independent["manifest_sha256"] != input_hashes["reseau"]
            or network["signature"]["plan_sha256"] != input_hashes["plan"]
            or network["tuiles_validees"] != plan["tuiles"] or independent["tuiles_controlees"] != plan["tuiles"]):
        raise ValueError("Préparation, réseau régional et contrôle indépendant incompatibles")
    verify_files(args.preparation, plan["fichiers"])
    speed = plan["parametres"]["vitesse_kmh"]
    budget = plan["parametres"]["duree_s"]
    if not math.isfinite(speed) or speed <= 0 or not math.isfinite(budget) or budget <= 0 or network["signature"]["vitesse_kmh"] != speed:
        raise ValueError("Vitesse ou durée incohérente")
    inputs = args.preparation / "entrees"
    population = read_points(inputs / "gold_candidates.parquet", "id_carreau", plan["parametres"]["taille_tuile_m"])
    offers = read_points(inputs / "gold_offre.parquet", "offre_id", plan["parametres"]["taille_tuile_m"])
    if len(population) != plan["carreaux"] or len(offers) != plan["offres"]:
        raise ValueError("Effectifs des points incompatibles avec le plan")
    tiles = sorted(set(population.tuile_id) | set(offers.tuile_id))
    if not set(tiles).issubset(network["tuiles"]):
        raise ValueError("Tuile principale sans réseau")
    if args.tuiles:
        if not set(args.tuiles).issubset(tiles):
            raise ValueError("Tuile inconnue ou sans origine")
        tiles = sorted(set(args.tuiles))
    pop_parts, offer_parts, receipts = [], [], {}
    pop_groups, offer_groups = population.groupby("tuile_id").indices, offers.groupby("tuile_id").indices
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir.parent, prefix=".raccordements_") as temp:
        stage = Path(temp) / "livraison"
        stage.mkdir()
        for i, tile in enumerate(tiles, 1):
            directory = args.reseau / tile
            qc_path = directory / "reseau_pieton_qc.json"
            qc_hash = sha256(qc_path)
            qc = json.loads(qc_path.read_text())
            if (qc_hash != network["tuiles"][tile]["qc_sha256"] or qc["signature"] != network["signature"]
                    or qc["tuile_id"] != tile or qc["etat"] != "valide" or qc["modele_marche"] != "bidirectionnel"):
                raise ValueError(f"Reçu de tuile incohérent : {tile}")
            verify_files(directory, qc["fichiers"])
            nodes = gpd.read_parquet(directory / NODE_FILE, columns=["noeud_id", "composante_id", "geometry"])
            edges = gpd.read_parquet(directory / EDGE_FILE, columns=["de", "vers", "cle", "geometry"])
            zone_frame = gpd.read_parquet(directory / "zone_requete.parquet")
            if any(f.crs is None or f.crs.to_epsg() != 2154 for f in [nodes, edges, zone_frame]) or len(zone_frame) != 1:
                raise ValueError(f"Projection ou fenêtre incorrecte : {tile}")
            zone = zone_frame.geometry.iloc[0]
            pop = population.iloc[pop_groups.get(tile, [])].copy()
            offer = offers.iloc[offer_groups.get(tile, [])].copy()
            pop["categorie"] = "population"
            offer["categorie"] = "offre"
            pop["type"] = "population"
            pop["seuil_raccordement_m"] = args.max_raccord_population_m
            offer["seuil_raccordement_m"] = args.max_raccord_offre_m
            combined = gpd.GeoDataFrame(pd.concat([pop, offer], ignore_index=True), crs=2154)
            result = snap_points(combined, nodes, edges, zone, speed, budget)
            if len(pop):
                pop_parts.append(result.loc[result.categorie.eq("population")].drop(columns=[c for c in offer.columns if c not in pop.columns]))
            if len(offer):
                offer_parts.append(result.loc[result.categorie.eq("offre")].drop(columns=[c for c in pop.columns if c not in offer.columns]))
            verify_files(directory, qc["fichiers"])
            if sha256(qc_path) != qc_hash:
                raise ValueError(f"Tuile modifiée pendant le contrôle : {tile}")
            receipts[tile] = qc_hash
            if i == 1 or i % 25 == 0 or i == len(tiles):
                print(f"Raccordements : {i}/{len(tiles)} tuiles ; {tile}", flush=True)
        outputs = {}
        for category, parts, key, original in [("population", pop_parts, "id_carreau", population),
                                                ("offre", offer_parts, "offre_id", offers)]:
            if not parts:
                continue
            frame = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=2154).sort_values(key).reset_index(drop=True)
            expected_ids = set(original.loc[original.tuile_id.isin(tiles), key])
            if not frame[key].is_unique or set(frame[key]) != expected_ids:
                raise ValueError(f"Points omis ou comptés plusieurs fois : {category}")
            if frame.geometry.isna().any() or frame.statut.isna().any():
                raise ValueError("Diagnostic incomplet")
            frame.to_parquet(stage / f"raccordements_{category}.parquet", index=False)
            review = frame.loc[frame.a_examiner].copy()
            review["point_x"], review["point_y"] = review.geometry.x, review.geometry.y
            review.drop(columns="geometry").to_csv(stage / f"a_examiner_{category}.csv", index=False)
            # Couche de points interne, utilisable dans QGIS ; petits effectifs
            # INSEE conservés, à agréger avant toute publication grand public.
            (stage / f"a_examiner_{category}.geojson").write_text(review.to_crs(4326).to_json(drop_id=True), encoding="utf-8")
            outputs[category] = frame
        report = {"execution_utc": datetime.now(timezone.utc).isoformat(),
                  "etat": "controle_regional_termine" if not args.tuiles else "controle_partiel_termine",
                  "sources_sha256": input_hashes, "script_sha256": sha256(Path(__file__)),
                  "tuiles_controlees": len(tiles), "tuiles_sans_origine_non_parcourues": plan["tuiles"] - plan["tuiles_avec_origines"],
                  "recus_tuiles_sha256": receipts,
                  "parametres": {"vitesse_kmh": speed, "duree_s": budget, "seuil_population_m": args.max_raccord_population_m,
                                 "seuil_offre_m": args.max_raccord_offre_m},
                  "resultats": {k: summarize(f) for k, f in outputs.items()},
                  "offre_par_type": {k: summarize(f) for k, f in outputs.get("offre", pd.DataFrame(columns=["type"])).groupby("type")},
                  "o1_o2_calcules": False,
                  "limites": ["ok signifie raccordement géométrique dans le seuil ; aucun trajet de 300 secondes n'est démontré.",
                              "Raccord droit hypothétique ; murs, accès privés au bâti, étages, pente et entrées de métro non résolus.",
                              "Les points de métro sont tous signalés pour vérifier la position des entrées.",
                              "Composantes et tailles locales à chaque fenêtre ; minoritaire ne prouve pas un isolement régional.",
                              "Distance au-delà de la marge non garantie comme minimum régional ; les rejets au seuil restent valides.",
                              "Offre limitée aux réseaux livrés ; les trains et vélos sans station ne sont pas inclus.",
                              "Fichiers internes avec petits effectifs de population ; agréger avant publication grand public."]}
        report["fichiers_sha256"] = {f.name: sha256(f) for f in sorted(stage.iterdir())}
        (stage / "raccordements_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_files(args.preparation, plan["fichiers"])
        for path, key in [(plan_path, "plan"), (network_path, "reseau"), (independent_path, "controle_independant")]:
            if sha256(path) != input_hashes[key]:
                raise ValueError("Entrée modifiée pendant le contrôle")
        # Aucun dossier final partiellement écrit ; les sources restent intactes.
        if args.output_dir.exists():
            raise ValueError("Le dossier de sortie est apparu pendant le contrôle")
        stage.rename(args.output_dir)
    print(json.dumps(report["resultats"], ensure_ascii=False, indent=2), flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preparation", type=Path, default=ROOT / "data/silver/preparation_reseau_ara")
    ap.add_argument("--reseau", type=Path, default=ROOT / "data/silver/reseau_ara_bidirectionnel")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/silver/raccordements_ara")
    ap.add_argument("--max-raccord-population-m", type=float, default=150)
    ap.add_argument("--max-raccord-offre-m", type=float, default=50)
    ap.add_argument("--tuiles", nargs="+", help="Contrôle partiel explicite, sans statut régional")
    return ap


if __name__ == "__main__":
    build(parser().parse_args())
