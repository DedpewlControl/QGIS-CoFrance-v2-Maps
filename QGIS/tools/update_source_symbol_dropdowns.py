"""Synchronize QGIS ``symbol_type`` dropdowns in source GeoPackages."""

import argparse
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ElementTree

from create_template_geopackages import SYMBOL_TYPES, _symbol_widget


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIRECTORY = REPOSITORY_ROOT / "QGIS" / "src"


def quote_identifier(value):
    return '"{}"'.format(str(value).replace('"', '""'))


def table_exists(connection, table_name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def feature_tables(connection):
    return [
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM gpkg_contents "
            "WHERE data_type = 'features' ORDER BY table_name"
        )
    ]


def has_symbol_type(connection, table_name):
    return any(
        row[1].lower() == "symbol_type"
        for row in connection.execute(
            "PRAGMA table_info({})".format(quote_identifier(table_name))
        )
    )


def synchronized_qml(qml):
    widget = _symbol_widget()
    field_pattern = re.compile(
        r"(?P<indent>^[ \t]*)<field\b(?=[^>]*\bname=[\"']symbol_type[\"'])"
        r"[^>]*>.*?</field>",
        re.DOTALL | re.MULTILINE,
    )
    match = field_pattern.search(qml)
    if match:
        indent = match.group("indent")
        replacement = "\n".join(
            indent + (line[4:] if line.startswith("    ") else line)
            if line.strip()
            else line
            for line in widget.splitlines()
        )
        return qml[: match.start()] + replacement + qml[match.end() :]

    closing = re.search(r"^[ \t]*</fieldConfiguration>", qml, re.MULTILINE)
    if closing:
        return qml[: closing.start()] + widget + "\n" + qml[closing.start() :]

    qgis_closing = qml.rfind("</qgis>")
    if qgis_closing < 0:
        raise RuntimeError("styleQML has no qgis root element")
    block = "  <fieldConfiguration>\n{}\n  </fieldConfiguration>\n".format(widget)
    return qml[:qgis_closing] + block + qml[qgis_closing:]


def dropdown_is_current(qml):
    field_match = re.search(
        r"<field\b(?=[^>]*\bname=[\"']symbol_type[\"'])[^>]*>.*?</field>",
        qml,
        re.DOTALL,
    )
    if not field_match:
        return False
    block = field_match.group(0)
    values = re.findall(r'<Option\b[^>]*\bvalue="([^"]+)"[^>]*/>', block)
    return values == list(SYMBOL_TYPES)


def minimal_dropdown_qml():
    return """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Fields">
  <fieldConfiguration>
{widget}
  </fieldConfiguration>
</qgis>
""".format(widget=_symbol_widget())


def inspect_geopackage(path, apply=False):
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity check failed before update")
        symbol_tables = [
            table_name
            for table_name in feature_tables(connection)
            if has_symbol_type(connection, table_name)
        ]
        if not symbol_tables:
            return [], []
        if not table_exists(connection, "layer_styles"):
            raise RuntimeError("symbol layer has no layer_styles table")

        supported = set(SYMBOL_TYPES)
        for table_name in symbol_tables:
            stored_values = {
                str(row[0]).strip().lower()
                for row in connection.execute(
                    "SELECT DISTINCT symbol_type FROM {} "
                    "WHERE symbol_type IS NOT NULL".format(
                        quote_identifier(table_name)
                    )
                )
                if str(row[0]).strip()
            }
            invalid = sorted(stored_values - supported)
            if invalid:
                raise RuntimeError(
                    "unsupported symbol_type values in {!r}: {}".format(
                        table_name, ", ".join(invalid)
                    )
                )

        changes = []
        for table_name in symbol_tables:
            styles = list(
                connection.execute(
                    "SELECT id, styleQML FROM layer_styles "
                    "WHERE f_table_name = ? AND styleQML IS NOT NULL",
                    (table_name,),
                )
            )
            if not styles:
                qml = minimal_dropdown_qml()
                ElementTree.fromstring(qml)
                changes.append((None, table_name, qml))
                continue
            for style_id, qml in styles:
                ElementTree.fromstring(qml)
                if not dropdown_is_current(qml):
                    updated_qml = synchronized_qml(qml)
                    ElementTree.fromstring(updated_qml)
                    changes.append((style_id, table_name, updated_qml))

        if not apply or not changes:
            return symbol_tables, changes

        counts_before = {
            table_name: connection.execute(
                "SELECT COUNT(*) FROM {}".format(quote_identifier(table_name))
            ).fetchone()[0]
            for table_name in symbol_tables
        }
        connection.execute("BEGIN IMMEDIATE")
        try:
            for style_id, table_name, qml in changes:
                if style_id is None:
                    geometry_column = connection.execute(
                        "SELECT column_name FROM gpkg_geometry_columns "
                        "WHERE table_name = ?",
                        (table_name,),
                    ).fetchone()[0]
                    connection.execute(
                        """INSERT INTO layer_styles
                           (f_table_catalog, f_table_schema, f_table_name,
                            f_geometry_column, styleName, styleQML, styleSLD,
                            useAsDefault, description, owner, ui)
                           VALUES ('', '', ?, ?, 'default', ?, '', 1,
                                   'CoFrance symbol dropdown', '', '')""",
                        (table_name, geometry_column, qml),
                    )
                else:
                    connection.execute(
                        "UPDATE layer_styles SET styleQML = ? WHERE id = ?",
                        (qml, style_id),
                    )
            for table_name, expected_count in counts_before.items():
                actual_count = connection.execute(
                    "SELECT COUNT(*) FROM {}".format(quote_identifier(table_name))
                ).fetchone()[0]
                if actual_count != expected_count:
                    raise RuntimeError("feature count changed for {}".format(table_name))
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("integrity check failed after update")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return symbol_tables, changes
    finally:
        connection.close()


def run(source_directory, apply=False):
    paths = sorted(source_directory.rglob("*.gpkg"))
    symbol_files = 0
    symbol_layers = 0
    changed_files = 0
    changed_styles = 0
    failures = []
    for path in paths:
        try:
            tables, changes = inspect_geopackage(path, apply=apply)
            if tables:
                symbol_files += 1
                symbol_layers += len(tables)
            if changes:
                changed_files += 1
                changed_styles += len(changes)
        except Exception as error:
            failures.append((path, str(error)))
    return {
        "files": len(paths),
        "symbol_files": symbol_files,
        "symbol_layers": symbol_layers,
        "changed_files": changed_files,
        "changed_styles": changed_styles,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--source-directory", type=Path, default=DEFAULT_SOURCE_DIRECTORY
    )
    arguments = parser.parse_args()
    result = run(arguments.source_directory.resolve(), apply=arguments.apply)
    mode = "Updated" if arguments.apply else "Would update"
    print("GeoPackages scanned: {}".format(result["files"]))
    print("Symbol GeoPackages: {}".format(result["symbol_files"]))
    print("Symbol layers: {}".format(result["symbol_layers"]))
    print("{} files: {}".format(mode, result["changed_files"]))
    print("{} styles: {}".format(mode, result["changed_styles"]))
    if result["failures"]:
        for path, error in result["failures"]:
            print("ERROR {}: {}".format(path, error))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
