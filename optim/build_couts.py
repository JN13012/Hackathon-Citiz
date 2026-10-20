"""Rôle 4 - table silver `couts` (une ligne = un poste de coût), coût et recette par équipement.

Lit optim/couts.yaml, vérifie que chaque montant a une source ou la mention « hypothèse »,
puis calcule pour chaque équipement (et ses variantes, ex. station_citiz_electrique) et chaque
scénario (bas / central / haut) : investissement initial net des aides et coût annualisé
= investissement / durée de vie + exploitation.
Recettes Citiz : tarifs publics GBFS (system_pricing_plans) des réseaux Citiz du territoire,
appliqués à une location type (hypothèses de couts.yaml), d'où une marge annuelle par station.

Sorties :
- data/bronze/citiz_tarifs.json          formules tarifaires telles que lues
- data/silver/tarifs_citiz.parquet       une formule par ligne, avec le prix de la location type
- data/silver/couts.parquet              postes (type_equipement, poste, montant, unite, source, …)
- data/silver/couts_equipements.parquet  équipement x scénario de coût (bas / central / haut), entrée de l'optimiseur
- data/silver/marge_citiz.parquet        station Citiz x scénario (pessimiste / central / optimiste) : recette, coût, marge
- data/silver/couts_qc.txt               rapport qualité
Usage : python optim/build_couts.py [--refresh]   (après build_offre_partagee.py, pour les réseaux du territoire)
Les tarifs lus aujourd'hui (data/bronze/citiz_tarifs.json) sont réutilisés ; --refresh pour les relire.
"""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import geopandas as gpd
import pandas as pd
import yaml

import build_offre_partagee as O
import territoire as T

COSTS_FILE = T.ROOT / "optim" / "couts.yaml"
SCENARIOS = {"bas": "min", "central": "montant", "haut": "max"}
USAGE_BY_SCENARIO = {"bas": "min", "central": "valeur", "haut": "max"}  # niveau d'usage
# marge : pessimiste = usage bas et coûts hauts ; optimiste = usage haut et coûts bas
MARGIN_SCENARIOS = {"pessimiste": ("bas", "haut"), "central": ("central", "central"), "optimiste": ("haut", "bas")}
QUANTITY_BY_UNIT = {"véhicule": "vehicules_par_station", "vélo": "vehicules_par_station",
                    "place": "places_par_station"}  # station, arrêt : x 1
COLUMNS = ["type_equipement", "poste", "montant", "unite", "source", "statut", "min", "max",
           "nature", "quantite", "duree_vie_ans", "inclus_dans", "option", "aide", "annee", "url", "note"]
REFERENCE_PLAN = ("sans abonnement", "grand public")  # formule sans engagement


# ---------------------------------------------------------------- coûts

def load_postes(spec):
    rows = []
    for equip, dims in spec["dimensionnement"].items():
        for p in spec[equip]:
            unit = p.get("unite", "")
            per = unit.split("/")[1] if "/" in unit else "station"
            rows.append({**{k: p.get(k) for k in COLUMNS if k in p},
                         "type_equipement": equip,
                         "nature": "exploitation" if unit.endswith("/an") else "investissement",
                         "quantite": dims.get(QUANTITY_BY_UNIT.get(per), 1)})
    df = pd.DataFrame(rows).reindex(columns=COLUMNS)
    df["statut"] = df["source"].map(lambda s: "hypothèse" if s == "hypothèse" else "source")
    df["aide"] = df["aide"].fillna(False).astype(bool)
    for col in ("montant", "min", "max", "duree_vie_ans", "quantite"):
        df[col] = pd.to_numeric(df[col])
    df["annee"] = df["annee"].astype("Int64")
    return df


def validate(df):
    errors = []
    def check(mask, msg):
        errors.extend(f"{r.type_equipement}.{r.poste} : {msg}" for r in df[mask].itertuples())
    counted = df["inclus_dans"].isna()
    check(df["source"].isna(), "ni source ni mention « hypothèse »")
    check((df["statut"] == "source") & df["url"].isna(), "source sans url")
    check(counted & ~df["aide"] & ~(df["montant"] > 0), "montant manquant ou <= 0")
    check(counted & df["aide"] & ~(df["montant"] < 0), "une aide doit être négative")
    check(counted & ~((df["min"] <= df["montant"]) & (df["montant"] <= df["max"])), "il faut min <= montant <= max")
    check(counted & (df["nature"] == "investissement") & ~(df["duree_vie_ans"] > 0), "investissement sans durée de vie")
    check(~counted & ~df["inclus_dans"].isin(df["poste"]), "inclus_dans pointe vers un poste inconnu")
    if errors:
        raise SystemExit("optim/couts.yaml invalide :\n  " + "\n  ".join(errors))


