# Generate CoFrance point names

This QGIS Processing script creates a text GeoPackage for the FIX points,
navaids or airport reference points already placed on a map. It automatically
chooses the masters from the placed layer's name, matches coordinates, offsets
new text points and writes a copy of the empty CoFrance text template. It
requires QGIS 3.40 or later.

## Install once

1. Open **Processing > Toolbox**.
2. In the Toolbox's **Scripts** menu (Python icon), choose **Add Script to Toolbox**.
3. Select `generate_fix_names.py` from this directory.
4. Search the Toolbox for **Generate CoFrance point names**.

Use the [suite installer](README.md) to update the tools and archive the old
separate folder-names entry. The script can also be opened
and run in the Processing Script Editor.

## Run

Open **Generate CoFrance point names** and choose **Mode**:

- **Manual - one layer:** choose **Manual: placed symbols** and **Manual: new
  names GeoPackage**. The layer selector is searchable. Point type and master
  references are detected automatically.
- **Automatic - folder and subfolders:** choose **Automatic: regional folder to
  scan**. The manual layer/output fields are ignored. Only missing names
  packages are generated.

Fill only the fields for the selected mode. Expand **Advanced** for a manual
point-type override, category override, matching tolerance, working CRS or label
offsets. The CoFrance text template stays automatic.

The original algorithm ID and mode values are unchanged for existing models
and Python calls. Both workflows are available in this single toolbox entry.

### Automatic folder mode

The script scans all GeoPackages and their spatial tables beneath the selected
folder. Only recognised point symbol layers with a `symbol_type` field are used;
existing text, airspace and line layers are excluded. In the standard regional
layout, FIX and Navaid sources come from `0_CCA_...`, and ARPs come from
`4_CCA_..._VFR`.

| Sources | Destination for missing names |
|---|---|
| FIX symbols in `0_CCA_Aquitaine` | `3_CCA_Aquitaine_FIX_Names/CCA_Aquitaine_FIX_Names.gpkg` |
| Navaids in `0_CCA_Aquitaine` | `3_CCA_Aquitaine_Navaids_Names/CCA_Aquitaine_Navaids_Names.gpkg` |
| ARPs in `4_CCA_Aquitaine_VFR` | `4_CCA_Aquitaine_VFR/CCA_Aquitaine_ARP_Names.gpkg` |

Existing matching `3_..._Names` folders are reused, including singular
`Navaid_Names` and `NavAid_Names` spellings. Missing folders are created. Several
FIX symbol layers for one map, such as main, minor and significant FIX files,
are combined into one FIX names output. Several Navaid layers are combined in
the same way. Each ARP names output stays beside its source symbols. The text
category follows each point type: FIX_NAME, VOR_NAME, NDB_NAME or ARP_NAME.
DME, TACAN and VORTAC symbols use corresponding DME_NAME, TACAN_NAME and
VORTAC_NAME categories.

An existing text layer in the relevant FIX/Navaid names folder causes that
category to be skipped. In a VFR folder, only an existing ARP names file/table
counts; unrelated VFR text does not prevent ARP names generation. **Existing
layers, including empty text layers, are left unchanged.** Empty folders or
folders without a text layer receive a new output. Re-running the tool only
creates names which are still missing.

Generated files are added to the current QGIS project. The log lists created,
existing and failed outputs, and reports unmatched/ambiguous symbol points. A
failure for one output does not block other outputs. Cancellation leaves files
which were already completed. Source layers and the template are read-only.
Folder mode reads saved source data; save any symbol edits in QGIS first.

In both modes, the actual layer inside each GeoPackage and its QGIS display name
match the output filename without `.gpkg`. For example,
`CCA_La_Rochelle_ARP_names.gpkg` contains a layer named
`CCA_La_Rochelle_ARP_names`, rather than `cofrance_text`. The template's saved
style is retained. Existing files are skipped as before; this naming applies to
new outputs.

### Manual layer mode

Choose **Point type** explicitly for a generic symbol-layer name, or
leave it Automatic to detect from the layer/source name. Leave **category
override** empty for semantic categories. Mixed Navaids outputs use VOR_NAME
and NDB_NAME per feature. An explicit custom override is still accepted.
Legacy IFR_FIX_Names / IFR_FIX_NAMS values now request automatic categories.


Load the placed-symbol layer in QGIS, then open the tool. The master and master
name-field selectors have been removed; these are now automatic:

| Placed layer name contains (case insensitive) | Masters | Name field |
|---|---|---|
| `Navaids` or `Navaid` | `LFXX_NDB` + `LFXX_VOR` | `ident` |
| `VOR` | `LFXX_VOR` | `ident` |
| `NDB` | `LFXX_NDB` | `ident` |
| `FIX` | `LFXX_FIX_ENR` + `LFXX_FIX_PROC` | `ident` |
| `ARP` or `ARPs` | `LFXX_Airports` | `icao` |

