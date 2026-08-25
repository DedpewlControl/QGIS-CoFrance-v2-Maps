# CoFrance v2 QGIS layer templates

Use GeoPackage layers. All activation fields must exist in every template, but
their values may be left null/empty when a feature is always visible.

| Template | Field | QGIS field type | Required value |
|---|---|---|---|
| Polygon | `name` | Text | Yes |
| Polygon | `activation_icao` | Text | Only for activation |
| Polygon | `activation_arr` | Text | Only for arrival activation |
| Polygon | `activation_dep` | Text | Only for departure activation |
| Line | `name` | Text | Yes |
| Line | `activation_icao` | Text | Only for activation |
| Line | `activation_arr` | Text | Only for arrival activation |
| Line | `activation_dep` | Text | Only for departure activation |
| Symbol | `name` | Text | Yes |
| Symbol | `symbol_type` | Text | Yes; configure a QGIS Value Map |
| Symbol | `activation_icao` | Text | Only for activation |
| Symbol | `activation_arr` | Text | Only for arrival activation |
| Symbol | `activation_dep` | Text | Only for departure activation |
| Text | `uuid` | Text | Yes |
| Text | `text` | Text | Yes |
| Text | `activation_icao` | Text | Only for activation |
| Text | `activation_arr` | Text | Only for arrival activation |
| Text | `activation_dep` | Text | Only for departure activation |

Geometry types are MultiPolygon, MultiLineString, MultiPoint (symbol), and
MultiPoint (text). The exporter also promotes single geometries to the matching
multi-geometry automatically.

Supported `symbol_type` values:

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

Configure these as stored values in a QGIS Value Map. Friendly display labels
can differ from the stored values.

Runways may be entered with commas or spaces. For example, `04L, 04R` is
exported as `04L 04R`. When all three activation values are empty, no
`activation` property is exported.