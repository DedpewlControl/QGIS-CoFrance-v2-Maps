"""Tests for the QGIS-independent CoFrance v2 schema rules.

Run from the repository root with:
    python -m unittest geojsonStyler.test_plugin
"""

import unittest

from .cofrance_schema import (
    ACTIVATION_FIELDS,
    CoFranceSchemaError,
    LAYER_FIELDS,
    build_activation,
    detect_layer_kind,
    missing_fields,
    normalize_runways,
    round_coordinates,
    stable_json,
    validate_layer_fields,
    validate_symbol_type,
)


class LayerSchemaTests(unittest.TestCase):
    def test_all_templates_include_activation_fields(self):
        for fields in LAYER_FIELDS.values():
            for activation_field in ACTIVATION_FIELDS:
                self.assertIn(activation_field, fields)

    def test_detects_all_four_layer_kinds(self):
        activation = list(ACTIVATION_FIELDS)
        self.assertEqual(
            detect_layer_kind("polygon", ["name"] + activation), "polygon"
        )
        self.assertEqual(detect_layer_kind("line", ["name"] + activation), "line")
        self.assertEqual(
            detect_layer_kind("point", ["name", "symbol_type"] + activation),
            "symbol",
        )
        self.assertEqual(
            detect_layer_kind("point", ["uuid", "text"] + activation), "text"
        )

    def test_field_names_are_case_insensitive(self):
        fields = ["NAME", "ACTIVATION_ICAO", "ACTIVATION_ARR", "ACTIVATION_DEP"]
        self.assertEqual(missing_fields("line", fields), [])

    def test_ambiguous_point_layer_is_rejected(self):
        fields = ["name", "symbol_type", "uuid", "text"] + list(ACTIVATION_FIELDS)
        with self.assertRaisesRegex(CoFranceSchemaError, "ambiguous"):
            detect_layer_kind("point", fields)

    def test_missing_template_field_is_reported(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "activation_dep"):
            validate_layer_fields(
                "polygon", ["name", "activation_icao", "activation_arr"]
            )


class ActivationTests(unittest.TestCase):
    def test_empty_activation_is_omitted(self):
        self.assertIsNone(build_activation(None, "", None))

    def test_arrival_and_departure_activation(self):
        self.assertEqual(
            build_activation(" lfmn ", "04l, 04R", "22l 22R"),
            {
                "activeRunways": {
                    "LFMN": {"arr": "04L 04R", "dep": "22L 22R"}
                }
            },
        )

    def test_runways_are_normalized_and_deduplicated(self):
        self.assertEqual(normalize_runways("04L, 04l; 04R"), "04L 04R")

    def test_runway_requires_icao(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "activation_icao"):
            build_activation(None, "04L", None)

    def test_icao_requires_a_runway(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "at least one runway"):
            build_activation("LFMN", None, None)

    def test_invalid_icao_is_rejected(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "four letters"):
            build_activation("MN", "04L", None)

    def test_invalid_runway_is_rejected(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "Invalid runway"):
            build_activation("LFMN", "37L", None)


class SymbolAndGroupingTests(unittest.TestCase):
    def test_supported_symbol_is_normalized(self):
        self.assertEqual(validate_symbol_type(" VOR_CLASSIC "), "vor_classic")
        self.assertEqual(validate_symbol_type("diamond_cross"), "diamond_cross")
        self.assertEqual(validate_symbol_type("cross"), "cross")

    def test_unknown_symbol_is_rejected(self):
        with self.assertRaisesRegex(CoFranceSchemaError, "Unsupported"):
            validate_symbol_type("star")

    def test_stable_json_ignores_dictionary_order(self):
        self.assertEqual(stable_json({"b": 2, "a": 1}), stable_json({"a": 1, "b": 2}))

    def test_coordinates_are_limited_to_six_decimal_places(self):
        coordinates = [
            [7.123456789, 43.987654321],
            [-0.0000001, 7],
        ]
        self.assertEqual(
            round_coordinates(coordinates),
            [[7.123457, 43.987654], [0.0, 7]],
        )


if __name__ == "__main__":
    unittest.main()
