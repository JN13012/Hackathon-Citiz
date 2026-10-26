"""Rôle 3 - table de variables du modèle (gold_features) : une ligne par carreau candidat de 200 m (ARA).

Fusionne, pour chaque carreau, ce qui décrit le lieu et son entourage à 5 min à pied :
- carreaux_socio (Rôle 3)        habitants, ménages, revenus, âges, ménages sans voiture, équipements, emplois
- arrets (Rôle 1)                arrêts et passages par jour, par mode
- stations_partagees (Rôle 4)    stations Citiz, vélo, autres autopartages
- bornes_ara (EDA)               bornes de recharge publiques et leur puissance
- communes_croisees (EDA)        type de territoire (aire d'attraction), densité, population de la commune
- communes_epci                  EPCI de la commune, pour la validation croisée par blocs
- candidats_couverture, gold_couverture, paires_accessibles_existant (Rôle 2, réseau piéton réel)
                                 population à 5 min à pied et habitants gagnés par type d'équipement

Voisinage : disque de 400 m autour du centre du carreau (≈ 5 min à pied à vol d'oiseau), identique pour
tous les carreaux, y compris les 97 462 candidats que le réseau piéton ne sait pas raccorder. Les variables
routées du Rôle 2 sont ajoutées à part (vides quand le candidat n'est pas calculable).

Colonnes :
- `cible_*`   ce que les modèles apprennent (à ne jamais utiliser comme variable)
- `info_*`    utile à l'optimiseur ou à l'affichage, exclu du modèle (fuite de la cible)
- le reste    variables explicatives

Tout est lu et écrit en CSV (Parquet bloqué sur certains postes).
Usage : py -m model.build_features   ou   python model/build_features.py [--racine .]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy import sparse
from scipy.spatial import cKDTree

RAYON_M = 400        # voisinage « 5 min à pied » à vol d'oiseau
RAYON_CIBLE_M = 200  # une station existante à moins de 200 m du centre = carreau positif
RAYON_BORNE_M = 300  # borne de recharge « sur place » pour la règle électrique
MODES_LOURDS = {"tram", "metro", "funiculaire", "cable_car"}
# type d'équipement -> types d'offre existante qui desservent déjà (mêmes groupes que optim/optimise.py)
OFFRE_PAR_TYPE = {
    "citiz": {"citiz"},
    "bus": {"bus", "tram", "metro", "trolleybus", "funiculaire", "cable_car", "ferry"},
    "velo": {"velo"},
}


def lire(chemin, **kw):
    if not chemin.exists():
        raise SystemExit(f"Fichier manquant : {chemin}")
    return pd.read_csv(chemin, **kw)


def voisins(xy, rayon):
    """Matrice creuse carreau x carreau : 1 si les centres sont à moins de `rayon` (diagonale comprise)."""
    arbre = cKDTree(xy)
    paires = arbre.query_pairs(rayon, output_type="ndarray")
    n = len(xy)
    i = np.concatenate([paires[:, 0], paires[:, 1], np.arange(n)])
    j = np.concatenate([paires[:, 1], paires[:, 0], np.arange(n)])
    return sparse.csr_matrix((np.ones(len(i)), (i, j)), shape=(n, n))


def compte(xy_centres, xy_points, rayon, poids=None):
    """Nombre (ou somme des poids) de points à moins de `rayon` de chaque centre."""
    if len(xy_points) == 0:
        return np.zeros(len(xy_centres))
    listes = cKDTree(xy_points).query_ball_point(xy_centres, rayon)
    if poids is None:
        return np.array([len(l) for l in listes], dtype=float)
    poids = np.asarray(poids, dtype=float)
    return np.array([poids[l].sum() if l else 0.0 for l in listes])


def distance_min(xy_centres, xy_points):
    if len(xy_points) == 0:
        return np.full(len(xy_centres), np.nan)
    return cKDTree(xy_points).query(xy_centres)[0]


def main(racine: Path):
    silver, gold = racine / "data" / "silver", racine / "data" / "gold"
    donnees_eda = racine / "eda-citiz" / "donnees"

    car = lire(silver / "carreaux_socio.csv", dtype={"code_commune": str, "code_iris": str})
    xy = car[["x_centre", "y_centre"]].to_numpy()
    n = len(car)
    print(f"carreaux : {n}")

    # --- 1. Agrégats socio sur le disque de 400 m -------------------------------------------------------
    W = voisins(xy, RAYON_M)
    pop = car["population"].fillna(0).to_numpy()
    men = car["menages"].fillna(0).to_numpy()
    f = pd.DataFrame({"id_carreau": car["id_carreau"]})
    f["population"] = pop
    f["revenu"] = car["revenu"]
    f["part_menages_sans_voiture"] = car["part_menages_sans_voiture"]
    f["pop_400m"] = W @ pop
    f["menages_400m"] = W @ men
    f["nb_carreaux_habites_400m"] = np.asarray(W.sum(axis=1)).ravel()
    pond = lambda col, w: (W @ (car[col].fillna(0).to_numpy() * w)) / np.maximum(W @ w, 1e-9)
    f["menages_sans_voiture_400m"] = W @ (car["part_menages_sans_voiture"].fillna(0).to_numpy() * men)
    f["part_sans_voiture_400m"] = pond("part_menages_sans_voiture", men)
    f["revenu_400m"] = pond("revenu", pop)
    for c in ["part_moins_18", "part_18_39", "part_40_64", "part_65_plus"]:
        f[f"{c}_400m"] = pond(c, pop)
    for c in ["emplois", "nb_equipements", "nb_equip_services", "nb_equip_commerces", "nb_equip_enseignement",
              "nb_equip_sante", "nb_equip_transports", "nb_equip_sports_culture", "nb_equip_tourisme"]:
        f[f"{c}_400m"] = W @ car[c].fillna(0).to_numpy()
    f["donnee_masquee"] = car["donnee_masquee"].astype(int)

    # --- 2. Transports en commun -----------------------------------------------------------------------
    arr = lire(silver / "arrets.csv")
    axy = arr[["x_l93", "y_l93"]].to_numpy()
    f["nb_arrets_400m"] = compte(xy, axy, RAYON_M)
    f["passages_jour_400m"] = compte(xy, axy, RAYON_M, arr["nb_passages_jour"].fillna(0))
    lourds = arr["mode"].isin(MODES_LOURDS).to_numpy()
    f["nb_arrets_lourds_400m"] = compte(xy, axy[lourds], RAYON_M)
    f["dist_arret_lourd_m"] = distance_min(xy, axy[lourds])
    f["dist_arret_m"] = distance_min(xy, axy)
    # gare (approximation : carreau avec un équipement BPE du domaine transports)
    gare = car["nb_equip_transports"].fillna(0).to_numpy() > 0
    f["dist_equip_transport_m"] = distance_min(xy, xy[gare])

    # --- 3. Mobilité partagée ---------------------------------------------------------------------------
    st = lire(silver / "stations_partagees.csv")
    sxy = {t: st.loc[st["type"] == t, ["x_l93", "y_l93"]].to_numpy() for t in ["citiz", "velo", "autopartage"]}
    f["nb_stations_velo_400m"] = compte(xy, sxy["velo"], RAYON_M)
    f["nb_autopartage_autre_400m"] = compte(xy, sxy["autopartage"], RAYON_M)

    # --- 4. Bornes de recharge --------------------------------------------------------------------------
    bornes_chemin = donnees_eda / "bornes_ara.csv"
    if bornes_chemin.exists():
        b = pd.read_csv(bornes_chemin)
        bx, by = Transformer.from_crs(4326, 2154, always_xy=True).transform(b["lon"].to_numpy(), b["lat"].to_numpy())
        bxy = np.column_stack([bx, by])
        f["nb_bornes_300m"] = compte(xy, bxy, RAYON_BORNE_M)
        f["nb_bornes_7kw_300m"] = compte(xy, bxy[b["kw"].fillna(0).to_numpy() >= 7], RAYON_BORNE_M)
        f["dist_borne_m"] = distance_min(xy, bxy)
    else:
        print(f"  (bornes absentes : {bornes_chemin})")

    # --- 5. Commune, territoire, EPCI ------------------------------------------------------------------
    com = lire(donnees_eda / "communes_croisees.csv", dtype={"code": str, "CATEAAV2020": str})
    com = com.rename(columns={"code": "code_commune", "population": "pop_commune",
                              "densite_hab_km2": "densite_commune", "dens": "grille_densite"})
    com = com[["code_commune", "pop_commune", "densite_commune", "grille_densite", "territoire",
               "CATEAAV2020", "pop_aire"]]
    # code Filosofi 2021 ; à défaut, commune actuelle de l'IRIS (fusions) ; arrondissements de Lyon -> Lyon
    code = car["code_commune"].str.zfill(5)
    code = code.where(code.isin(com["code_commune"]), car["code_iris"].str[:5])
    code = code.where(~code.fillna("").str.match(r"^6938\d$"), "69123")
    f["code_commune"] = code
    f = f.merge(com, on="code_commune", how="left")
    ep = lire(silver / "communes_epci.csv", dtype={"code_insee": str, "code_epci": str})
    f = f.merge(ep[["code_insee", "code_epci"]].rename(columns={"code_insee": "code_commune"}),
                on="code_commune", how="left")
    print(f"  sans territoire : {f['territoire'].isna().sum()} carreaux ; sans EPCI : {f['code_epci'].isna().sum()}")
    f["pole"] = f["territoire"].fillna("").str.contains("pôle").astype(int)

    # --- 6. Cibles et informations (exclues du modèle) ------------------------------------------------
    f["cible_citiz"] = (compte(xy, sxy["citiz"], RAYON_CIBLE_M) > 0).astype(int)
    f["cible_velo"] = (compte(xy, sxy["velo"], RAYON_CIBLE_M) > 0).astype(int)
    # bus : passages par jour de l'arrêt le plus proche s'il est à moins de 200 m, sinon vide
    d, k = cKDTree(axy).query(xy)
    f["cible_bus_passages_jour"] = np.where(d <= RAYON_CIBLE_M, arr["nb_passages_jour"].to_numpy()[k], np.nan)
    f["info_dist_citiz_m"] = distance_min(xy, sxy["citiz"])
    f["info_dist_velo_m"] = distance_min(xy, sxy["velo"])
    f["info_x_l93"], f["info_y_l93"] = xy[:, 0], xy[:, 1]
    f["info_lon"], f["info_lat"] = car["lon"], car["lat"]

    # --- 7. Couverture à pied réelle (Rôle 2) et habitants gagnés par type -----------------------------
    cc = gold / "candidats_couverture.csv"
    if cc.exists():
        cand = pd.read_csv(cc, usecols=["id_candidat", "statut", "population_couverte"])
        cand = cand.rename(columns={"id_candidat": "id_carreau", "statut": "info_statut_couverture",
                                    "population_couverte": "info_pop_5min_pied"})
        f = f.merge(cand, on="id_carreau", how="left")
        rel = pd.read_csv(gold / "gold_couverture.csv", usecols=["id_candidat", "id_carreau"])
        paires = pd.read_csv(gold / "paires_accessibles_existant.csv", usecols=["id_carreau", "offre_id"])
        offre = pd.read_csv(gold / "gold_offre.csv", usecols=["offre_id", "type"])
        paires = paires.merge(offre, on="offre_id", how="left")
        popc = car.set_index("id_carreau")["population"]
        rel["pop"] = rel["id_carreau"].map(popc).fillna(0)
        for t, types in OFFRE_PAR_TYPE.items():
            deja = set(paires.loc[paires["type"].isin(types), "id_carreau"])
            gagne = rel.loc[~rel["id_carreau"].isin(deja)].groupby("id_candidat")["pop"].sum()
            f[f"info_pop_gagnee_{t}"] = f["id_carreau"].map(gagne)
            ok = f["info_statut_couverture"].eq("ok")
            f.loc[ok & f[f"info_pop_gagnee_{t}"].isna(), f"info_pop_gagnee_{t}"] = 0
            f[f"info_deja_desservi_{t}"] = f["id_carreau"].isin(deja).astype(int)
    else:
        print(f"  (couverture du Rôle 2 absente : {cc})")

    num = f.select_dtypes("float").columns
    f[num] = f[num].round(4)
    sortie = gold / "gold_features.csv"
    f.to_csv(sortie, index=False)
    print(f"écrit {sortie} : {f.shape[0]} lignes x {f.shape[1]} colonnes")
    print(f"  positifs Citiz {f['cible_citiz'].sum()}, vélo {f['cible_velo'].sum()}, "
          f"bus renseignés {f['cible_bus_passages_jour'].notna().sum()}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent.parent,
                   help="racine du dépôt (contient data/ et eda-citiz/)")
    main(p.parse_args().racine)
