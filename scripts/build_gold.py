"""Rôle 1 (Data engineer) : construit les tables gold `gold_offre` et `gold_candidates`.

gold_offre      : un point par arrêt ou station.
                  Colonnes : offre_id, source, type, nom, operateur, valeur, unite, geometry (Lambert 93)
                  - arrêts (silver/arrets)              -> type = bus, tram, metro...  valeur = passages/jour
                  - stations (silver/stations_partagees) -> type = velo, trottinette, citiz...  valeur = capacité
gold_candidates : centroïdes des carreaux peuplés (silver/carreaux_socio), avec id_carreau.

Chaque table d'entrée est facultative : ce qui manque est signalé, le reste est construit.
Usage : python scripts/build_gold.py
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
CRS = "EPSG:2154"


def read_silver(name):
    path = SILVER / f"{name}.parquet"
    if not path.exists():
        print(f"[absent] {path.name} : étape ignorée")
        return None
    gdf = gpd.read_parquet(path)
    return gdf.to_crs(CRS) if gdf.crs else gdf.set_crs(CRS)


def offre_from_arrets(arrets):
    return gpd.GeoDataFrame({
        "offre_id": "arret_" + arrets["arret_id"].astype(str),
        "source": "arrets",
        "type": arrets["mode"],
        "nom": arrets["nom"],
        "operateur": arrets["reseau"],
        "valeur": arrets["nb_passages_jour"],
        "unite": "passages_par_jour",
        "geometry": arrets.geometry,
    }, crs=CRS)


def offre_from_stations(stations):
    capacite = stations["capacite"] if "capacite" in stations else pd.Series(index=stations.index, dtype="float64")
    return gpd.GeoDataFrame({
        "offre_id": "station_" + stations["station_id"].astype(str),
        "source": "stations_partagees",
        "type": stations["type"],
        "nom": stations["commune"] if "commune" in stations else pd.NA,
        "operateur": stations["operateur"],
        "valeur": capacite,
        "unite": "capacite",
        "geometry": stations.geometry,
    }, crs=CRS)


def candidates_from_carreaux(carreaux, population_min=1):
    peuples = carreaux[carreaux["population"].fillna(0) >= population_min]
    return gpd.GeoDataFrame({
        "id_carreau": peuples["id_carreau"],
        "population": peuples["population"],
        "geometry": peuples.geometry.centroid,
    }, crs=CRS).reset_index(drop=True)


def write_gold(gdf, name):
    GOLD.mkdir(parents=True, exist_ok=True)
    path = GOLD / f"{name}.parquet"
    gdf.to_parquet(path)
    print(f"Écrit : {path}  ({len(gdf)} lignes)")


def main():
    parts = []
    arrets = read_silver("arrets")
    if arrets is not None:
        parts.append(offre_from_arrets(arrets))
    stations = read_silver("stations_partagees")
    if stations is not None:
        parts.append(offre_from_stations(stations))

    if parts:
        offre = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS)
        assert offre.offre_id.is_unique, "offre_id en double"
        write_gold(offre, "gold_offre")
        print(offre.groupby(["source", "type"]).size().to_string())
    else:
        print("Aucune entrée pour gold_offre.")

    carreaux = read_silver("carreaux_socio")
    if carreaux is not None:
        candidates = candidates_from_carreaux(carreaux)
        assert candidates.id_carreau.is_unique, "id_carreau en double"
        write_gold(candidates, "gold_candidates")


if __name__ == "__main__":
    main()
