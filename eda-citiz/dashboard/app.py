"""Tableau de bord Citiz · carte d'accès aux transports, France métropolitaine.

Lancer depuis la racine du projet :  python -m streamlit run eda-citiz/dashboard/app.py
Données lues (CSV uniquement, aucun Parquet), dans eda-citiz/donnees/ :
  acces_france_<région>.csv      carreaux de 200 m avec le nombre d'options à 5 min par type (une ligne par carreau habité)
  offre_france.csv               arrêts et stations avec le nombre d'habitants desservis
  couverture_pieton_<n>.csv      carreaux couverts à 5 min à pied par chaque candidat
  stations_citiz_france.csv, tarifs_reference.csv  (relevé des réseaux Citiz : stations virtuelles, véhicules, tarif de référence)
"""
import os
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from scipy import sparse
from scipy.spatial import cKDTree

st.set_page_config(page_title="Citiz · Carte d'accès", page_icon="🚗", layout="wide")

RACINE = Path(os.environ.get("CITIZ_RACINE", Path(__file__).resolve().parent.parent))   # dossier eda-citiz
DONNEES = RACINE / "donnees"
FACCES = sorted(DONNEES.glob("acces_france_*.csv"))
FSTX = DONNEES / "stations_citiz_france.csv"
FTARIFS = DONNEES / "tarifs_reference.csv"
FHYP = DONNEES / "hypotheses_station_citiz.csv"
FCO2 = DONNEES / "hypotheses_co2.csv"
FO2 = DONNEES / "offre_france.csv"
FCOUV = sorted(DONNEES.glob("couverture_pieton_*.csv"))
HORS_AIRE = "Commune hors attraction des villes"
CATEGORIES = ["Métropole", "Grande ville", "Ville moyenne", "Zone rurale"]
DEFINITIONS = {"Métropole": "aire de 1 million d'habitants et plus", "Grande ville": "aire de 250 000 à 1 million d'habitants",
               "Ville moyenne": "aire de 50 000 à 250 000 habitants", "Zone rurale": "aire de moins de 50 000 habitants ou hors aire"}
CHALEUR = [[44, 123, 182, 60], [120, 190, 220, 120], [200, 230, 200, 160], [255, 255, 150, 190], [253, 174, 97, 215], [215, 25, 28, 235]]
BLEU, ROUGE = "#1F5A8C", "#C0392B"


TYPE_OFFRE = {"bus": "n_bus", "autre": "n_bus", "tram": "n_tram_metro", "metro": "n_tram_metro", "trolleybus": "n_tram_metro", "funiculaire": "n_tram_metro",
              "cable_car": "n_tram_metro", "ferry": "n_tram_metro", "telepherique": "n_tram_metro", "train": "n_train", "velo": "n_velo",
              "autopartage": "n_autopartage", "citiz": "n_citiz"}
COULEURS_OFFRE = {"bus": [70, 100, 160], "autre": [70, 100, 160], "tram": [200, 40, 40], "metro": [140, 30, 120], "trolleybus": [70, 100, 160], "funiculaire": [90, 90, 90],
                  "cable_car": [90, 90, 90], "telepherique": [90, 90, 90], "train": [20, 20, 120], "ferry": [30, 150, 190], "velo": [30, 160, 90], "autopartage": [160, 110, 40], "citiz": [0, 0, 0]}
LIBELLE_OFFRE = {"bus": "Bus et cars", "autre": "Bus et cars", "trolleybus": "Bus et cars", "tram": "Tramway", "metro": "Métro", "train": "Gare", "velo": "Vélos en libre-service",
                 "autopartage": "Autopartage", "citiz": "Station Citiz", "funiculaire": "Câble et bateau", "cable_car": "Câble et bateau", "telepherique": "Câble et bateau", "ferry": "Câble et bateau"}


def esp(v, d=0):
    return f"{v:,.{d}f}".replace(",", " ")


def metres(lon, lat):
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return np.c_[lon * 111_320 * np.cos(np.radians(45.3)), lat * 110_540]


