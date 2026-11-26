# QGIS to CoFrance v2

A QGIS plugin for converting styled vector layers into the GeoJSON format used
by CoFrance v2.

The exporter supports polygons, lines, symbols, and text labels. It reads
visual styling from QGIS, validates the required attributes, converts runway
activation fields, transforms geometries to WGS 84, and produces a clean
CoFrance-compatible `FeatureCollection`.

This project is based on Simon's original QGIS exporter plugin and has been
substantially reworked for the current CoFrance v2 layer schema and workflow.

## Features

- Export multiple QGIS vector layers to one GeoJSON file.
- Convert polygons to `MultiPolygon`, lines to `MultiLineString`, and points to
  `MultiPoint`.
- Read fill, outline, line, marker, and label styling from QGIS.
- Support all 28 current CoFrance symbol types.
- Convert editable runway fields into CoFrance `activation` objects.
- Transform source layers to `EPSG:4326` automatically.
- Limit exported coordinates to six decimal places.
- Validate layer schemas and feature values before writing the output.
- Preserve QGIS layer-tree order when selecting layers for export.
- Preselect highlighted QGIS layers/groups in the export dialog, with a button
  to reload the highlights and a visible selected-layer count.

## Requirements

- QGIS 3.x
- A QGIS project containing vector layers based on the schemas documented
  below

No third-party Python packages are required by the plugin.

## Installation

For a packaged update, use **Plugins > Manage and Install Plugins > Install from
ZIP** and select `QGIS-CoFrance-Exporter.zip`, then restart/reload the plugin.
The ZIP contains the plugin folder `geojsonStyler`.

1. Download or clone this repository.
2. Copy the `QGIS-CoFrance-Exporter` directory into the `python/plugins`
   directory of your QGIS user profile and rename the copy to `geojsonStyler`.
   Replace the existing copy when updating the plugin.
3. Restart QGIS.
4. Open **Plugins → Manage and Install Plugins** and enable
   **QGIS to CoFrance v2**.

The exporter is available from the Vector menu and the QGIS toolbar as
**Export CoFrance v2 GeoJSON**.

## Quick start

Highlight layers or a map group in the QGIS Layers panel, then open **Export
CoFrance v2 GeoJSON**. Those targets are checked automatically, including vector
layers in nested groups. Check the selected-layer count, adjust checkboxes as
needed, then export. **Use highlighted layers/groups** replaces the checked
targets with the current highlight selection. Changing highlights afterward
does not silently change the checked export list. A highlighted layer is exported
with all features allowed by its filter; map-feature selections are separate.

The [CoFrance Processing tools index](QGIS/tools/README.md) lists the current
thirteen tools and the one-step installer, which backs up previous profile copies.

Use [Navaids](QGIS/tools/generate_navaid_packages.md) or
[Airports](QGIS/tools/generate_airport_packages.md) to create Symbols + Names,
or [Runways](QGIS/tools/generate_runway_packages.md) for Lines only, selected by
perimeter or a manual identifier/ICAO list. Navaid types are cross-checked against
the selected SIA eAIP edition, with its AIRAC cycle recorded and cached in QGIS.

Use the separate [Airways](QGIS/tools/generate_airways.md),
[SID](QGIS/tools/generate_sid.md) and [STAR](QGIS/tools/generate_star.md) tools
to create line, symbol and name GeoPackages from high/low airways selected by
identifier list or perimeter, and STARs/SIDs selected by airport ICAO and runway
configuration, with reference or ordered manual routes.

Use [Generate CoFrance trajectory points and names](QGIS/tools/generate_trajectory_points.md)
for any selected FRA/route layer. This replaces the Bordeaux-specific tool.
[Names from placed symbols](QGIS/tools/generate_fix_names.md) and
[specified point lists](QGIS/tools/create_points_from_list.md) now assign
FIX_NAME, VOR_NAME, NDB_NAME and other categories by point type.

Use [Modify CoFrance Z-index](QGIS/tools/modify_z_index.md) to set the stored
z-index field across selected layers or groups.

Other tools cover [terminal/enroute holds](QGIS/tools/generate_holding.md),
[map structures](QGIS/tools/create_map_structure.md), and
[recovering exported GeoJSON](QGIS/tools/import_cofrance_map.md).

Ready-made GeoPackages are available in [`QGIS/templates`](QGIS/templates/):

- `cofrance_polygons.gpkg`
- `cofrance_lines.gpkg`
- `cofrance_symbols.gpkg`
- `cofrance_text.gpkg`

These files are master templates. **Do not edit them directly.** A GeoPackage
stores both its schema and its features, so editing a template would write your
map data into the repository copy.

For each layer you need:

1. Copy the appropriate GeoPackage into your QGIS project's data directory.
   When using the symbol template, also copy the `svg` directory beside the
   GeoPackage so QGIS can resolve the bundled symbol artwork.
