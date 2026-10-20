"""O1 et O2 locaux : trajets piétons entre centres de carreaux et offre recensée.

Aucune surface tamponnée n'intervient dans les comptages. Les raccordements
rectilignes aux voies et la représentation de la population au centre du carreau
sont des hypothèses explicites, pas une mesure d'accès aux bâtiments.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import STRtree, union_all

try:
    from geo.build_isochrones import CRS, ROOT, WalkingNetwork, sha256
except ModuleNotFoundError:  # python geo/build_accessibilite.py
    from build_isochrones import CRS, ROOT, WalkingNetwork, sha256


@dataclass(frozen=True)
class Snap:
    index: int
    position: float
    distance: float
    statut: str


class PairNetwork(WalkingNetwork):
    """Réutilise l'index/validation des isochrones pour router des paires de points."""

    def __init__(self, graph, speed_kmh):
        super().__init__(graph, speed_kmh)
        # Le modèle marche dans les deux sens d'une même voie physique.
        # La simple présence d'une autre voie v->u ne suffit pas.
        for (u, v, _), line in self.by_key.items():
            if not any(line.equals(self.by_key[(v, u, k)]) for k in graph[v][u]):
                raise ValueError("Une voie ne possède pas sa géométrie inverse pour la marche.")

    def snap(self, point, budget_s, max_snap_m):
        matches, distances = self.tree.query_nearest(point, all_matches=True, return_distance=True)
        index = int(min(matches, key=lambda i: self.keys[i]))
        distance_m = float(distances[0])
        status = "ok"
        if distance_m > max_snap_m:
            status = "raccordement_trop_loin"
        elif distance_m / self.speed_ms >= budget_s:
            status = "budget_acces_epuise"
        return Snap(index, self.lines[index].project(point), distance_m, status)

    def node_times(self, start, budget_s):
        if start.statut != "ok":
            return {}
        u, v, _ = self.keys[start.index]
        access = start.distance / self.speed_ms
        virtual = object()
        try:
            self.graph.add_edge(virtual, u, temps_pieton_s=access + start.position / self.speed_ms)
            self.graph.add_edge(virtual, v, temps_pieton_s=access + (self.lines[start.index].length - start.position) / self.speed_ms)
            reached = nx.single_source_dijkstra_path_length(self.graph, virtual, cutoff=budget_s, weight="temps_pieton_s")
            reached.pop(virtual)
            return reached
        finally:
            if virtual in self.graph:
                self.graph.remove_node(virtual)

    def travel_time(self, start, target, reached):
        if start.statut != "ok" or target.statut != "ok":
            return math.inf
        u, v, _ = self.keys[target.index]
        line = self.lines[target.index]
        duration = min(reached.get(u, math.inf) + target.position / self.speed_ms,
                       reached.get(v, math.inf) + (line.length - target.position) / self.speed_ms)
        start_line = self.lines[start.index]
        # Deux points peuvent se rejoindre à l'intérieur d'une voie longue sans
        # atteindre ses nœuds. Exiger aussi les mêmes nœuds évite les faux liens
        # entre routes superposées mais déconnectées.
        if (set(self.keys[start.index][:2]) == {u, v} and start_line.equals(line)):
            projected = line.interpolate(target.position)
            direct = start.distance / self.speed_ms + abs(start.position - start_line.project(projected)) / self.speed_ms
            duration = min(duration, direct)
        return duration + target.distance / self.speed_ms


def read_frame(path, id_col, geometry_types):
    frame = gpd.read_parquet(path)
    if frame.crs is None or frame.empty:
        raise ValueError(f"Source vide ou projection absente : {path}")
    if id_col not in frame or frame[id_col].isna().any():
        raise ValueError(f"Identifiants absents : {id_col}")
    frame[id_col] = frame[id_col].astype(str).str.strip()
    if frame[id_col].eq("").any() or frame[id_col].duplicated().any():
        raise ValueError(f"Identifiants vides ou dupliqués : {id_col}")
    if (frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid).any() or not frame.geom_type.isin(geometry_types).all():
        raise ValueError(f"Géométries invalides : {path}")
    return frame.to_crs(CRS)


