"""Archive les deux extraits OSM Geofabrik couvrant l'ARA, avec contrôle MD5."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {name: f"https://download.geofabrik.de/europe/france/{name}-latest.osm.pbf"
           for name in ["auvergne", "rhone-alpes"]}


def checksum(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, url in SOURCES.items():
        path = output_dir / f"{name}.osm.pbf"
        request = urllib.request.Request(url + ".md5", headers={"User-Agent": "MobilityCitizHackathon/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            expected = response.read().decode().split()[0]
        if path.exists() and checksum(path, "md5") == expected:
            print(f"{name} : archive déjà vérifiée.", flush=True)
        else:
            partial = path.with_suffix(".pbf.part")
            for attempt in range(3):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "MobilityCitizHackathon/1.0"})
                    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as stream:
                        received, milestone = 0, 0
                        modified = response.headers.get("Last-Modified")
                        for block in iter(lambda: response.read(1024 * 1024), b""):
                            stream.write(block)
                            received += len(block)
                            if received // (25 * 1024**2) > milestone:
                                milestone = received // (25 * 1024**2)
                                print(f"{name} : {received / 1024**2:.0f} Mio téléchargés", flush=True)
                    if checksum(partial, "md5") != expected:
                        raise ValueError(f"MD5 Geofabrik incorrect : {name}")
                    partial.replace(path)
                    break
                except (OSError, ValueError):
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
        rows.append({"nom": name, "url": url, "md5": expected, "sha256": checksum(path),
                     "octets": path.stat().st_size, "chemin": str(path)})
    report = {"execution_utc": datetime.now(timezone.utc).isoformat(), "source": "Geofabrik / OpenStreetMap contributors",
              "licence": "ODbL", "archives": rows}
    (output_dir / "sources_osm.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print("Archives OSM ARA téléchargées et vérifiées.", flush=True)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/bronze/osm_ara")
    download(ap.parse_args().output_dir)
