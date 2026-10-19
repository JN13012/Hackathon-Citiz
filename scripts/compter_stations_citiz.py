"""Compte les stations et les véhicules des réseaux Citiz (flux GBFS) - décision D1.

Écrit data/bronze/citiz_comptage.csv et affiche un tableau par réseau.
Usage : python scripts/compter_stations_citiz.py
"""
import csv
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bronze" / "citiz_comptage.csv"
API = "https://transport.data.gouv.fr/api/datasets"
TIMEOUT = 60


def list_feeds(gbfs):
    """Retourne {nom_du_flux: url} pour GBFS 3.0 (data.feeds) et GBFS 2.x (data.<langue>.feeds)."""
    data = gbfs.get("data", {})
    feeds = data.get("feeds")
    if feeds is None:
        for value in data.values():
            if isinstance(value, dict) and "feeds" in value:
                feeds = value["feeds"]
                break
    return {f["name"]: f["url"] for f in (feeds or []) if "name" in f and "url" in f}


def count_stations(station_information):
    return len(station_information.get("data", {}).get("stations", []))


def count_vehicles(get_json, feeds):
    """Nombre de véhicules : vehicle_status si présent, sinon somme de station_status."""
    if "vehicle_status" in feeds:
        data = get_json(feeds["vehicle_status"]).get("data", {})
        return len(data.get("vehicles", data.get("bikes", [])))
    if "station_status" in feeds:
        stations = get_json(feeds["station_status"]).get("data", {}).get("stations", [])
        return sum(s.get("num_vehicles_available", s.get("num_bikes_available", 0)) or 0 for s in stations)
    return None


def get_json(url):
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def citiz_gbfs_urls():
    for d in get_json(API):
        if "citiz" not in (d.get("title") or "").lower():
            continue
        if d.get("type") not in (None, "vehicles-sharing"):
            continue
        for r in d.get("resources", []):
            url = r.get("url") or ""
            if (r.get("format") or "").lower() == "gbfs" or "gbfs" in url.lower():
                yield d.get("title"), url
                break


def main():
    rows = []
    for title, url in citiz_gbfs_urls():
        row = {"reseau": title, "stations": "", "vehicules": "", "erreur": ""}
        try:
            feeds = list_feeds(get_json(url))
            if "station_information" in feeds:
                row["stations"] = count_stations(get_json(feeds["station_information"]))
            vehicles = count_vehicles(get_json, feeds)
            row["vehicules"] = "" if vehicles is None else vehicles
        except Exception as exc:  # flux hétérogènes : on note l'erreur et on continue
            row["erreur"] = str(exc)[:120]
        rows.append(row)
        print(f"{title:55s} stations={row['stations']!s:>5}  véhicules={row['vehicules']!s:>5}  {row['erreur']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reseau", "stations", "vehicules", "erreur"])
        writer.writeheader()
        writer.writerows(rows)
    total_s = sum(int(r["stations"]) for r in rows if r["stations"] != "")
    total_v = sum(int(r["vehicules"]) for r in rows if r["vehicules"] != "")
    print(f"\n{len(rows)} réseaux, {total_s} stations, {total_v} véhicules")
    print(f"Écrit : {OUT}")


if __name__ == "__main__":
    main()
