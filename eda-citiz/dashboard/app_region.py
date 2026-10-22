"""Tableau de bord Citiz : où ouvrir les prochaines stations, électriques ou non, et à quel prix.

Lancer depuis la racine du projet :  python -m streamlit run eda-citiz/dashboard/app.py
Données lues (CSV uniquement, dans eda-citiz/) :
  donnees/powerbi/carte_acces_200m.csv     carreaux de 200 m (cellule 15 du notebook)       obligatoire
  donnees/stations_croisees.csv            stations Citiz croisées (cellule 10)               conseillé
  donnees/communes_croisees.csv            communes croisées (cellule 10)                     conseillé
  donnees/bornes_ara.csv                   bornes de recharge, créé ici depuis irve_consolide.csv
  donnees/vehicle_status_*.csv, citiz_*_vehicle_types_*.json   relevé GBFS de la flotte (facultatif)
"""
import glob
import json
import os
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from scipy import sparse
from scipy.spatial import cKDTree

st.set_page_config(page_title="Citiz · Où ouvrir la prochaine station ?", page_icon="🚗", layout="wide")

RACINE = Path(os.environ.get("CITIZ_RACINE", Path(__file__).resolve().parent.parent))   # dossier eda-citiz
DONNEES = RACINE / "donnees"
F200 = DONNEES / "powerbi" / "carte_acces_200m.csv"
FSTX = DONNEES / "stations_croisees.csv"
FSTATIONS = RACINE / "sorties" / "stations.csv"
FCOM = DONNEES / "communes_croisees.csv"
FBORNES = DONNEES / "bornes_ara.csv"
FIRVE = DONNEES / "irve_consolide.csv"
BBOX_ARA = (2.06, 44.11, 7.19, 46.81)
RAYON_BORNE = 300              # une borne publique « utilisable » est à moins de 300 m de la station
MAX_POINTS = 60_000            # au-delà, la carte échantillonne pour rester fluide
COULEURS = np.array([[143, 163, 184], [255, 213, 79], [251, 140, 0], [229, 57, 53], [127, 0, 0]])
LIBELLES_NB = ["0 · aucune option", "1 · faible (1 à 2)", "2 · moyen (3 à 5)", "3 · élevé (6 à 10)", "4 · très élevé (plus de 10)"]
LIBELLES_HAB = ["0 · aucune option", "1 · faible", "2 · moyen", "3 · élevé", "4 · très élevé"]
BLEU, ROUGE, VERT, GRIS = "#1F5A8C", "#C0392B", "#2E7D32", "#8FA3B8"
RESEAUX = {"Métropole de Lyon": "citiz_lyon", "Alpes Loire": "citiz_alpes_loire", "Auto-en-Velay": "citiz_auto_en_velay"}
EV_OK, EV_BORNE, EV_NON = "Électrique : borne publique à 300 m", "Électrique : borne à installer", "Thermique ou hybride"
COULEUR_EV = alt.Scale(domain=[EV_OK, EV_BORNE, EV_NON], range=[VERT, "#81C784", GRIS])

COLONNES = ["lon", "lat", "commune", "aire", "territoire", "habitants", "options_pour_1000_hab",
            "nb_arrets_5min", "nb_stations_citiz_5min", "niveau"]