def categorie(pop_aire):
    if pop_aire >= 1_000_000:
        return "Métropole"
    if pop_aire >= 250_000:
        return "Grande ville"
    if pop_aire >= 50_000:
        return "Ville moyenne"
    return "Zone rurale"


# ---------- Chargement ----------
@st.cache_resource(show_spinner="Chargement des carreaux de 200 m de toute la France…")
def charger():
    df = pd.concat([pd.read_csv(f, dtype={"region": str}) for f in FACCES]).sort_values("k").reset_index(drop=True)
    for c in ("commune", "aire", "region"):
        df[c] = df[c].astype("category")
    df["categorie"] = pd.cut(df["pop_aire"], [-1, 49_999, 249_999, 999_999, 1e12], labels=["Zone rurale", "Ville moyenne", "Grande ville", "Métropole"]).astype(str)
    df.loc[df["aire"] == HORS_AIRE, "categorie"] = "Zone rurale"
    df["categorie"] = df["categorie"].astype("category")
    xy = metres(df["lon"], df["lat"])
    df["x"], df["y"] = xy[:, 0], xy[:, 1]
    df["h_citiz"] = df["habitants"] * (df["nb_stations_citiz_5min"] > 0)
    df["h_sans_voiture"] = df["habitants"] * df["part_sans_voiture"]
    if "menages" in df:
        df["h_revenu"] = df["menages"] * df["revenu"]          # revenu pondéré par les ménages
        df["h_65"] = df["habitants"] * df["part_65_plus"]
    return df


TYPES = {"n_bus": "Bus et cars", "n_tram_metro": "Tram et métro", "n_train": "Trains (gares)", "n_velo": "Vélos en libre-service",
         "n_autopartage": "Autres autopartages", "n_citiz": "Stations Citiz"}


def avec_options(df, choisis):
    """Recalcule le nombre d'options (et les parts d'habitants) selon les types cochés."""
    df = df.copy(deep=False)       # copie légère : les colonnes d'origine restent partagées
    df["nb_options_5min"] = df[choisis].sum(axis=1) if choisis else 0
    df["h_sans"] = df["habitants"] * (df["nb_options_5min"] == 0)
    df["h_options"] = df["habitants"] * df["nb_options_5min"]
    return df


@st.cache_resource
def charger_offre():
    return pd.read_csv(FO2) if FO2.exists() else None


@st.cache_resource
def charger_stations():
    """Stations Citiz de toute la France (relevé GBFS des réseaux), avec nom de commune, réseau, stations virtuelles et véhicules."""
    if FSTX.exists():
        s = pd.read_csv(FSTX).assign(commune=lambda d: d["commune"].fillna(""))
        s["virtuelle"] = s["virtuelle"].astype(bool)
        if "a_venir" in s:
            s = s[s["a_venir"] == 0]
        return s.reset_index(drop=True)
    o = charger_offre()
    if o is None:
        return None
    return o[o["type"] == "citiz"][["lon", "lat", "nom"]].reset_index(drop=True).assign(commune="", virtuelle=False, reseau="", vehicules=0)


@st.cache_resource
def charger_a_venir():
    if not FSTX.exists():
        return None
    s = pd.read_csv(FSTX)
    return s[s["a_venir"] == 1].assign(commune="", type_txt="Ouverture prochaine").reset_index(drop=True) if "a_venir" in s else None


@st.cache_resource
def charger_hypotheses():
    h = pd.read_csv(FHYP).set_index("scenario") if FHYP.exists() else None
    c = pd.read_csv(FCO2).iloc[0] if FCO2.exists() else None
    return h, c


@st.cache_resource
def charger_tarifs():
    return pd.read_csv(FTARIFS).set_index("system_id")["prix_location_type"] if FTARIFS.exists() else None


