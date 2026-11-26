# Generate CoFrance runway packages

Install with the [suite installer](README.md), then search Processing for
**Generate CoFrance runway packages**.

Choose **Polygon perimeter** or a manual identifier/ICAO list, enter a map/output
base name and choose a **new or empty output folder**. Click Run to create
**Lines only**, plus an import_report.json report. A perimeter is not required in
manual mode. Perimeter mode ignores a previously entered manual list.

The automatic reference is **LFXX_Runways**. Loaded project references are preferred.
Navaids fall back to QGIS/navdata; Airports and Runways also check QGIS/misc and
the QGIS-Diagrams reference paths already used by this project. Load the masters
in QGIS if those paths are unavailable. Airport/Runway tools also provide an
optional searchable reference override in Advanced.

Perimeter mode selects runway lines intersecting the polygon and keeps the
complete runway by default. Advanced offers optional clipping to the perimeter,
including its holes. Manual mode accepts airport identifiers such as LFBD, LFBO
and keeps complete runway geometry.

Optional **Runway ends** filters match either end, for example 05, 23 or 05/23.
Parallel-runway suffixes L/R/C are supported. The reference must have rwy1/rwy2
or a runway field to use this filter. A manual airport with no matching runway
stops the run before writing any files.

Output: <base>_Runways_Lines.gpkg. No symbol or name GeoPackage is generated.
Airport, runway ends and original reference attributes are recorded in the line
metadata; the template's z-index, zoom and activation fields remain available.

Selected perimeter features and reference layer filters are respected. Polygon
holes are retained. No source package is edited and existing output data is
never overwritten. GeoPackage table/layer names match the filenames. Outputs
load into QGIS after completion. If writing fails before publication, the
staging outputs are discarded together.

The working CRS defaults to EPSG:2154. Lines use the CoFrance line template
and a saved grey 1-pixel style, which you can adjust after generation.