def population_groups(cells, minimum):
    """Fusion déterministe des petits groupes, en privilégiant un bord commun.

    La population n'est ni divisée ni redistribuée. Un voisin géométriquement
    disjoint n'est utilisé que faute de voisin contigu ; ce repli est compté.
    """
    groups = {str(r.id_carreau): {"ids": [str(r.id_carreau)], "population": float(r.population), "geometry": r.geometry}
              for r in cells.sort_values("id_carreau").itertuples()}
    fallbacks = 0
    while len(groups) > 1:
        small = [key for key, group in groups.items() if group["population"] < minimum]
        if not small:
            break
        key = min(small, key=lambda k: (groups[k]["population"], k))
        current = groups.pop(key)
        def priority(other):
            candidate = groups[other]
            shared = current["geometry"].boundary.intersection(candidate["geometry"].boundary).length
            return (0 if shared > 0.01 else 1, -shared if shared > 0.01 else current["geometry"].distance(candidate["geometry"]), other)
        neighbor = min(groups, key=priority)
        if priority(neighbor)[0]:
            fallbacks += 1
        target = groups.pop(neighbor)
        ids = sorted(current["ids"] + target["ids"])
        groups[min(ids)] = {"ids": ids, "population": current["population"] + target["population"],
                            "geometry": union_all([current["geometry"], target["geometry"]])}
    rows, mapping = [], []
    for key, group in sorted(groups.items()):
        group_id = "g_" + hashlib.sha256("|".join(group["ids"]).encode()).hexdigest()[:16]
        rows.append({"groupe_id": group_id, "population": group["population"], "nb_carreaux": len(group["ids"]),
                     "seuil_population_atteint": group["population"] >= minimum, "geometry": group["geometry"]})
        mapping.extend({"id_carreau": cell, "groupe_id": group_id} for cell in group["ids"])
    return gpd.GeoDataFrame(rows, crs=CRS), pd.DataFrame(mapping), fallbacks


def route_pairs(network, population, offers, budget_s, snap_population_m, snap_offre_m):
    pop_snaps = [network.snap(p, budget_s, snap_population_m) for p in population.geometry]
    offer_snaps = [network.snap(p, budget_s, snap_offre_m) for p in offers.geometry]
    radius = budget_s * network.speed_ms
    tree = STRtree(offers.geometry.to_numpy())
    pairs, uncertain = [], []
    for i, pop in enumerate(population.itertuples(index=False)):
        reached = network.node_times(pop_snaps[i], budget_s)
        for j in sorted(tree.query(pop.geometry, predicate="dwithin", distance=radius + 1e-6)):
            offer = offers.iloc[j]
            if not pop.dans_pilote and not offer.dans_pilote:
                continue
            if pop_snaps[i].statut != "ok" or offer_snaps[j].statut != "ok":
                uncertain.append({"id_carreau": pop.id_carreau, "offre_id": offer.offre_id})
                continue
            duration = network.travel_time(pop_snaps[i], offer_snaps[j], reached)
            if duration <= budget_s + 1e-7:
                pairs.append({"id_carreau": pop.id_carreau, "offre_id": offer.offre_id, "temps_pieton_s": min(duration, budget_s)})
        if (i + 1) % 100 == 0 or i + 1 == len(population):
            print(f"Routage : {i + 1}/{len(population)} carreaux ; {len(pairs)} paires accessibles.", flush=True)
    relations = pd.DataFrame(pairs, columns=["id_carreau", "offre_id", "temps_pieton_s"])
    unknown = pd.DataFrame(uncertain, columns=["id_carreau", "offre_id"])
    def diagnostics(frame, snaps, id_col):
        return gpd.GeoDataFrame([
            {id_col: row[id_col], "dans_pilote": bool(row.dans_pilote), "statut": snap.statut,
             "distance_raccordement_m": snap.distance, "temps_acces_s": snap.distance / network.speed_ms,
             "raccord_de": int(network.keys[snap.index][0]), "raccord_vers": int(network.keys[snap.index][1]),
             "raccord_cle": int(network.keys[snap.index][2]), "abscisse_raccord_m": snap.position,
             "geometry": row.geometry} for (_, row), snap in zip(frame.iterrows(), snaps)
        ], crs=CRS)
    return relations, unknown, diagnostics(population, pop_snaps, "id_carreau"), diagnostics(offers, offer_snaps, "offre_id")


