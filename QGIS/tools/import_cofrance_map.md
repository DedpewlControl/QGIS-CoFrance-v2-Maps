# Import an exported CoFrance map into QGIS

Install import_cofrance_map.py through **Processing Toolbox > Scripts > Add
Script to Toolbox**, then search for **Import exported CoFrance map**.

## Use

1. Choose **One exported GeoJSON** or **Folder of exported GeoJSON files**.
2. Select the .geojson file, or the folder containing the exported files for
   one map. Folder mode includes GeoJSON files in subfolders. Select the map's
   folder rather than the entire CoFrance maps repository.
3. Enter a map name, such as **La Rochelle recovered**.
4. Choose a **new or empty QGIS map folder** on disk, for example
   QGIS/src/LFBB/La Rochelle recovered.
5. Run the tool, then save your QGIS project with **Ctrl+S**.

Imported groups appear under **Imported CoFrance maps / map name**. You can move
the map group under the appropriate FIR afterward. The existing exporter works
with the imported numbered subgroups.

This imports CoFrance v2 GeoJSON map data, not screenshots, PDFs or a map URL.
Input GeoJSON uses WGS84 longitude/latitude. A standalone file with a .json
extension is also accepted if it contains a GeoJSON FeatureCollection.

## Recovered data

| Exported data | QGIS result |
|---|---|
| Numbered GeoJSON filenames | Matching numbered disk folders and layer groups |
| Mixed geometry/style features | Separate, editable, styled GeoPackages |
| Merged MultiPoint/MultiLineString/MultiPolygon geometry | Individual editable parts; polygon holes retained |
| name | name field |
| pointStyle.type | symbol_type field, plus a QGIS marker/SVG preview |
| uuid and textStyle.text | uuid/text fields and Single Labels |
| Text position | Exact exported point position; no extra 3500-metre shift |
| Fill, outline, dash, marker colour and text formatting | Saved default QGIS styles inside each GeoPackage |
| Runway and sector activation | Editable activation fields used by the exporter |
| z-index, zoomin, zoomout | z_index, zoomin, zoomout fields |

Each GeoPackage's actual layer name matches its filename. A different style
for the same name/category creates another layer, with a numbered suffix.
Consecutive runs are kept separate where needed to retain feature drawing
order. Multiple styles for text are split into separate layers because the
current exporter reads one shared label format per layer.

The combined 4_..._ARPs and separate 4_..._VFR_Point_Names exports are restored
under 4_..._VFR. ARP symbols and names receive layer names that the exporter
recognises for combining into 4_..._ARPs on the next export.

A standalone, unnumbered GeoJSON goes into 0_CCA_<map name>. Exact original
subgroups cannot be inferred when the export no longer contains that information.

## Preservation and limitations

Source exports are read only, and nonempty output folders are refused. Outputs
are prepared in a staging directory before publication. Original properties
are retained in **source_properties**, together with **source_file** and
**source_index** tracking fields.

Legacy trajectory exports such as Carte_Trajectoires_UIR.geojson are supported.
A null lineStyle.dashArray is treated as a solid line. Features with null geometry
or an empty coordinate array are skipped and counted in the report; all nonempty
features are imported. Malformed nonempty geometries still cause an error.
XYZ coordinates are preserved, with separate layers for different geometry
dimensions. Original properties, including null dash values, remain in
source_properties.

Read **import_report.json** and the Processing log after importing. They list
approximations and retained properties which the current exporter may not emit.
Examples include legacy font weights, custom polygon outline dashes, point
size/opacity and line opacity. The legacy fix_filled type is mapped to
triangle_filled. Specialised symbols can have an approximate QGIS preview while
preserving symbol_type for export.

The original layer boundaries, QGIS styling units, editing history and
coordinate precision beyond what was exported cannot be recovered. Conditions
requiring multiple airports in one active/inactive-runway rule, or unknown
activation rule types, cause an explanatory error: the current QGIS template
fields cannot represent those conditions without loss. Source data remains
unchanged.

The importer uses the repository's QGIS/templates GeoPackages and bundled SVG
artwork. If the repository moves, update TEMPLATE_FOLDER in the script. Keep the
imported svg folder with its GeoPackages; if relocating the recovered map,
check/relink SVG paths in QGIS.

## Checks

Run test_import_cofrance_map.py using QGIS's Python environment. Integration
checks cover importing and re-exporting geometry with polygon holes, styles,
activation, draw/zoom attributes, ARP/VFR grouping, source metadata, existing
output protection and cleanup after a failed import. It was also checked on
the actual exported Aquitaine map.
