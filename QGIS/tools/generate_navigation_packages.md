# Shared navigation package extraction reference

For the toolbox use the separate [Airways](generate_airways.md),
[SID](generate_sid.md) and [STAR](generate_star.md) scripts. Each shows only its
own inputs. The installer archives the old combined entry from the profile,
keeping a backup. This file documents the shared engine and its legacy API;
the combined engine stays in the repository for existing Python integrations.

# Generate airway, STAR and SID packages

Install the current suite using install_cofrance_tools.py as described in
[the tools index](README.md), or add generate_navigation_packages.py through
Processing Toolbox > Scripts > Add Script to Toolbox.

Search for **Generate CoFrance airways, STAR or SID packages**.

## Choose the workflow

All modes create separate Lines, Symbols and Names GeoPackages. Choose a new
or empty output folder for each run.

| Mode | Selection | Polygon needed? |
|---|---|---|
| High or low airways | Polygon perimeter, optionally filtered by airway identifiers | Yes |
| High or low airways | Manual airway identifier list | No |
| STAR from reference | Airport ICAO + runway configuration + optional approach/procedure filter | No |
| SID from reference | Airport ICAO + runway configuration + optional SID name filter | No |
| Manual STAR / Manual SID | Airport ICAO + runway configuration + ordered point list | No |

A polygon is used **only** in the airway perimeter workflow. Procedure modes and
airway-list mode ignore any old polygon left in the dialog.

Loaded reference layers take precedence over the matching QGIS/navdata file.
You can select an alternative line reference. Lookup uses the reference's fields,
not its QGIS visual style. The dropdown accepts typed layer-name searches.

## Airways: perimeter or list

Select **High airways** or **Low airways**, then choose **Airways: selection method**.

### Polygon perimeter

Select your polygon. The normal QGIS selected-features option is supported.
Otherwise all polygons in that input are combined. Holes and separate parts
are retained.

Leave airway identifiers empty to include all intersecting airways, or add an
optional identifier filter. Lines are clipped by default; choose complete
intersecting source features if preferred.

### Manual airway identifier list

No polygon is required. Enter airway names, for example:

```text
A2, UM*
```

Matching is case insensitive. Commas, semicolons, spaces or newlines separate
names. Asterisks and question marks are wildcards. An empty list stops the run.
Complete matching airways and their named points are generated, including
portions outside any polygon still selected in the dialog.

This is a list of **airway identifiers**, not an ordered waypoint list.
Manual waypoint sequences remain available in the STAR/SID modes.

The default references are LFXX_HIGH_AWY and LFXX_LOW_AWY. Low airways may have
one multipart feature for an entire airway.

## STAR/SID: airport and runway configuration

An airport ICAO and runway configuration are required in every STAR/SID mode.
For example:

- Airport: LFBD
- Runway configuration: 23
- Or a multiple-runway configuration such as 14L/14R or 05,23

Runway numbers normalize 5 to 05 and retain L/R/C suffixes. Numbers must be
01 to 36. Multiple codes can be separated by commas, spaces, semicolons, + or /.
Reference modes can filter several airport ICAOs; a manual procedure uses one
airport ICAO so its metadata is unambiguous.

Procedures are selected by these fields and the optional procedure filter;
they are not clipped to or selected by the polygon. Manual procedures store
the ICAO and complete runway configuration in their line metadata.

## Reference STAR / approach example

Choose **STAR from reference**, Airport **LFBD**, Runway **23**, and Procedure
label **ILS23**. To match the reference's RNAV labels, use **RNAV23** or the alias
**RNP23**. A family filter such as ILS or RNAV includes that approach family;
STAR selects the reference's STAR-labelled groups.

The supplied LFXX_STAR file contains these fields:

- icao
- runway
- category
- procedures, such as ILS23;RNAV23

It groups geometry by airport/runway/approach labels. It does **not** preserve
individual STAR names. Selecting ILS23 selects every geometry component in the
matching source feature; it cannot disentangle ILS, RNAV and STAR components
already merged into that feature. An RNP filter is only an alias for the
dataset's RNAV spelling, not proof of a particular procedure specification.

The STAR reference's numeric runway field can merge parallel runways, such as
14L and 14R into 14. Side-specific configurations are matched against the
approach labels when available; the merged source geometry is still kept as
a group. Runway-independent STAR groups for the selected airport are included
when they match the procedure filter (for example STAR, or a blank filter).

Use **Manual STAR** when you need a particular ordered route that is not
separately represented. Alternatively provide a more detailed line reference
with a star, procedure or procedure_name field.

## Named SID example

Choose **SID from reference**, enter an airport/runway, and specify a named SID
in **Reference procedures: names or labels**. The bundled LFXX_SID contains a
sid field, so individual SIDs can be filtered. Wildcards also work here.

