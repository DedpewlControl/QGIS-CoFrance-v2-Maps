"""Recover editable CoFrance GeoPackages from exported v2 GeoJSON.

Install with Processing Toolbox > Scripts > Add Script to Toolbox.
"""

from contextlib import closing
from datetime import datetime, timezone
import gc
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    Qgis, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsJsonUtils,
    QgsLayerTreeGroup, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings,
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingOutputMultipleLayers,
    QgsProcessingOutputNumber, QgsProcessingOutputString, QgsProcessingParameterEnum,
    QgsProcessingParameterFile, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString, QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer, QgsTextFormat,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)

TEMPLATE_FOLDER = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\templates"
)
TEMPLATES = {"polygon": "polygons", "line": "lines", "symbol": "symbols", "text": "text"}
SYMBOL_TYPES = (
    "point", "circle", "square", "diamond", "diamond_cross", "circle_cross",
    "cross", "cross_large", "x", "asterix", "triangle_hollow", "triangle_filled",
    "triangle_hollow_thick_bottom_border", "triangle_with_circle_rings",
    "circle_with_outer_rings", "vor", "vor_dme", "dme", "ndb", "navaid", "tacan",
    "vortac", "vor_classic", "ndb_classic", "aerodrome", "aerodrome_paved",
    "aerodrome_ticks", "aerodrome_paved_ticks",
)
SVG_FILES = {"vor": "VOR.svg", "vor_dme": "VOR_DME.svg", "dme": "DME.svg",
             "ndb": "NDB.svg", "tacan": "TACAN.svg", "vortac": "VORTAC.svg"}
DISPLAY_FIELDS = {"z-index": "z_index", "zoomin": "zoomin", "zoomout": "zoomout"}


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", str(value)).strip(" ._")[:110]
    if not name:
        name = "Imported_layer"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.I):
        name = "_" + name
    return name


def child_group(parent, name):
    return next((node for node in parent.children()
                 if isinstance(node, QgsLayerTreeGroup) and node.name().casefold() == name.casefold()), None)


def note(notes, message):
    if message not in notes:
        notes.append(message)


def owner_group(stem, base):
    if not re.match(r"^\d+(?:\.\d+)?_", stem):
        return "0_" + base
    stem = safe_name(stem)
    # Restore the combined ARPs and separate VFR names to their original VFR group.
    match = re.fullmatch(r"(4_(?:CCA|EGA)_.+?)(?:_VFR)?_ARPs?(?:_Names?)?", stem, re.I)
    if match:
        return match.group(1) + "_VFR"
    match = re.fullmatch(r"(4_(?:CCA|EGA)_.+?_VFR)_Point_Names", stem, re.I)
    return match.group(1) if match else stem


def activation_fields(activation):
    if not activation:
        return {}
    if not isinstance(activation, dict):
        raise ValueError("activation must be an object")
    supported = {"activeRunways", "unactiveRunway", "sectorOwnedByMe", "sectorOwnedByOthers"}
    if set(activation) - supported:
        raise ValueError("Unsupported activation rules: " + ", ".join(sorted(set(activation) - supported)))
    result = {}
    for key, prefix in (("activeRunways", "activation"), ("unactiveRunway", "activation_unactive")):
        rule = activation.get(key)
        if not rule:
            continue
        if not isinstance(rule, dict) or len(rule) != 1:
            raise ValueError(key + " must contain one airport to fit the current QGIS activation fields")
        icao, runways = next(iter(rule.items()))
        if not re.fullmatch(r"[A-Za-z]{4}", icao) or not isinstance(runways, dict):
            raise ValueError("Invalid runway activation airport/rules")
        if set(runways) - {"arr", "dep"}:
            raise ValueError("Unsupported runway condition in " + key)
        result[prefix + "_icao"] = icao.upper()
        for direction in ("arr", "dep"):
            values = runways.get(direction, [])
            if not isinstance(values, list) or any(
                    not isinstance(v, str) or not re.fullmatch(r"(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?", v.upper())
                    for v in values):
                raise ValueError("Invalid runway list in " + key)
            if values:
                result[prefix + "_" + direction] = ", ".join(v.upper() for v in values)
        if not result.get(prefix + "_arr") and not result.get(prefix + "_dep"):
            raise ValueError(key + " must contain arrival or departure runways")
    for key, field in (("sectorOwnedByMe", "activation_sector_me"),
                       ("sectorOwnedByOthers", "activation_sector_others")):
        if key in activation:
            values = activation[key]
            if not isinstance(values, list) or any(
                    not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*", v)
                    for v in values):
                raise ValueError("Invalid sector list in " + key)
            if values:
                result[field] = ", ".join(values)
    return result


