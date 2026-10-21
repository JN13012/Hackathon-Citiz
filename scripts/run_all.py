"""Rejoue la chaîne bronze -> silver -> gold sans intervention manuelle.

Chaque rôle ajoute ses étapes dans STEPS, dans l'ordre des dépendances.
Usage : python scripts/run_all.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("Rôle 1 - table arrets (silver)", ["scripts/build_arrets.py", "--contours", "data/silver/contours.parquet"]),
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


def main():
    for label, cmd in STEPS:
        print(f"\n=== {label} ===")
        subprocess.run([sys.executable, *cmd], cwd=ROOT, check=True)
    print("\nChaîne terminée.")


if __name__ == "__main__":
    main()
