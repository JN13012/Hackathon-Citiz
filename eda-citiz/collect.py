"""Relevé de disponibilité des voitures Citiz (flux GBFS 3.0 publics).
 
Uniquement la bibliothèque standard de Python 3.8+ : rien à installer.
 
    python collect.py                      # un relevé toutes les 5 min, jusqu'à Ctrl+C
    python collect.py --interval 300 --out donnees
 
Pour chaque relevé et chaque réseau (par défaut ceux d'Auvergne-Rhône-Alpes : Lyon, Alpes Loire, Auto-en-Velay) :
  - station_status et vehicle_status sont écrits en CSV à plat (un fichier par jour) ;
  - la réponse brute de chaque flux est aussi gardée (jsonl.gz) au cas où un champ manquerait ;
  - station_information et vehicle_types sont enregistrés une fois par jour.
Le script reprend seul après une coupure réseau et peut être relancé sans rien perdre.
"""
import argparse, csv, gzip, json, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
 
NETWORKS = {
    "citiz_grand_est": "https://backend.citiz.fr/public/provider/1/gbfs/3.0/gbfs.json",
    "citiz_provence": "https://backend.citiz.fr/public/provider/6/gbfs/3.0/gbfs.json",
    # Auvergne-Rhône-Alpes (numéros de réseau du catalogue MobilityData)
    "citiz_lyon": "https://backend.citiz.fr/public/provider/3/gbfs/3.0/gbfs.json",
    "citiz_alpes_loire": "https://backend.citiz.fr/public/provider/5/gbfs/3.0/gbfs.json",
    "citiz_auto_en_velay": "https://backend.citiz.fr/public/provider/23/gbfs/3.0/gbfs.json",
}
# Réseaux relevés par défaut : ceux de la région choisie (changer ici ou avec --networks)
DEFAULT_NETWORKS = ["citiz_lyon", "citiz_alpes_loire", "citiz_auto_en_velay"]
UA = "hackathon-mobilite-citiz/0.1 (releve de disponibilite, 1 requete par flux toutes les 5 min)"
STATUS_FIELDS = ["ts", "network", "station_id", "num_vehicles_available", "num_docks_available",
                 "is_installed", "is_renting", "is_returning", "last_reported", "vehicle_types_available"]
VEHICLE_FIELDS = ["ts", "network", "vehicle_id", "station_id", "vehicle_type_id", "lat", "lon",
                  "is_reserved", "is_disabled", "current_range_meters", "last_reported"]
 
 
def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)
 
 
def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))
 
 
def feed_urls(gbfs_url):
    # L'adresse de découverte existe sous « gbfs/3.0 » ou « gbfs/v3.0 » : on essaie les deux.
    candidates = [gbfs_url]
    if "/gbfs/3.0/" in gbfs_url:
        candidates.append(gbfs_url.replace("/gbfs/3.0/", "/gbfs/v3.0/"))
    last = None
    for url in candidates:
        try:
            feeds = fetch(url, tries=2)["data"]["feeds"]
            return {f["name"]: f["url"] for f in feeds}
        except Exception as e:
            last = e
    raise last
 
 
def append_csv(path, fields, rows):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
 
 
def append_raw(path, record):
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
 
 
def snapshot(out, urls, day, ts, saved_static):
    for net, feeds in urls.items():
        # Données quasi fixes : une fois par jour et par réseau
        for name in ("station_information", "vehicle_types"):
            key = (net, name, day)
            if name in feeds and key not in saved_static:
                try:
                    (out / f"{net}_{name}_{day}.json").write_text(
                        json.dumps(fetch(feeds[name]), ensure_ascii=False), encoding="utf-8")
                    saved_static.add(key)
                except Exception as e:
                    log(net, name, "erreur :", e)
        # Relevés fréquents
        if "station_status" in feeds:
            try:
                data = fetch(feeds["station_status"])
                st = data["data"]["stations"]
                rows = [{"ts": ts, "network": net, **s,
                         "vehicle_types_available": json.dumps(s.get("vehicle_types_available", []))} for s in st]
                append_csv(out / f"station_status_{day}.csv", STATUS_FIELDS, rows)
                append_raw(out / f"raw_{day}.jsonl.gz", {"ts": ts, "network": net, "feed": "station_status", "data": data})
                log(net, "station_status :", len(st), "stations")
            except Exception as e:
                log(net, "station_status erreur :", e)
        if "vehicle_status" in feeds:
            try:
                data = fetch(feeds["vehicle_status"])
                veh = data["data"]["vehicles"]
                rows = [{"ts": ts, "network": net, **v} for v in veh]
                append_csv(out / f"vehicle_status_{day}.csv", VEHICLE_FIELDS, rows)
                append_raw(out / f"raw_{day}.jsonl.gz", {"ts": ts, "network": net, "feed": "vehicle_status", "data": data})
                log(net, "vehicle_status :", len(veh), "véhicules")
            except Exception as e:
                log(net, "vehicle_status erreur :", e)
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300, help="secondes entre deux relevés (min. 60)")
    ap.add_argument("--out", default="donnees", help="dossier de sortie")
    ap.add_argument("--once", action="store_true", help="un seul relevé puis arrêt (test)")
    ap.add_argument("--networks", default=",".join(DEFAULT_NETWORKS), help="réseaux à relever, séparés par des virgules (voir NETWORKS)")
    ap.add_argument("--gbfs-base", default=None, help="(tests) remplace l'hôte backend.citiz.fr")
    a = ap.parse_args()
    a.interval = max(a.interval, 60)  # ne pas surcharger le service
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = {k: v for k, v in NETWORKS.items() if k in a.networks.split(",")}
    if a.gbfs_base:
        chosen = {k: v.replace("https://backend.citiz.fr", a.gbfs_base) for k, v in chosen.items()}
    saved_static = set()
    urls = {}
    log("Démarrage. Dossier :", out.resolve(), "| Arrêt : Ctrl+C")
    while True:
        started = time.time()
        for net, gbfs in chosen.items():  # (re)découverte des flux au besoin
            if net not in urls:
                try:
                    urls[net] = feed_urls(gbfs)
                except Exception as e:
                    log(net, "flux introuvable, nouvelle tentative au prochain tour :", e)
        now = datetime.now(timezone.utc)
        snapshot(out, urls, f"{now:%Y-%m-%d}", now.isoformat(timespec="seconds"), saved_static)
        if a.once:
            return
        time.sleep(max(1, a.interval - (time.time() - started)))
 
 
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêté.")