@st.cache_resource(show_spinner="Chargement de la couverture à pied des candidats…", hash_funcs={np.ndarray: lambda a: len(a)})
def charger_couverture(cles):
    """Matrice candidat x carreau : 1 si le carreau est à 5 min à pied (réseau piéton) du candidat. `cles` : numéro k de chaque ligne du tableau."""
    if not FCOUV:
        return None
    c = pd.concat([pd.read_csv(f) for f in FCOUV])
    pos = pd.Series(np.arange(len(cles)), index=cles)
    c = c[c["candidat"].isin(pos.index) & c["carreau"].isin(pos.index)]
    n = len(cles)
    return sparse.csr_matrix((np.ones(len(c), np.float32), (pos[c["candidat"]].to_numpy(), pos[c["carreau"]].to_numpy())), shape=(n, n))


def candidats(df, sub, M, seuil, hmin, n):
    """Choix glouton sur le réseau piéton : chaque candidat gagne le plus d'habitants encore sans station Citiz à 5 min à pied."""
    if M is None:
        return pd.DataFrame()
    hab = df["habitants"].to_numpy(float)
    base = hab * (df["n_citiz"].to_numpy() == 0)          # habitants sans station Citiz à 5 min
    gains0 = M @ base
    dans_zone = np.zeros(len(df), bool)
    dans_zone[sub.index.to_numpy()] = True
    masque = dans_zone & (gains0 > 0) & (hab >= hmin) & (df["nb_arrets_5min"].to_numpy() >= seuil)
    cand = np.flatnonzero(masque)
    if cand.size == 0:
        return pd.DataFrame()
    cand = cand[np.argsort(-gains0[cand])[:4000]]
    Mc = M[cand]
    restant = base.copy()
    lignes = []
    for rang in range(1, n + 1):
        gains = Mc @ restant
        k = int(gains.argmax())
        if gains[k] <= 0:
            break
        r = df.iloc[cand[k]]
        lignes.append({"rang": rang, "commune": r["commune"], "aire": r["aire"], "categorie": r["categorie"],
                       "habitants_gagnes": float(gains[k]), "arrets_a_5min": int(r["nb_arrets_5min"]), "lon": r["lon"], "lat": r["lat"]})
        restant[Mc[k].indices] = 0
    return pd.DataFrame(lignes)


# échelle de chaleur : positions (part de la longueur) et couleurs ; les repères 0 / 0,25 / 0,5 / 1 / 3 options tombent à 0 / 20 / 40 / 70 / 100 %
POS_VALEURS = np.array([0, 1 / 12, 1 / 6, 1 / 3, 1.0])     # en part de l'échelle (3 options = 1)
POS_RAMPE = np.array([0, 0.2, 0.4, 0.7, 1.0])
RAMPE_T = np.array([0, .1, .2, .3, .4, .55, .7, .85, 1.0])
RAMPE_RGB = np.array([[35, 45, 205], [75, 105, 235], [115, 190, 230], [110, 200, 165], [115, 190, 100], [200, 215, 80], [245, 215, 60], [240, 140, 50], [215, 45, 35]], float)


def couleur_chaleur(v, vmax):
    """Couleur (RGB) d'une valeur d'options par habitant : bleu foncé (0) -> bleu clair -> vert -> jaune -> orange -> rouge (échelle non linéaire)."""
    t = np.interp(np.clip(np.asarray(v, float) / vmax, 0, 1), POS_VALEURS, POS_RAMPE)
    return np.stack([np.interp(t, RAMPE_T, RAMPE_RGB[:, c]) for c in range(3)], axis=-1)


def gaussian_filter(a, sigma):
    """Flou gaussien en numpy pur (le module scipy.ndimage est bloqué sur certains postes)."""
    def noyau(n):
        i = np.arange(n)
        k = np.exp(-0.5 * ((i[:, None] - i[None, :]) / sigma) ** 2)
        return k / k.sum(axis=1, keepdims=True)
    return noyau(a.shape[0]) @ a @ noyau(a.shape[1]).T