def variants(df):
    """{nom: postes} : chaque équipement, plus une variante par option (ex. station_citiz_electrique)."""
    out = {}
    for equip, g in df[df["inclus_dans"].isna()].groupby("type_equipement"):
        out[equip] = g[g["option"].isna()]
        for opt in g["option"].dropna().unique():
            out[f"{equip}_{opt}"] = g[g["option"].isna() | (g["option"] == opt)]
    return out


def equipment_costs(df):
    rows = []
    for name, g in variants(df).items():
        invest = g["nature"] == "investissement"
        for scenario, col in SCENARIOS.items():
            amount = g[col] * g["quantite"]
            annual = (amount / g["duree_vie_ans"]).where(invest, amount)
            rows.append({"type_equipement": name, "scenario": scenario,
                         "cout_investissement": amount[invest].sum(),
                         "aides": amount[g["aide"]].sum(),
                         "cout_exploitation_annuel": amount[~invest].sum(),
                         "cout_annualise": annual.sum()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- recettes Citiz

def segment_cost(segments, x):
    """Coût GBFS d'une quantité x (km ou minutes) : rate à chaque interval entamé, ou forfait si interval = 0."""
    total = 0.0
    for seg in segments or []:
        start, end = seg.get("start", 0), seg.get("end")
        if x <= start:
            continue
        upto = x if end is None else min(x, end)
        interval = seg.get("interval") or 0
        total += math.ceil((upto - start) / interval) * seg["rate"] if interval > 0 else seg["rate"]
    return total


def trip_price(plan, hours, km):
    return plan.get("price", 0) + segment_cost(plan.get("per_km_pricing"), km) \
        + segment_cost(plan.get("per_min_pricing"), hours * 60)


def load_citiz_plans(refresh=False):
    """Formules tarifaires de tous les réseaux Citiz (backend.citiz.fr), relues une fois par jour."""
    cache = T.BRONZE / "citiz_tarifs.json"
    if not refresh and cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime).date() == date.today():
        print(f"Tarifs Citiz du jour réutilisés ({cache.name}) ; --refresh pour les relire")
        return json.loads(cache.read_text(encoding="utf-8"))
    def read(job):
        _, url = job
        try:
            feeds = O.feed_urls(O.get_json(url).get("data", {}), url)
            system_id = O.get_json(feeds["system_information"])["data"].get("system_id")
            plans = O.get_json(feeds["system_pricing_plans"])["data"]["plans"]
            return [{**p, "system_id": system_id} for p in plans]
        except Exception as e:
            print(f"tarifs indisponibles pour {url} : {type(e).__name__}")
            return []
    with ThreadPoolExecutor(max_workers=10) as pool:
        plans = [p for ps in pool.map(read, O.citiz_backend_jobs()) for p in ps]
    T.BRONZE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(plans, ensure_ascii=False), encoding="utf-8")
    return plans


def citiz_tariffs(plans, usage):
    hours, km = usage["duree_location_h"]["valeur"], usage["km_par_location"]["valeur"]
    df = pd.DataFrame([{
        "system_id": p["system_id"], "plan_id": p.get("plan_id"), "nom": O.text(p.get("name")),
        "prix_fixe": p.get("price", 0), "taxable": p.get("is_taxable"),
        "prix_location_type": trip_price(p, hours, km), "_plan": p} for p in plans])
    df["reference"] = False
    for _, g in df.groupby("system_id"):
        ref = g[g["nom"].str.lower().str.contains("|".join(REFERENCE_PLAN), na=False)]
        ref = ref if len(ref) else g
        df.loc[ref["prix_location_type"].idxmin(), "reference"] = True  # formule la moins chère
    return df