Runways normalize 5 to 05 and preserve suffixes such as 14L. Use a comma-separated
list for multiple airports, runways or procedure names. Blank procedure-name filters include all reference features matching the
required airport and runway configuration.

## Manual ordered routes

For one route, paste:

```text
VOR:CNA > VOR:AGN > VOR:TOU
```

Commas, semicolons, spaces or newlines can separate the points. Their **input
order is preserved**. No sorting is applied.

To draw branches separately, put one labelled route on each line:

```text
Arrival north | POINTA > POINTB > POINTC
Arrival south | POINTD > POINTB > POINTC
```

Replace POINTA etc. with real identifiers. A # starts a comment. Unlabelled
blocks separated by a blank line also define separate routes. Named routes
must each fit on one line.

Supported source prefixes are VOR:, NDB:, FIX:, ENR: and PROC:. To resolve a
duplicate identifier, use a specific master plus @feature_id, such as VOR:BLV@123.
Unqualified names search all four masters and the optional additional point layer.
That layer needs an ident or name field and point geometry.

Unknown or ambiguous manual identifiers stop the entire run before outputs
are written. The tool never skips an unresolved intermediate point and joins
the surrounding points. Straight lines connect the supplied coordinates;
RF turns, fly-by/fly-over behaviour, headings, speeds and altitude constraints
are not calculated.

## Geometry and point behaviour

**Clip lines to perimeter** is the default for the airway perimeter workflow. Source lines are cut at the polygon
edges and holes. Disconnected pieces remain multipart geometry; they are not
joined through holes or across a gap.

**Keep complete intersecting reference features** retains the full source
feature geometry when any part intersects. For a merged airway feature,
this can include geometry outside the polygon. In airway perimeter mode, point anchors remain restricted to the polygon
whether lines are clipped or complete source features are kept. Offset names
may extend outside it. Airway-list and STAR/SID modes use complete selected
routes and their named points without any polygon restriction.

Point detection includes named FIX, VOR and NDB coordinates within the matching
tolerance of the selected line geometry. Reference STAR/SID matching also
includes FIX Procedures. Manual modes use the explicitly supplied points only.
A shared named point is generated once. In airway perimeter mode, boundaries
count as inside and hole interiors do not.

Clipped line ends and procedure turn vertices often have no identifier. Their
coordinates and nearest-name hints are written to import_report.json. No name
is assigned from a distant point. Enable **UNNAMED symbols/labels** to create
clearly marked UNNAMED_001 etc. for those vertices. Otherwise they are reported
without symbols or labels. If there are no named matches, the tool still writes
the lines and empty symbol/name packages for review.

## Outputs and categories

The output base name gets a mode suffix. For example:

- CCA_MyMap_HIGH_AIRWAYS_Lines.gpkg
- CCA_MyMap_HIGH_AIRWAYS_Symbols.gpkg
- CCA_MyMap_HIGH_AIRWAYS_Names.gpkg
- import_report.json and svg artwork

Other mode suffixes are LOW_AIRWAYS, STAR and SID. Actual layer names match the
filenames. CoFrance template schemas and field widgets are preserved.

Name categories follow each point type:

| Point type | Text uuid |
|---|---|
| FIX | FIX_NAME |
| VOR / VOR-DME | VOR_NAME |
| NDB | NDB_NAME |
| DME | DME_NAME |
| TACAN | TACAN_NAME |
| VORTAC | VORTAC_NAME |
| Airport reference point, in the point-name tools | ARP_NAME |

Names use Arial 10 pt, Single Labels, value text, and Offset from point placement.
The default geometry shift is X=0, Y=3500 metres in EPSG:2154. Change the working
CRS and offsets for the region/map scale. No extra display offset is added.

Line metadata includes route_name, icao, runway, procedures, source_reference,
source_fid and source_properties. Source activation/zoom fields are copied when
the reference already has them; the tool does not infer activation from a
runway filter. Generated point activation fields can be edited afterward.

## Preservation, installation and checks

Existing input layers, navigation files and nonempty output folders are not
overwritten. All three packages are staged and published together. Keep the
generated svg folder with the packages.

Only the entry script needs installing. Shared writer/matcher files are loaded
from this repository's QGIS/tools folder. Repository paths are fixed in the
scripts, as in the existing tools; update them if the repository moves.

Tested with QGIS 3.40.4 on all six drawing modes against the actual navigation data,
including Bordeaux ILS23 and a named Bordeaux SID. Automated tests cover polygon
holes, selected polygons, boundary points, clipping, complete-feature mode,
named/reference filters, RNAV aliasing, manual order/branches, custom point
sources, duplicate names, unresolved manual points, empty point packages,
CoFrance export and cleanup after a package fails.

