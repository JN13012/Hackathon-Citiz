"""Tableau de bord Citiz · focus Grenoble : accès aux transports, qui est concerné, où ouvrir, électrique, rentabilité.

Lancer depuis la racine du projet :  python -m streamlit run eda-citiz/dashboard/app.py
Données lues (CSV et GeoJSON uniquement, aucun Parquet), dans eda-citiz/donnees/ :
  grenoble/gold_o1.geojson, grenoble/gold_o2.geojson   O1 et O2 sur le réseau piéton (livraison géomatique, Grenoble)
  grenoble/couts_station_citiz.csv                     coûts sourcés d'une station Citiz (couts.yaml, euros 2025)
  powerbi/carte_acces_200m.csv                         carreaux de 200 m de la région (cellule 15 du notebook)
  stations_croisees.csv                                stations Citiz (cellule 10)
  bornes_ara.csv                                       bornes de recharge publiques en accès libre (créé depuis irve_consolide.csv)
"""
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

st.set_page_config(page_title="Citiz · Grenoble", page_icon="🚗", layout="wide")

RACINE = Path(os.environ.get("CITIZ_RACINE", Path(__file__).resolve().parent.parent))   # dossier eda-citiz
DONNEES = RACINE / "donnees"
GRE = DONNEES / "grenoble"
F_O1, F_O2, F_COUTS = GRE / "gold_o1.geojson", GRE / "gold_o2.geojson", GRE / "couts_station_citiz.csv"
F200 = DONNEES / "powerbi" / "carte_acces_200m.csv"
FSTX = DONNEES / "stations_croisees.csv"
FBORNES = DONNEES / "bornes_ara.csv"
FIRVE = DONNEES / "irve_consolide.csv"
AIRE = "Grenoble"
BBOX_ARA = (2.06, 44.11, 7.19, 46.81)
RAYON_BORNE = 300
RAYON_5MIN = 308               # 5 min à pied (400 m) ramenés à vol d'oiseau avec un coefficient de détour de 1,3
FROID_CHAUD = [(0.0, (44, 123, 182)), (0.25, (171, 217, 233)), (0.5, (255, 255, 191)), (0.75, (253, 174, 97)), (1.0, (215, 25, 28))]
BLEU, ROUGE, VERT, GRIS = "#1F5A8C", "#C0392B", "#2E7D32", "#8FA3B8"
TYPES = {"bus": ("Bus", (31, 119, 180)), "tram": ("Tram", (214, 39, 40)), "cable_car": ("Téléphérique", (148, 103, 189)),
         "citiz": ("Citiz", (44, 160, 44))}
MODES_O1 = {"Toutes les options": "options_par_habitant", "Bus": "options_bus_par_habitant", "Tram": "options_tram_par_habitant",
            "Citiz": "options_citiz_par_habitant", "Téléphérique": "options_cable_car_par_habitant"}
EV_OK, EV_BORNE, EV_NON = "Électrique : borne publique à 300 m", "Électrique : borne à installer", "Thermique ou hybride"
COULEUR_EV = alt.Scale(domain=[EV_OK, EV_BORNE, EV_NON], range=[VERT, "#81C784", GRIS])


def esp(v, d=0):
    return f"{v:,.{d}f}".replace(",", " ")


def euros(v):
    return f"{v:+,.0f} €".replace(",", " ")


def metres(lon, lat):
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return np.c_[lon * 111_320 * np.cos(np.radians(45.2)), lat * 110_540]


def voisins(xy_de, arbre_vers, rayon):
    m = cKDTree(xy_de).sparse_distance_matrix(arbre_vers, rayon, output_type="coo_matrix")
    return sparse.csr_matrix((np.ones_like(m.data), (m.row, m.col)), shape=m.shape)


def couleur_continue(v, vmax):
    """Du froid (bleu) au chaud (rouge) : dégradé continu."""
    t = np.clip(np.asarray(v, float) / max(vmax, 1e-9), 0, 1)
    xs = [p for p, _ in FROID_CHAUD]
    return np.stack([np.interp(t, xs, [c[i] for _, c in FROID_CHAUD]) for i in range(3)], axis=1).astype(int)


def degrade_css():
    return "linear-gradient(to right," + ",".join(f"rgb{c} {int(p * 100)}%" for p, c in FROID_CHAUD) + ")"


# ---------- Chargement ----------
@st.cache_data(show_spinner="Chargement des carreaux de la région de Grenoble…")
def charger_carreaux():
    df = pd.read_csv(F200)
    df = df[df["aire"] == AIRE].reset_index(drop=True)
    df["commune"] = df["commune"].fillna("?").astype(str)
    df["territoire"] = df["territoire"].fillna("Non classé").astype(str)
    df["position"] = df["territoire"].str.split(" · ").str[-1]
    xy = metres(df["lon"], df["lat"])
    df["x"], df["y"] = xy[:, 0], xy[:, 1]
    return df


@st.cache_data
def charger_stations():
    if not FSTX.exists():
        return None
    s = pd.read_csv(FSTX, dtype={"station_id": str, "code": str}).dropna(subset=["lon", "lat"])
    return s.reset_index(drop=True)


