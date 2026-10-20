"""Rôle 5 - table silver `ratios_co2` (une ligne = un ratio) et contrôle des sources de l'équipe.

1. Lit restitution/ratios_co2.yaml (enquête ADEME autopartage 2022, Impact CO₂ de l'ADEME),
   vérifie que chaque ratio a une source et une page, et que les facteurs d'émission correspondent
   au relevé Impact CO₂ gardé en bronze (data/bronze/ademe/impactco2_transport_1km.json).
2. Contrôle du dépôt : chaque table de data/silver et data/gold figure dans docs/dictionnaire_donnees.md,
   et chaque table du dictionnaire attendue en silver existe.

Sorties :
- data/silver/ratios_co2.parquet   ratio, valeur, unite, source, page (+ min, max, url, note)
- data/silver/ratios_co2_qc.txt    rapport qualité, dont le CO₂ évité par une voiture en autopartage
Usage : python restitution/build_ratios_co2.py
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
SPEC = ROOT / "restitution" / "ratios_co2.yaml"
DICTIONARY = ROOT / "docs" / "dictionnaire_donnees.md"
IMPACT_IDS = {"facteur_voiture_thermique": 4, "facteur_voiture_electrique": 5, "facteur_bus_thermique": 9,
              "facteur_tramway": 10, "facteur_velo_electrique": 8, "facteur_velo_mecanique": 7}
COLUMNS = ["ratio", "valeur", "unite", "source", "page", "min", "max", "titre_source", "url", "note"]


def load():
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    sources = spec["sources"]
    df = pd.DataFrame(spec["ratios"]).reindex(columns=[c for c in COLUMNS if c not in ("titre_source", "url")])
    df["titre_source"] = df["source"].map(lambda s: sources.get(s, {}).get("titre"))
    df["url"] = df["source"].map(lambda s: sources.get(s, {}).get("url"))
    for col in ("valeur", "min", "max"):
        df[col] = pd.to_numeric(df[col])
    df["page"] = df["page"].astype("string")
    return df[COLUMNS], sources


def check_ratios(df, sources):
    errors = []
    def check(mask, msg):
        errors.extend(f"{r} : {msg}" for r in df.loc[mask, "ratio"])
    check(df["ratio"].duplicated(), "ratio en double")
    check(df["valeur"].isna(), "valeur manquante")
    check(~df["source"].isin(list(sources) + ["hypothèse"]), "source inconnue")
    check((df["source"] != "hypothèse") & df["page"].isna(), "source sans page")
    has_range = df["min"].notna() | df["max"].notna()
    check(has_range & ~((df["min"] <= df["valeur"]) & (df["valeur"] <= df["max"])), "il faut min <= valeur <= max")
    # facteurs d'émission : identiques au relevé Impact CO₂ conservé en bronze (arrondi à 4 décimales)
    raw = ROOT / sources["impact_co2"]["fichier"]
    if raw.exists():
        api = {x["id"]: x["value"] for x in json.loads(raw.read_text())["data"]}
        for ratio, i in IMPACT_IDS.items():
            v = df.loc[df["ratio"] == ratio, "valeur"]
            if len(v) and i in api and abs(v.iloc[0] - api[i]) > 5e-5:
                errors.append(f"{ratio} : {v.iloc[0]} différent d'Impact CO₂ ({api[i]:.4f})")
    else:
        errors.append(f"relevé Impact CO₂ absent : {raw}")
    return errors


def check_dictionary():
    """Tables produites mais absentes du dictionnaire, et tables silver du dictionnaire non produites."""
    text = DICTIONARY.read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", text, flags=re.M))
    silver_section = text.split("## Tables gold")[0]
    expected_silver = set(re.findall(r"^\| `([a-z0-9_]+)` \|", silver_section, flags=re.M))
    produced = {p.stem: p.parent.name for d in (SILVER, GOLD) if d.exists() for p in d.glob("*.parquet")}
    undocumented = sorted(t for t in produced if t not in documented)
    missing = sorted(t for t in expected_silver if t not in produced)
    return produced, undocumented, missing


def main():
    df, sources = load()
    errors = check_ratios(df, sources)
    SILVER.mkdir(parents=True, exist_ok=True)
    if not errors:
        df.to_parquet(SILVER / "ratios_co2.parquet")

    r = df.set_index("ratio")
    km, fe = r.loc["km_voiture_evites_par_voiture_partagee"], r.at["facteur_voiture_thermique", "valeur"]
    produced, undocumented, missing = check_dictionary()
    lines = ["Rapport qualité - table ratios_co2 et contrôle des sources", "=" * 58,
             f"Ratios : {len(df)} ({(df.source == 'ademe_autopartage_2022').sum()} enquête ADEME 2022, "
             f"{(df.source == 'impact_co2').sum()} Impact CO₂, {(df.source == 'hypothèse').sum()} hypothèses)",
             f"Erreurs : {len(errors)}", *[f"  [ERREUR] {e}" for e in errors],
             "", "Ordre de grandeur (voiture thermique évitée) : CO₂ évité par une voiture en autopartage",
             f"  {km['min']:,.0f} à {km['max']:,.0f} km/an x {fe} kg/km = "
             f"{km['min'] * fe / 1000:.2f} à {km['max'] * fe / 1000:.2f} t CO₂e/an (central {km['valeur'] * fe / 1000:.2f} t)",
             "", f"Contrôle du dictionnaire ({DICTIONARY.relative_to(ROOT)}) :",
             f"  tables produites (silver + gold) : {len(produced)}",
             f"  [{'OK ' if not undocumented else 'À FAIRE'}] tables produites absentes du dictionnaire : {undocumented or 'aucune'}",
             f"  [{'OK ' if not missing else 'INFO'}] tables silver du dictionnaire non produites ici : {missing or 'aucune'}"]
    report = "\n".join(lines)
    (SILVER / "ratios_co2_qc.txt").write_text(report, encoding="utf-8")
    print(report)
    if errors:
        sys.exit(1)
    print(f"\nÉcrit : {SILVER / 'ratios_co2.parquet'}")


if __name__ == "__main__":
    main()