2. Rename the copy for your map, for example `nice_vfr_symbols.gpkg`.
3. Add the copied GeoPackage through **Layer → Add Layer → Add Vector Layer**.
4. Draw features, complete the required attributes, and style the layer in
   QGIS.
5. Run **Export CoFrance v2 GeoJSON**, select the layers, and choose an output
   file.

## Layer schemas

Identifier and activation attributes use QGIS **Text (string)** fields.
`z_index`, `zoomin`, and `zoomout` use **Integer** fields. Optional values may
be left null when they do not apply.

| Layer type | Geometry | Required fields |
|---|---|---|
| Polygon | `MultiPolygon` | `name` |
| Line | `MultiLineString` | `name` |
| Symbol | `MultiPoint` | `name`, `symbol_type` |
| Text | `MultiPoint` | `uuid`, `text` |

The original active-runway fields remain required for compatibility, although
their values may be null. Newly generated templates also contain these optional
wiki fields:

| Field | Purpose |
|---|---|
| `activation_icao` | Four-letter ICAO code used by the activation rule |
| `activation_arr` | Arrival runways for which the feature is active |
| `activation_dep` | Departure runways for which the feature is active |
| `activation_unactive_icao` | ICAO code for an inactive-runway condition |
| `activation_unactive_arr` | Arrival runways that hide the feature while active |
| `activation_unactive_dep` | Departure runways that hide the feature while active |
| `activation_sector_me` | Sectors that must be owned by the current controller |
| `activation_sector_others` | Sectors that must be owned by another controller |
| `z_index` | Draw order; lower values are drawn first |
| `zoomin` | Lowest zoom level at which the feature is visible |
| `zoomout` | Highest zoom level at which the feature is visible |

Although the templates use multi-geometries, the exporter also accepts and
promotes `Polygon`, `LineString`, and `Point` geometries automatically.

### Identifiers

- `name` identifies a polygon, line, or symbol group in the exported map.
- `uuid` identifies the text category. It does not need to be unique for every
  label; values such as `Nice VFR Point` may be reused across a layer.
- `text` contains the string displayed by a text feature.
- `symbol_type` selects the CoFrance symbol independently of the QGIS marker
  used for editing and previewing.

## QGIS styling

Keep **Use QGIS layer styles** enabled in the export dialog to transfer visual
properties.

| QGIS layer | Exported style |
|---|---|
| Polygon | Fill color and opacity; outline color, width, and dashes |
| Line | Color, width, and dash pattern |
| Symbol | Color; CoFrance uses a fixed pixel size for each symbol type |
| Text | Font family, size, weight, and color |

CoFrance supports one style object per exported feature. Complex stacked
symbols may therefore be reduced to the first compatible QGIS symbol layer.

## Text-label workflow

For FIX symbols, navaids or airport reference points already placed on a map, use the
[Generate CoFrance FIX names Processing script](QGIS/tools/generate_fix_names.md)
to choose the matching masters from the layer name and create an offset text
GeoPackage from the text template automatically. It supports a manual layer
picker and an automatic folder scan that creates only missing names layers.

Text position comes from point geometry, text content comes from the `text`
attribute, and appearance comes from QGIS labeling.

1. Add a point at the desired label position.
2. Enter a `uuid` and the displayed `text`.
3. Open **Layer Properties → Labels**.
4. Select **Single Labels** and set **Value** to the `text` field.
5. Configure the font, size, weight, and color under **Text**.
6. Optionally select **No symbols** under **Symbology** to hide the point
   marker in QGIS.

Each source feature is exported as a separate text feature. Rule-based and
data-defined label formats are not fully evaluated; use one shared label format
per text layer. Label offsets, rotation, buffers, and shadows are not part of
the current CoFrance `textStyle` schema.

## Activation

Leave all activation values empty for a feature that is always visible. Active
and inactive runway conditions each use an ICAO field plus one or both runway
lists. Sector ownership fields accept comma- or semicolon-separated names.
Use `+` to require multiple sectors together, for example
`LFFFUR+LFFFUH, LFFFUZ`.

Example QGIS values:

| Field | Value |
|---|---|
| `activation_icao` | `LFMN` |
| `activation_arr` | `04L, 04R` |
| `activation_dep` | *(empty)* |

The exporter produces:

```json
"activation": {
  "activeRunways": {
    "LFMN": {
      "arr": [
        "04L",
        "04R"
      ]
    }
  }
}
```

Runways may be separated with commas, spaces, or semicolons. Values are
uppercased, deduplicated, and exported as JSON arrays. Valid designators use a
runway number from `01` to `36` with an optional `L`, `C`, or `R` suffix.

Inactive runway fields export as `unactiveRunway`. Sector fields export as
`sectorOwnedByMe` and `sectorOwnedByOthers`, following the wiki's condition
combination rules.

## Supported symbols

The symbol template includes a QGIS Value Map dropdown for `symbol_type`.
Friendly labels may be shown in QGIS, but the stored value must be one of:

