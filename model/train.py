"""Rôle 3 - modèles de potentiel par type d'équipement et évaluation honnête (gold_scores).

Entrée : data/gold/gold_features.csv (model/build_features.py).
Trois modèles LightGBM, mêmes variables :
- citiz : classifieur « sosie » - le carreau a-t-il une station Citiz à moins de 200 m ?
- velo  : idem pour une station vélo
- bus   : régression du log des passages par jour de l'arrêt le plus proche (carreaux à moins de 200 m d'un arrêt)
La cible Citiz/vélo reflète l'offre existante (choix passés des opérateurs), pas une demande mesurée :
le score sert à classer des lieux, pas à prédire une probabilité de succès.

Évaluation : validation croisée par blocs géographiques (GroupKFold à 5 plis par EPCI : un EPCI n'est
jamais à la fois en entraînement et en test), contre des références simples et, si installé, XGBoost.
Métriques, calculées pli par pli puis moyennées : aire précision-rappel (déséquilibre ~1 pour 200),
aire ROC, rappel dans le top 1 % ;
Spearman et erreur absolue (log) pour le bus. Résultats aussi par type de territoire.

Sorties (CSV) :
- data/gold/gold_scores.csv            id_candidat, type, score, rang_pct (score sur tous les carreaux)
- model/evaluation_modele.csv          une ligne par type x modèle x périmètre
- model/importance_variables.csv       importance SHAP moyenne (|contribution|) par type et variable
Usage : py -m model.train   ou   python model/train.py [--racine .] [--sans-xgboost]
"""
import argparse
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, mean_absolute_error, roc_auc_score
from sklearn.model_selection import GroupKFold

GRAINE = 42
CATEGORIES = ["territoire", "grille_densite", "CATEAAV2020"]
NON_VARIABLES = {"id_carreau", "code_commune", "code_epci"}
# variables retirées par type : elles décrivent directement la cible (fuite)
FUITES = {
    # bornes : une partie des bornes publiques ont été posées pour les stations Citiz (causalité inversée) ;
    # elles servent à la règle électrique, pas au potentiel
    "citiz": {"nb_bornes_300m", "nb_bornes_7kw_300m", "dist_borne_m"},
    "velo": {"nb_stations_velo_400m"},
    "bus": {"nb_arrets_400m", "passages_jour_400m", "nb_arrets_lourds_400m", "dist_arret_lourd_m", "dist_arret_m"},
}
# Modèle volontairement simple : avec ~1 500 positifs, des arbres profonds sur-apprennent les villes vues
# (essai à 31 feuilles et pondération des positifs : battu par la référence population).
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=500, reg_lambda=10,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=GRAINE, verbose=-1)


def variables(df, t):
    return [c for c in df.columns
            if c not in NON_VARIABLES and not c.startswith(("cible_", "info_")) and c not in FUITES[t]]


def rappel_top(y, s, part=0.01):
    k = max(1, int(len(s) * part))
    top = np.argsort(-s)[:k]
    return y[top].sum() / max(y.sum(), 1)


def metriques_classif(y, s):
    return {"aire_pr": average_precision_score(y, s), "auc_roc": roc_auc_score(y, s),
            "rappel_top1pct": rappel_top(y, s), "positifs": int(y.sum()), "n": len(y)}


def modele(t, pos=None, neg=None):
    if t == "bus":
        return lgb.LGBMRegressor(**PARAMS)
    return lgb.LGBMClassifier(**PARAMS)


def xgb_modele(t, pos=None, neg=None):
    import xgboost as xgb
    kw = dict(n_estimators=300, learning_rate=0.03, max_depth=3, min_child_weight=50, reg_lambda=10,
              subsample=0.8, colsample_bytree=0.8,
              tree_method="hist", enable_categorical=True, random_state=GRAINE, n_jobs=-1)
    if t == "bus":
        return xgb.XGBRegressor(**kw)
    return xgb.XGBClassifier(**kw, eval_metric="aucpr")


def predire(m, X, t):
    return m.predict(X) if t == "bus" else m.predict_proba(X)[:, 1]


def validation(df, t, avec_xgb):
    """Prédictions hors pli (out-of-fold) par EPCI pour LightGBM (et XGBoost)."""
    cols = variables(df, t)
    if t == "bus":
        d = df[df["cible_bus_passages_jour"].notna()]
        y = np.log1p(d["cible_bus_passages_jour"].to_numpy())
    else:
        d = df
        y = d[f"cible_{t}"].to_numpy()
    X, g = d[cols], d["code_epci"].to_numpy()
    oof = {"lightgbm": np.zeros(len(d))}
    plis = np.zeros(len(d), dtype=int)
    if avec_xgb:
        oof["xgboost"] = np.zeros(len(d))
    for k, (tr, te) in enumerate(GroupKFold(n_splits=5).split(X, y, g)):
        pos = y[tr].sum() if t != "bus" else None
        neg = len(tr) - pos if t != "bus" else None
        m = modele(t, pos, neg).fit(X.iloc[tr], y[tr])
        oof["lightgbm"][te] = predire(m, X.iloc[te], t)
        if avec_xgb:
            mx = xgb_modele(t, pos, neg).fit(X.iloc[tr], y[tr])
            oof["xgboost"][te] = predire(mx, X.iloc[te], t)
        plis[te] = k
        print(f"  {t} pli {k + 1}/5")
    # références simples
    oof["ref_population"] = d["pop_400m"].to_numpy()
    oof["ref_pop_sans_voiture"] = d["menages_sans_voiture_400m"].to_numpy()
    return d, y, oof, plis


