# CoFrance v2 template GeoPackages

Add the required GeoPackage layers to QGIS through **Layer → Add Layer → Add
Vector Layer**:

- `cofrance_polygons.gpkg` — `MULTIPOLYGON`, EPSG:4326
- `cofrance_lines.gpkg` — `MULTILINESTRING`, EPSG:4326
- `cofrance_symbols.gpkg` — `MULTIPOINT`, EPSG:4326
- `cofrance_text.gpkg` — `MULTIPOINT`, EPSG:4326

Each package contains one empty layer with the required CoFrance fields. The
three activation fields are nullable. Identifier fields and `symbol_type` are
non-null. The symbol package restricts `symbol_type` to the 14 supported
values.

QGIS field editor configuration is stored as the default database style. The
matching `.qml` sidecar is also included as a fallback: open **Layer
Properties → Symbology → Style → Load Style** if the field configuration is not
loaded automatically. The symbol style configures `symbol_type` as a Value Map
dropdown.

The `.qml` files configure attribute editors only; visual symbology and text
label appearance should be designed in QGIS for each working layer.

Regenerate and validate all files from the repository root with:

```text
python tools/create_template_geopackages.py
python tools/validate_template_geopackages.py
```
