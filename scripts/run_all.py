"""Rejoue la chaîne bronze -> silver -> gold sans intervention manuelle.

Chaque rôle ajoute ses étapes dans STEPS, dans l'ordre des dépendances.
Usage : python scripts/run_all.py [--territoire ara|france]
  ara (défaut) : Auvergne-Rhône-Alpes, tables dans data/silver et data/gold ;
  france : France métropolitaine, Corse comprise, tables dans data/france/ (les résultats ARA ne sont
           pas touchés) : téléchargements, nettoyage, réseau piéton et accès à 5 min des 13 régions,
           jusqu'aux tables prêtes pour le modèle (environ 10 h de calcul, voir docs/donnees_france.md).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("Rôle 2 - contours IGN (silver)",                ["geo/download_contours_ign.py"]),
    ("Rôle 1 - table arrets (silver)", ["scripts/build_arrets.py", "--contours", "{silver}/contours.parquet"]),
    # ("Rôle 2 - réseau piéton et contours (silver)", ["geo/build_reseau.py"]),
    ("Rôle 3 - carreaux_socio (silver)",            ["model/build_carreaux.py"]),
    ("Rôle 4 - stations_partagees (silver)",          ["optim/build_offre_partagee.py"]),
    ("Rôle 4 - projections (silver)",                 ["optim/build_projections.py"]),
    ("Rôle 4 - couts (silver)",                       ["optim/build_couts.py"]),
    ("Rôle 5 - ratios_co2 (silver), contrôle des sources", ["restitution/build_ratios_co2.py"]),
    ("Rôle 1 - gold_offre, gold_candidates",        ["scripts/build_gold.py"]),
    ("Contrôle des tables transport",             ["scripts/controle_qualite.py"]),
    # ("Rôle 2 - isochrones, O1, O2, couverture",     ["geo/build_isochrones.py"]),
    # ("Rôle 3 - features, modèle, scores",           ["model/train.py"]),
    ("Rôle 4 - optimiseur, liste priorisée (gold)",   ["optim/optimise.py"]),
]

# France métropolitaine : mêmes nettoyages, sources nationales, puis réseau piéton région par région.
# Le modèle et l'optimiseur se lancent ensuite, sur les tables produites.
FRANCE = "data/france/silver/contours.parquet"
FRANCE_STEPS = [
    ("Rôle 2 - OSM France métropolitaine (bronze)",   ["geo/download_osm_ara.py", "--france"]),
    ("Rôle 2 - contours IGN des 13 régions (silver)", ["geo/download_contours_ign.py"]),
    ("Rôle 1 - table arrets (silver)",                ["scripts/build_arrets.py", "--contours", FRANCE, "--tolerer-echecs"]),
    ("Rôle 3 - carreaux_socio (silver)",              ["model/build_carreaux.py", "--contours-iris", "data/bronze/ign/iris_wfs_france.gpkg"]),
    ("Rôle 4 - stations_partagees (silver)",          ["optim/build_offre_partagee.py", "--contours", FRANCE]),
    ("Rôle 4 - projections (silver)",                 ["optim/build_projections.py", "--contours", FRANCE]),
    ("Rôle 4 - couts (silver)",                       ["optim/build_couts.py"]),
    ("Rôle 5 - ratios_co2 (silver), contrôle des sources", ["restitution/build_ratios_co2.py"]),
    ("Rôle 1 - gold_offre, gold_candidates",          ["scripts/build_gold.py"]),
    ("Contrôle des tables transport",                 ["scripts/controle_qualite.py"]),
    ("Rôle 2 - réseau piéton, accès à 5 min et couverture des 13 régions, assemblage (gold)",
     ["bash", "scripts/run_regions_france.sh"]),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--territoire", default=os.environ.get("TERRITOIRE", "ara"), choices=["ara", "france"])
    args = ap.parse_args([] if argv is None else argv)
    env = {**os.environ, "TERRITOIRE": args.territoire}  # lu par optim/territoire.py dans chaque script
    silver = "data/silver" if args.territoire == "ara" else f"data/{args.territoire}/silver"
    print(f"Territoire : {args.territoire}")
    env["PYTHON"] = sys.executable  # interpréteur des scripts lancés par run_regions_france.sh
    for label, cmd in (STEPS if args.territoire == "ara" else FRANCE_STEPS):
        print(f"\n=== {label} ===")
        cmd = [c.format(silver=silver) for c in cmd]
        subprocess.run(cmd if cmd[0] == "bash" else [sys.executable, *cmd], cwd=ROOT, check=True, env=env)
    print("\nChaîne terminée.")


if __name__ == "__main__":
    main(sys.argv[1:])
