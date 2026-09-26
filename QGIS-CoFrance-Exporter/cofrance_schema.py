"""Pure-Python schema helpers for CoFrance v2 exports.

This module deliberately has no QGIS imports, so its validation rules can be
tested without starting QGIS.
"""

import json
import re


REQUIRED_ACTIVATION_FIELDS = (
    "activation_icao",
    "activation_arr",
    "activation_dep",
)

ACTIVATION_FIELDS = REQUIRED_ACTIVATION_FIELDS + (
    "activation_unactive_icao",
    "activation_unactive_arr",
    "activation_unactive_dep",
    "activation_sector_me",
    "activation_sector_others",
)

DISPLAY_FIELDS = ("z_index", "zoomin", "zoomout")

LAYER_FIELDS = {
    "polygon": ("name",) + REQUIRED_ACTIVATION_FIELDS,
    "line": ("name",) + REQUIRED_ACTIVATION_FIELDS,
    "symbol": ("name", "symbol_type") + REQUIRED_ACTIVATION_FIELDS,
    "text": ("uuid", "text") + REQUIRED_ACTIVATION_FIELDS,
}

# Symbol identifiers supported by CoFrance v2.
SUPPORTED_SYMBOL_TYPES = (
    "point",
    "circle",
    "square",
    "diamond",
    "diamond_cross",
    "circle_cross",
    "cross",
    "cross_large",
    "x",
    "asterix",
    "triangle_hollow",
    "triangle_filled",
    "triangle_hollow_thick_bottom_border",
    "triangle_with_circle_rings",
    "circle_with_outer_rings",
    "vor",
    "vor_dme",
    "dme",
    "ndb",
    "navaid",
    "tacan",
    "vortac",
    "vor_classic",
    "ndb_classic",
    "aerodrome",
    "aerodrome_paved",
    "aerodrome_ticks",
    "aerodrome_paved_ticks",
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


def _runway_condition(condition, field_prefix, icao, arrivals, departures):
    raw_values = (icao, arrivals, departures)
    if all(is_blank(value) for value in raw_values):
        return None

    normalized_icao = "" if is_blank(icao) else str(icao).strip().upper()
    if not normalized_icao:
        raise CoFranceSchemaError(
            "{}_icao is required when arrival or departure runways are set.".format(
                field_prefix
            )
        )
    if not _ICAO_RE.fullmatch(normalized_icao):
        raise CoFranceSchemaError(
            "{}_icao must contain exactly four letters.".format(field_prefix)
        )

    arr = normalize_runways(arrivals)
    dep = normalize_runways(departures)
    if not arr and not dep:
        raise CoFranceSchemaError(
            "Enter at least one runway in {}_arr or {}_dep.".format(
                field_prefix, field_prefix
            )
        )

    runway_rule = {}
    if arr:
        runway_rule["arr"] = arr
    if dep:
        runway_rule["dep"] = dep

    return {condition: {normalized_icao: runway_rule}}


def normalize_sector_conditions(value):
    """Normalize comma/semicolon-separated sector ownership expressions."""
    if is_blank(value):
        return []
    sectors = []
    for raw_token in re.split(r"[,;\n]+", str(value)):
        token = re.sub(r"\s+", "", raw_token).upper()
        if not token:
            continue
        if not re.fullmatch(r"[A-Z0-9_-]+(?:\+[A-Z0-9_-]+)*", token):
            raise CoFranceSchemaError(
                "Invalid sector ownership expression: {}".format(raw_token.strip())
            )
        if token not in sectors:
            sectors.append(token)
    return sectors


def build_activation(
    icao,
    arrivals,
    departures,
    unactive_icao=None,
    unactive_arrivals=None,
    unactive_departures=None,
    sector_owned_by_me=None,
    sector_owned_by_others=None,
):
    """Create the optional CoFrance activation object from QGIS fields."""
    raw_values = (
        icao,
        arrivals,
        departures,
        unactive_icao,
        unactive_arrivals,
        unactive_departures,
        sector_owned_by_me,
        sector_owned_by_others,
    )
    if all(is_blank(value) for value in raw_values):
        return None

    activation = {}
    active = _runway_condition(
        "activeRunways", "activation", icao, arrivals, departures
    )
    if active:
        activation.update(active)
    unactive = _runway_condition(
        "unactiveRunway",
        "activation_unactive",
        unactive_icao,
        unactive_arrivals,
        unactive_departures,
    )
    if unactive:
        activation.update(unactive)

    owned_by_me = normalize_sector_conditions(sector_owned_by_me)
    owned_by_others = normalize_sector_conditions(sector_owned_by_others)
    if owned_by_me:
        activation["sectorOwnedByMe"] = owned_by_me
    if owned_by_others:
        activation["sectorOwnedByOthers"] = owned_by_others
    return activation or None


def normalize_optional_integer(value, field_name):
    """Return an optional integer from a QGIS value or raise a readable error."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise CoFranceSchemaError("{} must be an integer.".format(field_name))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CoFranceSchemaError("{} must be an integer.".format(field_name))
    if not number.is_integer():
        raise CoFranceSchemaError("{} must be an integer.".format(field_name))
    return int(number)


def build_display_properties(z_index=None, zoomin=None, zoomout=None):
    """Build optional feature draw-order and zoom properties."""
    normalized = {
        "z-index": normalize_optional_integer(z_index, "z_index"),
        "zoomin": normalize_optional_integer(zoomin, "zoomin"),
        "zoomout": normalize_optional_integer(zoomout, "zoomout"),
    }
    if (
        normalized["zoomin"] is not None
        and normalized["zoomout"] is not None
        and normalized["zoomin"] > normalized["zoomout"]
    ):
        raise CoFranceSchemaError("zoomin must be less than or equal to zoomout.")
    return {key: value for key, value in normalized.items() if value is not None}


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