@st.cache_data(show_spinner="Préparation des bornes de recharge (une seule fois)…")
def charger_bornes():
    if FBORNES.exists():
        return pd.read_csv(FBORNES)
    if not FIRVE.exists():
        return None
    cols = ["id_pdc_itinerance", "puissance_nominale", "consolidated_longitude", "consolidated_latitude", "condition_acces"]
    b = pd.read_csv(FIRVE, usecols=cols, low_memory=False)
    x0, y0, x1, y1 = BBOX_ARA
    b = b[b["consolidated_longitude"].between(x0, x1) & b["consolidated_latitude"].between(y0, y1)]
    b = b[~b["condition_acces"].astype(str).str.contains("serv", case=False)].drop_duplicates("id_pdc_itinerance")
    b["kw"] = pd.to_numeric(b["puissance_nominale"], errors="coerce")
    b.loc[b["kw"] > 1000, "kw"] /= 1000
    b = b.rename(columns={"consolidated_longitude": "lon", "consolidated_latitude": "lat"})[["lon", "lat", "kw"]].dropna()
    try:
        b.to_csv(FBORNES, index=False)
    except OSError:
        pass
    return b.reset_index(drop=True)


@st.cache_data
def charger_o1():
    gj = json.loads(F_O1.read_text(encoding="utf-8"))
    props = pd.DataFrame([f["properties"] for f in gj["features"]])
    return gj, props