def grille_chaleur(d, facteur, vmax):
    """Chaleur continue : moyenne des options à 5 min pondérée par la population, lissée, en petites cellules fondues sur les bords."""
    marge = max(0.02, 0.03 * max(d["lon"].max() - d["lon"].min(), d["lat"].max() - d["lat"].min()))   # marge pour que le fondu de bord ne soit pas coupé
    lon0, lon1 = d["lon"].min() - marge, d["lon"].max() + marge
    merc = lambda lat: np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))   # le fond de carte est en Mercator
    m0, m1 = merc(d["lat"].min() - marge), merc(d["lat"].max() + marge)
    cote_deg = max(lon1 - lon0, m1 - m0) / 300
    larg, haut = int(np.ceil((lon1 - lon0) / cote_deg)), int(np.ceil((m1 - m0) / cote_deg))
    cote_m = cote_deg * 111_320 * np.cos(np.radians(d["lat"].mean()))                 # côté d'une cellule en mètres
    sigma = max(1.2, facteur * max(250.0, 3 * cote_m) / cote_m)                         # lissage en cellules
    ix = np.minimum(((d["lon"] - lon0) / cote_deg).astype(int), larg - 1).to_numpy()
    iy = np.minimum(((m1 - merc(d["lat"])) / cote_deg).astype(int), haut - 1).to_numpy()
    num = np.zeros((haut, larg)); den = np.zeros_like(num)
    np.add.at(num, (iy, ix), (d["nb_options_5min"] * d["habitants"]).to_numpy())
    np.add.at(den, (iy, ix), d["habitants"].to_numpy())
    n, m = gaussian_filter(num, sigma), gaussian_filter(den, sigma)
    presence = gaussian_filter((den > 0).astype(float), sigma * 1.5)
    ok = presence > 0.03
    jy, jx = np.nonzero(ok)
    v = n[jy, jx] / np.maximum(m[jy, jx], 1e-12)
    rgb = couleur_chaleur(v, vmax)
    merc_y = m1 - (jy + 0.5) * cote_deg
    lat = np.degrees(2 * np.arctan(np.exp(np.radians(merc_y))) - np.pi / 2)
    out = pd.DataFrame({"lon": lon0 + (jx + 0.5) * cote_deg, "lat": lat, "r": rgb[:, 0].astype(int), "g": rgb[:, 1].astype(int),
                        "b": rgb[:, 2].astype(int), "a": (np.clip(presence[jy, jx] / 0.12, 0, 1) * 150).astype(int)})
    out.attrs["cote"] = float(cote_m)
    return out


DOSSIER_STATIC = Path(__file__).resolve().parent / "static"


def fond_relief():
    """Fond de carte à relief (Esri World Topo : relief, mer, frontières, noms) : style écrit dans static/ et servi par Streamlit."""
    import json
    DOSSIER_STATIC.mkdir(exist_ok=True)
    f = DOSSIER_STATIC / "fond_relief.json"
    f.write_text(json.dumps({"version": 8, "sources": {"esri": {"type": "raster", "tileSize": 256, "maxzoom": 17, "attribution": "Fond © Esri, OpenStreetMap",
                             "tiles": ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"]}},
                             "layers": [{"id": "esri", "type": "raster", "source": "esri"}]}))
    return "fond_relief.json"


def image_chaleur(d, facteur, vmax):
    """Chaleur continue en image (une couleur par pixel, lissée) : écrit un PNG servi par Streamlit et renvoie (nom, limites)."""
    import hashlib
    from PIL import Image
    marge = max(0.02, 0.03 * max(d["lon"].max() - d["lon"].min(), d["lat"].max() - d["lat"].min()))   # marge pour que le fondu de bord ne soit pas coupé
    lon0, lon1 = d["lon"].min() - marge, d["lon"].max() + marge
    merc = lambda lat: np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))   # le fond de carte est en Mercator
    m0, m1 = merc(d["lat"].min() - marge), merc(d["lat"].max() + marge)
    grand = max(lon1 - lon0, m1 - m0)
    larg, haut = int(np.clip(1400 * (lon1 - lon0) / grand, 120, 1400)), int(np.clip(1400 * (m1 - m0) / grand, 120, 1400))
    px_m = (lon1 - lon0) / larg * 111_320 * np.cos(np.radians(d["lat"].mean()))
    sigma = max(1.5, facteur * max(250.0, 3 * px_m) / px_m)
    ix = np.minimum(((d["lon"] - lon0) / (lon1 - lon0) * larg).astype(int), larg - 1).to_numpy()
    iy = np.minimum(((m1 - merc(d["lat"])) / (m1 - m0) * haut).astype(int), haut - 1).to_numpy()
    num = np.zeros((haut, larg)); den = np.zeros_like(num)
    np.add.at(num, (iy, ix), (d["nb_options_5min"] * d["habitants"]).to_numpy())
    np.add.at(den, (iy, ix), d["habitants"].to_numpy())
    n, m = gaussian_filter(num, sigma), gaussian_filter(den, sigma)
    presence = gaussian_filter((den > 0).astype(float), sigma * 1.5)
    v = n / np.maximum(m, 1e-12)
    rgb = couleur_chaleur(v, vmax)
    alpha = np.clip(presence / 0.06, 0, 1) * 205
    img = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
    DOSSIER_STATIC.mkdir(exist_ok=True)
    nom = "chaleur_" + hashlib.md5(img.tobytes()).hexdigest()[:12] + ".png"
    if not (DOSSIER_STATIC / nom).exists():
        img.save(DOSSIER_STATIC / nom)
        for vieux in sorted(DOSSIER_STATIC.glob("chaleur_*.png"), key=lambda q: q.stat().st_mtime)[:-40]:
            vieux.unlink()
    return nom, [float(lon0), float(d["lat"].min() - marge), float(lon1), float(d["lat"].max() + marge)]


