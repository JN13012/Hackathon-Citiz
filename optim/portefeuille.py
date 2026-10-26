"""Objectif 3 - portefeuille priorisé sous budget : où ouvrir des stations Citiz, des arrêts de bus et des stations
vélo pour desservir le plus d'habitants au moindre coût, en tenant compte de l'offre existante.

Complète optim/optimise.py (Rôle 4) avec ce qui lui manque pour le livrable : lecture en CSV (Parquet bloqué sur
certains postes), scores du modèle (model/train.py), coûts calculés directement depuis optim/couts.yaml, usage et
marge de chaque station Citiz, règle électrique, CO₂, quota hors grandes villes et péréquation (Σ marges ≥ 0).

Méthode (couverture maximale sous budget, algorithme glouton paresseux, comme le Rôle 4) :
- valeur d'un site = habitants nouvellement desservis à 5 min à pied (réseau piéton du Rôle 2, carreaux déjà
  desservis par le même type retirés, un habitant compté une fois par type) × poids de potentiel du type ;
- poids : score LightGBM pour Citiz et bus ; pour le vélo, la référence « ménages sans voiture à 400 m »
  (le modèle vélo ne bat pas cette référence, voir model/RESULTATS_MODELE.md) ;
- ordre : valeur / coût annualisé, avec une distance minimale entre deux équipements neufs du même type ;
- budget annuel réparti entre les types (--repartition), car Citiz n'a pas le même rôle qu'un arrêt de bus ;
- Citiz : au moins --quota des nouvelles stations hors des grandes villes-centres (territoire « 1 grande aire ·
  pôle »), et péréquation : une station déficitaire n'est acceptée que si la somme des marges des stations déjà
  retenues reste ≥ 0 après elle (les stations rentables financent les autres) ;
- les candidats que le réseau piéton ne raccorde pas (centre à plus de 50 m d'une voie, surtout en peu dense)
  restent candidats, avec une couverture estimée à vol d'oiseau (400 m / 1,3 de détour), signalée.

Usage d'une station Citiz (hypothèse affichée) : une station neuve a 1 véhicule (couts.yaml) ; moyenne Citiz
≈ 0,52 location par jour et par véhicule (565 000 réservations, 3 000 véhicules), modulée par le score : ×0,5 à ×2
selon le rang du site parmi les stations Citiz existantes (site aussi bon que la station médiane : 0,52 par jour).
Recette = locations × 365 × prix moyen HT (40 / 50 / 60 € TTC selon le scénario).

Sorties (CSV, data/gold/) : portefeuille_priorise.csv, courbe_budget_portefeuille.csv, portefeuille_rapport.txt
Usage : py optim/portefeuille.py [--budget 500000] [--quota 0.4] [--repartition citiz=0.6,bus=0.2,velo=0.2]
"""
import argparse
import heapq
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.spatial import cKDTree

RACINE = Path(__file__).resolve().parent.parent
SILVER, GOLD = RACINE / "data" / "silver", RACINE / "data" / "gold"

TYPES = {  # type -> poste de couts.yaml, offre existante équivalente, distance minimale entre deux neufs
    "citiz": {"cout": "station_citiz", "offre": {"citiz"}, "dmin": 500},
    "bus": {"cout": "arret_bus", "offre": {"bus", "tram", "metro", "trolleybus", "funiculaire", "cable_car", "ferry"},
            "dmin": 300},
    "velo": {"cout": "station_velo", "offre": {"velo"}, "dmin": 300},
}
GRANDES_VILLES = "1 grande aire · pôle"
RAYON_ESTIME_M = 400 / 1.3      # 5 min à pied à vol d'oiseau avec détours (approximation du Rôle 4)
BUS_MAX_ARRET_M = 1000          # arrêt ajouté sur une ligne existante : un arrêt existe à moins de 1 km
# station neuve = 1 véhicule (couts.yaml) : on cale donc sur l'usage par véhicule, pas par station
LOC_JOUR_MOYEN = 565_000 / 3_000 / 365   # ≈ 0,52 location par jour et par véhicule (chiffres Citiz)
PRIX_TTC = {"bas": 40, "central": 50, "haut": 60}
TVA = 0.20
CO2_NET_T = {"electrique": 1.42, "thermique": -0.07}  # t CO₂e/an par voiture partagée, central net (notebook, §6)
VOITURES_REMPLACEES = 6.5


