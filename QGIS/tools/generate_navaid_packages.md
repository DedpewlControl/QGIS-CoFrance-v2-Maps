# Generate CoFrance navaid packages

Install with the [suite installer](README.md), then search Processing for
**Generate CoFrance navaid packages**.

Choose **Polygon perimeter** or a manual identifier/ICAO list, enter a map/output
base name and choose a **new or empty output folder**. Click Run to create
**Symbols and Names**, plus an import_report.json report. A perimeter is not required in
manual mode. Perimeter mode ignores a previously entered manual list.

The automatic reference is **LFXX_VOR + LFXX_NDB**. Loaded project references are preferred.
Navaids fall back to QGIS/navdata; Airports and Runways also check QGIS/misc and
the QGIS-Diagrams reference paths already used by this project. Load the masters
in QGIS if those paths are unavailable. Airport/Runway tools also provide an
optional searchable reference override in Advanced.

Select **Master layers**: LFXX_VOR + LFXX_NDB, LFXX_VOR only, or LFXX_NDB only.
These are source containers: selecting LFXX_VOR does not mean every record is a VOR. Perimeter mode
selects all matching navaids inside or on the polygon, respecting holes. Manual
mode accepts identifiers such as VOR:CNA, NDB:ABC, or source-qualified @feature_id
for ambiguous identifiers. FIX entries are rejected. Separate VOR and NDB with
the same identifier are retained; choose a qualifier to resolve manual ambiguity.
Perimeter duplicates of the same type/identifier within 1 m are created once.

Outputs: <base>_Navaids_Symbols.gpkg and <base>_Navaids_Names.gpkg. The actual
symbol types and name categories come from the external type cross-check below,
not from the source layer name.

## SIA eAIP cross-check

**Type cross-check** defaults to **SIA eAIP: selected AIRAC edition (cached)**.
The default reference is the SIA edition supplied for this project:

https://www.sia.aviation-civile.gouv.fr/media/dvd/eAIP_01_OCT_2026/FRANCE/AIRAC-2026-10-01/html/index-fr-FR.html

The tool reads the published **ENR 4.1 radio-navigation facility table**. It uses
the Service/Type, ID, frequency and geographic position columns. It does not
classify facilities from their LFXX_VOR/LFXX_NDB source container.

For terminal aids not covered by ENR 4.1, **Automatically check nearby airport
AD 2.19 pages** is enabled by default. For each unverified source point, the
script finds up to three nearest airports within 100 km in LFXX_Airports and
checks their published AD 2.19 tables. It uses the same project/repository/
QGIS-Diagrams airport-reference lookup as the airport package tool. The first
run can take longer while these pages are cached individually.

Proximity only selects pages to read: identifier, position and available
frequency must still match the published aid. This is a bounded nearby-airport
search, not a complete scan of every airport. Missing pages are reported and do
not invent types. With no airport reference available the script warns and
retains the ENR/explicit-page results.

**Supplementary SIA AD 2.19 airport ICAOs** in Advanced still adds explicit pages
(for example LFBC, LFBM, LFBH), including airports outside the automatic search.
Unsupported LOC/GP entries are excluded; explicit DME and TACAN rows are supported,
including TACANs published with a channel rather than a MHz frequency.

The edition field accepts either a plain URL or a Markdown link pasted from chat.
Links are normalized before constructing page URLs and cache keys.

No API key is needed. The first run downloads the selected SIA HTML tables;
the resulting typed reference is cached for 24 hours in the active QGIS profile
under processing/cofrance_cache. QGIS network/proxy settings are used. The SIA
HTML table structure must be accessible; a login, error or maintenance page is
rejected instead of treated as a successful verification.

The cache key includes the edition URL and supplementary aerodrome pages. An
older AIRAC cycle cannot be reused for a newly selected edition. The report
records the exact AIRAC cycle, page URLs, download/cache date and reference hash.
Automatically checked AD 2 pages have independent caches, including valid pages
with no supported aids. Their attempted ICAOs, download status and errors appear
under type_database.automatic_ad219_pages.
Change **SIA eAIP edition index URL** to select a later edition explicitly;
the script never silently switches to a different cycle.

**Force a fresh database download** bypasses the cache. An automatic refresh
failure can use an existing cache for exactly the same selected edition, with
a warning and its original download date. Failures refreshing required ENR or
explicit supplementary pages stop the run. Unavailable automatically chosen
candidate pages are recorded and their unresolved aids follow the selected
Skip/Stop policy; a nearby airport may not have a published AD 2 page.

Each source point is matched by exact identifier, position within the advanced
**Maximum type-reference distance** (1000 m by default), and frequency when both
datasets provide one. MHz source values are converted to kHz. Frequency alone
does not determine facility type. A distant facility with the same identifier
is never substituted. **Skip and report** is now the default for unverified or
conflicting source entries: the output contains only verified facilities, and
import_report.json lists all omissions under type_issues. If nothing verifies,
no empty packages are written. Advanced also offers **Stop and report; write
nothing** for strict runs. QGIS can remember an earlier strict setting; select
Skip and report explicitly when rerunning an existing dialog/history entry.

Older master files may contain withdrawn aids. Missing matches are recorded as
unverified, not automatically declared withdrawn. Increasing the distance or
falling back to layer names cannot establish a missing aid's current type.

| Published type | Symbols | Name category |
|---|---|---|
| VOR | vor | VOR_NAME |
| VOR-DME | vor_dme | VOR_NAME |
| DME | dme | DME_NAME |
| NDB | ndb | NDB_NAME |
| NDB-DME | overlaid ndb + dme | NDB_NAME, one name |
| TACAN | tacan | TACAN_NAME |
| VORTAC | vortac | VORTAC_NAME |

A combined facility in both source layers is created once when both match the
same reference record. For NDB-DME, the NDB source anchor is preferred. Source
coordinates are retained. Because the current exporter has no dedicated ndb_dme
symbol identifier, the package uses two supported symbols and one name.
POINT_COUNT counts facilities, not component symbol rows.

Generated packages retain navaid_type, type_source, type_reference_id and
match-distance fields. The report also includes unresolved matches and actual
symbol/name feature counts.

**Local typed database CSV** supports an offline alternative, with required
columns ident, type, latitude_deg, longitude_deg. Optional: id, frequency_khz,
dme_frequency_khz, dme_latitude_deg, dme_longitude_deg, source_url. A **Legacy
layer names** option remains available explicitly; it does not verify types and
marks them unverified. The default online provider is SIA, with no fallback to
OurAirports or another database.

Selected perimeter features and reference layer filters are respected. Polygon
holes are retained. No source package is edited and existing output data is
never overwritten. GeoPackage table/layer names match the filenames. Outputs
load into QGIS after completion. If writing fails before publication, the
staging outputs are discarded together.

Names use Arial, Single Labels on text, Offset from point, and an adjustable
3500 m northward geometry shift in EPSG:2154 by default. Change the working CRS
and offsets in Advanced for your map scale. Offset labels can fall outside the
selection polygon; selection applies to the source reference points.
