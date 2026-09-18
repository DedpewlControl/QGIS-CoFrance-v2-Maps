"""Pure-Python schema helpers for CoFrance v2 exports.

This module deliberately has no QGIS imports, so its validation rules can be
tested without starting QGIS.
"""

import json
import re


ACTIVATION_FIELDS = (
    "activation_icao",
    "activation_arr",
    "activation_dep",
)

LAYER_FIELDS = {
    "polygon": ("name",) + ACTIVATION_FIELDS,
    "line": ("name",) + ACTIVATION_FIELDS,
    "symbol": ("name", "symbol_type") + ACTIVATION_FIELDS,
    "text": ("uuid", "text") + ACTIVATION_FIELDS,
}

# Symbol identifiers supported by CoFrance v2.
SUPPORTED_SYMBOL_TYPES = (
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
    "vor_dme",
    "dme",
    "vor",
    "ndb",
    "navaid",
    "tacan",
    "vortac",
)

_ICAO_RE = re.compile(r"^[A-Z]{4}$")
_RUNWAY_RE = re.compile(r"^(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?$")


class CoFranceSchemaError(ValueError):
    """Raised when a layer or feature cannot be represented in CoFrance v2."""


def is_blank(value):
    """Return True for values that should be treated as an empty QGIS field."""
    return value is None or (isinstance(value, str) and not value.strip())


def normalized_field_map(field_names):
    """Map lower-case field names to their original spelling."""
    return {str(name).lower(): str(name) for name in field_names}


def missing_fields(layer_kind, field_names):
    """Return required template fields absent from a layer."""
    existing = set(normalized_field_map(field_names))
    return [name for name in LAYER_FIELDS[layer_kind] if name not in existing]


def detect_layer_kind(geometry_family, field_names):
    """Classify a QGIS layer from its geometry family and template fields."""
    geometry_family = str(geometry_family).lower()
    fields = set(normalized_field_map(field_names))

    if geometry_family == "polygon":
        return "polygon"
    if geometry_family == "line":
        return "line"
    if geometry_family != "point":
        raise CoFranceSchemaError(
            "Only polygon, line, and point geometry layers can be exported."
        )

    is_symbol = {"name", "symbol_type"}.issubset(fields)
    is_text = {"uuid", "text"}.issubset(fields)
    if is_symbol and is_text:
        raise CoFranceSchemaError(
            "Point layer is ambiguous: it has both symbol and text template fields."
        )
    if is_symbol:
        return "symbol"
    if is_text:
        return "text"
    raise CoFranceSchemaError(
        "Point layer must contain name + symbol_type, or uuid + text."
    )


def validate_layer_fields(layer_kind, field_names):
    """Raise a readable error when a layer does not follow its template."""
    missing = missing_fields(layer_kind, field_names)
    if missing:
        raise CoFranceSchemaError(
            "Missing template field{}: {}".format(
                "s" if len(missing) != 1 else "", ", ".join(missing)
            )
        )


def normalize_runways(value):
    """Normalize runway designators into a CoFrance string array."""
    if is_blank(value):
        return []

    tokens = [
        token.upper()
        for token in re.split(r"[\s,;]+", str(value).strip())
        if token
    ]
    invalid = [token for token in tokens if not _RUNWAY_RE.fullmatch(token)]
    if invalid:
        raise CoFranceSchemaError(
            "Invalid runway designator{}: {}".format(
                "s" if len(invalid) != 1 else "", ", ".join(invalid)
            )
        )

    # Remove duplicates without changing the order entered in QGIS.
    return list(dict.fromkeys(tokens))


def build_activation(icao, arrivals, departures):
    """Create an optional CoFrance activeRunways object from template fields."""
    raw_values = (icao, arrivals, departures)
    if all(is_blank(value) for value in raw_values):
        return None

    normalized_icao = "" if is_blank(icao) else str(icao).strip().upper()
    if not normalized_icao:
        raise CoFranceSchemaError(
            "activation_icao is required when arrival or departure runways are set."
        )
    if not _ICAO_RE.fullmatch(normalized_icao):
        raise CoFranceSchemaError(
            "activation_icao must contain exactly four letters."
        )

    arr = normalize_runways(arrivals)
    dep = normalize_runways(departures)
    if not arr and not dep:
        raise CoFranceSchemaError(
            "Enter at least one runway in activation_arr or activation_dep."
        )

    runway_rule = {}
    if arr:
        runway_rule["arr"] = arr
    if dep:
        runway_rule["dep"] = dep

    return {"activeRunways": {normalized_icao: runway_rule}}


def validate_symbol_type(value):
    """Return a normalized supported CoFrance symbol identifier."""
    if is_blank(value):
        raise CoFranceSchemaError("symbol_type cannot be empty.")
    symbol_type = str(value).strip().lower()
    if symbol_type not in SUPPORTED_SYMBOL_TYPES:
        raise CoFranceSchemaError(
            "Unsupported symbol_type {!r}. Supported values: {}".format(
                symbol_type, ", ".join(SUPPORTED_SYMBOL_TYPES)
            )
        )
    return symbol_type


def stable_json(value):
    """Return a deterministic JSON string suitable for a grouping key."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def round_coordinates(value, precision=6):
    """Recursively round a GeoJSON coordinate array to a maximum precision."""
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item, precision) for item in value]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoFranceSchemaError("GeoJSON coordinates must contain only numbers.")
    if isinstance(value, int):
        return value
    rounded = round(value, precision)
    # Avoid emitting negative zero after rounding a very small coordinate.
    return 0.0 if rounded == 0 else rounded
