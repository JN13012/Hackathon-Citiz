# Examen des 15 offres hors seuil — ARA, 20 octobre 2026

**Les 15 cas ont été examinés dans leurs sources GTFS/GBFS et les données OSM
brutes.** Le filtre et les distances du réseau sont reproduits à moins de
1 mm sur chaque point. Aucune erreur de construction du réseau n'a été
identifiée sur ces cas. Aucun point n'a été déplacé ni seuil augmenté.

| Conclusion | Cas | Suite |
|---|---:|---|
| Point défini dans un GTFS sans aucune référence horaire | 3 | Rôle 1 : distinguer lieu défini et offre desservie documentée |
| Voie proche exclue pour accès restreint dans OSM | 2 | Transport et géo : vérifier l'entrée ou l'autorisation piétonne |
| Écart géométrique entre position source et voies retenues | 10 | Rôles 1/4 et géo : vérifier position, accès réel et voies manquantes |

Les positions des **15 offres**, issues de **21 points sources**, sont
reproduites, y compris les regroupements. Cette fidélité ne garantit pas la
précision physique. Les 15 offres restent indéterminées pour les trajets ;
aucune correction automatique ni livraison O1/O2 n'est produite ici.

## Trois lieux sans horaires dans leurs GTFS

| Point | Source et identifiant d'origine | Références dans tous les horaires du fichier |
|---|---|---:|
| PONTON SEVRIER | `sibra_annecy.zip`, `0:1POSE` | 0 |
| PONTON DUINGT | `sibra_annecy.zip`, `0:1PODUI` | 0 |
| DEPOT ROMANS | `citea_valence.zip`, `0:DEP10` | 0 |