def hexa(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


def esp(v, d=0):
    """Nombre lisible à la française : 12 345."""
    return f"{v:,.{d}f}".replace(",", " ")


def euros(v):
    return f"{v:+,.0f} €".replace(",", " ")


def metres(lon, lat):
    """Coordonnées en mètres (approximation locale, suffisante pour des distances de quelques centaines de mètres)."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return np.c_[lon * 111_320 * np.cos(np.radians(45.3)), lat * 110_540]


def voisins(xy_de, arbre_vers, rayon):
    """Matrice creuse (départ × arrivée) des paires à moins de `rayon` mètres."""
    m = cKDTree(xy_de).sparse_distance_matrix(arbre_vers, rayon, output_type="coo_matrix")
    return sparse.csr_matrix((np.ones_like(m.data), (m.row, m.col)), shape=m.shape)


# ---------- Chargement ----------
def preparer(df: pd.DataFrame) -> pd.DataFrame:
    manque = [c for c in COLONNES if c not in df.columns]
    if manque:
        raise ValueError(f"Colonnes absentes de carte_acces_200m.csv : {manque}. Relance les cellules 14 et 15 du notebook.")
    df = df.copy()
    if "nb_options_5min" not in df.columns:
        df["nb_options_5min"] = df["nb_arrets_5min"] + df["nb_stations_citiz_5min"]
    if "niveau_nb" not in df.columns:
        df["niveau_nb"] = pd.cut(df["nb_options_5min"], [-0.01, 0, 2, 5, 10, np.inf],
                                 labels=["0 aucune option", "1 faible", "2 moyen", "3 élevé", "4 très élevé"]).astype(str)
    df["aire"] = df["aire"].fillna("Hors aire d'attraction").astype(str)
    df["territoire"] = df["territoire"].fillna("Non classé").astype(str)
    df["commune"] = df["commune"].fillna("?").astype(str)
    xy = metres(df["lon"], df["lat"])
    df["x"], df["y"] = xy[:, 0], xy[:, 1]
    df["rang_nb"] = df["niveau_nb"].astype(str).str[0].astype(int)
    df["rang_hab"] = df["niveau"].astype(str).str[0].astype(int)
    df["h_sans"] = df["habitants"] * (df["nb_options_5min"] == 0)
    df["h_citiz"] = df["habitants"] * (df["nb_stations_citiz_5min"] > 0)
    df["position"] = df["territoire"].str.split(" · ").str[-1]
    df["taille_aire"] = df["territoire"].str[0]          # 1 grande aire, 2 moyenne, 3 petite, 4 hors aire
    return df.reset_index(drop=True)


@st.cache_data(show_spinner="Chargement des carreaux de 200 m…")
def charger(chemin: str) -> pd.DataFrame:
    return preparer(pd.read_csv(chemin))


@st.cache_data(show_spinner="Chargement des stations Citiz…")
def charger_stations():
    if FSTX.exists():
        s = pd.read_csv(FSTX, dtype={"station_id": str, "code": str})
    elif FSTATIONS.exists():
        s = pd.read_csv(FSTATIONS, dtype={"station_id": str, "departement": str})
        s = s[s["region"] == "Auvergne-Rhône-Alpes"]
        if "ouverture_prochaine" in s.columns:
            s = s[~s["ouverture_prochaine"].astype(bool)]
        s = s[~s["nom"].astype(str).str.contains("hors-circuit|DEMOBOX", case=False)]
    else:
        return None
    s = s.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    s["network"] = s["reseau"].map(RESEAUX) if "reseau" in s else None
    return s


@st.cache_data(show_spinner="Préparation des bornes de recharge (une seule fois)…")
def charger_bornes():
    """Bornes publiques en accès libre de la région. Créé depuis le fichier consolidé IRVE, puis relu en CSV léger."""
    if FBORNES.exists():
        return pd.read_csv(FBORNES)
    if not FIRVE.exists():
        return None
    cols = ["id_pdc_itinerance", "puissance_nominale", "consolidated_longitude", "consolidated_latitude", "condition_acces"]
    b = pd.read_csv(FIRVE, usecols=cols, low_memory=False)
    x0, y0, x1, y1 = BBOX_ARA
    b = b[b["consolidated_longitude"].between(x0, x1) & b["consolidated_latitude"].between(y0, y1)]
    b = b[~b["condition_acces"].astype(str).str.contains("serv", case=False)]       # « Accès réservé » exclu
    b = b.drop_duplicates("id_pdc_itinerance")
    b["kw"] = pd.to_numeric(b["puissance_nominale"], errors="coerce")
    b.loc[b["kw"] > 1000, "kw"] /= 1000                                           # quelques fichiers donnent des W
    b = b.rename(columns={"consolidated_longitude": "lon", "consolidated_latitude": "lat"})[["lon", "lat", "kw"]].dropna()
    try:
        b.to_csv(FBORNES, index=False)
    except OSError:
        pass
    return b.reset_index(drop=True)


@st.cache_data(show_spinner="Lecture du relevé de la flotte…")
def charger_flotte():
    """Voitures vues dans le relevé GBFS : station, motorisation, part des relevés où elle est réservée."""
    fichiers = sorted(glob.glob(str(DONNEES / "vehicle_status_*.csv")))
    if not fichiers:
        return None, 0
    types = []
    for f in glob.glob(str(DONNEES / "citiz_*_vehicle_types_*.json")):
        reseau = Path(f).name.split("_vehicle_types")[0]
        try:
            for t in json.load(open(f, encoding="utf-8"))["data"]["vehicle_types"]:
                types.append((reseau, str(t["vehicle_type_id"]), t.get("propulsion_type", "?")))
        except (KeyError, ValueError):
            continue
    v = pd.concat([pd.read_csv(f, dtype={"vehicle_type_id": str, "station_id": str, "vehicle_id": str}) for f in fichiers])
    releves = v["ts"].nunique()
    T = pd.DataFrame(types, columns=["network", "vehicle_type_id", "motorisation"]).drop_duplicates(["network", "vehicle_type_id"])
    v = v.merge(T, on=["network", "vehicle_type_id"], how="left")
    v["motorisation"] = v["motorisation"].fillna("inconnue")
    v["is_reserved"] = v["is_reserved"].astype(str).str.lower().eq("true")
    par_voiture = (v.groupby(["network", "vehicle_id"])
                   .agg(station_id=("station_id", "last"), motorisation=("motorisation", "last"),
                        reservee=("is_reserved", "mean")).reset_index())
    return par_voiture, releves


@st.cache_data(show_spinner="Calcul des zones de chalandise et des bornes à proximité…")
def geometrie(df, stations, bornes, rayon):
    """Pour chaque carreau : distance à la station Citiz la plus proche, habitants dans la zone de chalandise,
    bornes publiques à 300 m. Pour chaque station existante : sa part des habitants (partagée avec les stations voisines)."""
    xy = df[["x", "y"]].to_numpy()
    hab = df["habitants"].to_numpy(float)
    arbre = cKDTree(xy)
    out = pd.DataFrame(index=df.index)
    a_bornes = bornes is not None and len(bornes) > 0
    if a_bornes:
        arbre_b = cKDTree(metres(bornes["lon"], bornes["lat"]))
        kw = bornes["kw"].to_numpy(float)

    def autour_bornes(xy_de):
        B = voisins(xy_de, arbre_b, RAYON_BORNE)
        return np.asarray(B.sum(axis=1)).ravel(), B.multiply(kw).max(axis=1).toarray().ravel()

    st_geo = None
    if stations is not None and len(stations):
        xy_st = metres(stations["lon"], stations["lat"])
        out["dist_station_m"] = cKDTree(xy_st).query(xy)[0]
        M = voisins(xy_st, arbre, rayon)                                   # stations × carreaux
        n_st = np.asarray(M.sum(axis=0)).ravel()                          # stations qui se partagent chaque carreau
        part = np.where(n_st > 0, hab / np.maximum(n_st, 1), 0.0)
        st_geo = pd.DataFrame({"pop_part": M @ part, "pop_zone": M @ hab})
        if a_bornes:
            st_geo["bornes_300m"], st_geo["kw_max_300m"] = autour_bornes(xy_st)
        else:
            st_geo["bornes_300m"], st_geo["kw_max_300m"] = np.nan, np.nan
        k = arbre.query(xy_st)[1]                                          # territoire = celui du carreau le plus proche
        for c in ["territoire", "position", "taille_aire", "aire", "commune"]:
            st_geo[c] = df[c].to_numpy()[k]
    else:
        out["dist_station_m"] = np.inf
    couvert = out["dist_station_m"].to_numpy() <= rayon
    V = voisins(xy, arbre, rayon)
    out["pop_zone"] = V @ hab
    out["pop_zone_libre"] = V @ (hab * ~couvert)                          # habitants pas encore à portée d'une station
    out["couvert"] = couvert
    if a_bornes:
        out["bornes_300m"], out["kw_max_300m"] = autour_bornes(xy)
    else:
        out["bornes_300m"], out["kw_max_300m"] = np.nan, np.nan
    return out, st_geo


# ---------- Modèle : usage, électrique, recettes et coûts (hypothèses) ----------
def modele(t: pd.DataFrame, H: dict, voitures=None) -> pd.DataFrame:
    """t doit contenir : clients_pot (habitants à portée), position, bornes_300m, kw_max_300m."""
    t = t.copy()
    taux = t["position"].map({"pôle": H["adh_pole"], "pôle secondaire": H["adh_couronne"], "couronne": H["adh_couronne"],
                              "hors aire": H["adh_hors"]}).fillna(H["adh_couronne"]) / 100
    t["abonnes"] = t["clients_pot"] * taux
    t["locations_mois"] = t["abonnes"] * H["loc_par_abonne"]
    if voitures is None:
        t["voitures"] = np.clip(np.ceil(t["locations_mois"] / H["loc_max_voiture"]), 1, H["voitures_max"])
    else:
        t["voitures"] = np.maximum(voitures, 1)
    t["loc_jour_voiture"] = t["locations_mois"] / 30 / t["voitures"]
    t["taux_usage"] = (t["loc_jour_voiture"] * H["duree_h"] / 24).clip(0, 1)
    # Électrique : recharge nécessaire comparée au temps libre entre deux locations
    a_borne = t["bornes_300m"].fillna(0).to_numpy() > 0
    kw = np.where(a_borne, t["kw_max_300m"].fillna(0).to_numpy(), H["kw_borne_dediee"])
    kw = np.maximum(np.minimum(kw, H["kw_voiture"]), 0.1)
    t["recharge_h_jour"] = t["loc_jour_voiture"] * H["km_loc"] * H["conso_kwh"] / 100 / kw
    t["libre_h_jour"] = (24 - t["loc_jour_voiture"] * H["duree_h"]).clip(lower=0)
    recharge_ok = (t["recharge_h_jour"] <= H["part_libre"] / 100 * t["libre_h_jour"]).to_numpy() & (H["km_loc"] <= 0.8 * H["autonomie_km"])
    t["electrique"] = np.where(recharge_ok & a_borne, EV_OK, np.where(recharge_ok & H["borne_dediee"], EV_BORNE, EV_NON))
    # Recettes
    panier = H["duree_h"] * H["prix_h"] + H["km_loc"] * H["prix_km"]
    t["recettes"] = t["locations_mois"] * 12 * panier + t["abonnes"] * 12 * H["abo_mois"]
    # Coûts, en thermique puis en électrique
    km_an = t["locations_mois"] * 12 * H["km_loc"]
    commun = H["assurance"] + H["stationnement"] + H["telematique"]
    fixe = H["ouverture"] / H["amort_ouverture"] + t["recettes"] * H["structure"] / 100
    cout_th = t["voitures"] * (H["voiture_th"] + H["entretien_th"] + commun) + km_an * H["conso_l"] / 100 * H["prix_l"] + fixe
    borne = np.where(t["electrique"] == EV_BORNE, max(H["cout_borne"] - H["aide_borne"], 0) / H["amort_borne"], 0.0)
    cout_ev = t["voitures"] * (H["voiture_ev"] + H["entretien_ev"] + commun) + km_an * H["conso_kwh"] / 100 * H["prix_kwh"] + fixe + borne
    possible = (t["electrique"] != EV_NON).to_numpy()
    t["ecart_ev"] = np.where(possible, cout_ev - cout_th, np.nan)          # surcoût annuel du passage à l'électrique
    t["ev_retenu"] = possible & (H["priorite_ev"] | (cout_ev <= cout_th))
    t["motorisation"] = np.where(t["ev_retenu"], "Électrique", "Thermique ou hybride")
    t["couts"] = np.where(t["ev_retenu"], cout_ev, cout_th)
    t["solde"] = t["recettes"] - t["couts"]
    return t


def caler(reseau, H, voitures):
    """Multiplie les trois parts d'abonnés par un même facteur pour que le réseau actuel soit à l'équilibre."""
    def solde(f):
        Hf = {**H, "adh_pole": H["adh_pole"] * f, "adh_couronne": H["adh_couronne"] * f, "adh_hors": H["adh_hors"] * f}
        return modele(reseau, Hf, voitures=voitures)["solde"].sum(), Hf
    bas, haut = 0.01, 50.0
    if solde(haut)[0] < 0 or solde(bas)[0] > 0:
        return H
    for _ in range(40):
        milieu = (bas + haut) / 2
        bas, haut = (milieu, haut) if solde(milieu)[0] < 0 else (bas, milieu)
    return solde(haut)[1]


def seuil_rentabilite(H, position):
    """Habitants à portée nécessaires pour qu'une station à une voiture thermique équilibre ses comptes."""
    test = pd.DataFrame({"clients_pot": np.arange(0, 50_001, 50.0), "position": position, "bornes_300m": 0, "kw_max_300m": 0.0})
    r = modele(test, {**H, "borne_dediee": False, "voitures_max": 1})
    ok = r[r["solde"] >= 0]
    return int(ok["clients_pot"].iloc[0]) if len(ok) else None


def emplacements(sub, geo, seuil, hmin, n, rayon, deja=None):
    """Choix glouton de n emplacements : chacun gagne le plus d'habitants encore sans station Citiz à portée.
    `deja` : emplacements déjà retenus (lon, lat), dont les habitants comptent comme desservis."""
    g = geo.loc[sub.index]
    a_couvrir = ~g["couvert"].to_numpy()
    if deja is not None and len(deja):
        a_couvrir &= cKDTree(metres(deja["lon"], deja["lat"])).query(sub[["x", "y"]].to_numpy())[0] > rayon
    cand_mask = a_couvrir & (sub["nb_arrets_5min"].to_numpy() >= seuil) & (sub["habitants"].to_numpy() >= hmin)
    cand = sub[cand_mask].assign(pop_zone_libre=g.loc[cand_mask, "pop_zone_libre"].to_numpy())
    cand = cand.nlargest(4000, "pop_zone_libre")
    if cand.empty:
        return pd.DataFrame()
    cible = sub[a_couvrir]
    M = voisins(cand[["x", "y"]].to_numpy(), cKDTree(cible[["x", "y"]].to_numpy()), rayon)
    hab = cible["habitants"].to_numpy(float)
    restant = np.ones(len(cible), dtype=bool)
    lignes = []
    for rang in range(1, n + 1):
        gains = M @ (hab * restant)
        k = int(gains.argmax())
        if gains[k] <= 0:
            break
        c = cand.iloc[k]
        lignes.append({"rang": rang, "idx": cand.index[k], "commune": c["commune"], "aire": c["aire"], "territoire": c["territoire"],
                       "position": c["position"], "taille_aire": c["taille_aire"], "habitants_gagnes": float(gains[k]),
                       "arrets_a_5min": int(c["nb_arrets_5min"]), "lon": c["lon"], "lat": c["lat"]})
        restant[M[k].indices] = False
    return pd.DataFrame(lignes)


@st.cache_data
def communes_candidates(chemin: str):
    """Les 213 communes du bilan EDA : hors pôle, sans station, ≥ 2 000 hab., ≥ 30 % des actifs vers le pôle, au moins un arrêt."""
    c = pd.read_csv(chemin, dtype={"code": str}, low_memory=False)
    c["desservie"] = c["desservie"].astype(str).str.lower().eq("true")
    zone = c[c["position"].isin(["couronne", "pôle secondaire", "hors aire"])]
    p = zone[(~zone["desservie"]) & (zone["population"] >= 2_000) & (zone["part_vers_pole_aire"] >= 0.3) & (zone["nb_arrets"] > 0)]
    aires_citiz = set(c.loc[c["desservie"], "LIBAAV2020"].dropna())
    p = p.assign(aire_avec_citiz=p["LIBAAV2020"].isin(aires_citiz))
    return p[["nom", "code", "LIBAAV2020", "territoire", "population", "part_vers_pole_aire",
              "nb_arrets", "nb_points_charge", "aire_avec_citiz"]].reset_index(drop=True)


def meilleur_site(cands, df, geo):
    """Pour chaque commune candidate, le carreau près d'un arrêt qui touche le plus d'habitants sans station Citiz à portée."""
    t = df[["commune", "aire", "nb_arrets_5min"]].assign(pop_zone_libre=geo["pop_zone_libre"])
    t = t[t["nb_arrets_5min"] > 0]
    best = t.sort_values("pop_zone_libre", ascending=False).drop_duplicates(["commune", "aire"])
    best = best.reset_index().rename(columns={"index": "idx", "aire": "LIBAAV2020", "commune": "nom"})
    return cands.merge(best[["idx", "nom", "LIBAAV2020", "pop_zone_libre"]], on=["nom", "LIBAAV2020"], how="left")


def vue_initiale(df: pd.DataFrame) -> pdk.ViewState:
    if df.empty:
        return pdk.ViewState(latitude=45.5, longitude=4.8, zoom=6.3)
    etendue = max(df["lat"].max() - df["lat"].min(), (df["lon"].max() - df["lon"].min()) * 0.7, 0.02)
    zoom = float(np.clip(np.log2(633 / etendue) - 0.4, 5, 14))
    return pdk.ViewState(latitude=float(df["lat"].median()), longitude=float(df["lon"].median()), zoom=zoom)


def carte(couches, df, tooltip=None):
    tooltip = tooltip or {"html": "<b>{commune}</b><br/>{habitants} habitants<br/>Arrêts à 5 min : {nb_arrets_5min}<br/>"
                                  "Stations Citiz à 5 min : {nb_stations_citiz_5min}<br/>"
                                  "Options pour 1 000 hab. : {options_pour_1000_hab}<br/>Niveau : {niveau_nb}"}
    st.pydeck_chart(pdk.Deck(layers=couches, initial_view_state=vue_initiale(df), map_provider="carto", map_style="light",
                             tooltip=tooltip), use_container_width=True)


def barres(df, col, titre, couleur, fmt="%"):
    return (alt.Chart(df).mark_bar(color=couleur)
            .encode(x=alt.X(f"{col}:Q", title=titre, axis=alt.Axis(format=fmt)),
                    y=alt.Y("territoire:N", sort="-x", title=None),
                    tooltip=["territoire", alt.Tooltip(f"{col}:Q", format=".1%" if fmt == "%" else ",.0f")])
            .properties(height=230))


def pct(titre):
    return st.column_config.ProgressColumn(titre, format="percent", min_value=0, max_value=1)


def nombre(titre):
    return st.column_config.NumberColumn(titre, format="%d")


COLS_ECO = {"locations_mois": st.column_config.NumberColumn("Locations/mois", format="%.0f"),
            "electrique": "Électrique possible ?", "motorisation": "Motorisation retenue",
            "ecart_ev": nombre("Électrique moins thermique (€/an)"), "choix": "Pourquoi ce site", "bornes_300m": "Bornes à 300 m",
            "recettes": nombre("Recettes (€/an)"), "couts": nombre("Coûts (€/an)"), "solde": nombre("Solde (€/an)")}


# ---------- Données ----------
st.title("🚗 Citiz · Où ouvrir les prochaines stations ?")
st.caption("Auvergne-Rhône-Alpes · carreaux de 200 m (Filosofi 2021), arrêts GTFS, bornes IRVE, stations et flotte Citiz (GBFS)")

try:
    if F200.exists():
        df = charger(str(F200))
    else:
        st.warning(f"Fichier introuvable : {F200}. Charge `carte_acces_200m.csv` (cellule 15 du notebook).")
        envoi = st.file_uploader("carte_acces_200m.csv", type="csv")
        if envoi is None:
            st.stop()
        df = preparer(pd.read_csv(envoi))
except ValueError as e:
    st.error(str(e))
    st.stop()
stations = charger_stations()
bornes = charger_bornes()
flotte, nb_releves = charger_flotte()

# ---------- Barre latérale : zone, critères, hypothèses ----------
sb = st.sidebar
sb.header("Zone d'analyse")
aires = df.groupby("aire")["habitants"].sum().sort_values(ascending=False).index.tolist()
choix_aire = sb.multiselect("Aire d'attraction", aires, placeholder="Toute la région",
                            help="Vide = toute la région. Choisis par exemple Lyon ou Grenoble pour zoomer.")
d1 = df[df["aire"].isin(choix_aire)] if choix_aire else df
territoires = sorted(d1["territoire"].unique())
choix_terr = sb.multiselect("Type de territoire", territoires, placeholder="Tous")
d2 = d1[d1["territoire"].isin(choix_terr)] if choix_terr else d1
choix_com = sb.multiselect("Commune", sorted(d2["commune"].unique()), placeholder="Toutes")
sub = d2[d2["commune"].isin(choix_com)].copy() if choix_com else d2.copy()

sb.header("Critères d'implantation")
rayon = sb.select_slider("Zone de chalandise d'une station (m, à vol d'oiseau)", [300, 400, 500, 600, 800], 500,
                         help="500 m comme dans l'EDA (habitants à 500 m des stations existantes), soit 6 à 8 min à pied.")
seuil = sb.slider("Au moins … arrêts de transport à 5 min", 0, 10, 1,
                  help="Une station Citiz complète les transports en commun : on cible les zones déjà connectées.")
hmin = sb.slider("Habitants minimum dans le carreau de 200 m", 11, 300, 30)
n_st = sb.slider("Nombre de stations à proposer", 5, 60, 20)
quota = sb.slider("Dont stations réservées aux villes moyennes, petites et hors aire", 0, 30, 6,
                  help="Choisies d'abord hors des grandes aires (400 000 habitants et plus), pour desservir aussi les villes moyennes et petites. "
                       "Leur déficit éventuel est financé par les stations rentables (péréquation).")
seul_citiz = sb.checkbox("Rester dans les aires où Citiz a déjà des stations", True,
                         help="Clermont-Ferrand, Vichy ou Montluçon n'ont aucune station : y ouvrir, c'est lancer un nouveau réseau.")

sb.header("Hypothèses (à remplacer par les chiffres Citiz)")
sb.caption("Aucune donnée de réservation ni de coût n'est publique : ces valeurs sont des ordres de grandeur à valider.")
H = {}
with sb.expander("Demande", expanded=True):
    calage = st.toggle("Caler la demande sur le réseau actuel", True,
                       help="La part d'abonnés est ajustée pour que les 622 stations actuelles soient, ensemble, à l'équilibre "
                            "avec les tarifs et coûts saisis. Les trois parts ci-dessous ne fixent alors que les écarts entre territoires.")
    H["adh_pole"] = st.number_input("Habitants abonnés, pôle des villes (%)", 0.0, 10.0, 1.0, 0.1,
                                    help="Part des habitants à portée qui s'abonnent. Plus forte en ville : 16 % de ménages sans voiture contre 4 % en couronne (recensement 2023).")
    H["adh_couronne"] = st.number_input("Habitants abonnés, couronne (%)", 0.0, 10.0, 0.5, 0.1)
    H["adh_hors"] = st.number_input("Habitants abonnés, hors aire (%)", 0.0, 10.0, 0.4, 0.1)
    zone_calage = st.empty()
    H["loc_par_abonne"] = st.number_input("Locations par abonné et par mois", 0.1, 10.0, 1.0, 0.1)
    H["loc_max_voiture"] = st.number_input("Locations maximum par voiture et par mois", 10, 120, 45, 5,
                                           help="Au-delà, on ajoute une voiture à la station.")
    H["voitures_max"] = st.number_input("Voitures maximum par nouvelle station", 1, 10, 3)
with sb.expander("Tarifs"):
    H["duree_h"] = st.number_input("Durée moyenne d'une location (h)", 0.5, 48.0, 4.0, 0.5)
    H["km_loc"] = st.number_input("Kilomètres par location", 1, 500, 40, 5)
    H["prix_h"] = st.number_input("Prix horaire (€)", 0.0, 20.0, 3.0, 0.1)
    H["prix_km"] = st.number_input("Prix du kilomètre (€)", 0.0, 2.0, 0.35, 0.01)
    H["abo_mois"] = st.number_input("Abonnement moyen (€ par abonné et par mois)", 0.0, 50.0, 5.0, 0.5)
with sb.expander("Coûts annuels"):
    H["voiture_th"] = st.number_input("Voiture thermique ou hybride : location longue durée (€/an)", 0, 20_000, 3_600, 100)
    H["voiture_ev"] = st.number_input("Voiture électrique : location longue durée (€/an)", 0, 20_000, 4_200, 100)
    H["assurance"] = st.number_input("Assurance (€/an par voiture)", 0, 10_000, 1_000, 50)
    H["entretien_th"] = st.number_input("Entretien et nettoyage, thermique (€/an)", 0, 10_000, 1_200, 50)
    H["entretien_ev"] = st.number_input("Entretien et nettoyage, électrique (€/an)", 0, 10_000, 900, 50)
    H["stationnement"] = st.number_input("Place de stationnement (€/an)", 0, 10_000, 300, 50)
    H["telematique"] = st.number_input("Boîtier et télématique (€/an)", 0, 5_000, 300, 50)
    H["conso_l"] = st.number_input("Consommation thermique (l/100 km)", 0.0, 15.0, 6.0, 0.5)
    H["prix_l"] = st.number_input("Prix du carburant (€/l)", 0.0, 4.0, 1.80, 0.05)
    H["ouverture"] = st.number_input("Ouverture d'une station : marquage, signalétique, lancement (€)", 0, 50_000, 3_000, 500)
    H["amort_ouverture"] = st.number_input("Amortissement de l'ouverture (ans)", 1, 15, 5)
    H["structure"] = st.number_input("Frais de structure (% des recettes)", 0, 60, 15)
with sb.expander("Électrique"):
    H["conso_kwh"] = st.number_input("Consommation électrique (kWh/100 km)", 5.0, 40.0, 17.0, 0.5)
    H["prix_kwh"] = st.number_input("Prix de l'électricité sur borne publique (€/kWh)", 0.0, 2.0, 0.35, 0.01)
    H["autonomie_km"] = st.number_input("Autonomie de la voiture (km)", 50, 800, 300, 10,
                                        help="300 km : autonomie médiane annoncée par le relevé GBFS de la flotte Citiz.")
    H["kw_voiture"] = st.number_input("Puissance de charge maximale acceptée par la voiture (kW)", 2.0, 150.0, 22.0, 1.0)
    H["part_libre"] = st.slider("Part du temps libre utilisable pour recharger (%)", 10, 100, 50,
                                help="Une voiture ne peut pas rester branchée tout son temps libre : nuits, borne occupée, trajet jusqu'à la borne.")
    H["priorite_ev"] = st.checkbox("Passer à l'électrique dès que c'est possible, même un peu plus cher", True)
    H["borne_dediee"] = st.checkbox("Autoriser la pose d'une borne dédiée quand aucune borne publique n'est à 300 m", True)
    H["kw_borne_dediee"] = st.number_input("Puissance d'une borne dédiée (kW)", 3.0, 50.0, 7.4, 0.1)
    H["cout_borne"] = st.number_input("Coût d'une borne dédiée posée (€)", 0, 50_000, 6_000, 500)
    H["aide_borne"] = st.number_input("Aide à la borne, type ADVENIR (€)", 0, 20_000, 1_500, 100)
    H["amort_borne"] = st.number_input("Amortissement de la borne (ans)", 1, 15, 8)

if sub.empty:
    st.info("Aucun carreau pour cette sélection.")
    st.stop()

geo, st_geo = geometrie(df, stations, bornes, rayon)

# Réseau existant, passé au même modèle
reseau = None
if st_geo is not None:
    garder = [c for c in ["nom", "lon", "lat", "station_id", "network", "capacity"] if c in stations]
    reseau = pd.concat([stations[garder].reset_index(drop=True), st_geo.reset_index(drop=True)], axis=1)
    vo = None
    if flotte is not None and "station_id" in reseau:
        par_st = (flotte.groupby(["network", "station_id"])
                  .agg(voitures_vues=("vehicle_id", "size"), electriques=("motorisation", lambda m: (m == "electric").sum()),
                       taux_reserve=("reservee", "mean")).reset_index())
        reseau = reseau.merge(par_st, on=["network", "station_id"], how="left")
        vo = reseau["voitures_vues"].fillna(reseau["capacity"] if "capacity" in reseau else 1).clip(lower=1).to_numpy()
    reseau = reseau.assign(clients_pot=reseau["pop_part"])
    if calage:
        H = caler(reseau, H, vo)
        zone_calage.caption(f"Parts calées : pôle {H['adh_pole']:.2f} %, couronne {H['adh_couronne']:.2f} %, hors aire {H['adh_hors']:.2f} %.")
    reseau = modele(reseau, H, voitures=vo)

# ---------- Stations proposées ----------
zone_sites = sub
if seul_citiz and stations is not None:
    zone_sites = sub[sub["aire"].isin(set(st_geo["aire"]))]
quota = min(quota, n_st)
petits = emplacements(zone_sites[zone_sites["taille_aire"] != "1"], geo, seuil, hmin, quota, rayon) if quota else pd.DataFrame()
autres = emplacements(zone_sites, geo, seuil, hmin, n_st - len(petits), rayon, deja=petits if len(petits) else None)
sites = pd.concat([petits.assign(choix="Quota villes moyennes et petites"), autres.assign(choix="Plus d'habitants gagnés")], ignore_index=True)
if not sites.empty:
    sites["rang"] = np.arange(1, len(sites) + 1)
    sites = sites.join(geo[["bornes_300m", "kw_max_300m"]], on="idx")
    sites = modele(sites.assign(clients_pot=sites["habitants_gagnes"]), H)

# ---------- Indicateurs clés ----------
hab = sub["habitants"].sum()
if not sites.empty:
    k = st.columns(5)
    k[0].metric("Habitants nouvellement desservis", esp(sites["habitants_gagnes"].sum()),
                help=f"Habitants à moins de {rayon} m d'une station proposée, qui n'avaient aucune station Citiz à cette distance.")
    k[1].metric("Locations par mois et par station", esp(sites["locations_mois"].median()),
                help="Médiane des stations proposées, d'après les hypothèses de demande.")
    k[2].metric("Part électrifiable", f"{(sites['electrique'] != EV_NON).mean():.0%}",
                delta=f"{(sites['electrique'] == EV_OK).mean():.0%} avec borne publique", delta_color="off",
                help="Le temps de recharge tient dans le temps libre entre deux locations, avec une borne publique à 300 m ou une borne dédiée.")
    k[3].metric("Solde annuel des stations proposées", euros(sites["solde"].sum()),
                help="Recettes moins coûts sur un an, toutes stations proposées confondues (hypothèses de la barre latérale).")
    k[4].metric("Stations rentables seules", f"{int((sites['solde'] >= 0).sum())} sur {len(sites)}",
                help="Les autres ont besoin d'être financées par le reste du réseau (péréquation).")
else:
    st.info("Aucun emplacement avec ces critères : baisse le nombre d'arrêts minimum ou le nombre d'habitants par carreau.")

onglets = st.tabs(["Synthèse", "Où ouvrir ?", "Électrique ?", "213 communes candidates", "Réseau actuel", "Carte d'accès", "Hypothèses et méthode"])

# ---------- Synthèse ----------
with onglets[0]:
    if not sites.empty:
        c1, c2 = st.columns([3, 2])
        with c1:
            st.subheader("Les stations proposées, de la plus rentable à la plus aidée")
            vue = sites.sort_values("solde", ascending=False).assign(nom=lambda t: t["rang"].astype(str) + ". " + t["commune"])
            st.altair_chart(alt.Chart(vue).mark_bar().encode(
                x=alt.X("solde:Q", title="Solde annuel estimé (€)"),
                y=alt.Y("nom:N", sort=vue["nom"].tolist(), title=None),
                color=alt.condition("datum.solde >= 0", alt.value(BLEU), alt.value(ROUGE)),
                tooltip=["commune", "aire", alt.Tooltip("habitants_gagnes:Q", format=",.0f", title="habitants gagnés"),
                         alt.Tooltip("locations_mois:Q", format=".0f", title="locations/mois"),
                         alt.Tooltip("recettes:Q", format=",.0f"), alt.Tooltip("couts:Q", format=",.0f", title="coûts"),
                         "electrique"]).properties(height=max(260, 18 * len(vue))), use_container_width=True)
        with c2:
            st.subheader("À retenir")
            s_pole, s_cour = seuil_rentabilite(H, "pôle"), seuil_rentabilite(H, "couronne")
            gagnes = sites["habitants_gagnes"].sum()
            hors_grandes = int((sites["taille_aire"] != "1").sum())
            n_aid = int((sites["solde"] < 0).sum())
            aidees = (f"{n_aid} station(s) sur {len(sites)} doivent être aidées par le reste du réseau "
                      f"({euros(sites.loc[sites['solde'] < 0, 'solde'].sum())} par an)") if n_aid else "chaque station est rentable seule"
            st.markdown(f"""
- **{len(sites)} stations** touchent **{esp(gagnes)} habitants** sans station Citiz à {rayon} m ({gagnes / hab:.1%} de la zone).
- **{(sites['electrique'] != EV_NON).mean():.0%}** peuvent être électriques ; **{(sites['electrique'] == EV_OK).mean():.0%}** ont déjà une borne publique à 300 m.
- Solde du portefeuille : **{euros(sites['solde'].sum())} par an** ; {aidees}.
- Passer en électrique les stations où c'est possible {'coûte' if sites['ecart_ev'].sum() > 0 else 'fait économiser'} **{esp(abs(sites['ecart_ev'].sum()))} € par an** par rapport au thermique (bornes dédiées comprises).
- **{hors_grandes}** stations proposées sont dans des villes moyennes, petites ou hors aire.
- Seuil de rentabilité d'une station à une voiture thermique : **{esp(s_pole) if s_pole else 'jamais atteint'} habitants** à portée en pôle urbain, **{esp(s_cour) if s_cour else 'jamais atteint'}** en couronne.
""")
            st.caption("Chiffres issus des hypothèses de la barre latérale, pas de données de réservation Citiz.")
        st.subheader("Par type de territoire")
        par_t = sites.groupby("territoire").agg(stations=("rang", "size"), habitants=("habitants_gagnes", "sum"),
                                                electrifiables=("electrique", lambda e: (e != EV_NON).mean()),
                                                solde=("solde", "sum")).reset_index()
        st.dataframe(par_t, hide_index=True, use_container_width=True,
                     column_config={"territoire": "Type de territoire", "stations": "Stations", "habitants": nombre("Habitants gagnés"),
                                    "electrifiables": pct("Électrifiables"), "solde": nombre("Solde annuel (€)")})

# ---------- Où ouvrir ----------
with onglets[1]:
    st.subheader("Emplacements proposés")
    st.write(f"Choix glouton : chaque station ajoutée touche le plus d'habitants encore sans station Citiz à {rayon} m, parmi les "
             f"carreaux d'au moins **{hmin} habitants** avec au moins **{seuil} arrêt(s)** à 5 min. Les stations Citiz existantes sont en bleu.")
    if not sites.empty:
        couleur = {EV_OK: [46, 125, 50], EV_BORNE: [129, 199, 132], EV_NON: [192, 57, 43]}
        aff = sites.assign(c=sites["electrique"].map(couleur), solde_txt=sites["solde"].map(euros),
                           hab_txt=sites["habitants_gagnes"].map(esp), loc_txt=sites["locations_mois"].round(0))
        couches = []
        if stations is not None:
            couches.append(pdk.Layer("ScatterplotLayer", data=stations[["lon", "lat", "nom"]].assign(commune=stations["nom"], rang="Station"),
                                     get_position="[lon, lat]", get_fill_color=[31, 90, 140, 200], get_radius=110,
                                     radius_min_pixels=3, pickable=True))
        couches.append(pdk.Layer("ScatterplotLayer", data=aff[["lon", "lat", "rang", "commune", "c", "solde_txt", "hab_txt", "loc_txt", "electrique"]],
                                 get_position="[lon, lat]", get_fill_color="c", get_line_color=[255, 255, 255], stroked=True,
                                 line_width_min_pixels=2, get_radius=rayon, radius_min_pixels=7, opacity=0.6, pickable=True))
        carte(couches, aff, {"html": "<b>{rang}. {commune}</b><br/>Habitants gagnés : {hab_txt}<br/>Locations/mois : {loc_txt}<br/>"
                                     "Solde annuel : {solde_txt}<br/>{electrique}"})
        st.caption("Vert foncé : électrique avec borne publique · vert clair : électrique avec borne à installer · rouge : thermique ou hybride. "
                   "Le disque a le rayon de la zone de chalandise.")
        tableau = sites[["rang", "choix", "commune", "aire", "territoire", "habitants_gagnes", "arrets_a_5min", "locations_mois", "voitures",
                         "taux_usage", "electrique", "motorisation", "ecart_ev", "bornes_300m", "recettes", "couts", "solde", "lon", "lat"]]
        st.dataframe(tableau, hide_index=True, use_container_width=True,
                     column_config={"rang": "N°", "commune": "Commune", "aire": "Aire", "territoire": "Territoire",
                                    "habitants_gagnes": nombre("Habitants gagnés"), "arrets_a_5min": "Arrêts à 5 min",
                                    "voitures": nombre("Voitures"), "taux_usage": pct("Taux d'usage"), **COLS_ECO})
        st.download_button("Télécharger les emplacements (CSV)", tableau.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "emplacements_proposes.csv", "text/csv")

# ---------- Électrique ----------
with onglets[2]:
    st.subheader("La voiture peut-elle être électrique ?")
    st.markdown(f"""
Règle appliquée à chaque station : elle peut être électrique si **le temps de recharge tient dans le temps libre entre deux locations**,
avec une borne à proximité.
- Recharge par jour = locations par jour × {H['km_loc']} km × {H['conso_kwh']} kWh/100 km ÷ puissance de la borne (plafonnée à {H['kw_voiture']:.0f} kW, ce que la voiture accepte).
- Temps libre par jour = 24 h − locations par jour × {H['duree_h']} h ; on n'en utilise que {H['part_libre']} %.
- Borne : une borne publique en accès libre à moins de {RAYON_BORNE} m, sinon une borne dédiée de {H['kw_borne_dediee']} kW ({'coût ajouté' if H['borne_dediee'] else 'option désactivée'}).
""")
    if not sites.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Stations proposées**")
            rep = sites["electrique"].value_counts().rename_axis("electrique").reset_index(name="stations")
            st.altair_chart(alt.Chart(rep).mark_arc(innerRadius=60).encode(
                theta="stations:Q", color=alt.Color("electrique:N", scale=COULEUR_EV, title=None),
                tooltip=["electrique", "stations"]).properties(height=260), use_container_width=True)
        with c2:
            st.markdown("**Recharge nécessaire contre temps disponible**")
            st.altair_chart(alt.Chart(sites.assign(dispo=sites["libre_h_jour"] * H["part_libre"] / 100)).mark_circle(size=90).encode(
                x=alt.X("dispo:Q", title="Temps utilisable pour recharger (h/jour)"),
                y=alt.Y("recharge_h_jour:Q", title="Recharge nécessaire (h/jour)"),
                color=alt.Color("electrique:N", scale=COULEUR_EV, title=None),
                tooltip=["commune", alt.Tooltip("recharge_h_jour:Q", format=".1f"), alt.Tooltip("dispo:Q", format=".1f"),
                         "bornes_300m"]).properties(height=260), use_container_width=True)
    if reseau is not None:
        st.markdown("**Le réseau actuel**")
        m = st.columns(3)
        if flotte is not None:
            m[0].metric("Voitures électriques aujourd'hui", f"{(flotte['motorisation'] == 'electric').mean():.0%}",
                        help=f"{int((flotte['motorisation'] == 'electric').sum())} sur {len(flotte)} voitures vues dans le relevé GBFS ; "
                             f"{(flotte['motorisation'] == 'hybrid').mean():.0%} d'hybrides.")
        m[1].metric("Stations actuelles avec une borne publique à 300 m", f"{(reseau['bornes_300m'].fillna(0) > 0).mean():.0%}")
        m[2].metric("Stations actuelles électrifiables (règle ci-dessus)", f"{(reseau['electrique'] != EV_NON).mean():.0%}")
        par_t = reseau.groupby("territoire").agg(stations=("nom", "size"), borne=("bornes_300m", lambda b: (b.fillna(0) > 0).mean()),
                                                 electrifiable=("electrique", lambda e: (e != EV_NON).mean())).reset_index()
        if "electriques" in reseau:
            ev_t = reseau.groupby("territoire")[["electriques", "voitures_vues"]].sum()
            par_t = par_t.merge((ev_t["electriques"] / ev_t["voitures_vues"].clip(lower=1)).rename("part_ev_flotte").reset_index(), on="territoire")
        st.dataframe(par_t, hide_index=True, use_container_width=True,
                     column_config={"territoire": "Type de territoire", "stations": "Stations", "borne": pct("Borne publique à 300 m"),
                                    "electrifiable": pct("Électrifiables"), "part_ev_flotte": pct("Voitures déjà électriques")})
    if bornes is None:
        st.warning("Bornes introuvables : place `irve_consolide.csv` (cellule 8) ou `bornes_ara.csv` dans eda-citiz/donnees.")

# ---------- 213 communes candidates ----------
with onglets[3]:
    st.subheader("Les communes au profil des communes desservies, sans station")
    if not FCOM.exists():
        st.warning(f"Fichier introuvable : {FCOM} (cellule 10 du notebook, version CSV).")
    else:
        cc = meilleur_site(communes_candidates(str(FCOM)), df, geo)
        trouve = cc["idx"].notna()
        cs = cc[trouve].copy()
        cs["idx"] = cs["idx"].astype(int)
        cs = cs.join(df[["lon", "lat", "position", "taille_aire"]], on="idx").join(geo[["bornes_300m", "kw_max_300m"]], on="idx")
        cs = modele(cs.assign(clients_pot=cs["pop_zone_libre"]), H)
        cs["priorite"] = np.where(cs["aire_avec_citiz"], "1 · aire où Citiz est présent", "2 · nouvelle aire")
        st.write(f"Critères du bilan EDA : commune hors pôle, sans station, au moins 2 000 habitants, au moins 30 % des actifs "
                 f"qui travaillent au pôle, au moins un arrêt. **{len(cc)} communes**, dont **{int(cc['aire_avec_citiz'].sum())}** "
                 f"dans une aire où Citiz est déjà présent. Pour chacune, le meilleur emplacement est le carreau près d'un arrêt "
                 f"qui touche le plus d'habitants à {rayon} m.")
        if (~trouve).any():
            st.caption(f"{int((~trouve).sum())} communes sans carreau habité près d'un arrêt dans la carte de 200 m : "
                       f"{', '.join(cc.loc[~trouve, 'nom'].head(8))}{'…' if (~trouve).sum() > 8 else ''}")
        m = st.columns(4)
        m[0].metric("Communes rentables seules", f"{int((cs['solde'] >= 0).sum())} sur {len(cs)}")
        m[1].metric("Habitants touchés (1 station par commune)", esp(cs["clients_pot"].sum()))
        m[2].metric("Électrifiables", f"{(cs['electrique'] != EV_NON).mean():.0%}")
        m[3].metric("Solde si on les ouvre toutes", euros(cs["solde"].sum()))
        filtre = st.radio("Afficher", ["Toutes", "Aires où Citiz est présent", "Nouvelles aires"], horizontal=True)
        vue = cs if filtre == "Toutes" else cs[cs["aire_avec_citiz"] == (filtre == "Aires où Citiz est présent")]
        vue = vue.sort_values(["priorite", "solde"], ascending=[True, False])
        st.dataframe(vue[["priorite", "nom", "LIBAAV2020", "territoire", "population", "part_vers_pole_aire", "clients_pot",
                          "locations_mois", "electrique", "motorisation", "ecart_ev", "bornes_300m", "recettes", "couts", "solde"]],
                     hide_index=True, use_container_width=True, height=520,
                     column_config={"priorite": "Priorité", "nom": "Commune", "LIBAAV2020": "Aire", "territoire": "Territoire",
                                    "population": nombre("Habitants"), "part_vers_pole_aire": pct("Actifs vers le pôle"),
                                    "clients_pot": nombre(f"Habitants à {rayon} m du site"), **COLS_ECO})
        st.download_button("Télécharger les communes candidates (CSV)",
                           vue.drop(columns=["idx"]).to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "communes_candidates.csv", "text/csv")
        st.caption("Clermont-Ferrand, Vichy et Montluçon n'ont aucune station Citiz : y ouvrir une station, c'est lancer un nouveau réseau, pas combler un trou.")

# ---------- Réseau actuel ----------
with onglets[4]:
    st.subheader("Le réseau actuel passé au même modèle")
    if reseau is None:
        st.warning("Stations introuvables : place `stations_croisees.csv` (cellule 10) dans eda-citiz/donnees.")
    else:
        st.write("Chaque station existante reçoit sa part des habitants à portée (partagée avec les stations voisines), puis passe "
                 "dans le même modèle que les stations proposées, avec le nombre de voitures vu dans le relevé. C'est le contrôle "
                 "de bon sens des hypothèses : si presque tout le réseau actuel paraît déficitaire, les hypothèses sont trop sévères.")
        m = st.columns(4)
        m[0].metric("Stations", len(reseau))
        m[1].metric("Stations rentables selon le modèle", f"{(reseau['solde'] >= 0).mean():.0%}")
        m[2].metric("Solde annuel modélisé du réseau", euros(reseau["solde"].sum()))
        if "taux_reserve" in reseau and reseau["taux_reserve"].notna().any():
            m[3].metric("Voitures réservées au moment du relevé", f"{reseau['taux_reserve'].mean():.0%}",
                        help=f"Moyenne sur {nb_releves} relevé(s) GBFS. Il faut plusieurs jours de relevé pour en faire un indicateur d'usage.")
        agg = dict(stations=("nom", "size"), habitants=("pop_part", "median"), rentables=("solde", lambda s: (s >= 0).mean()),
                   solde=("solde", "sum"))
        if "taux_reserve" in reseau:
            agg["taux_reserve"] = ("taux_reserve", "mean")
        par_t = reseau.groupby("territoire").agg(**agg).reset_index()
        st.dataframe(par_t, hide_index=True, use_container_width=True,
                     column_config={"territoire": "Type de territoire", "stations": "Stations",
                                    "habitants": nombre(f"Habitants par station (médiane, part à {rayon} m)"),
                                    "rentables": pct("Rentables"), "solde": nombre("Solde annuel (€)"),
                                    "taux_reserve": pct("Voitures réservées (relevé)")})
        if nb_releves:
            st.caption(f"Relevé GBFS : {nb_releves} instantané(s). Relance le collecteur sur au moins une semaine pour mesurer l'usage réel : "
                       "le tableau de bord lit automatiquement tous les fichiers vehicle_status_*.csv.")

# ---------- Carte d'accès ----------
with onglets[5]:
    st.subheader("Carte d'accès : du froid au chaud")
    k1, k2, k3 = st.columns(3)
    k1.metric("Habitants analysés", esp(hab))
    k2.metric("Sans aucune option à 5 min", f"{sub['h_sans'].sum() / hab:.0%}", help="Ni arrêt de transport, ni station Citiz à 5 min à pied.")
    k3.metric("Avec une station Citiz à 5 min", f"{sub['h_citiz'].sum() / hab:.0%}")
    mode = st.radio("Indicateur coloré", ["Nombre d'options à 5 min (arrêts + stations Citiz)", "Options pour 1 000 habitants (mesure du sujet)"],
                    horizontal=True)
    nb_mode = mode.startswith("Nombre")
    rang = sub["rang_nb"] if nb_mode else sub["rang_hab"]
    libs = LIBELLES_NB if nb_mode else LIBELLES_HAB
    if nb_mode:
        st.caption("Lecture : plus c'est chaud (rouge), plus il y a d'options à 5 min à pied. Les villes denses ressortent.")
    else:
        st.caption("Attention : cette mesure divise les options par le nombre d'habitants qui les partagent. "
                   "Elle avantage les zones peu denses et pénalise les centres-villes.")
    aff = sub.assign(r=COULEURS[rang.to_numpy(), 0], g=COULEURS[rang.to_numpy(), 1], b=COULEURS[rang.to_numpy(), 2])
    if len(aff) > MAX_POINTS:
        st.warning(f"{esp(len(aff))} carreaux : affichage d'un échantillon de {esp(MAX_POINTS)}. Choisis une aire d'attraction pour tout voir.")
        aff = aff.sample(MAX_POINTS, random_state=0)
    montrer = st.checkbox("Afficher les stations Citiz existantes", value=True, disabled=stations is None)
    couches = [pdk.Layer("ScatterplotLayer", data=aff[["lon", "lat", "commune", "habitants", "nb_arrets_5min", "nb_stations_citiz_5min",
                                                       "options_pour_1000_hab", "niveau_nb", "r", "g", "b"]],
                         get_position="[lon, lat]", get_fill_color="[r, g, b, 200]", get_radius=95,
                         radius_min_pixels=1.5, radius_max_pixels=14, pickable=True)]
    if montrer and stations is not None:
        st_aff = stations
        if len(sub) < len(df):
            st_aff = stations[(stations["lon"].between(sub["lon"].min() - 0.05, sub["lon"].max() + 0.05)) &
                              (stations["lat"].between(sub["lat"].min() - 0.05, sub["lat"].max() + 0.05))]
        couches.append(pdk.Layer("ScatterplotLayer", data=st_aff[["lon", "lat", "nom"]].assign(
            commune=st_aff["nom"], habitants="", nb_arrets_5min="", nb_stations_citiz_5min="", options_pour_1000_hab="", niveau_nb="Station Citiz"),
            get_position="[lon, lat]", get_fill_color=[255, 255, 255, 255], get_line_color=[20, 20, 20],
            stroked=True, line_width_min_pixels=2, get_radius=130, radius_min_pixels=5, pickable=True))
    carte(couches, aff)
    st.markdown(" &nbsp; ".join(f"<span style='color:{hexa(COULEURS[i])}'>●</span> {libs[i]}" for i in range(5))
                + (" &nbsp; <span>○</span> station Citiz" if montrer and stations is not None else ""), unsafe_allow_html=True)

    par_terr = sub.groupby("territoire").agg(habitants=("habitants", "sum"), h_citiz=("h_citiz", "sum"),
                                              h_sans=("h_sans", "sum")).reset_index()
    par_terr["part_citiz"] = par_terr["h_citiz"] / par_terr["habitants"]
    par_terr["part_sans"] = par_terr["h_sans"] / par_terr["habitants"]
    c1, c2 = st.columns(2)
    c1.markdown("**Part des habitants avec une station Citiz à 5 min**")
    c1.altair_chart(barres(par_terr, "part_citiz", "Part des habitants", BLEU), use_container_width=True)
    c2.markdown("**Part des habitants sans aucune option à 5 min**")
    c2.altair_chart(barres(par_terr, "part_sans", "Part des habitants", ROUGE), use_container_width=True)

# ---------- Méthode ----------
with onglets[6]:
    st.subheader("Hypothèses et méthode")
    st.markdown(f"""
**1. La voiture sera-t-elle utilisée ?** Pas de données de réservation publiques : l'usage est estimé par
habitants à portée × part d'abonnés × locations par abonné. La part d'abonnés est plus forte dans les pôles urbains
({H['adh_pole']} %) qu'en couronne ({H['adh_couronne']} %) et hors aire ({H['adh_hors']} %), parce que les ménages sans voiture y sont
4 fois plus nombreux (16 % contre 4 %, recensement 2023). Une nouvelle station ne compte que les habitants qui n'avaient pas de
station Citiz à {rayon} m, pour ne pas prendre de clients aux stations voisines.

**2. Peut-elle être électrique ?** Voir l'onglet Électrique : recharge nécessaire comparée au temps libre entre deux locations,
borne publique en accès libre à moins de {RAYON_BORNE} m (fichier consolidé IRVE) ou borne dédiée.

**3. Combien ça coûte ?** Recettes = locations × (durée × prix horaire + km × prix du km) + abonnements.
Coûts = voitures (location longue durée, assurance, entretien, stationnement, télématique) + énergie + ouverture et borne amorties
+ frais de structure. Le solde négatif d'une station se lit comme son besoin de péréquation.

**Emplacements** : choix glouton sur les carreaux de 200 m, chaque station ajoutée touche le plus d'habitants encore sans station.
Ni disponibilité de la voirie, ni autorisation de la commune ne sont prises en compte.

**Limites.**
- Toutes les valeurs de la barre latérale sont des hypothèses à remplacer par les chiffres de Citiz (tarifs, coûts, taux d'abonnés).
- Distances à vol d'oiseau : les habitants à portée sont surestimés, surtout hors des centres.
- Population Filosofi 2021 ; les carreaux de moins de 11 habitants sont exclus (secret statistique).
- Les bornes publiques sont partagées avec d'autres usagers : une borne à 300 m ne garantit pas une recharge à l'heure voulue.
- Le relevé GBFS de la flotte compte {nb_releves} instantané(s) : la part de voitures réservées n'est pas encore un indicateur d'usage.
- Motorisation des ménages non recalculée tant que Parquet est bloqué sur le PC (colonne vide dans communes_croisees.csv).
""")
