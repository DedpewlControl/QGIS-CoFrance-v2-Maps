# Create CoFrance points from a list

Install **create_points_from_list.py** through **Processing Toolbox > Scripts >
Add Script to Toolbox**, then search for **Create CoFrance points from list**.

Paste identifiers or select a UTF-8 text list, choose an output base name and
a **new or empty output folder**, then run. Both output layers load into QGIS.

## Example

Paste:

```text
CNA
ABB
VOR:AGN
ENR:AAUDE
```

Spaces, newlines, commas and semicolons all separate entries. A # begins a
comment. The optional list file uses the same syntax; a .csv is accepted only
as a simple list, without a header or additional columns. Use either the pasted
list or a file, not both.

With base name **CCA_MyMap_Points**, the tool creates:

- CCA_MyMap_Points_Symbols.gpkg
- CCA_MyMap_Points_Names.gpkg
- import_report.json
- svg artwork used by the navaid symbols

Each package's actual layer name matches its filename. Symbols are at the master
point coordinates. Names contain one text point per symbol, shifted above it.

## Masters and ambiguity

Automatic lookup searches LFXX_VOR, LFXX_NDB, LFXX_FIX_ENR and LFXX_FIX_PROC.
You can restrict the default source to Navaids, FIX, or one specific master.
Loaded project layers take precedence over the corresponding files in
QGIS/navdata. Both the project layer name and the source filename identify a
master. The script is standalone; other custom Processing scripts are not needed.

A prefix overrides the default source for that entry:

| Prefix | Source |
|---|---|
| VOR: | LFXX_VOR |
| NDB: | LFXX_NDB |
| FIX: | FIX Enroute and Procedures |
| ENR: | LFXX_FIX_ENR |
| PROC: | LFXX_FIX_PROC |

Matching is exact, case insensitive, and uses the ident field. It never uses a
nearby point as a replacement for an unknown name.

If an identifier occurs at different locations, the log lists its master and
feature ID. Use a specific source and @ID to select the intended point, for
example VOR:BLV@123, replacing 123 with the ID in your log. Full master names
such as LFXX_VOR:BLV@123 are also accepted.

Co-located copies of the same identifier in FIX Enroute and FIX Procedures,
within 1 metre, count as one fix, preferring Enroute in automatic lookup.
Different locations remain ambiguous. Repeated list entries resolving to the
same point create only one symbol and one name.

By default, any missing or ambiguous entry stops the run before files are written.
**Skip and report** creates the resolved points and lists skipped entries in
import_report.json. An entirely unresolved list produces no output.

## Symbol and label styling

VOR uses a VOR symbol; NDB uses an NDB symbol. FIX points use the chosen
**FIX symbol**, defaulting to triangle_hollow. Mixed point sources can share
the same symbols GeoPackage; its renderer categorizes features by symbol_type.

Names use the cofrance_text template with Arial 10 pt, **Single Labels**, value
**text**, and **Offset from point** placement. The text point marker is hidden.

The default text geometry offset is **X = 0, Y = 3500 metres in EPSG:2154**,
matching the existing CoFrance name-generation script. Change the offset for
your map's scale. Positive Y moves the label north in the working CRS; choose
a suitable projected CRS in metres for other regions. Rotate the map canvas
and screen-up may differ from grid north. No additional display offset is added.

## Fields and exports

Both packages preserve the CoFrance schemas, activation fields and field widgets.
The symbols package has name and symbol_type; the names package has uuid and text.
The names uuid follows each point type: FIX_NAME, VOR_NAME, NDB_NAME,
DME_NAME, TACAN_NAME or VORTAC_NAME. It is independent of the package/layer name. Both packages also include ident, source_master and
source_fid for tracing the lookup.

The existing CoFrance plugin can export the generated symbols and names.
Extra tracking attributes are for QGIS and may not be emitted by the exporter.
Keep the generated svg folder beside the packages. Saved SVG paths point to the
output folder; relink them if moving the files to another location.

## Files and checks

The repository path is fixed in QGIS_DIRECTORY near the top of the script,
as in the other tools. Update it if the repository moves.

Existing folders containing files are refused. Both packages are prepared in a
staging folder and published together, so a failure while creating names cannot
leave a completed symbols package in the chosen output folder. Source masters,
input lists and existing map data are not edited.

Run test_create_points_from_list.py using QGIS Python. Integration checks cover
mixed master sources, projected source CRSs, offsets, template styles and fields,
SVG paths after publication, plugin export, duplicates, ambiguity, file lists,
existing data, cancellation, and cleanup when the second package fails.

