"""Prépare les limites administratives locales IGN en Lambert 93.

Exemple (fichiers IGN déjà téléchargés et décompressés) :
    python geo/build_contours.py --communes data/bronze/ign/admin.gpkg \
        --communes-layer commune --codes-communes 38185

Sans --codes-communes ni --region, sélectionne la région ARA (code 84).
Les IRIS et EPCI sont facultatifs ; leur absence figure dans le rapport.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
from shapely import union_all
from shapely.geometry import MultiPolygon, Polygon


ROOT = Path(__file__).resolve().parent.parent
CRS = "EPSG:2154"
COMMUNE_CODES = ("INSEE_COM", "CODE_INSEE", "INSEE_COMMUNE", "CODE_COMMUNE", "INSEE", "CODE")
COMMUNE_NAMES = ("NOM", "NOM_COM", "NOM_COMMUNE", "NOM_OFFICIEL")
REGION_CODES = ("INSEE_REG", "CODE_INSEE_DE_LA_REGION", "CODE_REGION", "CODE_REG", "REG")
COLUMNS = [
    "id_zone", "code_insee", "code_arrondissement_municipal", "code_iris", "code_epci",
    "nom", "niveau", "geometry",
]
CODE_PATTERNS = {
    "commune": r"(?:\d{2}|2[AB])\d{3}",
    "iris": r"(?:\d{2}|2[AB])\d{7}",
    "epci": r"\d{9}",
}


def column(frame, aliases, required=True):
    """Reconnaît les noms IGN habituels sans imposer leur casse."""
    available = {str(name).upper(): name for name in frame.columns}
    for alias in aliases:
        if alias in available:
            return available[alias]
    if required:
        raise ValueError(
            f"Colonne attendue parmi {', '.join(aliases)}. "
            f"Colonnes disponibles : {', '.join(map(str, frame.columns))}"
        )
    return None


def text_codes(values):
    # Ne pas compléter automatiquement les codes courts : un zéro initial perdu
    # doit être corrigé dans l'export source, pas deviné ici.
    return values.astype("string").str.strip().str.upper()


def read_source(path, layer=None):
    """Lit un fichier local ; refuse de choisir arbitrairement une couche."""
    if not path.is_file():
        raise ValueError(f"Fichier absent : {path}. Télécharger et décompresser les données IGN.")
    layers = []
    if path.suffix.lower() == ".parquet":
        if layer:
            raise ValueError(f"--*-layer ne s'applique pas à un Parquet : {path}")
        frame = gpd.read_parquet(path)
    else:
        layers = gpd.list_layers(path)["name"].tolist()
        if layer is None and len(layers) != 1:
            raise ValueError(f"Préciser la couche de {path} avec --*-layer : {layers}")
        if layer is not None and layer not in layers:
            raise ValueError(f"Couche {layer!r} absente de {path} : {layers}")
        layer = layer or layers[0]
        frame = gpd.read_file(path, layer=layer)
    if frame.crs is None:
        raise ValueError(f"Projection source absente dans {path} : elle ne sera pas devinée.")
    if frame.empty:
        raise ValueError(f"Fichier ou couche vide : {path}")
    frame.attrs["source_layer"] = layer
    frame.attrs["source_layers"] = layers
    return frame


def check_polygons(frame, label):
    if frame.empty:
        raise ValueError(f"Aucune géométrie sélectionnée : {label}")
    bad = (
        frame.geometry.isna()
        | frame.geometry.is_empty
        | ~frame.geometry.is_valid
        | ~frame.geom_type.isin(["Polygon", "MultiPolygon"])
    )
    if bad.any():
        raise ValueError(
            f"{label} : {int(bad.sum())} géométrie(s) absente(s), vide(s), "
            "invalide(s) ou non polygonale(s). Corriger la source avant de continuer."
        )


def normalize(frame, level, codes, names, commune_codes=None, parent_lookup=None):
    """Conserve des clés distinctes pour les trois niveaux administratifs."""
    codes = text_codes(codes)
    invalid = ~codes.str.fullmatch(CODE_PATTERNS[level], na=False)
    if invalid.any():
        raise ValueError(f"Codes {level} invalides : {codes[invalid].head(5).tolist()}")
    if codes.duplicated().any():
        raise ValueError(f"Identifiants {level} en double : {codes[codes.duplicated()].head(5).tolist()}")
    names = names.astype("string").str.strip()
    if (names.isna() | names.eq("")).any():
        raise ValueError(f"Noms absents pour le niveau {level}")
    if level == "iris":
        parent_lookup = parent_lookup or {}
        source_parents = codes.str[:5]
        parents = source_parents.replace(parent_lookup)
        observed = text_codes(commune_codes).replace(parent_lookup) if commune_codes is not None else parents
        if not observed.eq(parents).fillna(False).all():
            raise ValueError("Le code commune d'un IRIS ne correspond pas au préfixe de CODE_IRIS.")
        arrondissements = source_parents.where(source_parents.isin(parent_lookup), pd.NA)
    else:
        parents = codes if level == "commune" else pd.Series(pd.NA, index=frame.index, dtype="string")
        arrondissements = pd.NA
    result = gpd.GeoDataFrame(
        {
            "id_zone": level + ":" + codes,
            "code_insee": parents,
            "code_arrondissement_municipal": arrondissements,
            "code_iris": codes if level == "iris" else pd.NA,
            "code_epci": codes if level == "epci" else pd.NA,
            "nom": names,
            "niveau": level,
            "geometry": frame.geometry,
        },
        crs=frame.crs,
    )
    check_polygons(result, level)
    result = result.to_crs(CRS)
    check_polygons(result, f"{level} après reprojection")
    return result


def polygon_part(geometry):
    """Une intersection en bordure peut aussi contenir des lignes ou des points."""
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    parts = [polygon_part(part) for part in getattr(geometry, "geoms", [])]
    return union_all([part for part in parts if not part.is_empty])


def clip_to_territory(frame, territory):
    selected = frame.loc[frame.geometry.intersects(territory)].copy()
    clipped = gpd.GeoSeries(
        [polygon_part(geometry) for geometry in selected.geometry.intersection(territory)],
        index=selected.index,
        crs=selected.crs,
    )
    changed = int((~selected.geometry.geom_equals(clipped)).sum())
    selected.geometry = clipped
    selected = selected.loc[~selected.geometry.is_empty & (selected.geometry.area > 0)].copy()
    check_polygons(selected, "découpage au territoire")
    return selected, changed


def build_contours(args):
    sources = {}

    def load(level, path=None, layer=None):
        path = path if path is not None else getattr(args, level)
        layer = layer if layer is not None else getattr(args, f"{level}_layer")
        frame = read_source(path, layer)
        sources[level] = {
            "fichier": str(path.resolve()),
            "couche": frame.attrs["source_layer"],
            "crs_source": str(frame.crs),
            "lignes_source": len(frame),
        }
        return frame

    communes = load("communes")
    parent_lookup = {}
    if args.iris:
        arr_path, arr_layer = args.arrondissements, args.arrondissements_layer
        if arr_path is None:
            # ADMIN EXPRESS v4 fournit cette table dans le même GeoPackage.
            for name in communes.attrs["source_layers"]:
                if name.lower() == "arrondissement_municipal":
                    arr_path, arr_layer = args.communes, name
                    break
        if arr_path is not None:
            mapping = load("arrondissements", arr_path, arr_layer)
            children = text_codes(mapping[column(mapping, ("INSEE_ARM", "CODE_INSEE"))])
            parents = text_codes(mapping[column(mapping, (
                "CODE_INSEE_DE_LA_COMMUNE_DE_RATTACH", "CODE_INSEE_DE_LA_COMMUNE_DE_RATTACHEMENT",
                "INSEE_COM", "CODE_COMMUNE",
            ))])
            if (
                children.duplicated().any()
                or not children.str.fullmatch(CODE_PATTERNS["commune"], na=False).all()
                or not parents.str.fullmatch(CODE_PATTERNS["commune"], na=False).all()
            ):
                raise ValueError("Correspondance arrondissement/commune invalide ou dupliquée.")
            parent_lookup = dict(zip(children, parents))
    code_col = column(communes, COMMUNE_CODES)
    if args.codes_communes:
        wanted = set(args.codes_communes)
        selected = communes.loc[text_codes(communes[code_col]).isin(wanted)].copy()
        missing = wanted - set(text_codes(selected[code_col]))
        if missing:
            raise ValueError(f"Communes demandées absentes : {', '.join(sorted(missing))}")
        selection = {"codes_communes": sorted(wanted)}
    else:
        region = args.region or "84"
        region_col = column(communes, REGION_CODES)
        selected = communes.loc[text_codes(communes[region_col]).eq(region)].copy()
        selection = {"code_region": region}
    check_polygons(selected, "communes du territoire")
    selected = normalize(
        selected, "commune", selected[code_col], selected[column(selected, COMMUNE_NAMES)]
    )
    commune_ids = set(selected.code_insee)
    boundary = selected.geometry.union_all()
    territory = gpd.GeoDataFrame(
        {"nom": ["Territoire d'étude"], "geometry": [boundary]}, crs=CRS
    )
    check_polygons(territory, "territoire")
    frames = [selected]
    clipped_counts = {}
    missing_iris = None
    used_parent_lookup = {}

    if args.iris:
        iris = load("iris")
        iris_code = column(iris, ("CODE_IRIS", "IRIS_CODE"))
        parent_col = column(iris, COMMUNE_CODES, required=False)
        prefixes = text_codes(iris[iris_code]).str[:5]
        prefixes = prefixes.replace(parent_lookup)
        parents = text_codes(iris[parent_col]).replace(parent_lookup) if parent_col else prefixes
        iris = iris.loc[parents.isin(commune_ids) | prefixes.isin(commune_ids)].copy()
        iris = normalize(
            iris, "iris", iris[iris_code], iris[column(iris, ("NOM_IRIS", "NOM"))],
            iris[parent_col] if parent_col else None, parent_lookup,
        )
        iris, clipped_counts["iris"] = clip_to_territory(iris, boundary)
        missing_iris = sorted(commune_ids - set(iris.code_insee))
        used_parent_lookup = {
            code: parent_lookup[code]
            for code in sorted(iris.code_arrondissement_municipal.dropna().unique())
        }
        frames.append(iris)

    if args.epci:
        epci = load("epci")
        epci = normalize(
            epci, "epci", epci[column(epci, ("CODE_SIREN", "SIREN_EPCI", "CODE_EPCI", "SIREN"))],
            epci[column(epci, ("NOM", "NOM_EPCI", "NOM_OFFICIEL"))],
        )
        epci, clipped_counts["epci"] = clip_to_territory(epci, boundary)
        frames.append(epci)

    contours = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)
    contours = contours[COLUMNS].sort_values(["niveau", "id_zone"]).reset_index(drop=True)
    for name in COLUMNS[:-1]:
        contours[name] = contours[name].astype("string")
    report = {
        "date_execution_utc": datetime.now(timezone.utc).isoformat(),
        "selection": selection,
        "millesime_declare": args.millesime,
        "sources": sources,
        "crs_sortie": CRS,
        "effectifs": {str(k): int(v) for k, v in contours.niveau.value_counts().items()},
        "niveaux_non_fournis": [level for level in ("iris", "epci") if getattr(args, level) is None],
        "communes_sans_iris": missing_iris,
        "rattachements_arrondissements_municipaux": used_parent_lookup,
        "geometries_redecoupees_au_territoire": clipped_counts,
        "surface_territoire_km2": float(territory.geometry.area.sum() / 1_000_000),
        "geometries_invalides": int((~contours.geometry.is_valid).sum()),
        "identifiants_dupliques": int(contours.id_zone.duplicated().sum()),
    }
    outputs = ("contours.parquet", "territoire.parquet", "contours_qc.json")
    for level in ("communes", "iris", "epci", "arrondissements"):
        source = getattr(args, level)
        if source and source.resolve() in {(args.output_dir / name).resolve() for name in outputs}:
            raise ValueError("Un fichier de sortie ne peut pas remplacer un fichier source.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Préparer tous les fichiers avant de remplacer les anciennes sorties.
    with TemporaryDirectory(prefix="contours-", dir=args.output_dir) as staging:
        staging = Path(staging)
        contours.to_parquet(staging / outputs[0], index=False)
        territory.to_parquet(staging / outputs[1], index=False)
        (staging / outputs[2]).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for name in outputs:
            (staging / name).replace(args.output_dir / name)
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--communes", type=Path, required=True, help="couche locale COMMUNE d'ADMIN EXPRESS")
    ap.add_argument("--communes-layer", help="nom de couche si le fichier contient plusieurs couches")
    ap.add_argument("--iris", type=Path, help="couche locale Contours IRIS (facultative)")
    ap.add_argument("--iris-layer")
    ap.add_argument("--epci", type=Path, help="couche locale EPCI (facultative)")
    ap.add_argument("--epci-layer")
    ap.add_argument("--arrondissements", type=Path, help="correspondance officielle arrondissement/commune, sinon détectée dans le GeoPackage des communes")
    ap.add_argument("--arrondissements-layer")
    territory = ap.add_mutually_exclusive_group()
    territory.add_argument("--region", help="code région IGN, 84 (ARA) par défaut")
    territory.add_argument("--codes-communes", nargs="+", help="codes INSEE, par exemple 38185 pour Grenoble")
    ap.add_argument("--millesime", help="millésime déclaré des sources, sinon non renseigné dans le rapport")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "data" / "silver")
    return ap


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    for level in ("iris", "epci", "arrondissements"):
        if getattr(args, f"{level}_layer") and getattr(args, level) is None:
            ap.error(f"--{level}-layer nécessite --{level}")
    if args.region and not re.fullmatch(r"\d{2}", args.region):
        ap.error("--region attend un code à deux chiffres, par exemple 84")
    if args.codes_communes and any(
        not re.fullmatch(CODE_PATTERNS["commune"], code) for code in args.codes_communes
    ):
        ap.error("--codes-communes attend des codes INSEE complets, par exemple 01001 ou 38185")
    try:
        report = build_contours(args)
    except (ValueError, OSError, RuntimeError) as exc:
        ap.exit(1, f"Erreur : {exc}\n")
    print(f"Contours écrits dans {args.output_dir}")
    print(f"Effectifs : {report['effectifs']}")
    if report["niveaux_non_fournis"]:
        print("Niveaux non fournis : " + ", ".join(report["niveaux_non_fournis"]))
    if report["communes_sans_iris"]:
        print(f"Attention : {len(report['communes_sans_iris'])} commune(s) sans IRIS dans les données fournies.")


if __name__ == "__main__":
    main()