def vue(lon, lat):
    if len(lon) == 0:
        return pdk.ViewState(latitude=46.6, longitude=2.5, zoom=4.6)
    etendue = max(lat.max() - lat.min(), (lon.max() - lon.min()) * 0.7, 0.02)
    zoom = float(np.clip(np.log2(560 / etendue) - 1.0 - (0.4 if etendue > 8 else 0), 4.2, 14))
    return pdk.ViewState(latitude=float((lat.max() + lat.min()) / 2 - (0.6 if etendue > 8 else 0)), longitude=float((lon.max() + lon.min()) / 2), zoom=zoom)


def indicateurs(d):
    h = d["habitants"].sum()
    return {"habitants": h, "part_citiz": d["h_citiz"].sum() / h if h else 0, "part_sans": d["h_sans"].sum() / h if h else 0,
            "options": d["h_options"].sum() / h if h else 0}


# ---------- Données ----------
st.title("🚗 Citiz · Carte d'accès aux transports")
st.caption("Options de transport à 5 minutes à pied · France métropolitaine")

if not FACCES:
    st.error(f"Fichiers introuvables : `{DONNEES}` ne contient pas de `acces_france_*.csv`.")
    st.stop()
df = charger()
stations = charger_stations()

# ---------- Barre latérale : choix de la ville ----------
sb = st.sidebar
sb.header("Ce qui compte comme option")
choisis = [k for k, v in TYPES.items() if sb.checkbox(v, True, key="t_" + k)]
echelle = sb.slider("Échelle : rouge à partir de … options", 2, 12, 3, help="Au-delà de cette valeur la couleur ne change plus. Même échelle pour toutes les villes, donc comparable.")
df = avec_options(df, choisis)
sb.header("Où regarder ?")
cat = sb.radio("Catégorie", ["Toute la France"] + CATEGORIES, help="Chaque commune prend la catégorie de son aire d'attraction (la ville et sa périphérie vont ensemble).")
if cat != "Toute la France":
    sb.caption(DEFINITIONS[cat])
    d_cat = df[df["categorie"] == cat]
    villes = d_cat.groupby("aire", observed=True)["habitants"].sum().sort_values(ascending=False).index.tolist()
    ville = sb.selectbox("Ville", ["Toutes les villes de cette catégorie"] + villes)
    sub = d_cat if ville.startswith("Toutes") else d_cat[d_cat["aire"] == ville]
    titre_zone = cat if ville.startswith("Toutes") else ville
else:
    sub, titre_zone = df, "France métropolitaine"

