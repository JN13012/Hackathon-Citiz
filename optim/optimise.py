"""Rôle 4 - optimiseur sous budget : liste priorisée des nouveaux équipements (Citiz, bus, vélo).

Question : avec un budget annuel donné, quels emplacements et quels équipements desservent le plus
d'habitants qui n'ont pas encore cette offre à 5 min à pied ?

Entrées (data/silver, data/gold) :
- gold_candidates (Rôle 1)      emplacements possibles (centres des carreaux habités)
- projections_carreaux (Rôle 4) population 2021 et 2030 de chaque carreau (Omphale)
- couts_equipements (Rôle 4)    coût annualisé de chaque équipement, scénarios bas / central / haut
- gold_couverture (Rôle 2)      carreaux à 5 min à pied de chaque candidat, sur le réseau piéton réel
- paires population-offre (Rôle 2, accessibilite_ara) desserte existante sur le réseau réel
- gold_offre (Rôle 1)           offre existante, pour l'approximation si les fichiers du Rôle 2 manquent
- gold_scores (Rôle 3)          usage prédit par candidat et par type, s'il existe (sinon poids 1)
- ratios_co2 (Rôle 5)           km évités et facteur d'émission pour le CO₂ évité des stations Citiz

Sans les fichiers du Rôle 2, la marche de 5 min est approchée par 400 m / 1,3 (détours) ≈ 308 m à vol
d'oiseau, comme l'EDA ; la colonne `methode_couverture` le dit dans les sorties.

Méthode : couverture maximale sous budget, résolue par un algorithme glouton paresseux (à chaque pas,
l'option qui apporte le plus d'habitants nouvellement desservis par euro), avec une distance minimale
entre deux nouveaux équipements du même type. Un habitant compte une seule fois par type.

Sorties (data/gold/) :
- gold_liste_priorisee.parquet / .csv / .geojson  rang, type, lieu, coût, habitants gagnés, cumul, CO₂
- gold_courbe_budget.parquet / .csv              habitants gagnés selon le budget, par scénario de coût
- optimiseur_qc.txt                              paramètres, sources utilisées, contrôles
Usage : python optim/optimise.py [--budget 500000] [--scenario central] [--equite 0.5]
"""
import argparse
import heapq
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
SILVER, GOLD = ROOT / "data" / "silver", ROOT / "data" / "gold"
CRS = 2154
WALK_RADIUS_M = 400 / 1.3  # 5 min à 4,8 km/h, avec un coefficient de détour de 1,3 (approximation EDA)

# type d'équipement de l'optimiseur -> ligne de couts_equipements et types d'offre existante équivalents
EQUIPMENTS = {
    "citiz": {"cout": "station_citiz", "offre": {"citiz"}, "distance_min_m": 500},
    "bus": {"cout": "arret_bus", "offre": {"bus", "tram", "metro", "trolleybus", "funiculaire", "cable_car", "ferry"},
            "distance_min_m": 300},
    "velo": {"cout": "station_velo", "offre": {"velo"}, "distance_min_m": 300},
}
SMALL_COMMUNE = 10_000  # seuil EDA « petite commune / rural » pour le réglage d'équité
# Un arrêt de bus est chiffré « sur une ligne existante » (couts.yaml) : on ne le place qu'à proximité
# d'un arrêt existant (hypothèse : une ligne passe à moins de 1 km), au-delà des 5 min déjà desservies.
BUS_MAX_FROM_STOP_M = 1000


def read(path, geo=False):
    if not path.exists():
        return None
    return gpd.read_parquet(path) if geo else pd.read_parquet(path)


