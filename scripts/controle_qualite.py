import sys
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "optim"))
import territoire as T  # noqa: E402  (territoire : TERRITOIRE=ara|france)
SILVER, GOLD = T.SILVER, T.GOLD
CRS = "EPSG:2154"
# Cadre large du territoire en Lambert 93 (x min, y min, x max, y max), en mètres
CADRE = T.CADRE

problemes = []
lignes = []


def log(texte=""):
    lignes.append(texte)
    print(texte)


def verifie(ok, message):
    log(f"[{'OK ' if ok else 'ERREUR'}] {message}")
    if not ok:
        problemes.append(message)


def normalise(nom):
    nom = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode().lower()
    return " ".join(nom.split())


def doublons_proches(gdf, rayon=50):
    """Paires (même nom normalisé, distance < rayon) : liste de (ligne i, ligne j, distance en m)."""
    noms = gdf["nom"].map(normalise).values
    xy = np.column_stack([gdf.geometry.x.values, gdf.geometry.y.values])
    paires = cKDTree(xy).query_pairs(rayon)
    return [(i, j, float(np.hypot(*(xy[i] - xy[j])))) for i, j in paires if noms[i] == noms[j]]


def controle_points(gdf, nom_table, col_id):
    log(f"\n=== {nom_table} ({len(gdf)} lignes) ===")
    verifie(gdf.crs is not None and gdf.crs.to_epsg() == 2154, "projection Lambert 93 (EPSG:2154)")
    verifie(not gdf.geometry.is_empty.any() and not gdf.geometry.isna().any(), "aucune géométrie vide")
    verifie(gdf[col_id].is_unique, f"{col_id} unique")
    x, y = gdf.geometry.x, gdf.geometry.y
    dedans = (x >= CADRE[0]) & (x <= CADRE[2]) & (y >= CADRE[1]) & (y <= CADRE[3])
    verifie(dedans.all(), f"tous les points dans le cadre {T.LIBELLE} ({(~dedans).sum()} hors cadre)")
    manquants = gdf.drop(columns="geometry").isna().sum()
    manquants = manquants[manquants > 0]
    log("valeurs manquantes : " + (", ".join(f"{c}={n}" for c, n in manquants.items()) or "aucune"))


def main():
    arrets_path = SILVER / "arrets.parquet"
    if arrets_path.exists():
        arrets = gpd.read_parquet(arrets_path)
        controle_points(arrets, "silver/arrets", "arret_id")
        dup = doublons_proches(arrets)
        for i, j, d in dup[:10]:
            a, b = arrets.iloc[i], arrets.iloc[j]
            log(f"  doublon : {a.arret_id} / {b.arret_id} | {a.nom!r} | {a.reseau} / {b.reseau} | {d:.1f} m")
        verifie(len(dup) == 0, f"aucun doublon (même nom < 50 m) : {len(dup)} trouvé(s)")
        zero = (arrets["nb_passages_jour"].fillna(0) == 0).mean()
        log(f"arrêts à 0 passage : {zero:.1%}")
        verifie(zero < 0.2, "moins de 20 % d'arrêts sans passage")
        log("modes : " + ", ".join(f"{m}={n}" for m, n in arrets["mode"].value_counts().items()))
    else:
        verifie(False, "silver/arrets.parquet introuvable")

    offre_path = GOLD / "gold_offre.parquet"
    if offre_path.exists():
        offre = gpd.read_parquet(offre_path)
        controle_points(offre, "gold/gold_offre", "offre_id")
        if arrets_path.exists():
            n_arrets = (offre.source == "arrets").sum()
            verifie(n_arrets == len(arrets), f"gold_offre contient tous les arrêts ({n_arrets} sur {len(arrets)})")
    else:
        log("\n[absent] gold/gold_offre.parquet")

    contours_path = SILVER / "contours.parquet"
    if contours_path.exists() and arrets_path.exists():
        contours = gpd.read_parquet(contours_path).to_crs(CRS)
        dedans = gpd.sjoin(arrets[["arret_id", "geometry"]], contours[["geometry"]], predicate="within")
        hors = len(arrets) - dedans.arret_id.nunique()
        verifie(hors == 0, f"tous les arrêts dans les contours du territoire ({hors} hors contours)")
    else:
        log("\n[absent] silver/contours.parquet : filtre territoire par cadre seulement (rôle 2)")

    log(f"\nRésultat : {'ÉCHEC' if problemes else 'TOUT EST OK'} ({len(problemes)} problème(s))")
    GOLD.mkdir(parents=True, exist_ok=True)
    (GOLD / "controle_qualite.txt").write_text("\n".join(lignes), encoding="utf-8")
    sys.exit(1 if problemes else 0)


if __name__ == "__main__":
    main()