@st.cache_data
def charger_o2(stations_noms):
    gj = json.loads(F_O2.read_text(encoding="utf-8"))
    rows = []
    for f in gj["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        rows.append({**f["properties"], "lon": lon, "lat": lat})
    o2 = pd.DataFrame(rows)
    noms = dict(stations_noms)
    est_citiz = o2["type"] == "citiz"
    o2["nom_affiche"] = o2["nom"].astype(str)
    o2.loc[est_citiz, "nom_affiche"] = o2.loc[est_citiz, "offre_id"].map(lambda i: noms.get(str(i).split(":")[-1], "Station Citiz"))
    return o2


@st.cache_data
def charger_couts():
    return pd.read_csv(F_COUTS)


@st.cache_data(show_spinner="Calcul des zones de chalandise et des bornes à proximité…")
def geometrie(df, stations, bornes, rayon):
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
        M = voisins(xy_st, arbre, rayon)
        n_st = np.asarray(M.sum(axis=0)).ravel()
        part = np.where(n_st > 0, hab / np.maximum(n_st, 1), 0.0)
        st_geo = pd.DataFrame({"pop_part": M @ part, "pop_zone": M @ hab})
    else:
        out["dist_station_m"] = np.inf
    couvert = out["dist_station_m"].to_numpy() <= rayon
    out["pop_zone_libre"] = voisins(xy, arbre, rayon) @ (hab * ~couvert)
    out["couvert"] = couvert
    if a_bornes:
        out["bornes_300m"], out["kw_max_300m"] = autour_bornes(xy)
    else:
        out["bornes_300m"], out["kw_max_300m"] = np.nan, np.nan
    return out, st_geo


# ---------- Modèle : usage, électrique, recettes, coûts ----------
def cout_annuel(C, scen):
    """Coûts annualisés d'une station Citiz (couts.yaml) : par véhicule, par station, et borne dédiée nette des aides."""
    d = C.assign(annuel=C[scen] / C["duree_vie_ans"].fillna(1))
    base = d[d["option"].isna()]
    return (base.loc[base["par"] == "vehicule", "annuel"].sum(), base.loc[base["par"] == "station", "annuel"].sum(),
            d.loc[d["option"] == "electrique", "annuel"].sum())


def modele(t, P, C, scen, prix=None):
    """t : clients_pot (habitants à portée), bornes_300m, kw_max_300m. Tous les montants en € HT par an."""
    t = t.copy()
    prix = P["prix_ttc"] if prix is None else prix
    t["abonnes"] = t["clients_pot"] * P["adoption"]
    t["demande_resa_an"] = t["abonnes"] * P["resa_client_an"]
    t["voitures"] = np.clip(np.ceil(t["demande_resa_an"] / P["cap_resa_veh"]), 1, P["voitures_max"])
    t["resa_an"] = np.minimum(t["demande_resa_an"], t["voitures"] * P["cap_resa_veh"])      # une station ne peut pas dépasser la capacité de ses voitures
    t["plafonnee"] = t["demande_resa_an"] > t["resa_an"]
    t["resa_veh_jour"] = t["resa_an"] / t["voitures"] / 365
    t["taux_usage"] = (t["resa_veh_jour"] * P["h_resa"] / 24).clip(0, 1)
    # électrique : recharge nécessaire contre temps libre entre deux locations
    a_borne = t["bornes_300m"].fillna(0).to_numpy() > 0
    kw = np.where(a_borne, t["kw_max_300m"].fillna(0).to_numpy(), P["kw_borne_dediee"])
    kw = np.maximum(np.minimum(kw, P["kw_voiture"]), 0.1)
    t["recharge_h_jour"] = t["resa_veh_jour"] * P["km_resa"] * P["conso_kwh"] / 100 / kw
    t["libre_h_jour"] = (24 - t["resa_veh_jour"] * P["h_resa"]).clip(lower=0)
    possible = (t["recharge_h_jour"] <= P["part_libre"] / 100 * t["libre_h_jour"]).to_numpy() & (P["km_resa"] <= 0.8 * P["autonomie_km"])
    t["electrique"] = np.where(possible & a_borne, EV_OK, np.where(possible & P["borne_dediee"], EV_BORNE, EV_NON))
    ev = possible & (a_borne | P["borne_dediee"]) & P["priorite_ev"]
    t["motorisation"] = np.where(ev, "Électrique", "Thermique ou hybride")
    # recettes et coûts
    t["recettes"] = t["resa_an"] * prix / (1 + P["tva"])
    par_veh, par_station, borne = cout_annuel(C, scen)
    surcout_ve = P["surcout_ve"] / 6 * ev
    t["couts_th"] = t["voitures"] * (par_veh + P["autres_couts_veh"]) + par_station
    t["ecart_ev"] = np.where(possible & (a_borne | P["borne_dediee"]),
                             t["voitures"] * P["surcout_ve"] / 6 + np.where(a_borne, 0.0, borne), np.nan)
    t["couts"] = t["couts_th"] + np.where(ev, t["voitures"] * P["surcout_ve"] / 6 + np.where(a_borne, 0.0, borne), 0.0)
    t["marge"] = t["recettes"] - t["couts"]
    return t


def emplacements(sub, geo, seuil, hmin, n, rayon):
    g = geo.loc[sub.index]
    a_couvrir = ~g["couvert"].to_numpy()
    cand_mask = a_couvrir & (sub["nb_arrets_5min"].to_numpy() >= seuil) & (sub["habitants"].to_numpy() >= hmin)
    cand = sub[cand_mask].assign(pop_zone_libre=g.loc[cand_mask, "pop_zone_libre"].to_numpy()).nlargest(4000, "pop_zone_libre")
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
        lignes.append({"rang": rang, "idx": cand.index[k], "commune": c["commune"], "territoire": c["territoire"],
                       "habitants_gagnes": float(gains[k]), "arrets_a_5min": int(c["nb_arrets_5min"]), "lon": c["lon"], "lat": c["lat"]})
        restant[M[k].indices] = False
    return pd.DataFrame(lignes)


def vue(lon, lat, zoom):
    return pdk.ViewState(latitude=float(np.median(lat)), longitude=float(np.median(lon)), zoom=zoom)


def carte(couches, lon, lat, zoom=11.5, tooltip=None):
    st.pydeck_chart(pdk.Deck(layers=couches, initial_view_state=vue(lon, lat, zoom), map_provider="carto", map_style="light",
                             tooltip=tooltip), use_container_width=True)


def pct(titre):
    return st.column_config.ProgressColumn(titre, format="percent", min_value=0, max_value=1)


def nombre(titre):
    return st.column_config.NumberColumn(titre, format="%d")


# ---------- Données ----------
st.title("🚗 Citiz · Grenoble")
st.caption("Accès aux transports à 5 min à pied, qui est concerné, où ouvrir une station, électrique ou non, et ce que cela rapporte.")

manquants = [str(f) for f in (F_O1, F_O2, F_COUTS, F200) if not f.exists()]
if manquants:
    st.error("Fichiers introuvables :\n\n" + "\n".join(f"- `{m}`" for m in manquants))
    st.stop()

df_all = charger_carreaux()
stations = charger_stations()
bornes = charger_bornes()
gj1, o1 = charger_o1()
noms_citiz = tuple((str(r.station_id), r.nom) for r in stations[stations["reseau"] == "Alpes Loire"].itertuples()) if stations is not None else ()
o2 = charger_o2(noms_citiz)
C = charger_couts()

# ---------- Barre latérale ----------
sb = st.sidebar
sb.header("Zone d'implantation")
communes = df_all.groupby("commune")["habitants"].sum().sort_values(ascending=False).index.tolist()
choix_com = sb.multiselect("Communes de l'aire de Grenoble", communes, placeholder="Toute l'aire",
                           help="Vide = les 200 communes de l'aire. Grenoble est déjà bien couverte : les nouvelles stations se trouvent surtout en périphérie.")
sub = df_all[df_all["commune"].isin(choix_com)].copy() if choix_com else df_all.copy()
rayon = sb.select_slider("Zone de chalandise (m, à vol d'oiseau)", [250, 308, 400, 500, 600], RAYON_5MIN,
                         help="308 m = 5 min à pied (400 m) avec un coefficient de détour de 1,3.")
seuil = sb.slider("Au moins … arrêts de transport à 5 min", 0, 10, 1)
hmin = sb.slider("Habitants minimum dans le carreau de 200 m", 11, 300, 30)
n_st = sb.slider("Nombre de stations à proposer", 3, 40, 10)

sb.header("Données Citiz")
sb.caption("Chiffres annuels communiqués par Citiz pour l'ensemble du réseau.")
resa = sb.number_input("Réservations par an", 1, 10_000_000, 565_000, 5_000)
km = sb.number_input("Kilomètres parcourus par an", 1, 1_000_000_000, 42_000_000, 500_000)
heures = sb.number_input("Heures réservées par an", 1, 100_000_000, 5_500_000, 50_000)
vehicules = sb.number_input("Véhicules actifs", 1, 100_000, 3_000, 100)
clients = sb.number_input("Clients actifs", 1, 1_000_000, 50_000, 1_000)
n_stations = sb.number_input("Stations", 1, 10_000, 1_573, 10)
prix_ttc = sb.slider("Prix moyen d'une réservation, TTC (€)", 40.0, 60.0, 50.0, 1.0, help="Fourchette Citiz : 40 à 60 € par réservation.")
tva = sb.number_input("TVA", 0.0, 0.3, 0.20, 0.01)

sb.header("Hypothèses")
with sb.expander("Demande et flotte"):
    voitures_max = st.number_input("Voitures maximum par nouvelle station", 1, 10, 2, help="Citiz : 1,9 voiture par station en moyenne.")
    cap_resa_veh = st.number_input("Réservations maximum par voiture et par an", 100, 700, 250, 10,
                                   help="Citiz : 188 en moyenne. Au-delà de ce plafond, on ajoute une voiture.")
    autres_couts_veh = st.number_input("Autres coûts par voiture et par an (€)", 0, 20_000, 0, 100,
                                       help="Assurance, énergie, nettoyage : supposés inclus dans l'entretien de couts.yaml (2 000 € par station). À renseigner si Citiz les connaît.")
    scen = st.radio("Scénario de coûts", ["bas", "central", "haut"], index=1, horizontal=True)
with sb.expander("Électrique"):
    conso_kwh = st.number_input("Consommation (kWh/100 km)", 5.0, 40.0, 17.0, 0.5)
    autonomie_km = st.number_input("Autonomie (km)", 50, 800, 300, 10, help="300 km : autonomie annoncée dans le relevé GBFS de la flotte Citiz.")
    kw_voiture = st.number_input("Puissance de charge acceptée par la voiture (kW)", 2.0, 150.0, 22.0, 1.0)
    part_libre = st.slider("Part du temps libre utilisable pour recharger (%)", 10, 100, 50)
    borne_dediee = st.checkbox("Autoriser une borne dédiée s'il n'y a pas de borne publique à 300 m", True)
    kw_borne_dediee = st.number_input("Puissance d'une borne dédiée (kW)", 3.0, 50.0, 7.4, 0.1)
    priorite_ev = st.checkbox("Passer à l'électrique dès que c'est possible", True)
    surcout_ve = st.number_input("Surcoût d'achat d'une voiture électrique (€)", 0, 30_000, 0, 500,
                                 help="Non chiffré dans couts.yaml (même prix de voiture) : 0 par défaut.")

# paramètres dérivés des données Citiz
P = dict(prix_ttc=prix_ttc, tva=tva, h_resa=heures / resa, km_resa=km / resa, resa_client_an=resa / clients,
         resa_veh_an=resa / vehicules, clients_par_station=clients / n_stations, veh_par_station=vehicules / n_stations,
         voitures_max=voitures_max, cap_resa_veh=cap_resa_veh, autres_couts_veh=autres_couts_veh,
         conso_kwh=conso_kwh, autonomie_km=autonomie_km, kw_voiture=kw_voiture, part_libre=part_libre,
         borne_dediee=borne_dediee, kw_borne_dediee=kw_borne_dediee, priorite_ev=priorite_ev, surcout_ve=surcout_ve)

if sub.empty:
    st.info("Aucun carreau pour cette sélection.")
    st.stop()

geo, st_geo = geometrie(df_all, stations, bornes, rayon)
geo_sub = geo.loc[sub.index]

# calage de l'adoption : un client moyen par station Citiz = clients / stations
pop_moy = st_geo.loc[st_geo["pop_zone"] > 0, "pop_part"].mean() if st_geo is not None else np.nan
adoption_auto = P["clients_par_station"] / pop_moy if pop_moy and pop_moy > 0 else 0.01
adoption_pct = sb.number_input("Habitants qui deviennent clients (%)", 0.0, 10.0, round(adoption_auto * 100, 2), 0.05,
                               help=f"Calé sur le réseau : {P['clients_par_station']:.1f} clients par station ÷ {pop_moy:,.0f} habitants "
                                    "à portée en moyenne d'une station de l'aire de Grenoble. Modifiable.".replace(",", " "))
P["adoption"] = adoption_pct / 100

# ---------- Stations proposées ----------
sites = emplacements(sub, geo, seuil, hmin, n_st, rayon)
if not sites.empty:
    sites = sites.join(geo[["bornes_300m", "kw_max_300m"]], on="idx")
    sites = modele(sites.assign(clients_pot=sites["habitants_gagnes"]), P, C, scen)

# O1 sur le réseau piéton (Grenoble)
pop1 = o1["population_evaluee"].fillna(0)
o1_moy = float((o1["options_par_habitant"] * pop1).sum() / pop1.sum())
part_option = float(pop1[o1["options_par_habitant"] > 0].sum() / pop1.sum())
part_citiz_o1 = float(pop1[o1["options_citiz_par_habitant"] > 0].sum() / pop1.sum())
part_citiz_aire = float(sub["habitants"][geo_sub["couvert"]].sum() / sub["habitants"].sum())

# ---------- Indicateurs clés ----------
k = st.columns(5)
k[0].metric("Habitants avec une station Citiz à 5 min", f"{part_citiz_o1:.0%}",
            delta=f"{part_citiz_aire:.0%} sur la zone d'implantation", delta_color="off",
            help="Grenoble, calculé sur le réseau piéton (300 s à 4,5 km/h). Sous le chiffre : la zone choisie à gauche, à vol d'oiseau.")
k[1].metric("Options de transport par habitant", f"{o1_moy:.1f}",
            help=f"Grenoble, moyenne pondérée par la population ; {part_option:.1%} des habitants ont au moins une option à 5 min.")
if sites.empty:
    k[2].metric("Habitants nouvellement desservis", "0")
    st.info("Aucun emplacement avec ces critères : baisse le nombre d'arrêts ou d'habitants minimum.")
else:
    k[2].metric("Habitants nouvellement desservis", esp(sites["habitants_gagnes"].sum()),
                help=f"Habitants à moins de {rayon} m d'une station proposée, sans station Citiz à cette distance avant.")
    k[3].metric("Part électrifiable", f"{(sites['electrique'] != EV_NON).mean():.0%}",
                delta=f"{(sites['electrique'] == EV_OK).mean():.0%} avec borne publique", delta_color="off",
                help="Recharge compatible avec le temps libre entre deux locations, borne publique à 300 m ou borne dédiée.")
    k[4].metric("Marge annuelle par station", euros(sites["marge"].median()),
                delta=f"{int((sites['marge'] >= 0).sum())} stations sur {len(sites)} rentables", delta_color="off",
                help=f"Médiane des stations proposées, à {prix_ttc:.0f} € TTC par réservation, coûts {scen}. Estimation : voir l'onglet Rentabilité.")

onglets = st.tabs(["1 · Accès aux transports", "2 · Qui est concerné", "Où ouvrir ?", "Électrique ?", "Rentabilité", "Méthode et limites"])

# ---------- Onglet 1 : O1 ----------
with onglets[0]:
    st.subheader("Options de transport accessibles par habitant en 5 minutes à pied")
    mode = st.radio("Mode", list(MODES_O1), horizontal=True)
    col = MODES_O1[mode]
    vals = o1[col].fillna(0).to_numpy()
    vmax = max(float(np.quantile(vals, 0.95)), 1.0)
    rgb = couleur_continue(vals, vmax)
    feats = []
    for f, v, c in zip(gj1["features"], vals, rgb):
        p = f["properties"]
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"couleur": [int(c[0]), int(c[1]), int(c[2]), 200], "options": round(float(v), 1),
                                     "population": int(round(p.get("population") or 0)),
                                     "bus": round(p.get("options_bus_par_habitant") or 0, 1), "tram": round(p.get("options_tram_par_habitant") or 0, 1),
                                     "citiz": round(p.get("options_citiz_par_habitant") or 0, 1),
                                     "passages": int(round((p.get("passages_jour_bus_par_habitant") or 0) + (p.get("passages_jour_tram_par_habitant") or 0)))}})
    couche = pdk.Layer("GeoJsonLayer", data={"type": "FeatureCollection", "features": feats}, get_fill_color="properties.couleur",
                       get_line_color=[255, 255, 255, 120], line_width_min_pixels=0.3, stroked=True, filled=True, pickable=True)
    centre = o2
    carte([couche], centre["lon"], centre["lat"], 12.3,
          {"html": "<b>{properties.options} options par habitant</b> (" + mode.lower() + ")<br/>Habitants de la zone : {properties.population}<br/>"
                   "Bus : {properties.bus} · Tram : {properties.tram} · Citiz : {properties.citiz}"})
    st.markdown(f"<div style='display:flex;align-items:center;gap:8px;max-width:520px'><span>0</span>"
                f"<div style='flex:1;height:12px;border-radius:6px;background:{degrade_css()}'></div><span>{vmax:.0f} ou plus</span></div>"
                "<div style='font-size:0.85em;opacity:0.7'>Options par habitant : du plus froid (peu d'options) au plus chaud (beaucoup).</div>",
                unsafe_allow_html=True)
    classes = pd.cut(o1["options_par_habitant"].fillna(0), [-0.01, 0, 2, 5, 10, np.inf],
                     labels=["0", "1 à 2", "3 à 5", "6 à 10", "plus de 10"])
    rep = o1.assign(classe=classes).groupby("classe", observed=False)["population_evaluee"].sum().reset_index()
    rep["part"] = rep["population_evaluee"] / rep["population_evaluee"].sum()
    c1, c2 = st.columns([2, 3])
    c1.markdown("**Répartition des habitants**")
    c1.altair_chart(alt.Chart(rep).mark_bar(color=BLEU).encode(
        x=alt.X("classe:N", title="Options accessibles", sort=["0", "1 à 2", "3 à 5", "6 à 10", "plus de 10"]),
        y=alt.Y("part:Q", title="Part des habitants", axis=alt.Axis(format="%")),
        tooltip=["classe", alt.Tooltip("part:Q", format=".1%"), alt.Tooltip("population_evaluee:Q", format=",.0f", title="habitants")]).properties(height=220),
        use_container_width=True)
    c2.markdown("**À retenir**")
    c2.markdown(f"""
- **{part_option:.1%}** des habitants de Grenoble ont au moins une option à 5 min à pied ; en moyenne **{o1_moy:.1f}** options par habitant.
- **{part_citiz_o1:.0%}** ont une station Citiz à 5 min, **{pop1[o1['options_tram_par_habitant'] > 0].sum() / pop1.sum():.0%}** un tram, **{pop1[o1['options_bus_par_habitant'] > 0].sum() / pop1.sum():.0%}** un bus.
- Calcul sur le réseau piéton OpenStreetMap, {len(o1)} zones d'au moins 20 habitants, population Filosofi 2021 ({esp(pop1.sum())} habitants).
""")