def coverage_pairs(candidates, carreaux, notes):
    """Paires (indice candidat, indice carreau) à 5 min : réseau réel du Rôle 2, sinon approximation."""
    cov = read(GOLD / "gold_couverture.parquet")
    if cov is not None:
        info = read(GOLD / "candidats_couverture.parquet")
        if info is not None and "utilisable_optimiseur" in info:
            ok = set(info.loc[info["utilisable_optimiseur"], "id_candidat"])
            cov = cov[cov["id_candidat"].isin(ok)]
            notes.append(f"candidats utilisables (statut ok du Rôle 2) : {len(ok):,}")
        cand_idx = pd.Series(np.arange(len(candidates)), index=candidates["id_carreau"])
        car_idx = pd.Series(np.arange(len(carreaux)), index=carreaux["id_carreau"])
        keep = cov["id_candidat"].isin(cand_idx.index) & cov["id_carreau"].isin(car_idx.index)
        if (~keep).any():
            notes.append(f"[ATTENTION] {int((~keep).sum())} relations sans correspondance d'identifiant, écartées")
        cov = cov[keep]
        notes.append(f"couverture : gold_couverture du Rôle 2 (réseau piéton réel), {len(cov):,} relations")
        return "reseau_pieton", cand_idx[cov["id_candidat"]].to_numpy(), car_idx[cov["id_carreau"]].to_numpy()
    xy_c = np.c_[candidates.geometry.x, candidates.geometry.y]
    xy_p = np.c_[carreaux.geometry.x, carreaux.geometry.y]
    pairs = cKDTree(xy_c).query_ball_tree(cKDTree(xy_p), WALK_RADIUS_M)
    ci = np.repeat(np.arange(len(pairs)), [len(p) for p in pairs])
    pi = np.fromiter((j for p in pairs for j in p), dtype=np.int64, count=len(ci))
    notes.append(f"[APPROXIMATION] gold_couverture absent : {WALK_RADIUS_M:.0f} m à vol d'oiseau, {len(ci):,} relations")
    return "vol_oiseau_308m", ci, pi


def existing_service(carreaux, notes):
    """Carreaux déjà desservis, par type d'équipement (booléen par carreau)."""
    offre = read(GOLD / "gold_offre.parquet", geo=True)
    served = {t: np.zeros(len(carreaux), dtype=bool) for t in EQUIPMENTS}
    if offre is None:
        notes.append("[ATTENTION] gold_offre absent : aucune desserte existante retirée")
        return served
    pairs = read(GOLD / "accessibilite_ara" / "resultats" / "internes" / "paires_accessibles.parquet")
    car_idx = pd.Series(np.arange(len(carreaux)), index=carreaux["id_carreau"])
    if pairs is not None:
        types = offre.set_index("offre_id")["type"]
        pairs = pairs.assign(type=pairs["offre_id"].map(types)).dropna(subset=["type"])
        pairs = pairs[pairs["id_carreau"].isin(car_idx.index)]
        for t, spec in EQUIPMENTS.items():
            served[t][car_idx[pairs.loc[pairs["type"].isin(spec["offre"]), "id_carreau"]].to_numpy()] = True
        notes.append("desserte existante : paires population-offre du Rôle 2 (réseau piéton réel)")
        return served
    tree = cKDTree(np.c_[carreaux.geometry.x, carreaux.geometry.y])
    for t, spec in EQUIPMENTS.items():
        pts = offre[offre["type"].isin(spec["offre"])]
        if len(pts):
            hits = tree.query_ball_point(np.c_[pts.geometry.x, pts.geometry.y], WALK_RADIUS_M)
            served[t][np.unique(np.fromiter((j for h in hits for j in h), dtype=np.int64))] = True
    notes.append(f"[APPROXIMATION] desserte existante : gold_offre à {WALK_RADIUS_M:.0f} m à vol d'oiseau")
    return served


def usage_weights(candidates, notes):
    """Usage prédit par candidat et par type (gold_scores du Rôle 3), normalisé autour de 1 ; sinon 1."""
    scores = read(GOLD / "gold_scores.parquet")
    weights = {t: np.ones(len(candidates)) for t in EQUIPMENTS}
    if scores is None:
        notes.append("usage prédit : gold_scores absent, poids 1 (population seule, référence du PLAN)")
        return weights
    idx = pd.Series(np.arange(len(candidates)), index=candidates["id_carreau"])
    id_col = "id_candidat" if "id_candidat" in scores else "id_carreau"
    for t in EQUIPMENTS:
        sub = scores[scores.get("type", t) == t] if "type" in scores else scores
        col = next((c for c in ("score", "usage_predit", t) if c in sub), None)
        if col is None:
            continue
        s = sub.set_index(id_col)[col].reindex(candidates["id_carreau"])
        weights[t] = (s / s.mean()).fillna(1.0).to_numpy()
    notes.append("usage prédit : gold_scores du Rôle 3, normalisé (moyenne 1)")
    return weights


