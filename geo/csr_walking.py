"""Routage borné depuis des raccordements contrôlés, sans graphe Python global."""

import math
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


class CSRWalking:
    def __init__(self, edges, speed_kmh=4.5):
        self.speed = speed_kmh / 3.6
        if not math.isfinite(self.speed) or self.speed <= 0:
            raise ValueError("Vitesse invalide")
        if edges.duplicated(["de", "vers", "cle"]).any():
            raise ValueError("Arcs dupliqués")
        if not np.isfinite(edges[["longueur_m", "temps_pieton_s"]]).all().all() or edges[["longueur_m", "temps_pieton_s"]].le(0).any().any():
            raise ValueError("Poids invalides")
        if not np.allclose(edges.temps_pieton_s, edges.longueur_m / self.speed, rtol=0, atol=1e-6):
            raise ValueError("Vitesse incompatible avec les poids")
        self.lengths = edges.set_index(["de", "vers", "cle"]).longueur_m
        # Les arêtes parallèles sont des alternatives : CSR les sommerait.
        cheapest = edges.groupby(["de", "vers"]).temps_pieton_s.min().reset_index()
        self.ids = pd.Index(np.unique(np.r_[cheapest.de, cheapest.vers]))
        self.matrix = csr_matrix((cheapest.temps_pieton_s,
                                  (self.ids.get_indexer(cheapest.de), self.ids.get_indexer(cheapest.vers))),
                                 shape=(len(self.ids), len(self.ids)))

    def length(self, snap):
        key = (int(snap.raccord_de), int(snap.raccord_vers), int(snap.raccord_cle))
        if key not in self.lengths.index:
            raise KeyError(key)
        length = float(self.lengths.loc[key])
        if not -1e-6 <= snap.abscisse_raccord_m <= length + 1e-6 or not math.isfinite(snap.temps_acces_s) or snap.temps_acces_s < 0:
            raise ValueError("Raccordement incompatible avec son segment")
        return length

    def prepare_targets(self, targets):
        rows = []
        for row in targets.itertuples(index=False):
            status = row.statut
            u = v = -1
            length = position = cost = 0.
            node = getattr(row, "noeud_osm", None)
            if status == "ok":
                if pd.notna(node):
                    u = v = self.ids.get_indexer([int(node)])[0]
                    if u < 0:
                        status = "segment_cible_absent_tuile"
                else:
                    try:
                        length = self.length(row)
                        u, v = self.ids.get_indexer([int(row.raccord_de), int(row.raccord_vers)])
                        position, cost = row.abscisse_raccord_m, row.temps_acces_s
                    except KeyError:
                        status = "segment_cible_absent_tuile"
            rows.append((status, u, v, length, position, cost))
        return pd.DataFrame(rows, columns=["statut", "u", "v", "length", "position", "cost"])

    def durations(self, source, targets, prepared, budget=300):
        length = self.length(source)
        u, v = self.ids.get_indexer([int(source.raccord_de), int(source.raccord_vers)])
        distances = dijkstra(self.matrix, directed=True, indices=[u, v], limit=budget)
        result = np.full(len(targets), np.inf)
        valid = np.flatnonzero(prepared.statut.eq("ok"))
        if not len(valid):
            return result
        p = prepared.iloc[valid]
        to_u = np.minimum(distances[0, p.u] + source.temps_acces_s + source.abscisse_raccord_m / self.speed,
                          distances[1, p.u] + source.temps_acces_s + (length - source.abscisse_raccord_m) / self.speed)
        to_v = np.minimum(distances[0, p.v] + source.temps_acces_s + source.abscisse_raccord_m / self.speed,
                          distances[1, p.v] + source.temps_acces_s + (length - source.abscisse_raccord_m) / self.speed)
        result[valid] = np.minimum(to_u + p.position.to_numpy() / self.speed,
                                   to_v + (p.length.to_numpy() - p.position.to_numpy()) / self.speed) + p.cost.to_numpy()
        # Deux projections peuvent partager l'intérieur d'un segment long.
        for i in valid:
            target = targets.iloc[i]
            if pd.notna(target.noeud_osm):
                continue
            if (int(source.raccord_cle) == int(target.raccord_cle)
                    and {int(source.raccord_de), int(source.raccord_vers)} == {int(target.raccord_de), int(target.raccord_vers)}):
                position = target.abscisse_raccord_m
                if source.raccord_de != target.raccord_de:
                    position = length - position
                direct = source.temps_acces_s + abs(source.abscisse_raccord_m - position) / self.speed + target.temps_acces_s
                result[i] = min(result[i], direct)
        return result


def route_candidates(engine, population, endpoints, candidates, budget=300):
    """Un propriétaire par centre ; union des accès au niveau de la station."""
    known, unknown = {}, {}
    if not candidates.size:
        return pd.DataFrame(columns=["id_carreau", "offre_id", "temps_pieton_s", "acces_id"]), pd.DataFrame(columns=["id_carreau", "offre_id", "motif"])
    targets = endpoints.reset_index(drop=True)
    prepared = engine.prepare_targets(targets) if engine is not None else None
    order = np.argsort(candidates[0], kind="stable")
    candidates = candidates[:, order]
    split = np.flatnonzero(np.diff(candidates[0])) + 1
    for indices in np.split(np.arange(candidates.shape[1]), split):
        i = candidates[0, indices[0]]
        source = population.iloc[i]
        js = candidates[1, indices]
        if source.statut == "ok" and engine is not None:
            times = engine.durations(source, targets.iloc[js].reset_index(drop=True), prepared.iloc[js].reset_index(drop=True), budget)
        else:
            times = np.full(len(js), np.inf)
        for k, j in enumerate(js):
            target = targets.iloc[j]
            key = (source.id_carreau, target.offre_id)
            status = prepared.statut.iloc[j] if prepared is not None else target.statut
            if source.statut != "ok" or status != "ok":
                unknown[key] = "population_" + source.statut if source.statut != "ok" else "offre_" + status
            elif times[k] <= budget + 1e-7:
                value = (min(float(times[k]), budget), target.acces_id if pd.notna(target.acces_id) else "")
                if key not in known or value < known[key]:
                    known[key] = value
    pairs = pd.DataFrame([{"id_carreau": p, "offre_id": o, "temps_pieton_s": t, "acces_id": a or None}
                          for (p, o), (t, a) in sorted(known.items())], columns=["id_carreau", "offre_id", "temps_pieton_s", "acces_id"])
    pending = pd.DataFrame([{"id_carreau": p, "offre_id": o, "motif": why} for (p, o), why in sorted(unknown.items()) if (p, o) not in known],
                           columns=["id_carreau", "offre_id", "motif"])
    return pairs, pending