def citiz_revenue(tariffs, usage, vehicles_per_station):
    """Recette annuelle HT d'une station du territoire, moyenne pondérée par le nombre de stations du réseau."""
    stations = gpd.read_parquet(T.SILVER / "stations_partagees.parquet")
    weights = stations[stations["type"] == "citiz"].groupby("system_id").size()
    ref = tariffs[tariffs["reference"]].set_index("system_id")
    weights = weights[weights.index.isin(ref.index)]
    tva = usage["tva"]["valeur"]
    rows = []
    for scenario, col in USAGE_BY_SCENARIO.items():
        hours, km = usage["duree_location_h"][col], usage["km_par_location"][col]
        price = sum(trip_price(ref.loc[s, "_plan"], hours, km) * w for s, w in weights.items()) / weights.sum()
        per_year = usage["locations_par_vehicule_par_jour"][col] * 365 * vehicles_per_station
        rows.append({"scenario": scenario, "prix_location_ht": price / (1 + tva),
                     "recette_annuelle": price / (1 + tva) * per_year})
    return pd.DataFrame(rows), weights


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="relire les tarifs Citiz même s'ils datent d'aujourd'hui")
    args = ap.parse_args()
    spec = yaml.safe_load(COSTS_FILE.read_text(encoding="utf-8"))
    df = load_postes(spec)
    validate(df)
    costs = equipment_costs(df)

    usage = spec["recettes"]["station_citiz"]
    tariffs = citiz_tariffs(load_citiz_plans(args.refresh), usage)
    revenue, weights = citiz_revenue(tariffs, usage, spec["dimensionnement"]["station_citiz"]["vehicules_par_station"])
    rev = revenue.set_index("scenario")["recette_annuelle"]
    cost = costs.set_index(["type_equipement", "scenario"])["cout_annualise"]
    margin = pd.DataFrame([
        {"type_equipement": equip, "scenario": name, "recette_annuelle": rev[usage_level],
         "cout_annualise": cost[(equip, cost_level)], "marge_annuelle": rev[usage_level] - cost[(equip, cost_level)]}
        for equip in costs["type_equipement"].unique() if equip.startswith("station_citiz")
        for name, (usage_level, cost_level) in MARGIN_SCENARIOS.items()]).round(0)
    costs = costs.round({c: 0 for c in costs.columns if c.startswith(("cout", "aides"))})

    T.SILVER.mkdir(parents=True, exist_ok=True)
    df.to_parquet(T.SILVER / "couts.parquet")
    costs.to_parquet(T.SILVER / "couts_equipements.parquet")
    margin.to_parquet(T.SILVER / "marge_citiz.parquet")
    tariffs.drop(columns="_plan").to_parquet(T.SILVER / "tarifs_citiz.parquet")

    counted = df[df["inclus_dans"].isna()]
    hyp = counted.groupby("type_equipement")["statut"].apply(lambda s: (s == "hypothèse").sum())
    ref = tariffs[tariffs["reference"] & tariffs["system_id"].isin(weights.index)]
    fmt = lambda v: f"{v:,.0f}"
    lines = ["Rapport qualité - tables couts, couts_equipements et tarifs_citiz", "=" * 64,
             f"Postes : {len(df)} ({len(counted)} chiffrés, {len(df) - len(counted)} inclus dans un autre poste)",
             f"Postes chiffrés sourcés / hypothèses : {(counted.statut == 'source').sum()} / {(counted.statut == 'hypothèse').sum()}",
             "Hypothèses par équipement : " + ", ".join(f"{k} {v}" for k, v in hyp.items()),
             "Dimensionnement et usage d'une station Citiz : hypothèses, voir couts.yaml",
             "", "Coût annualisé (€ HT / an) :",
             costs.pivot(index="type_equipement", columns="scenario", values="cout_annualise")[list(SCENARIOS)].to_string(float_format=fmt),
             "", "Investissement initial net des aides (€ HT, central) :",
             costs[costs.scenario == "central"].set_index("type_equipement")["cout_investissement"].to_string(float_format=fmt),
             "", f"Formules Citiz lues : {len(tariffs)} sur {tariffs['system_id'].nunique()} réseaux",
             "Formule de référence des réseaux du territoire (location type "
             f"{usage['duree_location_h']['valeur']} h / {usage['km_par_location']['valeur']} km, TTC) :",
             ref[["system_id", "nom", "prix_location_type"]].assign(stations=ref["system_id"].map(weights))
                .to_string(index=False, float_format=lambda v: f"{v:.2f} €"),
             "", "Usage d'une station Citiz -> prix HT d'une location et recette annuelle :",
             revenue.set_index("scenario").to_string(float_format=fmt),
             "", "Marge annuelle d'une station Citiz (€ HT / an) :",
             margin.pivot(index="type_equipement", columns="scenario", values="marge_annuelle")[list(MARGIN_SCENARIOS)]
                .to_string(float_format=fmt)]
    report = "\n".join(lines)
    (T.SILVER / "couts_qc.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
