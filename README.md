# QGIS to CoFrance v2

A QGIS plugin for converting styled vector layers into the GeoJSON format used
by CoFrance v2.

The exporter supports polygons, lines, symbols, and text labels. It reads
visual styling from QGIS, validates the required attributes, converts runway
activation fields, transforms geometries to WGS 84, and produces a clean
CoFrance-compatible `FeatureCollection`.

## Features

- Export multiple QGIS vector layers to one GeoJSON file.
- Convert polygons to `MultiPolygon`, lines to `MultiLineString`, and points to
  `MultiPoint`.
- Read fill, outline, line, marker, and label styling from QGIS.
- Support all 21 CoFrance symbol types.
- Convert editable runway fields into CoFrance `activation` objects.
- Transform source layers to `EPSG:4326` automatically.
- Limit exported coordinates to six decimal places.
- Validate layer schemas and feature values before writing the output.
- Preserve QGIS layer-tree order when selecting layers for export.

## Requirements

- QGIS 3.x
- A QGIS project containing vector layers based on the schemas documented
  below

No third-party Python packages are required by the plugin.

## Installation

1. Download or clone this repository.
2. Copy the `geojsonStyler` directory into the `python/plugins` directory of
   your QGIS user profile.
3. Restart QGIS.
4. Open **Plugins → Manage and Install Plugins** and enable
   **QGIS to CoFrance v2**.

The exporter is available from the Vector menu and the QGIS toolbar as
**Export CoFrance v2 GeoJSON**.

## Quick start

Ready-made GeoPackages are available in [`templates`](templates/):

- `cofrance_polygons.gpkg`
- `cofrance_lines.gpkg`
- `cofrance_symbols.gpkg`
- `cofrance_text.gpkg`

These files are master templates. **Do not edit them directly.** A GeoPackage
stores both its schema and its features, so editing a template would write your
map data into the repository copy.

For each layer you need:

1. Copy the appropriate GeoPackage into your QGIS project's data directory.
2. Rename the copy for your map, for example `nice_vfr_symbols.gpkg`.
3. Add the copied GeoPackage through **Layer → Add Layer → Add Vector Layer**.
4. Draw features, complete the required attributes, and style the layer in
   QGIS.
5. Run **Export CoFrance v2 GeoJSON**, select the layers, and choose an output
   file.

## Layer schemas

Use QGIS **Text (string)** fields for every attribute listed below. The three
activation fields must exist in every template, but their values may be null
when the feature is always visible.

| Layer type | Geometry | Required fields |
|---|---|---|
| Polygon | `MultiPolygon` | `name` |
| Line | `MultiLineString` | `name` |
| Symbol | `MultiPoint` | `name`, `symbol_type` |
| Text | `MultiPoint` | `uuid`, `text` |

Every layer must also contain these nullable Text fields:

| Field | Purpose |
|---|---|
| `activation_icao` | Four-letter ICAO code used by the activation rule |
| `activation_arr` | Arrival runways for which the feature is active |
| `activation_dep` | Departure runways for which the feature is active |

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
| Polygon | Fill color and opacity; outline color, width, opacity, and dashes |
| Line | Color, width, opacity, and dash pattern |
| Symbol | Color, size category, and opacity |
| Text | Font family, size, weight, and color |

CoFrance supports one style object per exported feature. Complex stacked
symbols may therefore be reduced to the first compatible QGIS symbol layer.

## Text-label workflow

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

## Runway activation

Leave all three activation values empty for a feature that is always visible.
For a conditional feature, enter a four-letter ICAO code and one or both runway
lists.

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
      "arr": "04L 04R"
    }
  }
}
```

Runways may be separated with commas, spaces, or semicolons. Values are
uppercased, deduplicated, and exported with spaces. Valid designators use a
runway number from `01` to `36` with an optional `L`, `C`, or `R` suffix.

## Supported symbols

The symbol template includes a QGIS Value Map dropdown for `symbol_type`.
Friendly labels may be shown in QGIS, but the stored value must be one of:

- `diamond`
- `circle_cross`
- `diamond_cross`
- `triangle_hollow`
- `triangle_filled`
- `triangle_hollow_thick_bottom_border`
- `triangle_with_circle_rings`
- `circle`
- `square`
- `asterix`
- `cross`
- `circle_with_outer_rings`
- `vor_classic`
- `ndb_classic`
- `vor_dme`
- `dme`
- `vor`
- `ndb`
- `navaid`
- `tacan`
- `vortac`

Unknown symbol values stop the export and produce a validation message.

## Export behavior

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
python -m unittest geojsonStyler.test_plugin
```

Template GeoPackages can be regenerated and checked with:

```text
python tools/create_template_geopackages.py
python tools/validate_template_geopackages.py
```