# ---------------------------------------------------------------------------------------------------- coûts
def couts_annualises(chemin_yaml):
    """Coût annualisé (€ HT 2025/an) par équipement et scénario, depuis couts.yaml : investissements amortis
    linéairement + exploitation ; variante électrique = borne - prime ADVENIR. Même règle que build_couts.py."""
    y = yaml.safe_load(open(chemin_yaml, encoding="utf-8"))
    ipc = y["euros_constants"]["ipc"]
    ref = ipc[y["euros_constants"]["annee_reference"]]
    res = {}
    for equip in ("station_citiz", "arret_bus", "station_velo"):
        dim = y["dimensionnement"][equip]
        for scen, champ in (("bas", "min"), ("central", "montant"), ("haut", "max")):
            base, option, invest = 0.0, 0.0, {}
            for p in y[equip]:
                if p.get("inclus_dans") or p.get("aide"):
                    continue
                m = p.get(champ, p.get("montant")) * ref / ipc.get(p.get("annee", 2025), ref)
                unite = p["unite"]
                q = dim["vehicules_par_station"] if ("véhicule" in unite or "vélo" in unite) else 1
                annuel = m * q if unite.endswith("/an") else m * q / p["duree_vie_ans"]
                invest[p["poste"]] = (m * q, p.get("duree_vie_ans", 1))
                if p.get("option"):
                    option += annuel
                else:
                    base += annuel
            for p in y[equip]:
                if p.get("aide"):  # prime : -min(taux x poste de base, plafond), amortie comme la borne
                    m, duree = invest[p["base"]]
                    option -= min(p["taux"] * m, p["plafond"]) / duree
            res[(equip, scen)] = base
            if option:
                res[(f"{equip}_electrique", scen)] = base + option
    return res


# ------------------------------------------------------------------------------------------------ données
def charger():
    f = pd.read_csv(GOLD / "gold_features.csv", dtype={"code_commune": str, "code_epci": str})
    car = pd.read_csv(SILVER / "carreaux_socio.csv", usecols=["id_carreau", "population"])
    assert (f["id_carreau"].to_numpy() == car["id_carreau"].to_numpy()).all(), "ordre des carreaux différent"
    idx = pd.Series(np.arange(len(f)), index=f["id_carreau"])
    pop = car["population"].fillna(0).to_numpy(float)
    xy = f[["info_x_l93", "info_y_l93"]].to_numpy()

    # couverture à 5 min : réseau piéton (Rôle 2) pour les candidats « ok », estimée sinon
    cov = pd.read_csv(GOLD / "gold_couverture.csv")
    ok = f["info_statut_couverture"].eq("ok").to_numpy()
    cov = cov[cov["id_candidat"].isin(f.loc[ok, "id_carreau"])]
    ci_r, pi_r = idx[cov["id_candidat"]].to_numpy(), idx[cov["id_carreau"]].to_numpy()
    autres = np.flatnonzero(~ok)
    voisins = cKDTree(xy).query_ball_point(xy[autres], RAYON_ESTIME_M)
    ci_e = np.repeat(autres, [len(v) for v in voisins])
    pi_e = np.fromiter((j for v in voisins for j in v), dtype=np.int64, count=len(ci_e))
    ci, pi = np.concatenate([ci_r, ci_e]), np.concatenate([pi_r, pi_e])
    methode = np.where(ok, "reseau_pieton", "estimee_vol_oiseau")

    # desserte existante par type (paires carreau -> offre du Rôle 2)
    paires = pd.read_csv(GOLD / "paires_accessibles_existant.csv", usecols=["id_carreau", "offre_id"])
    offre = pd.read_csv(GOLD / "gold_offre.csv", usecols=["offre_id", "type", "x_l93", "y_l93"])
    paires = paires.merge(offre[["offre_id", "type"]], on="offre_id")
    deja = {}
    for t, spec in TYPES.items():
        d = np.zeros(len(f), bool)
        d[idx[paires.loc[paires["type"].isin(spec["offre"]), "id_carreau"]].to_numpy()] = True
        deja[t] = d

    # poids de potentiel par type (moyenne 1 sur les carreaux)
    sc = pd.read_csv(GOLD / "gold_scores.csv").pivot(index="id_candidat", columns="type", values="score")
    sc = sc.reindex(f["id_carreau"])
    poids = {"citiz": sc["citiz"].to_numpy(), "bus": sc["bus"].to_numpy(),
             "velo": f["menages_sans_voiture_400m"].to_numpy()}
    poids = {t: np.nan_to_num(w / np.nanmean(w), nan=0.0) for t, w in poids.items()}

    # où chaque type est autorisé
    arrets = offre[offre["type"].isin(TYPES["bus"]["offre"])][["x_l93", "y_l93"]].to_numpy()
    autorise = {"citiz": np.ones(len(f), bool),
                "bus": cKDTree(arrets).query(xy)[0] <= BUS_MAX_ARRET_M,
                "velo": f.groupby("code_commune")["cible_velo"].transform("max").to_numpy() > 0}
    return f, pop, xy, ci, pi, methode, deja, poids, autorise


