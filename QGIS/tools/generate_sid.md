# Generate CoFrance SID packages

Install with [the suite installer](README.md), then open **Generate CoFrance
SID packages** in the Processing toolbox.

Enter **Airport ICAO** and **Runway configuration**, such as LFBD and 23, or
LFBO and 14L/14R. Choose **Drawing source**:

- **Reference procedures:** optionally filter by SID names or approach labels.
- **Manual ordered points:** enter points in order, such as VOR:CNA > AGN > TOU.
  Named branches use one line each: Route name | POINT1 > POINT2 > POINT3.

The LFXX_SID reference is selected automatically. Manual routes require one airport;
reference extraction can use several ICAO codes. A polygon is not used.
Unknown or ambiguous manual points stop generation before writing outputs.

Choose a **Map/output base name** and a **new or empty output folder**, then run.
The tool creates separate **Lines**, **Symbols** and **Names** GeoPackages,
an import_report.json report, and any required SVG assets. Actual layer names
match the GeoPackage filenames.

Expand **Advanced** for alternative references, an additional named point layer,
unmatched-vertex placeholders, matching tolerance, working CRS and name offsets.
Airways also offers perimeter clipping behaviour there. Defaults remain EPSG:2154,
5 m tolerance and labels shifted 3500 m north, using Arial and the text field.
Names use FIX_NAME, VOR_NAME, NDB_NAME or other semantic categories as appropriate.
Existing output data is never overwritten.

The drawings are cartographic geometry; manual connections do not calculate
ARINC paths, aircraft performance or protected areas. See the
[shared extraction reference](generate_navigation_packages.md) for data limitations
and output metadata.
