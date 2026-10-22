#!/bin/bash
# Réseau piéton et accès à 5 min de la France métropolitaine, région par région, puis assemblage.
# Lancé par python scripts/run_all.py --territoire france, après les tables silver France et l'OSM
# France (data/bronze/osm_france/) ; osmium-tool (Mac : brew install osmium-tool) ou l'image Docker du projet.
# Reprise possible : les étapes déjà faites sont sautées. Un échec de région n'arrête pas les suivantes.
# Usage : scripts/run_regions_france.sh [codes région…]     (défaut : les 13 régions)
#         RUNNER=docker scripts/run_regions_france.sh 84 76   (calcul dans le conteneur, mémoire limitée)
# Journal d'avancement : data/france/journaux/progression.txt
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
J=data/france/journaux; mkdir -p $J
PY_BIN=${PYTHON:-.venv/bin/python}  # interpréteur du projet (run_all.py le transmet)
if [ "${RUNNER:-local}" = docker ]; then
  RUN=(docker compose run --rm -T -e TERRITOIRE=france -e PYTHONUNBUFFERED=1 -v "$ROOT/geo:/app/geo:ro"
       -v "$ROOT/optim:/app/optim:ro" -v "$ROOT/scripts:/app/scripts:ro" -v "$ROOT/model:/app/model:ro" pipeline python)
  export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)
else
  RUN=(env TERRITOIRE=france PYTHONUNBUFFERED=1 "$PY_BIN")
fi
PY=(env TERRITOIRE=france PYTHONUNBUFFERED=1 "$PY_BIN")  # tables lourdes : toujours sur la machine
log() { echo "$(date +%T) $*" | tee -a $J/progression.txt; }
REGIONS=("$@"); [ ${#REGIONS[@]} -eq 0 ] && REGIONS=(11 24 27 28 32 44 52 53 75 76 84 93 94)

for C in "${REGIONS[@]}"; do
  R=data/france/silver/regions/$C; G=data/france/gold/regions/$C; t0=$(date +%s)
  P=(--preparation $R/preparation_reseau --reseau $R/reseau)
  # 1. découpe des tables (carreaux rattachés par leur centre) et extrait OSM de la région
  [ -f $R/decoupage_qc.json ] || "${PY[@]}" geo/decouper_region.py --region $C > $J/decoupe$C.log 2>&1 \
    || { log "$C ÉCHEC découpe"; continue; }
  [ -f data/france/bronze/osm_regions/region-$C.osm.pbf ] || "${RUN[@]}" geo/decouper_region.py --region $C --osm-seulement \
    > $J/osm$C.log 2>&1 || { log "$C ÉCHEC extrait OSM"; continue; }
  # 2. plan de tuiles (offres jusqu'à 1 km au-delà de la limite, tolérance du trait de côte 200 m)
  [ -d $R/preparation_reseau ] || "${PY[@]}" geo/prepare_reseau_ara.py --arrets $R/arrets.parquet \
    --stations $R/stations_partagees.parquet --population $R/carreaux_socio.parquet --contours $R/contours.parquet \
    --territoire $R/territoire.parquet --osm-dir data/france/bronze/osm_regions --archives region-$C --region $C \
    --marge-offre-m 1000 --tolerance-population-m 200 --reseaux-requis --output-dir $R/preparation_reseau \
    > $J/prep$C.log 2>&1 || { log "$C ÉCHEC préparation"; continue; }
  # 3. réseau piéton par tuiles, puis contrôle indépendant
  if ! grep -q regional_termine $R/reseau/construction_reseau_ara.json 2>/dev/null; then
    extra=(); [ -d $R/reseau ] && extra=(--reprendre)
    log "$C début réseau"
    "${RUN[@]}" geo/run_reseau_ara.py "${P[@]}" --osm-dir data/france/bronze/osm_regions \
      --cache-dir data/france/bronze/osm_regions/cache_reseau ${extra[@]+"${extra[@]}"} > $J/reseau$C.log 2>&1 \
      || { log "$C ÉCHEC réseau"; continue; }
  fi
  [ -f $R/reseau/controle_independant.json ] || "${PY[@]}" geo/controle_reseau.py "${P[@]}" --workers 6 \
    > $J/controle$C.log 2>&1 || { log "$C ÉCHEC contrôle réseau"; continue; }
  # 4. raccordements, accès à 5 min (O1/O2, paires carreau-offre) et couverture des candidats
  [ -f $R/raccordements/raccordements_qc.json ] || { rm -rf $R/raccordements; "${RUN[@]}" geo/check_raccordements_ara.py \
    "${P[@]}" --output-dir $R/raccordements > $J/racc$C.log 2>&1; } || { log "$C ÉCHEC raccordements"; continue; }
  [ -f $G/accessibilite/resultats/accessibilite_qc.json ] || "${RUN[@]}" geo/build_accessibilite_ara.py "${P[@]}" \
    --raccordements $R/raccordements --sans-acces-stations --output-dir $G/accessibilite --workers 2 \
    > $J/acces$C.log 2>&1 || { log "$C ÉCHEC accès"; continue; }
  [ -f $G/couverture/resultats/couverture_qc.json ] || "${RUN[@]}" geo/build_couverture.py "${P[@]}" \
    --raccordements $R/raccordements --output-dir $G/couverture --workers 2 > $J/couv$C.log 2>&1 \
    || { log "$C ÉCHEC couverture"; continue; }
  log "$C TERMINÉ en $(( ($(date +%s)-t0)/60 )) min"
done
# 5. tables France (refus si une région manque)
"${PY[@]}" geo/assembler_regions.py > $J/assemblage.log 2>&1 && log "assemblage France fait" || log "assemblage refusé : voir $J/assemblage.log"
