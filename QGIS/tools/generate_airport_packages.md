# Generate CoFrance airport packages

Install with the [suite installer](README.md), then search Processing for
**Generate CoFrance airport packages**.

Choose **Polygon perimeter** or a manual identifier/ICAO list, enter a map/output
base name and choose a **new or empty output folder**. Click Run to create
**Symbols and Names**, plus an import_report.json report. A perimeter is not required in
manual mode. Perimeter mode ignores a previously entered manual list.

The automatic reference is **LFXX_Airports**. Loaded project references are preferred.
Navaids fall back to QGIS/navdata; Airports and Runways also check QGIS/misc and
the QGIS-Diagrams reference paths already used by this project. Load the masters
in QGIS if those paths are unavailable. Airport/Runway tools also provide an
optional searchable reference override in Advanced.

Perimeter mode selects airport reference points inside or on the polygon.
Manual mode accepts airport identifiers such as LFBD, LFBO. Names use the ICAO
field, not a full airport name.

**Airport symbol selection** defaults to **Automatic: SIA AD 2.12 runway surfaces**.
The script reads only the selected airports' AD 2 runway physical-characteristics
tables from the SIA edition in Advanced (default: AIRAC 2026-10-01):

| Published runways | Symbol |
|---|---|
| Any paved runway, above, below or exactly 1000 m | aerodrome_paved_ticks |
| All runways unpaved, including grass/landing strips | aerodrome_ticks |

An airport with paved and grass runways receives the paved symbol. Both requested
paved length bands use the same symbol; runway length does not change that symbol.
Runway lengths, published surfaces, source URL, AIRAC cycle and download/cache
status are recorded in import_report.json. Symbol/name features also retain
airport_surface, runway_length_m (the longest published runway), and type_source.

The first automatic run needs internet access; each airport page is cached for
24 hours in the QGIS profile under processing/cofrance_cache. Cached data belongs
to its exact edition and airport. If a normal refresh fails, valid cached data
from that edition may be reused with a warning. **Force refresh** fails instead
of reusing stale data. Canceling does not publish output packages.

For offline use, Advanced > **Optional saved SIA AD 2 HTML folder** accepts saved
pages named FR-AD-2.LFBD-fr-FR.html, etc. Those pages must be from the edition
selected by SIA AIRAC edition index URL; the script cannot verify the edition of
a renamed local file. Save the actual airport page, not the index/frames page.

Missing AD 2 pages, missing runway tables, or surfaces that cannot establish the
airport symbol stop before writing any packages. Some grass/VFR fields are documented elsewhere rather than
in AD 2. For these, choose **Manual: use the symbol below** and select the desired
symbol explicitly. Manual mode does not contact SIA. Split a mixed selection into
separate runs if some airports require a manual override.

Outputs: <base>_ARPs.gpkg (Symbols) and <base>_ARP_Names.gpkg (Names). This naming
keeps the packages compatible with combined ARP exports from a VFR group. Names
use the ARP_NAME category. Airport positions are the reference points, not
runway thresholds. Ambiguous manual identifiers require a filtered reference.

Selected perimeter features and reference layer filters are respected. Polygon
holes are retained. No source package is edited and existing output data is
never overwritten. GeoPackage table/layer names match the filenames. Outputs
load into QGIS after completion. If writing fails before publication, the
staging outputs are discarded together.

Names use Arial, Single Labels on text, Offset from point, and an adjustable
3500 m northward geometry shift in EPSG:2154 by default. Change the working CRS
and offsets in Advanced for your map scale. Offset labels can fall outside the
selection polygon; selection applies to the source reference points.
