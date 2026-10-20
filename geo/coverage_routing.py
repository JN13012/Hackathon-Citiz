"""Relations entre deux ensembles de centres, sur le réseau piéton contrôlé."""

import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra

from geo.csr_walking import CSRWalking


class CoverageNetwork:
    """Raccordements préparés par lots ; aucun objet pandas dans la boucle Dijkstra."""

    def __init__(self, edges):
        self.network = CSRWalking(edges)

    def prepare(self, points):
        keys = pd.MultiIndex.from_frame(points[["raccord_de", "raccord_vers", "raccord_cle"]])
        indices = self.network.lengths.index.get_indexer(keys)
        length = np.zeros(len(points))
        found = indices >= 0
        length[found] = self.network.lengths.to_numpy()[indices[found]]
        u = self.network.ids.get_indexer(points.raccord_de)
        v = self.network.ids.get_indexer(points.raccord_vers)
        position = points.abscisse_raccord_m.to_numpy(dtype=float)
        cost = points.temps_acces_s.to_numpy(dtype=float)
        good = points.statut.eq("ok").to_numpy()
        present = found & (u >= 0) & (v >= 0)
        checked = good & present
        if (not np.isfinite(position[checked]).all() or not np.isfinite(cost[checked]).all()
                or (position[checked] < -1e-6).any() or (position[checked] > length[checked] + 1e-6).any()
                or (cost[checked] < 0).any()):
            raise ValueError("Raccordement incompatible avec le tronçon")
        return {"u": u, "v": v, "length": length, "position": position, "cost": cost,
                "key": points.raccord_cle.to_numpy(dtype=np.int64),
                "valid": good & present, "missing": good & ~present}

    def times(self, sources, i, targets, js, budget=300.):
        """Temps dans les deux sens, avec raccordements et trajet intérieur au segment."""
        speed = self.network.speed
        u, v = sources["u"][i], sources["v"][i]
        cost, pos, length = sources["cost"][i], sources["position"][i], sources["length"][i]
        seed_cost = np.array([cost + pos / speed, cost + (length - pos) / speed])
        seeds = seed_cost <= budget + 1e-7
        result = np.full(len(js), np.inf)
        if seeds.any():
            reached = dijkstra(self.network.matrix, directed=True, indices=np.array([u, v])[seeds], limit=budget)
            at_u = np.min(reached[:, targets["u"][js]] + seed_cost[seeds, None], axis=0)
            at_v = np.min(reached[:, targets["v"][js]] + seed_cost[seeds, None], axis=0)
            result = np.minimum(at_u + targets["position"][js] / speed,
                                at_v + (targets["length"][js] - targets["position"][js]) / speed) + targets["cost"][js]
        same = ((sources["key"][i] == targets["key"][js])
                & (((u == targets["u"][js]) & (v == targets["v"][js]))
                   | ((u == targets["v"][js]) & (v == targets["u"][js]))))
        position = np.where(u == targets["u"][js], targets["position"][js], length - targets["position"][js])
        direct = cost + np.abs(pos - position) / speed + targets["cost"][js]
        result[same] = np.minimum(result[same], direct[same])
        return result


def route_coverage(engine, candidates, population, query):
    """Retourne les relations connues et indéterminées ; candidat et habitant distincts."""
    ci, pi, times = [], [], []
    ui, up, reasons = [], [], []
    if not query.size:
        return (pd.DataFrame(columns=["id_candidat", "id_carreau", "temps_pieton_s"]),
                pd.DataFrame(columns=["id_candidat", "id_carreau", "motif"]))
    source_good = candidates.statut.eq("ok").to_numpy()
    target_good = population.statut.eq("ok").to_numpy()
    source = target = None
    if engine is not None:
        source, target = engine.prepare(candidates), engine.prepare(population)
        if source["missing"].any():
            raise ValueError("Raccordement d'un candidat absent de sa tuile principale")
        target_good = target["valid"]
    q = query[:, np.argsort(query[0], kind="stable")]
    starts = np.r_[0, np.flatnonzero(np.diff(q[0])) + 1, q.shape[1]]
    for begin, end in zip(starts[:-1], starts[1:]):
        i, js = q[0, begin], q[1, begin:end]
        if not source_good[i]:
            ui.extend([i] * len(js)); up.extend(js)
            reasons.extend(["candidat_" + candidates.statut.iloc[i]] * len(js))
            continue
        bad = js[~target_good[js]]
        ui.extend([i] * len(bad)); up.extend(bad)
        for j in bad:
            reasons.append("population_segment_absent_tuile" if target is not None and target["missing"][j]
                           else "population_" + population.statut.iloc[j])
        good = js[target_good[js]]
        if not len(good):
            continue
        if engine is None:
            raise ValueError("Réseau requis pour les raccordements valides")
        duration = engine.times(source, i, target, good)
        reached = duration <= 300.0000001
        ci.extend([i] * int(reached.sum())); pi.extend(good[reached]); times.extend(np.minimum(duration[reached], 300.))
    ids, cells = candidates.id_candidat.to_numpy(), population.id_carreau.to_numpy()
    pairs = pd.DataFrame({"id_candidat": ids[np.array(ci, dtype=int)], "id_carreau": cells[np.array(pi, dtype=int)], "temps_pieton_s": times})
    unknown = pd.DataFrame({"id_candidat": ids[np.array(ui, dtype=int)], "id_carreau": cells[np.array(up, dtype=int)], "motif": reasons})
    return pairs.sort_values(["id_candidat", "id_carreau"]).reset_index(drop=True), unknown.sort_values(["id_candidat", "id_carreau"]).reset_index(drop=True)