sb.header("Couches")
afficher_stations = sb.checkbox("Stations Citiz", False)
afficher_virtuelles = sb.checkbox("Stations virtuelles", False, help="Stations sans emplacement matérialisé (relevé des réseaux Citiz, toute la France).")
afficher_a_venir = sb.checkbox("Stations Citiz annoncées", False, help="Stations notées « ouverture prochaine » dans le relevé des réseaux.")
afficher_candidats = sb.checkbox("Candidats pour une nouvelle station", False)
afficher_offre = sb.checkbox("Arrêts et stations (taille = habitants desservis)", False, help="Chaque arrêt, station de vélos ou d'autopartage, avec le nombre d'habitants à moins de 5 min à pied.")
lissage = sb.slider("Lissage de la chaleur", 0.5, 4.0, 1.0, 0.5, help="Plus la valeur est grande, plus la chaleur est étalée entre quartiers voisins.")

sb.header("Candidats")
n_cand = sb.slider("Nombre de candidats", 3, 40, 10, help="Combien de nouveaux emplacements proposer, du plus utile au moins utile.")
hmin = sb.slider("Habitants minimum à l'emplacement", 11, 300, 30, help="On écarte les carreaux de 200 m presque vides.")
with sb.expander("Réglage avancé"):
    seuil = st.slider("Arrêts de transport minimum à 5 min", 0, 10, 1, help="Une station Citiz complète les transports en commun : on cherche là où ils existent déjà. Mettre 0 pour ne pas tenir compte des transports.")

if sub.empty:
    st.info("Aucun carreau pour cette sélection.")
    st.stop()

cands = candidats(df, sub, charger_couverture(df["k"].to_numpy()), seuil, hmin, n_cand)
ind = indicateurs(sub)

# stations de la zone : celles dont le carreau habité le plus proche appartient à la zone
@st.cache_resource(show_spinner=False)
def plus_proche_carreau(_df, _stations):
    return cKDTree(_df[["x", "y"]].to_numpy()).query(metres(_stations["lon"], _stations["lat"]))[1]


st_zone = pd.DataFrame()
carreau_station = None
if stations is not None:
    carreau_station = plus_proche_carreau(df, stations)
    dans = np.zeros(len(df), bool)
    dans[sub.index.to_numpy()] = True
    st_zone = stations[dans[carreau_station]]

# ---------- Chiffres clés ----------
hyp, co2 = charger_hypotheses()
hab = sub["habitants"].sum()
sans_voiture = sub["h_sans_voiture"].sum()
n = len(cands)
st.subheader(titre_zone)
k = st.columns(5)
k[0].metric("Habitants", esp(hab))
k[1].metric("Sans aucune option à 5 min", f"{ind['part_sans']:.0%}", help="Ni arrêt de transport, ni station Citiz à 5 min à pied.")
k[2].metric("Station Citiz à 5 min", f"{ind['part_citiz']:.0%}", help="Part des habitants à moins de 5 min à pied d'une station Citiz.")
k[3].metric("Ménages sans voiture", f"{sans_voiture / hab:.0%}" if hab else "-", help="Part de ménages sans voiture (INSEE), pondérée par la population.")
k[4].metric("Stations Citiz", esp(len(st_zone)), delta=f"dont {int(st_zone['virtuelle'].sum()) if len(st_zone) else 0} virtuelles", delta_color="off")

# ---------- Carte ----------
if st.get_option("server.enableStaticServing"):
    nom_png, bornes = image_chaleur(sub, lissage, echelle)
    hote = st.context.headers.get("Host", "localhost:8501")
    chaleur = pdk.Layer("BitmapLayer", data=None, image=f"http://{hote}/app/static/{nom_png}", bounds=bornes, opacity=1.0)
else:   # repli sans image : petites cellules lissées
    grille = grille_chaleur(sub, lissage, echelle)
    chaleur = pdk.Layer("GridCellLayer", data=grille, get_position="[lon, lat]", cell_size=grille.attrs["cote"] * 1.06, coverage=1,
                        get_fill_color="[r, g, b, a]", extruded=False, pickable=False)
couches = [chaleur]
style_fond = "road"
if st.get_option("server.enableStaticServing"):
    style_fond = f"http://{hote}/app/static/{fond_relief()}"