# --------------------------------------------------------------------------------------- usage et marge Citiz
def marge_citiz(f, couts):
    """Locations par jour, statut électrique, coût, recette et marge (bas / central / haut) de chaque carreau."""
    s = pd.read_csv(GOLD / "gold_scores.csv").query("type == 'citiz'").set_index("id_candidat")["score"]
    s = s.reindex(f["id_carreau"]).to_numpy()
    existants = np.sort(s[f["cible_citiz"].to_numpy() == 1])
    p = np.searchsorted(existants, s) / len(existants)        # rang parmi les stations existantes (0 à 1)
    loc_jour = LOC_JOUR_MOYEN * 0.5 * 4 ** p                  # ×0,5 (p=0) ; ×1 (médiane) ; ×2 (p=1)
    borne = f["nb_bornes_7kw_300m"].to_numpy() > 0
    rural = f["territoire"].isin(["4 hors aire · hors aire", "3 petite aire · couronne"]).to_numpy()
    statut = np.where(rural, "hybride conseillé (locations longues)",
                      np.where(borne, "électrique, borne publique à 300 m", "électrique avec borne dédiée"))
    out = pd.DataFrame({"locations_jour": loc_jour, "statut_vehicule": statut})
    for scen in ("bas", "central", "haut"):
        cout = np.where(statut == "électrique avec borne dédiée",
                        couts[("station_citiz_electrique", scen)], couts[("station_citiz", scen)])
        # bas = coûts bas et prix bas ; haut = coûts hauts et prix hauts (fourchettes de sensibilité)
        recette = loc_jour * 365 * PRIX_TTC[scen] / (1 + TVA)
        out[f"cout_{scen}"] = cout
        out[f"marge_{scen}"] = recette - cout
    out["co2_t_an"] = np.where(rural, CO2_NET_T["thermique"], CO2_NET_T["electrique"])
    return out


# ------------------------------------------------------------------------------------------------ sélection
class Couverture:
    def __init__(self, ci, pi, n):
        o = np.argsort(ci, kind="stable")
        self.pi = pi[o]
        self.debut = np.searchsorted(ci[o], np.arange(n + 1))

    def carreaux(self, c):
        return self.pi[self.debut[c]:self.debut[c + 1]]


def glouton(cov, pop, xy, couvert, cout, poids, candidats, budget, dmin, accepter=None, deja_pris=None):
    """Glouton paresseux sur (habitants gagnés × poids) / coût. `cout` est un tableau par carreau.
    `accepter(c)` : contrainte supplémentaire (péréquation, quota). Met à jour `couvert` sur place."""
    gain = lambda c: float(pop[(cells := cov.carreaux(c))][~couvert[cells]].sum())
    tas = [(-gain(c) * poids[c] / cout[c], int(c)) for c in candidats if poids[c] > 0]
    tas = [x for x in tas if x[0] < 0]
    heapq.heapify(tas)
    pris, depense = [], 0.0
    pos = np.empty((0, 2)) if deja_pris is None else xy[deja_pris]
    while tas:
        neg, c = heapq.heappop(tas)
        g = gain(c)
        if g <= 0:
            continue
        ratio = g * poids[c] / cout[c]
        if tas and ratio < -tas[0][0] - 1e-12:
            heapq.heappush(tas, (-ratio, c))
            continue
        if depense + cout[c] > budget:
            continue
        if len(pos) and np.hypot(*(pos - xy[c]).T).min() < dmin:
            continue
        if accepter is not None and not accepter(c):
            continue
        cells = cov.carreaux(c)
        nouveaux = cells[~couvert[cells]]
        couvert[nouveaux] = True
        depense += cout[c]
        pos = np.vstack([pos, xy[c]])
        pris.append((c, float(pop[nouveaux].sum())))
    return pris


