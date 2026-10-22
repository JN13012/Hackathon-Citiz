"""Contrôle indépendant d'un réseau piéton construit par tuiles (build_reseau_ara.py).

Relit les GeoParquet de chaque tuile sans passer par le code du constructeur et écrit
`controle_independant.json` dans le dossier du réseau, exigé par check_raccordements_ara.py,
build_accessibilite_ara.py et build_couverture.py. Contrôles, comme pour l'ARA
(docs/execution_reseau_ara.md) :
- tuiles du bilan toutes présentes, empreintes du rapport de tuile et de ses fichiers conformes ;
- identifiants de nœuds et d'arcs uniques, géométries présentes et valides ;
- extrémités des arcs : nœuds existants, à moins de 1 mm des points de la géométrie ;
- longueurs positives, temps = longueur / vitesse ;
- marche bidirectionnelle : chaque arc a un retour de même coût.

Usage : python geo/controle_reseau.py --preparation <dossier> --reseau <dossier> [--workers 4]
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import shapely

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from geo.prepare_reseau_ara import sha256  # noqa: E402


def check_tile(directory, qc_sha256, speed):
    """Anomalies d'une tuile (liste vide si tout est conforme) et effectifs."""
    problems = []
    qc_path = directory / "reseau_pieton_qc.json"
    if not qc_path.exists():
        return directory.name, ["dossier ou rapport de tuile absent"], {}
    if sha256(qc_path) != qc_sha256:
        problems.append("rapport de tuile différent du bilan")
    report = json.loads(qc_path.read_text())
    for name, digest in report.get("fichiers", {}).items():
        path = directory / name
        if not path.exists() or sha256(path) != digest:
            problems.append(f"fichier absent ou modifié : {name}")
    nodes = gpd.read_parquet(directory / "reseau_pieton_noeuds.parquet")
    edges = gpd.read_parquet(directory / "reseau_pieton_troncons.parquet")
    if nodes.crs is None or nodes.crs.to_epsg() != 2154 or (len(edges) and edges.crs.to_epsg() != 2154):
        problems.append("projection différente d'EPSG:2154")
    if not nodes.noeud_id.is_unique:
        problems.append("identifiants de nœuds dupliqués")
    if edges.duplicated(["de", "vers", "cle"]).any():
        problems.append("identifiants d'arcs dupliqués")
    for label, frame in (("nœuds", nodes), ("arcs", edges)):
        geoms = frame.geometry.to_numpy()
        if len(frame) and (shapely.is_missing(geoms) | shapely.is_empty(geoms) | ~shapely.is_valid(geoms)).any():
            problems.append(f"géométries de {label} absentes, vides ou invalides")
    if len(edges):
        xy = nodes.set_index("noeud_id").geometry
        known = edges.de.isin(xy.index) & edges.vers.isin(xy.index)
        if not known.all():
            problems.append(f"{int((~known).sum())} arcs vers des nœuds absents")
        e = edges.loc[known]
        line = e.geometry.to_numpy()
        far = (shapely.distance(shapely.get_point(line, 0), xy.reindex(e.de).to_numpy()) > 0.001) | \
              (shapely.distance(shapely.get_point(line, -1), xy.reindex(e.vers).to_numpy()) > 0.001)
        if far.any():
            problems.append(f"{int(far.sum())} extrémités d'arcs éloignées de leurs nœuds")
        length, time = edges.longueur_m.to_numpy(), edges.temps_pieton_s.to_numpy()
        if not (np.isfinite(length).all() and (length > 0).all()):
            problems.append("longueurs absentes, non finies ou nulles")
        if not np.allclose(time, length / (speed / 3.6), rtol=0, atol=1e-6):
            problems.append("temps de marche incohérents avec les longueurs")
        cost = edges.groupby(["de", "vers"]).temps_pieton_s.min()
        back = cost.reindex(list(zip(cost.index.get_level_values(1), cost.index.get_level_values(0))))
        missing = np.isnan(back.to_numpy())
        if missing.any():
            problems.append(f"{int(missing.sum())} arcs sans retour")
        if (np.abs(back.to_numpy()[~missing] - cost.to_numpy()[~missing]) > 1e-6).any():
            problems.append("retours de coût différent")
    return directory.name, problems, {"noeuds": len(nodes), "arcs": len(edges)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preparation", type=Path, required=True)
    ap.add_argument("--reseau", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    plan_path, manifest_path = args.preparation / "plan_reseau_ara.json", args.reseau / "construction_reseau_ara.json"
    plan, manifest = json.loads(plan_path.read_text()), json.loads(manifest_path.read_text())
    speed = plan["parametres"]["vitesse_kmh"]
    if manifest["etat"] != "regional_termine" or manifest["signature"]["plan_sha256"] != sha256(plan_path):
        raise SystemExit("Réseau incomplet ou construit sur un autre plan : contrôle refusé")
    if not math.isfinite(speed) or speed <= 0:
        raise SystemExit("Vitesse invalide dans le plan")
    tiles = manifest["tuiles"]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(check_tile, [args.reseau / t for t in tiles],
                                [tiles[t]["qc_sha256"] for t in tiles], [speed] * len(tiles), chunksize=8))
    anomalies = {tile: problems for tile, problems, _ in results if problems}
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(),
        "etat": "valide" if not anomalies and len(results) == plan["tuiles"] else "invalide",
        "manifest_sha256": sha256(manifest_path), "plan_sha256": sha256(plan_path),
        "code": {"chemin": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "tuiles_planifiees": plan["tuiles"], "tuiles_controlees": len(results),
        "copies_noeuds": sum(r[2].get("noeuds", 0) for r in results),
        "copies_arcs": sum(r[2].get("arcs", 0) for r in results),
        "controles": ["empreintes", "identifiants uniques", "géométries valides", "références et extrémités des arcs",
                      "longueurs positives", "temps = longueur / vitesse", "retour de même coût"],
        "anomalies": anomalies,
    }
    (args.reseau / "controle_independant.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Contrôle {report['etat']} : {len(results)}/{plan['tuiles']} tuiles, {len(anomalies)} avec anomalies")
    return 0 if report["etat"] == "valide" else 1


if __name__ == "__main__":
    sys.exit(main())
