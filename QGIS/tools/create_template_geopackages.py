"""Generate empty CoFrance v2 QGIS template GeoPackages.

The generator uses only Python's SQLite support so it can be rerun without
QGIS or GDAL. Each package includes a QGIS default field style; the symbol
package configures ``symbol_type`` as a Value Map dropdown.
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "templates"

ACTIVATION_FIELDS = (
    "activation_icao",
    "activation_arr",
    "activation_dep",
)

SYMBOL_TYPES = (
    "diamond",
    "circle_cross",
    "diamond_cross",
    "triangle_hollow",
    "triangle_filled",
    "triangle_hollow_thick_bottom_border",
    "triangle_with_circle_rings",
    "circle",
    "square",
    "asterix",
    "cross",
    "circle_with_outer_rings",
    "vor_classic",
    "ndb_classic",
)

TEMPLATES = {
    "polygons": {
        "geometry": "MULTIPOLYGON",
        "fields": ("name",) + ACTIVATION_FIELDS,
        "required": ("name",),
    },
    "lines": {
        "geometry": "MULTILINESTRING",
        "fields": ("name",) + ACTIVATION_FIELDS,
        "required": ("name",),
    },
    "symbols": {
        "geometry": "MULTIPOINT",
        "fields": ("name", "symbol_type") + ACTIVATION_FIELDS,
        "required": ("name", "symbol_type"),
    },
    "text": {
        "geometry": "MULTIPOINT",
        "fields": ("uuid", "text") + ACTIVATION_FIELDS,
        "required": ("uuid", "text"),
    },
}

WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["World Geodetic System 1984",'
    'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]]'
)


def _option(name, value, value_type="QString"):
    return '<Option name="{}" value="{}" type="{}"/>'.format(
        name, value, value_type
    )


def _text_widget(field_name):
    return """    <field name="{name}" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>""".format(name=field_name)


def _symbol_widget():
    entries = []
    for symbol_type in SYMBOL_TYPES:
        entries.append(
            """              <Option type="Map">
                {option}
              </Option>""".format(
                option=_option(symbol_type, symbol_type)
            )
        )
    return """    <field name="symbol_type" configurationFlags="None">
      <editWidget type="ValueMap">
        <config>
          <Option type="Map">
            <Option name="map" type="List">
{entries}
            </Option>
          </Option>
        </config>
      </editWidget>
    </field>""".format(entries="\n".join(entries))


def qgis_field_style(template_name, fields):
    """Return a minimal QGIS QML style containing field editor widgets."""
    widgets = []
    for field_name in fields:
        if template_name == "symbols" and field_name == "symbol_type":
            widgets.append(_symbol_widget())
        else:
            widgets.append(_text_widget(field_name))
    aliases = "\n".join(
        '    <alias name="" index="{}" field="{}"/>'.format(index, field_name)
        for index, field_name in enumerate(fields)
    )
    return """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Fields">
  <fieldConfiguration>
{widgets}
  </fieldConfiguration>
  <aliases>
{aliases}
  </aliases>
</qgis>
""".format(widgets="\n".join(widgets), aliases=aliases)


def _create_core_tables(connection):
    connection.executescript(
        """
        PRAGMA application_id = 1196444487;
        PRAGMA user_version = 10400;
        PRAGMA foreign_keys = ON;

        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        );

        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL,
            min_x DOUBLE,
            min_y DOUBLE,
            max_x DOUBLE,
            max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id)
        );

        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT uk_gc_table_name UNIQUE (table_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
                REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id)
        );

        CREATE TABLE layer_styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            f_table_catalog VARCHAR,
            f_table_schema VARCHAR,
            f_table_name VARCHAR,
            f_geometry_column VARCHAR,
            styleName TEXT,
            styleQML TEXT,
            styleSLD TEXT,
            useAsDefault BOOLEAN,
            description TEXT,
            owner VARCHAR(30),
            ui TEXT,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    connection.executemany(
        """INSERT INTO gpkg_spatial_ref_sys
           (srs_name, srs_id, organization, organization_coordsys_id,
            definition, description)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            (
                "Undefined Cartesian SRS",
                -1,
                "NONE",
                -1,
                "undefined",
                "undefined Cartesian coordinate reference system",
            ),
            (
                "Undefined Geographic SRS",
                0,
                "NONE",
                0,
                "undefined",
                "undefined geographic coordinate reference system",
            ),
            (
                "WGS 84 geodetic",
                4326,
                "EPSG",
                4326,
                WGS84_WKT,
                "longitude/latitude coordinates in decimal degrees",
            ),
        ),
    )


def _create_template(path, template_name, definition):
    layer_name = "cofrance_{}".format(template_name)
    qml = qgis_field_style(template_name, definition["fields"])
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.stem + "_", suffix=".gpkg", dir=OUTPUT_DIRECTORY
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary_path)
        try:
            _create_core_tables(connection)

            columns = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT', '"geom" BLOB']
            symbol_values = ",".join("'{}'".format(value) for value in SYMBOL_TYPES)
            for field_name in definition["fields"]:
                column = '"{}" TEXT'.format(field_name)
                if field_name in definition["required"]:
                    column += " NOT NULL"
                if field_name == "symbol_type":
                    column += " CHECK (\"symbol_type\" IN ({}))".format(symbol_values)
                columns.append(column)
            connection.execute(
                'CREATE TABLE "{}" ({})'.format(layer_name, ", ".join(columns))
            )

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            connection.execute(
                """INSERT INTO gpkg_contents
                   (table_name, data_type, identifier, description, last_change,
                    min_x, min_y, max_x, max_y, srs_id)
                   VALUES (?, 'features', ?, ?, ?, NULL, NULL, NULL, NULL, 4326)""",
                (
                    layer_name,
                    layer_name,
                    "Empty CoFrance v2 {} template".format(template_name),
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO gpkg_geometry_columns
                   (table_name, column_name, geometry_type_name, srs_id, z, m)
                   VALUES (?, 'geom', ?, 4326, 0, 0)""",
                (layer_name, definition["geometry"]),
            )
            connection.execute(
                """INSERT INTO layer_styles
                   (f_table_catalog, f_table_schema, f_table_name,
                    f_geometry_column, styleName, styleQML, styleSLD,
                    useAsDefault, description, owner, ui)
                   VALUES ('', '', ?, 'geom', 'default', ?, '', 1,
                           'CoFrance v2 template field configuration', '', '')""",
                (layer_name, qml),
            )
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("SQLite integrity check failed: {}".format(result))
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    # Keep a readable sidecar so the editor configuration can also be loaded
    # manually if a QGIS installation is configured not to load DB styles.
    path.with_suffix(".qml").write_text(qml, encoding="utf-8")


def generate_all():
    generated = []
    for template_name, definition in TEMPLATES.items():
        path = OUTPUT_DIRECTORY / "cofrance_{}.gpkg".format(template_name)
        _create_template(path, template_name, definition)
        generated.append(path)
    return generated


if __name__ == "__main__":
    for generated_path in generate_all():
        print(generated_path)