def optimiser(budget, quota, repartition, data, mc, couts, scen="central"):
    f, pop, xy, ci, pi, methode, deja, poids, autorise = data
    cov = Couverture(ci, pi, len(f))
    grande = f["territoire"].eq(GRANDES_VILLES).to_numpy()
    lignes = []
    for t in TYPES:
        b = budget * repartition.get(t, 0)
        if b <= 0:
            continue
        couvert = deja[t].copy()
        cand = np.flatnonzero(autorise[t])
        if t == "citiz":
            cout = mc[f"cout_{scen}"].to_numpy()
            marge = mc[f"marge_{scen}"].to_numpy()
            etat = {"somme": 0.0}

            def perequation(c):  # une station déficitaire doit être financée par le surplus déjà acquis
                if etat["somme"] + marge[c] < 0:
                    return False
                etat["somme"] += marge[c]
                return True
            # 1) hors grandes villes : au moins `quota` du budget Citiz ; 2) le reste, partout
            pris = []
            if quota > 0:
                # on finance d'abord les stations rentables des grandes villes qui serviront de péréquation…
                b_gv = b * (1 - quota)
                pris_gv = glouton(cov, pop, xy, couvert, cout, poids[t], cand[grande[cand]], b_gv,
                                  TYPES[t]["dmin"], perequation)
                pris_hv = glouton(cov, pop, xy, couvert, cout, poids[t], cand[~grande[cand]],
                                  b - sum(cout[c] for c, _ in pris_gv), TYPES[t]["dmin"], perequation,
                                  deja_pris=[c for c, _ in pris_gv])
                pris = pris_gv + pris_hv
            else:
                pris = glouton(cov, pop, xy, couvert, cout, poids[t], cand, b, TYPES[t]["dmin"], perequation)
        else:
            c_unit = couts[(TYPES[t]["cout"], scen)]
            cout = np.full(len(f), c_unit)
            pris = glouton(cov, pop, xy, couvert, cout, poids[t], cand, b, TYPES[t]["dmin"])
        for c, g in pris:
            lignes.append({"type": t, "i": c, "habitants_gagnes": g, "cout_annualise": cout[c],
                           "valeur": g * poids[t][c], "ratio": g * poids[t][c] / cout[c]})
    return pd.DataFrame(lignes)