def number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(name + " is out of range")
    return float(value)


def color(value, default=(0, 0, 0), opacity=1):
    value = default if value is None else value
    if isinstance(value, str):
        result = QColor(value)
        if not result.isValid():
            raise ValueError("Invalid style color: " + value)
    elif isinstance(value, list) or isinstance(value, tuple):
        if len(value) not in (3, 4):
            raise ValueError("Style colors need three RGB values")
        channels = [int(number(v, "color channel", 0, 255)) for v in value]
        result = QColor(*channels)
    else:
        raise ValueError("Invalid style color")
    result.setAlphaF(result.alphaF() * number(opacity, "opacity", 0, 1))
    return result


def prepare_feature(feature, path, index, notes):
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError("Expected a GeoJSON Feature")
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("Feature properties must be an object")
    raw_geometry = feature.get("geometry")
    if not raw_geometry:
        note(notes, str(path) + ": empty geometries were skipped")
        return None
    if (isinstance(raw_geometry, dict)
            and raw_geometry.get("type") in ("Point", "MultiPoint", "LineString",
                                             "MultiLineString", "Polygon", "MultiPolygon")
            and raw_geometry.get("coordinates") == []):
        note(notes, "{}: feature {} has empty coordinates; skipped".format(path, index))
        return None
    geometry = QgsJsonUtils.geometryFromGeoJson(compact(raw_geometry))
    if geometry.isNull() or geometry.isEmpty():
        raise ValueError("Geometry is empty or cannot be read")
    family = geometry.type()
    if family == QgsWkbTypes.PointGeometry:
        if "textStyle" in properties:
            if "pointStyle" in properties:
                raise ValueError("Point has both textStyle and pointStyle")
            kind = "text"
        elif "pointStyle" in properties:
            kind = "symbol"
        else:
            raise ValueError("Points require CoFrance textStyle or pointStyle; this may not be a map export")
    elif family == QgsWkbTypes.LineGeometry:
        kind = "line"
    elif family == QgsWkbTypes.PolygonGeometry:
        kind = "polygon"
    else:
        raise ValueError("Unsupported geometry family")
    attrs = activation_fields(properties.get("activation"))
    for key, field in DISPLAY_FIELDS.items():
        if key in properties and properties[key] is not None:
            value = number(properties[key], key)
            if not value.is_integer():
                raise ValueError(key + " must be an integer")
            attrs[field] = int(value)
    if attrs.get("zoomin") is not None and attrs.get("zoomout") is not None and attrs["zoomin"] > attrs["zoomout"]:
        raise ValueError("zoomin exceeds zoomout")
    style_names = {"polygon": ("fillStyle", "lineStyle"), "line": ("lineStyle",),
                   "symbol": ("pointStyle",), "text": ("textStyle",)}[kind]
    style = {}
    for key in style_names:
        if key in properties:
            if not isinstance(properties[key], dict):
                raise ValueError(key + " must be an object")
            style[key] = dict(properties[key])
            # Older exports explicitly use null for a solid stroke. Keep the
            # original value in source_properties, but use QGIS's solid default.
            if key == "lineStyle" and style[key].get("dashArray") is None:
                style[key].pop("dashArray", None)
    if kind == "text":
        text = style["textStyle"].pop("text", None)
        if text is None or not str(text).strip():
            raise ValueError("textStyle.text cannot be empty")
        identifier = str(properties.get("uuid") or path.stem).strip()
        attrs.update(uuid=identifier, text=str(text))
        if not properties.get("uuid"):
            note(notes, str(path) + ": a missing text uuid was inferred from the filename")
    else:
        identifier = str(properties.get("name") or path.stem).strip()
        attrs["name"] = identifier
        if not properties.get("name"):
            note(notes, str(path) + ": a missing feature name was inferred from the filename")
        if kind == "symbol":
            original = str(style["pointStyle"].get("type", "")).lower()
            symbol_type = {"fix_filled": "triangle_filled"}.get(original, original)
            if symbol_type not in SYMBOL_TYPES:
                raise ValueError("Unsupported pointStyle.type: " + original)
            attrs["symbol_type"] = symbol_type
            style["pointStyle"]["type"] = symbol_type
            if original != symbol_type:
                note(notes, "Legacy fix_filled symbols were mapped to triangle_filled")
    known = {"name", "uuid", "activation", *DISPLAY_FIELDS, *style_names}
    if set(properties) - known:
        note(notes, "Additional properties are retained in source_properties; the exporter may not emit them")
    attrs["source_properties"] = compact(properties)
    attrs["source_file"] = str(path)
    attrs["source_index"] = index
    parts = geometry.asGeometryCollection() if geometry.isMultipart() else [geometry]
    if kind == "text" and len(parts) > 1:
        note(notes, "Multipart text was expanded into separate editable labels at every exported point")
    for part in parts:
        part.convertToMultiType()
    return kind, identifier, style, attrs, parts