# ---------- Onglet 2 : O2 ----------
with onglets[1]:
    st.subheader("Qui est concerné : les arrêts et stations, et le nombre d'habitants qu'ils desservent")
    choix_types = st.multiselect("Types", list(TYPES), default=list(TYPES), format_func=lambda t: TYPES[t][0])
    d = o2[o2["type"].isin(choix_types)].copy()
    d["rayon"] = 25 + 3.2 * np.sqrt(d["population_desservie"].fillna(0))
    d["couleur"] = d["type"].map(lambda t: list(TYPES[t][1]) + [200])
    d["type_txt"] = d["type"].map(lambda t: TYPES[t][0])
    d["pop_txt"] = d["population_desservie"].map(lambda v: esp(v) if pd.notna(v) else "?")
    d["valeur_txt"] = [(f"{v:.0f} passages par jour" if u == "passages_par_jour" else f"{v:.0f} places") if pd.notna(v) else "?" for v, u in zip(d["valeur"], d["unite"])]
    couche = pdk.Layer("ScatterplotLayer", data=d[["lon", "lat", "nom_affiche", "type_txt", "pop_txt", "valeur_txt", "rayon", "couleur"]],
                       get_position="[lon, lat]", get_fill_color="couleur", get_radius="rayon", radius_min_pixels=3, radius_max_pixels=40,
                       get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1, pickable=True)
    carte([couche], o2["lon"], o2["lat"], 12.3,
          {"html": "<b>{type_txt} · {nom_affiche}</b><br/>Habitants desservis à 5 min : {pop_txt}<br/>{valeur_txt}"})
    st.markdown(" &nbsp; ".join(f"<span style='color:rgb{TYPES[t][1]}'>●</span> {TYPES[t][0]}" for t in TYPES)
                + " &nbsp;·&nbsp; la taille du rond est proportionnelle au nombre d'habitants desservis", unsafe_allow_html=True)
    rec = (o2.groupby("type").agg(points=("offre_id", "size"), mediane=("population_desservie", "median"),
                                  maximum=("population_desservie", "max")).reindex(list(TYPES)).reset_index())
    rec["type"] = rec["type"].map(lambda t: TYPES[t][0])
    st.markdown("**Par type**")
    st.dataframe(rec, hide_index=True, use_container_width=True,
                 column_config={"type": "Type", "points": "Points dans Grenoble", "mediane": nombre("Habitants desservis (médiane)"),
                                "maximum": nombre("Habitants desservis (maximum)")})
    st.markdown("**Les 10 points qui desservent le plus d'habitants**")
    top = d.sort_values("population_desservie", ascending=False).head(10)
    st.dataframe(top[["type_txt", "nom_affiche", "population_desservie", "valeur_txt"]], hide_index=True, use_container_width=True,
                 column_config={"type_txt": "Type", "nom_affiche": "Nom", "population_desservie": nombre("Habitants desservis"), "valeur_txt": "Offre"})
    st.caption("Les populations se recouvrent d'un point à l'autre : un même habitant est desservi par plusieurs points. Ne pas les additionner. "
               "Le nombre d'habitants inclut les communes voisines atteignables en 5 min.")

