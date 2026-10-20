"""Même regroupement glouton que le pilote, avec tas et index spatial statique."""

import hashlib
import heapq
import time
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree, union_all


def population_groups_fast(cells, minimum=20):
    cells = cells.sort_values("id_carreau").reset_index(drop=True)
    if cells.empty or not cells.id_carreau.is_unique or cells.population.isna().any() or cells.population.le(0).any():
        raise ValueError("Carreaux ou populations invalides")
    if not np.isfinite(minimum) or minimum <= 0:
        raise ValueError("Seuil invalide")
    geometries = cells.geometry.to_numpy()
    tree = STRtree(geometries)
    owner = np.arange(len(cells))
    groups = {i: {"members": [i], "population": float(r.population), "geometry": r.geometry, "version": 0}
              for i, r in enumerate(cells.itertuples(index=False))}
    heap = [(g["population"], i, 0) for i, g in groups.items() if g["population"] < minimum]
    heapq.heapify(heap)
    fallbacks, merges, last_log = 0, 0, time.monotonic()
    while heap and len(groups) > 1:
        pop, key, version = heapq.heappop(heap)
        if key not in groups or groups[key]["version"] != version or groups[key]["population"] != pop:
            continue
        current = groups[key]
        # Toutes les frontières communes sont dans cette requête. Si aucun
        # voisin ne touche, étendre la distance jusqu'à trouver les plus proches.
        candidates = set(owner[tree.query(current["geometry"], predicate="intersects")]) - {key}
        radius = 200.
        while not candidates:
            candidates = set(owner[tree.query(current["geometry"], predicate="dwithin", distance=radius)]) - {key}
            radius *= 2
        boundary = current["geometry"].boundary

        def priority(other):
            target = groups[other]
            shared = boundary.intersection(target["geometry"].boundary).length
            return (0 if shared > .01 else 1, -shared if shared > .01 else current["geometry"].distance(target["geometry"]), other)

        neighbor = min(candidates, key=priority)
        if priority(neighbor)[0]:
            fallbacks += 1
        target = groups[neighbor]
        members = current["members"] + target["members"]
        new_key = min(key, neighbor)
        new = {"members": members, "population": pop + target["population"],
               "geometry": union_all([current["geometry"], target["geometry"]]),
               "version": max(current["version"], target["version"]) + 1}
        del groups[key], groups[neighbor]
        groups[new_key] = new
        owner[members] = new_key
        if new["population"] < minimum:
            heapq.heappush(heap, (new["population"], new_key, new["version"]))
        merges += 1
        if time.monotonic() - last_log >= 15:
            print(f"Regroupement : {merges} fusions ; {len(groups)} groupes restants.", flush=True)
            last_log = time.monotonic()
    rows, mapping = [], []
    ids = cells.id_carreau.to_numpy()
    for key in sorted(groups):
        group = groups[key]
        members = sorted(ids[group["members"]])
        group_id = "g_" + hashlib.sha256("|".join(members).encode()).hexdigest()[:16]
        rows.append({"groupe_id": group_id, "population": group["population"], "nb_carreaux": len(members),
                     "seuil_population_atteint": group["population"] >= minimum, "geometry": group["geometry"]})
        mapping.extend({"id_carreau": identifier, "groupe_id": group_id} for identifier in members)
    return gpd.GeoDataFrame(rows, crs=cells.crs), pd.DataFrame(mapping), fallbacks
