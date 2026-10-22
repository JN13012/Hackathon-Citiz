"""Construit la table silver `carreaux_socio` (rôle 3 : la population et son profil).

Une ligne par carreau de 200 m habité du territoire, avec population, ménages, revenus, âges,
part des ménages sans voiture, équipements et emplois, en Lambert 93 (EPSG:2154).

Sources (téléchargées une fois dans data/bronze/insee/ ; Filosofi dans data/bronze/filosofi/, partagé avec le rôle 4) :
  Filosofi 2021 carreaux 200 m, recensement (logement IRIS, emplois commune : 2023 sinon 2022),
  BPE 2025 fichier-détail, contours IRIS IGN (service WFS ou fichier), populations de référence 2023.

Sorties (data/silver/) :
  carreaux_socio.parquet (GeoParquet), carreaux_socio.csv (sans géométrie, avec lat/lon),
  equipements.parquet, dictionnaire_carreaux_socio.md, SOURCES_population.md, carte_carreaux_socio.png

Code de sortie : 0 si tous les contrôles passent, 1 sinon.
"""
import argparse
import datetime
import glob
import http.client
import io
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

# Certificats HTTPS : urllib utilise le magasin d'OpenSSL de Python, vide avec l'installeur
# python.org sur macOS (erreur CERTIFICATE_VERIFY_FAILED). On prend le trousseau du système
# (truststore, utile derrière un réseau qui inspecte le HTTPS), sinon les certificats de certifi.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    import ssl
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "optim"))
import territoire as T  # noqa: E402  (territoire : TERRITOIRE=ara|france)
AURA = ("01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74")


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--departements", default=",".join(T.DEPARTEMENTS or AURA),
                   help="codes départements du territoire, séparés par des virgules (défaut : ceux du territoire)")
    p.add_argument("--communes", default="",
                   help="codes INSEE de communes, séparés par des virgules ; prend le dessus sur --departements")
    p.add_argument("--contours-iris", default="",
                   help="fichier ou lien des contours IRIS (gpkg/shp/zip/7z) ; vide : service WFS de l'IGN, sinon commune")
    p.add_argument("--seuil-faible-population", type=int, default=20,
                   help="carreaux sous ce nombre d'habitants marqués faible_population (défaut : 20)")
    p.add_argument("--data", default=str(RACINE / "data"), help="dossier data/ des sources (défaut : data/ à la racine du dépôt)")
    p.add_argument("--sortie", default="", help="dossier des tables silver (défaut : silver du territoire, ou <data>/silver si --data est donné)")
    p.add_argument("--sans-carte", action="store_true", help="ne pas dessiner carte_carreaux_socio.png")
    return p.parse_args(argv)