def greedy(cand_xy, ci, pi, population, served, cost, weight, budget, dmin):
    """Couverture maximale sous budget, glouton paresseux sur le ratio habitants gagnés / coût."""
    order = np.argsort(ci, kind="stable")
    ci, pi = ci[order], pi[order]
    starts = np.searchsorted(ci, np.arange(len(cand_xy) + 1))
    covered = served.copy()

    def gain(c):
        cells = pi[starts[c]:starts[c + 1]]
        return float(population[cells[~covered[cells]]].sum()) * weight[c]

    # gains initiaux en une passe : somme de la population non desservie par segment de candidat
    values = population[pi] * ~covered[pi]
    nonempty = np.flatnonzero(starts[1:] > starts[:-1])
    initial = np.add.reduceat(values, starts[nonempty]) * weight[nonempty] if len(values) else np.array([])
    heap = [(-g / cost, int(c)) for c, g in zip(nonempty, initial) if g > 0]
    heapq.heapify(heap)
    chosen, spent, picked_xy = [], 0.0, np.empty((0, 2))
    while heap and spent + cost <= budget:
        neg, c = heapq.heappop(heap)
        g = gain(c)
        if g <= 0:
            continue
        if heap and g / cost < -heap[0][0] - 1e-9:  # gain périmé : on remet le candidat à jour
            heapq.heappush(heap, (-g / cost, c))
            continue
        if len(picked_xy) and np.hypot(*(picked_xy - cand_xy[c]).T).min() < dmin:
            continue  # trop proche d'un équipement déjà choisi du même type
        cells = pi[starts[c]:starts[c + 1]]
        new_cells = cells[~covered[cells]]
        covered[new_cells] = True
        spent += cost
        picked_xy = np.vstack([picked_xy, cand_xy[c]])
        chosen.append((c, float(population[new_cells].sum()), g))
    return chosen


def bus_allowed(candidates, notes):
    """Candidats où un nouvel arrêt de bus est plausible : à moins de 1 km d'un arrêt existant."""
    offre = read(GOLD / "gold_offre.parquet", geo=True)
    if offre is None:
        return np.ones(len(candidates), dtype=bool)
    stops = offre[offre["type"].isin(EQUIPMENTS["bus"]["offre"])]
    dist, _ = cKDTree(np.c_[stops.geometry.x, stops.geometry.y]).query(np.c_[candidates.geometry.x, candidates.geometry.y])
    allowed = dist <= BUS_MAX_FROM_STOP_M
    notes.append(f"arrêts de bus : candidats à moins de {BUS_MAX_FROM_STOP_M} m d'un arrêt existant "
                 f"(ligne existante, hypothèse) : {int(allowed.sum()):,}")
    return allowed