tooltip_stations = {"html": "<b>{nom}</b><br/>{commune}<br/>{type_txt}"}
if stations is not None and len(st_zone):
    normales = st_zone[~st_zone["virtuelle"]].assign(type_txt="Station Citiz")
    virt = st_zone[st_zone["virtuelle"]].assign(type_txt="Station virtuelle")
    if afficher_stations and len(normales):
        couches.append(pdk.Layer("ScatterplotLayer", data=normales[["lon", "lat", "nom", "commune", "type_txt"]], get_position="[lon, lat]",
                                 get_fill_color=[255, 255, 255, 255], get_line_color=[20, 20, 20], stroked=True, line_width_min_pixels=1,
                                 get_radius=40, radius_min_pixels=2.5, pickable=True))
    if afficher_virtuelles and len(virt):
        couches.append(pdk.Layer("ScatterplotLayer", data=virt[["lon", "lat", "nom", "commune", "type_txt"]], get_position="[lon, lat]",
                                 get_fill_color=[148, 103, 189, 255], get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=2,
                                 get_radius=110, radius_min_pixels=7, pickable=True))
av = charger_a_venir()
if afficher_a_venir and av is not None and len(av):
    av_z = av[av["lon"].between(sub["lon"].min() - 0.02, sub["lon"].max() + 0.02) & av["lat"].between(sub["lat"].min() - 0.02, sub["lat"].max() + 0.02)]
    if len(av_z):
        couches.append(pdk.Layer("ScatterplotLayer", data=av_z[["lon", "lat", "nom", "commune", "type_txt"]], get_position="[lon, lat]",
                                 get_fill_color=[255, 127, 14, 230], get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1.5,
                                 get_radius=60, radius_min_pixels=5, pickable=True))
offre = charger_offre()
if afficher_offre and offre is not None:
    o = offre[offre["type"].map(lambda t: TYPE_OFFRE.get(t, "n_bus") in choisis)]
    large = sub["lon"].max() - sub["lon"].min() > 3 or sub["lat"].max() - sub["lat"].min() > 2.5
    if large:      # vue large : tous les modes sauf bus, plus les 2 000 bus qui desservent le plus d'habitants
        bus = o[o["type"].map(lambda t: LIBELLE_OFFRE.get(t) == "Bus et cars")]
        o = pd.concat([o.drop(bus.index), bus.nlargest(2000, "population_desservie")])
    if len(sub) < len(df):
        o = o[o["lon"].between(sub["lon"].min() - 0.02, sub["lon"].max() + 0.02) & o["lat"].between(sub["lat"].min() - 0.02, sub["lat"].max() + 0.02)]
    o = o.assign(couleur=o["type"].map(lambda t: COULEURS_OFFRE.get(t, [120, 120, 120])), commune="",
                 type_txt=o["type"].map(lambda t: LIBELLE_OFFRE.get(t, "Autre")) + " · " + o["population_desservie"].fillna(0).map(lambda v: f"{esp(v)} habitants à 5 min"))
    if len(o):
        couches.insert(1, pdk.Layer("ScatterplotLayer", data=o[["lon", "lat", "nom", "commune", "type_txt", "population_desservie", "couleur"]], get_position="[lon, lat]",
                                    get_fill_color="[couleur[0], couleur[1], couleur[2], 210]", get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=0.5,
                                    get_radius="30 + population_desservie / 6", radius_min_pixels=2.5, radius_max_pixels=26, pickable=True))
if afficher_candidats and not cands.empty:
    aff = cands.assign(nom="Candidat " + cands["rang"].astype(str), type_txt=cands["habitants_gagnes"].map(lambda v: f"{esp(v)} habitants gagnés"),
                       rang_txt=cands["rang"].astype(str))
    couches.append(pdk.Layer("ScatterplotLayer", data=aff[["lon", "lat", "nom", "commune", "type_txt"]], get_position="[lon, lat]",
                             get_fill_color=[20, 20, 20, 240], get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=2,
                             get_radius=120, radius_min_pixels=11, pickable=True))
    couches.append(pdk.Layer("TextLayer", data=aff[["lon", "lat", "rang_txt"]], get_position="[lon, lat]", get_text="rang_txt", get_size=14,
                             get_color=[255, 255, 255], get_text_anchor="'middle'", get_alignment_baseline="'center'"))