- `point`
- `circle`
- `square`
- `diamond`
- `diamond_cross`
- `circle_cross`
- `cross`
- `cross_large`
- `x`
- `asterix`
- `triangle_hollow`
- `triangle_filled`
- `triangle_hollow_thick_bottom_border`
- `triangle_with_circle_rings`
- `circle_with_outer_rings`
- `vor`
- `vor_dme`
- `dme`
- `ndb`
- `navaid`
- `tacan`
- `vortac`
- `vor_classic`
- `ndb_classic`
- `aerodrome`
- `aerodrome_paved`
- `aerodrome_ticks`
- `aerodrome_paved_ticks`

Selecting `vor_dme`, `dme`, `vor`, `ndb`, `tacan`, or `vortac` automatically
displays its bundled SVG in QGIS after the edit is applied. The generic
`navaid` value currently uses the fallback marker because it has no dedicated
SVG asset.

Unknown symbol values stop the export and produce a validation message.

## Export behavior

By default, **Export separate files by subgroup** is enabled. Select a parent
group such as **Limoges**, click **OK**, and choose an output folder. Each
numbered subgroup exports all its checked layers (including nested groups) into
one file named after the subgroup, for example:

- `0_CCA_Limoges.geojson`
- `1_CCA_Limoges_MVA.geojson`
- `3_CCA_Limoges_Navaids_Names.geojson`
- `3_CCA_Limoges_FIX_Names.geojson`

Other numbered subgroups, such as procedures, export the same way when selected.
The VFR subgroup is split into `4_CCA_Limoges_VFR_Point_Names.geojson` for
VFR point names and `4_CCA_Limoges_ARPs.geojson` for ARP symbols plus ARP names.
ARP layer names ending in `ARP`, `ARPs`, `ARP_Names`, or `ARPs_Names` are
recognized, including names containing spaces. Other layers in the VFR subgroup
go into the VFR point names file. Files are only created for selected layers;
empty groups produce no file. Layer draw order is preserved within each file.

Existing files require confirmation before replacement. All batches are
validated and staged before any destination file is replaced. Conflicting
filenames from different group paths are reported so they cannot overwrite
each other. Uncheck **Export separate files by subgroup** to combine all checked
layers into a single file instead.

**Update manifest.json when found** is also enabled by default for batch
exports. Choose your map's destination folder as usual. The plugin looks for
`manifest.json` in that folder, then its parent folders, using the closest one.
It registers exported filenames without the `.geojson` extension in the matching
FIR/airport list. For example, Limoges maps update `LFBB` → `LFBL`, even when
exporting into an `LFBB/LFBL` folder below the manifest.

Existing map names identify the correct entry; the `0_CCA_…` or `0_EGA_…` base
map also identifies where newly exported subgroups belong. ICAO folder names
provide context for shared or new map names. Existing entries and their order,
other airports, and `$schema` are preserved. Partial exports add missing names
without removing unchecked maps. Names differing only in letter case are
corrected to match the actual exported filename. When exporting a combined ARP
map, matching legacy entries such as `_VFR_ARPs`, `_VFR_ARP`, `_ARP`,
`_ARP_Names`, or `_VFR_ARPs_Names` are replaced by one `_ARPs` entry at the
first ARP position. This cleanup applies only to the exported ARP map in its
matching section; VFR point names and other airports keep their entries.
Legacy group names also export with the canonical `_ARPs.geojson` filename.
A manifest which already has all names and needs no cleanup is left untouched.

Manifest changes are staged with the GeoJSON files and applied after all layers
validate. Invalid JSON, ambiguous or missing destinations, or a manifest changed
by another program stop the export with a message. Uncheck the manifest option
to export without updating it. When no manifest is found, export works normally.
The plugin writes GeoJSON to the folder you choose; it does not move existing
files or create new FIR/airport sections in the manifest.

- Output coordinates are always WGS 84 longitude/latitude (`EPSG:4326`).
- Coordinates are rounded to a maximum of six decimal places.
- Features with empty geometries are skipped.
- Non-text geometries are merged only when their geometry type, identifier,
  style, and activation properties match.
- Text features remain separate so each label retains its own content and
  position.
- Unrelated QGIS attributes are not copied into the output GeoJSON.
- Incomplete activation data, missing required values, invalid runways, and
  unsupported symbols stop the export with a descriptive error.

## Development

The schema and activation helpers can be tested without starting QGIS:

```text
python -m unittest QGIS-CoFrance-Exporter.test_plugin QGIS-CoFrance-Exporter.test_group_exports QGIS-CoFrance-Exporter.test_manifest_export
```

The batch test module also runs dialog and file-export integration tests when
launched using QGIS's Python runtime (`python-qgis-ltr.bat` on Windows).

Template GeoPackages can be regenerated and checked with:

```text
python tools/create_template_geopackages.py
python tools/validate_template_geopackages.py
```