# ---------- Onglet 3 : où ouvrir ----------
with onglets[2]:
    st.subheader("Où ouvrir ? Les habitants sans station Citiz à 5 min")
    st.write(f"La chaleur montre où vivent les habitants **sans station Citiz à moins de {rayon} m**. Les ronds sont les {len(sites)} stations proposées : "
             f"chacune touche le plus d'habitants encore non couverts, dans des carreaux d'au moins {hmin} habitants avec au moins {seuil} arrêt(s) à 5 min.")
    non_couverts = sub[~geo_sub["couvert"]]
    couches = [pdk.Layer("HeatmapLayer", data=non_couverts[["lon", "lat", "habitants"]], get_position="[lon, lat]", get_weight="habitants",
                         radius_pixels=45, intensity=1.2, threshold=0.03, aggregation="SUM",
                         color_range=[[44, 123, 182, 40], [171, 217, 233, 120], [255, 255, 191, 170], [253, 174, 97, 210], [215, 25, 28, 235]])]
    if stations is not None:
        s_aff = stations[stations["lon"].between(sub["lon"].min() - 0.03, sub["lon"].max() + 0.03) & stations["lat"].between(sub["lat"].min() - 0.03, sub["lat"].max() + 0.03)]
        couches.append(pdk.Layer("ScatterplotLayer", data=s_aff[["lon", "lat", "nom"]].assign(rang="Station Citiz", hab_txt="", etat=""),
                                 get_position="[lon, lat]", get_fill_color=[255, 255, 255, 255], get_line_color=[20, 20, 20], stroked=True,
                                 line_width_min_pixels=1.5, get_radius=60, radius_min_pixels=3, pickable=True))
    if not sites.empty:
        couleur = {EV_OK: [46, 125, 50], EV_BORNE: [129, 199, 132], EV_NON: [90, 90, 90]}
        aff = sites.assign(c=sites["electrique"].map(couleur), hab_txt=sites["habitants_gagnes"].map(esp), etat=sites["electrique"],
                           nom=sites["commune"])
        couches.append(pdk.Layer("ScatterplotLayer", data=aff[["lon", "lat", "rang", "nom", "hab_txt", "etat", "c"]], get_position="[lon, lat]",
                                 get_fill_color="c", get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=2, get_radius=90,
                                 radius_min_pixels=8, pickable=True))
    carte(couches, sub["lon"], sub["lat"], 10.8 if not choix_com else 12,
          {"html": "<b>{rang} · {nom}</b><br/>Habitants gagnés : {hab_txt}<br/>{etat}"})
    st.caption("Chaleur : habitants sans station Citiz à portée. Ronds blancs : stations Citiz existantes. Ronds verts : stations proposées "
               "(vert foncé : borne publique à 300 m, vert clair : borne à installer, gris : thermique).")
    if not sites.empty:
        tableau = sites[["rang", "commune", "habitants_gagnes", "arrets_a_5min", "bornes_300m", "electrique", "resa_an", "voitures",
                         "plafonnee", "recettes", "couts", "marge", "lon", "lat"]]
        st.dataframe(tableau, hide_index=True, use_container_width=True,
                     column_config={"rang": "N°", "commune": "Commune", "habitants_gagnes": nombre("Habitants gagnés"), "arrets_a_5min": "Arrêts à 5 min",
                                    "bornes_300m": "Bornes à 300 m", "electrique": "Électrique ?", "resa_an": nombre("Réservations/an"),
                                    "voitures": nombre("Voitures"), "plafonnee": "Plafonnée par la flotte", "recettes": nombre("Recettes HT (€/an)"), "couts": nombre("Coûts (€/an)"),
                                    "marge": nombre("Marge (€/an)")})
        st.download_button("Télécharger les emplacements (CSV)", tableau.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "emplacements_grenoble.csv", "text/csv")

