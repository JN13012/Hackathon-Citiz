"""Tests de l'algorithme glouton de l'optimiseur (optim/optimise.py) sur de petits cas construits à la main."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "optim"))
import optimise as O  # noqa: E402


def case():
    # 3 candidats alignés tous les 100 m, 4 carreaux ; le candidat 1 couvre ce que couvrent 0 et 2
    xy = np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]])
    ci = np.array([0, 0, 1, 1, 1, 2, 2])
    pi = np.array([0, 1, 1, 2, 3, 2, 3])
    population = np.array([10.0, 20.0, 30.0, 40.0])
    return xy, ci, pi, population


class GreedyTest(unittest.TestCase):
    def test_budget_is_respected(self):
        xy, ci, pi, pop = case()
        chosen = O.greedy(xy, ci, pi, pop, np.zeros(4, bool), cost=100, weight=np.ones(3), budget=150, dmin=0)
        self.assertEqual(len(chosen), 1)  # un seul équipement à 100 € dans un budget de 150 €

    def test_best_candidate_first_and_no_double_count(self):
        xy, ci, pi, pop = case()
        chosen = O.greedy(xy, ci, pi, pop, np.zeros(4, bool), cost=1, weight=np.ones(3), budget=10, dmin=0)
        self.assertEqual(chosen[0][0], 1)          # le candidat 1 couvre 90 habitants
        self.assertEqual(chosen[0][1], 90.0)
        gained = sum(g for _, g, _ in chosen)
        self.assertEqual(gained, pop.sum())        # chaque habitant compté une seule fois

    def test_existing_service_is_not_gained_again(self):
        xy, ci, pi, pop = case()
        served = np.array([False, True, True, True])  # seul le carreau 0 n'est pas desservi
        chosen = O.greedy(xy, ci, pi, pop, served, cost=1, weight=np.ones(3), budget=10, dmin=0)
        self.assertEqual([(c, g) for c, g, _ in chosen], [(0, 10.0)])

    def test_minimum_distance_between_new_equipments(self):
        xy, ci, pi, pop = case()
        chosen = O.greedy(xy, ci, pi, pop, np.zeros(4, bool), cost=1, weight=np.ones(3), budget=10, dmin=150)
        # le candidat 1 est choisi ; 0 et 2 sont à 100 m : refusés
        self.assertEqual([c for c, _, _ in chosen], [1])

    def test_weight_zero_excludes_candidate(self):
        xy, ci, pi, pop = case()
        chosen = O.greedy(xy, ci, pi, pop, np.zeros(4, bool), cost=1, weight=np.array([1.0, 0.0, 1.0]),
                          budget=10, dmin=0)
        self.assertNotIn(1, [c for c, _, _ in chosen])


if __name__ == "__main__":
    unittest.main()
