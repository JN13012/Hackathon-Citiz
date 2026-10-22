"""Convertit le portefeuille priorisé (objectif 3, équipe ML) en portefeuille.js pour la carte HTML."""
import json, sys
from pathlib import Path
import pandas as pd
D, OUT = Path(sys.argv[1]), Path(sys.argv[2])
p = pd.read_csv(D / "portefeuille_priorise.csv").sort_values("rang")
n = lambda v, k=0: None if pd.isna(v) else round(float(v), k)
rows = [[int(r.rang), r.type, r.nom_commune, int(r.habitants_gagnes), int(r.cout_annualise), n(r.locations_jour, 2), n(r.marge_central), r.vehicule if isinstance(r.vehicule, str) else "",
         n(r.co2_evite_t_an, 2), round(r.lon, 5), round(r.lat, 5), int(r.budget_cumule), int(r.habitants_cumules), int(r.couverture == "estimee_vol_oiseau")] for r in p.itertuples()]
(OUT / "portefeuille.js").write_text("const PORTEFEUILLE=" + json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
print(len(rows), "équipements ;", p.type.value_counts().to_dict())