# ---------- Onglet 4 : électrique ----------
with onglets[3]:
    st.subheader("La voiture peut-elle être électrique ?")
    st.markdown(f"""
Une station peut être électrique si **le temps de recharge tient dans le temps libre entre deux locations**, avec une borne à proximité.
Avec les données Citiz : une réservation dure **{P['h_resa']:.1f} h** et fait **{P['km_resa']:.0f} km** ; une voiture est réservée **{P['resa_veh_an'] / 365:.2f} fois par jour** en moyenne.
- Recharge par jour = réservations par jour × {P['km_resa']:.0f} km × {conso_kwh} kWh/100 km ÷ puissance de la borne (plafonnée à {kw_voiture:.0f} kW).
- Temps libre = 24 h − heures réservées ; on n'en utilise que {part_libre} %.
- Borne : une borne publique en accès libre à moins de {RAYON_BORNE} m, sinon une borne dédiée de {kw_borne_dediee} kW ({'autorisée' if borne_dediee else 'non autorisée'}).
""")
    if not sites.empty:
        c1, c2 = st.columns(2)
        rep = sites["electrique"].value_counts().rename_axis("electrique").reset_index(name="stations")
        c1.markdown("**Stations proposées**")
        c1.altair_chart(alt.Chart(rep).mark_arc(innerRadius=60).encode(theta="stations:Q", color=alt.Color("electrique:N", scale=COULEUR_EV, title=None),
                                                                      tooltip=["electrique", "stations"]).properties(height=260), use_container_width=True)
        c2.markdown("**Recharge nécessaire contre temps disponible (h par jour)**")
        c2.altair_chart(alt.Chart(sites.assign(dispo=sites["libre_h_jour"] * part_libre / 100)).mark_circle(size=90).encode(
            x=alt.X("dispo:Q", title="Temps utilisable pour recharger"), y=alt.Y("recharge_h_jour:Q", title="Recharge nécessaire"),
            color=alt.Color("electrique:N", scale=COULEUR_EV, title=None),
            tooltip=["commune", alt.Tooltip("recharge_h_jour:Q", format=".1f"), alt.Tooltip("dispo:Q", format=".1f"), "bornes_300m"]).properties(height=260),
            use_container_width=True)
    if st_geo is not None and bornes is not None:
        zone_st = st_geo[st_geo["pop_zone"] > 0]
        a_borne = pd.Series(np.nan, index=st_geo.index)
        arbre_b = cKDTree(metres(bornes["lon"], bornes["lat"]))
        d_borne = arbre_b.query(metres(stations["lon"], stations["lat"]))[0]
        in_zone = st_geo["pop_zone"].to_numpy() > 0
        m = st.columns(2)
        m[0].metric("Stations actuelles de la zone avec une borne publique à 300 m", f"{(d_borne[in_zone] <= RAYON_BORNE).mean():.0%}",
                    help=f"{int(in_zone.sum())} stations Citiz de l'aire de Grenoble.")
        m[1].metric("Bornes publiques en accès libre dans la zone", esp(int(((bornes["lon"].between(sub['lon'].min(), sub['lon'].max())) & (bornes["lat"].between(sub['lat'].min(), sub['lat'].max()))).sum())))
    if bornes is None:
        st.warning("Bornes introuvables : place `irve_consolide.csv` ou `bornes_ara.csv` dans eda-citiz/donnees.")