def aggregate(population, offers, cells, pairs, unknown, pop_qc, offer_qc, minimum):
    modes = sorted(offers["type"].unique())
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", mode) for mode in modes):
        raise ValueError("Types d'offre non normalisés.")
    linked = pairs.merge(offers.drop(columns="geometry"), on="offre_id", validate="many_to_one")
    counts = pd.DataFrame(index=population.id_carreau)
    counts["nb_options"] = linked.groupby("id_carreau").size().reindex(counts.index, fill_value=0)
    for mode in modes:
        counts[f"nb_options_{mode}"] = linked.loc[linked["type"].eq(mode)].groupby("id_carreau").size().reindex(counts.index, fill_value=0)
    weighted_columns = []
    for unit, prefix in [("passages_par_jour", "passages_jour"), ("capacite", "capacite")]:
        for mode in modes:
            subset = linked.loc[linked.unite.eq(unit) & linked["type"].eq(mode)]
            # Ne pas mélanger fréquences et capacités, ni les capacités entre modes.
            if not ((offers.unite == unit) & (offers["type"] == mode)).any():
                continue
            column = f"{prefix}_{mode}"
            counts[column] = subset.groupby("id_carreau").valeur.sum().reindex(counts.index, fill_value=0)
            missing_ids = subset.loc[subset.valeur.isna(), "id_carreau"]
            counts.loc[counts.index.isin(missing_ids), column] = np.nan
            weighted_columns.append(column)
    counts["calcul_complet"] = ~counts.index.isin(unknown.id_carreau) & counts.index.isin(pop_qc.loc[pop_qc.statut.eq("ok"), "id_carreau"])
    counts = counts.join(population.set_index("id_carreau")[["population", "dans_pilote"]])
    groups, mapping, fallbacks = population_groups(cells, minimum)
    measures = mapping.merge(counts.reset_index(), on="id_carreau", validate="one_to_one")
    rows = []
    for group in groups.itertuples(index=False):
        members = measures.loc[measures.groupe_id.eq(group.groupe_id)]
        complete = bool(members.calcul_complet.all())
        disclosed = bool(group.seuil_population_atteint)
        row = {"groupe_id": group.groupe_id, "nb_carreaux": group.nb_carreaux,
               "population": group.population if disclosed else None,
               "statut": "ok" if complete and disclosed else "population_insuffisante" if not disclosed else "partiel",
               "population_evaluee": float(members.loc[members.calcul_complet, "population"].sum()) if disclosed else None,
               "geometry": group.geometry}
        for column in ["nb_options", *[f"nb_options_{m}" for m in modes], *weighted_columns]:
            suffix = "options" if column == "nb_options" else column.removeprefix("nb_")
            value = float((members[column] * members.population).sum() / group.population) if disclosed and members[column].notna().all() else None
            row[f"{suffix}_par_habitant_min"] = value
            row[f"{suffix}_par_habitant"] = value if complete else None
        rows.append(row)
    o1 = gpd.GeoDataFrame(rows, crs=CRS)
    # Une même personne peut être desservie par plusieurs stations. Les lignes
    # O2 ne s'additionnent pas pour obtenir une population totale unique.
    served = pairs.merge(population.drop(columns="geometry"), on="id_carreau", validate="many_to_one")
    total = served.groupby("offre_id").population.sum()
    city = served.loc[served.dans_pilote].groupby("offre_id").population.sum()
    n_cells = served.groupby("offre_id").size()
    o2 = offers.loc[offers.dans_pilote].copy()
    o2["statut"] = np.where(o2.offre_id.isin(unknown.offre_id), "partiel", "ok")
    failed = set(offer_qc.loc[offer_qc.statut.ne("ok"), "offre_id"])
    o2.loc[o2.offre_id.isin(failed), "statut"] = "non_calcule"
    for column, values in [("population_desservie", total), ("population_desservie_dans_pilote", city), ("nb_carreaux_desservis", n_cells)]:
        o2[column + "_min"] = o2.offre_id.map(values).fillna(0)
        o2.loc[o2.statut.eq("non_calcule"), column + "_min"] = np.nan
        o2[column] = o2[column + "_min"].where(o2.statut.eq("ok"))
    return o1, o2, mapping, counts, fallbacks


