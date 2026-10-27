# Generate CoFrance trajectory points and names

This replaces the Bordeaux-specific tool. Install **generate_trajectory_points.py**
and search for **Generate CoFrance trajectory points and names**.

1. Select **Trajectory line layer**. Any FRA, airway or procedure line layer is
   accepted; there is no default Bordeaux source.
2. Optionally include its matching _Dashed companion (disabled by default).
3. Optionally select an existing named point/symbol layer and enable its use.
   It needs ident or name, and can preserve supported symbol_type values.
4. Choose points anywhere on the lines or only at stored vertices.
5. Set the matching tolerance, FIX symbol, output base name and name offset.
6. Choose a new or empty output folder and run.

Navigation masters are LFXX_VOR, LFXX_NDB and LFXX_FIX_ENR, with the option to
include FIX Procedures or use Enroute FIX only. Loaded sources take precedence
over QGIS/navdata. Shared identifiers within 1 metre are merged; existing named
points take priority. Names are never inferred from a distant master.

The output folder contains <base>_Symbols.gpkg and <base>_Names.gpkg, a matching
report and SVG artwork. Source lines and existing point packages are preserved.

Text uuid categories follow each point: FIX_NAME, VOR_NAME, NDB_NAME, DME_NAME,
TACAN_NAME or VORTAC_NAME. Names use Arial, Single Labels on text, Offset from
point placement, and a default 3500-metre northward geometry shift in EPSG:2154.
Change the offset for your map scale.

Unmatched vertices are reported with coordinates and nearest-master hints.
Enable UNNAMED placeholders to create editable symbols/labels for them. A
crossing without a stored vertex or named navigation point is not invented.

For airway or airport/runway procedure extraction that also creates route lines, use
[Generate airway, STAR and SID packages](generate_navigation_packages.md).

The script uses the shared create_points_from_list.py writer from this
repository automatically. The old generate_bordeaux_fra_points.py is retained
only as a compatibility entry point; the installer archives its old profile copy.