# ---------- Onglet 5 : rentabilité ----------
with onglets[4]:
    st.subheader("Rentabilité d'une station : données Citiz et coûts sourcés")
    st.info("Estimation. Les recettes viennent des chiffres Citiz (réservations, prix par réservation). Les coûts viennent de `couts.yaml` "
            "(sources publiques, euros 2025). L'usage d'une nouvelle station est calé sur la moyenne du réseau : à remplacer par l'usage mesuré station par station.")
    m = st.columns(4)
    m[0].metric("Réservations par véhicule et par an", f"{P['resa_veh_an']:.0f}", help=f"{P['resa_veh_an'] / 365:.2f} par jour")
    m[1].metric("Heures par réservation", f"{P['h_resa']:.1f}")
    m[2].metric("Kilomètres par réservation", f"{P['km_resa']:.0f}")
    m[3].metric("Recette HT par véhicule et par an", esp(P["resa_veh_an"] * prix_ttc / (1 + tva)) + " €")
    par_veh, par_station, borne = cout_annuel(C, scen)
    st.markdown(f"**Coût annuel, scénario {scen}** : {esp(par_veh)} € par véhicule (achat, boîtier et habillage amortis sur 6 ans) + "
                f"{esp(par_station)} € par station (aménagement amorti sur 10 ans et entretien). Une borne dédiée ajoute {esp(borne)} € par an, aide ADVENIR déduite.")
    if not sites.empty:
        grille = []
        for s_ in ["bas", "central", "haut"]:
            lig = {"Scénario de coûts": s_}
            for p_ in (40.0, 50.0, 60.0):
                lig[f"{p_:.0f} € la réservation"] = modele(sites.assign(clients_pot=sites["habitants_gagnes"]), P, C, s_, prix=p_)["marge"].sum()
            grille.append(lig)
        st.markdown("**Marge annuelle du portefeuille proposé** (somme des stations, € par an)")
        st.dataframe(pd.DataFrame(grille), hide_index=True, use_container_width=True,
                     column_config={c: nombre(c) for c in ["40 € la réservation", "50 € la réservation", "60 € la réservation"]})
        if sites["plafonnee"].any():
            st.caption(f"{int(sites['plafonnee'].sum())} stations sur {len(sites)} atteignent le plafond de la flotte ({voitures_max} voitures × {cap_resa_veh} réservations) : "
                       "ce sont les quartiers les plus denses, donc leurs résultats sont identiques. La demande réelle est à mesurer.")
        neg = sites.loc[sites["marge"] < 0, "marge"].sum()
        pos = sites.loc[sites["marge"] >= 0, "marge"].sum()
        m = st.columns(3)
        m[0].metric("Stations rentables seules", f"{int((sites['marge'] >= 0).sum())} sur {len(sites)}")
        m[1].metric("Marge des stations rentables", euros(pos))
        m[2].metric("Besoin de péréquation", euros(neg), help="Déficit des stations non rentables, à financer par le reste du réseau.")
        vue_m = sites.sort_values("marge", ascending=False).assign(nom=lambda t: t["rang"].astype(str) + ". " + t["commune"])
        st.altair_chart(alt.Chart(vue_m).mark_bar().encode(
            x=alt.X("marge:Q", title="Marge annuelle estimée (€ HT)"), y=alt.Y("nom:N", sort=vue_m["nom"].tolist(), title=None),
            color=alt.condition("datum.marge >= 0", alt.value(BLEU), alt.value(ROUGE)),
            tooltip=["commune", alt.Tooltip("recettes:Q", format=",.0f"), alt.Tooltip("couts:Q", format=",.0f"), alt.Tooltip("marge:Q", format=",.0f"), "electrique"]
        ).properties(height=max(200, 24 * len(vue_m))), use_container_width=True)

