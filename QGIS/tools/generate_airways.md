# Generate CoFrance airway packages

Install with [the suite installer](README.md), then open **Generate CoFrance
airway packages** in the Processing toolbox.

Choose **Airway level** (High or Low) and **Selection method**:

- **Polygon perimeter:** select a polygon layer; optionally filter by airway identifiers.
- **Manual airway identifier list:** enter identifiers such as A2, B3; no polygon is required.

Perimeter mode clips the lines by default and limits point anchors to the perimeter.
List mode keeps complete matching routes. Wildcards are supported in identifier filters.
The LFXX_HIGH_AWY or LFXX_LOW_AWY reference is selected automatically.

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
