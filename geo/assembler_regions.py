"""Assemble les résultats régionaux (réseau piéton, accès à 5 min, couverture) en tables France.

Chaque carreau de population appartient à une seule région (decouper_region.py) : les tables par
carreau ou par candidat se concatènent sans double compte. Une offre proche d'une limite peut être
calculée dans deux régions (marge d'offre) : gold_o2 garde, pour chaque offre, la ligne de la région
où elle dessert le plus d'habitants.

Entrées : data/france/gold/regions/<code>/{accessibilite,couverture}/resultats/
Sorties (data/france/gold/) : gold_couverture, candidats_couverture, couverture_indeterminee,
gold_o1, gold_o2, accessibilite/resultats/internes/paires_accessibles (+ paires_indeterminees),
assemblage_regions_qc.json. Refuse d'assembler si une région manque, sauf --partiel.
Usage : TERRITOIRE=france python geo/assembler_regions.py [--partiel]
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "optim"))
import territoire as T  # noqa: E402

REGIONS = T.GOLD / "regions"
# table de sortie -> (étape, chemin dans resultats/, clé d'unicité)
TABLES = {
    "gold_couverture": ("couverture", "gold_couverture.parquet", ["id_candidat", "id_carreau"]),
    "candidats_couverture": ("couverture", "candidats_couverture.parquet", ["id_candidat"]),
    "couverture_indeterminee": ("couverture", "couverture_indeterminee.parquet", None),
    "gold_o1": ("accessibilite", "publics/gold_o1.parquet", None),
    "gold_o2": ("accessibilite", "publics/gold_o2.parquet", None),
    "paires_accessibles": ("accessibilite", "internes/paires_accessibles.parquet", ["id_carreau", "offre_id"]),
    "paires_indeterminees": ("accessibilite", "internes/paires_indeterminees.parquet", None),
}


def read(path):
    try:
        return gpd.read_parquet(path)
    except ValueError:  # table sans géométrie
        return pd.read_parquet(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partiel", action="store_true", help="assembler les régions terminées seulement (essai)")
    args = ap.parse_args(argv)
    if T.NOM == "ara":
        ap.error("à lancer avec TERRITOIRE=france")
    done = [c for c in T.CODES if (REGIONS / c / "couverture/resultats/couverture_qc.json").exists()
            and (REGIONS / c / "accessibilite/resultats/accessibilite_qc.json").exists()]
    missing = sorted(set(T.CODES) - set(done))
    if missing and not args.partiel:
        raise SystemExit(f"Régions non terminées : {', '.join(missing)} (--partiel pour un essai)")
    report = {"execution_utc": datetime.now(timezone.utc).isoformat(), "regions": done,
              "regions_manquantes": missing, "etat": "partiel" if missing else "france_complete", "tables": {}}
    out = {}
    for name, (step, rel, key) in TABLES.items():
        frames = []
        for code in done:
            frame = read(REGIONS / code / step / "resultats" / rel)
            frame["region"] = code
            frames.append(frame)
        table = pd.concat(frames, ignore_index=True)
        if isinstance(frames[0], gpd.GeoDataFrame):
            table = gpd.GeoDataFrame(table, geometry=frames[0].geometry.name, crs=frames[0].crs)
        if key and table.duplicated(key).any():
            raise SystemExit(f"{name} : {int(table.duplicated(key).sum())} doublons de {key} entre régions")
        if name == "gold_o2":  # offre frontalière : la région où elle dessert le plus d'habitants
            pop = next((c for c in table.columns if c.startswith("population")), None)
            n = len(table)
            table = table.sort_values(pop, ascending=False) if pop else table
            table = table.drop_duplicates("offre_id").sort_index()
            report["offres_frontalieres_dedoublonnees"] = n - len(table)
        out[name] = table
        report["tables"][name] = len(table)
    T.GOLD.mkdir(parents=True, exist_ok=True)
    internes = T.ACCESSIBILITE / "resultats" / "internes"
    internes.mkdir(parents=True, exist_ok=True)
    for name, table in out.items():
        target = internes / f"{name}.parquet" if name.startswith("paires") else T.GOLD / f"{name}.parquet"
        table.to_parquet(target, index=False)
    cand = out["candidats_couverture"]
    report["candidats_utilisables"] = int(cand["utilisable_optimiseur"].sum()) if "utilisable_optimiseur" in cand else None
    (T.GOLD / "assemblage_regions_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