# ---------- Méthode ----------
with onglets[5]:
    st.subheader("Méthode et limites")
    st.markdown(f"""
**Accès (O1) et personnes concernées (O2).** Calculés par l'équipe géomatique sur le réseau piéton OpenStreetMap de Grenoble : 300 secondes à 4,5 km/h,
temps de raccordement compris aux deux extrémités. O1 = options accessibles par habitant, moyenne pondérée par la population, par zone d'au moins 20 habitants.
O2 = population à moins de 5 min de chaque arrêt ou station, communes voisines comprises.

**Où ouvrir.** Sur la zone d'implantation (aire de Grenoble, carreaux de 200 m), à vol d'oiseau : {RAYON_5MIN} m pour 5 min à pied. Chaque station ajoutée touche le plus
d'habitants encore sans station Citiz. Ni la voirie, ni les autorisations ne sont prises en compte.

**Usage.** Habitants gagnés × part qui devient cliente ({adoption_pct:.2f} %, calée pour qu'une station ait en moyenne {P['clients_par_station']:.0f} clients, comme sur le réseau Citiz)
× {P['resa_client_an']:.1f} réservations par client et par an. Le nombre de voitures s'ajuste (au plus {voitures_max}, {cap_resa_veh} réservations par voiture et par an) : au-delà, la demande est plafonnée par la flotte, sinon une station d'un quartier dense paraîtrait 5 fois meilleure que la moyenne du réseau ({resa / n_stations:.0f} réservations par station).

**Électrique.** Recharge nécessaire comparée au temps libre entre deux locations, borne publique à {RAYON_BORNE} m (fichier consolidé IRVE) ou borne dédiée.

**Rentabilité.** Recettes = réservations × prix TTC ÷ (1 + TVA). Coûts = `couts.yaml` (véhicule, boîtier, habillage, aménagement de la place, entretien ; borne dédiée moins aide ADVENIR pour l'électrique).

**Limites.**
- L'usage d'une nouvelle station est une moyenne du réseau, pas une mesure : une station en centre-ville et une station de périphérie ne se comportent pas pareil.
- Assurance, énergie et nettoyage sont supposés inclus dans l'entretien de `couts.yaml` (2 000 € par station) ; le champ « autres coûts par voiture » permet de les corriger.
- Le prix de 40 à 60 € par réservation est supposé TTC ; les 565 000 réservations sont annuelles.
- Les distances de la zone d'implantation sont à vol d'oiseau, donc les habitants à portée sont surestimés hors des centres.
- La population est celle des ménages fiscaux Filosofi 2021 ; les carreaux de moins de 11 habitants sont exclus.
- Les bornes publiques sont partagées avec d'autres usagers : une borne à 300 m ne garantit pas la recharge à l'heure voulue.
- Les populations O2 se recouvrent : ne pas les additionner. Le nom des stations Citiz vient du croisement avec la liste des stations du réseau Alpes Loire.
""")