st.pydeck_chart(pdk.Deck(layers=couches, initial_view_state=vue(sub["lon"], sub["lat"]), map_provider="carto", map_style=style_fond,
                         tooltip=tooltip_stations), width="stretch", height=700)
_stops = ",".join(f"rgb({int(c[0])},{int(c[1])},{int(c[2])}) {int(t * 100)}%" for t, c in zip(RAMPE_T, RAMPE_RGB))
_ticks = "".join(f"<span style='position:absolute;white-space:nowrap;left:{int(p * 100)}%;transform:translateX({'-100%' if q == 1 else '-50%'})'>{(f'{echelle * q:g}'.replace('.', ',')) + (' et +' if q == 1 else '')}</span>" for p, q in zip(POS_RAMPE, POS_VALEURS))
st.markdown(f"<div style='max-width:520px;padding:12px 16px;border-radius:14px;box-shadow:0 1px 6px rgba(0,0,0,.18)'>"
            f"<div style='font-weight:600;margin-bottom:8px'>Solutions de transport à 5 min à pied par habitant</div>"
            f"<div style='height:14px;border-radius:7px;background:linear-gradient(to right,{_stops})'></div>"
            f"<div style='position:relative;height:20px;margin-top:4px;font-size:0.9em'>{_ticks}</div>"
            f"<div style='display:flex;justify-content:space-between;font-size:0.85em;opacity:0.7'><span>plus froid</span><span>plus chaud</span></div>"
            "<div style='font-size:0.8em;opacity:0.75;margin-top:6px'>Taille des points = habitants desservis · "
            "<span style='color:#4664a0'>●</span> bus · <span style='color:#c82828'>●</span> tram · <span style='color:#8c1e78'>●</span> métro · "
            "<span style='color:#141478'>●</span> gare · <span style='color:#1ea05a'>●</span> vélos · <span style='color:#a06e28'>●</span> autopartage · "
            "<span style='color:#444'>○</span> Citiz · <span style='color:#9467bd'>●</span> virtuelle · <span style='color:#ff7f0e'>●</span> annoncée · <b>●</b> candidat</div></div>", unsafe_allow_html=True)

# ---------- Candidats ----------
if not cands.empty:
    with st.expander("Liste des candidats"):
        st.dataframe(cands[["rang", "commune", "aire", "categorie", "habitants_gagnes", "arrets_a_5min", "lon", "lat"]], hide_index=True, width="stretch",
                     column_config={"rang": "N°", "commune": "Commune", "aire": "Aire d'attraction", "categorie": "Catégorie",
                                    "habitants_gagnes": st.column_config.NumberColumn("Habitants gagnés", format="%d"), "arrets_a_5min": "Arrêts à 5 min"})
        st.download_button("Télécharger (CSV)", cands.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"), "candidats.csv", "text/csv")
elif afficher_candidats:
    st.info("Aucun candidat avec ces critères : baisse le nombre d'arrêts ou d'habitants minimum.")

with st.expander("Méthode et limites"):
    st.markdown(f"""
- **Accès :** options (arrêts, gares, vélos, autopartage, Citiz) à 5 min à pied sur le réseau piéton, par carreau de 200 m (Filosofi 2021). Environ 7 % des habitants, surtout ruraux, sont mesurés à vol d'oiseau.
- **Catégories :** aire d'attraction INSEE 2020 (Métropole ≥ 1 M, Grande ville 250 k à 1 M, Ville moyenne 50 k à 250 k, Zone rurale).
- **Candidats :** choix glouton sur le réseau piéton, chaque habitant compté une fois, carreaux d'au moins {hmin} habitants. Gains prudents ; ni voirie, ni autorisation, ni fréquences ne sont pris en compte.
- **Coûts, marge, CO₂ :** hypothèses de l'équipe pour une station et une voiture ; la marge dépend fortement de l'usage.
""")