The project layer's name takes priority. If it is generic (such as
`cofrance_symbols`), detection uses the GeoPackage filename. Unrecognised names
produce an explanatory error. For generic layer names, set the manual point-type selector explicitly.

The script first looks for the required masters already loaded in the project,
recognising their names or GeoPackage filenames. Otherwise it opens them from
`QGIS/navdata` or `QGIS/misc`. The airport master also has a fallback to the
existing project's `QGIS-Diagrams/QGIS/src/misc/LFXX_Airports.gpkg` source in the
adjacent repository. All required masters must be available; the script reports
any missing file rather than silently using half of a pair. The log shows which
masters were used. Loaded master-layer filters are respected.

The **Placed symbols** dropdown is searchable. Click its text, press
**Ctrl+A**, and type any part of a layer name, for example `lille` or `fix`.
Choose a matching suggestion; matching ignores capitalisation. The dropdown
lists compatible point layers, and its file browser remains available.

| Setting | Lille example |
|---|---|
| Placed symbols | `CCA_Lille_FIX.gpkg` |
| Category override | Leave empty for automatic FIX_NAME |
| Working CRS | `EPSG:2154` (RGF93 / Lambert-93) |
| Maximum matching distance | `5` metres |
| X offset | `0` metres |
| Y offset | `3500` metres |
| New text GeoPackage | A new filename, e.g. `Lille_FIX_Names_generated.gpkg` |

Click **Run**. The result is added to the project with the template's stored
style. The script automatically uses this repository's
`QGIS/templates/cofrance_text.gpkg`; there is no template selector. Its absolute
path is set in `TEXT_TEMPLATE` near the top of the script, and the master file
locations are derived from it. This also works when QGIS copies the script into
your Processing profile. Update that constant if you move the repository.
Adjust font and colour in Layer Properties > Labels as needed.
The text template defaults to **Single Labels**, value **text**, **Arial Regular
10 pt**, and **Offset from point** placement. The placement X/Y offsets default
to zero; the script's geometry offsets still determine the exported position.

The offsets move the actual text geometry. They are measured along the working
CRS axes, then converted to the template CRS (EPSG:4326 for `cofrance_text.gpkg`).
This matters because the CoFrance exporter uses geometry for text positions;
QGIS label-display offsets alone do not change the exported position. The
3500-metre default reproduces the supplied Lille labels; use other offsets for
other map layouts. It is a map-layout distance, not a fixed pixel gap at every
zoom level.

## Matching and output

- The reference is the **symbol** layer, not the existing offset names layer.
- Masters provide the individual identifiers. The symbol layer's `name` field
  can be a shared group name such as `Lille FIX`.
- Matching uses coordinates and a distance threshold in the working CRS. CRS
  conversion is automatic; all layers must have the correct source CRS assigned.
- Shared records with the same identifier within 1 metre are merged when
  combining the masters, retaining the first master's coordinates (NDB before
  VOR; Enroute before Procedures). Different identifiers remain separate.
- Multiple remaining master points inside the threshold are ambiguous and skipped. Points
  with no match are also skipped. The log lists reference feature IDs to inspect.
  Review that log before exporting; increasing the threshold can cause wrong or
  ambiguous matches.
- Repeated placements of the same master point produce one label. Distinct
  master points with the same identifier remain distinct.
- Every feature in each layer is considered, respecting layer filters and
  ignoring feature selections. Multipart point features are handled part by part.
- `text` comes from `ident` for FIX/navaids or `icao` for airports; `uuid` is the automatic point-type category unless manually overridden.
  Activation, zoom and draw-order attributes present in the template are copied
  from the reference symbol. For repeated placements, the first occurrence wins.
- Output coordinates come from the matched master point plus the offset.
- The template must be empty and contain exactly one point or multipoint table
  with `uuid` and `text` fields. Its schema and saved styles are preserved.
- Existing output files are refused. Choose a new filename on subsequent runs.

## If every reference point is skipped

Check the detected type and masters listed in the log. Matching does not find
an NDB name for an unrelated VFR point. The first five unmatched points include
the nearest master name and distance to help diagnose an incorrect layer or
coordinate system.

Use the symbol layer as the reference. For example, Aquitaine's
`cca-aquitaine-vfr-points.gpkg` already has `uuid` and `text` fields containing
labels such as `W`, `N` and `NE`; the QGIS project labels it from `text`. Export
that existing text layer directly if those are the labels you need. Increasing
the matching tolerance would not turn those VFR labels into NDB identifiers.

## Development checks

`test_generate_fix_names.py` exercises manual mode, recursive folder creation,
multiple GeoPackage tables, combining masters and symbols, skipping existing
files, regenerating only missing ARP names, preserving source/template bytes,
and continuing after a failed output. Run it with QGIS's Python environment:

```powershell
& 'C:\Program Files\QGIS 3.40.4\bin\python-qgis-ltr.bat' QGIS/tools/test_generate_fix_names.py
```