def plan_import(files, base, notes, feedback):
    plans = []
    counters = {}
    source_count = skipped = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as error:
            raise QgsProcessingException("Cannot read '{}': {}".format(path, error)) from error
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
            raise QgsProcessingException(str(path) + " must be a GeoJSON FeatureCollection")
        crs = (data.get("crs") or {}).get("properties", {}).get("name", "")
        if crs and not ("4326" in crs or "CRS84" in crs.upper()):
            raise QgsProcessingException(str(path) + ": only WGS84 longitude/latitude GeoJSON is supported")
        owner = owner_group(path.stem, base)
        is_arp_file = bool(re.search(r"(?:^|_)ARPs?(?:_Names?)?$", path.stem, re.I))
        previous = None
        for index, feature in enumerate(data["features"], 1):
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs")
            source_count += 1
            try:
                prepared = prepare_feature(feature, path, index, notes)
                if prepared is None:
                    skipped += 1
                    continue
                kind, identifier, style, attrs, parts = prepared
                # Validate styles before any output is written.
                for key, value in style.items():
                    color(value.get("color"), opacity=value.get("opacity", 1))
                key = (owner, kind, identifier, compact(style), parts[0].wkbType())
            except (ValueError, TypeError, RuntimeError) as error:
                raise QgsProcessingException("{}: feature {}: {}".format(path, index, error)) from error
            if key != previous:
                layer_name = safe_name(identifier)
                if owner.endswith("_VFR") and is_arp_file and kind in ("text", "symbol"):
                    layer_name = re.sub(r"^4_", "", owner[:-4]) + ("_ARP_Names" if kind == "text" else "_ARPs")
                counter_key = (owner.casefold(), layer_name.casefold())
                counters[counter_key] = counters.get(counter_key, 0) + 1
                occurrence = counters[counter_key]
                if occurrence > 1:
                    suffix = re.search(r"(_ARPs|_ARP_Names)$", layer_name, re.I)
                    layer_name = (layer_name[:suffix.start()] + "_" + str(occurrence).zfill(2) + suffix.group()
                                  if suffix else layer_name + "_" + str(occurrence).zfill(2))
                plans.append({"owner": owner, "name": layer_name, "kind": kind, "style": style,
                              "wkb_type": parts[0].wkbType(), "rows": []})
            plans[-1]["rows"].extend((attrs, part) for part in parts)
            previous = key
    if not plans:
        raise QgsProcessingException("No supported map features were found")
    return plans, source_count, skipped


def css_weight(value, notes):
    weight = int(number(value, "fontWeight", 1))
    qt_weights = {300: 25, 400: 50, 500: 57, 600: 75, 700: 87, 900: 99}
    chosen = min(qt_weights, key=lambda candidate: abs(candidate - weight))
    if chosen != weight:
        note(notes, "Some font weights were approximated to weights supported by the current exporter; originals remain in source_properties")
    return qt_weights[chosen]