Ces points existent dans `stops.txt`, mais aucun horaire de `stop_times.txt`
ne les utilise, sur tout le fichier et pas seulement le jour de référence.
Le collecteur local les conserve et leur attribue `autre`, faute de course
permettant de déterminer le mode. Le
[référentiel GTFS](https://gtfs.org/documentation/schedule/reference/)
distingue la définition des lieux et les passages des courses. Ne pas
attribuer un mode « ferry » ou « bus » d'après le nom. L'absence d'horaire
dans ces fichiers ne prouve pas une absence de service sur le terrain.

Des pontons OSM sont proches : environ **0,8 m à Sevrier**, **2,3 m à Duingt**.
Ils portent `man_made=pier`, sans `highway`, et ne sont pas intégrés au graphe.
Le [modèle OSM des pontons](https://wiki.openstreetmap.org/wiki/Tag:man_made%3Dpier)
admet des objets linéaires ou surfaciques ; leur transformation systématique
en voies marchables nécessiterait une règle supplémentaire à valider et ne
résoudrait pas l'absence de desserte documentée de ces deux points.

**Action pour le rôle 1 :** garder une provenance par arrêt et distinguer les
arrêts utilisés par les horaires de ceux seulement définis. Appliquer la
règle sur l'ensemble des sources avec rapport d'impact et publication
protégée ; ne pas supprimer manuellement les trois lignes du Parquet.
Cette modification du périmètre transport n'a pas été appliquée ici.

## Deux accès restreints

- **Rue de Sète** : deux points GTFS TCL regroupés. La rue OSM à **0,45 m**
  porte `access=private` et `foot=private` (`way/35065508`, Rue de Fos-sur-Mer).
  La voie retenue est bien à **211,53 m**. Des points OSM de même nom et une
  référence TCL corroborent le lieu, sans prouver un accès public à pied.
- **Lycée La Xavière** : une voie de service à **17,47 m** porte
  `access=private` (`way/1030210508`). La voie retenue est à **57,52 m**.

Ces exclusions correspondent au filtre. Les
[tags d'accès OSM](https://wiki.openstreetmap.org/wiki/Key:access) décrivent
des restrictions ; un arrêt de bus ne permet pas de les lever automatiquement.
Transport et géo doivent vérifier l'entrée ou une autorisation particulière.
Aucune conclusion de terrain ou juridique n'est déduite du seul tag OSM.

## Dix positions à confirmer

Pour ces points, les voies autorisées les plus proches dans les extractions
OSM brutes coïncident avec celles du réseau. Aucun segment admissible plus
proche n'a été retrouvé ; un accès absent d'OSM reste possible.

| Point | Distance livrée | Distance des points source à une voie retenue | Observation |
|---|---:|---:|---|
| BEAUMIER | 52,11 m | 52,11 m | Position GTFS reproduite ; D 262 retenue |
| TROMPETTE | 78,37 m | 74,49–82,24 m | Deux points GTFS ; points OSM de même nom à environ 94–98 m du centre livré, donc positions discordantes à vérifier |
| PANETTE | 102,96 m | 84,82–121,11 m | Deux points GTFS ; Route des Virettes retenue |
| COMPLEXE SPORTIF | 76,57 m | 76,57 m | Avenue Jean Moulin retenue |
| Lycée Hôtelier Vidaude | 55,49 m | 55,49 m | Voie de service retenue ; aucun accès public plus proche trouvé |
| LES FERRANDS | 155,92 m | 149,99–161,85 m | Deux points GTFS ; D 208a retenue |
| LES PIALOUX | 58,85 m | 53,06–64,63 m | Deux points GTFS ; Route de la Raye retenue |
| CONTON | 50,32 m | 50,32 m | L'autre arrêt homonyme est à plus de 50 m et appartient à un autre groupe |
| COMBE LEORAT | 54,00 m | **35,60–60,30 m** | Le centre regroupé échoue alors qu'un arrêt source respecte le seuil |
| CARE SAMOENS - SEVE - Zoe | 58,68 m | 58,68 m | Coordonnées GBFS Clem reproduites ; Route des Drugères retenue |

**COMBE LEORAT : regroupement à revoir avec le rôle 1.** Deux arrêts séparés
d'environ 40 m sont représentés par une moyenne. Ce centre est hors seuil,
alors qu'une position source est à 35,60 m d'une voie. Examiner une
représentation des accès physiques multiples, avec lien au groupe logique
et conservation des fréquences. Choisir arbitrairement la position qui
fait passer le contrôle masquerait le problème. Toute évolution doit
propager les identifiants et recalculer les relations O1/O2 affectées.

**Clem : rôle 4 et géo.** La position correspond au relevé GBFS local
(`system_id=clem-france`, `station_id=clem-20930`). Vérifier la place et son
accès auprès de l'opérateur ; aucune position de remplacement fiable n'a été
identifiée dans les données locales. Les huit autres arrêts du tableau
concernent le rôle 1 et la géo. Une position OSM peut aussi être approximative
ou ancienne : ne pas l'utiliser comme correction automatique.

## Livraison et reproduction

Dossier `data/silver/audit_raccordements_offre_ara/` :

| Fichier | Contenu |
|---|---|
| `diagnostic_offres.csv`, `.parquet`, `.geojson` | Les 15 points, causes, rôles, actions, distances et preuves |
| `points_sources.parquet` | Les 21 points physiques, identifiants source, nombre total de références horaires et distances individuelles |
| `voies_osm_proches.parquet` | Segments à moins de 300 m des points, tags OSM et décision du filtre |
| `audit_qc.json` | Empreintes, effectifs, contrôles, limites et absence de correction automatique |

GeoParquet EPSG:2154, GeoJSON EPSG:4326 ; couches ouvrables dans QGIS.
Aucune application de restitution n'a été ajoutée.

L'extraction dans `data/bronze/osm_ara/audit_raccordements_20261020/` utilise
les deux archives OSM figées, des fenêtres de 1 km et
`osmium extract --strategy complete_ways`, puis `osmium cat` en XML.
`extraction.json` conserve les commandes et empreintes. Les limites sont
celles du réseau régional ; aucune nouvelle source OSM n'a été substituée.

```bash
python geo/audit_raccordements_offre.py \
  --gtfs 'tcl_lyon=/mnt/c/Users/Cyber/Downloads/tcl_lyon.ZIP'
```

Pour rejouer, choisir un nouveau `--output-dir` ; aucun dossier existant
n'est remplacé. Le chemin TCL est celui de WSL ; dans Docker, placer une
copie du ZIP dans un volume monté et adapter le chemin.

**97 tests passent**, dont quatre nouveaux sur la provenance des groupes,
les homonymes, lieux sans horaires, restrictions et stations partagées.
Les sorties ont été relues : 15 identifiants uniques, 21 preuves sources,
distances finies, preuve GBFS Clem, empreintes conformes et raccordements
inchangés. Journaux dans `data/silver/journaux/audit_raccordements_offre_ara.log`
et `tests_audit_raccordements_offre.log`.

Un premier essai omettant la preuve GBFS a été invalidé et archivé sous
`audit_raccordements_offre_ara_essai_incomplet_20261020` ; il ne fait pas partie
de la livraison. La version finale refuse un point sans preuve source.

Les métros, composantes secondaires et centres de population restent des
examens distincts. Le test de trajets sur Lyon peut avancer avec les statuts
actuels et les cas indéterminés explicites. Tout changement de coordonnées ou
d'offre demandera une nouvelle version des entrées et des raccordements.