def main(argv=None):
    # Chaque ligne s'affiche aussitôt, même quand la sortie est redirigée (run_all.py, Docker, fichier de log)
    sys.stdout.reconfigure(line_buffering=True)
    args = arguments(argv)
    DEPARTEMENTS = tuple(d.strip().zfill(2) for d in args.departements.split(",") if d.strip())
    COMMUNES = [c.strip().zfill(5) for c in args.communes.split(",") if c.strip()]
    SEUIL_FAIBLE_POPULATION = args.seuil_faible_population
    LIEN_CONTOURS_IRIS = args.contours_iris
    CARTE = not args.sans_carte
    DOSSIER = os.path.join(args.data, "bronze", "insee")
    SORTIE = args.sortie or (str(T.SILVER) if args.data == str(RACINE / "data") else os.path.join(args.data, "silver"))
    os.makedirs(SORTIE, exist_ok=True)
    # --- Liens INSEE (ne pas modifier) ------------------------------------------------
    LIENS = {
        "filosofi": "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip",
        "logement_2023": "https://www.insee.fr/fr/statistiques/fichier/9061333/base_ic_logement_2023_xlsx.zip",
        "logement_2022": "https://www.insee.fr/fr/statistiques/fichier/8647012/base-ic-logement-2022_csv.zip",
        "bpe_2025": "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet",
        "emplois_2023": "https://www.insee.fr/fr/statistiques/fichier/9002680/base_cc_emploi_pop_active_2023_xlsx.zip",
        "emplois_2022": "https://www.insee.fr/fr/statistiques/fichier/8581444/base-cc-emploi-pop-active-2022_csv.zip",
        "pop_legales_2023": "https://www.insee.fr/fr/statistiques/fichier/8680726/ensemble.zip",
    }


    os.makedirs(DOSSIER, exist_ok=True)
    AUJOURDHUI = datetime.date.today().isoformat()
    SOURCES = []   # rempli au fil des étapes, écrit dans SOURCES.md à la fin

    def telecharger(url, nom, dossier=DOSSIER):
        """Télécharge une fois (les relances réutilisent le fichier)."""
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, nom)
        if os.path.exists(chemin) and os.path.getsize(chemin) > 0:
            return chemin
        print(f"   téléchargement de {url.split('/')[-1]} …")
        if url.startswith(("http://", "https://")):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            partiel = f"{chemin}.{os.getpid()}.part"   # propre au processus : deux lancements simultanés ne se gênent pas
            # Le serveur INSEE coupe parfois la connexion (IncompleteRead) : on recommence avant d'abandonner.
            for essai in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=600) as r, open(partiel, "wb") as f:
                        total, recu, palier = int(r.headers.get("Content-Length") or 0), 0, 0
                        while bloc := r.read(1 << 20):
                            f.write(bloc)
                            recu += len(bloc)
                            if recu >= palier + 20e6:   # un point d'avancement tous les 20 Mo
                                palier = recu
                                print(f"      {recu / 1e6:,.0f} Mo" + (f" / {total / 1e6:,.0f} Mo" if total else ""))
                    if total and recu != total:
                        raise http.client.IncompleteRead(b"", total - recu)
                    break
                except (http.client.HTTPException, urllib.error.URLError, OSError) as e:
                    if os.path.exists(partiel):
                        os.remove(partiel)
                    if essai == 3:
                        raise
                    print(f"   téléchargement interrompu ({type(e).__name__}), nouvel essai dans {5 * 2 ** essai} s")
                    time.sleep(5 * 2 ** essai)
            os.replace(partiel, chemin)
        else:
            shutil.copy(url.replace("file://", ""), chemin)
        print(f"   {os.path.getsize(chemin) / 1e6:,.0f} Mo")
        return chemin

    def extraire(archive):
        """Décompresse un zip ou un 7z et renvoie la liste des fichiers."""
        cible = os.path.join(DOSSIER, os.path.basename(archive) + "_x")   # toujours dans bronze/insee/
        if not os.path.isdir(cible):
            os.makedirs(cible)
            if archive.lower().endswith(".7z"):
                import py7zr
                with py7zr.SevenZipFile(archive) as z:
                    z.extractall(cible)
            else:
                with zipfile.ZipFile(archive) as z:
                    z.extractall(cible)
        return [p for p in glob.glob(f"{cible}/**/*", recursive=True) if os.path.isfile(p)]

    def plus_gros(fichiers, extensions):
        candidats = [f for f in fichiers if f.lower().endswith(extensions)]
        return max(candidats, key=os.path.getsize) if candidats else None

    def separateur(chemin):
        with open(chemin, encoding="utf-8", errors="ignore") as f:
            ligne = f.readline()
        return ";" if ligne.count(";") > ligne.count(",") else ","

    def nombre(serie):
        return pd.to_numeric(serie.astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce")

    def dans_territoire(codes):
        codes = codes.astype(str).str.zfill(5)
        return codes.isin(COMMUNES) if COMMUNES else codes.str.startswith(DEPARTEMENTS)

    def lire_base_insee(chemin, identifiants, motifs):
        """Lit une base INSEE « une colonne par variable » (CSV ou Excel).
        Garde les colonnes d'identifiant et celles qui correspondent aux motifs.
        Renvoie None si le fichier n'a pas ce format."""
        garder = lambda c: str(c) in identifiants or any(re.fullmatch(m, str(c)) for m in motifs)
        if chemin.lower().endswith(".csv"):
            df = pd.read_csv(chemin, sep=separateur(chemin), usecols=garder, dtype=str)
            return df if any(re.fullmatch(m, c) for m in motifs for c in df.columns) else None
        meilleur = None
        for feuille in pd.ExcelFile(chemin, engine="calamine").sheet_names:
            tete = pd.read_excel(chemin, sheet_name=feuille, header=None, nrows=15, dtype=str, engine="calamine")
            lignes = [i for i, l in tete.iterrows() if set(l.astype(str)) & set(identifiants)]
            if not lignes:
                continue
            entete = [str(c) for c in tete.iloc[lignes[0]]]
            if not any(re.fullmatch(m, c) for m in motifs for c in entete):
                continue
            df = pd.read_excel(chemin, sheet_name=feuille, header=lignes[0], dtype=str, engine="calamine")
            df = df[[c for c in df.columns if garder(c)]]
            if meilleur is None or len(df) > len(meilleur):
                meilleur = df
        return meilleur

    def colonne(df, *motifs):
        for m in motifs:
            for c in df.columns:
                if re.fullmatch(m, str(c), flags=re.IGNORECASE):
                    return c
        return None

    print("Territoire :", COMMUNES or DEPARTEMENTS)

    print("1/7 Filosofi 2021")
    # Même fichier et même dossier que optim/build_projections.py (rôle 4) : téléchargé une seule fois pour les deux
    archive = telecharger(LIENS["filosofi"], "Filosofi2021_carreaux_200m_csv.zip",
                          dossier=os.path.join(args.data, "bronze", "filosofi"))
    fichiers = extraire(archive)
    csv = max([f for f in fichiers if f.lower().endswith(".csv") and "met" in os.path.basename(f).lower()]
              or [f for f in fichiers if f.lower().endswith(".csv")], key=os.path.getsize)
    print("   fichier :", os.path.basename(csv))

    AGES = ["ind_0_3", "ind_4_5", "ind_6_10", "ind_11_17", "ind_18_24",
            "ind_25_39", "ind_40_54", "ind_55_64", "ind_65_79", "ind_80p"]
    UTILES = ["idcar_200m", "lcog_geo", "ind", "men", "ind_snv", "i_est_200"] + AGES

    def lire_codes(texte):
        """Codes communes d'un lcog_geo, quel que soit le séparateur : "69381", "6938169382", "69381,69382"…
        Un morceau qui n'est pas fait de codes de 5 caractères (ex. "26,09") est ignoré.
        Les codes corses (2A004, 2B033) gardent leur lettre."""
        codes = []
        for morceau in re.split(r"[^0-9AB]+", texte.upper()):
            if morceau and len(morceau) % 5 == 0:
                codes += [morceau[i:i + 5] for i in range(0, len(morceau), 5)]
        return codes

    morceaux, rejets = [], 0
    for m in pd.read_csv(csv, sep=separateur(csv), usecols=lambda c: c.lower() in UTILES, dtype=str, chunksize=400_000):
        m.columns = m.columns.str.lower()
        codes = m["lcog_geo"].fillna("").str.strip()
        blocs = codes.map(lire_codes)                      # liste des codes communes de 5 chiffres
        valide = blocs.map(len) > 0
        rejets += int((~valide & codes.str.startswith(DEPARTEMENTS)).sum())
        m, blocs = m[valide], blocs[valide]
        ok = blocs.map(lambda b: any((c in COMMUNES) if COMMUNES else c.startswith(DEPARTEMENTS) for c in b))
        m = m[ok].copy()
        m["code_commune"] = blocs[ok].map(lambda b: next(c for c in b if ((c in COMMUNES) if COMMUNES else c.startswith(DEPARTEMENTS))))
        m["multi_communes"] = blocs[ok].map(len) > 1
        morceaux.append(m)
    filo = pd.concat(morceaux, ignore_index=True)

    for c in [c for c in filo.columns if c.startswith(("ind", "men", "i_est"))]:
        filo[c] = nombre(filo[c])
    filo = filo.rename(columns={"idcar_200m": "id_carreau", "ind": "population", "men": "menages"})
    filo["revenu"] = np.where(filo["population"] > 0, filo["ind_snv"] / filo["population"], np.nan)
    filo["donnee_masquee"] = filo["i_est_200"] == 1

    ages = [a for a in AGES if a in filo.columns]
    def part(cols):
        cols = [c for c in cols if c in ages]
        return filo[cols].sum(axis=1) / filo["population"] if cols else np.nan
    filo["part_moins_18"] = part(["ind_0_3", "ind_4_5", "ind_6_10", "ind_11_17"])
    filo["part_18_39"] = part(["ind_18_24", "ind_25_39"])
    filo["part_40_64"] = part(["ind_40_54", "ind_55_64"])
    filo["part_65_plus"] = part(["ind_65_79", "ind_80p"])

    xy = filo["id_carreau"].str.extract(r"N(\d+)E(\d+)").astype(float)
    if xy.isna().any().any():
        raise ValueError("Identifiants de carreaux inattendus : " + str(filo.loc[xy.isna().any(axis=1), "id_carreau"].head(3).tolist()))
    carreaux = gpd.GeoDataFrame(filo, geometry=shapely.box(xy[1], xy[0], xy[1] + 200, xy[0] + 200), crs="EPSG:3035").to_crs("EPSG:2154")

    SOURCES.append(("Population, ménages, revenus, âges", "Filosofi 2021, carreaux 200 m (CSV)", LIENS["filosofi"]))
    print(f"   {len(carreaux):,} carreaux · {carreaux['population'].sum():,.0f} habitants · {carreaux['code_commune'].nunique():,} communes")
    print(f"   tranches d'âge trouvées : {len(ages)}/10")
    print(f"   lignes écartées car code commune illisible : {rejets}")

    print("2/7 Ménages sans voiture")
    MOTIFS_LOG = [r"P\d\d_RP", r"P\d\d_RP_VOIT1P"]
    log, version_log = None, None
    for version, cle in [("2023", "logement_2023"), ("2022", "logement_2022")]:
        try:
            archive = telecharger(LIENS[cle], f"{cle}.zip")
            fichier = plus_gros(extraire(archive), (".csv", ".xlsx"))
            log = lire_base_insee(fichier, {"IRIS", "COM", "CODGEO", "GEO"}, MOTIFS_LOG)
        except Exception as e:
            print(f"   base {version} illisible : {e}")
            log = None
        if log is not None and colonne(log, MOTIFS_LOG[0]) and colonne(log, MOTIFS_LOG[1]):
            version_log = version
            break
        print(f"   base {version} : pas au format attendu, on essaie la suivante")
    if version_log is None:
        raise RuntimeError("Aucune base Logement lisible : envoie une capture de cette erreur.")

    col_iris = colonne(log, r"IRIS", r"CODGEO", r"GEO")
    col_rp, col_voit = colonne(log, MOTIFS_LOG[0]), colonne(log, MOTIFS_LOG[1])
    log["code_iris"] = log[col_iris].astype(str).str.zfill(9)
    log["code_commune"] = log["code_iris"].str[:5]
    log = log[dans_territoire(log["code_commune"])].copy()
    log["rp"], log["rp_voit"] = nombre(log[col_rp]), nombre(log[col_voit])

    def sans_voiture(rp, voit):
        return np.where(rp > 0, np.clip(1 - voit / rp, 0, 1), np.nan)
    log["part"] = sans_voiture(log["rp"], log["rp_voit"])
    par_commune = log.groupby("code_commune")[["rp", "rp_voit"]].sum()
    par_commune["part"] = sans_voiture(par_commune["rp"], par_commune["rp_voit"])

    SOURCES.append(("Ménages sans voiture", f"Recensement {version_log}, Logement, base à l'IRIS ({col_rp}, {col_voit})",
                    LIENS[f"logement_{version_log}"]))
    print(f"   version utilisée : {version_log} · {len(log):,} IRIS · variables {col_rp} et {col_voit}")
    print(f"   part sans voiture sur le territoire : {1 - log['rp_voit'].sum() / log['rp'].sum():.1%}")

    print("3/7 Rattachement aux carreaux")
    carreaux["code_iris"] = None
    iris, source_iris = None, None

    def iris_par_service_ign():
        """Contours IRIS via le service web (WFS) de l'IGN, limités à l'emprise du territoire."""
        import json, time, xml.etree.ElementTree as ET
        base = "https://data.geopf.fr/wfs/ows"

        def ouvrir(u):
            """Le service IGN renvoie parfois une erreur 500 passagère : on réessaie avant d'abandonner."""
            for essai in range(4):
                try:
                    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read()
                except Exception as e:
                    if essai == 3:
                        raise
                    print(f"   service IGN indisponible ({e}), nouvel essai dans {5 * 2 ** essai} s")
                    time.sleep(5 * 2 ** essai)
        caps = ET.fromstring(ouvrir(base + "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities"))
        noms = [e.text for e in caps.iter() if e.tag.endswith("}Name") and e.text and e.text.upper().startswith("STATISTICALUNITS.IRIS:")]
        if not noms:
            raise RuntimeError("couche IRIS introuvable dans le service IGN")
        couche = sorted(noms, key=lambda n: ("contours" not in n.lower(), n))[0]
        xmin, ymin, xmax, ymax = carreaux.total_bounds
        morceaux, debut = [], 0
        while True:
            url = (f"{base}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES={couche}"
                   f"&SRSNAME=EPSG:2154&BBOX={xmin},{ymin},{xmax},{ymax},urn:ogc:def:crs:EPSG::2154"
                   f"&OUTPUTFORMAT=application/json&COUNT=5000&STARTINDEX={debut}")
            page = json.loads(ouvrir(url))
            if not page.get("features"):
                break
            morceaux.append(gpd.GeoDataFrame.from_features(page["features"], crs="EPSG:2154"))
            print(f"   service IGN : {debut + len(page['features']):,} IRIS reçus…")
            if len(page["features"]) < 5000:
                break
            debut += 5000
        return pd.concat(morceaux, ignore_index=True), f"Service WFS IGN, couche {couche}", base

    try:
        if LIEN_CONTOURS_IRIS.strip() and os.path.isfile(LIEN_CONTOURS_IRIS.strip()) \
                and LIEN_CONTOURS_IRIS.strip().lower().endswith((".gpkg", ".shp", ".parquet")):
            geo = LIEN_CONTOURS_IRIS.strip()  # fichier local déjà prêt (ex. data/bronze/ign/iris_wfs_france.gpkg)
            iris = gpd.read_parquet(geo) if geo.lower().endswith(".parquet") else gpd.read_file(geo, engine="pyogrio")
            source_iris, lien_iris = os.path.basename(geo), geo
        elif LIEN_CONTOURS_IRIS.strip():
            nom = "contours_iris" + (".7z" if ".7z" in LIEN_CONTOURS_IRIS.lower() else ".zip")
            geo = plus_gros(extraire(telecharger(LIEN_CONTOURS_IRIS.strip(), nom)), (".gpkg", ".shp"))
            iris, source_iris, lien_iris = gpd.read_file(geo, engine="pyogrio"), os.path.basename(geo), LIEN_CONTOURS_IRIS.strip()
        else:
            print("   pas de lien : on demande les contours IRIS au service web de l'IGN")
            iris, source_iris, lien_iris = iris_par_service_ign()
    except Exception as e:
        print(f"   contours IRIS indisponibles ({str(e)[:120]})")
        iris = None

    if iris is not None and len(iris):
        col_code = colonne(iris, r"CODE_IRIS", r"code_iris", r"iris_code", r"IRIS")
        iris["code_iris"] = iris[col_code].astype(str).str.zfill(9)
        iris = iris[dans_territoire(iris["code_iris"].str[:5])].to_crs("EPSG:2154")
        centres = gpd.GeoDataFrame(carreaux[["id_carreau"]], geometry=carreaux.geometry.centroid, crs="EPSG:2154")
        lien = gpd.sjoin(centres, iris[["code_iris", "geometry"]], predicate="within", how="left")
        lien = lien.drop_duplicates("id_carreau").set_index("id_carreau")["code_iris"]
        carreaux["code_iris"] = carreaux["id_carreau"].map(lien)
        SOURCES.append(("Contours des IRIS", source_iris, lien_iris))
        print(f"   {len(iris):,} IRIS · {carreaux['code_iris'].notna().mean():.1%} des carreaux reliés à un IRIS")
    else:
        print("   on utilise la valeur de la commune pour tous les carreaux (moins précis)")

    carreaux["part_menages_sans_voiture"] = carreaux["code_iris"].map(log.set_index("code_iris")["part"])
    carreaux["source_part_voiture"] = np.where(carreaux["part_menages_sans_voiture"].notna(), "iris", None)
    manque = carreaux["part_menages_sans_voiture"].isna()
    carreaux.loc[manque, "part_menages_sans_voiture"] = carreaux.loc[manque, "code_commune"].map(par_commune["part"])
    carreaux.loc[manque & carreaux["part_menages_sans_voiture"].notna(), "source_part_voiture"] = "commune"
    print(carreaux["source_part_voiture"].value_counts(dropna=False).to_string())

    print("4/7 Équipements")
    import pyarrow.parquet as pq
    DOMAINES = {"A": "services", "B": "commerces", "C": "enseignement", "D": "sante",
                "E": "transports", "F": "sports_culture", "G": "tourisme"}
    chemin = telecharger(LIENS["bpe_2025"], "bpe_2025.parquet")
    schema = pq.read_schema(chemin).names
    voulues = {n.upper(): n for n in schema}
    cols = [voulues[c] for c in ["DEPCOM", "DEP", "TYPEQU", "LAMBERT_X", "LAMBERT_Y", "QUALITE_XY", "QUALITE_GEOLOC"] if c in voulues]
    bpe = pq.read_table(chemin, columns=cols).to_pandas()
    bpe.columns = [c.upper() for c in bpe.columns]
    bpe = bpe[dans_territoire(bpe["DEPCOM"])].copy()
    bpe["x"], bpe["y"] = nombre(bpe["LAMBERT_X"]), nombre(bpe["LAMBERT_Y"])
    qualite = next((c for c in ["QUALITE_XY", "QUALITE_GEOLOC"] if c in bpe.columns), None)
    if qualite:
        print("   qualité de géolocalisation :", bpe[qualite].value_counts(dropna=False).to_dict())
        # BPE 2025 : QUALITE_XY vaut A (bonne), B (acceptable), M (mauvaise), _U, _Z ; les éditions plus
        # anciennes écrivaient le libellé en toutes lettres
        mauvaise = (bpe[qualite].astype(str).str.upper().eq("M")
                    | bpe[qualite].astype(str).str.lower().str.contains("mauvais|non g", regex=True))
        print(f"   {mauvaise.sum():,} équipements mal géolocalisés écartés")
        bpe = bpe[~mauvaise]
    sans_xy = bpe[["x", "y"]].isna().any(axis=1)
    print(f"   {len(bpe):,} équipements dans le territoire, {sans_xy.sum():,} sans coordonnées (écartés)")
    bpe = bpe[~sans_xy]
    equipements = gpd.GeoDataFrame(
        {"code_commune": bpe["DEPCOM"].astype(str).str.zfill(5).values, "typequ": bpe["TYPEQU"].values,
         "domaine": bpe["TYPEQU"].astype(str).str[0].map(DOMAINES).values},
        geometry=gpd.points_from_xy(bpe["x"], bpe["y"]), crs="EPSG:2154")

    lien = gpd.sjoin(equipements, carreaux[["id_carreau", "geometry"]], predicate="within", how="inner")
    compte = lien.pivot_table(index="id_carreau", columns="domaine", values="typequ", aggfunc="count", fill_value=0)
    for d in DOMAINES.values():
        carreaux[f"nb_equip_{d}"] = carreaux["id_carreau"].map(compte[d]).fillna(0).astype(int) if d in compte else 0
    carreaux["nb_equipements"] = carreaux[[f"nb_equip_{d}" for d in ["services", "commerces", "enseignement", "sante"]]].sum(axis=1)
    carreaux["nb_equipements_tous"] = carreaux[[f"nb_equip_{d}" for d in DOMAINES.values()]].sum(axis=1)

    SOURCES.append(("Équipements", "BPE 2025, fichier-détail géolocalisé (Parquet)", LIENS["bpe_2025"]))
    print(f"   {len(lien):,} équipements dans un carreau habité · {(carreaux['nb_equipements'] > 0).mean():.0%} des carreaux en ont au moins un")

    print("5/7 Emplois")
    emp, version_emp = None, None
    for version, cle in [("2023", "emplois_2023"), ("2022", "emplois_2022")]:
        try:
            archive = telecharger(LIENS[cle], f"{cle}.zip")
            fichier = plus_gros(extraire(archive), (".csv", ".xlsx"))
            emp = lire_base_insee(fichier, {"CODGEO", "COM", "GEO"}, [r"P\d\d_EMPLT"])
        except Exception as e:
            print(f"   base {version} illisible : {e}")
            emp = None
        if emp is not None and colonne(emp, r"P\d\d_EMPLT"):
            version_emp = version
            break
        print(f"   base {version} : pas au format attendu, on essaie la suivante")
    if version_emp is None:
        raise RuntimeError("Aucune base Emplois lisible : envoie une capture de cette erreur.")

    col_geo, col_emplt = colonne(emp, r"CODGEO", r"COM", r"GEO"), colonne(emp, r"P\d\d_EMPLT")
    emp["code_commune"] = emp[col_geo].astype(str).str.zfill(5)
    emplois_commune = nombre(emp.set_index("code_commune")[col_emplt]).groupby(level=0).sum()
    emplois_commune = emplois_commune[dans_territoire(pd.Series(emplois_commune.index, index=emplois_commune.index))]
    # La base donne Lyon, Paris et Marseille en commune entière ET par arrondissement : on garde les arrondissements
    # (comme Filosofi et les IRIS), sinon le total du territoire compte ces villes deux fois
    emplois_commune = emplois_commune.drop(["69123", "75056", "13055"], errors="ignore")

    # Commune actuelle : celle de l'IRIS 2025 qui contient le centre du carreau, si l'INSEE la connaît.
    # Elle corrige les communes fusionnées depuis 2021 (code Filosofi ancien, emplois au code nouveau).
    com_iris = carreaux["code_iris"].astype("string").str[:5]
    carreaux["code_commune_actuelle"] = np.where(com_iris.isin(emplois_commune.index), com_iris, carreaux["code_commune"])
    fusions = (carreaux["code_commune_actuelle"] != carreaux["code_commune"]).sum()
    print(f"   {fusions:,} carreaux rattachés à leur commune actuelle (fusions depuis 2021, limites communales)")

    poids = carreaux["nb_equipements_tous"].astype(float)
    groupe = carreaux["code_commune_actuelle"]
    total = poids.groupby(groupe).transform("sum")
    poids = poids.where(total > 0, carreaux["population"])
    carreaux["emplois"] = groupe.map(emplois_commune) * poids / poids.groupby(groupe).transform("sum")
    print(f"   carreaux sans emplois : {carreaux['emplois'].isna().sum():,}")

    SOURCES.append(("Emplois au lieu de travail", f"Recensement {version_emp}, base communale ({col_emplt})", LIENS[f"emplois_{version_emp}"]))
    print(f"   version utilisée : {version_emp} · variable {col_emplt} · {emplois_commune.sum():,.0f} emplois dans le territoire")

    print("6/7 Assemblage")
    carreaux["faible_population"] = carreaux["population"] < SEUIL_FAIBLE_POPULATION
    centres = carreaux.geometry.centroid
    carreaux["x_centre"], carreaux["y_centre"] = centres.x.round(1), centres.y.round(1)
    wgs = centres.to_crs("EPSG:4326")
    carreaux["lon"], carreaux["lat"] = wgs.x.round(6), wgs.y.round(6)

    COLONNES = ["id_carreau", "code_commune", "code_commune_actuelle", "code_iris", "multi_communes", "population", "menages", "revenu",
                "part_moins_18", "part_18_39", "part_40_64", "part_65_plus",
                "part_menages_sans_voiture", "source_part_voiture",
                "nb_equipements", "nb_equipements_tous"] + [f"nb_equip_{d}" for d in DOMAINES.values()] + [
                "emplois", "donnee_masquee", "faible_population", "x_centre", "y_centre", "lon", "lat", "geometry"]
    carreaux_socio = carreaux[COLONNES].drop_duplicates("id_carreau").copy()
    for c in ["part_moins_18", "part_18_39", "part_40_64", "part_65_plus", "part_menages_sans_voiture"]:
        carreaux_socio[c] = carreaux_socio[c].round(4)
    carreaux_socio["revenu"] = carreaux_socio["revenu"].round(0)
    carreaux_socio["emplois"] = carreaux_socio["emplois"].round(1)
    print(f"   {len(carreaux_socio):,} lignes · {len(COLONNES)} colonnes")

    print("7/7 Contrôles")
    def controle(nom, ok, detail=""):
        print(("✅" if ok else "❌"), nom, detail)
        return ok

    emplois_attendus = emplois_commune[emplois_commune.index.isin(carreaux_socio["code_commune_actuelle"])].sum()
    tests = [
        controle("Identifiants uniques", carreaux_socio["id_carreau"].is_unique),
        controle("Population > 0 partout", (carreaux_socio["population"] > 0).all()),
        controle("Part sans voiture entre 0 et 1", carreaux_socio["part_menages_sans_voiture"].dropna().between(0, 1).all()),
        controle("Parts d'âge entre 0 et 1", carreaux_socio[["part_moins_18", "part_18_39", "part_40_64", "part_65_plus"]].stack().between(0, 1.0001).all()),
        controle("Tous les carreaux dans le territoire", dans_territoire(carreaux_socio["code_commune"]).all()),
        controle("Emplois répartis = total INSEE", abs(carreaux_socio["emplois"].sum() - emplois_attendus) <= 0.001 * emplois_attendus + 1,
                 f"({carreaux_socio['emplois'].sum():,.0f} / {emplois_attendus:,.0f})"),
        controle("Projection Lambert 93", carreaux_socio.crs.to_epsg() == 2154),
    ]
    print()
    print(f"Population totale : {carreaux_socio['population'].sum():,.0f} habitants")
    print(f"Revenu moyen      : {carreaux['ind_snv'].sum() / carreaux['population'].sum():,.0f} €/an")
    print(f"Données masquées  : {carreaux_socio['donnee_masquee'].mean():.1%} des carreaux")
    print(f"Faible population : {carreaux_socio['faible_population'].mean():.1%} des carreaux (< {SEUIL_FAIBLE_POPULATION} hab.)")
    print()
    print("Valeurs manquantes :")
    manq = carreaux_socio.drop(columns="geometry").isna().sum().loc[lambda s: s > 0]
    print(manq.to_string() if len(manq) else "   aucune")
    print()
    print("Population par département :")
    print(carreaux_socio.groupby(carreaux_socio["code_commune"].str[:2])["population"].sum().round(0).to_string())
    print()
    print("TABLE LIVRABLE ✅" if all(tests) else "À VÉRIFIER ❌ : envoie une capture")

    if CARTE:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm
        fig, axes = plt.subplots(1, 3, figsize=(21, 7), constrained_layout=True)
        cartes = [("population", "Population par carreau (échelle log)", "viridis", LogNorm(vmin=1, vmax=carreaux_socio["population"].quantile(0.999))),
                  ("part_menages_sans_voiture", "Part des ménages sans voiture", "magma_r", None),
                  ("nb_equipements", "Équipements par carreau (échelle log)", "cividis", LogNorm(vmin=1, vmax=max(2, carreaux_socio["nb_equipements"].quantile(0.999))))]
        for ax, (col, titre, cmap, norme) in zip(axes, cartes):
            donnees = carreaux_socio[carreaux_socio[col] > 0] if norme is not None else carreaux_socio
            donnees.plot(column=col, cmap=cmap, norm=norme, legend=True, ax=ax, legend_kwds={"shrink": 0.7})
            ax.set_title(titre)
            ax.set_aspect("equal")
            ax.set_axis_off()
        fig.savefig(os.path.join(SORTIE, "carte_carreaux_socio.png"), dpi=110)
        plt.close(fig)
        print("   carte :", os.path.join(SORTIE, "carte_carreaux_socio.png"))

    print("Contrôle externe")
    fichiers = extraire(telecharger(LIENS["pop_legales_2023"], "pop_legales_2023.zip"))
    csvs = [f for f in fichiers if f.lower().endswith(".csv")]
    f_pop = next((f for f in csvs if os.path.basename(f).lower() == "donnees_communes.csv"),
                 min([f for f in csvs if "commun" in os.path.basename(f).lower()], key=lambda f: len(os.path.basename(f))))
    print("   fichier :", os.path.basename(f_pop))
    pop = pd.read_csv(f_pop, sep=separateur(f_pop), dtype=str)
    pop["code_commune"] = pop[colonne(pop, r"DEP")].str.zfill(2).str[:2] + pop[colonne(pop, r"COM")].str.zfill(3).str[-3:]
    pop["pop_legale"] = nombre(pop[colonne(pop, r"PMUN")].str.replace(" ", ""))
    pop = pop[dans_territoire(pop["code_commune"])]

    # Paris, Lyon et Marseille sont en arrondissements dans Filosofi et, selon l'édition, en arrondissements ou en
    # commune entière dans les populations légales : on regroupe les deux côtés en commune entière pour comparer
    def commune_entiere(code):
        if code.startswith("6938"): return "69123"
        if code.startswith("751"): return "75056"
        if code.startswith("132") and int(code[3:]) >= 1 and code[:3] == "132" and code != "13200": return "13055"
        return code
    pop["code_commune"] = pop["code_commune"].map(commune_entiere)
    col_nom = colonne(pop, r"Commune", r"LIBGEO", r"NOM.*")
    pop[col_nom] = pop[col_nom].where(pop["code_commune"] != "69123", "Lyon")
    nom_commune = pop.groupby("code_commune")[col_nom].first()
    pop = pop.groupby("code_commune", as_index=False)["pop_legale"].sum()
    filo_com = carreaux_socio.groupby(carreaux_socio["code_commune_actuelle"].map(commune_entiere))["population"].sum()
    comp = pop.set_index("code_commune")[["pop_legale"]].join(filo_com.rename("pop_filosofi"), how="inner")
    non_comparees = carreaux_socio.loc[~carreaux_socio["code_commune_actuelle"].map(commune_entiere).isin(comp.index), "population"].sum()
    print(f"   population Filosofi sans commune correspondante (fusions de communes) : {non_comparees:,.0f}")
    comp["ratio"] = comp["pop_filosofi"] / comp["pop_legale"]
    ratio_global = comp["pop_filosofi"].sum() / comp["pop_legale"].sum()
    print(f"   territoire : {comp['pop_filosofi'].sum():,.0f} (Filosofi) / {comp['pop_legale'].sum():,.0f} (légale) = {ratio_global:.1%}")

    dep = comp.groupby(comp.index.str[:2])[["pop_filosofi", "pop_legale"]].sum()
    dep["ratio"] = (dep["pop_filosofi"] / dep["pop_legale"]).round(3)
    print("\nPar département :")
    print(dep.to_string())

    grandes = comp[comp["pop_legale"] >= 5000].copy()
    grandes["commune"] = grandes.index.map(nom_commune)
    grandes["ecart"] = (grandes["ratio"] - grandes["ratio"].median()).abs()
    print(f"\nCommunes de 5 000 habitants et plus : {len(grandes)} · ratio médian {grandes['ratio'].median():.1%}")
    print("Les 10 plus gros écarts (à expliquer à l'oral) :")
    print(grandes.sort_values("ecart", ascending=False).head(10)[["commune", "pop_legale", "pop_filosofi", "ratio"]].round(3).to_string())

    masques = carreaux_socio["donnee_masquee"]
    print(f"\nCarreaux estimés par l'INSEE (moins de 11 ménages) : {masques.mean():.1%} des carreaux, "
          f"{carreaux_socio.loc[masques, 'population'].sum() / carreaux_socio['population'].sum():.1%} de la population")
    tests.append(controle("Population cohérente avec le recensement (88 à 98 %)", 0.88 <= ratio_global <= 0.98, f"({ratio_global:.1%})"))
    SOURCES.append(("Contrôle de cohérence", f"Populations de référence 2023 · Filosofi = {ratio_global:.1%} de la population légale",
                    LIENS["pop_legales_2023"]))

    carreaux_socio.to_parquet(f"{SORTIE}/carreaux_socio.parquet")
    carreaux_socio.drop(columns="geometry").to_csv(f"{SORTIE}/carreaux_socio.csv", index=False)
    equipements.to_parquet(f"{SORTIE}/equipements.parquet")

    DESCRIPTION = {
        "id_carreau": "Identifiant INSEE du carreau de 200 m (Filosofi 2021)",
        "code_commune": "Code INSEE de la commune dans Filosofi 2021 (la première du territoire si le carreau en touche plusieurs)",
        "code_commune_actuelle": "Commune actuelle : celle de l'IRIS 2025 contenant le centre du carreau (corrige les fusions depuis 2021), sinon code_commune",
        "code_iris": "Code de l'IRIS contenant le centre du carreau (vide sans contours IRIS)",
        "multi_communes": "Vrai si le carreau touche plusieurs communes",
        "population": "Habitants (ménages fiscaux, Filosofi 2021)",
        "menages": "Ménages (Filosofi 2021)",
        "revenu": "Niveau de vie moyen par habitant, €/an (ind_snv / ind)",
        "part_moins_18": "Part des moins de 18 ans", "part_18_39": "Part des 18-39 ans",
        "part_40_64": "Part des 40-64 ans", "part_65_plus": "Part des 65 ans et plus",
        "part_menages_sans_voiture": f"Part des résidences principales sans voiture (Recensement {version_log})",
        "source_part_voiture": "Niveau de la part sans voiture : iris ou commune",
        "nb_equipements": "Équipements BPE 2025 : services + commerces + enseignement + santé",
        "nb_equipements_tous": "Tous les équipements BPE 2025 du carreau",
        "emplois": f"HYPOTHÈSE : emplois de la commune actuelle (Recensement {version_emp}) répartis au prorata des équipements, sinon de la population",
        "donnee_masquee": "Vrai si la valeur Filosofi est imputée (secret statistique)",
        "faible_population": f"Vrai si moins de {SEUIL_FAIBLE_POPULATION} habitants : à regrouper (O1, vie privée)",
        "x_centre": "Centre du carreau, Lambert 93 (m)", "y_centre": "Centre du carreau, Lambert 93 (m)",
        "lon": "Centre du carreau, longitude WGS84", "lat": "Centre du carreau, latitude WGS84",
        "geometry": "Carreau de 200 m, Lambert 93 (EPSG:2154)",
    }
    manquants = carreaux_socio.drop(columns="geometry").isna().sum()
    lignes = ["| Colonne | Type | Manquants | Description |", "|---|---|---|---|"]
    for c in carreaux_socio.columns:
        desc = DESCRIPTION.get(c) or ("Équipements BPE 2025 du domaine " + c.replace("nb_equip_", "") if c.startswith("nb_equip_") else "")
        lignes.append(f"| `{c}` | {carreaux_socio[c].dtype} | {manquants.get(c, 0)} | {desc} |")
    territoire = ", ".join(COMMUNES) if COMMUNES else "départements " + ", ".join(DEPARTEMENTS)
    with open(f"{SORTIE}/dictionnaire_carreaux_socio.md", "w", encoding="utf-8") as f:
        f.write(f"# carreaux_socio\n\nTerritoire : {territoire}. {len(carreaux_socio):,} carreaux habités, "
                f"{carreaux_socio['population'].sum():,.0f} habitants. Généré le {AUJOURDHUI}.\n\n" + "\n".join(lignes) + "\n\n"
                "## Hypothèses\n"
                "1. Carreau sur plusieurs communes : rattaché à la première commune du territoire de `lcog_geo`.\n"
                "2. Part sans voiture : celle de l'IRIS contenant le centre du carreau, sinon celle de la commune.\n"
                "3. Emplois : répartis au prorata des équipements du carreau (sinon de la population).\n"
                "4. Filosofi compte les ménages fiscaux : population en communauté non incluse.\n")
    with open(f"{SORTIE}/SOURCES_population.md", "w", encoding="utf-8") as f:
        f.write("# Sources · population et profil (rôle 3)\n\n| Donnée | Version | Lien | Téléchargé le |\n|---|---|---|---|\n")
        for donnee, version, lien in SOURCES:
            f.write(f"| {donnee} | {version} | {lien} | {AUJOURDHUI} |\n")

    print("Fichiers écrits dans", SORTIE, ":", sorted(f for f in os.listdir(SORTIE) if "carreaux_socio" in f or f.startswith(("equipements", "SOURCES_population", "dictionnaire_carreaux"))))

    return 0 if all(tests) else 1


if __name__ == "__main__":
    sys.exit(main())