# ------------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget", type=float, default=500_000, help="budget annuel (€ HT, coûts annualisés)")
    ap.add_argument("--quota", type=float, default=0.4, help="part du budget Citiz hors grandes villes-centres")
    ap.add_argument("--repartition", default="citiz=0.6,bus=0.2,velo=0.2")
    ap.add_argument("--courbe", nargs="+", type=float, default=[100_000, 250_000, 500_000, 1_000_000, 2_000_000])
    a = ap.parse_args()
    rep = {k: float(v) for k, v in (kv.split("=") for kv in a.repartition.split(","))}

    couts = couts_annualises(RACINE / "optim" / "couts.yaml")
    data = charger()
    f = data[0]
    mc = marge_citiz(f, couts)
    noms = pd.read_csv(RACINE / "eda-citiz" / "donnees" / "communes_croisees.csv", dtype={"code": str},
                       usecols=["code", "nom"]).set_index("code")["nom"]

    sel = optimiser(a.budget, a.quota, rep, data, mc, couts)
    sel = sel.sort_values("ratio", ascending=False).reset_index(drop=True)
    i = sel["i"].to_numpy()
    est_citiz = (sel["type"] == "citiz").to_numpy()
    out = pd.DataFrame({
        "rang": np.arange(1, len(sel) + 1), "type": sel["type"], "id_carreau": f["id_carreau"].to_numpy()[i],
        "commune": f["code_commune"].to_numpy()[i], "nom_commune": noms.reindex(f["code_commune"].to_numpy()[i]).to_numpy(),
        "territoire": f["territoire"].to_numpy()[i],
        "habitants_gagnes": sel["habitants_gagnes"].round(0), "cout_annualise": sel["cout_annualise"].round(0),
        "cout_par_habitant": (sel["cout_annualise"] / sel["habitants_gagnes"]).round(1),
        "score_potentiel": np.round([data[7][t][j] for t, j in zip(sel["type"], i)], 3),
        "locations_jour": np.where(est_citiz, mc["locations_jour"].to_numpy()[i].round(2), np.nan),
        "vehicule": np.where(est_citiz, mc["statut_vehicule"].to_numpy()[i], ""),
        **{f"marge_{s}": np.where(est_citiz, mc[f"marge_{s}"].to_numpy()[i].round(0), np.nan)
           for s in ("bas", "central", "haut")},
        "co2_evite_t_an": np.where(est_citiz, mc["co2_t_an"].to_numpy()[i], np.nan),
        "couverture": data[5][i], "lon": f["info_lon"].to_numpy()[i], "lat": f["info_lat"].to_numpy()[i],
    })
    out["budget_cumule"] = out["cout_annualise"].cumsum()
    out["habitants_cumules"] = out["habitants_gagnes"].cumsum()
    out.to_csv(GOLD / "portefeuille_priorise.csv", index=False)

    # courbe budget -> habitants, avec et sans quota (coût de l'équité)
    courbe = []
    for b in a.courbe:
        for q in sorted({0.0, a.quota}):
            s = optimiser(b, q, rep, data, mc, couts)
            c = s[s["type"] == "citiz"]
            courbe.append({"budget_annuel": b, "quota_hors_grandes_villes": q, "equipements": len(s),
                           "habitants_gagnes": round(s["habitants_gagnes"].sum()),
                           **{f"nb_{t}": int((s["type"] == t).sum()) for t in TYPES},
                           **{f"habitants_{t}": round(s.loc[s["type"] == t, "habitants_gagnes"].sum()) for t in TYPES},
                           "part_citiz_hors_grandes_villes": round(float((~f["territoire"].eq(GRANDES_VILLES)
                                                                          .to_numpy()[c["i"]]).mean()), 3) if len(c) else 0,
                           "marge_citiz_central": round(mc["marge_central"].to_numpy()[c["i"]].sum()),
                           "co2_evite_t_an": round(mc["co2_t_an"].to_numpy()[c["i"]].sum(), 1)})
    courbe = pd.DataFrame(courbe)
    courbe.to_csv(GOLD / "courbe_budget_portefeuille.csv", index=False)

    ci = out[out["type"] == "citiz"]
    lignes = [
        "Portefeuille priorisé sous budget (objectif 3)", "=" * 46,
        f"Budget annuel {a.budget:,.0f} € HT ; répartition {a.repartition} ; quota Citiz hors grandes villes {a.quota:.0%}",
        "Coûts annualisés (central, € HT/an) : " + ", ".join(f"{k[0]} {v:,.0f}" for k, v in couts.items() if k[1] == "central"),
        f"Équipements : {len(out)} ({', '.join(f'{t} {int((out.type == t).sum())}' for t in TYPES)})",
        f"Habitants nouvellement desservis (somme des types) : {out.habitants_gagnes.sum():,.0f}",
        *[f"  {t:6s}: {out.loc[out.type == t, 'habitants_gagnes'].sum():>9,.0f} hab., "
          f"{out.loc[out.type == t, 'cout_annualise'].sum() / max(out.loc[out.type == t, 'habitants_gagnes'].sum(), 1):6.1f} €/hab./an"
          for t in TYPES],
        f"Citiz : {len(ci)} stations, dont {(ci.territoire != GRANDES_VILLES).mean():.0%} hors grandes villes ; "
        f"marge totale bas / central / haut : {ci.marge_bas.sum():,.0f} / {ci.marge_central.sum():,.0f} / {ci.marge_haut.sum():,.0f} € HT/an",
        f"  stations déficitaires financées par péréquation : {(ci.marge_central < 0).sum()}",
        f"  véhicules : " + ", ".join(f"{k} {v}" for k, v in ci.vehicule.value_counts().items()),
        f"  CO₂ évité ≈ {ci.co2_evite_t_an.sum():,.0f} t/an (net, central) ; voitures privées en moins ≈ {len(ci) * VOITURES_REMPLACEES:,.0f}",
        f"Candidats à couverture estimée (hors réseau piéton) retenus : {(out.couverture != 'reseau_pieton').sum()}",
        "", "Courbe budget -> habitants :", courbe.to_string(index=False),
        "", "15 premiers :", out.head(15)[["rang", "type", "nom_commune", "habitants_gagnes", "cout_par_habitant",
                                           "locations_jour", "marge_central", "vehicule"]].to_string(index=False),
        "", "Contrôles :",
        f"  [{'OK' if out.cout_annualise.sum() <= a.budget + 1 else 'ERREUR'}] budget respecté",
        f"  [{'OK' if ci.marge_central.sum() >= 0 else 'ERREUR'}] Σ marges Citiz ≥ 0 (central)",
        f"  [{'OK' if (out.habitants_gagnes > 0).all() else 'ERREUR'}] chaque équipement gagne des habitants",
    ]
    rapport = "\n".join(lignes)
    (GOLD / "portefeuille_rapport.txt").write_text(rapport, encoding="utf-8")
    print(rapport)


if __name__ == "__main__":
    main()