def evaluer(d, y, oof, plis, t):
    """Métriques calculées pli par pli puis moyennées : chaque modèle de pli a sa propre échelle de scores,
    on ne mélange donc pas les plis. Par territoire : rangs normalisés dans chaque pli, puis regroupés."""
    lignes = []
    for nom_m, s in oof.items():
        par_pli = []
        for k in np.unique(plis):
            m = plis == k
            if t == "bus":
                r = {"spearman": spearmanr(y[m], s[m]).statistic}
                if not nom_m.startswith("ref_"):
                    r["erreur_abs_log"] = mean_absolute_error(y[m], s[m])
            else:
                r = metriques_classif(y[m], s[m])
            par_pli.append(r)
        pp = pd.DataFrame(par_pli)
        ligne = {"type": t, "modele": nom_m, "perimetre": "toute l'ARA (moyenne des 5 plis)", "n": len(y)}
        for c in pp.columns:
            if c in ("n", "positifs"):
                continue
            ligne[c] = pp[c].mean()
            ligne[f"{c}_ecart_type"] = pp[c].std()
        if t != "bus":
            ligne["positifs"] = int(y.sum())
        lignes.append(ligne)
    rangs = {nom: pd.Series(s).groupby(plis).rank(pct=True).to_numpy() for nom, s in oof.items()}
    for v in d["territoire"].dropna().unique():
        m = (d["territoire"] == v).to_numpy()
        if (t != "bus" and y[m].sum() < 5) or m.sum() < 50:
            continue
        for nom_m, s in rangs.items():
            if t == "bus":
                r = {"spearman": spearmanr(y[m], s[m]).statistic}
            else:
                r = {"aire_pr": average_precision_score(y[m], s[m]), "auc_roc": roc_auc_score(y[m], s[m]),
                     "positifs": int(y[m].sum())}
            lignes.append({"type": t, "modele": nom_m, "perimetre": f"territoire : {v}", "n": int(m.sum()), **r})
    return lignes


def importance_shap(m, X, t, n=20000):
    ech = X.sample(min(n, len(X)), random_state=GRAINE)
    contrib = m.predict(ech, pred_contrib=True) if t == "bus" else m.predict_proba(ech, pred_contrib=True)
    contrib = np.asarray(contrib)[:, :-1]  # dernière colonne = valeur de base
    return pd.DataFrame({"type": t, "variable": X.columns, "shap_moyen_abs": np.abs(contrib).mean(axis=0)}) \
        .sort_values("shap_moyen_abs", ascending=False)


def main(racine: Path, avec_xgb: bool):
    gold, dossier_modele = racine / "data" / "gold", racine / "model"
    df = pd.read_csv(gold / "gold_features.csv", dtype={"code_commune": str, "code_epci": str,
                                                        "CATEAAV2020": str, "grille_densite": str})
    for c in CATEGORIES:
        df[c] = df[c].astype("category")
    print(f"gold_features : {df.shape}")
    if avec_xgb:
        try:
            import xgboost  # noqa: F401
        except ImportError:
            print("  xgboost absent : contrôle XGBoost ignoré")
            avec_xgb = False

    evals, importances, scores = [], [], []
    for t in ["citiz", "velo", "bus"]:
        print(f"[{t}] validation par EPCI")
        d, y, oof, plis = validation(df, t, avec_xgb)
        evals += evaluer(d, y, oof, plis, t)
        # modèle final sur toutes les données, appliqué à tous les carreaux
        cols = variables(df, t)
        pos = y.sum() if t != "bus" else None
        m = modele(t, pos, len(y) - pos if t != "bus" else None).fit(d[cols], y)
        s = predire(m, df[cols], t)
        if t == "bus":
            s = np.expm1(s)  # passages par jour prédits
        scores.append(pd.DataFrame({"id_candidat": df["id_carreau"], "type": t, "score": s,
                                    "rang_pct": pd.Series(s).rank(pct=True).to_numpy()}))
        importances.append(importance_shap(m, d[cols], t))

    ev = pd.DataFrame(evals)
    num = ev.select_dtypes("float").columns
    ev[num] = ev[num].round(4)
    ev.to_csv(dossier_modele / "evaluation_modele.csv", index=False)
    pd.concat(importances).round(5).to_csv(dossier_modele / "importance_variables.csv", index=False)
    sc = pd.concat(scores)
    sc["score"] = sc["score"].round(6)
    sc["rang_pct"] = sc["rang_pct"].round(5)
    sc.to_csv(gold / "gold_scores.csv", index=False)
    cols_aff = [c for c in ["type", "modele", "aire_pr", "aire_pr_ecart_type", "rappel_top1pct", "auc_roc",
                            "spearman", "erreur_abs_log"] if c in ev]
    print(ev[ev["perimetre"].str.startswith("toute")][cols_aff].to_string(index=False))
    print(f"écrit gold_scores.csv ({len(sc)} lignes), evaluation_modele.csv, importance_variables.csv")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument("--sans-xgboost", action="store_true")
    a = p.parse_args()
    main(a.racine, not a.sans_xgboost)
