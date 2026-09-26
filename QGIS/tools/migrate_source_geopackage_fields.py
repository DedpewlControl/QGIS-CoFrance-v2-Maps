"""Add current CoFrance optional fields to every source GeoPackage.

Run without ``--apply`` for an audit. With ``--apply``, each GeoPackage is
updated in its own transaction and checked for SQLite integrity and unchanged
feature counts before the transaction is committed.
"""

import argparse
from pathlib import Path
import sqlite3


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIRECTORY = REPOSITORY_ROOT / "QGIS" / "src"

FIELDS = (
    ("z_index", "INTEGER"),
    ("zoomin", "INTEGER"),
    ("zoomout", "INTEGER"),
    ("activation_icao", "TEXT"),
    ("activation_arr", "TEXT"),
    ("activation_dep", "TEXT"),
    ("activation_unactive_icao", "TEXT"),
    ("activation_unactive_arr", "TEXT"),
    ("activation_unactive_dep", "TEXT"),
    ("activation_sector_me", "TEXT"),
    ("activation_sector_others", "TEXT"),
)


def quote_identifier(value):
    return '"{}"'.format(str(value).replace('"', '""'))


def feature_tables(connection):
    return [
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM gpkg_contents "
            "WHERE data_type = 'features' ORDER BY table_name"
        )
    ]


def missing_fields(connection, table_name):
    existing = {
        row[1].lower()
        for row in connection.execute(
            "PRAGMA table_info({})".format(quote_identifier(table_name))
        )
    }
    return [(name, field_type) for name, field_type in FIELDS if name not in existing]


def inspect_geopackage(path, apply=False):
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity check failed before migration")
        tables = feature_tables(connection)
        if not tables:
            raise RuntimeError("contains no registered feature tables")
        changes = []
        for table_name in tables:
            missing = missing_fields(connection, table_name)
            if missing:
                changes.append((table_name, missing))
        if not apply or not changes:
            return tables, changes

        counts_before = {
            table_name: connection.execute(
                "SELECT COUNT(*) FROM {}".format(quote_identifier(table_name))
            ).fetchone()[0]
            for table_name in tables
        }
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table_name, missing in changes:
                for field_name, field_type in missing:
                    connection.execute(
                        "ALTER TABLE {} ADD COLUMN {} {}".format(
                            quote_identifier(table_name),
                            quote_identifier(field_name),
                            field_type,
                        )
                    )
            for table_name, expected_count in counts_before.items():
                actual_count = connection.execute(
                    "SELECT COUNT(*) FROM {}".format(quote_identifier(table_name))
                ).fetchone()[0]
                if actual_count != expected_count:
                    raise RuntimeError(
                        "feature count changed for {}: {} -> {}".format(
                            table_name, expected_count, actual_count
                        )
                    )
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("integrity check failed after migration")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return tables, changes
    finally:
        connection.close()


def run(source_directory, apply=False):
    paths = sorted(source_directory.rglob("*.gpkg"))
    changed_files = 0
    changed_layers = 0
    added_columns = 0
    failures = []
    for path in paths:
        try:
            _tables, changes = inspect_geopackage(path, apply=apply)
            if changes:
                changed_files += 1
                changed_layers += len(changes)
                added_columns += sum(len(fields) for _table, fields in changes)
        except Exception as error:
            failures.append((path, str(error)))
    return {
        "files": len(paths),
        "changed_files": changed_files,
        "changed_layers": changed_layers,
        "added_columns": added_columns,
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
    mode = "Migrated" if arguments.apply else "Would migrate"
    print("GeoPackages scanned: {}".format(result["files"]))
    print("{}: {}".format(mode, result["changed_files"]))
    print("Feature layers affected: {}".format(result["changed_layers"]))
    print("Columns added: {}".format(result["added_columns"]))
    if result["failures"]:
        for path, error in result["failures"]:
            print("ERROR {}: {}".format(path, error))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