def write_preview(path, o1, o2, boundary, parameters):
    data = {"boundary": boundary.__geo_interface__, "bounds": boundary.bounds,
            "o1": json.loads(o1.to_json(drop_id=True)), "o2": json.loads(o2.to_json(drop_id=True))}
    html = '''<!doctype html><html lang="fr"><meta charset="utf-8"><title>Accessibilité — O1 et O2</title>
<style>body{font:16px system-ui;color:#193043;margin:24px}svg{width:100%;height:72vh;background:#f2f5f7}button{padding:10px;margin-right:8px}#info{min-height:45px}.note{font-size:14px;max-width:1100px}</style>
<h1>Mobilité accessible à pied · O1 et O2</h1>
<button id="o1">O1 · options par habitant</button><button id="o2">O2 · population desservie</button>
<p id="info">Survoler une zone ou une station pour voir son résultat.</p><svg id="map" aria-label="Carte des résultats O1 et O2"></svg>
<p class="note">O1 : nombre d'options accessibles à un habitant, moyenne pondérée par la population des carreaux regroupés (minimum __MIN_POP__ habitants). O2 : population estimée au centre des carreaux, y compris dans les communes voisines. __DUREE__ s, __VITESSE__ km/h, temps de raccordement inclus aux deux extrémités. La carte représente uniquement l'offre livrée ; son exhaustivité n'est pas garantie. Les valeurs nulles indiquent un calcul incomplet.</p>
<p class="note">Population : Filosofi 2021 ; contours IGN ; © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> ; offre GTFS et GBFS livrée par l'équipe. Voir accessibilite_qc.json pour la provenance et les limites.</p>
<script id="data" type="application/json">__DATA__</script><script>
(()=>{const d=JSON.parse(document.getElementById('data').textContent),svg=document.getElementById('map'),info=document.getElementById('info');
const b=d.bounds,pad=80;svg.setAttribute('viewBox',[b[0]-pad,-b[3]-pad,b[2]-b[0]+pad*2,b[3]-b[1]+pad*2].join(' '));
function shape(g){if(g.type==='Polygon')return g.coordinates.map(r=>r.map((p,i)=>(i?'L':'M')+p[0]+' '+(-p[1])).join(' ')+'Z').join(' ');return g.coordinates.map(c=>shape({type:'Polygon',coordinates:c})).join(' ');}
function element(tag,attrs){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);svg.appendChild(e);return e;}
function draw(mode){svg.replaceChildren();element('path',{d:shape(d.boundary),fill:'none',stroke:'#677d8d','stroke-width':2,'vector-effect':'non-scaling-stroke'});
const key=mode==='o1'?'options_par_habitant':'population_desservie',features=d[mode].features,max=Math.max(1,...features.map(f=>f.properties[key]||0));
for(const f of features){const p=f.properties,value=p[key],color=value===null?'#a6afb7':`hsl(${210-(value/max)*170} 65% 48%)`;let e;
if(mode==='o1')e=element('path',{d:shape(f.geometry),fill:color,'fill-opacity':.8,stroke:'#fff','stroke-width':.6,'vector-effect':'non-scaling-stroke','fill-rule':'evenodd'});
else e=element('circle',{cx:f.geometry.coordinates[0],cy:-f.geometry.coordinates[1],r:12+Math.sqrt((value||0)/max)*45,fill:color,'fill-opacity':.7,stroke:'#fff','stroke-width':1,'vector-effect':'non-scaling-stroke'});
e.addEventListener('mouseenter',()=>{info.textContent=mode==='o1'?`${p.groupe_id} · ${p.nb_carreaux} carreau(x) · ${p.population} habitants · O1 : ${value===null?'indéterminé':value.toFixed(2)} option(s) par habitant · ${p.statut}`:`${p.type} · ${p.nom} · ${p.offre_id} · O2 : ${value===null?'indéterminé':value.toFixed(1)} habitants desservis · ${p.statut}`;});}}
document.getElementById('o1').onclick=()=>draw('o1');document.getElementById('o2').onclick=()=>draw('o2');draw('o1');})();</script></html>'''
    html = html.replace('__MIN_POP__', str(parameters['seuil_regroupement_population'])).replace('__DUREE__', str(parameters['duree_s'])).replace('__VITESSE__', str(parameters['vitesse_kmh']))
    path.write_text(html.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')


def build_accessibilite(args):
    for name in ["duree_s", "min_population", "max_raccord_population_m", "max_raccord_offre_m"]:
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Paramètre non positif ou non fini : {name}")
    contours = read_frame(args.contours, "id_zone", ["Polygon", "MultiPolygon"])
    selected = contours.loc[contours.niveau.eq("commune") & contours.code_insee.isin(args.codes_communes)]
    if set(selected.code_insee) != set(args.codes_communes):
        raise ValueError("Communes demandées absentes des contours.")
    boundary = union_all(selected.geometry)
    qc_path = args.reseau_dir / "reseau_pieton_qc.json"
    graph_path = args.reseau_dir / "reseau_pieton.graphml"
    zone_path = args.reseau_dir / "zone_requete.parquet"
    qc_network = json.loads(qc_path.read_text(encoding="utf-8"))
    if not set(args.codes_communes).issubset(qc_network.get("codes_communes", [])):
        raise ValueError("Communes non couvertes par le réseau.")
    speed = float(qc_network["vitesse_kmh_hypothese"])
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Vitesse du réseau invalide.")
    radius = args.duree_s * speed / 3.6
    zone = gpd.read_parquet(zone_path)
    if zone.crs is None or zone.empty or not zone.geometry.is_valid.all():
        raise ValueError("Zone réseau invalide.")
    zone_geometry = union_all(zone.to_crs(CRS).geometry)
    # Les centres O2 et les chemins peuvent tous deux sortir de la commune.
    # 2 rayons est une borne suffisante, conservatrice, pour ce calcul local.
    if not zone_geometry.buffer(0.01).covers(boundary.buffer(2 * radius)):
        raise ValueError("Marge réseau insuffisante pour calculer O2 sans effet de bord.")
    cells = read_frame(args.population, "id_carreau", ["Polygon", "MultiPolygon"])
    cells["population"] = pd.to_numeric(cells.population, errors="raise")
    if not np.isfinite(cells.population).all() or not cells.population.gt(0).all():
        raise ValueError("Populations absentes, non finies ou non positives.")
    population = cells[["id_carreau", "population", "geometry"]].copy()
    population.geometry = population.geometry.centroid
    population["dans_pilote"] = population.geometry.covered_by(boundary)
    population = population.loc[population.geometry.covered_by(boundary.buffer(radius + 1e-6))].reset_index(drop=True)
    pilot_ids = set(population.loc[population.dans_pilote, "id_carreau"])
    cells = cells.loc[cells.id_carreau.isin(pilot_ids), ["id_carreau", "population", "geometry"]].copy()
    if cells.empty:
        raise ValueError("Aucun centre de population dans le pilote.")
    offers = read_frame(args.offre, "offre_id", ["Point"])
    offers["dans_pilote"] = offers.geometry.covered_by(boundary)
    offers = offers.loc[offers.geometry.covered_by(boundary.buffer(radius + 1e-6))].reset_index(drop=True)
    if offers.empty or not offers.dans_pilote.any():
        raise ValueError("Aucune offre dans le pilote.")
    if offers[["type", "source", "unite"]].isna().any().any():
        raise ValueError("Type, source ou unité absents de l'offre.")
    if not offers.unite.isin(["passages_par_jour", "capacite"]).all():
        raise ValueError("Unité d'offre inconnue.")
    offers["valeur"] = pd.to_numeric(offers.valeur, errors="raise")
    if (~np.isfinite(offers.valeur.dropna())).any() or offers.valeur.dropna().lt(0).any():
        raise ValueError("Fréquence ou capacité invalide.")
    print(f"Population : {len(cells)} carreaux pilote, {len(population)} avec voisins ; offre : {int(offers.dans_pilote.sum())} pilote, {len(offers)} avec voisins.", flush=True)
    graph = ox.load_graphml(graph_path, edge_dtypes={"longueur_m": float, "temps_pieton_s": float}, node_dtypes={"composante_id": int})
    zone_hash = qc_network.get("acquisition_origine", {}).get("zone_sha256")
    if zone_hash and (zone_hash != graph.graph.get("zone_sha256") or zone_hash != hashlib.sha256(zone_geometry.wkb).hexdigest()):
        raise ValueError("Provenances graphe/zone/rapport incohérentes.")
    network = PairNetwork(graph, speed)
    pairs, unknown, pop_qc, offer_qc = route_pairs(network, population, offers, args.duree_s, args.max_raccord_population_m, args.max_raccord_offre_m)
    o1, o2, mapping, counts, fallbacks = aggregate(population, offers, cells, pairs, unknown, pop_qc, offer_qc, args.min_population)
    pilot_counts = counts.loc[counts.dans_pilote]
    evaluated = pilot_counts.calcul_complet.all()
    pop_total = float(cells.population.sum())
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "codes_communes": sorted(set(args.codes_communes)), "crs": CRS,
        "etat_calcul": "complet" if o1.statut.eq("ok").all() and o2.statut.eq("ok").all() else "partiel",
        "carreaux_pilote": len(cells), "carreaux_avec_voisins": len(population), "population_pilote": pop_total,
        "offres_pilote": len(o2), "offres_avec_voisins": len(offers), "offres_par_type_pilote": o2["type"].value_counts().to_dict(),
        "groupes_o1": len(o1), "carreaux_sous_seuil_avant_regroupement": int(cells.population.lt(args.min_population).sum()),
        "regroupements_sans_bord_commun": fallbacks, "paires_accessibles": len(pairs), "paires_indeterminees": len(unknown),
        "statuts_population": pop_qc.statut.value_counts().to_dict(), "statuts_offre": offer_qc.statut.value_counts().to_dict(),
        "valeurs_offre_manquantes_pilote": int(o2.valeur.isna().sum()),
        "valeurs_offre_manquantes_avec_voisins": int(offers.valeur.isna().sum()),
        "groupes_o1_capacite_incomplete": int(o1[[c for c in o1 if c.startswith("capacite_") and not c.endswith("_min")]].isna().any(axis=1).sum()),
        "distance_raccordement_max_population_m": float(pop_qc.distance_raccordement_m.max()),
        "distance_raccordement_max_offre_m": float(offer_qc.distance_raccordement_m.max()),
        "o1_moyenne_options_par_habitant": float((pilot_counts.nb_options * pilot_counts.population).sum() / pop_total) if evaluated else None,
        "population_pilote_avec_au_moins_une_option": float(pilot_counts.loc[pilot_counts.nb_options.gt(0), "population"].sum()) if evaluated else None,
        "hypotheses": {"duree_s": args.duree_s, "vitesse_kmh": speed, "rayon_preselection_m": radius,
                        "raccord_population_max_m": args.max_raccord_population_m, "raccord_offre_max_m": args.max_raccord_offre_m,
                        "seuil_regroupement_population": args.min_population,
                        "o1": "N_i options distinctes accessibles au centre i ; groupe G : somme(P_i*N_i)/somme(P_i).",
                        "frequences": "Sommes des passages/jour des arrêts accessibles, séparées par mode, puis moyenne pondérée par population pour O1.",
                        "o2": "Somme des populations des centres accessibles, y compris hors de la commune pilote ; aucun double compte au sein d'une ligne.",
                        "selection_population": "Centre du carreau couvert par la commune ; population entière du carreau, sans prorata de surface.",
                        "raccordement": "Segment droit vers la voie la plus proche ; coût inclus au départ ET à l'arrivée. Égalités départagées par identifiant de tronçon.",
                        "regroupement": "Plus petit groupe sous le seuil fusionné vers le plus long bord commun ; voisin le plus proche à défaut ; identifiants pour départager."},
        "limitations": [
            "Population Filosofi 2021 des ménages fiscaux : estimations parfois imputées/fractionnaires, pas la population communale exhaustive.",
            "Tous les habitants d'un carreau sont représentés à son centre ; aucune desserte réelle des bâtiments n'est démontrée.",
            f"Raccords droits hypothétiques : murs, étages, passages privés et dénivelés non résolus. Seuil population {args.max_raccord_population_m} m ; offre {args.max_raccord_offre_m} m.",
            f"Calcul complet pour les fichiers fournis ; exhaustivité de l'offre non garantie. Stations vélo recensées dans le pilote : {int(o2['type'].eq('velo').sum())}.",
            "Le nombre d'options compte des points d'offre, pas des lignes distinctes ni des véhicules disponibles. Les fréquences ne sont pas des départs uniques dédupliqués entre arrêts.",
            "Les populations O2 se recouvrent entre stations : ne pas additionner les lignes pour obtenir des habitants uniques.",
            *qc_network.get("limitations", []),
        ],
        "script": {"chemin": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "versions": {"osmnx": ox.__version__, "networkx": nx.__version__, "geopandas": gpd.__version__, "pandas": pd.__version__},
        "sources": {name: {"chemin": str(path), "sha256": sha256(path)} for name, path in {
            "population": args.population, "offre": args.offre, "contours": args.contours, "graphe": graph_path,
            "rapport_reseau": qc_path, "zone_requete": zone_path,
        }.items()},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir) as temp:
        stage = Path(temp)
        for name, frame in [("gold_o1", o1), ("gold_o2", o2), ("raccordements_population", pop_qc), ("raccordements_offre", offer_qc)]:
            frame.to_parquet(stage / f"{name}.parquet", index=False)
            if name.startswith("gold_"):
                frame.drop(columns="geometry").to_csv(stage / f"{name}.csv", index=False)
                (stage / f"{name}.geojson").write_text(frame.to_crs(4326).to_json(drop_id=True), encoding="utf-8")
        pairs.to_parquet(stage / "accessibilite_carreaux_offres.parquet", index=False)
        unknown.to_parquet(stage / "paires_indeterminees.parquet", index=False)
        mapping.to_parquet(stage / "groupes_o1.parquet", index=False)
        write_preview(stage / "apercu_o1_o2.html", o1, o2, boundary, report["hypotheses"])
        (stage / "accessibilite_qc.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(args.output_dir / path.name)
    print(f"O1 : {len(o1)} groupes ; O2 : {len(o2)} offres. Sorties : {args.output_dir}", flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes-communes", nargs="+", required=True)
    ap.add_argument("--population", type=Path, default=ROOT / "data/silver/carreaux_socio.parquet")
    ap.add_argument("--offre", type=Path, default=ROOT / "data/gold/gold_offre.parquet")
    ap.add_argument("--contours", type=Path, default=ROOT / "data/silver/contours.parquet")
    ap.add_argument("--reseau-dir", type=Path, default=ROOT / "data/silver/reseau_grenoble")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data/gold/accessibilite_grenoble")
    ap.add_argument("--duree-s", type=float, default=300)
    ap.add_argument("--min-population", type=float, default=20)
    ap.add_argument("--max-raccord-population-m", type=float, default=150)
    ap.add_argument("--max-raccord-offre-m", type=float, default=50)
    return ap


if __name__ == "__main__":
    ap = parser()
    try:
        build_accessibilite(ap.parse_args())
    except (ValueError, OSError, KeyError, nx.NetworkXException) as exc:
        ap.exit(1, f"Erreur accessibilité : {exc}\n")