def marker_symbol(style, assets, notes):
    kind = style["type"]
    tint = color(style.get("color"), (255, 255, 255))
    size = number(style.get("size", 6), "point size", 0.01)
    if "size" in style or "opacity" in style:
        note(notes, "Point size/opacity are restored for preview but the current exporter does not emit those properties")
    if kind in SVG_FILES and (assets / SVG_FILES[kind]).is_file():
        symbol = QgsMarkerSymbol()
        svg = QgsSvgMarkerSymbolLayer(str(assets / SVG_FILES[kind]))
        svg.setColor(tint)
        symbol.changeSymbolLayer(0, svg)
        note(notes, "Navigation symbols use the bundled QGIS SVG artwork; browser and QGIS previews may differ")
    else:
        shapes = {"point": "circle", "circle": "circle", "square": "square", "diamond": "diamond",
                  "diamond_cross": "diamond", "circle_cross": "circle", "cross": "cross",
                  "cross_large": "cross", "x": "cross2", "asterix": "star",
                  "triangle_hollow": "triangle", "triangle_filled": "triangle",
                  "triangle_hollow_thick_bottom_border": "triangle",
                  "triangle_with_circle_rings": "triangle", "circle_with_outer_rings": "circle"}
        shape = shapes.get(kind, "circle")
        hollow = kind in ("circle", "square", "diamond", "diamond_cross", "circle_cross",
                          "triangle_hollow", "triangle_hollow_thick_bottom_border",
                          "triangle_with_circle_rings", "circle_with_outer_rings")
        fill = QColor(tint)
        if hollow:
            fill.setAlpha(0)
        symbol = QgsMarkerSymbol.createSimple({"name": shape, "color": fill.name(QColor.HexArgb),
                                               "outline_color": tint.name(), "outline_width": "0.25"})
        # QColor HexArgb strings are not the native QGIS r,g,b,a property syntax.
        symbol.symbolLayer(0).setColor(fill)
        symbol.symbolLayer(0).setStrokeColor(tint)
        if kind in ("diamond_cross", "circle_cross"):
            cross = QgsSimpleMarkerSymbolLayer()
            cross.setShape(QgsSimpleMarkerSymbolLayer.Cross)
            cross.setColor(tint)
            cross.setStrokeColor(tint)
            symbol.appendSymbolLayer(cross)
        if kind not in shapes or kind in ("asterix", "triangle_hollow_thick_bottom_border",
                                         "triangle_with_circle_rings", "circle_with_outer_rings"):
            note(notes, "Some specialised symbols use an approximate QGIS preview; symbol_type is preserved for export")
    symbol.setSize(size)
    symbol.setSizeUnit(Qgis.RenderUnit.Pixels)
    symbol.setOpacity(number(style.get("opacity", 1), "point opacity", 0, 1))
    return symbol


