"""Calcule des isochrones piétons sur le réseau local, sans téléchargement.

Les chemins atteignables sont exacts pour le modèle déclaré. Leur tampon
cartographique représente une approximation de surface, pas un accès au bâti.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
from pyproj import CRS as Projection
from shapely import STRtree, distance, get_point, reverse, union_all
from shapely.geometry import LineString
from shapely.ops import substring


ROOT = Path(__file__).resolve().parent.parent
CRS = "EPSG:2154"
EPS = 1e-6


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_origins(args):
    origins = gpd.read_parquet(args.origines)
    contours = gpd.read_parquet(args.contours)
    if origins.crs is None or contours.crs is None:
        raise ValueError("Projection absente des origines ou des contours.")
    if args.id_col not in origins:
        raise ValueError(f"Colonne d'identifiant absente : {args.id_col}")
    ids = origins[args.id_col].astype("string").str.strip()
    if ids.isna().any() or ids.eq("").any() or ids.duplicated().any():
        raise ValueError("Identifiants d'origine absents, vides ou dupliqués.")
    if origins.empty or not origins.geom_type.eq("Point").all():
        raise ValueError("Les origines doivent être des points non vides.")
    if (origins.geometry.isna() | origins.geometry.is_empty | ~origins.geometry.is_valid).any():
        raise ValueError("Géométries d'origine absentes, vides ou invalides.")
    if not {"niveau", "code_insee"}.issubset(contours.columns):
        raise ValueError("Les contours doivent contenir niveau et code_insee.")
    selected = contours.loc[(contours.niveau == "commune") & contours.code_insee.isin(args.codes_communes)]
    missing = set(args.codes_communes) - set(selected.code_insee)
    if missing or selected.code_insee.duplicated().any():
        raise ValueError(f"Sélection de communes absente ou dupliquée : {sorted(missing)}")
    if (selected.geometry.isna() | selected.geometry.is_empty | ~selected.geometry.is_valid).any() or not selected.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Les contours sélectionnés doivent être des polygones valides.")
    boundary = union_all(selected.to_crs(CRS).geometry)
    origins = origins.to_crs(CRS)
    if not np.isfinite(np.column_stack([origins.geometry.x, origins.geometry.y])).all():
        raise ValueError("Coordonnées d'origine non finies après reprojection.")
    origins = gpd.GeoDataFrame({
        "origine_id": ids,
        "nom_origine": origins["nom"].fillna("").astype(str) if "nom" in origins else ids,
        "geometry": origins.geometry,
    }, crs=CRS)
    count = len(origins)
    origins = origins.loc[origins.geometry.covered_by(boundary)].copy().reset_index(drop=True)
    if origins.empty:
        raise ValueError("Aucune origine dans les communes sélectionnées.")
    return origins, boundary, count


class WalkingNetwork:
    """Index spatial et poids de marche validés une seule fois pour toutes les origines."""

    def __init__(self, graph, speed_kmh):
        if not math.isfinite(speed_kmh) or speed_kmh <= 0:
            raise ValueError("Vitesse de marche absente ou invalide.")
        if not isinstance(graph, nx.MultiDiGraph) or not graph.edges:
            raise ValueError("Le réseau doit être un MultiDiGraph non vide.")
        if graph.graph.get("network_type") != "walk":
            raise ValueError("Le graphe doit être issu du réseau walk de build_reseau.py.")
        if Projection.from_user_input(graph.graph.get("crs")) != Projection.from_epsg(2154):
            raise ValueError("Le réseau doit être en EPSG:2154.")
        self.graph = graph
        self.speed_ms = speed_kmh / 3.6
        nodes, edges = ox.graph_to_gdfs(graph)
        xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
        if not np.isfinite(xy).all():
            raise ValueError("Coordonnées non finies dans le réseau.")
        if edges.geometry.isna().any() or edges.geometry.is_empty.any() or not edges.geometry.is_valid.all() or not edges.geom_type.eq("LineString").all():
            raise ValueError("Géométries de tronçons invalides.")
        self.keys = list(edges.index)
        geometries = edges.geometry.to_numpy()
        starts = get_point(geometries, 0)
        ends = get_point(geometries, -1)
        points_u = nodes.geometry.reindex(edges.index.get_level_values(0)).to_numpy()
        points_v = nodes.geometry.reindex(edges.index.get_level_values(1)).to_numpy()
        # OSMnx peut stocker la même ligne dans les deux sens. La remettre dans
        # le sens u -> v est indispensable pour couper le bon bout du tronçon.
        forward = (distance(starts, points_u) < 0.001) & (distance(ends, points_v) < 0.001)
        backward = (distance(ends, points_u) < 0.001) & (distance(starts, points_v) < 0.001)
        if not (forward | backward).all():
            raise ValueError("Une extrémité de tronçon ne correspond pas à son nœud.")
        self.lines = np.where(forward, geometries, reverse(geometries))
        self.by_key = dict(zip(self.keys, self.lines))
        for (u, v, key), line in zip(self.keys, self.lines):
            data = graph[u][v][key]
            length, duration = float(data.get("longueur_m", math.nan)), float(data.get("temps_pieton_s", math.nan))
            if not math.isfinite(length) or length <= 0 or not math.isfinite(duration) or duration <= 0:
                raise ValueError("Poids de marche absents, non finis ou non positifs.")
            if not math.isclose(length, line.length, abs_tol=0.001) or not math.isclose(duration, length / self.speed_ms, abs_tol=0.001):
                raise ValueError("Poids de marche incohérents avec la vitesse et la géométrie.")
            if not graph.has_edge(v, u):
                raise ValueError("Le modèle attend un réseau piéton bidirectionnel.")
        self.tree = STRtree(self.lines)

    def reachable(self, point, budget_s=300, max_snap_m=50):
        matches, distances = self.tree.query_nearest(point, all_matches=True, return_distance=True)
        index = min(matches, key=lambda i: self.keys[i])
        line, (u, v, key) = self.lines[index], self.keys[index]
        distance_m = float(distances[0])
        position = line.project(point)
        snap = line.interpolate(position)
        access_s = distance_m / self.speed_ms
        result = {
            "statut": "ok", "distance_raccordement_m": distance_m, "temps_acces_s": access_s,
            "raccord_de": int(u), "raccord_vers": int(v), "raccord_cle": int(key),
            "abscisse_raccord_m": position, "raccord_x": snap.x, "raccord_y": snap.y,
            "nb_noeuds_atteints": 0, "temps_max_portions_s": None,
        }
        if distance_m > max_snap_m:
            result["statut"] = "raccordement_trop_loin"
            return result, None
        if access_s >= budget_s:
            result["statut"] = "budget_acces_epuise"
            return result, None
        # Une origine virtuelle relie les deux extrémités de la voie avec le
        # vrai coût depuis la projection, et le coût du raccordement hors réseau.
        virtual = object()
        graph = self.graph
        try:
            graph.add_edge(virtual, u, temps_pieton_s=access_s + position / self.speed_ms)
            graph.add_edge(virtual, v, temps_pieton_s=access_s + (line.length - position) / self.speed_ms)
            reached = nx.single_source_dijkstra_path_length(graph, virtual, cutoff=budget_s, weight="temps_pieton_s")
            reached.pop(virtual)
        finally:
            if virtual in graph:
                graph.remove_node(virtual)
        parts, max_time = [], access_s
        remaining_m = (budget_s - access_s) * self.speed_ms
        # Cette portion existe même si les deux extrémités d'une longue voie
        # restent hors budget. Un algorithme limité aux nœuds la perdrait.
        start, end = max(0, position - remaining_m), min(line.length, position + remaining_m)
        if end - start > EPS:
            parts.append(substring(line, start, end))
            max_time = max(max_time, access_s + max(position - start, end - position) / self.speed_ms)
        for node, elapsed in reached.items():
            for a, b, edge_key in graph.out_edges(node, keys=True):
                edge_line = self.by_key[(a, b, edge_key)]
                length = min(edge_line.length, max(0, budget_s - elapsed) * self.speed_ms)
                if length > EPS:
                    parts.append(substring(edge_line, 0, length))
                    max_time = max(max_time, elapsed + length / self.speed_ms)
        if distance_m > EPS:
            parts.append(LineString([point, snap]))
        result["nb_noeuds_atteints"] = len(reached)
        result["temps_max_portions_s"] = max_time
        return result, union_all(parts)


def write_preview(path, polygons, paths, origins, boundary):
    """Aperçu autonome ; simplification de 1 m limitée à l'affichage."""
    points = origins.set_index("origine_id").geometry
    lines = paths.set_index("origine_id").geometry
    payload = {
        "territoire": boundary.simplify(1).__geo_interface__, "bounds": boundary.bounds,
        "rows": [{
            "id": row.origine_id, "nom": row.nom_origine,
            "polygone": row.geometry.simplify(1).__geo_interface__,
            "chemins": lines.loc[row.origine_id].simplify(1).__geo_interface__,
            "point": list(points.loc[row.origine_id].coords)[0], "bounds": row.geometry.bounds,
            "temps": row.duree_s, "vitesse": row.vitesse_kmh, "acces": row.temps_acces_s,
        } for row in polygons.itertuples(index=False)],
    }
    html = """<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Isochrones piétonnes — aperçu</title>
<style>body{font:16px system-ui;margin:20px;color:#182633}h1{font-size:24px}select,button{padding:8px;margin:4px}svg{width:100%;height:68vh;background:#f6f8fa;border:1px solid #ccd5dc}p{max-width:1000px}.note{font-size:14px;color:#46545f}</style>
<h1>Zones accessibles à pied — pilote</h1><label>Origine <select id="origin"></select></label><button id="city">Vue du territoire</button><p id="info"></p>
<svg id="map" role="img" aria-label="Chemins atteignables et représentation polygonale"><path id="boundary" fill="none" stroke="#94a3b8" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
<path id="area" fill="#73bc9a" fill-opacity=".45" fill-rule="evenodd"/><path id="roads" fill="none" stroke="#166b51" stroke-width="1" vector-effect="non-scaling-stroke"/><circle id="point" r="5" fill="#225bcc"/></svg>
<p class="note">Bleu : départ. Vert foncé : chemins atteignables. Vert clair : tampon cartographique de ces chemins. Le raccordement droit au réseau est une hypothèse ; les polygones ne démontrent pas l'accès aux bâtiments. Le détail du modèle et les éventuelles origines exclues figurent dans isochrones_qc.json. Aperçu sans fond de carte ; géométries d'affichage simplifiées à 1 m.</p>
<p class="note">Sources : IGN ; © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> (ODbL).</p>
<script id="data" type="application/json">__DATA__</script><script>
(()=>{const data=JSON.parse(document.getElementById('data').textContent), select=document.getElementById('origin'), svg=document.getElementById('map');
function path(g){if(g.type==='Polygon')return g.coordinates.map(r=>r.map((p,i)=>(i?'L':'M')+p[0]+' '+(-p[1])).join(' ')+'Z').join(' ');if(g.type==='MultiPolygon')return g.coordinates.map(c=>path({type:'Polygon',coordinates:c})).join(' ');if(g.type==='LineString')return g.coordinates.map((p,i)=>(i?'L':'M')+p[0]+' '+(-p[1])).join(' ');if(g.type==='MultiLineString')return g.coordinates.map(c=>path({type:'LineString',coordinates:c})).join(' ');return '';}
function fit(b){const pad=30;svg.setAttribute('viewBox',[b[0]-pad,-b[3]-pad,b[2]-b[0]+2*pad,b[3]-b[1]+2*pad].join(' '));}
document.getElementById('boundary').setAttribute('d',path(data.territoire));
if(!data.rows.length){document.getElementById('info').textContent='Aucune isochrone calculée : consulter le rapport de qualité.';fit(data.bounds);return;}
for(const [i,row] of data.rows.entries()){const option=document.createElement('option');option.value=i;option.textContent=row.nom+' · '+row.id;select.appendChild(option);}
function draw(){const row=data.rows[Number(select.value)];document.getElementById('area').setAttribute('d',path(row.polygone));document.getElementById('roads').setAttribute('d',path(row.chemins));const p=document.getElementById('point');p.setAttribute('cx',row.point[0]);p.setAttribute('cy',-row.point[1]);document.getElementById('info').textContent=row.temps+' secondes · '+row.vitesse+' km/h · accès au réseau : '+row.acces.toFixed(1)+' s';fit(row.bounds);}
select.addEventListener('change',draw);document.getElementById('city').addEventListener('click',()=>fit(data.bounds));draw();})();
</script></html>"""
    path.write_text(html.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")), encoding="utf-8")


def build_isochrones(args):
    for value, name, allow_zero in [(args.duree_s, "durée", False), (args.tampon_m, "tampon", False), (args.max_raccord_m, "raccordement", True)]:
        if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
            raise ValueError(f"Valeur invalide pour {name}.")
    origins, boundary, total = read_origins(args)
    qc_path = args.reseau_dir / "reseau_pieton_qc.json"
    graph_path = args.reseau_dir / "reseau_pieton.graphml"
    network_qc = json.loads(qc_path.read_text(encoding="utf-8"))
    covered_codes = network_qc.get("codes_communes")
    if covered_codes is not None and not set(args.codes_communes).issubset(covered_codes):
        raise ValueError("Les communes demandées ne sont pas couvertes par ce réseau.")
    speed = float(network_qc["vitesse_kmh_hypothese"])
    print(f"Chargement du réseau ; {len(origins)} origines sélectionnées sur {total}.", flush=True)
    graph = ox.load_graphml(graph_path, edge_dtypes={"longueur_m": float, "temps_pieton_s": float}, node_dtypes={"composante_id": int})
    network = WalkingNetwork(graph, speed)
    zone = gpd.read_parquet(args.reseau_dir / "zone_requete.parquet")
    if zone.crs is None or zone.empty or not zone.geometry.is_valid.all() or not zone.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Zone du réseau absente ou invalide.")
    zone_geometry = union_all(zone.to_crs(CRS).geometry)
    zone_hash = network_qc.get("acquisition_origine", {}).get("zone_sha256")
    if zone_hash and (zone_hash != graph.graph.get("zone_sha256") or zone_hash != hashlib.sha256(zone_geometry.wkb).hexdigest()):
        raise ValueError("Le graphe, la zone et le rapport réseau ne proviennent pas de la même extraction.")
    rows, polygons, paths = [], [], []
    for index, origin in enumerate(origins.itertuples(index=False), start=1):
        result, lines = network.reachable(origin.geometry, args.duree_s, args.max_raccord_m)
        result.update({
            "origine_id": origin.origine_id, "type_origine": args.type_origine,
            "nom_origine": origin.nom_origine,
            "duree_s": args.duree_s, "vitesse_kmh": speed,
            "proche_limite_reseau": not zone_geometry.covers(origin.geometry.buffer(args.duree_s * network.speed_ms)),
        })
        if args.id_col in {"id_carreau", "arret_id", "station_id"}:
            result[args.id_col] = origin.origine_id
        rows.append({**result, "geometry": origin.geometry})
        if lines is not None:
            polygon = lines.buffer(args.tampon_m)
            if polygon.is_empty or not polygon.is_valid or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError(f"Isochrone invalide : {origin.origine_id}")
            polygons.append({**result, "tampon_m": args.tampon_m, "surface_m2": polygon.area, "geometry": polygon})
            paths.append({**result, "geometry": lines})
        if index % 25 == 0 or index == len(origins):
            print(f"Isochrones : {index}/{len(origins)} origines traitées.", flush=True)
    origin_export = gpd.GeoDataFrame(rows, crs=CRS)
    # Même schéma de colonnes quand aucune origine ne peut être raccordée.
    path_export = gpd.GeoDataFrame(paths, columns=origin_export.columns, crs=CRS)
    polygon_columns = [*origin_export.columns.drop("geometry"), "tampon_m", "surface_m2", "geometry"]
    polygon_export = gpd.GeoDataFrame(polygons, columns=polygon_columns, crs=CRS)
    report = {
        "execution_utc": datetime.now(timezone.utc).isoformat(), "crs": CRS,
        "etat": "complet" if len(polygons) == len(origins) else "partiel",
        "type_origine": args.type_origine, "codes_communes": sorted(set(args.codes_communes)),
        "colonne_identifiant_source": args.id_col,
        "origines_entree": total, "origines_hors_selection": total - len(origins),
        "origines_selectionnees": len(origins), "isochrones_produites": len(polygons),
        "statuts": origin_export.statut.value_counts().to_dict(),
        "origines_non_calculees": origin_export.loc[origin_export.statut != "ok", ["origine_id", "statut", "distance_raccordement_m"]].to_dict("records"),
        "origines_proches_limite_reseau": int(origin_export.proche_limite_reseau.sum()),
        "duree_s": args.duree_s, "vitesse_kmh_hypothese": speed,
        "max_raccord_m_hypothese": args.max_raccord_m, "tampon_cartographique_m": args.tampon_m,
        "distance_raccordement_max_m": float(origin_export.distance_raccordement_m.max()),
        "geometries_invalides": int((~polygon_export.geometry.is_valid).sum()),
        "sources": {name: {"chemin": str(path), "sha256": sha256(path)} for name, path in {
            "origines": args.origines, "contours": args.contours, "graphe": graph_path,
            "rapport_reseau": qc_path, "zone_requete": args.reseau_dir / "zone_requete.parquet",
        }.items()},
        "modele": "Projection sur le tronçon le plus proche, coût d'accès euclidien, Dijkstra en secondes et découpe des tronçons à la limite du budget.",
        "limitations": [
            "Le raccordement droit au réseau est une hypothèse : murs, dénivelés et ouvrages superposés ne sont pas résolus.",
            "Le polygone est le tampon des chemins atteignables : approximation cartographique, sans accès démontré aux bâtiments ou aux parcelles.",
            "Le tampon peut dépasser les extrémités atteignables et franchir un obstacle de faible largeur ; utiliser les chemins et le routage pour les mesures d'accessibilité.",
            "Les origines proches de la limite peuvent avoir une isochrone tronquée par l'extraction du réseau.",
            *network_qc.get("limitations", []),
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir) as temp:
        stage = Path(temp)
        origin_export.to_parquet(stage / "origines_qc.parquet", index=False)
        path_export.to_parquet(stage / "chemins_accessibles.parquet", index=False)
        polygon_export.to_parquet(stage / "gold_isochrones.parquet", index=False)
        (stage / "gold_isochrones.geojson").write_text(polygon_export.to_crs(4326).to_json(drop_id=True), encoding="utf-8")
        write_preview(stage / "apercu_isochrones.html", polygon_export, path_export, origin_export, boundary)
        (stage / "isochrones_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(args.output_dir / path.name)
    print(f"Écrit dans {args.output_dir} : {len(polygons)} isochrones, {len(origins) - len(polygons)} origine(s) non calculée(s).", flush=True)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origines", type=Path, required=True, help="GeoParquet de points (arrêts, stations ou candidats)")
    ap.add_argument("--id-col", required=True, help="identifiant unique de la source, conservé comme texte")
    ap.add_argument("--type-origine", required=True, choices=["arret", "station", "candidat", "autre"])
    ap.add_argument("--codes-communes", nargs="+", required=True)
    ap.add_argument("--contours", type=Path, default=ROOT / "data/silver/contours.parquet")
    ap.add_argument("--reseau-dir", type=Path, default=ROOT / "data/silver/reseau_grenoble")
    ap.add_argument("--output-dir", type=Path, required=True, help="dossier dédié au périmètre et au type d'origine")
    ap.add_argument("--duree-s", type=float, default=300)
    ap.add_argument("--max-raccord-m", type=float, default=50)
    ap.add_argument("--tampon-m", type=float, default=10, help="largeur de représentation de chaque côté des chemins")
    return ap


if __name__ == "__main__":
    ap = parser()
    try:
        build_isochrones(ap.parse_args())
    except (ValueError, OSError, KeyError, nx.NetworkXException) as exc:
        ap.exit(1, f"Erreur isochrones : {exc}\n")
