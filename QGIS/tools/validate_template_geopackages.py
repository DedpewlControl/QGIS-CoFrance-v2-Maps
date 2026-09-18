"""Validate generated CoFrance template GeoPackages and QGIS styles."""

from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ElementTree

from create_template_geopackages import (
    OUTPUT_DIRECTORY,
    SVG_STYLE_FILES,
    SYMBOL_TYPES,
    TEMPLATES,
)


def validate_all():
    results = []
    for template_name, definition in TEMPLATES.items():
        path = OUTPUT_DIRECTORY / "cofrance_{}.gpkg".format(template_name)
        if not path.is_file():
            raise RuntimeError("Missing template: {}".format(path))

        layer_name = "cofrance_{}".format(template_name)
        with sqlite3.connect(path) as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != 0x47504B47:
                raise RuntimeError("Invalid GeoPackage application ID: {}".format(path))
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("Integrity check failed for {}: {}".format(path, integrity))
            content = connection.execute(
                "SELECT data_type, srs_id FROM gpkg_contents WHERE table_name = ?",
                (layer_name,),
            ).fetchone()
            if content != ("features", 4326):
                raise RuntimeError("Invalid contents registration: {}".format(path))
            geometry = connection.execute(
                """SELECT geometry_type_name, srs_id, z, m
                   FROM gpkg_geometry_columns WHERE table_name = ?""",
                (layer_name,),
            ).fetchone()
            if geometry != (definition["geometry"], 4326, 0, 0):
                raise RuntimeError("Invalid geometry registration: {}".format(path))
            columns = [
                row[1]
                for row in connection.execute(
                    'PRAGMA table_info("{}")'.format(layer_name)
                )
            ]
            expected_columns = ["fid", "geom"] + list(definition["fields"])
            if columns != expected_columns:
                raise RuntimeError(
                    "Unexpected fields in {}: {}".format(path, ", ".join(columns))
                )
            style = connection.execute(
                """SELECT styleQML FROM layer_styles
                   WHERE f_table_name = ? AND useAsDefault = 1""",
                (layer_name,),
            ).fetchone()
            if not style:
                raise RuntimeError("Missing embedded QGIS style: {}".format(path))
            ElementTree.fromstring(style[0])
            if template_name == "symbols":
                for symbol_type in SYMBOL_TYPES:
                    if 'value="{}"'.format(symbol_type) not in style[0]:
                        raise RuntimeError(
                            "Symbol dropdown is missing {}".format(symbol_type)
                        )
                for removed_type in ("vor_classic", "ndb_classic"):
                    if 'value="{}"'.format(removed_type) in style[0]:
                        raise RuntimeError(
                            "Removed symbol remains in dropdown: {}".format(
                                removed_type
                            )
                        )
                style_root = ElementTree.fromstring(style[0])
                renderer = style_root.find("renderer-v2")
                if renderer is None or renderer.get("attr") != "symbol_type":
                    raise RuntimeError("Missing categorized symbol_type renderer")
                category_values = {
                    category.get("value")
                    for category in renderer.findall("./categories/category")
                }
                for symbol_type, svg_filename in SVG_STYLE_FILES.items():
                    if symbol_type not in category_values:
                        raise RuntimeError(
                            "SVG renderer is missing {}".format(symbol_type)
                        )
                    svg_path = OUTPUT_DIRECTORY / "svg" / svg_filename
                    if not svg_path.is_file():
                        raise RuntimeError("Missing SVG asset: {}".format(svg_path))
                    if 'value="svg/{}"'.format(svg_filename) not in style[0]:
                        raise RuntimeError(
                            "QGIS style does not reference {}".format(svg_filename)
                        )

        sidecar = path.with_suffix(".qml")
        ElementTree.parse(sidecar)
        results.append((path, layer_name, definition["geometry"], path.stat().st_size))
    return results


if __name__ == "__main__":
    for path, layer_name, geometry, size in validate_all():
        print("{} | {} | {} | {} bytes".format(path, layer_name, geometry, size))