def apply_style(layer, kind, styles, assets, notes):
    if kind == "text":
        style = styles.get("textStyle", {})
        family = str(style.get("fontFamily") or "Arial")
        font = QFont(family)
        font.setWeight(css_weight(style.get("fontWeight", 400), notes))
        text_format = QgsTextFormat()
        text_format.setFont(font)
        text_format.setFamilies([family])
        text_format.setNamedStyle("")
        text_format.setSize(number(style.get("fontSize", 12), "font size", 1))
        text_format.setSizeUnit(Qgis.RenderUnit.Points)
        text_format.setColor(color(style.get("color")))
        settings = QgsPalLayerSettings()
        settings.fieldName = "text"
        settings.isExpression = False
        settings.placement = Qgis.LabelPlacement.OverPoint
        settings.quadOffset = QgsPalLayerSettings.QuadrantOver
        settings.xOffset = settings.yOffset = 0
        settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": "0,0,0,0", "outline_style": "no", "size": "0"})))
        if float(style.get("fontSize", 12)) % 1:
            note(notes, "Fractional text sizes are stored in QGIS; the current exporter rounds them down")
        return
    layer.setLabelsEnabled(False)
    if kind == "symbol":
        symbol = marker_symbol(styles["pointStyle"], assets, notes)
    elif kind == "line":
        style = styles.get("lineStyle", {})
        symbol = QgsLineSymbol.createSimple({})
        simple = symbol.symbolLayer(0)
        simple.setColor(color(style.get("color"), opacity=style.get("opacity", 1)))
        simple.setWidth(number(style.get("width", 1), "line width", 0))
        simple.setWidthUnit(Qgis.RenderUnit.Pixels)
        if "dashArray" in style:
            values = style["dashArray"]
            if not isinstance(values, list) or len(values) < 2:
                raise ValueError("dashArray must contain at least two lengths")
            simple.setUseCustomDashPattern(True)
            simple.setCustomDashVector([number(v, "dash length", 0.001) for v in values])
            simple.setCustomDashPatternUnit(Qgis.RenderUnit.Pixels)
            if len(values) > 2:
                note(notes, "Long dash arrays are stored for preview; the current exporter only emits the first two values")
        if "opacity" in style:
            note(notes, "Line opacity is restored for preview but the current exporter does not emit it")
        if float(style.get("width", 1)) % 1 or float(style.get("width", 1)) < 1:
            note(notes, "Line widths are preserved for preview; the current exporter emits integer widths of at least one")
    else:
        fill = styles.get("fillStyle", {"opacity": 0})
        outline = styles.get("lineStyle")
        symbol = QgsFillSymbol.createSimple({})
        simple = symbol.symbolLayer(0)
        simple.setColor(color(fill.get("color"), (255, 255, 255), fill.get("opacity", 1)))
        if outline is None:
            simple.setStrokeStyle(Qt.NoPen)
        else:
            simple.setStrokeColor(color(outline.get("color"), opacity=outline.get("opacity", 1)))
            simple.setStrokeWidth(number(outline.get("width", 1), "outline width", 0))
            simple.setStrokeWidthUnit(Qgis.RenderUnit.Pixels)
            dash = outline.get("dashArray")
            if dash is not None:
                if dash == [4, 2]:
                    simple.setStrokeStyle(Qt.DashLine)
                elif dash == [1, 2]:
                    simple.setStrokeStyle(Qt.DotLine)
                else:
                    simple.setStrokeStyle(Qt.DashLine)
                    note(notes, "A custom polygon outline dash was approximated; the original remains in source_properties")
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def clone_template(template, path, name, wkb_type):
    with closing(sqlite3.connect(template.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        table = source.execute("SELECT table_name FROM gpkg_geometry_columns").fetchone()[0]
        if source.execute('SELECT count(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]:
            raise QgsProcessingException("Template is not empty: " + str(template))
        with closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
    dataset = None
    try:
        with gdal.ExceptionMgr():
            dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR | gdal.OF_UPDATE)
            dataset.ExecuteSQL('ALTER TABLE "' + table.replace('"', '""') +
                               '" RENAME TO "' + name.replace('"', '""') + '"')
    finally:
        if dataset is not None:
            dataset.Close()
        dataset = None
    with closing(sqlite3.connect(path)) as db:
        # Empty templates are 2D. Match the source dimensions before opening
        # the provider, so exported XYZ geometry is retained without flattening.
        db.execute("UPDATE gpkg_geometry_columns SET z=?, m=? WHERE table_name=?",
                   (int(QgsWkbTypes.hasZ(wkb_type)), int(QgsWkbTypes.hasM(wkb_type)), name))
        db.execute("UPDATE gpkg_contents SET identifier=? WHERE table_name=?", (name, name))
        db.execute("UPDATE layer_styles SET f_table_name=? WHERE f_table_name=?", (name, table))
        db.commit()


def published_style_xml(doc, stage, output):
    # Rewrite decoded XML attribute values, so paths containing & or other
    # XML characters are handled before Qt serialises/escapes the document.
    replacements = ((str(stage).replace("\\", "/"), str(output).replace("\\", "/")),
                    (str(stage), str(output)))
    pending = [doc.documentElement()]
    while pending:
        element = pending.pop()
        attributes = element.attributes()
        values = [(attributes.item(i).nodeName(), attributes.item(i).nodeValue())
                  for i in range(attributes.count())]
        for key, value in values:
            updated = value
            for old, new in replacements:
                updated = updated.replace(old, new)
            if updated != value:
                element.setAttribute(key, updated)
        child = element.firstChildElement()
        while not child.isNull():
            pending.append(child)
            child = child.nextSiblingElement()
    return doc.toString()


def write_layer(plan, stage, output, notes, feedback):
    folder = stage / plan["owner"]
    folder.mkdir(exist_ok=True)
    path = folder / (plan["name"] + ".gpkg")
    template = TEMPLATE_FOLDER / ("cofrance_" + TEMPLATES[plan["kind"]] + ".gpkg")
    clone_template(template, path, plan["name"], plan["wkb_type"])
    layer = None
    try:
        layer = QgsVectorLayer(str(path) + "|layername=" + plan["name"], plan["name"], "ogr")
        if not layer.isValid() or not layer.startEditing():
            raise QgsProcessingException("Cannot edit imported layer " + plan["name"])
        for field in (QgsField("source_properties", QVariant.String), QgsField("source_file", QVariant.String),
                      QgsField("source_index", QVariant.Int)):
            if not layer.addAttribute(field):
                raise QgsProcessingException("Cannot add source tracking fields")
        for attrs, geometry in plan["rows"]:
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; incomplete import discarded")
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            for field, value in attrs.items():
                feature[field] = value
            if not layer.addFeature(feature):
                raise QgsProcessingException("Cannot insert an imported feature")
        if not layer.commitChanges():
            raise QgsProcessingException("; ".join(layer.commitErrors()))
        apply_style(layer, plan["kind"], plan["style"], stage / "svg", notes)
        doc = QDomDocument()
        error = layer.exportNamedStyle(doc)
        if error:
            raise QgsProcessingException(error)
        qml = published_style_xml(doc, stage, output)
    finally:
        if layer is not None:
            if layer.isEditable():
                layer.rollBack()
            # Tracebacks may retain Python wrappers. Release the native OGR
            # connection explicitly so Windows can discard failed staging files.
            sip.delete(layer)
        layer = None
        gc.collect()
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE layer_styles SET styleQML=?,useAsDefault=1 WHERE f_table_name=?",
                   (qml, plan["name"]))
        db.commit()
    return str(Path(plan["owner"]) / (plan["name"] + ".gpkg"))


class ImportCoFranceMap(QgsProcessingAlgorithm):
    def name(self):
        return "import_cofrance_map"

    def displayName(self):
        return "Import exported CoFrance map"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def createInstance(self):
        return ImportCoFranceMap()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return (
            "Rebuild editable GeoPackages from a CoFrance v2 GeoJSON file or a folder of exported map files. "
            "Recreates numbered folders and QGIS groups, fields, point symbols, polygon/line styling and text labels. "
            "Merged geometry is split into editable parts. Text stays at its exported coordinates; no offset is added. "
            "Choose a new or empty output folder. Files are prepared before publishing; existing maps are not overwritten. "
            "Original feature properties are retained in source_properties and limitations are recorded in import_report.json. "
            "The original source layer boundaries, precision and advanced styling cannot always be recovered. "
            "Imported layers appear under Imported CoFrance maps / map name. Save the QGIS project afterward."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            "MODE", "Source", options=["One exported GeoJSON", "Folder of exported GeoJSON files"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFile(
            "INPUT", "Single file: exported GeoJSON", fileFilter="GeoJSON (*.geojson *.json)", optional=True))
        self.addParameter(QgsProcessingParameterFile(
            "INPUT_FOLDER", "Folder mode: exported map folder (scans subfolders)",
            behavior=QgsProcessingParameterFile.Folder, optional=True))
        self.addParameter(QgsProcessingParameterString("MAP_NAME", "Map name (e.g. La Rochelle)"))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "New or empty QGIS map folder"))
        self.addOutput(QgsProcessingOutputMultipleLayers("OUTPUTS", "Imported GeoPackage layers"))
        self.addOutput(QgsProcessingOutputString("REPORT", "Import report"))
        self.addOutput(QgsProcessingOutputNumber("LAYER_COUNT", "Layers created"))
        self.addOutput(QgsProcessingOutputNumber("FEATURE_COUNT", "Editable features created"))
        self.addOutput(QgsProcessingOutputNumber("SKIPPED_COUNT", "Empty source features skipped"))

    def processAlgorithm(self, parameters, context, feedback):
        project = context.project()
        if project is None:
            raise QgsProcessingException("Run with an open QGIS project")
        label = self.parameterAsString(parameters, "MAP_NAME", context).strip()
        if not label or re.search(r"[\x00-\x1f]", label):
            raise QgsProcessingException("Enter a map name")
        parent = child_group(project.layerTreeRoot(), "Imported CoFrance maps")
        if parent is not None and child_group(parent, label) is not None:
            raise QgsProcessingException("An imported map group already has this name; choose a new map name")
        if self.parameterAsEnum(parameters, "MODE", context) == 0:
            value = self.parameterAsFile(parameters, "INPUT", context)
            if not value or not Path(value).is_file():
                raise QgsProcessingException("Choose an exported CoFrance GeoJSON file")
            files = [Path(value).resolve()]
        else:
            value = self.parameterAsFile(parameters, "INPUT_FOLDER", context)
            if not value or not Path(value).is_dir():
                raise QgsProcessingException("Choose the folder containing one map's exported GeoJSON files")
            files = sorted(p.resolve() for p in Path(value).rglob("*")
                           if p.is_file() and p.suffix.lower() == ".geojson")
            if not files:
                raise QgsProcessingException("No GeoJSON files found")
        output_value = self.parameterAsFileOutput(parameters, "OUTPUT_FOLDER", context)
        if not output_value:
            raise QgsProcessingException("Choose a new or empty output folder")
        output = Path(output_value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output must be a new or empty folder; existing files are never overwritten")
        missing = [str(TEMPLATE_FOLDER / ("cofrance_" + name + ".gpkg"))
                   for name in TEMPLATES.values() if not (TEMPLATE_FOLDER / ("cofrance_" + name + ".gpkg")).is_file()]
        if missing:
            raise QgsProcessingException("Missing templates: " + ", ".join(missing))
        notes = []
        plans, source_count, skipped = plan_import(files, "CCA_" + safe_name(label), notes, feedback)
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".cofrance-import-", dir=output.parent))
        relatives = []
        try:
            if (TEMPLATE_FOLDER / "svg").is_dir():
                shutil.copytree(TEMPLATE_FOLDER / "svg", stage / "svg")
            for index, plan in enumerate(plans):
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled; incomplete import discarded")
                try:
                    relatives.append(write_layer(plan, stage, output, notes, feedback))
                except (ValueError, RuntimeError, sqlite3.Error) as error:
                    raise QgsProcessingException("Cannot import layer '{}': {}".format(plan["name"], error)) from error
                feedback.setProgress((index + 1) * 90 / len(plans))
            report = {
                "created_utc": datetime.now(timezone.utc).isoformat(), "map_name": label,
                "source_files": [str(p) for p in files], "source_features": source_count,
                "editable_features": sum(len(plan["rows"]) for plan in plans),
                "skipped_empty_features": skipped, "layers": relatives, "notes": notes,
                "limitations": [
                    "Layer boundaries are inferred from exported filenames, identifiers and consecutive styles.",
                    "Coordinates have only the precision present in the export.",
                    "Source properties retain metadata the current exporter may not emit.",
                ],
            }
            (stage / "import_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            # rmdir only accepts an empty directory, including if another process
            # added a file after our initial check.
            removed_empty = output.exists()
            if removed_empty:
                output.rmdir()
            try:
                os.rename(stage, output)
            except OSError:
                if removed_empty and not output.exists():
                    output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent != output.parent.resolve() or not stage.name.startswith(".cofrance-import-"):
                    raise RuntimeError("Unexpected import staging path")
                shutil.rmtree(stage)
        parent = child_group(project.layerTreeRoot(), "Imported CoFrance maps") or project.layerTreeRoot().addGroup("Imported CoFrance maps")
        group = parent.addGroup(label)
        owners = {}
        loaded = []
        try:
            for plan, relative in zip(plans, relatives):
                if plan["owner"] not in owners:
                    owners[plan["owner"]] = group.insertGroup(0, plan["owner"])
                layer = QgsVectorLayer(str(output / relative) + "|layername=" + plan["name"], plan["name"], "ogr")
                if not layer.isValid():
                    raise QgsProcessingException("Saved layer cannot be loaded: " + relative)
                project.addMapLayer(layer, False)
                owners[plan["owner"]].insertLayer(0, layer)
                loaded.append(layer.id())
        except Exception:
            project.removeMapLayers(loaded)
            parent.removeChildNode(group)
            raise
        project.setDirty(True)
        for message in notes:
            feedback.pushWarning(message)
        feedback.pushInfo("Imported {} layers and {} editable features. Save the QGIS project.".format(
            len(plans), report["editable_features"]))
        feedback.setProgress(100)
        return {"OUTPUT_FOLDER": str(output), "OUTPUTS": [str(output / name) for name in relatives],
                "REPORT": str(output / "import_report.json"), "LAYER_COUNT": len(plans),
                "FEATURE_COUNT": report["editable_features"], "SKIPPED_COUNT": skipped}