def run(equite, candidates, carreaux, ci, pi, served, costs, weights, cand_small, pop_col, scenario, budget,
        repartition=None):
    """Une optimisation : budget réparti entre les trois types selon le meilleur ratio habitants / euro.
    Un habitant compte une fois par type : il peut gagner à la fois un arrêt de bus et une station Citiz."""
    population = carreaux[pop_col].to_numpy(dtype=float)
    cand_xy = np.c_[candidates.geometry.x, candidates.geometry.y]
    equity = np.where(cand_small, 1 + equite, 1.0)  # bonus aux emplacements situés dans une petite commune
    results = []
    for t, spec in EQUIPMENTS.items():
        cost = costs.loc[(spec["cout"], scenario), "cout_annualise"]
        w = weights[t] * equity
        if repartition is not None and repartition.get(t, 0) <= 0:
            continue
        type_budget = budget * repartition[t] if repartition else budget
        for c, gained, score in greedy(cand_xy, ci, pi, population, served[t], cost, w, type_budget, spec["distance_min_m"]):
            results.append({"type": t, "cand": c, "habitants_gagnes": gained, "score": score, "cout_annualise": cost})
    df = pd.DataFrame(results)
    if df.empty:
        return df
    # sans répartition imposée, un seul budget pour les trois types : meilleurs ratios jusqu'à épuisement
    df["ratio"] = df["score"] / df["cout_annualise"]
    df = df.sort_values("ratio", ascending=False)
    if repartition is None:
        df = df[df["cout_annualise"].cumsum() <= budget]
    df = df.reset_index(drop=True)
    df["rang"] = np.arange(1, len(df) + 1)
    df["budget_cumule"] = df["cout_annualise"].cumsum()
    df["habitants_cumules"] = df["habitants_gagnes"].cumsum()
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget", type=float, default=500_000, help="budget annuel en € HT 2025 (coûts annualisés)")
    ap.add_argument("--scenario", default="central", choices=["bas", "central", "haut"], help="scénario de coût")
    ap.add_argument("--horizon", default="2030", choices=["2021", "2030"], help="population de référence")
    ap.add_argument("--equite", type=float, default=0.0,
                    help="bonus de poids pour les communes de moins de 10 000 habitants (0 = aucun, 0.5 = +50 %%)")
    ap.add_argument("--repartition", help="parts du budget par type, ex. citiz=0.5,bus=0.25,velo=0.25 "
                                          "(par défaut : libre, au meilleur ratio habitants / euro)")
    ap.add_argument("--courbe", nargs="+", type=float, default=[100_000, 250_000, 500_000, 1_000_000, 2_000_000],
                    help="budgets annuels pour la courbe budget -> habitants")
    args = ap.parse_args()

    notes = []
    candidates = read(GOLD / "gold_candidates.parquet", geo=True)
    carreaux = read(SILVER / "projections_carreaux.parquet", geo=True)
    costs = read(SILVER / "couts_equipements.parquet")
    if candidates is None or carreaux is None or costs is None:
        raise SystemExit("Entrées manquantes : gold_candidates, projections_carreaux et couts_equipements sont requis")
    candidates, carreaux = candidates.to_crs(CRS).reset_index(drop=True), carreaux.to_crs(CRS).reset_index(drop=True)
    costs = costs.set_index(["type_equipement", "scenario"])
    pop_col = "population_2030" if args.horizon == "2030" else "population_2021"
    missing = ~candidates["id_carreau"].isin(carreaux["id_carreau"])
    notes.append(f"candidats : {len(candidates):,} ; carreaux : {len(carreaux):,} ; candidats sans carreau projeté : {int(missing.sum())}")

    method, ci, pi = coverage_pairs(candidates, carreaux, notes)
    served = existing_service(carreaux, notes)
    weights = usage_weights(candidates, notes)
    weights["bus"] = weights["bus"] * bus_allowed(candidates, notes)  # poids 0 : candidat exclu pour le bus
    repartition = None
    if args.repartition:
        repartition = {k: float(v) for k, v in (kv.split("=") for kv in args.repartition.split(","))}
        if abs(sum(repartition.values()) - 1) > 1e-6 or not set(repartition) <= set(EQUIPMENTS):
            raise SystemExit("--repartition : types citiz, bus, velo et parts dont la somme vaut 1")
    commune_pop = carreaux.groupby("commune")["population_2021"].transform("sum")
    small_by_carreau = pd.Series((commune_pop < SMALL_COMMUNE).to_numpy(), index=carreaux["id_carreau"])
    cand_small = small_by_carreau.reindex(candidates["id_carreau"]).fillna(False).to_numpy(dtype=bool)

    best = run(args.equite, candidates, carreaux, ci, pi, served, costs, weights, cand_small, pop_col,
               args.scenario, args.budget, repartition)
    if best.empty:
        raise SystemExit("Aucun équipement ne peut être ajouté avec ce budget")
    cand = candidates.iloc[best["cand"]].reset_index(drop=True)
    ratios = read(SILVER / "ratios_co2.parquet")
    co2 = 0.0
    if ratios is not None:
        r = ratios.set_index("ratio")["valeur"]
        co2 = r["km_voiture_evites_par_voiture_partagee"] * r["facteur_voiture_thermique"] / 1000  # t CO₂e/an/voiture
    vehicles = 1  # vehicules_par_station de couts.yaml (station Citiz)
    communes = carreaux.set_index("id_carreau")["commune"].reindex(cand["id_carreau"]).to_numpy()
    contours = read(SILVER / "contours.parquet")
    names = {} if contours is None else contours[contours["niveau"] == "commune"].set_index("code_insee")["nom"].to_dict()
    out = gpd.GeoDataFrame({
        "rang": best["rang"], "type": best["type"], "id_candidat": cand["id_carreau"],
        "commune": communes, "nom_commune": [names.get(c) for c in communes],
        "cout_annualise": best["cout_annualise"].round(0),
        "cout_investissement": [costs.loc[(EQUIPMENTS[t]["cout"], args.scenario), "cout_investissement"] for t in best["type"]],
        f"habitants_gagnes_{args.horizon}": best["habitants_gagnes"].round(1),
        "habitants_cumules": best["habitants_cumules"].round(1), "budget_cumule": best["budget_cumule"].round(0),
        "co2_evite_t_an": np.where(best["type"] == "citiz", round(co2 * vehicles, 2), np.nan),
        "methode_couverture": method, "scenario_cout": args.scenario, "equite": args.equite,
        "geometry": cand.geometry.to_numpy()}, crs=CRS)
    GOLD.mkdir(parents=True, exist_ok=True)
    out.to_parquet(GOLD / "gold_liste_priorisee.parquet")
    out.drop(columns="geometry").assign(lon=out.to_crs(4326).geometry.x.round(6), lat=out.to_crs(4326).geometry.y.round(6)) \
        .to_csv(GOLD / "gold_liste_priorisee.csv", index=False)
    out.to_crs(4326).to_file(GOLD / "gold_liste_priorisee.geojson", driver="GeoJSON")

    # courbe budget -> habitants, pour les trois scénarios de coût, avec et sans équité
    curve = []
    for scenario in ("bas", "central", "haut"):
        for eq in sorted({0.0, args.equite, 0.5}):
            for b in args.courbe:
                sel = run(eq, candidates, carreaux, ci, pi, served, costs, weights, cand_small, pop_col, scenario, b,
                          repartition)
                curve.append({"scenario_cout": scenario, "equite": eq, "budget_annuel": b,
                              "equipements": len(sel), "habitants_gagnes": round(float(sel["habitants_gagnes"].sum()), 0),
                              **{f"nb_{t}": int((sel["type"] == t).sum()) for t in EQUIPMENTS},
                              "part_petites_communes": round(float(cand_small[sel["cand"]].mean()), 3) if len(sel) else 0.0})
    curve = pd.DataFrame(curve)
    curve.to_parquet(GOLD / "gold_courbe_budget.parquet")
    curve.to_csv(GOLD / "gold_courbe_budget.csv", index=False)

    marge = read(SILVER / "marge_citiz.parquet")
    n_citiz = int((out["type"] == "citiz").sum())  # colonne : out.type serait le type de géométrie
    lines = ["Rapport - optimiseur sous budget (Rôle 4)", "=" * 42,
             f"Budget annuel : {args.budget:,.0f} € HT 2025 ; scénario de coût : {args.scenario} ; "
             f"population {args.horizon} ; équité : +{args.equite:.0%} pour les communes < {SMALL_COMMUNE:,} hab.",
             *[f"- {n}" for n in notes], "",
             f"Répartition du budget : {args.repartition or 'libre (meilleur ratio habitants / euro)'}",
             f"Équipements retenus : {len(out)} ({', '.join(f'{t} {int((out['type'] == t).sum())}' for t in EQUIPMENTS)})",
             f"Budget utilisé : {out['budget_cumule'].max():,.0f} € HT/an ; investissement initial : {out['cout_investissement'].sum():,.0f} €",
             f"Habitants nouvellement desservis ({args.horizon}, somme sur les types) : {out[f'habitants_gagnes_{args.horizon}'].sum():,.0f}",
             f"CO₂ évité par les stations Citiz (voiture thermique évitée) : {np.nansum(out['co2_evite_t_an']):,.1f} t CO₂e/an",
             (f"Marge des {n_citiz} stations Citiz (central) : "
              f"{n_citiz * marge.set_index(['type_equipement', 'scenario']).loc[('station_citiz', 'central'), 'marge_annuelle']:,.0f} € HT/an"
              if marge is not None and n_citiz else "Marge Citiz : non calculée"),
             "", "Contrôles :",
             f"  [{'OK ' if out['budget_cumule'].max() <= args.budget else 'ERREUR'}] budget respecté",
             f"  [{'OK ' if out['id_candidat'].is_unique or out.groupby('type')['id_candidat'].apply(lambda s: s.is_unique).all() else 'ERREUR'}] pas de candidat en double par type",
             f"  [{'OK ' if (out[f'habitants_gagnes_{args.horizon}'] > 0).all() else 'ERREUR'}] chaque équipement gagne des habitants",
             "", "Courbe budget -> habitants (scénario central, sans équité) :",
             curve[(curve.scenario_cout == "central") & (curve.equite == 0)]
                [["budget_annuel", "equipements", "nb_citiz", "nb_bus", "nb_velo", "habitants_gagnes"]].to_string(index=False),
             "", "10 premiers équipements :",
             out.head(10)[["rang", "type", "nom_commune", "cout_annualise", f"habitants_gagnes_{args.horizon}", "habitants_cumules"]].to_string(index=False)]
    report = "\n".join(lines)
    (GOLD / "optimiseur_qc.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
